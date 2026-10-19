"""Browse commands: list decisions, constraints, entities, and history."""

from __future__ import annotations

import typer

from mementic.cli import context
from mementic.console import (
    COLOR_DIM,
    COLOR_ID,
    COLOR_SUCCESS,
    COLOR_WARN,
    STATUS_DEPRECATED,
    STATUS_OK,
    console,
    escape,
    panel,
    rule,
    table,
    text,
)
from mementic.text import format_timestamp

list_app = typer.Typer(help="Browse decisions, constraints, and entities")

_VALID_STATUSES = {"accepted", "deprecated", "active"}


def _validate_status(status: str | None) -> None:
    """Validate --status, exiting on an invalid value."""
    if status and status not in _VALID_STATUSES:
        typer.echo(
            f"Invalid status '{status}'. "
            f"Valid: {', '.join(sorted(_VALID_STATUSES))}",
            err=True,
        )
        raise typer.Exit(code=1)


def _superseded_label(superseded_by: str | None) -> str:
    """Render the Superseded-by cell: successor ID, ``retired``, or blank."""
    if superseded_by is None:
        return ""
    return "retired" if superseded_by == "retired" else superseded_by


def _replacement_chain(entry_id: str, records: list) -> list[str]:
    """Return the ordered ``entry -> successor -> ...`` ID trail.

    ``records`` arrive chronologically from the store. Skipping successors
    already in the chain keeps self-replacements and cycles out of the
    rendered trail.
    """
    chain = [entry_id]
    for record in records:
        successor = record["replaced_by"]
        if successor and successor not in chain:
            chain.append(successor)
    return chain


@list_app.command()
def decisions(
    n: int = typer.Option(context.DEFAULT_PAGE_SIZE, "-n", help="Show only the last N entries"),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status: accepted, deprecated, active",
    ),
) -> None:
    """List architectural decisions in a scannable table."""
    _validate_status(status)
    with context.resolve_store() as store:
        pairs = store.get_decisions_with_liveness()
    total = len(pairs)
    if status == "deprecated":
        pairs = [pair for pair in pairs if pair[1] is not None]
    elif status in ("accepted", "active"):
        pairs = [pair for pair in pairs if pair[1] is None]
    if n > 0:
        pairs = pairs[-n:]

    if not pairs:
        console.print(text("No decisions found."))
        raise typer.Exit()

    show_superseded = status == "deprecated"
    decision_table = table()
    decision_table.add_column("", width=6)
    decision_table.add_column(
        "ID", style=COLOR_ID, no_wrap=True, overflow="ellipsis", width=24
    )
    decision_table.add_column("Date", style=COLOR_DIM, no_wrap=True, width=10)
    decision_table.add_column(
        "Title",
        no_wrap=True,
        overflow="ellipsis",
        width=18 if show_superseded else 24,
    )
    if show_superseded:
        decision_table.add_column(
            "Superseded By", style=COLOR_WARN, no_wrap=True, overflow="ellipsis", width=12
        )
    for decision, superseded_by in pairs:
        date = decision.created_at[:10] if decision.created_at else ""
        icon = STATUS_OK if superseded_by is None else STATUS_DEPRECATED
        color = COLOR_SUCCESS if superseded_by is None else COLOR_WARN
        row = [
            f"[{color}]{icon}[/]",
            escape(decision.id),
            date,
            escape(decision.title),
        ]
        if show_superseded:
            row.append(escape(_superseded_label(superseded_by)))
        decision_table.add_row(*row)
    console.print(
        panel(decision_table, title=f"Decisions ({len(pairs)} of {total})")
    )


@list_app.command()
def constraints(
    n: int = typer.Option(context.DEFAULT_PAGE_SIZE, "-n", help="Show only the last N entries"),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status: accepted, deprecated, active",
    ),
) -> None:
    """List constraints in a scannable table."""
    _validate_status(status)
    with context.resolve_store() as store:
        pairs = store.get_constraints_with_liveness()
    total = len(pairs)
    if status == "deprecated":
        pairs = [pair for pair in pairs if pair[1] is not None]
    elif status in ("accepted", "active"):
        pairs = [pair for pair in pairs if pair[1] is None]
    show_superseded = status == "deprecated"
    if n > 0:
        pairs = pairs[-n:]
    show_enforced = any(constraint.enforced_by for constraint, _ in pairs)

    if not pairs:
        console.print(text("No constraints found."))
        raise typer.Exit()

    description_width = 36
    if show_enforced and show_superseded:
        description_width = 18
    elif show_enforced or show_superseded:
        description_width = 30
    constraint_table = table()
    constraint_table.add_column("", width=6)
    constraint_table.add_column(
        "ID", style=COLOR_ID, no_wrap=True, overflow="ellipsis", width=30
    )
    if show_enforced:
        constraint_table.add_column(
            "Enforced By", style=COLOR_DIM, no_wrap=True, width=14
        )
    constraint_table.add_column(
        "Description", no_wrap=True, overflow="ellipsis", width=description_width
    )
    if show_superseded:
        constraint_table.add_column(
            "Superseded By", style=COLOR_WARN, no_wrap=True, overflow="ellipsis", width=12
        )
    for constraint, superseded_by in pairs:
        icon = STATUS_OK if superseded_by is None else STATUS_DEPRECATED
        color = COLOR_SUCCESS if superseded_by is None else COLOR_WARN
        row = [f"[{color}]{icon}[/]", escape(constraint.id)]
        if show_enforced:
            row.append(
                f"via {escape(constraint.enforced_by)}"
                if constraint.enforced_by
                else ""
            )
        row.append(escape(constraint.description))
        if show_superseded:
            row.append(escape(_superseded_label(superseded_by)))
        constraint_table.add_row(*row)
    console.print(
        panel(constraint_table, title=f"Constraints ({len(pairs)} of {total})")
    )


@list_app.command()
def entities(
    n: int = typer.Option(context.DEFAULT_PAGE_SIZE, "-n", help="Show only the last N entries"),
    stale: bool = typer.Option(
        False,
        "--stale",
        help="Show only paths referencing files that no longer exist",
    ),
) -> None:
    """List referenced file paths in a scannable table."""
    paths = context.resolve_paths()
    with context.resolve_store(paths) as store:
        all_paths = store.get_all_paths()
    total = len(all_paths)
    if stale:
        all_paths = [path for path in all_paths if not paths.entity_exists(path)]
    if n > 0:
        all_paths = all_paths[-n:]

    if not all_paths:
        console.print(text("No entities found."))
        raise typer.Exit()

    entity_table = table()
    entity_table.add_column("Path", style=COLOR_ID, no_wrap=True, overflow="ellipsis")
    for path in all_paths:
        entity_table.add_row(escape(path))
    console.print(
        panel(entity_table, title=f"Entities ({len(all_paths)} of {total})")
    )


def history(
    entry_id: str | None = typer.Argument(
        None, help="Decision or constraint ID to trace (omit for recent records)"
    ),
    n: int = typer.Option(
        context.DEFAULT_PAGE_SIZE, "--n", "-n", help="Number of records to show"
    ),
) -> None:
    """Show deprecation records: one entry's lifecycle chain or recent records."""
    rows: list = []
    records: list = []
    with context.resolve_store() as store:
        if entry_id is None:
            rows = store.get_all_deprecations()
        else:
            entry_type = store.get_entry_type(entry_id)
            for candidate in ([entry_type] if entry_type else []) + (
                ["decision", "constraint"] if not entry_type else []
            ):
                records = store.get_replacement_chain(candidate, entry_id)
                if records:
                    break

    if entry_id is None:
        if not rows:
            console.print(text("No deprecation history recorded yet."))
            return
        total = len(rows)
        if n > 0:
            rows = rows[:n]
        dep_table = table()
        dep_table.add_column("At", style=COLOR_DIM, no_wrap=True, width=16)
        dep_table.add_column(
            "Entry", style=COLOR_ID, no_wrap=True, overflow="ellipsis", width=30
        )
        dep_table.add_column("Reason", no_wrap=True, overflow="ellipsis", width=28)
        for record in rows:
            dep_table.add_row(
                format_timestamp(record["at"]),
                escape(record["entry_id"]),
                escape(record["reason"] or ""),
            )
        console.print(
            panel(dep_table, title=f"Deprecation history ({len(rows)} of {total})")
        )
        return

    if not records:
        console.print(text(f"No deprecation history for '{escape(entry_id)}'."))
        return

    chain_ids = _replacement_chain(entry_id, records)

    dep_table = table()
    dep_table.add_column("At", style=COLOR_DIM)
    dep_table.add_column("Entry", style=COLOR_ID)
    dep_table.add_column("Replaced By")
    dep_table.add_column("Reason", overflow="fold")
    for record in records:
        successor = (
            "[dim]retired[/]"
            if record["replaced_by"] is None
            else escape(record["replaced_by"])
        )
        dep_table.add_row(
            record["at"],
            escape(record["entry_id"]),
            successor,
            escape(record["reason"] or ""),
        )
    chain = " -> ".join(escape(chain_id) for chain_id in chain_ids)
    console.print(
        panel(
            text(f"[{COLOR_ID} bold]{chain}[/]"),
            rule(),
            dep_table,
            title=f"History: {escape(entry_id)}",
        )
    )


def register(app: typer.Typer) -> None:
    """Register the list command group and history command."""
    app.add_typer(list_app, name="list")
    app.command()(history)
