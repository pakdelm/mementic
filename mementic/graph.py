from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, overload

import networkx as nx

from mementic.format import resolve_repo_name
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    GraphPayload,
)

SCHEMA_VERSION = 4


class GraphStore:
    """Knowledge graph store backed by NetworkX (in-memory) and optionally SQLite.

    When ``db_path`` is None, operates in NetworkX-only mode with JSON save/load.
    When ``db_path`` is provided, persists to SQLite: mutations update NetworkX and
    flush to SQLite; queries read directly from SQLite via indexed JOINs.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._g: nx.DiGraph = nx.DiGraph()
        self._conn: sqlite3.Connection | None = None
        self._db_path: Path | None = None
        self._in_memory = db_path == ":memory:"
        if self._in_memory:
            self._ensure_db()
        elif db_path:
            self._db_path = Path(db_path)
            self._ensure_db()

    def close(self) -> None:
        """Close the SQLite connection if open. Safe to call multiple times."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Database lifecycle
    # ------------------------------------------------------------------

    def _ensure_db(self) -> None:
        """Open the SQLite database, apply migrations, and load data into NetworkX."""
        if self._in_memory:
            self._conn = sqlite3.connect(":memory:")
        else:
            assert self._db_path is not None
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._migrate_schema(self._conn)
        self._load_from_db()

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        """Apply schema migrations sequentially based on PRAGMA user_version."""
        version: int = conn.execute("PRAGMA user_version").fetchone()[0]

        if version < 1:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'accepted',
                    rationale TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    repo TEXT DEFAULT '',
                    affected_entities TEXT DEFAULT '[]',
                    rejected_alternatives TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS constraints (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    enforced_by TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    repo TEXT DEFAULT '',
                    bound_entities TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'file',
                    repo TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (repo, id)
                );

                CREATE TABLE IF NOT EXISTS edges (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (source_id, target_id, kind)
                );

                CREATE TABLE IF NOT EXISTS project_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo TEXT NOT NULL UNIQUE,
                    label TEXT,
                    ingested_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS extraction_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    source_label TEXT DEFAULT '',
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    model TEXT DEFAULT '',
                    status TEXT DEFAULT 'success',
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_decisions_repo
                    ON decisions(repo);
                CREATE INDEX IF NOT EXISTS idx_decisions_status
                    ON decisions(status);
                CREATE INDEX IF NOT EXISTS idx_constraints_repo
                    ON constraints(repo);
                CREATE INDEX IF NOT EXISTS idx_edges_source
                    ON edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target
                    ON edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_extraction_op
                    ON extraction_log(operation);

                PRAGMA user_version = 1;
            """)

        if version < 2:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type   TEXT NOT NULL,
                    entity_id     TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    details       TEXT DEFAULT '{}',
                    source        TEXT DEFAULT '',
                    timestamp     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_log_entity
                    ON audit_log(entity_type, entity_id);
                CREATE INDEX IF NOT EXISTS idx_audit_log_time
                    ON audit_log(timestamp);
                PRAGMA user_version = 2;
            """)

        if version < 3:
            conn.executescript("""
                ALTER TABLE project_sources
                ADD COLUMN last_captured_commit TEXT DEFAULT '';
                PRAGMA user_version = 3;
            """)

        if version < 4:
            conn.executescript(
                """
                -- Normalize legacy DecisionStatus values removed in Spec 13.
                -- LOWER() matches the case-insensitive coercion in the
                -- Decision validator, so hand-edited rows like 'Proposed'
                -- or 'SUPERSEDED' are migrated too.
                UPDATE decisions SET status = 'deprecated' WHERE LOWER(status) = 'superseded';
                UPDATE decisions SET status = 'accepted' WHERE LOWER(status) = 'proposed';
                PRAGMA user_version = 4;
                """
            )

    def _load_from_db(self) -> None:
        """Load all rows from SQLite into the in-memory NetworkX graph."""
        assert self._conn is not None

        for row in self._conn.execute("SELECT * FROM decisions"):
            payload = dict(row)
            payload["affected_entities"] = json.loads(payload["affected_entities"])
            payload["rejected_alternatives"] = json.loads(
                payload["rejected_alternatives"]
            )
            self._g.add_node(row["id"], node_type="Decision", payload=payload)

        for row in self._conn.execute("SELECT * FROM constraints"):
            payload = dict(row)
            payload["bound_entities"] = json.loads(payload["bound_entities"])
            self._g.add_node(row["id"], node_type="Constraint", payload=payload)

        for row in self._conn.execute("SELECT * FROM entities"):
            payload = dict(row)
            self._g.add_node(row["id"], node_type="CodeEntity", payload=payload)

        for row in self._conn.execute("SELECT * FROM edges"):
            self._g.add_edge(
                row["source_id"], row["target_id"], relation=row["kind"]
            )

    def _write_back(self) -> None:
        """Sync the NetworkX graph to SQLite. No-op when db_path is None."""
        if self._conn is None:
            return

        now = datetime.now(timezone.utc).isoformat()

        with self._conn:
            self._conn.execute("DELETE FROM edges")

            for u, v, attrs in self._g.edges(data=True):
                self._conn.execute(
                    "INSERT INTO edges (source_id, target_id, kind) "
                    "VALUES (?, ?, ?)",
                    (u, v, attrs.get("relation", "")),
                )

            for node_id, attrs in self._g.nodes(data=True):
                node_type = attrs.get("node_type", "")
                payload: dict[str, Any] = attrs.get("payload", {})

                if node_type == "Decision":
                    self._conn.execute(
                        """INSERT OR REPLACE INTO decisions
                           (id, title, status, rationale, source, repo,
                            affected_entities, rejected_alternatives,
                            created_at, modified_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            payload.get("id", node_id),
                            payload.get("title", ""),
                            payload.get("status", "accepted"),
                            payload.get("rationale", ""),
                            payload.get("source", ""),
                            payload.get("repo", ""),
                            json.dumps(payload.get("affected_entities", [])),
                            json.dumps(
                                payload.get("rejected_alternatives", [])
                            ),
                            payload.get("created_at", now),
                            payload.get("modified_at", now),
                        ),
                    )
                elif node_type == "Constraint":
                    self._conn.execute(
                        """INSERT OR REPLACE INTO constraints
                           (id, description, enforced_by, source, repo,
                            bound_entities, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            payload.get("id", node_id),
                            payload.get("description", ""),
                            payload.get("enforced_by", ""),
                            payload.get("source", ""),
                            payload.get("repo", ""),
                            json.dumps(payload.get("bound_entities", [])),
                            payload.get("status", "active"),
                            payload.get("created_at", now),
                        ),
                    )
                elif node_type == "CodeEntity":
                    self._conn.execute(
                        """INSERT OR REPLACE INTO entities
                           (id, type, repo, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            payload.get("id", node_id),
                            payload.get("type", "file"),
                            payload.get("repo", ""),
                            payload.get("created_at", now),
                        ),
                    )

    # ------------------------------------------------------------------
    # Helpers for row → Pydantic model parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_decision(row: sqlite3.Row) -> Decision:
        payload = dict(row)
        payload["affected_entities"] = json.loads(payload["affected_entities"])
        payload["rejected_alternatives"] = json.loads(
            payload["rejected_alternatives"]
        )
        return Decision(**payload)

    @staticmethod
    def _row_to_constraint(row: sqlite3.Row) -> Constraint:
        payload = dict(row)
        payload["bound_entities"] = json.loads(payload["bound_entities"])
        return Constraint(**payload)

    # ------------------------------------------------------------------
    # Mutations (NetworkX + optional SQLite write-back)
    # ------------------------------------------------------------------

    def _log_audit(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        details: str | dict[str, Any] = "",
        source: str = "",
    ) -> None:
        """Write an audit log entry to the SQLite database if connected."""
        if self._conn is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        detail_str = json.dumps(details) if isinstance(details, dict) else details
        self._conn.execute(
            "INSERT INTO audit_log (entity_type, entity_id, action, details, source, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entity_type, entity_id, action, detail_str, source, now),
        )
        self._conn.commit()

    def add_decision(self, d: Decision) -> None:
        is_new = d.id not in self._g
        if d.id in self._g:
            existing_type = self._g.nodes[d.id].get("node_type")
            if existing_type and existing_type != "Decision":
                raise ValueError(
                    f"Node {d.id} already exists as {existing_type}, "
                    f"refusing to overwrite as Decision"
                )
            self._g.nodes[d.id]["node_type"] = "Decision"
            self._g.nodes[d.id]["payload"] = d.model_dump()
        else:
            self._g.add_node(d.id, node_type="Decision", payload=d.model_dump())
        self._write_back()
        self._log_audit(
            "decision", d.id, "add" if is_new else "modify",
            {"title": d.title, "status": d.status.value},
            source=d.source,
        )

    def add_constraint(self, c: Constraint) -> None:
        is_new = c.id not in self._g
        if c.id in self._g:
            existing_type = self._g.nodes[c.id].get("node_type")
            if existing_type and existing_type != "Constraint":
                raise ValueError(
                    f"Node {c.id} already exists as {existing_type}, "
                    f"refusing to overwrite as Constraint"
                )
            self._g.nodes[c.id]["node_type"] = "Constraint"
            self._g.nodes[c.id]["payload"] = c.model_dump()
        else:
            self._g.add_node(
                c.id, node_type="Constraint", payload=c.model_dump()
            )
        self._write_back()
        self._log_audit(
            "constraint", c.id, "add" if is_new else "modify",
            {"description": c.description, "status": c.status},
            source=c.source,
        )

    def add_code_entity(self, e: CodeEntity) -> None:
        is_new = e.id not in self._g
        if e.id in self._g:
            existing_type = self._g.nodes[e.id].get("node_type")
            if existing_type and existing_type != "CodeEntity":
                raise ValueError(
                    f"Node {e.id} already exists as {existing_type}, "
                    f"refusing to overwrite as CodeEntity"
                )
            self._g.nodes[e.id]["node_type"] = "CodeEntity"
            self._g.nodes[e.id]["payload"] = e.model_dump()
        else:
            self._g.add_node(
                e.id, node_type="CodeEntity", payload=e.model_dump()
            )
        self._write_back()
        self._log_audit(
            "CodeEntity", e.id, "add" if is_new else "modify",
            {"type": e.type.value},
        )

    def link(self, source_id: str, target_id: str, relation: str) -> None:
        self._g.add_edge(source_id, target_id, relation=relation)
        self._write_back()
        self._log_audit(
            "edge",
            f"{source_id}→{target_id}",
            "link",
            {"source": source_id, "target": target_id, "relation": relation},
        )

    def deprecate_entity(self, entity_id: str, reason: str = "") -> dict:
        """Deprecate a single decision or constraint by ID.

        Appends a ``[DEPRECATED]`` (or ``[DEPRECATED: {reason}]``) note
        to the entity's rationale/description, sets its status, and
        persists the change.

        Args:
            entity_id: The decision or constraint ID to deprecate.
            reason: Optional reason for deprecation.

        Returns:
            Dict with keys ``status`` and ``message``.
            ``status`` is one of ``"deprecated"``, ``"already_deprecated"``,
            ``"not_found"``, or ``"wrong_type"``.
        """
        if not self.has_node(entity_id):
            return {
                "status": "not_found",
                "message": f"Entity '{entity_id}' not found.",
            }

        node_type: str | None = None
        for nid, ntype, _payload in self.iter_nodes():
            if nid == entity_id:
                node_type = ntype
                break

        if node_type is None:
            return {
                "status": "not_found",
                "message": f"Entity '{entity_id}' not found.",
            }

        if node_type == "Decision":
            payload = self.get_node_payload(entity_id)
            decision = Decision(**payload)
            if decision.status == DecisionStatus.DEPRECATED:
                return {
                    "status": "already_deprecated",
                    "message": f"Decision '{entity_id}' is already deprecated.",
                }
            deprecation_note = (
                f" [DEPRECATED: {reason}]" if reason else " [DEPRECATED]"
            )
            original_title = decision.title
            decision.rationale += deprecation_note
            decision.status = DecisionStatus.DEPRECATED
            self.add_decision(decision)
            return {
                "status": "deprecated",
                "message": f"Deprecated decision '{entity_id}': {original_title}",
            }

        if node_type == "Constraint":
            payload = self.get_node_payload(entity_id)
            constraint = Constraint(**payload)
            if constraint.status == "deprecated":
                return {
                    "status": "already_deprecated",
                    "message": f"Constraint '{entity_id}' is already deprecated.",
                }
            deprecation_note = (
                f" [DEPRECATED: {reason}]" if reason else " [DEPRECATED]"
            )
            original_description = constraint.description
            constraint.description += deprecation_note
            constraint.status = "deprecated"
            self.add_constraint(constraint)
            return {
                "status": "deprecated",
                "message": (
                    f"Deprecated constraint '{entity_id}': {original_description}"
                ),
            }

        return {
            "status": "wrong_type",
            "message": (
                f"Entity '{entity_id}' is a {node_type} node, "
                f"not a decision or constraint."
            ),
        }

    # ------------------------------------------------------------------
    # Queries (SQLite when available, otherwise NetworkX)
    # ------------------------------------------------------------------

    @overload
    def get_constraints_for_entities(
        self, paths: list[str], prefix_match: bool = False, *,
        annotate_overlap: Literal[False] = False,
    ) -> list[Constraint]: ...
    @overload
    def get_constraints_for_entities(
        self, paths: list[str], prefix_match: bool = False, *,
        annotate_overlap: Literal[True],
    ) -> list[tuple[Constraint, int]]: ...
    def get_constraints_for_entities(
        self, paths: list[str], prefix_match: bool = False, *,
        annotate_overlap: bool = False,
    ) -> list[Constraint] | list[tuple[Constraint, int]]:
        """Return constraints whose bound_entities match any of the given paths.

        Args:
            paths: Entity paths to match against constraint bound_entities.
            prefix_match: If True, use prefix matching (be.startswith(p))
                instead of exact match.
            annotate_overlap: If True, return list of (Constraint, overlap_score)
                tuples. Overlap score is the count of bound_entities that
                match the given paths.

        Returns:
            List of Constraint objects, or list of (Constraint, int) tuples
            when annotate_overlap=True.
        """
        seen: set[str] = set()
        items: list[tuple[Constraint, int]] = []

        if self._conn is not None:
            for row in self._conn.execute("SELECT * FROM constraints"):
                bound_entities: list[str] = json.loads(row["bound_entities"])
                overlap = 0
                for be in bound_entities:
                    for p in paths:
                        if be == p or (prefix_match and be.startswith(p)):
                            overlap += 1
                            break  # count each bound_entity at most once
                if overlap > 0 and row["id"] not in seen:
                    seen.add(row["id"])
                    items.append((self._row_to_constraint(row), overlap))
            if annotate_overlap:
                return items
            return [c for c, _ in items]

        for _node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "Constraint":
                continue
            bound_entities = attrs["payload"].get("bound_entities", [])
            overlap = 0
            for be in bound_entities:
                for p in paths:
                    if be == p or (prefix_match and be.startswith(p)):
                        overlap += 1
                        break
            if overlap > 0 and attrs["payload"]["id"] not in seen:
                seen.add(attrs["payload"]["id"])
                items.append((Constraint(**attrs["payload"]), overlap))
        if annotate_overlap:
            return items
        return [c for c, _ in items]

    @overload
    def get_decisions_for_entities(
        self, paths: list[str], prefix_match: bool = False, *,
        annotate_overlap: Literal[False] = False,
    ) -> list[Decision]: ...
    @overload
    def get_decisions_for_entities(
        self, paths: list[str], prefix_match: bool = False, *,
        annotate_overlap: Literal[True],
    ) -> list[tuple[Decision, int]]: ...
    def get_decisions_for_entities(
        self, paths: list[str], prefix_match: bool = False, *,
        annotate_overlap: bool = False,
    ) -> list[Decision] | list[tuple[Decision, int]]:
        """Return decisions directly linked to entity nodes matching paths.

        Only returns decisions with direct edges to matching entity nodes
        (no transitive constraint-based expansion).

        Args:
            paths: Entity paths to find related decisions for.
            prefix_match: If True, use prefix matching (entity_id.startswith(p))
                instead of exact match.
            annotate_overlap: If True, return list of (Decision, overlap_score)
                tuples. Overlap score is the count of entity IDs linked to
                the decision that match the given paths.

        Returns:
            List of Decision objects, or list of (Decision, int) tuples
            when annotate_overlap=True.
        """
        items: dict[str, tuple[Decision, int]] = {}

        if self._conn is not None:
            if prefix_match:
                # Build WHERE clause with LIKE for prefix matching
                conditions = []
                params: list[str] = []
                for p in paths:
                    conditions.append(
                        "e.target_id = ? OR e.target_id LIKE ? || '%'"
                    )
                    params.append(p)
                    params.append(p)
                where_clause = " OR ".join(conditions)
                for row in self._conn.execute(
                    f"SELECT d.*, COUNT(DISTINCT e.target_id) as overlap "
                    f"FROM decisions d "
                    f"JOIN edges e ON d.id = e.source_id "
                    f"WHERE ({where_clause}) "
                    f"GROUP BY d.id",
                    params,
                ):
                    items[row["id"]] = (self._row_to_decision(row), row["overlap"])
            else:
                placeholders = ",".join("?" for _ in paths)
                for row in self._conn.execute(
                    f"SELECT d.*, COUNT(DISTINCT e.target_id) as overlap "
                    f"FROM decisions d "
                    f"JOIN edges e ON d.id = e.source_id "
                    f"WHERE e.target_id IN ({placeholders}) "
                    f"GROUP BY d.id",
                    paths,
                ):
                    items[row["id"]] = (self._row_to_decision(row), row["overlap"])

            if annotate_overlap:
                return list(items.values())
            return [dec for dec, _ in items.values()]

        for node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "CodeEntity":
                continue
            matches = any(
                node_id == p or (prefix_match and node_id.startswith(p))
                for p in paths
            )
            if not matches:
                continue
            for pred in self.get_predecessors(node_id):
                pred_attrs = self._g.nodes[pred]
                if pred_attrs.get("node_type") == "Decision":
                    if pred not in items:
                        items[pred] = (Decision(**pred_attrs["payload"]), 1)
                    else:
                        dec, prev = items[pred]
                        items[pred] = (dec, prev + 1)

        if annotate_overlap:
            return list(items.values())
        return [dec for dec, _ in items.values()]

    @overload
    def get_evolution(
        self, entity_path: str, prefix_match: bool = False, *,
        annotate_overlap: Literal[False] = False,
    ) -> list[Decision]: ...
    @overload
    def get_evolution(
        self, entity_path: str, prefix_match: bool = False, *,
        annotate_overlap: Literal[True],
    ) -> list[tuple[Decision, int]]: ...
    def get_evolution(
        self, entity_path: str, prefix_match: bool = False, *,
        annotate_overlap: bool = False,
    ) -> list[Decision] | list[tuple[Decision, int]]:
        """Return decisions that shaped the given entity path.

        Finds decisions via two paths:
        1. Decisions linked to constraints whose bound_entities match
           the entity path.
        2. Decisions directly linked to entity nodes matching the path.

        Args:
            entity_path: The entity path to trace evolution for.
            prefix_match: If True, use prefix matching instead of exact match.
            annotate_overlap: If True, return list of (Decision, overlap_score)
                tuples. Overlap score is the count of bound entities or
                matched entity paths that match entity_path.

        Returns:
            List of Decision objects, or list of (Decision, int) tuples
            when annotate_overlap=True.
        """
        items: dict[str, tuple[Decision, int]] = {}

        if self._conn is not None:
            def _add_decision(dec_id: str, overlap_delta: int) -> None:
                if dec_id not in items:
                    drow = self._conn.execute(  # type: ignore[union-attr]
                        "SELECT * FROM decisions WHERE id = ?",
                        (dec_id,),
                    ).fetchone()
                    if drow:
                        items[drow["id"]] = (
                            self._row_to_decision(drow),
                            overlap_delta,
                        )
                else:
                    dec, prev = items[dec_id]
                    items[dec_id] = (dec, prev + overlap_delta)

            # Part 1: decisions linked via constraints bound to entity_path
            for crow in self._conn.execute("SELECT * FROM constraints"):
                bound_entities: list[str] = json.loads(
                    crow["bound_entities"]
                )
                match_count = sum(
                    1
                    for be in bound_entities
                    if be == entity_path
                    or (prefix_match and be.startswith(entity_path))
                )
                if match_count > 0:
                    for erow in self._conn.execute(
                        "SELECT source_id FROM edges WHERE target_id = ?",
                        (crow["id"],),
                    ):
                        _add_decision(erow["source_id"], match_count)

            # Part 2: decisions directly linked to matching entity nodes
            for erow in self._conn.execute("SELECT * FROM entities"):
                if (
                    erow["id"] == entity_path
                    or (prefix_match and erow["id"].startswith(entity_path))
                ):
                    for edge_row in self._conn.execute(
                        "SELECT source_id FROM edges WHERE target_id = ?",
                        (erow["id"],),
                    ):
                        _add_decision(edge_row["source_id"], 1)

            if annotate_overlap:
                return list(items.values())
            return [dec for dec, _ in items.values()]

        # NetworkX path
        for _node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "Constraint":
                continue
            bound_entities = attrs["payload"].get("bound_entities", [])
            match_count = sum(
                1
                for be in bound_entities
                if be == entity_path
                or (prefix_match and be.startswith(entity_path))
            )
            if match_count > 0:
                for pred in self.get_predecessors(attrs["payload"]["id"]):
                    pred_attrs = self._g.nodes[pred]
                    if pred_attrs.get("node_type") == "Decision":
                        if pred not in items:
                            items[pred] = (Decision(**pred_attrs["payload"]), match_count)
                        else:
                            dec, prev = items[pred]
                            items[pred] = (dec, prev + match_count)

        for node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "CodeEntity":
                continue
            if node_id == entity_path or (
                prefix_match and node_id.startswith(entity_path)
            ):
                for pred in self.get_predecessors(node_id):
                    pred_attrs = self._g.nodes[pred]
                    if pred_attrs.get("node_type") == "Decision":
                        if pred not in items:
                            items[pred] = (Decision(**pred_attrs["payload"]), 1)
                        else:
                            dec, prev = items[pred]
                            items[pred] = (dec, prev + 1)

        if annotate_overlap:
            return list(items.values())
        return [dec for dec, _ in items.values()]

    def get_all_decisions(self) -> list[Decision]:
        if self._conn is not None:
            return [
                self._row_to_decision(row)
                for row in self._conn.execute("SELECT * FROM decisions")
            ]
        return [
            Decision(**attrs["payload"])
            for _node_id, attrs in self._g.nodes(data=True)
            if attrs.get("node_type") == "Decision"
        ]

    def get_all_active_constraints(self) -> list[Constraint]:
        """Return all constraints with ``status == "active"``.

        Filters out deprecated/superseded constraints.  Used by
        ``get_task_context`` to provide the full active set for
        keyword-based ranking (Spec 14).

        Returns:
            List of active Constraint objects.
        """
        all_c = self.get_all_constraints()
        return [c for c in all_c if c.status == "active"]

    def get_all_active_decisions(self) -> list[Decision]:
        """Return all decisions with ``status == "accepted"``.

        Filters out deprecated/rejected decisions.  Used by
        ``get_task_context`` to provide the full accepted set for
        relevance ranking (Spec 14).

        Returns:
            List of accepted Decision objects.
        """
        all_d = self.get_all_decisions()
        return [d for d in all_d if d.status == DecisionStatus.ACCEPTED]

    def get_all_constraints(self) -> list[Constraint]:
        if self._conn is not None:
            return [
                self._row_to_constraint(row)
                for row in self._conn.execute("SELECT * FROM constraints")
            ]
        return [
            Constraint(**attrs["payload"])
            for _node_id, attrs in self._g.nodes(data=True)
            if attrs.get("node_type") == "Constraint"
        ]

    # ------------------------------------------------------------------
    # Serialization (JSON for backward compat; no-op for SQLite)
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        if self._conn is not None:
            # SQLite mode: data is already persisted; save is a no-op.
            return

        nodes_payload: list[dict[str, Any]] = []
        for node_id, attrs in self._g.nodes(data=True):
            nodes_payload.append({
                "id": node_id,
                "type": attrs.get("node_type", ""),
                "data": attrs.get("payload", {}),
            })

        edges_payload: list[dict[str, str]] = []
        for u, v, attrs in self._g.edges(data=True):
            edges_payload.append({
                "source": u,
                "target": v,
                "relation": attrs.get("relation", ""),
            })

        payload = GraphPayload(nodes=nodes_payload, edges=edges_payload)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps(payload.model_dump(mode="json"), indent=2)
        )
        tmp.replace(p)

    @classmethod
    def load(cls, path: str) -> GraphStore:
        data = json.loads(Path(path).read_text())
        payload = GraphPayload(**data)
        gs = cls()
        for node in payload.nodes:
            node_data: dict[str, Any] = dict(node)
            gs._g.add_node(
                node_data["id"],
                node_type=node_data.get("type", ""),
                payload=node_data.get("data", {}),
            )
        for edge in payload.edges:
            edge_data: dict[str, str] = dict(edge)
            gs._g.add_edge(
                edge_data["source"],
                edge_data["target"],
                relation=edge_data.get("relation", ""),
            )
        return gs

    # ------------------------------------------------------------------
    # Node / Edge accessors (always NetworkX)
    # ------------------------------------------------------------------

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    def get_node_payload(self, node_id: str) -> dict[str, Any]:
        return dict(self._g.nodes[node_id].get("payload", {}))

    # Mapping from NetworkX node_type to audit_log entity_type
    _ENTITY_TYPE_MAP: dict[str, str] = {
        "Decision": "decision",
        "Constraint": "constraint",
        "CodeEntity": "CodeEntity",
    }

    def set_node_payload(self, node_id: str, payload: dict[str, Any]) -> None:
        self._g.nodes[node_id]["payload"] = payload
        node_type = self._g.nodes[node_id].get("node_type", "unknown")
        entity_type = self._ENTITY_TYPE_MAP.get(node_type, node_type.lower())
        self._log_audit(
            entity_type, node_id, "modify",
            {"changed_keys": list(payload.keys())},
        )

    def iter_nodes(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Return list of (node_id, node_type, payload) for all nodes."""
        return [
            (nid, attrs.get("node_type", ""), attrs.get("payload", {}))
            for nid, attrs in self._g.nodes(data=True)
        ]

    def iter_edges(self) -> list[tuple[str, str, str]]:
        """Return list of (source_id, target_id, relation) for all edges."""
        return [
            (u, v, attrs.get("relation", ""))
            for u, v, attrs in self._g.edges(data=True)
        ]

    def has_outgoing_edges(self, node_id: str) -> bool:
        try:
            next(self._g.successors(node_id))
            return True
        except StopIteration:
            return False

    def get_predecessors(self, node_id: str) -> list[str]:
        """Return all predecessor node IDs for the given node."""
        if node_id not in self._g:
            return []
        return list(self._g.predecessors(node_id))

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        if self._conn is not None:
            return {
                "decisions": self._conn.execute(
                    "SELECT COUNT(*) FROM decisions"
                ).fetchone()[0],
                "constraints": self._conn.execute(
                    "SELECT COUNT(*) FROM constraints"
                ).fetchone()[0],
                "entities": self._conn.execute(
                    "SELECT COUNT(*) FROM entities"
                ).fetchone()[0],
                "edges": self._conn.execute(
                    "SELECT COUNT(*) FROM edges"
                ).fetchone()[0],
            }

        decisions = 0
        constraints = 0
        entities = 0
        for _node_id, attrs in self._g.nodes(data=True):
            nt = attrs.get("node_type", "")
            if nt == "Decision":
                decisions += 1
            elif nt == "Constraint":
                constraints += 1
            elif nt == "CodeEntity":
                entities += 1
        return {
            "decisions": decisions,
            "constraints": constraints,
            "entities": entities,
            "edges": self._g.number_of_edges(),
        }

    # ------------------------------------------------------------------
    # Graph cleaning — deterministic dead-reference removal
    # ------------------------------------------------------------------

    def clean_dead_references(self) -> dict[str, int]:
        """Clean dead references from the graph. Deterministic, zero network cost.

        Called transparently before every get_task_context and get_entity_history
        query. The agent never knows it ran.

        Returns:
            Dict with action counts: 'dead_bindings_removed', 'constraints_deprecated',
            'stale_entities_removed', 'orphaned_entities_removed',
            'orphaned_enforced_by_cleared', 'status_inconsistencies_resolved'.
        """
        counts: dict[str, int] = {
            "dead_bindings_removed": 0,
            "constraints_deprecated": 0,
            "stale_entities_removed": 0,
            "orphaned_entities_removed": 0,
            "orphaned_enforced_by_cleared": 0,
            "status_inconsistencies_resolved": 0,
        }

        existing_ids: set[str] = {nid for nid, _, _ in self.iter_nodes()}
        for nid, ntype, payload in self.iter_nodes():
            if ntype != "Constraint":
                continue
            bound_entities: list[str] = payload.get("bound_entities", [])
            if not bound_entities:
                continue
            filtered = [be for be in bound_entities if be in existing_ids]
            removed = len(bound_entities) - len(filtered)
            if removed > 0:
                payload["bound_entities"] = filtered
                self.set_node_payload(nid, payload)
                counts["dead_bindings_removed"] += removed

        for nid, ntype, payload in self.iter_nodes():
            if ntype != "Constraint":
                continue
            bound_entities = payload.get("bound_entities", [])
            if not bound_entities and payload.get("status") != "deprecated":
                payload["status"] = "deprecated"
                self.set_node_payload(nid, payload)
                counts["constraints_deprecated"] += 1

        # Only FILE-type entities are checked for disk existence.
        # CLASS/FUNCTION/MODULE entities have compound IDs (e.g.
        # "src/mod.py:MyClass") that are not valid filesystem paths.
        stale_ids: list[str] = []
        for nid, ntype, payload in self.iter_nodes():
            if ntype != "CodeEntity":
                continue
            if payload.get("type") == "file" and not Path(nid).exists():
                stale_ids.append(nid)

        for nid in stale_ids:
            self._g.remove_node(nid)  # also removes incident edges
            counts["stale_entities_removed"] += 1

        edge_targets: set[str] = {tgt for _, tgt, _ in self.iter_edges()}

        orphan_ids: list[str] = []
        for nid, ntype, _payload in self.iter_nodes():
            if ntype != "CodeEntity":
                continue
            if nid not in edge_targets:
                orphan_ids.append(nid)

        for nid in orphan_ids:
            self._g.remove_node(nid)
            counts["orphaned_entities_removed"] += 1

        decision_ids: set[str] = {
            nid for nid, ntype, _ in self.iter_nodes() if ntype == "Decision"
        }
        for nid, ntype, payload in self.iter_nodes():
            if ntype != "Constraint":
                continue
            enforced_by = payload.get("enforced_by", "")
            if enforced_by and enforced_by not in decision_ids:
                payload["enforced_by"] = ""
                self.set_node_payload(nid, payload)
                counts["orphaned_enforced_by_cleared"] += 1

        deprecated_decision_ids: set[str] = {
            nid
            for nid, ntype, payload in self.iter_nodes()
            if ntype == "Decision" and payload.get("status") == "deprecated"
        }
        for nid, ntype, payload in self.iter_nodes():
            if ntype != "Constraint":
                continue
            enforced_by = payload.get("enforced_by", "")
            if (
                enforced_by in deprecated_decision_ids
                and payload.get("status") == "active"
            ):
                payload["status"] = "deprecated"
                self.set_node_payload(nid, payload)
                counts["status_inconsistencies_resolved"] += 1

        self._write_back()

        return counts

    # Capture checkpoint (stored in project_sources)

    def get_capture_checkpoint(self, repo_path: str) -> str:
        """Return the last captured git commit SHA for a repo.

        Args:
            repo_path: Absolute path to the repository.

        Returns:
            The commit SHA string, or empty string if never captured.
        """
        if self._conn is None:
            return ""
        repo_name = resolve_repo_name(repo_path)
        row = self._conn.execute(
            "SELECT last_captured_commit FROM project_sources WHERE repo = ?",
            (repo_name,),
        ).fetchone()
        return row[0] if row else ""

    def set_capture_checkpoint(self, repo_path: str, commit: str) -> None:
        """Set the last captured git commit SHA for a repo.

        Uses INSERT OR REPLACE against the UNIQUE constraint on repo.
        If the repo isn't registered yet, creates a row.  If it exists,
        updates the commit and bumps ingested_at.

        Args:
            repo_path: Absolute path to the repository.
            commit: The git commit SHA to store.
        """
        if self._conn is None:
            return
        repo_name = resolve_repo_name(repo_path)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR REPLACE INTO project_sources
               (repo, label, ingested_at, last_captured_commit)
               VALUES (?, ?, COALESCE(
                   (SELECT ingested_at FROM project_sources WHERE repo = ?),
                   ?
               ), ?)""",
            (repo_name, "", repo_name, now, commit),
        )
        self._conn.commit()
