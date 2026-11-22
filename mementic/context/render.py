"""Evolution, history, merged-pack, and JSON renderers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
    return f"- [{decision.created_at}] {decision.title}: {decision.rationale} (#{decision.id})"


def format_constraint_line(constraint: Constraint, *, marker: str = "") -> str:
    """Format one constraint with its bound paths and optional staleness suffix.

    Args:
        constraint: Constraint to render.
        marker: Prebuilt staleness suffix for this entry, supplied by the caller so
            the single-path and batched commands share one formatter. Empty when
            the entry's bindings are current or unclassified.

    Returns:
        One line: ``- <description> (#<id>, bound: <entities|none>)<marker>``.
    """
    entities = ", ".join(constraint.paths) if constraint.paths else "none"
    return f"- {constraint.description} (#{constraint.id}, bound: {entities}){marker}"


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
            lines.append(f"... and {total - shown} more decisions omitted (token budget).")

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

    constraint_label = f"{stats.shown_constraints} of {stats.matched_constraints}"
    decision_label = f"{stats.shown_decisions} of {stats.matched_decisions}"

    if keywords:
        keyword_summary = ", ".join(keywords)
        summary = (
            f"{constraint_label} constraints, {decision_label} decisions "
            f"matched for [{keyword_summary}]"
        )
    else:
        summary = f"{stats.shown_constraints} constraints, {stats.shown_decisions} decisions"

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

    Decisions and constraints are emitted in the order given, which for the
    CLI is the store's chronological order.

    Args:
        entity_path: Path being traced.
        decisions: ``(decision, overlap)`` tuples.
        constraints: Constraints bound to the path.

    Returns:
        Dict with ``summary``, ``file``, ``decisions``, and ``constraints``.
    """
    decisions_json: list[dict] = []
    for decision, _ in decisions:
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

    summary = f"{len(decisions)} decisions, {len(constraints)} constraints affecting {entity_path}"

    return {
        "summary": summary,
        "file": entity_path,
        "decisions": decisions_json,
        "constraints": constraints_json,
    }


def format_merged_history(
    paths: Sequence[str],
    decisions: Sequence[tuple[Decision, Sequence[str]]],
    constraints: Sequence[tuple[Constraint, Sequence[str]]],
    *,
    max_tokens: int = DEFAULT_EVOLUTION_MAX_TOKENS,
    unmatched: Sequence[str] = (),
    markers: Mapping[str, str] | None = None,
) -> str:
    """Render one merged pack for several queried paths.

    Args:
        paths: Unique canonical paths, in argument order.
        decisions: ``(decision, matched batch paths)`` pairs. Ranked with
            rank_and_truncate by matched-path count, then created_at desc.
        constraints: ``(constraint, matched batch paths)`` pairs, in caller
            (store) order.
        max_tokens: Shared budget for the decision lines only.
        unmatched: Paths that matched no decision and no constraint.
        markers: Optional entry-ID to staleness-suffix map, applied to
            constraint lines only; decision lines never carry a marker.

    Returns:
        Markdown merged pack.
    """
    marker_map: Mapping[str, str] = markers or {}
    matched_by_id = {decision.id: matched for decision, matched in decisions}

    def decision_line(decision: Decision) -> str:
        """Render one decision line with its matched-path suffix."""
        line = _compact_evolution_line(decision)
        matched = matched_by_id.get(decision.id, ())
        if matched:
            line += f" (matched: {', '.join(matched)})"
        return line

    selected, shown, total = rank_and_truncate(
        [(decision, len(matched)) for decision, matched in decisions],
        max_tokens,
        line_formatter=decision_line,
    )

    lines = [f"## Files queried ({len(paths)})"]
    lines.extend(f"- {path}" for path in paths)
    lines.append("")
    if total == 0:
        noun = "file" if len(paths) == 1 else "files"
        lines.append(f"No architectural decisions found for {len(paths)} {noun}.")
    else:
        lines.extend(decision_line(decision) for decision in selected)
        if shown < total:
            lines.append(f"... and {total - shown} more decisions omitted (token budget).")

    if constraints:
        lines.append("")
        lines.append("### Constraints")
        for constraint, matched in constraints:
            line = format_constraint_line(constraint)
            if matched:
                line += f" (matched: {', '.join(matched)})"
            lines.append(line + marker_map.get(constraint.id, ""))

    if unmatched:
        lines.append("")
        lines.append("### No rules matched")
        lines.extend(f"- {path}" for path in unmatched)

    return "\n".join(lines)
