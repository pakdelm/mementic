from __future__ import annotations

import json
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
        timestamp="2024-01-01T00:00:00Z",
        tags=["performance"],
    )
    d2 = Decision(
        id="d2",
        title="Adopt pydantic",
        rationale="Validation",
        source="ADR-002",
        status=DecisionStatus.PROPOSED,
        timestamp="2024-02-01T00:00:00Z",
        tags=["validation"],
    )
    d3 = Decision(
        id="d3",
        title="Use SQLite",
        rationale="Simple persistence",
        source="ADR-003",
        status=DecisionStatus.ACCEPTED,
        timestamp="2024-03-01T00:00:00Z",
        tags=["storage"],
    )

    c1 = Constraint(
        id="c1",
        description="All I/O must be async",
        bound_entities=["src/services/io.py"],
        enforced_by="d1",
        source="ADR-001",
        status="active",
        timestamp="2024-01-01T00:00:00Z",
    )
    c2 = Constraint(
        id="c2",
        description="Data models use pydantic",
        bound_entities=["src/models/base.py", "src/models/types.py"],
        enforced_by="d2",
        source="ADR-002",
        status="active",
        timestamp="2024-02-01T00:00:00Z",
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

    def test_get_pending_decisions(self, populated_graph: GraphStore) -> None:
        pending = populated_graph.get_pending_decisions()
        assert len(pending) == 1
        assert pending[0].id == "d2"
        assert pending[0].status == DecisionStatus.PROPOSED

    def test_get_pending_constraints(self) -> None:
        gs = GraphStore()
        c_active = Constraint(
            id="c1",
            description="A constraint",
            source="src",
            status="active",
            timestamp="2024-01-01T00:00:00Z",
        )
        c_pending = Constraint(
            id="c2",
            description="A pending constraint",
            source="src",
            status="proposed",
            timestamp="2024-01-01T00:00:00Z",
        )
        gs.add_constraint(c_active)
        gs.add_constraint(c_pending)
        pending = gs.get_pending_constraints()
        assert len(pending) == 1
        assert pending[0].id == "c2"

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

    def test_prefix_match(self, populated_graph: GraphStore) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/services/"]
        )
        assert len(result) == 1
        assert result[0].id == "c1"

    def test_multiple_paths(self, populated_graph: GraphStore) -> None:
        result = populated_graph.get_constraints_for_entities(
            ["src/services/io.py", "src/models/base.py"]
        )
        assert len(result) == 2


class TestTraversal:
    def test_get_decisions_for_entities(self, populated_graph: GraphStore) -> None:
        decisions = populated_graph.get_decisions_for_entities(
            ["src/services/io.py"]
        )
        ids = {d.id for d in decisions}
        assert "d1" in ids

    def test_get_decisions_direct_link(self, populated_graph: GraphStore) -> None:
        decisions = populated_graph.get_decisions_for_entities(
            ["src/models/base.py"]
        )
        ids = {d.id for d in decisions}
        assert "d2" in ids
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

    def test_get_evolution_prefix(self, populated_graph: GraphStore) -> None:
        evolution = populated_graph.get_evolution("src/models/base.py:BaseModel")
        ids = {d.id for d in evolution}
        assert "d2" in ids


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
            timestamp="2024-01-01T00:00:00Z",
        ))
        assert gs.has_outgoing_edges("lonely") is False


class TestAuditLog:
    def test_save_writes_audit_log(self, populated_graph: GraphStore) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            populated_graph.save(graph_path)
            audit_path = Path(tmpdir) / "audit.jsonl"
            assert audit_path.exists()
            lines = audit_path.read_text().strip().split("\n")
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert "timestamp" in entry
            assert entry["action"] == "save"
            assert entry["decisions"] == 3
            assert entry["constraints"] == 2
            assert entry["entities"] == 4
            assert entry["edges"] == 6


class TestIdempotentAdd:
    def test_add_decision_twice_updates_payload(self) -> None:
        gs = GraphStore()
        d1 = Decision(
            id="d-update", title="Original", rationale="First",
            source="test", status=DecisionStatus.PROPOSED,
            timestamp="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="d-update", title="Updated", rationale="Second",
            source="test", status=DecisionStatus.ACCEPTED,
            timestamp="2024-01-02T00:00:00Z",
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
            source="test", status=DecisionStatus.PROPOSED,
            timestamp="2024-01-01T00:00:00Z",
        )
        d2 = Decision(
            id="d-type", title="Updated", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            timestamp="2024-01-02T00:00:00Z",
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
            source="test", status=DecisionStatus.PROPOSED,
            timestamp="2024-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="already exists as CodeEntity"):
            gs.add_decision(d)

    def test_add_constraint_on_decision_raises(self) -> None:
        gs = GraphStore()
        gs.add_decision(Decision(
            id="col", title="Existing", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            timestamp="2024-01-01T00:00:00Z",
        ))
        c = Constraint(
            id="col", description="New constraint",
            source="test", status="proposed",
            timestamp="2024-01-01T00:00:00Z",
        )
        with pytest.raises(ValueError, match="already exists as Decision"):
            gs.add_constraint(c)

    def test_add_code_entity_on_decision_raises(self) -> None:
        gs = GraphStore()
        gs.add_decision(Decision(
            id="ent-col", title="Exists", rationale="...",
            source="test", status=DecisionStatus.ACCEPTED,
            timestamp="2024-01-01T00:00:00Z",
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
            source="test", status="proposed",
            timestamp="2024-01-01T00:00:00Z",
        )
        c2 = Constraint(
            id="c-update", description="New rule", bound_entities=["src/x.py"],
            source="test", status="active",
            timestamp="2024-01-02T00:00:00Z",
        )
        gs.add_constraint(c1)
        gs.add_constraint(c2)
        payload = gs.get_node_payload("c-update")
        assert payload["description"] == "New rule"
        assert payload["status"] == "active"
