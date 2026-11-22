"""The check command: list live rules bound to the files changed in a diff."""

from __future__ import annotations

import typer

from mementic.cli import context
from mementic.console import (
    COLOR_DIM,
    COLOR_ID,
    console,
    escape,
    panel,
    table,
    text,
)
from mementic.git import ChangedFile, GitDiffError, NotAWorkTreeError, changed_files
from mementic.paths import ForeignGraphPathError, ProjectPaths

_SUMMARY_FORMAT: str = "[mementic: {rules} constraints | {changed}]"
_NO_CHANGED_FILES: str = "no changed files"
_DISCLAIMER: str = "Lists the live rules bound to changed files. It does not verify the change."
_NOT_A_WORK_TREE: str = (
    "Error: {base} is not inside a Git work tree, so changed files cannot be read. "
    "Run 'mementic check' from the repository root, or use "
    "'mementic query file <path>'."
)


def _query_path(paths: ProjectPaths, reported: str) -> str:
    """Return the canonical query path for one Git-reported path.

    A path inside an initialized nested repository belongs to that graph, so
    canonicalization refuses it; the listing stays informational, so the path
    Git printed is used unchanged instead.

    Args:
        paths: Resolved paths anchoring the graph.
        reported: Repo-relative POSIX path as printed by Git.

    Returns:
        The canonical entity path, or ``reported`` for a nested graph's path.
    """
    try:
        return paths.canonical_entity_path(reported)
    except ForeignGraphPathError:
        return reported


def _change_labels(
    paths: ProjectPaths, changes: list[ChangedFile]
) -> tuple[list[str], dict[str, str]]:
    """Return the canonical query paths and their display labels.

    A rename contributes both its old and new path so a rule bound to either
    side matches, and both labels render as ``new (renamed from old)``.

    Args:
        paths: Resolved paths anchoring the graph.
        changes: Changed files as reported by Git.

    Returns:
        The deduplicated canonical query paths in change order, and a map
        from each canonical path to the label shown in the table.
    """
    query_paths: list[str] = []
    labels: dict[str, str] = {}
    seen: set[str] = set()
    for change in changes:
        current = _query_path(paths, change.path)
        label = current
        candidates = [current]
        if change.previous_path is not None:
            previous = _query_path(paths, change.previous_path)
            label = f"{current} (renamed from {previous})"
            candidates.insert(0, previous)
        for candidate in candidates:
            labels.setdefault(candidate, label)
            if candidate not in seen:
                seen.add(candidate)
                query_paths.append(candidate)
    return query_paths, labels


def _changed_files_cell(matched: list[str], labels: dict[str, str]) -> str:
    """Render one constraint's matched changed paths as a table cell.

    A directory binding and a rename can match the same file through both
    the old and new path, so labels are deduplicated while keeping their
    first-seen order.
    """
    rendered = (escape(labels.get(path, path)) for path in matched)
    return ", ".join(dict.fromkeys(rendered))


def check(
    decisions: bool = typer.Option(
        False,
        "--decisions",
        help="Also show the decision enforcing each listed rule",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        metavar="REF",
        help=(
            "Diff the working tree against the merge-base with REF, so committed work "
            "on the branch is included; default is uncommitted changes only"
        ),
    ),
) -> None:
    """List the live rules bound to the files changed in git diff.

    ``--base`` widens the diff to the merge-base with REF, so work committed
    on the branch is listed alongside uncommitted edits; without it only
    uncommitted changes to tracked files appear.

    Informational only: the listing never fails because a rule matches. A
    missing graph, a graph root outside a Git work tree, or an unreadable
    diff exits non-zero because the diff cannot be produced at all.
    """
    paths = context.resolve_paths()
    with context.resolve_store(paths) as store:
        try:
            changes = changed_files(paths.base_dir, base=base)
        except NotAWorkTreeError as exc:
            typer.echo(
                _NOT_A_WORK_TREE.format(base=paths.base_dir.as_posix()),
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except GitDiffError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        query_paths, labels = _change_labels(paths, changes)
        matches = store.get_constraints_for_paths(query_paths)
        decision_titles = (
            {decision.id: decision.title for decision in store.get_live_decisions()}
            if decisions
            else {}
        )

    changed_label = f"{len(changes)} changed files" if changes else _NO_CHANGED_FILES
    typer.echo(_SUMMARY_FORMAT.format(rules=len(matches), changed=changed_label))
    typer.echo(_DISCLAIMER)

    if not changes:
        console.print(text("No changed files to check."))
        return
    if not matches:
        console.print(text("No live rules are bound to the changed files."))
        return

    check_table = table()
    check_table.add_column("Rule", overflow="fold")
    check_table.add_column("ID", style=COLOR_ID, overflow="fold")
    check_table.add_column(
        "Changed files",
        style=COLOR_DIM,
        overflow="fold",
    )
    if decisions:
        check_table.add_column(
            "Decision",
            style=COLOR_DIM,
            overflow="fold",
        )
    for constraint, matched_paths in matches:
        row = [
            escape(constraint.description),
            escape(constraint.id),
            _changed_files_cell(matched_paths, labels),
        ]
        if decisions:
            row.append(escape(decision_titles.get(constraint.enforced_by, constraint.enforced_by)))
        check_table.add_row(*row)
    console.print(panel(check_table, title=f"Rules for changed files ({len(matches)})"))
