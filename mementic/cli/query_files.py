"""Batched ``mementic query files`` subcommand.

Queries several paths in one invocation, deduplicates canonical paths, and
emits a single merged history pack. The store is opened once for the whole
batch so a ten-path query costs one connection.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import typer

from mementic.cli import context
from mementic.context import DEFAULT_EVOLUTION_MAX_TOKENS, format_merged_history
from mementic.models import Constraint, Decision
from mementic.store import MementicStore
from mementic.text import sanitize

_SUMMARY_PATH_LIMIT: int = 3

MatchedConstraints = list[tuple[Constraint, list[str]]]
MatchedDecisions = list[tuple[Decision, list[str]]]


def _format_paths_summary(paths: Sequence[str]) -> str:
    """Summarize queried paths for the compact summary line.

    Args:
        paths: Unique canonical paths in argument order.

    Returns:
        ``"<K> path(s): p1, p2, p3"`` truncated to the first three paths,
        with ``" (+R more)"`` appended when ``K`` exceeds the limit.
    """
    count = len(paths)
    noun = "path" if count == 1 else "paths"
    shown = ", ".join(paths[:_SUMMARY_PATH_LIMIT])
    label = f"{count} {noun}: {shown}"
    if count > _SUMMARY_PATH_LIMIT:
        label += f" (+{count - _SUMMARY_PATH_LIMIT} more)"
    return label


def _collect_merged_entries(
    store: MementicStore,
    unique_paths: Sequence[str],
    *,
    path_exists: Callable[[str], bool],
) -> tuple[MatchedConstraints, MatchedDecisions, list[str]]:
    """Collect live entries matching any queried path, each rendered once.

    Args:
        store: Open store for this invocation.
        unique_paths: Deduplicated canonical paths in argument order.
        path_exists: Filesystem-liveness predicate passed to path matching.

    Returns:
        ``(constraints, decisions, unmatched_paths)`` where the matched-path
        lists preserve argument order and ``unmatched_paths`` holds every
        queried path that matched zero live constraints and zero live
        decisions.
    """
    constraint_hits: dict[str, list[str]] = {}
    decision_hits: dict[str, list[str]] = {}
    for path in unique_paths:
        for entry_id in store.get_path_matches("constraint", path, path_exists=path_exists):
            constraint_hits.setdefault(entry_id, []).append(path)
        for entry_id in store.get_path_matches("decision", path, path_exists=path_exists):
            decision_hits.setdefault(entry_id, []).append(path)

    live_constraints = store.get_live_constraints() if constraint_hits else []
    merged_constraints: MatchedConstraints = [
        (constraint, constraint_hits[constraint.id])
        for constraint in live_constraints
        if constraint.id in constraint_hits
    ]

    live_decisions = store.get_live_decisions() if decision_hits else []
    merged_decisions: MatchedDecisions = [
        (decision, decision_hits[decision.id])
        for decision in live_decisions
        if decision.id in decision_hits
    ]

    live_constraint_ids = {constraint.id for constraint, _ in merged_constraints}
    live_decision_ids = {decision.id for decision, _ in merged_decisions}
    matched_paths: set[str] = set()
    for entry_id, paths in constraint_hits.items():
        if entry_id in live_constraint_ids:
            matched_paths.update(paths)
    for entry_id, paths in decision_hits.items():
        if entry_id in live_decision_ids:
            matched_paths.update(paths)

    unmatched_paths = [path for path in unique_paths if path not in matched_paths]
    return merged_constraints, merged_decisions, unmatched_paths


def files(
    paths: list[str] = typer.Argument(..., help="One or more file paths to inspect"),
    max_tokens: int = typer.Option(
        DEFAULT_EVOLUTION_MAX_TOKENS,
        "--max-tokens",
        help="Maximum token budget for the merged decision trace",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        "-c",
        help="Agent-friendly output: summary line plus undecorated pack text",
    ),
) -> None:
    """Show constraints and decisions affecting one or more files."""
    if max_tokens < 1:
        typer.echo(
            f"Error: --max-tokens must be at least 1 (got {max_tokens}).",
            err=True,
        )
        raise typer.Exit(code=2)

    project_paths = context.resolve_paths()
    try:
        canonical_paths = [project_paths.canonical_entity_path(raw) for raw in paths]
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    unique_paths = list(dict.fromkeys(canonical_paths))

    with context.resolve_store(project_paths) as store:
        merged_constraints, merged_decisions, unmatched_paths = _collect_merged_entries(
            store,
            unique_paths,
            path_exists=project_paths.entity_exists,
        )

    body = format_merged_history(
        unique_paths,
        merged_decisions,
        merged_constraints,
        unmatched_paths=unmatched_paths,
        max_tokens=max_tokens,
    )
    path_label = _format_paths_summary(unique_paths)
    typer.echo(
        f"[mementic: {len(merged_constraints)} constraints, "
        f"{len(merged_decisions)} decisions | {path_label}]"
    )
    if compact:
        typer.echo(sanitize(body))
        return

    from mementic.console import console, escape, markdown, panel

    console.print(panel(markdown(body), title=f"Files history: {escape(path_label)}"))
