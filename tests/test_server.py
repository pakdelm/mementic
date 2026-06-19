"""Tests for mementic server helpers — _deprecate_entity, review_context, and summary prefixes."""

from __future__ import annotations

import asyncio
import pytest

from mementic.config import MementicConfig
from mementic.graph import GraphStore
from mementic.models import Constraint, Decision, DecisionStatus, EntityType, CodeEntity
from mementic.server import _deprecate_entity, review_context

# ── Helpers ──────────────────────────────────────────────────────────


def _add_decision(gs: GraphStore, d_id: str, status: DecisionStatus) -> None:
    d = Decision(
        id=d_id,
        title="Test Decision",
        rationale="Test rationale",
        source="test",
        status=status,
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
    )
    gs.add_decision(d)


def _add_constraint(gs: GraphStore, c_id: str, status: str, bound_entities: list[str] | None = None) -> None:
    c = Constraint(
        id=c_id,
        description="Test constraint",
        source="test",
        status=status,
        created_at="2024-01-01T00:00:00Z",
        bound_entities=bound_entities or [],
    )
    gs.add_constraint(c)


def _add_code_entity(gs: GraphStore, e_id: str) -> None:
    e = CodeEntity(id=e_id, type=EntityType.FILE, repo="test-repo")
    gs.add_code_entity(e)


# ── _deprecate_entity ────────────────────────────────────────────────


class TestDeprecateEntity:
    """Tests for the _deprecate_entity helper function."""

    def test_deprecate_active_decision(self) -> None:
        gs = GraphStore()
        _add_decision(gs, "d1", DecisionStatus.ACCEPTED)

        result = _deprecate_entity(gs, "d1")
        assert result["status"] == "deprecated"
        assert "d1" in result["message"]

        payload = gs.get_node_payload("d1")
        assert payload["status"] == "deprecated"
        assert "[DEPRECATED]" in payload["rationale"]

    def test_deprecate_active_decision_with_reason(self) -> None:
        gs = GraphStore()
        _add_decision(gs, "d2", DecisionStatus.ACCEPTED)

        result = _deprecate_entity(gs, "d2", reason="No longer needed")
        assert result["status"] == "deprecated"
        payload = gs.get_node_payload("d2")
        assert "[DEPRECATED: No longer needed]" in payload["rationale"]

    def test_deprecate_active_constraint(self) -> None:
        gs = GraphStore()
        _add_constraint(gs, "c1", "active")

        result = _deprecate_entity(gs, "c1")
        assert result["status"] == "deprecated"

        payload = gs.get_node_payload("c1")
        assert payload["status"] == "deprecated"
        assert "[DEPRECATED]" in payload["description"]

    def test_deprecate_active_constraint_with_reason(self) -> None:
        gs = GraphStore()
        _add_constraint(gs, "c2", "active")

        result = _deprecate_entity(gs, "c2", reason="Obsolete")
        assert result["status"] == "deprecated"
        payload = gs.get_node_payload("c2")
        assert "[DEPRECATED: Obsolete]" in payload["description"]

    def test_deprecate_already_deprecated_decision(self) -> None:
        gs = GraphStore()
        _add_decision(gs, "d_dep", DecisionStatus.DEPRECATED)

        result = _deprecate_entity(gs, "d_dep")
        assert result["status"] == "already_deprecated"
        assert "already deprecated" in result["message"].lower()

    def test_deprecate_already_deprecated_constraint(self) -> None:
        gs = GraphStore()
        _add_constraint(gs, "c_dep", "deprecated")

        result = _deprecate_entity(gs, "c_dep")
        assert result["status"] == "already_deprecated"
        assert "already deprecated" in result["message"].lower()

    def test_deprecate_nonexistent_entity(self) -> None:
        gs = GraphStore()

        result = _deprecate_entity(gs, "nonexistent")
        assert result["status"] == "not_found"
        assert "not found" in result["message"].lower()

    def test_deprecate_code_entity_returns_wrong_type(self) -> None:
        gs = GraphStore()
        _add_code_entity(gs, "src/test.py")

        result = _deprecate_entity(gs, "src/test.py")
        assert result["status"] == "wrong_type"
        assert "CodeEntity" in result["message"]


# ── review_context ──────────────────────────────────────────────────


def _make_review_graph() -> GraphStore:
    """Create a GraphStore with decisions, constraints, and code entities.

    Constraints have real bound_entities pointing to existing CodeEntity
    nodes so clean_dead_references doesn't auto-deprecate them.
    """
    gs = GraphStore()

    _add_decision(gs, "d_active", DecisionStatus.ACCEPTED)
    _add_decision(gs, "d_active2", DecisionStatus.ACCEPTED)

    _add_code_entity(gs, "src/auth.py")
    _add_code_entity(gs, "src/models.py")

    _add_constraint(gs, "c_active", "active", bound_entities=["src/auth.py"])
    _add_constraint(gs, "c_active2", "active", bound_entities=["src/models.py"])

    return gs


def _mock_config() -> MementicConfig:
    """Return a MementicConfig suitable for patching _load_graph.

    Uses a real MementicConfig instance — lightweight and has all the
    nested default attributes (output, retrieval, etc.) that the tools
    access.
    """
    return MementicConfig()


class TestReviewContext:
    """Tests for the review_context MCP tool."""

    def test_no_deprecated_ids_reports_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no deprecated_ids provided, returns clean summary with active counts."""
        gs = _make_review_graph()
        cfg = _mock_config()

        from mementic import server
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))
        monkeypatch.setattr(server, "_save_graph", lambda g, c: None)

        result = asyncio.run(review_context("Test feature"))

        assert result.startswith("---SUMMARY: review_context: 'Test feature'")
        assert "4 active" in result
        assert "2 decisions" in result
        assert "2 constraints" in result

    def test_deprecate_valid_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecating valid IDs produces summary with deprecated and remaining counts."""
        gs = _make_review_graph()
        cfg = _mock_config()

        from mementic import server
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))
        monkeypatch.setattr(server, "_save_graph", lambda g, c: None)

        result = asyncio.run(
            review_context("Refactor auth", deprecated_ids=["d_active", "c_active"])
        )

        assert "deprecated d_active, c_active" in result
        assert "2 active" in result  # d_active2 + c_active2
        assert "1 decisions" in result
        assert "1 constraints" in result

    def test_mixed_valid_and_already_deprecated_and_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mixed IDs: some successfully deprecated, some already deprecated, some not found."""
        gs = _make_review_graph()
        cfg = _mock_config()

        # Pre-deprecate one decision
        _deprecate_entity(gs, "d_active")

        from mementic import server
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))
        monkeypatch.setattr(server, "_save_graph", lambda g, c: None)

        result = asyncio.run(
            review_context(
                "Mixed test",
                deprecated_ids=["d_active", "c_active", "ghost-id"],
            )
        )

        assert "deprecated c_active" in result
        assert "already deprecated: d_active" in result
        assert "not found: ghost-id" in result
        # d_active already deprecated, c_active just got deprecated
        # Remaining active: d_active2 + c_active2 = 2
        assert "2 active" in result
        assert "1 decisions" in result
        assert "1 constraints" in result

    def test_clean_dead_references_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify clean_dead_references() is called during review_context.

        Adds an orphaned constraint (bound_entities pointing to nonexistent
        nodes) and verifies it gets auto-deprecated by clean_dead_references.
        """
        gs = _make_review_graph()
        cfg = _mock_config()

        # Add an orphaned constraint whose bound_entities point to nothing
        orphaned = Constraint(
            id="c_orphan",
            description="Orphaned constraint",
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
            bound_entities=["nonexistent_entity"],
        )
        gs.add_constraint(orphaned)

        from mementic import server
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))
        monkeypatch.setattr(server, "_save_graph", lambda g, c: None)

        result = asyncio.run(review_context("Orphan test"))

        # After clean_dead_references, the orphaned constraint's bound_entities
        # get filtered to [] and the constraint is auto-deprecated
        # Active left: d_active, d_active2, c_active, c_active2 = 4
        # (c_orphan is now deprecated, c_active's bound to src/auth.py stays intact)
        assert "4 active" in result

    def test_empty_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """review_context on an empty graph reports 0 active."""
        gs = GraphStore()
        cfg = _mock_config()

        from mementic import server
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))
        monkeypatch.setattr(server, "_save_graph", lambda g, c: None)

        result = asyncio.run(review_context("Empty graph"))
        assert "0 active" in result
        assert "0 decisions" in result
        assert "0 constraints" in result


# ── Summary prefixes ────────────────────────────────────────────────


class TestSummaryPrefixes:
    """Tests that MCP tool responses include ---SUMMARY prefixes."""

    def test_get_task_context_summary_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_task_context response starts with ---SUMMARY: get_task_context: N constraints, M decisions."""
        from mementic import server

        gs = GraphStore()
        _add_decision(gs, "d1", DecisionStatus.ACCEPTED)
        _add_decision(gs, "d2", DecisionStatus.ACCEPTED)

        # Constraint needs real bound_entities to survive clean_dead_references
        _add_code_entity(gs, "src/test.py")
        _add_constraint(gs, "c1", "active", bound_entities=["src/test.py"])

        cfg = _mock_config()
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))

        result = asyncio.run(
            server.get_task_context("Fix auth bug in login flow")
        )

        assert result.startswith("---SUMMARY: get_task_context: ")
        assert "1 constraints" in result
        assert "2 decisions" in result
        assert "Fix auth bug" in result

    def test_get_entity_history_summary_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_entity_history response starts with ---SUMMARY: get_entity_history."""
        from mementic import server

        gs = GraphStore()

        # Add a decision, code entity, and constraint that links them
        d = Decision(
            id="d1",
            title="Test Decision",
            rationale="Test rationale",
            source="test",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
            affected_entities=["src/auth.py"],
        )
        gs.add_decision(d)

        # Add the code entity
        e = CodeEntity(id="src/auth.py", type=EntityType.FILE, repo="test")
        gs.add_code_entity(e)

        # Add a constraint bound to the entity
        c = Constraint(
            id="c1",
            description="Auth constraint",
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
            bound_entities=["src/auth.py"],
            enforced_by="d1",
        )
        gs.add_constraint(c)

        # Link: decision → constraint (enforces)
        gs.link("d1", "c1", "enforces")
        # Link: constraint → code entity (binds)
        gs.link("c1", "src/auth.py", "binds")

        cfg = _mock_config()
        monkeypatch.setattr(server, "_load_graph", lambda project=None: (gs, cfg))

        result = asyncio.run(
            server.get_entity_history("src/auth.py")
        )

        assert result.startswith("---SUMMARY: get_entity_history: ")
        assert "decisions touching 'src/auth.py'" in result
        assert "1 decisions" in result
