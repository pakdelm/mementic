"""Query commands and their context-pack rendering.

Every text query emits one plain ``[mementic: ...]`` summary line for the
agent-enforcement contract. JSON mode skips that line.
"""

from __future__ import annotations

import json

import typer

from mementic.cli import context
from mementic.context import (
    DEFAULT_EVOLUTION_MAX_TOKENS,
    build_context_pack,
    format_context_pack_json,
    format_evolution,
    format_file_history_json,
    split_pack_budget,
)
from mementic.text import sanitize

query_app = typer.Typer(help="Search and retrieve from the knowledge graph")


@query_app.command()
def keywords(
    keywords: list[str] = typer.Argument(
        ..., help="Keywords for matching (e.g. auth login session pydantic)"
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        help="Total token budget for the pack; overrides both section budgets proportionally",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    compact: bool = typer.Option(
        False,
        "--compact",
        "-c",
        help="Agent-friendly output: summary line plus undecorated pack text",
    ),
) -> None:
    """Return ranked context pack of constraints and decisions matched by keywords."""
    if max_tokens is not None and max_tokens < 1:
        typer.echo(
            f"Error: --max-tokens must be at least 1 (got {max_tokens}). "
            "Pass a positive total token budget, or omit the flag to use "
            "the module defaults.",
            err=True,
        )
        raise typer.Exit(code=2)

    paths = context.resolve_paths()
    with context.resolve_store(paths) as store:
        constraints = store.get_live_constraints()
        decisions = store.get_live_decisions()
    max_constraint_tokens, max_decision_tokens = split_pack_budget(max_tokens)

    if json_output:
        result_json = format_context_pack_json(
            decisions,
            constraints,
            max_constraint_tokens=max_constraint_tokens,
            max_decision_tokens=max_decision_tokens,
            keywords=keywords,
            path_exists=paths.entity_exists,
        )
        typer.echo(json.dumps(result_json, indent=2, default=str))
        return

    result = build_context_pack(
        decisions,
        constraints,
        max_constraint_tokens=max_constraint_tokens,
        max_decision_tokens=max_decision_tokens,
        keywords=keywords,
        path_exists=paths.entity_exists,
    )
    stats = result.stats
    kw_list = ", ".join(keywords)
    typer.echo(
        f"[mementic: {stats.shown_constraints} of {stats.matched_constraints} constraints, "
        f"{stats.shown_decisions} of {stats.matched_decisions} decisions | {kw_list}]"
    )
    if compact:
        typer.echo(sanitize(result.text))
        return

    from mementic.console import (
        COLOR_DIM,
        console,
        escape,
        markdown,
        panel,
        rule,
        text,
    )

    summary = text(
        f"[{COLOR_DIM}]{stats.shown_constraints} of {stats.matched_constraints} constraints, "
        f"{stats.shown_decisions} of {stats.matched_decisions} decisions[/]"
    )
    console.print(
        panel(
            summary,
            rule(),
            markdown(result.text),
            title=f"Context pack: {escape(kw_list)}",
        )
    )


@query_app.command()
def file(
    path: str = typer.Argument(..., help="File path to trace evolution for"),
    prefix: bool = typer.Option(False, "--prefix", help="Use prefix matching for entity paths"),
    max_tokens: int = typer.Option(
        DEFAULT_EVOLUTION_MAX_TOKENS,
        "--max-tokens",
        help="Maximum token budget for the decision trace",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    compact: bool = typer.Option(
        False,
        "--compact",
        "-c",
        help="Agent-friendly output: summary line plus undecorated pack text",
    ),
) -> None:
    """Show constraints and decisions affecting a specific file."""
    if max_tokens < 1:
        typer.echo(
            f"Error: --max-tokens must be at least 1 (got {max_tokens}).",
            err=True,
        )
        raise typer.Exit(code=2)
    paths = context.resolve_paths()
    try:
        canonical_path = paths.canonical_entity_path(path)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    # Canonicalizing runs the path through Path, which drops a trailing
    # separator. Stored entity paths never end with one, so the separator is
    # what bounds a prefix match to a directory rather than a sibling name
    # (``repo-a/src`` vs ``repo-a/src-legacy``); restore it for directory
    # prefixes.
    if prefix and path.strip().endswith(("/", "\\")) and canonical_path:
        if not canonical_path.endswith("/"):
            canonical_path += "/"
    with context.resolve_store(paths) as store:
        constraints = store.get_constraints_for_path(
            canonical_path, prefix=prefix, path_exists=paths.entity_exists
        )
        decisions = store.get_decisions_for_path(
            canonical_path, prefix=prefix, path_exists=paths.entity_exists
        )

    if json_output:
        result = format_file_history_json(canonical_path, decisions, constraints)
        typer.echo(json.dumps(result, indent=2, default=str))
        return

    evolution_text = format_evolution(canonical_path, decisions, max_tokens=max_tokens)
    if constraints:
        evolution_text += "\n\n### Constraints\n"
        for constraint in constraints:
            entities = ", ".join(constraint.paths) if constraint.paths else "none"
            evolution_text += f"- {constraint.description} (#{constraint.id}, bound: {entities})\n"
    typer.echo(
        f"[mementic: {len(constraints)} constraints, {len(decisions)} decisions | {canonical_path}]"
    )
    if compact:
        typer.echo(sanitize(evolution_text))
        return

    from mementic.console import console, escape, markdown, panel

    console.print(panel(markdown(evolution_text), title=f"File history: {escape(canonical_path)}"))


@query_app.command()
def search(
    term: str = typer.Argument(..., help="Search term for titles, rationales, and descriptions"),
) -> None:
    """Full-text search across decisions and constraints.

    Deprecated entries stay searchable.
    """
    from mementic.console import (
        COLOR_HEADER,
        COLOR_ID,
        console,
        escape,
        panel,
        table,
        text,
    )

    with context.resolve_store() as store:
        all_decisions = store.get_all_decisions()
        all_constraints = store.get_all_constraints()
    needle = term.lower()
    matching_decisions = [
        decision
        for decision in all_decisions
        if needle in decision.title.lower() or needle in decision.rationale.lower()
    ]
    matching_constraints = [
        constraint for constraint in all_constraints if needle in constraint.description.lower()
    ]

    panel_lines: list = []
    if matching_decisions:
        decisions_table = table(show_header=False, show_edge=False, padding=(0, 1))
        decisions_table.add_column("ID", style=COLOR_ID)
        decisions_table.add_column("Title")
        for decision in matching_decisions:
            decisions_table.add_row(escape(decision.id), escape(decision.title))
        panel_lines.append(text(f"[{COLOR_HEADER} bold]Decisions ({len(matching_decisions)})[/]"))
        panel_lines.append(decisions_table)
    if matching_constraints:
        if panel_lines:
            panel_lines.append(text(""))
        constraints_table = table(show_header=False, show_edge=False, padding=(0, 1))
        constraints_table.add_column("ID", style=COLOR_ID)
        constraints_table.add_column("Description")
        for constraint in matching_constraints:
            constraints_table.add_row(escape(constraint.id), escape(constraint.description))
        panel_lines.append(
            text(f"[{COLOR_HEADER} bold]Constraints ({len(matching_constraints)})[/]")
        )
        panel_lines.append(constraints_table)

    typer.echo(
        f"[mementic: {len(matching_constraints)} constraints, "
        f"{len(matching_decisions)} decisions | {term}]"
    )
    if not panel_lines:
        console.print(text(f'No matches for "{escape(term)}".'))
        return
    console.print(panel(*panel_lines, title=f'Search: "{escape(term)}"'))


@query_app.command()
def stats() -> None:
    """Show graph statistics."""
    from mementic.console import console, kv_table, panel

    with context.resolve_store() as store:
        graph_stats = store.stats()
    stats_table = kv_table()
    stats_table.add_column("Metric", style="bold", width=20)
    stats_table.add_column("Value", justify="right")
    labels = {
        "decisions": "Decisions",
        "constraints": "Constraints",
        "paths": "Entry paths",
        "deprecated": "Deprecated",
    }
    for key, value in graph_stats.items():
        stats_table.add_row(labels.get(key, key.title()), str(value))
    console.print(panel(stats_table, title="Graph statistics"))
