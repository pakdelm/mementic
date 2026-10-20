"""Add-decision and add-constraint commands."""

from __future__ import annotations

import sqlite3

import typer

from mementic.cli import context
from mementic.cli.views import add_summary
from mementic.console import console, text
from mementic.entries import EntryWriter, MissingDecisionError

add_app = typer.Typer(help="Add decisions and constraints to the graph")


@add_app.command()
def decision(
    title: str = typer.Argument(..., help="Short title for the decision"),
    rationale: str = typer.Option(
        ..., "--rationale", "-r", help="Justification for the decision"
    ),
    entities: list[str] | None = typer.Option(
        None, "--entity", "-e", help="Affected file paths (repeatable)"
    ),
) -> None:
    """Record a new architectural decision.

    Auto-resolves against an existing entry with the same slug: a new slug is
    accepted, near-identical content is skipped as a duplicate, and different
    content deprecates the existing entry before accepting the new one.
    """
    with context.resolve_store() as store:
        try:
            result = EntryWriter(store).add_decision(
                title=title,
                rationale=rationale,
                paths=entities or [],
            )
        except (ValueError, sqlite3.Error, OSError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    console.print(text(add_summary(result.kind, result.entry_id, result.outcome)))


@add_app.command()
def constraint(
    description: str = typer.Argument(..., help="Constraint description"),
    entities: list[str] = typer.Option(
        ..., "--entity", "-e", help="Bound entity paths (repeatable, required)"
    ),
    decision_id: str | None = typer.Option(
        None, "--decision-id", "-d", help="Enforcing decision ID"
    ),
    rule_id: str | None = typer.Option(
        None, "--id", help="Explicit rule ID (auto-generated if omitted)"
    ),
) -> None:
    """Record a new constraint.

    Auto-resolves against an existing entry with the same slug: a new slug is
    accepted, near-identical content is skipped as a duplicate, and different
    content deprecates the existing entry before accepting the new one.
    """
    with context.resolve_store() as store:
        try:
            result = EntryWriter(store).add_constraint(
                description=description,
                paths=entities,
                enforced_by=decision_id or "",
                rule_id=rule_id,
            )
        except MissingDecisionError as exc:
            typer.echo(f"Error: {exc}.", err=True)
            raise typer.Exit(code=1) from exc
        except (ValueError, sqlite3.Error, OSError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    console.print(text(add_summary(result.kind, result.entry_id, result.outcome)))
