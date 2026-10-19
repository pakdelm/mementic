"""The init command."""

from __future__ import annotations

from pathlib import Path

import typer

from mementic.cli import context
from mementic.console import (
    C_DIM,
    C_HEADER,
    C_PATH,
    console,
    escape,
    panel,
    rule,
    text,
)
from mementic.inject import sync_enforcement
from mementic.store import MementicStore


def _display_path(path: Path, base: Path) -> str:
    """Render a path relative to the base directory when possible."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def init() -> None:
    """Initialize mementic for this repository.

    Creates the graph database when missing (idempotent) and syncs the
    enforcement section in AGENTS.md (created, updated, or left unchanged).
    Deterministic: no prompts, no interactivity branching.
    """
    paths = context.resolve_paths()
    created = False
    if not paths.graph_db.exists():
        store = MementicStore(db_path=paths.graph_db)
        store.close()
        created = True

    enforcement_note = sync_enforcement(paths.agents_md)
    graph_state = "created" if created else "already exists"

    console.print(
        panel(
            text(
                f"[{C_HEADER} bold]Graph[/]: "
                f"[{C_PATH}]{escape(_display_path(paths.graph_db, paths.base_dir))}[/] "
                f"[{C_DIM}]({graph_state})[/]"
            ),
            rule(),
            text(escape(enforcement_note)),
            rule(),
            text(
                f"[{C_DIM} italic]Next: agents record rules inline via "
                f"'mementic add decision|constraint', then query with "
                f"'mementic query keywords <kw1> <kw2> ...'[/]"
            ),
            title="Mementic initialized",
        )
    )


def register(app: typer.Typer) -> None:
    """Register the init command."""
    app.command()(init)
