from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from mementic.graph import GraphStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
)


@pytest.fixture
def populated_graph() -> GraphStore:
    gs = GraphStore()

    d1 = Decision(
        id="d1",
        title="Use async",
        rationale="Better performance",
        source="ADR-001",
        status=DecisionStatus.ACCEPTED,
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
    )
    d2 = Decision(
        id="d2",
        title="Adopt pydantic",
        rationale="Validation",
        source="ADR-002",
        status=DecisionStatus.ACCEPTED,
        created_at="2024-02-01T00:00:00Z",
        modified_at="2024-02-01T00:00:00Z",
    )
    d3 = Decision(
        id="d3",
        title="Use SQLite",
        rationale="Simple persistence",
        source="ADR-003",
        status=DecisionStatus.ACCEPTED,
        created_at="2024-03-01T00:00:00Z",
        modified_at="2024-03-01T00:00:00Z",
    )

    c1 = Constraint(
        id="c1",
        description="All I/O must be async",
        bound_entities=["src/services/io.py"],
        enforced_by="d1",
        source="ADR-001",
        status="active",
        created_at="2024-01-01T00:00:00Z",
    )
    c2 = Constraint(
        id="c2",
        description="Data models use pydantic",
        bound_entities=["src/models/base.py", "src/models/types.py"],
        enforced_by="d2",
        source="ADR-002",
        status="active",
        created_at="2024-02-01T00:00:00Z",
    )

    e1 = CodeEntity(
        id="src/services/io.py",
        type=EntityType.FILE,
        repo="my-repo",
    )
    e2 = CodeEntity(
        id="src/models/base.py",
        type=EntityType.FILE,
        repo="my-repo",
    )
    e3 = CodeEntity(
        id="src/models/base.py:BaseModel",
        type=EntityType.CLASS,
        repo="my-repo",
    )
    e4 = CodeEntity(
        id="src/models/types.py",
        type=EntityType.FILE,
        repo="my-repo",
    )

    gs.add_decision(d1)
    gs.add_decision(d2)
    gs.add_decision(d3)
    gs.add_constraint(c1)
    gs.add_constraint(c2)
    gs.add_code_entity(e1)
    gs.add_code_entity(e2)
    gs.add_code_entity(e3)
    gs.add_code_entity(e4)

    gs.link("d1", "c1", "enforces")
    gs.link("d2", "c2", "enforces")
    gs.link("c1", "src/services/io.py", "binds")
    gs.link("c2", "src/models/base.py", "binds")
    gs.link("c2", "src/models/types.py", "binds")
    gs.link("d3", "src/models/base.py", "affects")

    return gs


class TestDecisionOperations:
    def test_add_and_get_all_decisions(self, populated_graph: GraphStore) -> None:
        decisions = populated_graph.get_all_decisions()
        assert len(decisions) == 3
        titles = {d.title for d in decisions}
        assert titles == {"Use async", "Adopt pydantic", "Use SQLite"}

    def test_get_all_constraints(self, populated_graph: GraphStore) -> None:
        constraints = populated_graph.get_all_constraints()
        assert len(constraints) == 2


class TestConstraintQuerying:
    def test_exact_match(self, populated_graph: GraphStore) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/services/io.py"]
        )
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_prefix_match_opt_in(self, populated_graph: GraphStore) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/services/"], prefix_match=True
        )
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_multiple_paths(self, populated_graph: GraphStore) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/services/io.py", "src/models/base.py"]
        )
        assert len(result) == 2


class TestConstraintExactMatch:
    def test_constraints_exact_match_only(
        self, populated_graph: GraphStore
    ) -> None:
        result = populated_graph.get_constraints_for_entities(["src/services/"])
        assert len(result) == 0

        result = populated_graph.get_constraints_for_entities(
            ["src/services/io.py"]
        )
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_constraints_prefix_mode(
        self, populated_graph: GraphStore
    ) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/services/"], prefix_match=True
        )
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_constraints_no_fuzzy_fallback(
        self, populated_graph: GraphStore
    ) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/models/base.py.method"]
        )
        assert len(result) == 0

        result = populated_graph.get_constraints_for_entities(
            ["src/models/base.py.method"], prefix_match=True
        )
        assert len(result) == 0


class TestTraversal:
    def test_get_decisions_for_entities(self, populated_graph: GraphStore) -> None:
        decisions = populated_graph.get_decisions_for_entities(
            ["src/models/base.py"]
        )
        ids = {d.id for d in decisions}
        assert "d3" in ids

    def test_get_decisions_direct_link(self, populated_graph: GraphStore) -> None:
        decisions = populated_graph.get_decisions_for_entities(
            ["src/models/base.py"]
        )
        ids = {d.id for d in decisions}
        assert "d3" in ids

    def test_get_evolution(self, populated_graph: GraphStore) -> None:
        evolution = populated_graph.get_evolution("src/services/io.py")
        ids = {d.id for d in evolution}
        assert "d1" in ids

    def test_get_evolution_direct_link(self, populated_graph: GraphStore) -> None:
        evolution = populated_graph.get_evolution("src/models/base.py")
        ids = {d.id for d in evolution}
        assert "d2" in ids
        assert "d3" in ids

    def test_decisions_direct_only(
        self, populated_graph: GraphStore
    ) -> None:
        decisions = populated_graph.get_decisions_for_entities(
            ["src/models/base.py"]
        )
        ids = {d.id for d in decisions}
        assert "d3" in ids

    def test_decisions_no_transitive(
        self, populated_graph: GraphStore
    ) -> None:
        decisions = populated_graph.get_decisions_for_entities(
            ["src/services/io.py"]
        )
        ids = {d.id for d in decisions}
        assert "d1" not in ids

    def test_get_evolution_prefix(self, populated_graph: GraphStore) -> None:
        evolution = populated_graph.get_evolution(
            "src/models/", prefix_match=True
        )
        ids = {d.id for d in evolution}
        assert "d2" in ids
        assert "d3" in ids

    def test_get_evolution_exact_match_only(
        self, populated_graph: GraphStore
    ) -> None:
        # Exact match: only the exact path returns results
        evolution = populated_graph.get_evolution("src/models/")
        assert len(evolution) == 0

        evolution = populated_graph.get_evolution("src/services/io.py")
        assert {d.id for d in evolution} == {"d1"}

    def test_get_evolution_prefix_opt_in(
        self, populated_graph: GraphStore
    ) -> None:
        # With prefix_match=True, directory prefix matches work
        evolution = populated_graph.get_evolution(
            "src/services/", prefix_match=True
        )
        assert {d.id for d in evolution} == {"d1"}

        # Class entity with no direct edges: exact match finds the entity
        # but it has no predecessors, so no decisions
        evolution = populated_graph.get_evolution(
            "src/models/base.py:BaseModel"
        )
        assert len(evolution) == 0

    def test_decisions_for_entities_prefix_match(
        self, populated_graph: GraphStore
    ) -> None:
        # Exact match (default) for partial path returns nothing
        decisions = populated_graph.get_decisions_for_entities(
            ["src/models/"]
        )
        assert len(decisions) == 0

        # prefix_match=True matches directory prefixes
        decisions = populated_graph.get_decisions_for_entities(
            ["src/models/"], prefix_match=True
        )
        ids = {d.id for d in decisions}
        assert "d3" in ids
        # d2 links to entities via constraint, not directly, so not included


class TestSaveLoad:
    def test_roundtrip(self, populated_graph: GraphStore) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "graph.json")
            populated_graph.save(path)

            loaded = GraphStore.load(path)

            assert loaded.get_all_decisions() == populated_graph.get_all_decisions()
            assert loaded.get_all_constraints() == populated_graph.get_all_constraints()
            assert loaded.stats() == populated_graph.stats()

            json_text = Path(path).read_text()
            data = json.loads(json_text)
            assert "nodes" in data
            assert "edges" in data
            assert len(data["nodes"]) == 9
            assert len(data["edges"]) == 6


class TestStats:
    def test_stats_empty(self) -> None:
        gs = GraphStore()
        assert gs.stats() == {"decisions": 0, "constraints": 0, "entities": 0, "edges": 0}

    def test_stats_populated(self, populated_graph: GraphStore) -> None:
        stats = populated_graph.stats()
        assert stats["decisions"] == 3
        assert stats["constraints"] == 2
        assert stats["entities"] == 4
        assert stats["edges"] == 6


class TestNodeAccessors:
    def test_has_node_true(self, populated_graph: GraphStore) -> None:
        assert populated_graph.has_node("d1") is True

    def test_has_node_false(self, populated_graph: GraphStore) -> None:
        assert populated_graph.has_node("nonexistent") is False

    def test_get_node_payload(self, populated_graph: GraphStore) -> None:
        payload = populated_graph.get_node_payload("d1")
        assert payload["title"] == "Use async"

    def test_set_node_payload(self, populated_graph: GraphStore) -> None:
        populated_graph.set_node_payload("d1", {"title": "Updated"})
        payload = populated_graph.get_node_payload("d1")
        assert payload["title"] == "Updated"

    def test_iter_nodes(self, populated_graph: GraphStore) -> None:
        nodes = populated_graph.iter_nodes()
        assert len(nodes) == 9
        types = {t for _, t, _ in nodes}
        assert types == {"Decision", "Constraint", "CodeEntity"}

    def test_has_outgoing_edges_true(self, populated_graph: GraphStore) -> None:
        assert populated_graph.has_outgoing_edges("d1") is True

    def test_has_outgoing_edges_false(self, populated_graph: GraphStore) -> None:
        gs = GraphStore()
        gs.add_decision(Decision(
            id="lonely", title="No edges", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))
        assert gs.has_outgoing_edges("lonely") is False


class TestAuditLog:
    def test_save_does_not_write_audit_jsonl(self, populated_graph: GraphStore) -> None:
        """Spec 12d: save() no longer writes legacy audit.jsonl — use SQLite audit_log."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            populated_graph.save(graph_path)
            audit_path = Path(tmpdir) / "audit.jsonl"
            assert not audit_path.exists()


class TestIdempotentAdd:
    def test_add_decision_twice_updates_payload(self) -> None:
        gs = GraphStore()
        d1 = Decision(
            id="d-update", title="Original", rationale="First",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="d-update", title="Updated", rationale="Second",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-02T00:00:00Z",
            modified_at="2024-01-02T00:00:00Z",
        )
        gs.add_decision(d1)
        gs.add_decision(d2)
        payload = gs.get_node_payload("d-update")
        assert payload["title"] == "Updated"
        assert payload["rationale"] == "Second"
        assert payload["status"] == "accepted"

    def test_add_decision_keeps_node_type_on_merge(self) -> None:
        gs = GraphStore()
        d1 = Decision(
            id="d-type", title="Test", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="d-type", title="Updated", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-02T00:00:00Z",
            modified_at="2024-01-02T00:00:00Z",
        )
        gs.add_decision(d1)
        gs.add_decision(d2)
        decisions = gs.get_all_decisions()
        assert any(d.id == "d-type" for d in decisions)

    def test_add_decision_on_code_entity_raises(self) -> None:
        gs = GraphStore()
        gs.add_code_entity(CodeEntity(
            id="collision", type=EntityType.FILE, repo="",
        ))
        d = Decision(
            id="collision", title="Collision", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="already exists as CodeEntity"):
            gs.add_decision(d)

    def test_add_constraint_on_decision_raises(self) -> None:
        gs = GraphStore()
        gs.add_decision(Decision(
            id="col", title="Existing", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))
        c = Constraint(
            id="col", description="New constraint",
            source="test", status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="already exists as Decision"):
            gs.add_constraint(c)

    def test_add_code_entity_on_decision_raises(self) -> None:
        gs = GraphStore()
        gs.add_decision(Decision(
            id="ent-col", title="Exists", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        ))
        e = CodeEntity(id="ent-col", type=EntityType.FILE, repo="")
        with pytest.raises(ValueError, match="already exists as Decision"):
            gs.add_code_entity(e)

    def test_add_code_entity_twice_updates_payload(self) -> None:
        gs = GraphStore()
        e1 = CodeEntity(id="e-update", type=EntityType.FILE, repo="a")
        e2 = CodeEntity(id="e-update", type=EntityType.CLASS, repo="b")
        gs.add_code_entity(e1)
        gs.add_code_entity(e2)
        payload = gs.get_node_payload("e-update")
        assert payload["type"] == "class"
        assert payload["repo"] == "b"

    def test_add_constraint_twice_updates_payload(self) -> None:
        gs = GraphStore()
        c1 = Constraint(
            id="c-update", description="Old rule",
            source="test", status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        c2 = Constraint(
            id="c-update", description="New rule", bound_entities=["src/x.py"],
            source="test", status="active",
            created_at="2024-01-02T00:00:00Z",
        )
        gs.add_constraint(c1)
        gs.add_constraint(c2)
        payload = gs.get_node_payload("c-update")
        assert payload["description"] == "New rule"
        assert payload["status"] == "active"


# ------------------------------------------------------------------
# SQLite-backed GraphStore tests
# ------------------------------------------------------------------


class TestSQLitePersistence:
    """Tests that verify SQLite-backed GraphStore using in-memory databases."""

    @staticmethod
    def _populate(gs: GraphStore) -> None:
        d1 = Decision(
            id="d1",
            title="Use async",
            rationale="Better performance",
            source="ADR-001",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="d2",
            title="Adopt pydantic",
            rationale="Validation",
            source="ADR-002",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-02-01T00:00:00Z",
            modified_at="2024-02-01T00:00:00Z",
        )
        c1 = Constraint(
            id="c1",
            description="All I/O must be async",
            bound_entities=["src/services/io.py"],
            enforced_by="d1",
            source="ADR-001",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        e1 = CodeEntity(
            id="src/services/io.py",
            type=EntityType.FILE,
            repo="my-repo",
        )

        gs.add_decision(d1)
        gs.add_decision(d2)
        gs.add_constraint(c1)
        gs.add_code_entity(e1)
        gs.link("d1", "c1", "enforces")
        gs.link("c1", "src/services/io.py", "binds")

    def test_crud_and_persistence(self) -> None:
        """Data written via _write_back is queryable through the same instance."""
        gs = GraphStore(db_path=":memory:")
        self._populate(gs)

        assert gs.stats() == {
            "decisions": 2, "constraints": 1,
            "entities": 1, "edges": 2,
        }
        assert len(gs.get_all_decisions()) == 2
        assert len(gs.get_all_constraints()) == 1
        assert {d.id for d in gs.get_all_decisions()} == {"d1", "d2"}

        # Verify SQLite tables are populated (proof _write_back worked)
        assert gs._conn is not None
        row = gs._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
        assert row is not None and row[0] == 2

        # Query through SQLite path
        constraints = gs.get_constraints_for_entities(["src/services/io.py"])
        assert len(constraints) == 1
        assert constraints[0].id == "c1"

        evolution = gs.get_evolution("src/services/io.py")
        assert {d.id for d in evolution} == {"d1"}
        gs.close()

    def test_save_noop_when_db_path_set(self) -> None:
        """save() is a no-op in SQLite mode — no JSON written."""
        gs = GraphStore(db_path=":memory:")
        self._populate(gs)

        json_path = Path("graph.json")
        try:
            gs.save(str(json_path))
            assert not json_path.exists()
        finally:
            gs.close()

    def test_idempotent_add_persists(self) -> None:
        """Second add_decision with same ID overwrites in SQLite."""
        gs = GraphStore(db_path=":memory:")

        d1 = Decision(
            id="dup", title="First", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="dup", title="Second", rationale="Updated",
            source="test", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-02T00:00:00Z",
            modified_at="2024-01-02T00:00:00Z",
        )
        gs.add_decision(d1)
        gs.add_decision(d2)

        decisions = gs.get_all_decisions()
        assert len(decisions) == 1
        assert decisions[0].title == "Second"
        gs.close()

    def test_cross_type_collision_raises(self) -> None:
        """Adding a Decision with same ID as CodeEntity raises ValueError."""
        gs = GraphStore(db_path=":memory:")

        gs.add_code_entity(CodeEntity(
            id="collision", type=EntityType.FILE, repo="",
        ))
        d = Decision(
            id="collision", title="X", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="already exists as CodeEntity"):
            gs.add_decision(d)
        gs.close()

    def test_constraints_exact_match_sqlite(self) -> None:
        gs = GraphStore(db_path=":memory:")
        self._populate(gs)

        r = gs.get_constraints_for_entities(["src/services/io.py"])
        assert len(r) == 1

        r = gs.get_constraints_for_entities(["src/services/"])
        assert len(r) == 0

        r = gs.get_constraints_for_entities(["src/services/"], prefix_match=True)
        assert len(r) == 1
        gs.close()

    def test_stats_empty_sqlite(self) -> None:
        gs = GraphStore(db_path=":memory:")
        assert gs.stats() == {
            "decisions": 0, "constraints": 0,
            "entities": 0, "edges": 0,
        }
        gs.close()

    def test_load_classmethod_still_works(self) -> None:
        """JSON load classmethod works for backward compatibility."""
        gs = GraphStore()
        self._populate(gs)

        json_path = Path("test_graph.json")
        try:
            gs.save(str(json_path))
            loaded = GraphStore.load(str(json_path))
            assert loaded.stats() == {
                "decisions": 2, "constraints": 1,
                "entities": 1, "edges": 2,
            }
        finally:
            if json_path.exists():
                json_path.unlink()

    def test_evolution_sqlite(self) -> None:
        gs = GraphStore(db_path=":memory:")
        self._populate(gs)

        evo = gs.get_evolution("src/services/io.py")
        assert {d.id for d in evo} == {"d1"}

        evo = gs.get_evolution("src/services/")
        assert len(evo) == 0

        evo = gs.get_evolution("src/services/", prefix_match=True)
        assert {d.id for d in evo} == {"d1"}
        gs.close()

    def test_decisions_for_entities_sqlite(self) -> None:
        gs = GraphStore(db_path=":memory:")

        d = Decision(
            id="d-sql", title="SQL", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        e = CodeEntity(id="src/db.py", type=EntityType.FILE, repo="my-repo")
        gs.add_decision(d)
        gs.add_code_entity(e)
        gs.link("d-sql", "src/db.py", "affects")

        decisions = gs.get_decisions_for_entities(["src/db.py"])
        assert {d.id for d in decisions} == {"d-sql"}

        decisions = gs.get_decisions_for_entities(["nonexistent"])
        assert len(decisions) == 0

        decisions = gs.get_decisions_for_entities(["src/"], prefix_match=True)
        assert {d.id for d in decisions} == {"d-sql"}

        decisions = gs.get_decisions_for_entities(["src/"])
        assert len(decisions) == 0
        gs.close()

    def test_migration_creates_tables(self) -> None:
        """Verify that _migrate_schema creates all expected tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        GraphStore._migrate_schema(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        expected = {
            "decisions", "constraints", "entities", "edges",
            "project_sources", "extraction_log", "audit_log",
        }
        assert expected.issubset(tables)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 4
        conn.close()

    def test_migration_v4_normalizes_legacy_statuses(self, tmp_path: Path) -> None:
        """SCHEMA_VERSION=4 migration converts legacy status rows in SQLite."""
        db_path = tmp_path / "legacy.db"

        # Bring schema to v3 only, then insert legacy rows manually.
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        GraphStore._migrate_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        # Roll back to v3 to simulate an old DB and insert legacy statuses.
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            "INSERT INTO decisions (id, title, status, rationale, source, repo, "
            "affected_entities, rejected_alternatives, "
            "created_at, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("d-prop", "Proposed", "proposed", "r", "t", "", "[]", "[]",
             "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO decisions (id, title, status, rationale, source, repo, "
            "affected_entities, rejected_alternatives, "
            "created_at, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("d-sup", "Superseded", "superseded", "r", "t", "", "[]", "[]",
             "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Opening via GraphStore triggers the v4 migration.
        gs = GraphStore(db_path=db_path)
        assert gs._conn is not None
        version = gs._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 4

        rows = {
            row["id"]: row["status"]
            for row in gs._conn.execute("SELECT id, status FROM decisions")
        }
        assert rows["d-prop"] == "accepted"
        assert rows["d-sup"] == "deprecated"
        gs.close()

    def test_migration_v4_normalizes_mixedcase_statuses(self, tmp_path: Path) -> None:
        """v4 migration matches legacy statuses case-insensitively.

        Regression: the original migration used exact-match ``WHERE status =
        'proposed'`` while the Decision validator coerces case-insensitively.
        Hand-edited rows like ``Proposed`` / ``SUPERSEDED`` slipped past the
        migration but got normalized in-memory, producing SQLite/NetworkX
        drift until the next write-back.
        """
        db_path = tmp_path / "mixedcase.db"

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        GraphStore._migrate_schema(conn)
        # Roll back to v3 and insert mixed-case legacy statuses.
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            "INSERT INTO decisions (id, title, status, rationale, source, repo, "
            "affected_entities, rejected_alternatives, "
            "created_at, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("d-prop", "Proposed", "Proposed", "r", "t", "", "[]", "[]",
             "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO decisions (id, title, status, rationale, source, repo, "
            "affected_entities, rejected_alternatives, "
            "created_at, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("d-sup", "Superseded", "SUPERSEDED", "r", "t", "", "[]", "[]",
             "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        gs = GraphStore(db_path=db_path)
        assert gs._conn is not None
        rows = {
            row["id"]: row["status"]
            for row in gs._conn.execute("SELECT id, status FROM decisions")
        }
        assert rows["d-prop"] == "accepted"
        assert rows["d-sup"] == "deprecated"
        gs.close()


class TestSQLiteAuditLog:
    """Tests that verify audit_log entries are written on mutations (in-memory DB)."""

    def test_add_decision_logs_audit(self) -> None:
        gs = GraphStore(db_path=":memory:")

        d = Decision(
            id="d-audit", title="Test Audit", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_decision(d)

        assert gs._conn is not None
        rows = gs._conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id",
            ("d-audit",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["entity_type"] == "decision"
        assert rows[0]["action"] == "add"
        details = json.loads(rows[0]["details"])
        assert details["title"] == "Test Audit"
        assert details["status"] == "accepted"
        gs.close()

    def test_add_decision_modify_logs_audit(self) -> None:
        gs = GraphStore(db_path=":memory:")

        d1 = Decision(
            id="d-audit", title="First", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="d-audit", title="Second", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-02T00:00:00Z",
            modified_at="2024-01-02T00:00:00Z",
        )
        gs.add_decision(d1)
        gs.add_decision(d2)

        assert gs._conn is not None
        rows = gs._conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id",
            ("d-audit",),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["action"] == "add"
        assert rows[1]["action"] == "modify"
        gs.close()

    def test_add_constraint_logs_audit(self) -> None:
        gs = GraphStore(db_path=":memory:")

        c = Constraint(
            id="c-audit", description="A new constraint",
            source="t", status="active",
            created_at="2024-01-01T00:00:00Z",
        )
        gs.add_constraint(c)

        assert gs._conn is not None
        rows = gs._conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ?",
            ("c-audit",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["entity_type"] == "constraint"
        assert rows[0]["action"] == "add"
        details = json.loads(rows[0]["details"])
        assert details["description"] == "A new constraint"
        assert details["status"] == "active"
        gs.close()

    def test_add_code_entity_logs_audit(self) -> None:
        gs = GraphStore(db_path=":memory:")

        e = CodeEntity(id="src/test.py", type=EntityType.FILE, repo="my-repo")
        gs.add_code_entity(e)

        assert gs._conn is not None
        rows = gs._conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ?",
            ("src/test.py",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["entity_type"] == "entity"
        assert rows[0]["action"] == "add"
        details = json.loads(rows[0]["details"])
        assert details["type"] == "file"
        gs.close()

    def test_link_logs_audit(self) -> None:
        gs = GraphStore(db_path=":memory:")

        d = Decision(
            id="d-edge", title="Edge test", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        e = CodeEntity(id="src/edge.py", type=EntityType.FILE, repo="")
        gs.add_decision(d)
        gs.add_code_entity(e)
        gs.link("d-edge", "src/edge.py", "affects")

        assert gs._conn is not None
        rows = gs._conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = ? ORDER BY id",
            ("edge",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "link"
        details = json.loads(rows[0]["details"])
        assert details["source"] == "d-edge"
        assert details["target"] == "src/edge.py"
        assert details["relation"] == "affects"
        gs.close()

    def test_set_node_payload_logs_audit(self) -> None:
        gs = GraphStore(db_path=":memory:")

        d = Decision(
            id="d-mod", title="Payload Test", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_decision(d)

        # Clear audit entries from add_decision to isolate set_node_payload
        assert gs._conn is not None
        gs._conn.execute("DELETE FROM audit_log")
        gs._conn.commit()

        gs.set_node_payload("d-mod", {"title": "Updated Payload", "status": "accepted"})

        rows = gs._conn.execute(
            "SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id",
            ("d-mod",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["entity_type"] == "decision"
        assert rows[0]["action"] == "modify"
        details = json.loads(rows[0]["details"])
        assert "changed_keys" in details
        gs.close()

    def test_audit_log_not_written_without_conn(self) -> None:
        """Mutation methods don't crash when no SQLite connection is active."""
        gs = GraphStore()  # No db_path
        d = Decision(
            id="d-noaudit", title="No Audit", rationale="...",
            source="t", status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        gs.add_decision(d)  # Should not raise
        gs.link("d-noaudit", "d-noaudit", "self")
        gs.set_node_payload("d-noaudit", {"x": 1})
