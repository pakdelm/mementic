"""Context-pack construction, budget splits, and markdown section rendering."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from mementic.context.ranking import (
    document_frequencies,
    idf_weights,
    rank_and_truncate,
    rank_keyword_section,
    truncate_ranked,
)
from mementic.models import Constraint, Decision
from mementic.schema import utc_now
from mementic.text import truncate_entity_list

CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS: int = 500
CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS: int = 500

DEAD_PATH_GRACE_HOURS: int = 24

_TRUNCATION_HINT: str = "refine keywords or raise --max-tokens"

EntryT = TypeVar("EntryT", bound="Constraint | Decision")


def split_pack_budget(total: int | None) -> tuple[int, int]:
    """Split a total pack token budget into ``(constraints, decisions)``.

    ``None`` returns the defaults. Otherwise the total is divided
    proportionally to the default split; the constraint share is floored
    and the remainder goes to decisions, so no zero-token section is
    reachable.
    """
    if total is None:
        return (
            CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )
    share = CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS / (
        CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS + CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS
    )
    constraint_tokens = math.floor(total * share)
    return (constraint_tokens, total - constraint_tokens)


def _compact_constraint_line(constraint: Constraint) -> str:
    """Format a constraint as a compact single-line marker."""
    bound_suffix = ""
    if constraint.paths:
        entities = truncate_entity_list(constraint.paths)
        bound_suffix = f", bound: {entities}"
    return f"- {constraint.description} (#{constraint.id}{bound_suffix})"


def _compact_decision_line(decision: Decision) -> str:
    """Format a decision as a compact single-line marker."""
    affects_suffix = ""
    if decision.paths:
        entities = truncate_entity_list(decision.paths)
        affects_suffix = f", affects: {entities}"
    return f"- {decision.rationale} (#{decision.id}{affects_suffix})"


def _try_parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO 8601 timestamp; ``None`` when unparseable."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into an aware UTC datetime."""
    parsed = _try_parse_timestamp(value)
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed


def drop_dead_path_entries(
    entries: Sequence[EntryT],
    *,
    path_exists: Callable[[str], bool] | None = None,
    now: str | None = None,
    base_dir: str | Path | None = None,
) -> list[EntryT]:
    """Drop entries whose every bound path has vanished from disk.

    Entries with no bound paths always survive. An entry with at least one
    existing path survives. Entries created within
    :data:`DEAD_PATH_GRACE_HOURS` of ``now`` are exempt; unparseable
    ``created_at`` stamps also count as inside the grace window.
    """
    if path_exists is not None:
        exists = path_exists
    elif base_dir is not None:
        base = Path(base_dir)

        def exists(path: str) -> bool:
            return (base / path).exists()

    else:
        raise ValueError("path_exists or base_dir is required")

    now_dt = _parse_timestamp(now if now is not None else utc_now())
    cutoff = now_dt - timedelta(hours=DEAD_PATH_GRACE_HOURS)
    kept: list[EntryT] = []
    for entry in entries:
        if not entry.paths:
            kept.append(entry)
            continue
        if any(exists(path) for path in entry.paths):
            kept.append(entry)
            continue
        created = _try_parse_timestamp(entry.created_at)
        if created is not None and created < cutoff:
            continue
        kept.append(entry)
    return kept


@dataclass(kw_only=True)
class ContextPackStats:
    """Shown-vs-matched accounting for one rendered context pack."""

    matched_constraints: int = 0
    shown_constraints: int = 0
    matched_decisions: int = 0
    shown_decisions: int = 0


@dataclass(kw_only=True)
class ContextPackResult:
    """Rendered context pack text plus its accounting and selected items."""

    text: str
    stats: ContextPackStats
    constraints: list[Constraint] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)


def _render_keyword_section(
    heading: str,
    total_in: int,
    matched: int,
    selected: Sequence[Any],
    line_formatter: Callable[[Any], str],
    noun: str,
) -> list[str]:
    """Render one keyword-path section with honest accounting markers."""
    lines = ["", heading]
    if matched == 0:
        if total_in == 0:
            lines.append("_None_")
        else:
            lines.append(f"_0 of {total_in} {noun} matched your keywords._")
        return lines
    if not selected:
        lines.append(f"+{matched} more below threshold - {_TRUNCATION_HINT}")
        return lines
    for item in selected:
        lines.append(line_formatter(item))
    if len(selected) < matched:
        lines.append(f"+{matched - len(selected)} more below threshold - {_TRUNCATION_HINT}")
    return lines


def _render_recency_fallback(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int,
    max_decision_tokens: int,
) -> ContextPackResult:
    """Render the no-keywords pack: recency ranking with budget caps."""
    stats = ContextPackStats()

    constraint_tuples = [(constraint, 0) for constraint in constraints]
    selected_c, shown_c, _ = rank_and_truncate(
        constraint_tuples,
        max_constraint_tokens,
        line_formatter=_compact_constraint_line,
    )

    decision_tuples = [(decision, 0) for decision in decisions]
    selected_d, shown_d, _ = rank_and_truncate(
        decision_tuples,
        max_decision_tokens,
        line_formatter=_compact_decision_line,
    )

    stats.matched_constraints = len(constraints)
    stats.shown_constraints = shown_c
    stats.matched_decisions = len(decisions)
    stats.shown_decisions = shown_d

    lines = ["## Architectural Context"]
    lines.append("")
    lines.append("### Constraints (MUST follow)")
    if shown_c == 0:
        if len(constraints) == 0:
            lines.append("_None_")
        else:
            lines.append(f"_... and {len(constraints)} constraints omitted (token budget)._")
    else:
        for constraint in selected_c:
            lines.append(_compact_constraint_line(constraint))
        if shown_c < len(constraints):
            omitted = len(constraints) - shown_c
            lines.append(f"... and {omitted} more constraints omitted (token budget).")

    lines.append("")
    lines.append("### Relevant Decisions")
    if shown_d == 0:
        if len(decisions) == 0:
            lines.append("_None_")
        else:
            lines.append(f"_... and {len(decisions)} decisions omitted (token budget)._")
    else:
        for decision in selected_d:
            lines.append(_compact_decision_line(decision))
        if shown_d < len(decisions):
            omitted = len(decisions) - shown_d
            lines.append(f"... and {omitted} more decisions omitted (token budget).")

    return ContextPackResult(
        text="\n".join(lines),
        stats=stats,
        constraints=selected_c,
        decisions=selected_d,
    )


def build_context_pack(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int,
    max_decision_tokens: int,
    *,
    keywords: list[str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    base_dir: str | Path | None = None,
) -> ContextPackResult:
    """Build the context pack body plus shown/matched statistics.

    Dead-path filtering applies to keyword packs; the no-keyword recency
    fallback renders the caller's live set unchanged.
    """
    stats = ContextPackStats()

    if keywords:
        live_constraints = drop_dead_path_entries(
            constraints, path_exists=path_exists, base_dir=base_dir
        )
        live_decisions = drop_dead_path_entries(
            decisions, path_exists=path_exists, base_dir=base_dir
        )

        n_docs = len(live_constraints) + len(live_decisions)
        doc_freq = document_frequencies([*live_constraints, *live_decisions], keywords)
        idf = idf_weights(n_docs, doc_freq)

        ranked_c = rank_keyword_section(live_constraints, keywords, idf)
        ranked_d = rank_keyword_section(live_decisions, keywords, idf)
        selected_c = truncate_ranked(ranked_c, max_constraint_tokens, _compact_constraint_line)
        selected_d = truncate_ranked(ranked_d, max_decision_tokens, _compact_decision_line)

        stats.matched_constraints = len(ranked_c)
        stats.shown_constraints = len(selected_c)
        stats.matched_decisions = len(ranked_d)
        stats.shown_decisions = len(selected_d)

        kw_summary = ", ".join(keywords)
        header = f"## Architectural Context for: [{kw_summary}]"
        total_shown = stats.shown_constraints + stats.shown_decisions
        total_matched = stats.matched_constraints + stats.matched_decisions
        header += f" - shown {total_shown} of {total_matched} matched"

        lines = [header]
        lines.extend(
            _render_keyword_section(
                "### Constraints (MUST follow)",
                len(constraints),
                stats.matched_constraints,
                selected_c,
                _compact_constraint_line,
                "constraints",
            )
        )
        lines.extend(
            _render_keyword_section(
                "### Relevant Decisions",
                len(decisions),
                stats.matched_decisions,
                selected_d,
                _compact_decision_line,
                "decisions",
            )
        )
        return ContextPackResult(
            text="\n".join(lines),
            stats=stats,
            constraints=selected_c,
            decisions=selected_d,
        )

    return _render_recency_fallback(
        decisions, constraints, max_constraint_tokens, max_decision_tokens
    )
