"""Tests for mementic auto-resolve capture engine."""

from __future__ import annotations

from mementic.conflict import _word_overlap, auto_resolve_capture
from mementic.graph import GraphStore
from mementic.models import Constraint, Decision, DecisionStatus


def make_decision(
    id: str = "d-001",
    title: str = "Use async",
    rationale: str = "Better performance and scalability",
    status: DecisionStatus = DecisionStatus.ACCEPTED,
) -> Decision:
    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        source="test",
        status=status,
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
    )


class TestWordOverlap:
    def test_identical_texts(self) -> None:
        assert _word_overlap("use async for io", "use async for io") == 1.0

    def test_no_overlap(self) -> None:
        assert _word_overlap("use async for io", "adopt pydantic models") == 0.0

    def test_partial_overlap(self) -> None:
        ratio = _word_overlap("use async for io", "use async everywhere")
        assert ratio == 0.4

    def test_both_empty(self) -> None:
        assert _word_overlap("", "") == 1.0

    def test_one_empty(self) -> None:
        assert _word_overlap("text", "") == 0.0

    def test_case_insensitive(self) -> None:
        assert _word_overlap("Use Async", "use async") == 1.0


class TestAutoResolveCapture:
    """Tests for auto_resolve_capture three-rule logic."""

    # ── Rule 1: New slug → ACCEPT ──

    def test_new_slug_accepted(self) -> None:
        """Decision with no existing ID match is counted as new."""
        graph = GraphStore()
        existing = make_decision(id="use-async", title="Use async", rationale="Better performance")
        graph.add_decision(existing)

        proposed = [
            make_decision(id="adopt-pydantic", title="Adopt pydantic", rationale="Type safety"),
        ]
        counts = auto_resolve_capture(graph, proposed)
        assert counts["new_decisions"] == 1
        assert counts["updated_decisions"] == 0
        assert counts["skipped_decisions"] == 0

    # ── Rule 2: Same slug, same content → SKIP ──

    def test_same_slug_same_content_skipped(self) -> None:
        """Decision with same ID and same rationale (>=80% overlap) is skipped."""
        graph = GraphStore()
        existing = make_decision(
            id="use-jwt-auth",
            title="Use JWT Auth",
            rationale="Standard token-based auth for APIs",
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="use-jwt-auth",
                title="Use JWT Auth",
                rationale="Standard token-based auth for APIs",  # same content
            ),
        ]
        counts = auto_resolve_capture(graph, proposed)
        assert counts["new_decisions"] == 0
        assert counts["updated_decisions"] == 0
        assert counts["skipped_decisions"] == 1

    def test_same_slug_similar_content_skipped(self) -> None:
        """Decision with same ID and very similar rationale is skipped."""
        graph = GraphStore()
        existing = make_decision(
            id="use-sqlite",
            title="Use SQLite",
            rationale="Simple persistence with no server needed for local storage",
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="use-sqlite",
                title="SQLite config update",
                rationale="Simple persistence with no server needed for storage layer",
            ),
        ]
        counts = auto_resolve_capture(graph, proposed)
        # Rationales are very similar (>80% overlap), so skip
        assert counts["skipped_decisions"] == 1
        assert counts["updated_decisions"] == 0

    # ── Rule 3: Same slug, different content → deprecate existing, ACCEPT ──

    def test_same_slug_different_content_updates(self) -> None:
        """Decision with same ID but different rationale is an update."""
        graph = GraphStore()
        existing = make_decision(
            id="use-jwt-auth",
            title="Use JWT Auth",
            rationale="Token-based auth for APIs",
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="use-jwt-auth",
                title="Use JWT Auth",
                rationale="Completely different approach to JWT authentication with rotating keys",
            ),
        ]
        counts = auto_resolve_capture(graph, proposed)
        assert counts["updated_decisions"] == 1
        assert counts["new_decisions"] == 0
        assert counts["skipped_decisions"] == 0

        # Verify existing decision was deprecated
        payload = graph.get_node_payload("use-jwt-auth")
        assert payload["status"] == "deprecated"

    # ── Empty graph ──

    def test_empty_graph_all_new(self) -> None:
        """All decisions are new when graph is empty."""
        graph = GraphStore()
        proposed = [
            make_decision(id="new-1", title="Some decision", rationale="Some rationale"),
        ]
        counts = auto_resolve_capture(graph, proposed)
        assert counts["new_decisions"] == 1
        assert counts["updated_decisions"] == 0
        assert counts["skipped_decisions"] == 0

    # ── Deprecated existing not matched (no existing node) ──

    def test_deprecated_existing_no_conflict(self) -> None:
        """Deprecated existing decision is treated same as absent — new decision accepted."""
        graph = GraphStore()
        existing = make_decision(
            id="old-idea",
            title="Old Idea",
            rationale="Some old rationale",
            status=DecisionStatus.DEPRECATED,
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="old-idea",
                title="Old Idea",
                rationale="New rationale completely different from old",
            ),
        ]
        counts = auto_resolve_capture(graph, proposed)
        # Existing node is DEPRECATED, but node exists. Rationales differ (< 0.8 overlap).
        # Rule 3: deprecate existing (already deprecated), accept new.
        assert counts["updated_decisions"] == 1

    # ── Multiple proposed decisions ──

    def test_multiple_proposed_mixed(self) -> None:
        """Multiple proposals: some new, some duplicates, some updates."""
        graph = GraphStore()
        graph.add_decision(make_decision(
            id="use-jwt",
            title="Use JWT",
            rationale="Token-based auth",
        ))

        proposed = [
            make_decision(
                id="use-jwt",
                title="Use JWT",
                rationale="Token-based auth",  # same → skip
            ),
            make_decision(
                id="add-rate-limiting",
                title="Add rate limiting",  # new
                rationale="Protect against abuse",
            ),
        ]
        counts = auto_resolve_capture(graph, proposed)
        assert counts["skipped_decisions"] == 1
        assert counts["new_decisions"] == 1
        assert counts["updated_decisions"] == 0

    # ── Constraints ──

    def test_constraints_new_and_duplicate(self) -> None:
        """New constraints accepted, duplicate constraints skipped."""
        graph = GraphStore()
        graph.add_constraint(Constraint(
            id="must-be-stateless",
            description="Services must be stateless",
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        ))

        proposed_constraints = [
            Constraint(
                id="must-be-stateless",
                description="Services must be stateless",  # duplicate
                source="test",
                status="active",
                created_at="2024-01-02T00:00:00Z",
            ),
            Constraint(
                id="use-https",
                description="All endpoints must use HTTPS",  # new
                source="test",
                status="active",
                created_at="2024-01-02T00:00:00Z",
            ),
        ]
        counts = auto_resolve_capture(
            graph, [], proposed_constraints
        )
        assert counts["skipped_constraints"] == 1
        assert counts["new_constraints"] == 1
        assert counts["updated_constraints"] == 0

    def test_constraint_update_different_description(self) -> None:
        """Constraint with same ID but different description triggers update."""
        graph = GraphStore()
        graph.add_constraint(Constraint(
            id="use-async-io",
            description="All I/O must be async",
            source="test",
            status="active",
            created_at="2024-01-01T00:00:00Z",
        ))

        proposed_constraints = [
            Constraint(
                id="use-async-io",
                description="All network and file I/O must use async patterns",
                source="test",
                status="active",
                created_at="2024-01-02T00:00:00Z",
            ),
        ]
        counts = auto_resolve_capture(
            graph, [], proposed_constraints
        )
        assert counts["updated_constraints"] == 1

        # Verify existing constraint was deprecated
        payload = graph.get_node_payload("use-async-io")
        assert payload["status"] == "deprecated"
