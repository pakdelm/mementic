"""Shared output helpers for CLI commands."""

from __future__ import annotations

from mementic.console import C_ID, C_SUCCESS, C_WARN
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


def add_outcome_label(outcome: AddOutcome, kind: EntryKind) -> str:
    """Return the human-facing label for an add outcome.

    Args:
        outcome: Writer outcome.
        kind: Entry kind.

    Returns:
        Label used in the one-line confirmation.
    """
    if outcome is AddOutcome.DUPLICATE:
        return "skipped (duplicate)"
    if outcome is AddOutcome.REPLACED:
        return "accepted (previous entry deprecated)"
    if outcome is AddOutcome.REVIVED:
        return "accepted (replaced deprecated entry)"
    return "active" if kind is EntryKind.CONSTRAINT else "accepted"


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
    if outcome is AddOutcome.DUPLICATE:
        return (
            f"[{C_WARN}]Skipped {noun}[/] [{C_ID}]{entry_id}[/] "
            f"({add_outcome_label(outcome, kind)})"
        )
    return (
        f"[{C_SUCCESS}]Added {noun}[/] [{C_ID}]{entry_id}[/] "
        f"({add_outcome_label(outcome, kind)})"
    )
