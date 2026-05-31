"""Conflict detection engine for Mementic capture.

Two deterministic rules, zero API cost:
1. Slug collision: slugify(title) matches existing accepted decision ID.
2. Entity overlap: overlapping affected entities + substantially different rationales.
"""

from __future__ import annotations

from mementic.format import slugify
from mementic.graph import GraphStore
from mementic.models import Decision


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


def detect_conflicts(
    graph: GraphStore,
    proposed: list[Decision],
    affected_entities: list[str],
) -> list[Decision]:
    """Detect conflicts between proposed decisions and existing accepted ones.

    Args:
        graph: The current knowledge graph.
        proposed: Newly extracted decisions to check for conflicts.
        affected_entities: File paths affected by the proposed decisions.

    Returns:
        List of existing accepted Decision objects that conflict with
        the proposed decisions. An empty list means no conflicts.

    Implements two deterministic rules:
    1. Slug collision — slugified title matches an existing accepted ID.
    2. Entity overlap — proposed entities overlap with an existing decision's
       entities, AND the rationales differ substantially (<50% word overlap).
    """
    conflicts: list[Decision] = []
    existing = graph.get_all_decisions()

    # Only compare against accepted decisions
    accepted = [d for d in existing if d.status == "accepted"]

    for p in proposed:
        p_slug = slugify(p.title, max_length=80)

        for existing_d in accepted:
            # Rule 1: Slug collision
            if p_slug == existing_d.id:
                conflicts.append(existing_d)
                continue

            # Rule 2: Entity overlap + rationale divergence
            # Get the entities the existing decision affects by following its edges
            existing_entities: set[str] = set()
            for _nid, ntype, _payload in graph.iter_nodes():
                if ntype != "CodeEntity":
                    continue
                # Check if this entity node has an incoming edge from the existing decision
                for pred in graph._g.predecessors(_nid):
                    if pred == existing_d.id:
                        existing_entities.add(_nid)

            # If no existing entities to compare, skip
            if not existing_entities:
                continue

            proposed_set = set(affected_entities)
            overlap = existing_entities & proposed_set
            if not overlap:
                continue

            # Check rationale similarity
            overlap_ratio = _word_overlap(p.rationale, existing_d.rationale)
            if overlap_ratio < 0.5:
                conflicts.append(existing_d)

    # Deduplicate conflicts
    seen: set[str] = set()
    result: list[Decision] = []
    for d in conflicts:
        if d.id not in seen:
            seen.add(d.id)
            result.append(d)
    return result
