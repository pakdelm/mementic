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
    """Open the project graph database.

    Exits cleanly when the database exists but has a foreign schema version
    so callers see the migration hint instead of a traceback.
    """
    resolved = paths or resolve_paths()
    try:
        return MementicStore(db_path=resolved.graph_db)
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def try_open_store(paths: ProjectPaths) -> MementicStore | None:
    """Open the store, returning None when health checks should report failure."""
    try:
        return MementicStore(db_path=paths.graph_db)
    except (sqlite3.Error, OSError, ValueError, RuntimeError):
        return None
