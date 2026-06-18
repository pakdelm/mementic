from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mementic.models import Constraint, Decision, DecisionStatus

# ID length constants for slugify(). Both decision and constraint IDs
# share the same max length for consistency.
DECISION_ID_MAX_LENGTH: int = 40
CONSTRAINT_ID_MAX_LENGTH: int = 40


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


def find_file_paths(text: str) -> list[str]:
    """Find potential file paths in text matching common source file patterns.

    Args:
        text: The text to scan for file paths.

    Returns:
        A list of unique file path strings found in the text.
    """
    pattern = r"[\w/\-]+\.(?:py|ts|js|java|go|rs|rb|yaml|yml|json|toml|md)\b"
    return list(set(re.findall(pattern, text)))


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


def detect_repo_path(*, check_global_config: bool = False) -> str:
    """Detect the repository root path.

    When ``check_global_config`` is ``True``, checks the global config
    first (matching CWD against registered repos) before falling back
    to git root.  This is useful for the MCP server which needs a
    *registered* repo path for proper config resolution.

    Args:
        check_global_config: If True, check ``~/.mementic/config.json``
            for a registered repo matching CWD before trying git.

    Returns:
        The detected repo path, or ``""`` when ``check_global_config``
        is True and no repo is found.
    """
    if check_global_config:
        try:
            from mementic.config import load_global_config

            global_cfg = load_global_config()
            cwd = Path.cwd().resolve()
            for proj_entry in global_cfg.projects.values():
                for repo_entry in proj_entry.repos.values():
                    try:
                        if Path(repo_entry.path).resolve() == cwd:
                            return repo_entry.path
                    except (OSError, ValueError):
                        continue
        except Exception:
            pass

    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass

    return "" if check_global_config else str(Path.cwd())


# ---------------------------------------------------------------------------
# Compact format helpers
# ---------------------------------------------------------------------------

# Max character width for the joined entity list in compact format lines.
# Truncation happens at a ``, `` boundary so a path is never split mid-token.
_MAX_ENTITY_LIST_CHARS = 80


def _truncate_entity_list(entities: list[str], max_chars: int = _MAX_ENTITY_LIST_CHARS) -> str:
    """Join entity paths with ``", "``, truncating with ``"..."`` past a limit.

    Guards against a single item with many bound files blowing the token
    budget. Truncation happens at the last complete ``, `` separator at or
    before ``max_chars`` so a path is never split mid-token — a partial path
    like ``src/modu...`` could be mistaken for a real file by the LLM. If no
    separator exists before ``max_chars`` (a single very long path), a hard
    character-boundary cut is used as a fallback.

    The ellipsis is appended *after* truncation, so the returned string may
    be up to ``max_chars + 3`` characters long.

    Args:
        entities: List of entity path strings.
        max_chars: Maximum character count of the joined string before
            truncation is applied.

    Returns:
        The joined string, or a truncated copy ending in ``"..."`` if the
        joined form exceeds ``max_chars``. Returns ``""`` for an empty list.
    """
    joined = ", ".join(entities)
    if len(joined) > max_chars:
        cut = joined.rfind(", ", 0, max_chars)
        if cut == -1:
            # No separator before the limit: single very long path. Fall
            # back to a hard character-boundary cut.
            return joined[:max_chars] + "..."
        return joined[:cut] + "..."
    return joined


def _compact_constraint_line(constraint: Constraint) -> str:
    """Format a constraint as a compact single-line marker.

    Drops the ``[C]`` prefix and ``(enforced by: X)`` suffix to save tokens
    while preserving the description and ID reference. When the constraint
    has bound entities, they are appended as ``(bound: a.py, b.py)`` so agents
    can see *where* the rule applies without reading files. Long entity lists
    are truncated via :func:`_truncate_entity_list`.

    Returns:
        ``- {description} (#{id})`` when no bound entities, otherwise
        ``- {description} (#{id}, bound: {entities})``.
    """
    bound_suffix = ""
    if constraint.bound_entities:
        entities = _truncate_entity_list(constraint.bound_entities)
        bound_suffix = f", bound: {entities}"
    return f"- {constraint.description} (#{constraint.id}{bound_suffix})"


def _compact_decision_line(decision: Decision) -> str:
    """Format a decision as a compact single-line marker.

    Drops title, date, and rejected alternatives to save tokens while
    preserving the rationale and ID reference. When the decision has
    affected entities, they are appended as ``(affects: a.py, b.py)`` so
    agents can see *where* the decision applies without reading files. Long
    entity lists are truncated via :func:`_truncate_entity_list`.

    Returns:
        ``- {rationale} (#{id})`` when no affected entities, otherwise
        ``- {rationale} (#{id}, affects: {entities})``.
    """
    affects_suffix = ""
    if decision.affected_entities:
        entities = _truncate_entity_list(decision.affected_entities)
        affects_suffix = f", affects: {entities}"
    return f"- {decision.rationale} (#{decision.id}{affects_suffix})"


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
    max_tokens: int = 1500,
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
    max_tokens: int = 1500,
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
