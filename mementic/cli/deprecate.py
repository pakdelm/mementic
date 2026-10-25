"""The deprecate command."""

from __future__ import annotations

import sqlite3

import typer

from mementic.cli import context
from mementic.console import (
    COLOR_FAIL,
    COLOR_SUCCESS,
    COLOR_WARN,
    console,
    escape,
    text,
)
from mementic.entries import DeprecateOutcome, EntryWriter


def deprecate(
    ids: list[str] = typer.Argument(
        ..., help="Decision or constraint IDs to deprecate"
    ),
    reason: str = typer.Option(
        "", "--reason", "-r", help="Reason for deprecation"
    ),
) -> None:
    """Mark one or more decisions or constraints as deprecated.

    Writes a tombstone row (with reason) into the deprecations table;
    deprecated entries remain listed for history but are excluded from
    mementic memory queries.
    """
    with context.resolve_store() as store:
        try:
            results = EntryWriter(store).deprecate_many(ids, reason)
        except (sqlite3.Error, OSError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    deprecated = sum(
        1 for result in results if result.outcome is DeprecateOutcome.DEPRECATED
    )
    already = sum(
        1 for result in results if result.outcome is DeprecateOutcome.ALREADY_DEPRECATED
    )
    errors = sum(1 for result in results if result.outcome is DeprecateOutcome.NOT_FOUND)

    for result in results:
        if result.outcome is DeprecateOutcome.NOT_FOUND:
            typer.echo(f"Error: {result.entry_id} not found", err=True)
        elif result.outcome is DeprecateOutcome.ALREADY_DEPRECATED:
            console.print(text(f"Skipped {escape(result.entry_id)}: already deprecated"))
        elif reason:
            console.print(
                text(f"Deprecated {escape(result.entry_id)}: retired ({escape(reason)})")
            )
        else:
            console.print(text(f"Deprecated {escape(result.entry_id)}: retired"))

    if len(results) > 1:
        parts: list[str] = []
        if deprecated:
            parts.append(f"[{COLOR_SUCCESS}]{deprecated} deprecated[/]")
        if already:
            parts.append(f"[{COLOR_WARN}]{already} skipped[/]")
        if errors:
            parts.append(f"[{COLOR_FAIL}]{errors} not found[/]")
        console.print(text("Summary: " + ", ".join(parts)))

    if errors:
        raise typer.Exit(code=1)
