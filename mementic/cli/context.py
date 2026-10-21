"""Shared CLI context: resolved paths, store opening, and defaults."""

from __future__ import annotations

import sqlite3

import typer

from mementic.paths import ProjectPaths
from mementic.store import MementicStore

DEFAULT_PAGE_SIZE: int = 20
DEFAULT_LOG_SUMMARY_WIDTH: int = 19


def resolve_paths() -> ProjectPaths:
    """Resolve project and store paths for this invocation."""
    return ProjectPaths.from_cwd()


def resolve_store(paths: ProjectPaths | None = None) -> MementicStore:
    """Open the CWD graph database.

    Exits cleanly when the graph is missing so read/write commands do not
    silently create a store. ``mementic init`` is the only creation path.

    Args:
        paths: Optional resolved paths. Resolved from CWD when omitted.

    Returns:
        An open read-write store.

    Raises:
        typer.Exit: When the graph does not exist or cannot be opened.
    """
    resolved = paths or resolve_paths()
    if not resolved.graph_db.exists():
        typer.echo(
            f"Error: no mementic graph in {resolved.base_dir}. "
            "Run 'mementic init' here, or cd to the directory that owns .mementic.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        return MementicStore(db_path=resolved.graph_db)
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def try_open_store(paths: ProjectPaths) -> MementicStore | None:
    """Open the store read-only, returning None when health checks should fail.

    The existence check prevents ``MementicStore`` from creating the parent
    directory and database file just by opening it.
    """
    if not paths.graph_db.exists():
        return None
    try:
        return MementicStore(db_path=paths.graph_db, read_only=True)
    except (sqlite3.Error, OSError, ValueError, RuntimeError):
        return None
