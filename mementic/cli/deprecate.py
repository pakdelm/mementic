"""The deprecate command."""

from __future__ import annotations

import sqlite3

import typer

from mementic.cli import context
from mementic.console import (
    COLOR_DIM,
    COLOR_FAIL,
    COLOR_HEADER,
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
            typer.echo(result.message, err=True)

    parts: list[str] = []
    if deprecated:
        parts.append(f"[{COLOR_SUCCESS}]{deprecated} deprecated[/]")
    if already:
        parts.append(f"[{COLOR_WARN}]{already} already deprecated[/]")
    if errors:
        parts.append(f"[{COLOR_FAIL}]{errors} errors[/]")
    if reason:
        parts.append(f"[{COLOR_DIM}]reason: {escape(reason)}[/]")

    id_list = ", ".join(escape(entry_id) for entry_id in ids)
    console.print(
        text(
            f"[{COLOR_HEADER}]Deprecated:[/] "
            + ", ".join(parts)
            + f" ([{COLOR_DIM}]{id_list}[/])"
        )
    )

    if errors:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    """Register the deprecate command."""
    app.command()(deprecate)
