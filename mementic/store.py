"""Relational storage layer for mementic (schema v5).

A thin data-access module over raw ``sqlite3``. No ORM, no in-memory graph:
every read is a SQL query and every write is an INSERT/REPLACE on flat
tables. Liveness is *derived* — an entry is dead iff a ``deprecations`` row
for it exists with ``at`` newer than the entry's ``created_at`` (the
timestamp correlation distinguishes incarnations when a deprecated slug is
re-added). No status column exists anywhere.

Schema note: ``constraints.enforced_by`` carries ``DEFAULT ''``,
but SQLite FK semantics treat '' as a real value that must match a parent
row. Standalone constraints are therefore stored with ``enforced_by = NULL``
(mapped transparently to ``""`` at read time); non-empty references remain
FK-enforced, so dangling IDs raise :class:`sqlite3.IntegrityError`.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from urllib.parse import quote

from mementic.models import Constraint, Decision
from mementic.schema import (
    DEFAULT_AUDIT_LIMIT,
    LIVE_WHERE,
    SCHEMA_DDL,
    SCHEMA_VERSION,
    SET_USER_VERSION_SQL,
    successor_timestamp,
    utc_now,
)

_MAX_STAMP_RETRIES = 10


def _insert_deprecation(conn: sqlite3.Connection, row: tuple) -> str:
    """Insert one tombstone row, bumping the stamp on clock-tick collisions.

    The ``deprecations`` primary key includes ``at``, so two lifecycle
    events for the same entry within one OS clock tick (coarse clocks,
    notably pre-3.13 Windows) collide. On collision the stamp advances by
    one microsecond and the insert retries — bounded, then loud.

    Args:
        conn: Open sqlite3 connection (inside an active transaction).
        row: ``(entry_type, entry_id, replaced_by, reason, at)``.

    Returns:
        The stamp that was actually inserted.

    Raises:
        RuntimeError: If no unique stamp could be inserted within the
            retry bound.
    """
    for _ in range(_MAX_STAMP_RETRIES):
        try:
            conn.execute(
                "INSERT INTO deprecations (entry_type, entry_id, "
                "replaced_by, reason, at) VALUES (?, ?, ?, ?, ?)",
                row,
            )
            return str(row[4])
        except sqlite3.IntegrityError:
            row = row[:4] + (successor_timestamp(str(row[4])),)
    raise RuntimeError(
        f"could not insert a unique deprecation stamp for {row[1]!r} "
        f"after {_MAX_STAMP_RETRIES} attempts"
    )


class MementicStore:
    """SQLite-backed memory store for decisions and constraints.

    Opens (creating if necessary) the database at ``db_path``, applies the
    schema-v5 DDL to fresh databases, and refuses databases from older
    schema versions — those predate the relational store; start fresh with
    ``mementic init`` or recover data from a pre-upgrade backup.

    With ``read_only=True`` the store opens the file through a SQLite
    ``mode=ro`` URI: it never creates directories or database files and any
    write attempt fails at the driver level, so read-only tooling
    structurally cannot mutate a database.
    """

    def __init__(self, db_path: str | Path, *, read_only: bool = False) -> None:
        self._db_path = Path(db_path)
        self._read_only = read_only
        self._in_transaction = False
        self._conn: sqlite3.Connection | None = None
        try:
            self._conn = self._connect()
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._ensure_schema()
        except BaseException:
            self.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        """Open the connection selected by the configured path and access mode.

        In-memory databases are read-write only. File-backed read-only
        stores open through a ``mode=ro`` URI so SQLite itself refuses
        writes and never creates directories or database files.

        Returns:
            An unconfigured connection; the caller sets row access and
            pragmas.

        Raises:
            ValueError: If ``read_only=True`` is combined with ``:memory:``.
        """
        if str(self._db_path) == ":memory:":
            if self._read_only:
                raise ValueError("read_only=True requires a file-backed database")
            return sqlite3.connect(":memory:")
        if self._read_only:
            uri = (
                "file:"
                + quote(self._db_path.resolve().as_posix(), safe="/:")
                + "?mode=ro"
            )
            return sqlite3.connect(uri, uri=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._db_path))

    def _configure_journal(self) -> None:
        """Use WAL so writes work on shared VM filesystems such as virtiofs.

        SQLite's rollback-journal modes fail with "attempt to write a
        readonly database" on an sbx virtiofs workspace, while WAL writes
        through the shared-memory file. Read-only stores keep whatever mode
        the writer left on the database.

        Raises:
            RuntimeError: If a file-backed read-write database cannot switch
                to WAL, which would make writes fail on mounted workspaces.
        """
        if self._read_only or str(self._db_path) == ":memory:":
            return
        conn = self._conn_or_raise()
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise RuntimeError(
                f"cannot enable WAL journal mode (got {mode!r}); "
                "the graph filesystem must support SQLite WAL"
            )

    def close(self) -> None:
        """Close the SQLite connection. Safe to call multiple times."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> MementicStore:
        """Return this store for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection when the ``with`` block exits."""
        self.close()

    def _conn_or_raise(self) -> sqlite3.Connection:
        """Return the open connection, or raise if the store is closed."""
        if self._conn is None:
            raise RuntimeError("store connection is closed")
        return self._conn

    @contextmanager
    def _write_scope(self) -> Iterator[None]:
        """Bound one write with a commit-or-rollback boundary.

        Outside an explicit transaction, the sqlite3 connection is used as
        a context manager: it commits when the block exits cleanly and
        rolls back when it raises. Inside :meth:`transaction`, this scope
        is a no-op so the outer transaction owns the final commit.
        """
        if self._in_transaction:
            yield
            return
        with self._conn_or_raise():
            yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group multiple writes into one atomic transaction.

        Nested calls join the outer transaction. Any exception rolls back
        every write performed since the outer transaction began.
        """
        conn = self._conn_or_raise()
        if self._in_transaction:
            yield
            return
        self._in_transaction = True
        try:
            conn.execute("BEGIN")
            yield
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            self._in_transaction = False

    def _ensure_schema(self) -> None:
        """Create the schema on fresh databases; reject foreign versions."""
        conn = self._conn_or_raise()
        version: int = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            self._configure_journal()
            return
        if self._read_only:
            raise RuntimeError(
                f"Read-only open refused: database schema is v{version} but this "
                f"build requires v{SCHEMA_VERSION} (or the file is not an "
                f"initialized mementic database). Open it read-write to migrate."
            )
        if version == 0:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables & {"decisions", "constraints"}:
                raise RuntimeError(
                    "Database has pre-versioning tables but user_version=0; "
                    "cannot open it safely."
                )
            self._configure_journal()
            conn.executescript(
                f"BEGIN;\n{SCHEMA_DDL}\n{SET_USER_VERSION_SQL};\nCOMMIT;"
            )
            return
        raise RuntimeError(
            f"Database schema is v{version} but this build requires "
            f"v{SCHEMA_VERSION}. Old-schema databases are not auto-upgraded: "
            f"start fresh with 'mementic init' (or move the file aside and "
            f"init), or recover its data with the matching historical build."
        )

    def add_decision(
        self,
        *,
        entry_id: str,
        title: str,
        rationale: str,
        created_at: str,
        paths: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Insert or update a decision and its paths.

        Overwrites are applied with an UPSERT (UPDATE-in-place), never
        DELETE+INSERT, so constraints whose ``enforced_by`` references this
        ID survive a re-add.

        Args:
            entry_id: Slug ID of the decision.
            title: Short title.
            rationale: Justification text.
            created_at: ISO 8601 creation timestamp. Must postdate any
                deprecations rows for prior incarnations of this ID so the
                new incarnation derives as live.
            paths: Affected file paths (replaces any existing set).

        Returns:
            ``"add"`` when newly created, ``"modify"`` when overwriting.

        Raises:
            ValueError: If the ID already exists as a constraint.
        """
        conn = self._conn_or_raise()
        existing_type = self.get_entry_type(entry_id)
        if existing_type == "constraint":
            raise ValueError(
                f"ID '{entry_id}' already exists as a constraint, "
                f"refusing to overwrite as a decision"
            )
        action = "modify" if existing_type == "decision" else "add"
        with self._write_scope():
            conn.execute(
                "INSERT INTO decisions (id, title, rationale, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, rationale=excluded.rationale, "
                "created_at=excluded.created_at",
                (entry_id, title, rationale, created_at),
            )
            self._replace_paths("decision", entry_id, paths)
            self._log_audit(
                "decision", entry_id, action, {"title": title},
            )
        return action

    def add_constraint(
        self,
        *,
        entry_id: str,
        description: str,
        enforced_by: str = "",
        created_at: str,
        paths: list[str] | tuple[str, ...] = (),
    ) -> str:
        """Insert or update a constraint and its paths.

        Overwrites are applied with an UPSERT (UPDATE-in-place), never
        DELETE+INSERT, so existing entry_paths rows are replaced cleanly
        and FK parents stay untouched.

        Args:
            entry_id: Slug ID of the constraint.
            description: Rule text.
            enforced_by: Enforcing decision ID, or empty for standalone.
                Non-empty values must reference an existing decision.
            created_at: ISO 8601 creation timestamp. Must postdate any
                deprecations rows for prior incarnations of this ID.
            paths: Bound file paths (replaces any existing set).

        Returns:
            ``"add"`` when newly created, ``"modify"`` when overwriting.

        Raises:
            ValueError: If the ID already exists as a decision.
            sqlite3.IntegrityError: If ``enforced_by`` names no decision.
        """
        conn = self._conn_or_raise()
        existing_type = self.get_entry_type(entry_id)
        if existing_type == "decision":
            raise ValueError(
                f"ID '{entry_id}' already exists as a decision, "
                f"refusing to overwrite as a constraint"
            )
        action = "modify" if existing_type == "constraint" else "add"
        with self._write_scope():
            conn.execute(
                "INSERT INTO constraints "
                "(id, description, enforced_by, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "description=excluded.description, "
                "enforced_by=excluded.enforced_by, "
                "created_at=excluded.created_at",
                # '' bypasses nothing under SQLite FK rules; store standalone
                # constraints as NULL so only real references are enforced.
                (entry_id, description, enforced_by or None, created_at),
            )
            self._replace_paths("constraint", entry_id, paths)
            self._log_audit(
                "constraint", entry_id, action, {"description": description},
            )
        return action

    def _replace_paths(
        self, entry_type: str, entry_id: str, paths: list[str] | tuple[str, ...],
    ) -> None:
        """Replace the entry_paths rows for one entry inside an open transaction."""
        conn = self._conn_or_raise()
        conn.execute(
            "DELETE FROM entry_paths WHERE entry_type = ? AND entry_id = ?",
            (entry_type, entry_id),
        )
        for path in dict.fromkeys(paths):
            conn.execute(
                "INSERT OR IGNORE INTO entry_paths (entry_type, entry_id, path) "
                "VALUES (?, ?, ?)",
                (entry_type, entry_id, path),
            )

    def deprecate(
        self,
        entry_type: str,
        entry_id: str,
        reason: str = "",
        replaced_by: str | None = None,
    ) -> dict[str, str]:
        """Record that a decision or constraint was superseded or retired.

        Writes one ``deprecations`` row (``replaced_by=NULL`` means retired
        without successor). The entry's rationale/description text is never
        mutated — liveness is derived from this table.

        Args:
            entry_type: ``"decision"`` or ``"constraint"``.
            entry_id: The entry to deprecate.
            reason: Optional human-readable reason.
            replaced_by: Optional successor entry ID.

        Returns:
            Dict with keys ``status``, ``message``, and — when a tombstone
            row was written — ``at`` holding the tombstone's ISO 8601
            stamp. ``status`` is one of ``"deprecated"``,
            ``"already_deprecated"``, or ``"not_found"``. Callers adding a
            successor incarnation must derive its ``created_at`` from
            ``at`` via :func:`successor_timestamp` so the new incarnation
            structurally postdates the tombstone.

        Raises:
            ValueError: If ``entry_type`` is not ``"decision"`` or
                ``"constraint"``.
        """
        if entry_type not in {"decision", "constraint"}:
            raise ValueError(
                f"entry_type must be 'decision' or 'constraint', "
                f"got {entry_type!r}"
            )
        conn = self._conn_or_raise()
        label = "title" if entry_type == "decision" else "description"
        table = entry_type + "s"
        row = conn.execute(
            f"SELECT id, {label} FROM {table} WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return {
                "status": "not_found",
                "message": f"{entry_type.title()} '{entry_id}' not found.",
            }
        if self.is_deprecated(entry_type, entry_id):
            return {
                "status": "already_deprecated",
                "message": (
                    f"{entry_type.title()} '{entry_id}' is already deprecated."
                ),
            }
        now = utc_now()
        with self._write_scope():
            inserted_at = _insert_deprecation(
                conn, (entry_type, entry_id, replaced_by, reason, now)
            )
            self._log_audit(
                entry_type,
                entry_id,
                "deprecate",
                {
                    "description": row[label],
                    "reason": reason,
                    "replaced_by": replaced_by or "",
                },
            )
        noun = entry_type.title()
        suffix = f" (superseded by '{replaced_by}')" if replaced_by else ""
        return {
            "status": "deprecated",
            "at": inserted_at,
            "message": f"Deprecated {noun.lower()} '{entry_id}'{suffix}",
        }

    def record_succession(
        self,
        entry_type: str,
        entry_id: str,
        replaced_by: str,
        reason: str = "",
    ) -> str:
        """Insert a tombstone row documenting replacement by a successor.

        Unlike :meth:`deprecate`, this always writes — it backs the
        replace-during-add path, where the prior tombstone predates the
        successor and must gain a ``replaced_by`` pointer. Callers must
        invoke this *before* adding the successor incarnation and derive
        the successor's ``created_at`` from the returned stamp via
        :func:`successor_timestamp`, so the new incarnation always
        postdates the tombstone structurally (never a wall-clock race).

        Returns:
            The tombstone's ISO 8601 stamp.

        Raises:
            ValueError: If ``entry_type`` is not ``"decision"`` or
                ``"constraint"``.
        """
        if entry_type not in {"decision", "constraint"}:
            raise ValueError(
                f"entry_type must be 'decision' or 'constraint', "
                f"got {entry_type!r}"
            )
        conn = self._conn_or_raise()
        now = utc_now()
        with self._write_scope():
            inserted_at = _insert_deprecation(
                conn, (entry_type, entry_id, replaced_by, reason, now)
            )
            details: dict = {
                "reason": reason,
                "replaced_by": replaced_by,
            }
            self._log_audit(entry_type, entry_id, "deprecate", details)
        return inserted_at

    def _log_audit(
        self, entity_type: str, entity_id: str, action: str, details: dict,
    ) -> None:
        """Append one audit_log row inside an open transaction."""
        conn = self._conn_or_raise()
        conn.execute(
            "INSERT INTO audit_log (entity_type, entity_id, action, details, "
            "source, timestamp) VALUES (?, ?, ?, ?, '', ?)",
            (entity_type, entity_id, action, json.dumps(details), utc_now()),
        )

    def get_entry_type(self, entry_id: str) -> str | None:
        """Return ``"decision"``, ``"constraint"``, or None for unknown IDs."""
        conn = self._conn_or_raise()
        if conn.execute(
            "SELECT 1 FROM decisions WHERE id = ?", (entry_id,)
        ).fetchone():
            return "decision"
        if conn.execute(
            "SELECT 1 FROM constraints WHERE id = ?", (entry_id,)
        ).fetchone():
            return "constraint"
        return None

    def get_decision(self, entry_id: str) -> Decision | None:
        """Return one decision by ID, or None."""
        conn = self._conn_or_raise()
        row = conn.execute(
            "SELECT * FROM decisions WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return Decision(
            id=row["id"],
            title=row["title"],
            rationale=row["rationale"],
            created_at=row["created_at"],
            paths=self._paths_for("decision", entry_id),
        )

    def get_constraint(self, entry_id: str) -> Constraint | None:
        """Return one constraint by ID, or None."""
        conn = self._conn_or_raise()
        row = conn.execute(
            "SELECT * FROM constraints WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        return Constraint(
            id=row["id"],
            description=row["description"],
            enforced_by=row["enforced_by"] or "",
            created_at=row["created_at"],
            paths=self._paths_for("constraint", entry_id),
        )

    def get_live_decisions(self) -> list[Decision]:
        """Return all decisions not superseded/retired (derived liveness)."""
        rows = self._query_entries("decisions", live_only=True)
        paths = self._paths_by_entry("decision")
        return [self._row_to_decision(r, paths) for r in rows]

    def get_all_decisions(self) -> list[Decision]:
        """Return all decisions regardless of deprecation state."""
        rows = self._query_entries("decisions", live_only=False)
        paths = self._paths_by_entry("decision")
        return [self._row_to_decision(r, paths) for r in rows]

    def get_live_constraints(self) -> list[Constraint]:
        """Return all constraints not superseded/retired (derived liveness)."""
        rows = self._query_entries("constraints", live_only=True)
        paths = self._paths_by_entry("constraint")
        return [self._row_to_constraint(r, paths) for r in rows]

    def get_all_constraints(self) -> list[Constraint]:
        """Return all constraints regardless of deprecation state."""
        rows = self._query_entries("constraints", live_only=False)
        paths = self._paths_by_entry("constraint")
        return [self._row_to_constraint(r, paths) for r in rows]

    def get_decisions_with_liveness(self) -> list[tuple[Decision, str | None]]:
        """Return ``(decision, superseded_by)`` pairs for every decision.

        ``superseded_by`` is ``None`` for live entries; otherwise the
        successor ID or the literal ``"retired"`` when no successor exists.
        """
        rows = self._query_entries(
            "decisions", live_only=False, with_superseded=True,
        )
        paths = self._paths_by_entry("decision")
        return [(self._row_to_decision(r, paths), r["superseded_by"]) for r in rows]

    def get_constraints_with_liveness(self) -> list[tuple[Constraint, str | None]]:
        """Return ``(constraint, superseded_by)`` pairs for every constraint."""
        rows = self._query_entries(
            "constraints", live_only=False, with_superseded=True,
        )
        paths = self._paths_by_entry("constraint")
        return [(self._row_to_constraint(r, paths), r["superseded_by"]) for r in rows]

    def _query_entries(
        self, table: str, *, live_only: bool, with_superseded: bool = False,
    ) -> list[sqlite3.Row]:
        """Query the decisions/constraints table with derived liveness."""
        conn = self._conn_or_raise()
        etype = table[:-1]
        alias = table[0]
        superseded_col = ""
        if with_superseded:
            superseded_col = (
                f",(SELECT CASE WHEN dp.replaced_by IS NULL THEN 'retired' "
                f"ELSE dp.replaced_by END FROM deprecations dp "
                f"WHERE dp.entry_type = :etype AND dp.entry_id = {alias}.id "
                f"AND dp.at > {alias}.created_at "
                f"ORDER BY dp.at DESC LIMIT 1) AS superseded_by"
            )
        where = f"WHERE {LIVE_WHERE.format(alias=alias)}" if live_only else ""
        sql = (
            f"SELECT {alias}.*{superseded_col} FROM {table} {alias} {where} "
            f"ORDER BY {alias}.created_at, {alias}.id"
        )
        return conn.execute(sql, {"etype": etype}).fetchall()

    def _row_to_decision(
        self,
        row: sqlite3.Row,
        paths_by_entry: dict[str, list[str]] | None = None,
    ) -> Decision:
        """Build a Decision (with paths) from a decisions row.

        Callers that load many rows pass a pre-grouped path map so the
        entry_paths table is read once instead of once per entry.
        """
        paths = (
            paths_by_entry.get(row["id"], [])
            if paths_by_entry is not None
            else self._paths_for("decision", row["id"])
        )
        return Decision(
            id=row["id"],
            title=row["title"],
            rationale=row["rationale"],
            created_at=row["created_at"],
            paths=paths,
        )

    def _row_to_constraint(
        self,
        row: sqlite3.Row,
        paths_by_entry: dict[str, list[str]] | None = None,
    ) -> Constraint:
        """Build a Constraint (with paths) from a constraints row.

        Callers that load many rows pass a pre-grouped path map so the
        entry_paths table is read once instead of once per entry.
        """
        paths = (
            paths_by_entry.get(row["id"], [])
            if paths_by_entry is not None
            else self._paths_for("constraint", row["id"])
        )
        return Constraint(
            id=row["id"],
            description=row["description"],
            enforced_by=row["enforced_by"] or "",
            created_at=row["created_at"],
            paths=paths,
        )

    def _paths_by_entry(self, entry_type: str) -> dict[str, list[str]]:
        """Return entry ID to ordered paths for one entry type."""
        conn = self._conn_or_raise()
        grouped: dict[str, list[str]] = {}
        rows = conn.execute(
            "SELECT entry_id, path FROM entry_paths WHERE entry_type = ? "
            "ORDER BY rowid",
            (entry_type,),
        )
        for row in rows:
            grouped.setdefault(row["entry_id"], []).append(row["path"])
        return grouped

    def _paths_for(self, entry_type: str, entry_id: str) -> list[str]:
        """Return ordered entry_paths rows for one entry."""
        conn = self._conn_or_raise()
        return [
            r["path"]
            for r in conn.execute(
                "SELECT path FROM entry_paths WHERE entry_type = ? "
                "AND entry_id = ? ORDER BY rowid",
                (entry_type, entry_id),
            )
        ]

    def get_constraints_for_path(
        self,
        path: str,
        *,
        prefix: bool = False,
        path_exists: Callable[[str], bool] | None = None,
    ) -> list[Constraint]:
        """Return live constraints bound to ``path``.

        Args:
            path: File path to match against entry_paths.
            prefix: When True, match entries whose stored path starts with
                ``path`` instead of requiring an exact match.
            path_exists: Optional filesystem-liveness predicate. When
                given, matched paths that fail it are dropped, and entries
                whose every matched path is dead are excluded entirely.

        Returns:
            Live constraints whose matched paths exist (when
            ``path_exists`` is given), ordered oldest-first. Ranking
            callers re-sort by recency.
        """
        ids = self._entry_ids_matching_path(
            "constraint", path, prefix=prefix, path_exists=path_exists,
        )
        if not ids:
            return []
        rows = [
            r
            for r in self._query_entries("constraints", live_only=True)
            if r["id"] in ids
        ]
        paths = self._paths_by_entry("constraint")
        return [self._row_to_constraint(r, paths) for r in rows]

    def get_decisions_for_path(
        self,
        path: str,
        *,
        prefix: bool = False,
        path_exists: Callable[[str], bool] | None = None,
    ) -> list[tuple[Decision, int]]:
        """Return live decisions affecting ``path`` with overlap scores.

        Args:
            path: File path to match against entry_paths.
            prefix: When True, use prefix matching instead of exact match.
            path_exists: Optional filesystem-liveness predicate. When
                given, dead paths are excluded before scoring and entries
                with no surviving paths are dropped.

        Returns:
            ``(decision, overlap)`` tuples where overlap counts how many
            of the decision's stored paths match the query (and survive
            ``path_exists``, when given).
        """
        matches = self.get_path_matches(
            "decision", path, prefix=prefix, path_exists=path_exists,
        )
        if not matches:
            return []
        results: list[tuple[Decision, int]] = []
        for d in self.get_live_decisions():
            if d.id not in matches:
                continue
            overlap = sum(1 for p in d.paths if p in matches[d.id])
            if overlap > 0:
                results.append((d, overlap))
        return results

    def _entry_ids_matching_path(
        self,
        entry_type: str,
        path: str,
        *,
        prefix: bool,
        path_exists: Callable[[str], bool] | None = None,
    ) -> set[str]:
        """Return entry IDs with at least one surviving matching path."""
        return set(self.get_path_matches(
            entry_type, path, prefix=prefix, path_exists=path_exists,
        ).keys())

    def get_path_matches(
        self,
        entry_type: str,
        path: str,
        *,
        prefix: bool = False,
        path_exists: Callable[[str], bool] | None = None,
    ) -> dict[str, set[str]]:
        """Map matching entry IDs to the set of their paths that matched.

        This is the single definition of path-overlap semantics for the
        whole codebase: callers filtering on filesystem liveness pass a
        ``path_exists`` predicate here rather than re-filtering results.

        Args:
            entry_type: ``"decision"`` or ``"constraint"``.
            path: Query path.
            prefix: Use SQL LIKE prefix matching (with escaping) when True;
                anchored at the start of stored paths (never substring).
            path_exists: Optional predicate; matched paths failing it are
                removed from each result set, and entries left with no
                surviving paths are omitted.

        Returns:
            Map of entry ID to the set of its stored paths that matched
            (and survived ``path_exists``, when given).
        """
        conn = self._conn_or_raise()
        if prefix:
            # Anchor the LIKE at the start: ``path + '%'`` matches stored
            # paths beginning with ``path`` (including prefix-siblings such
            # as ``src/utils.py`` for prefix ``src/util`` — same semantics
            # as the historical startswith behavior).
            escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace(
                "_", "\\_"
            )
            pattern = escaped + "%"
            rows = conn.execute(
                "SELECT entry_id, path FROM entry_paths "
                "WHERE entry_type = ? AND path LIKE ? ESCAPE '\\'",
                (entry_type, pattern),
            )
        else:
            rows = conn.execute(
                "SELECT entry_id, path FROM entry_paths "
                "WHERE entry_type = ? AND path = ?",
                (entry_type, path),
            )
        matches: dict[str, set[str]] = {}
        for r in rows:
            if path_exists is not None and not path_exists(r["path"]):
                continue
            matches.setdefault(r["entry_id"], set()).add(r["path"])
        return matches

    def get_all_paths(self) -> list[str]:
        """Return every distinct stored path, in insertion order."""
        conn = self._conn_or_raise()
        seen: dict[str, None] = {}
        for r in conn.execute(
            "SELECT path FROM entry_paths ORDER BY rowid"
        ):
            seen.setdefault(r["path"], None)
        return list(seen)

    def is_deprecated(self, entry_type: str, entry_id: str) -> bool:
        """Return True if the entry's current incarnation has a tombstone.

        Uses the same derived predicate as the live-set queries: a
        deprecations row stamped after the entry's ``created_at``.
        """
        conn = self._conn_or_raise()
        table = entry_type + "s"
        row = conn.execute(
            f"SELECT 1 FROM {table} e WHERE e.id = ? AND EXISTS ("
            f"  SELECT 1 FROM deprecations dp"
            f"  WHERE dp.entry_type = ? AND dp.entry_id = e.id"
            f"    AND dp.at > e.created_at)",
            (entry_id, entry_type),
        ).fetchone()
        return row is not None

    def get_deprecations_for_entry(
        self, entry_type: str, entry_id: str,
    ) -> list[sqlite3.Row]:
        """Return every deprecations row recorded for an entry ID.

        Includes rows belonging to replaced prior incarnations; ordered
        oldest first so history reads chronologically.
        """
        conn = self._conn_or_raise()
        return conn.execute(
            "SELECT * FROM deprecations WHERE entry_type = ? AND entry_id = ? "
            "ORDER BY at",
            (entry_type, entry_id),
        ).fetchall()

    def get_replacement_chain(
        self, entry_type: str, entry_id: str,
    ) -> list[sqlite3.Row]:
        """Return the transitive deprecation chain starting at an entry.

        Follows ``replaced_by`` successors breadth-first (cycle-safe) and
        returns every visited deprecations row ordered by timestamp —
        the record trail behind chains like ``x -> y -> z``.
        """
        self._conn_or_raise()
        collected: list[sqlite3.Row] = []
        visited: set[str] = {entry_id}
        frontier: deque[tuple[str, str]] = deque([(entry_type, entry_id)])
        while frontier:
            etype, eid = frontier.popleft()
            records = self.get_deprecations_for_entry(etype, eid)
            collected.extend(records)
            for rec in records:
                successor = rec["replaced_by"]
                if successor and successor not in visited:
                    visited.add(successor)
                    stype = self.get_entry_type(successor) or etype
                    frontier.append((stype, successor))
        collected.sort(key=lambda r: (r["at"], r["entry_id"]))
        return collected

    def get_all_deprecations(self) -> list[sqlite3.Row]:
        """Return the whole deprecations table, newest first."""
        conn = self._conn_or_raise()
        return conn.execute(
            "SELECT rowid, * FROM deprecations ORDER BY at DESC, rowid DESC"
        ).fetchall()

    def stats(self) -> dict[str, int]:
        """Return store statistics.

        Returns:
            Dict with ``decisions`` / ``constraints`` totals, ``paths``
            (entry_paths rows), and ``deprecated`` (currently dead entries).
        """
        conn = self._conn_or_raise()
        dead_decisions = self._count_dead("decisions")
        dead_constraints = self._count_dead("constraints")
        return {
            "decisions": conn.execute(
                "SELECT COUNT(*) FROM decisions"
            ).fetchone()[0],
            "constraints": conn.execute(
                "SELECT COUNT(*) FROM constraints"
            ).fetchone()[0],
            "paths": conn.execute(
                "SELECT COUNT(*) FROM entry_paths"
            ).fetchone()[0],
            "deprecated": dead_decisions + dead_constraints,
        }

    def _count_dead(self, table: str) -> int:
        """Count entries currently killed by a later-stamped tombstone."""
        conn = self._conn_or_raise()
        alias = table[0]
        sql = (
            f"SELECT COUNT(*) FROM {table} {alias} "
            f"WHERE NOT ({LIVE_WHERE.format(alias=alias)})"
        )
        return conn.execute(sql, {"etype": table[:-1]}).fetchone()[0]

    def count_audit_entries(self) -> int:
        """Return the total number of audit log entries.

        Returns:
            Row count of the ``audit_log`` table.
        """
        conn = self._conn_or_raise()
        return int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

    def get_audit_entries(
        self, limit: int = DEFAULT_AUDIT_LIMIT
    ) -> list[sqlite3.Row]:
        """Return recent audit log entries, newest first.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of ``sqlite3.Row`` objects with keys: id, entity_type,
            entity_id, action, details, source, timestamp.
        """
        conn = self._conn_or_raise()
        return conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
