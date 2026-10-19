"""Evolution, JSON, and file-history renderers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mementic.context.builder import (
    CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    build_context_pack,
)
from mementic.context.ranking import rank_and_truncate
from mementic.models import Constraint, Decision

DEFAULT_EVOLUTION_MAX_TOKENS: int = 1500
_RATIONALE_MAX_CHARS: int = 200


def _compact_evolution_line(decision: Decision) -> str:
    """Format a decision with provenance for evolution traces."""
    return (
        f"- [{decision.created_at}] {decision.title}: "
        f"{decision.rationale} (#{decision.id})"
    )


def format_evolution(
    entity_path: str,
    decisions: list[tuple[Decision, int]],
    max_tokens: int = DEFAULT_EVOLUTION_MAX_TOKENS,
) -> str:
    """Format an evolution trace using compact provenance lines.

    Args:
        entity_path: Entity path used in the heading.
        decisions: ``(decision, overlap)`` tuples.
        max_tokens: Token budget for the trace.

    Returns:
        Markdown evolution trace.
    """
    selected, shown, total = rank_and_truncate(
        decisions, max_tokens, line_formatter=_compact_evolution_line
    )

    lines = [f"## Evolution of {entity_path}"]
    if total == 0:
        lines.append(f"No architectural decisions found for {entity_path}.")
    else:
        for decision in selected:
            lines.append(_compact_evolution_line(decision))
        if shown < total:
            lines.append(
                f"... and {total - shown} more decisions omitted (token budget)."
            )

    return "\n".join(lines)


def format_context_pack_json(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int = CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    max_decision_tokens: int = CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    *,
    keywords: list[str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    base_dir: str | Path | None = None,
) -> dict:
    """Produce a JSON-serializable context pack.

    Args:
        decisions: Live decisions.
        constraints: Live constraints.
        max_constraint_tokens: Token budget for constraints.
        max_decision_tokens: Token budget for decisions.
        keywords: Optional query keywords.
        path_exists: Optional liveness predicate.
        base_dir: Optional entity-path anchor.

    Returns:
        Dict with ``summary``, ``constraints``, and ``decisions`` keys.
    """
    result = build_context_pack(
        decisions,
        constraints,
        max_constraint_tokens,
        max_decision_tokens,
        keywords=keywords,
        path_exists=path_exists,
        base_dir=base_dir,
    )
    stats = result.stats

    constraint_label = (
        f"{stats.shown_constraints} of {stats.matched_constraints}"
    )
    decision_label = f"{stats.shown_decisions} of {stats.matched_decisions}"

    if keywords:
        keyword_summary = ", ".join(keywords)
        summary = (
            f"{constraint_label} constraints, {decision_label} decisions "
            f"matched for [{keyword_summary}]"
        )
    else:
        summary = (
            f"{stats.shown_constraints} constraints, "
            f"{stats.shown_decisions} decisions"
        )

    truncated = (
        stats.shown_constraints < stats.matched_constraints
        or stats.shown_decisions < stats.matched_decisions
    )
    if truncated:
        summary += " (token budget)"

    return {
        "summary": summary,
        "constraints": [
            {
                "id": constraint.id,
                "description": constraint.description,
                "entities": constraint.paths,
            }
            for constraint in result.constraints
        ],
        "decisions": [
            {
                "id": decision.id,
                "title": decision.title,
                "rationale": decision.rationale,
                "entities": decision.paths,
            }
            for decision in result.decisions
        ],
    }


def format_file_history_json(
    entity_path: str,
    decisions: list[tuple[Decision, int]],
    constraints: list[Constraint],
) -> dict:
    """Produce a JSON-serializable file-history view.

    Args:
        entity_path: Path being traced.
        decisions: ``(decision, overlap)`` tuples.
        constraints: Constraints bound to the path.

    Returns:
        Dict with ``summary``, ``file``, ``decisions``, and ``constraints``.
    """
    ordered = sorted(
        decisions, key=lambda pair: pair[0].created_at, reverse=True
    )
    ordered = sorted(ordered, key=lambda pair: pair[1], reverse=True)
    decisions_json: list[dict] = []
    for decision, _ in ordered:
        rationale = decision.rationale
        if len(rationale) > _RATIONALE_MAX_CHARS:
            rationale = rationale[:_RATIONALE_MAX_CHARS] + "..."
        decisions_json.append(
            {
                "id": decision.id,
                "title": decision.title,
                "rationale": rationale,
                "created_at": decision.created_at,
            }
        )

    constraints_json: list[dict] = [
        {
            "id": constraint.id,
            "description": constraint.description,
            "created_at": constraint.created_at,
        }
        for constraint in constraints
    ]

    summary = (
        f"{len(decisions)} decisions, {len(constraints)} constraints "
        f"affecting {entity_path}"
    )

    return {
        "summary": summary,
        "file": entity_path,
        "decisions": decisions_json,
        "constraints": constraints_json,
    }
