"""Auto-resolve capture conflicts for Mementic capture.

Three deterministic rules, zero API cost:
1. New slug (no existing with same ID) → ACCEPT.
2. Same slug, same content → SKIP (duplicate).
3. Same slug, different content → deprecate existing, ACCEPT new.
"""

from __future__ import annotations

from mementic.graph import GraphStore
from mementic.models import Constraint, Decision, DecisionStatus


def _word_overlap(text_a: str, text_b: str) -> float:
    """Compute word overlap ratio between two texts.

    Returns a float between 0.0 and 1.0, where 1.0 means identical word sets.
    Uses set intersection over union of lowercase words.
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def auto_resolve_capture(
    graph: GraphStore,
    proposed_decisions: list[Decision],
    proposed_constraints: list[Constraint] | None = None,
) -> dict[str, int]:
    """Auto-resolve captured decisions and constraints against the existing graph.

    Three rules (applied to both decisions and constraints):
    1. New slug (no existing node with same ID) → ACCEPT.
    2. Same slug, same content → SKIP (duplicate).
    3. Same slug, different content → deprecate existing, ACCEPT new.

    For decisions, "content" is the ``rationale`` field (compared via _word_overlap
    with threshold 0.8).  For constraints, "content" is the ``description`` field.

    Args:
        graph: The current knowledge graph.
        proposed_decisions: Newly extracted decisions.
        proposed_constraints: Newly extracted constraints (optional).

    Returns:
        Dict with counts:
        ``{'new_decisions': N, 'updated_decisions': N, 'skipped_decisions': N,
           'new_constraints': N, 'updated_constraints': N, 'skipped_constraints': N}``
    """
    counts: dict[str, int] = {
        "new_decisions": 0,
        "updated_decisions": 0,
        "skipped_decisions": 0,
        "new_constraints": 0,
        "updated_constraints": 0,
        "skipped_constraints": 0,
    }

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    for decision in proposed_decisions:
        decision.status = DecisionStatus.ACCEPTED
        if not graph.has_node(decision.id):
            # Rule 1 — new slug
            counts["new_decisions"] += 1
            continue

        # Existing node found — verify it's actually a Decision (not Constraint/Entity)
        payload = graph.get_node_payload(decision.id)
        if "rationale" not in payload:
            # Cross-type ID collision — treat as new (don't corrupt other node types)
            counts["new_decisions"] += 1
            continue
        existing_rationale: str = payload.get("rationale", "")
        overlap = _word_overlap(decision.rationale, existing_rationale)
        if overlap >= 0.8:
            # Rule 2 — same content → skip
            counts["skipped_decisions"] += 1
        else:
            # Rule 3 — different content → deprecate existing, accept new
            payload["status"] = DecisionStatus.DEPRECATED.value
            graph.set_node_payload(decision.id, payload)
            counts["updated_decisions"] += 1

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    if proposed_constraints:
        for constraint in proposed_constraints:
            constraint.status = "active"
            if not graph.has_node(constraint.id):
                counts["new_constraints"] += 1
                continue

            payload = graph.get_node_payload(constraint.id)
            if "description" not in payload:
                # Cross-type ID collision — treat as new
                counts["new_constraints"] += 1
                continue
            existing_desc: str = payload.get("description", "")
            overlap = _word_overlap(constraint.description, existing_desc)
            if overlap >= 0.8:
                counts["skipped_constraints"] += 1
            else:
                payload["status"] = "deprecated"
                graph.set_node_payload(constraint.id, payload)
                counts["updated_constraints"] += 1

    return counts
