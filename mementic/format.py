from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Callable

from mementic.models import Constraint, Decision, DecisionStatus


def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string.

    Uses tiktoken if installed for accurate counting; falls back to
    ``len(text) // 4`` (reasonable estimate for English text).

    Args:
        text: The text to count tokens for.

    Returns:
        Estimated token count as an integer.
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, Exception):
        return len(text) // 4


def slugify(text: str, max_length: int | None = None) -> str:
    """Convert text to URL-friendly ID. Lowercase, hyphens, strip special chars.

    Args:
        text: The text to slugify.
        max_length: If set, truncate the slug to this length at a word boundary.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = text.strip("-")
    if max_length is not None and len(text) > max_length:
        # Truncate at word boundary (last hyphen within the limit)
        text = text[:max_length].rsplit("-", 1)[0].strip("-")
    return text


def timestamp() -> str:
    """Return ISO 8601 timestamp string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Compact format helpers
# ---------------------------------------------------------------------------


def _compact_constraint_line(c: Constraint) -> str:
    """Format a constraint as a compact single-line marker.

    Drops the ``[C]`` prefix and ``(enforced by: X)`` suffix to save tokens
    while preserving the description and ID reference.

    Returns:
        ``- {description} (#{id})``
    """
    return f"- {c.description} (#{c.id})"


def _compact_decision_line(d: Decision) -> str:
    """Format a decision as a compact single-line marker.

    Drops title, date, and rejected alternatives to save tokens while
    preserving the rationale and ID reference.

    Returns:
        ``- {rationale} (#{id})``
    """
    return f"- {d.rationale} (#{d.id})"


def _compact_evolution_line(d: Decision) -> str:
    """Format a decision with provenance for evolution traces.

    Keeps title, date, and rationale. Drops only the Rejected line.

    Returns:
        ``- [{created_at}] {title}: {rationale} (#{id})``
    """
    return f"- [{d.created_at}] {d.title}: {d.rationale} (#{d.id})"


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------


def _status_priority(status: DecisionStatus | str) -> int:
    """Convert a status value to a sort priority integer.

    Lower values are shown first.  Priority: ``accepted`` / ``active`` (0),
    everything else (1).

    Args:
        status: A ``DecisionStatus`` enum value or a raw string (Constraints
            store status as a plain string).

    Returns:
        An integer priority where 0 = highest.
    """
    s = status.value if isinstance(status, DecisionStatus) else str(status).lower()
    if s in ("accepted", "active"):
        return 0
    return 1


def rank_and_truncate(
    items: Sequence[tuple[Constraint | Decision, int]],
    max_tokens: int,
    *,
    line_formatter: Callable[[Any], str],
) -> tuple[list[Constraint | Decision], int, int]:
    """Rank items by overlap then recency then status, truncate to token budget.

    Sorting (three-pass stable sort, least-significant first):

    1. **Status** — ``accepted`` / ``active`` before ``deprecated``.
    2. **Recency** — ``created_at`` descending (newer first).
    3. **Overlap score** — higher first.

    Walks the sorted items, computes the token count of each compact format
    line, and stops **before** the item that would push the total over
    ``max_tokens`` (whole-item cut — never mid-line).

    Args:
        items: List of ``(item, overlap_score)`` tuples.
        max_tokens: Maximum token budget for the compact-formatted lines.
        line_formatter: Function to format an item as a compact line (one
            of ``_compact_*_line``).

    Returns:
        ``(selected_items, shown_count, total_count)``.
    """
    total = len(items)

    # Three-pass stable sort: least significant first, most significant last
    sorted_items = sorted(items, key=lambda x: _status_priority(x[0].status))
    sorted_items = sorted(sorted_items, key=lambda x: x[0].created_at, reverse=True)
    sorted_items = sorted(sorted_items, key=lambda x: x[1], reverse=True)

    selected: list[Constraint | Decision] = []
    tokens_used = 0
    for item, _overlap in sorted_items:
        line = line_formatter(item)
        line_tokens = count_tokens(line)
        if tokens_used + line_tokens > max_tokens:
            break
        selected.append(item)
        tokens_used += line_tokens

    return selected, len(selected), total


# ---------------------------------------------------------------------------
# Context pack & evolution formatting
# ---------------------------------------------------------------------------


def format_context_pack(
    task: str,
    decisions: list[tuple[Decision, int]],
    constraints: list[tuple[Constraint, int]],
    max_tokens: int = 1000,
) -> str:
    """Produces structured, token-budgeted markdown for agent context injection.

    Constraints are filled *first* against the full ``max_tokens`` budget.
    Decisions receive the remaining budget.  Both sections always appear
    (even when empty, with an omission note).

    Uses compact format lines (``_compact_*_line``) to maximise information
    density within the budget.  Items are ranked by overlap score, recency,
    and status via :func:`rank_and_truncate`.

    Args:
        task: Description of the task requesting context.
        decisions: List of ``(Decision, overlap_score)`` tuples.
        constraints: List of ``(Constraint, overlap_score)`` tuples.
        max_tokens: Maximum token budget for the constraint + decision lines.

    Returns:
        Formatted markdown string.
    """
    lines = [f"## Architectural Context for: {task}"]

    # --- Constraints section (always present) ---
    selected_c, shown_c, total_c = rank_and_truncate(
        constraints, max_tokens, line_formatter=_compact_constraint_line
    )
    tokens_used_c = 0
    for c in selected_c:
        tokens_used_c += count_tokens(
            _compact_constraint_line(c)  # type: ignore[arg-type]
        )

    lines.append("")
    lines.append("### Constraints (MUST follow)")
    if shown_c == 0:
        if total_c == 0:
            lines.append("_None_")
        else:
            lines.append(
                f"_... and {total_c} constraints omitted (token budget)._"
            )
    else:
        for c in selected_c:
            lines.append(_compact_constraint_line(c))  # type: ignore[arg-type]
        if shown_c < total_c:
            lines.append(
                f"... and {total_c - shown_c} more constraints omitted "
                f"(token budget)."
            )

    # --- Decisions section (always present) ---
    remaining = max_tokens - tokens_used_c
    selected_d, shown_d, total_d = rank_and_truncate(
        decisions, max(remaining, 0), line_formatter=_compact_decision_line
    )

    lines.append("")
    lines.append("### Relevant Decisions")
    if shown_d == 0:
        if total_d == 0:
            lines.append("_None_")
        else:
            lines.append(
                f"_... and {total_d} decisions omitted (token budget)._"
            )
    else:
        for d in selected_d:
            lines.append(_compact_decision_line(d))  # type: ignore[arg-type]
        if shown_d < total_d:
            lines.append(
                f"... and {total_d - shown_d} more decisions omitted "
                f"(token budget)."
            )

    return "\n".join(lines)


def format_evolution(
    entity_path: str,
    decisions: list[tuple[Decision, int]],
    max_tokens: int = 1000,
) -> str:
    """Format evolution trace for a code entity using compact provenance lines.

    Uses :func:`_compact_evolution_line` which preserves ``title`` and
    ``created_at`` for provenance, dropping only the ``Rejected`` line.
    Ranking and truncation follows the same logic as
    :func:`rank_and_truncate`.

    Args:
        entity_path: Path of the code entity (e.g. ``mementic/format.py``).
        decisions: List of ``(Decision, overlap_score)`` tuples.
        max_tokens: Maximum token budget for the compact-formatted lines.

    Returns:
        Formatted markdown string.
    """
    selected, shown, total = rank_and_truncate(
        decisions, max_tokens, line_formatter=_compact_evolution_line
    )

    lines = [f"## Evolution of {entity_path}"]
    if shown == 0:
        lines.append(f"No architectural decisions found for {entity_path}.")
    else:
        for d in selected:
            lines.append(_compact_evolution_line(d))  # type: ignore[arg-type]
        if shown < total:
            lines.append(
                f"... and {total - shown} more decisions omitted "
                f"(token budget)."
            )

    return "\n".join(lines)
