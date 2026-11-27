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


def _collect_path_hits(
    store: MementicStore,
    unique_paths: Sequence[str],
    entry_type: str,
    *,
    path_exists: Callable[[str], bool],
) -> dict[str, list[str]]:
    """Map matching entry ids to the queried paths that matched them.

    Args:
        store: Open store for this invocation.
        unique_paths: Deduplicated canonical paths in argument order.
        entry_type: ``"constraint"`` or ``"decision"``.
        path_exists: Filesystem-liveness predicate passed to path matching.

    Returns:
        Entry id to queried paths, preserving argument order.
    """
    hits: dict[str, list[str]] = {}
    for path in unique_paths:
        for entry_id in store.get_path_matches(entry_type, path, path_exists=path_exists):
            hits.setdefault(entry_id, []).append(path)
    return hits


def _live_constraints_matching(
    store: MementicStore,
    hits: dict[str, list[str]],
) -> MatchedConstraints:
    """Return live constraints in store order with their matched paths."""
    if not hits:
        return []
    return [
        (constraint, hits[constraint.id])
        for constraint in store.get_live_constraints()
        if constraint.id in hits
    ]


def _live_decisions_matching(
    store: MementicStore,
    hits: dict[str, list[str]],
) -> MatchedDecisions:
    """Return live decisions in store order with their matched paths."""
    if not hits:
        return []
    return [
        (decision, hits[decision.id])
        for decision in store.get_live_decisions()
        if decision.id in hits
    ]


def _unmatched_paths(
    unique_paths: Sequence[str],
    constraints: MatchedConstraints,
    decisions: MatchedDecisions,
) -> list[str]:
    """Return queried paths that matched no live constraint and no live decision."""
    matched = {path for _, paths in constraints for path in paths}
    matched.update(path for _, paths in decisions for path in paths)
    return [path for path in unique_paths if path not in matched]


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
    constraint_hits = _collect_path_hits(store, unique_paths, "constraint", path_exists=path_exists)
    decision_hits = _collect_path_hits(store, unique_paths, "decision", path_exists=path_exists)
    merged_constraints = _live_constraints_matching(store, constraint_hits)
    merged_decisions = _live_decisions_matching(store, decision_hits)
    unmatched_paths = _unmatched_paths(unique_paths, merged_constraints, merged_decisions)
    return merged_constraints, merged_decisions, unmatched_paths


def files(
    paths: list[str] = typer.Argument(..., help="One or more file paths to inspect"),
    max_tokens: int = typer.Option(
        DEFAULT_EVOLUTION_MAX_TOKENS,
        "--max-tokens",
        help="Token budget for merged decision lines; constraints are never truncated",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        "-c",
        help="Agent-friendly output: summary line plus undecorated pack text",
    ),
) -> None:
    """Show constraints and decisions affecting one or more files.

    Decisions that exceed the ``--max-tokens`` budget are dropped and
    replaced by a trailing count line; constraints are always shown in full.
    """
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
