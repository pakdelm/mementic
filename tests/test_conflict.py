"""Tests for mementic conflict detection engine (Spec 06c F2)."""

from __future__ import annotations

import pytest

from mementic.conflict import _word_overlap, detect_conflicts
from mementic.graph import GraphStore
from mementic.models import CodeEntity, Decision, DecisionStatus, EntityType


def make_decision(
    id: str = "d-001",
    title: str = "Use async",
    rationale: str = "Better performance and scalability",
    status: DecisionStatus = DecisionStatus.ACCEPTED,
    tags: list[str] | None = None,
) -> Decision:
    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        source="test",
        status=status,
        timestamp="2024-01-01T00:00:00Z",
        tags=tags or [],
    )


class TestWordOverlap:
    def test_identical_texts(self) -> None:
        assert _word_overlap("use async for io", "use async for io") == 1.0

    def test_no_overlap(self) -> None:
        assert _word_overlap("use async for io", "adopt pydantic models") == 0.0

    def test_partial_overlap(self) -> None:
        ratio = _word_overlap("use async for io", "use async everywhere")
        # "use" and "async" overlap, "for", "io", "everywhere" don't
        # intersection: {use, async} = 2
        # union: {use, async, for, io, everywhere} = 5
        # ratio = 2/5 = 0.4
        assert ratio == pytest.approx(0.4)

    def test_both_empty(self) -> None:
        assert _word_overlap("", "") == 1.0

    def test_one_empty(self) -> None:
        assert _word_overlap("text", "") == 0.0

    def test_case_insensitive(self) -> None:
        assert _word_overlap("Use Async", "use async") == 1.0


class TestDetectConflicts:
    def test_slug_collision_detected(self) -> None:
        """Decision whose slugified title matches existing accepted ID."""
        graph = GraphStore()
        existing = make_decision(
            id="use-jwt-auth",
            title="Use JWT Auth",
            rationale="Standard token-based auth",
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="new-1",
                title="Use JWT Auth",  # slugifies to "use-jwt-auth"
                rationale="Some different rationale about JWT",
                status=DecisionStatus.PROPOSED,
            )
        ]

        conflicts = detect_conflicts(graph, proposed, [])
        assert len(conflicts) == 1
        assert conflicts[0].id == "use-jwt-auth"

    def test_no_conflict_different_topics(self) -> None:
        """Decisions on different topics with no entity overlap."""
        graph = GraphStore()
        existing = make_decision(
            id="use-async",
            title="Use async",
            rationale="Better performance",
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="new-1",
                title="Adopt pydantic",
                rationale="Type safety for data models",
                status=DecisionStatus.PROPOSED,
            )
        ]

        conflicts = detect_conflicts(graph, proposed, [])
        assert len(conflicts) == 0

    def test_entity_overlap_with_divergent_rationale(self) -> None:
        """Proposed decision affects same entities but rationale is very different."""
        graph = GraphStore()
        existing = make_decision(
            id="use-sqlite",
            title="Use SQLite",
            rationale="Simple persistence with no server needed",
        )
        graph.add_decision(existing)
        graph.add_code_entity(CodeEntity(id="src/db.py", type=EntityType.FILE))
        graph.link("use-sqlite", "src/db.py", "affects")

        proposed = [
            make_decision(
                id="new-1",
                title="Replace DB layer",
                rationale="Need distributed transactions across multiple nodes",
                status=DecisionStatus.PROPOSED,
            )
        ]

        conflicts = detect_conflicts(graph, proposed, {"new-1": ["src/db.py"]})
        assert len(conflicts) == 1
        assert conflicts[0].id == "use-sqlite"

    def test_entity_overlap_with_similar_rationale_no_conflict(self) -> None:
        """Same entities but similar rationale — no conflict (it's a refinement)."""
        graph = GraphStore()
        existing = make_decision(
            id="use-sqlite",
            title="Use SQLite",
            rationale="Simple persistence with no server needed for local storage",
        )
        graph.add_decision(existing)
        graph.add_code_entity(CodeEntity(id="src/db.py", type=EntityType.FILE))
        graph.link("use-sqlite", "src/db.py", "affects")

        proposed = [
            make_decision(
                id="new-1",
                title="SQLite config update",
                rationale="Simple persistence with no server needed for storage layer",
                status=DecisionStatus.PROPOSED,
            )
        ]

        conflicts = detect_conflicts(graph, proposed, {"new-1": ["src/db.py"]})
        # Rationales are very similar (>50% overlap), so no conflict
        assert len(conflicts) == 0

    def test_no_conflict_when_no_entity_overlap(self) -> None:
        """Different entities, different topics — no conflict."""
        graph = GraphStore()
        existing = make_decision(
            id="use-sqlite",
            title="Use SQLite",
            rationale="Simple persistence",
        )
        graph.add_decision(existing)
        graph.add_code_entity(CodeEntity(id="src/db.py", type=EntityType.FILE))
        graph.link("use-sqlite", "src/db.py", "affects")

        proposed = [
            make_decision(
                id="new-1",
                title="Add auth middleware",
                rationale="Need JWT authentication",
                status=DecisionStatus.PROPOSED,
            )
        ]

        conflicts = detect_conflicts(graph, proposed, {"new-1": ["src/auth.py"]})
        assert len(conflicts) == 0

    def test_only_accepted_decisions_checked(self) -> None:
        """Deprecated existing decisions are not checked for conflicts."""
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
                id="new-1",
                title="Old Idea",  # slugifies to "old-idea"
                rationale="New rationale",
                status=DecisionStatus.PROPOSED,
            )
        ]

        conflicts = detect_conflicts(graph, proposed, [])
        assert len(conflicts) == 0

    def test_empty_graph_no_conflicts(self) -> None:
        graph = GraphStore()
        proposed = [
            make_decision(
                id="new-1",
                title="Some decision",
                rationale="Some rationale",
                status=DecisionStatus.PROPOSED,
            )
        ]
        conflicts = detect_conflicts(graph, proposed, [])
        assert len(conflicts) == 0

    def test_multiple_proposed_with_mixed_conflicts(self) -> None:
        """Multiple proposals, some conflict, some don't."""
        graph = GraphStore()
        existing = make_decision(
            id="use-jwt",
            title="Use JWT",
            rationale="Token-based auth",
        )
        graph.add_decision(existing)

        proposed = [
            make_decision(
                id="new-1",
                title="Use JWT",  # conflicts via slug
                rationale="Better JWT approach",
                status=DecisionStatus.PROPOSED,
            ),
            make_decision(
                id="new-2",
                title="Add rate limiting",  # no conflict
                rationale="Protect against abuse",
                status=DecisionStatus.PROPOSED,
            ),
        ]

        conflicts = detect_conflicts(graph, proposed, [])
        assert len(conflicts) == 1
        assert conflicts[0].id == "use-jwt"
