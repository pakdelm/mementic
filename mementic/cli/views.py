"""Shared output helpers for CLI commands."""

from __future__ import annotations

from mementic.console import COLOR_ID, COLOR_SUCCESS, COLOR_WARN
from mementic.entries import AddOutcome, EntryKind


def ellipsize(value: str, limit: int) -> str:
    """Shorten a display value to ``limit`` characters with a trailing ellipsis.

    Args:
        value: Display value.
        limit: Maximum output length.

    Returns:
        The value unchanged or truncated with ``...``.
    """
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def add_outcome_label(outcome: AddOutcome) -> str:
    """Return the parenthetical suffix for an add outcome.

    Args:
        outcome: Writer outcome.

    Returns:
        A label for outcomes worth calling out, or an empty string for a
        plain created entry.
    """
    if outcome is AddOutcome.DUPLICATE:
        return "duplicate"
    if outcome is AddOutcome.REPLACED:
        return "previous entry deprecated"
    if outcome is AddOutcome.REVIVED:
        return "replaced deprecated entry"
    return ""


def add_summary(kind: EntryKind, entry_id: str, outcome: AddOutcome) -> str:
    """Render one add confirmation line.

    Args:
        kind: Entry kind.
        entry_id: Stored slug ID.
        outcome: Writer outcome.

    Returns:
        Rich-markup confirmation line.
    """
    noun = kind.value
    label = add_outcome_label(outcome)
    suffix = f" ({label})" if label else ""
    if outcome is AddOutcome.DUPLICATE:
        return f"[{COLOR_WARN}]Skipped {noun}[/] [{COLOR_ID}]{entry_id}[/]{suffix}"
    return f"[{COLOR_SUCCESS}]Added {noun}[/] [{COLOR_ID}]{entry_id}[/]{suffix}"
