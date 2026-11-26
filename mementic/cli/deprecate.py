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
from mementic.entries import DeprecateOutcome, EntryWriter, InvalidSuccessorError


def deprecate(
    ids: list[str] = typer.Argument(..., help="Decision or constraint IDs to deprecate"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for deprecation"),
    replaced_by: str | None = typer.Option(
        None,
        "--replaced-by",
        help="ID of the live entry that replaces each deprecated entry",
    ),
) -> None:
    """Mark one or more decisions or constraints as deprecated.

    Writes a tombstone row (with reason) into the deprecations table;
    deprecated entries remain listed for history but are excluded from
    mementic memory queries. ``--replaced-by`` records a live successor on
    every tombstone, so an agent that finds a retired rule can follow it.
    """
    with context.resolve_store() as store:
        try:
            results = EntryWriter(store).deprecate_many(ids, reason, replaced_by)
        except InvalidSuccessorError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except (sqlite3.Error, OSError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    deprecated = sum(1 for result in results if result.outcome is DeprecateOutcome.DEPRECATED)
    also = sum(1 for result in results if result.outcome is DeprecateOutcome.SUCCESSOR_ADDED)
    already = sum(1 for result in results if result.outcome is DeprecateOutcome.ALREADY_DEPRECATED)
    errors = sum(1 for result in results if result.outcome is DeprecateOutcome.NOT_FOUND)

    for result in results:
        successor = result.replaced_by
        if result.outcome is DeprecateOutcome.NOT_FOUND:
            typer.echo(f"Error: {result.entry_id} not found", err=True)
        elif result.outcome is DeprecateOutcome.ALREADY_DEPRECATED:
            console.print(text(f"Skipped {escape(result.entry_id)}: already deprecated"))
        elif result.outcome is DeprecateOutcome.SUCCESSOR_ADDED and successor is not None:
            console.print(text(_successor_added_line(result.entry_id, successor, reason)))
        elif successor is not None:
            console.print(text(_deprecated_with_successor_line(result.entry_id, successor, reason)))
        elif reason:
            console.print(text(f"Deprecated {escape(result.entry_id)}: {escape(reason)}"))
        else:
            console.print(text(f"Deprecated {escape(result.entry_id)}."))

    if len(results) > 1:
        parts: list[str] = []
        if deprecated:
            parts.append(f"[{COLOR_SUCCESS}]{deprecated} deprecated[/]")
        if also:
            parts.append(f"[{COLOR_SUCCESS}]{also} also superseded[/]")
        if already:
            parts.append(f"[{COLOR_WARN}]{already} skipped[/]")
        if errors:
            parts.append(f"[{COLOR_FAIL}]{errors} not found[/]")
        console.print(text("Summary: " + ", ".join(parts)))

    if errors:
        raise typer.Exit(code=1)


def _deprecated_with_successor_line(entry_id: str, successor: str, reason: str) -> str:
    """Render the row for an entry deprecated with a recorded successor."""
    suffix = f": {escape(reason)}" if reason else "."
    return f"Deprecated {escape(entry_id)} (superseded by {escape(successor)}){suffix}"


def _successor_added_line(entry_id: str, successor: str, reason: str) -> str:
    """Render the row for an extra successor recorded on a deprecated entry."""
    suffix = f": {escape(reason)}" if reason else "."
    return f"Recorded successor {escape(successor)} for {escape(entry_id)}{suffix}"
