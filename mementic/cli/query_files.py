"""Batched ``query files`` command that merges several path lookups."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import typer

from mementic import staleness
from mementic.cli import context
from mementic.context import DEFAULT_EVOLUTION_MAX_TOKENS, format_merged_history
from mementic.models import Constraint, Decision
from mementic.paths import ProjectPaths
from mementic.store import MementicStore
from mementic.text import sanitize

_SUMMARY_PATH_LIMIT: int = 3


@dataclass(frozen=True, kw_only=True)
class _FileBatch:
    """Merged matches for one batch of canonical paths.

    Attributes:
        decisions: ``(decision, matched batch paths)`` pairs, in store order.
        constraints: ``(constraint, matched batch paths)`` pairs, in store order.
        unmatched: Canonical paths that matched no live decision or constraint.
    """

    decisions: list[tuple[Decision, list[str]]]
    constraints: list[tuple[Constraint, list[str]]]
    unmatched: list[str]


def _collect(
    store: MementicStore,
    canonical_paths: Sequence[str],
    *,
    path_exists: Callable[[str], bool],
) -> _FileBatch:
    """Collect one merged match set from a single open store.

    Each path is matched once against decisions and once against constraints,
    and the live entry tables are read once each, so the cost of a batch grows
    with the number of paths and not with the number of store opens.

    Args:
        store: Open store the batch reads from.
        canonical_paths: Unique canonical paths, in argument order.
        path_exists: Liveness predicate; matched paths failing it are dropped,
            which also keeps dead bindings out of the merged pack.

    Returns:
        Merged matches, with matched-path lists in argument order and only
        live entries present. ``unmatched`` is computed after liveness
        filtering, so a path whose only matches are deprecated is unmatched.
    """
    decision_matches: dict[str, list[str]] = {}
    constraint_matches: dict[str, list[str]] = {}
    for path in canonical_paths:
        for entry_id in store.get_path_matches("decision", path, path_exists=path_exists):
            decision_matches.setdefault(entry_id, []).append(path)
        for entry_id in store.get_path_matches("constraint", path, path_exists=path_exists):
            constraint_matches.setdefault(entry_id, []).append(path)

    decisions = [
        (decision, decision_matches[decision.id])
        for decision in store.get_live_decisions()
        if decision.id in decision_matches
    ]
    constraints = [
        (constraint, constraint_matches[constraint.id])
        for constraint in store.get_live_constraints()
        if constraint.id in constraint_matches
    ]

    matched_paths: set[str] = set()
    for _, matched in decisions:
        matched_paths.update(matched)
    for _, matched in constraints:
        matched_paths.update(matched)
    unmatched = [path for path in canonical_paths if path not in matched_paths]
    return _FileBatch(decisions=decisions, constraints=constraints, unmatched=unmatched)


def _unique_canonical_paths(paths: Sequence[str], project_paths: ProjectPaths) -> list[str]:
    """Canonicalize inputs in order, dropping repeats, before any store opens.

    Args:
        paths: Raw path arguments in argument order.
        project_paths: Resolved project paths; its canonicalizer is the only
            one used, so the batch and the single-path command agree.

    Returns:
        Unique canonical paths, first occurrence wins.

    Raises:
        typer.Exit: When a path is refused, such as a path owned by another
            directory's graph.
    """
    unique: dict[str, None] = {}
    for raw in paths:
        try:
            unique.setdefault(project_paths.canonical_entity_path(raw), None)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    return list(unique)


def _summary_line(paths: Sequence[str], batch: _FileBatch) -> str:
    """Format the single ``[mementic: ...]`` line for a merged pack.

    Args:
        paths: Unique canonical paths, in argument order.
        batch: Merged matches whose live entries are counted.

    Returns:
        One plain summary line naming the counts and the first few paths.
    """
    noun = "path" if len(paths) == 1 else "paths"
    listed = ", ".join(paths[:_SUMMARY_PATH_LIMIT])
    if len(paths) > _SUMMARY_PATH_LIMIT:
        listed += f" (+{len(paths) - _SUMMARY_PATH_LIMIT} more)"
    return (
        f"[mementic: {len(batch.constraints)} constraints, {len(batch.decisions)} decisions "
        f"| {len(paths)} {noun}: {listed}]"
    )


def files(
    paths: list[str] = typer.Argument(..., help="File paths to trace (two or more for a batch)"),
    max_tokens: int = typer.Option(
        DEFAULT_EVOLUTION_MAX_TOKENS,
        "--max-tokens",
        help="Shared token budget for the decision lines",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        "-c",
        help="Agent-friendly output: summary line plus undecorated pack text",
    ),
) -> None:
    """Show constraints and decisions affecting several files in one merged pack."""
    if max_tokens < 1:
        typer.echo(
            f"Error: --max-tokens must be at least 1 (got {max_tokens}).",
            err=True,
        )
        raise typer.Exit(code=2)

    project_paths = context.resolve_paths()
    canonical_paths = _unique_canonical_paths(paths, project_paths)
    with context.resolve_store(project_paths) as store:
        batch = _collect(store, canonical_paths, path_exists=project_paths.entity_exists)
        bindings = [
            binding for path in canonical_paths for binding in store.get_path_bindings(path)
        ]

    markers = staleness.markers_by_entry(
        staleness.classify_bindings(bindings, resolve=project_paths.entity_path)
    )
    body = format_merged_history(
        canonical_paths,
        batch.decisions,
        batch.constraints,
        max_tokens=max_tokens,
        unmatched=batch.unmatched,
        markers=markers,
    )
    typer.echo(_summary_line(canonical_paths, batch))
    if compact:
        typer.echo(sanitize(body))
        return

    from mementic.console import console, escape, markdown, panel

    label = "1 path" if len(canonical_paths) == 1 else f"{len(canonical_paths)} paths"
    console.print(panel(markdown(body), title=f"Files: {escape(label)}"))
