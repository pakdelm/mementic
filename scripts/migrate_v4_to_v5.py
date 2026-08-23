"""One-shot migration: mementic schema v4 (GraphStore hybrid) -> v5 (relational).

Reads a v4 database (.mementic/graph.db as created by the old NetworkX-backed
``GraphStore``), backs it up, and rebuilds it in place with the Spec 29
schema:

- decisions/constraints copied verbatim minus dead columns (``source``,
  ``repo``, ``modified_at``, ``rejected_alternatives``).
- The legacy ``proposed``-status constraints (zombies from an incomplete v4
  normalization) are DROPPED; the count is printed.
- ``entry_paths`` is materialized from ``affected_entities`` /
  ``bound_entities`` JSON plus ``affects`` / ``binds_to`` edges,
  deduplicated.
- ``deprecations`` rows are backfilled for entries deprecated at migration
  time. Where the old code appended ``[DEPRECATED: ...]`` to rationale or
  description text, that suffix is parsed out and becomes the structured
  reason; ``replaced_by`` stays NULL because same-slug overwrites destroyed
  most pre-existing lineage — the table fills from today onward.
- ``audit_log`` is carried forward untouched.

The entire rebuild (legacy-table drops, v5 DDL, data copies, and the
``user_version = 5`` stamp) runs inside ONE explicit ``BEGIN IMMEDIATE``
transaction. Any failure rolls back completely, leaving the original v4
database pristine on disk.

Refusals (non-zero exit, no mutation):
- database missing or ``PRAGMA user_version != 4``;
- ``<db>.bak`` already exists — refusing to overwrite an old backup;
- dangling ``constraints.enforced_by`` references found during pre-check.

Usage::

    uv run python scripts/migrate_v4_to_v5.py [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mementic.store import SCHEMA_DDL, SET_USER_VERSION_SQL  # noqa: E402

DEPRECATED_SUFFIX_RE = re.compile(r"\s*\[DEPRECATED(?::\s*(.*?))?\]\s*$")
SYMBOL_QUALIFIER_RE_A = re.compile(r"::[A-Za-z_]\w*$")
SYMBOL_QUALIFIER_RE_B = re.compile(r":[A-Za-z_]\w*$")


def _normalize_path(raw: str) -> str:
    """Strip trailing symbol qualifiers exactly like the CLI always did."""
    clean = SYMBOL_QUALIFIER_RE_A.sub("", raw)
    clean = SYMBOL_QUALIFIER_RE_B.sub("", clean)
    return clean


def _split_deprecation(text_value: str) -> tuple[str, str]:
    """Split a '[DEPRECATED: reason]' suffix off text.

    Returns:
        ``(clean_text, reason)`` where reason is ``""`` when no suffix or a
        bare ``[DEPRECATED]`` marker was present.
    """
    match = DEPRECATED_SUFFIX_RE.search(text_value)
    if not match:
        return text_value, ""
    clean = text_value[: match.start()]
    reason = (match.group(1) or "").strip()
    return clean.rstrip(), reason


def _iter_ddl(schema_sql: str) -> list[str]:
    """Split the schema DDL into individually executable statements.

    ``executescript()`` would COMMIT any open transaction before running,
    which would break the all-or-nothing rebuild guarantee, so every DDL
    statement goes through :meth:`sqlite3.Connection.execute` instead.
    """
    return [s.strip() for s in schema_sql.split(";") if s.strip()]


def migrate(db_path: Path) -> int:
    """Run the v4 -> v5 migration on ``db_path``.

    Returns:
        Process exit code (0 on success).
    """
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != 4:
        conn.close()
        print(
            f"ERROR: expected PRAGMA user_version == 4, found {version}. "
            f"Refusing to migrate '{db_path}'.",
            file=sys.stderr,
        )
        return 1

    # --- Refuse to clobber a previous backup (early exit, no mutation) ---
    backup_path = Path(str(db_path) + ".bak")
    if backup_path.exists():
        conn.close()
        print(
            f"ERROR: backup already exists — refusing to overwrite "
            f"'{backup_path}'. Move or delete it first.",
            file=sys.stderr,
        )
        return 1

    # --- Snapshot all v4 data into memory before touching the schema ---
    # Single-writer assumption: mementic is a per-user CLI tool; no
    # concurrent writer is expected between this snapshot and the backup
    # copy below. A writer in that window would diverge snapshot from .bak.
    decisions = conn.execute("SELECT * FROM decisions").fetchall()
    constraints = conn.execute("SELECT * FROM constraints").fetchall()
    edges = conn.execute(
        "SELECT source_id, target_id, kind FROM edges "
        "WHERE kind IN ('affects', 'binds_to')"
    ).fetchall()

    # Pre-check FK integrity so failures surface before any mutation.
    decision_ids = {r["id"] for r in decisions}
    dangling = [
        r["id"]
        for r in constraints
        if r["enforced_by"] and r["enforced_by"] not in decision_ids
    ]
    if dangling:
        conn.close()
        print(
            "ERROR: constraints reference missing decisions; fix manually "
            f"before migrating: {dangling}",
            file=sys.stderr,
        )
        return 1

    conn.close()

    # --- Backup before mutating ---
    shutil.copy2(db_path, backup_path)
    print(f"Backup written: {backup_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Manual transaction control: the rebuild below must be one atomic unit.
    conn.isolation_level = None
    migrated_at = datetime.now(timezone.utc).isoformat()

    entry_paths: set[tuple[str, str, str]] = set()
    deprecations: list[tuple[str, str, None, str, str]] = []
    copied_decisions = 0
    copied_constraints = 0
    dropped_proposed = 0
    backfilled_decisions = 0
    backfilled_constraints = 0
    paths_from_json = 0
    paths_from_edges = 0
    malformed_json_rows = 0

    def _parse_entity_list(raw_json: str | None) -> tuple[list[str], bool]:
        """Parse an entity JSON list defensively.

        Returns:
            ``(items, ok)`` where ``items`` holds only string entries and
            ``ok`` is False when the payload was undecodable, not a list,
            or contained non-string items.
        """
        try:
            value = json.loads(raw_json or "[]")
        except (json.JSONDecodeError, TypeError):
            return [], False
        if not isinstance(value, list):
            return [], False
        items = [v for v in value if isinstance(v, str)]
        return items, len(items) == len(value)

    try:
        conn.execute("BEGIN IMMEDIATE")

        # --- Rebuild the schema in place BEFORE writing (audit_log survives) --
        for stmt in (
    "DROP TABLE IF EXISTS entities",
    "DROP TABLE IF EXISTS edges",
    "DROP TABLE IF EXISTS project_sources",
    "DROP TABLE IF EXISTS extraction_log",
    "DROP TABLE IF EXISTS decisions",
    "DROP TABLE IF EXISTS constraints",
        ):
            conn.execute(stmt)
        for stmt in _iter_ddl(SCHEMA_DDL):
            conn.execute(stmt)

        for row in decisions:
            rationale = row["rationale"]
            reason = ""
            if str(row["status"]).lower() == "deprecated":
                rationale, reason = _split_deprecation(rationale)
                deprecations.append(
                    ("decision", row["id"], None, reason, migrated_at)
                )
                backfilled_decisions += 1
            conn.execute(
                "INSERT INTO decisions (id, title, rationale, created_at) "
                "VALUES (?, ?, ?, ?)",
                (row["id"], row["title"], rationale, row["created_at"]),
            )
            copied_decisions += 1
            affected, ok = _parse_entity_list(row["affected_entities"])
            if not ok:
                malformed_json_rows += 1
            for raw in affected:
                path = _normalize_path(raw)
                if path:
                    entry_paths.add(("decision", row["id"], path))
                    paths_from_json += 1

        for row in constraints:
            if str(row["status"]).lower() == "proposed":
                dropped_proposed += 1
                continue
            description = row["description"]
            reason = ""
            if str(row["status"]).lower() == "deprecated":
                description, reason = _split_deprecation(description)
                deprecations.append(
                    ("constraint", row["id"], None, reason, migrated_at)
                )
                backfilled_constraints += 1
            conn.execute(
                "INSERT INTO constraints (id, description, enforced_by, created_at) "
                "VALUES (?, ?, ?, ?)",
                # '' cannot satisfy the FK under SQLite semantics; standalone
                # constraints are stored as NULL (mapped back at read time).
                (
                    row["id"],
                    description,
                    row["enforced_by"] or None,
                    row["created_at"],
                ),
            )
            copied_constraints += 1
            bound, ok = _parse_entity_list(row["bound_entities"])
            if not ok:
                malformed_json_rows += 1
            for raw in bound:
                path = _normalize_path(raw)
                if path:
                    entry_paths.add(("constraint", row["id"], path))
                    paths_from_json += 1

        constraint_ids = {
            r["id"]
            for r in constraints if str(r["status"]).lower() != "proposed"
        }
        for edge in edges:
            if edge["kind"] == "affects" and edge["source_id"] in decision_ids:
                path = _normalize_path(edge["target_id"])
                if path:
                    entry_paths.add(("decision", edge["source_id"], path))
                    paths_from_edges += 1
            elif (
                edge["kind"] == "binds_to"
                and edge["source_id"] in constraint_ids
            ):
                path = _normalize_path(edge["target_id"])
                if path:
                    entry_paths.add(("constraint", edge["source_id"], path))
                    paths_from_edges += 1

        conn.executemany(
            "INSERT OR IGNORE INTO entry_paths (entry_type, entry_id, path) "
            "VALUES (?, ?, ?)",
            sorted(entry_paths),
        )
        conn.executemany(
            "INSERT INTO deprecations (entry_type, entry_id, replaced_by, "
            "reason, at) VALUES (?, ?, ?, ?, ?)",
            deprecations,
        )

        # Stamped inside the transaction so a crash can never leave a v4
        # database mis-marked as current.
        conn.execute(SET_USER_VERSION_SQL)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - any failure must roll back
        conn.rollback()
        conn.close()
        print(
            f"ERROR: migration failed ({exc}); '{db_path}' was left "
            f"untouched at schema v4. Backup: '{backup_path}'.",
            file=sys.stderr,
        )
        return 1

    counts = {
        "decisions": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
        "constraints": conn.execute(
            "SELECT COUNT(*) FROM constraints"
        ).fetchone()[0],
        "entry_paths": conn.execute(
            "SELECT COUNT(*) FROM entry_paths"
        ).fetchone()[0],
        "deprecations": conn.execute(
            "SELECT COUNT(*) FROM deprecations"
        ).fetchone()[0],
        "audit_log": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
    }
    final_version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    print("Migration report:")
    print(f"  decisions copied:          {copied_decisions}")
    print(f"    tombstones backfilled:   {backfilled_decisions}")
    print(f"  constraints copied:        {copied_constraints}")
    print(f"    proposed zombies DROPPED: {dropped_proposed}")
    print(f"    tombstones backfilled:   {backfilled_constraints}")
    print(f"  entry_paths materialized:  {counts['entry_paths']} unique")
    print(f"    from JSON lists:         {paths_from_json} refs")
    print(f"    from affects/binds_to:   {paths_from_edges} refs (deduplicated)")
    print(f"  malformed JSON rows:       {malformed_json_rows}")
    print(f"  deprecations rows:         {counts['deprecations']}")
    print(f"  audit_log preserved:       {counts['audit_log']}")
    print(f"  PRAGMA user_version:       {final_version}")
    print(
        "NOTE: pre-existing replacement lineage was mostly destroyed by "
        "same-slug overwrites in the v4 era; replaced_by is NULL for all "
        "backfilled tombstones. The table fills from today onward."
    )
    return 0


def main() -> None:
    """Parse arguments and run the migration."""
    parser = argparse.ArgumentParser(
        description="Migrate a mementic v4 database to schema v5.",
    )
    parser.add_argument(
        "--db",
        default=".mementic/graph.db",
        help="Path to the SQLite database (default: .mementic/graph.db)",
    )
    args = parser.parse_args()
    sys.exit(migrate(Path(args.db)))


if __name__ == "__main__":
    main()
