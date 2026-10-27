"""The init command."""

from __future__ import annotations

from mementic.cli import context
from mementic.console import (
    COLOR_DIM,
    COLOR_HEADER,
    COLOR_PATH,
    console,
    escape,
    panel,
    rule,
    text,
)
from mementic.inject import sync_enforcement, sync_gitignore
from mementic.paths import display_path
from mementic.store import MementicStore


def init() -> None:
    """Initialize mementic in the current directory.

    Creates the graph database when missing (idempotent), syncs the
    enforcement section in AGENTS.md, and ignores ``.mementic/`` in a Git
    work tree. The graph is CWD-local: subdirectories do not see it.
    """
    paths = context.resolve_paths()
    created = False
    if not paths.graph_db.exists():
        store = MementicStore(db_path=paths.graph_db)
        store.close()
        created = True

    graph_label = display_path(paths.graph_db, paths.base_dir)
    agents_label = display_path(paths.agents_md, paths.base_dir)
    enforcement_note = sync_enforcement(paths.agents_md, label=agents_label)
    gitignore_note = sync_gitignore(paths.base_dir, label=".gitignore")
    graph_state = "created" if created else "already exists"

    extra = (text(escape(gitignore_note)),) if gitignore_note is not None else ()
    console.print(
        panel(
            text(
                f"[{COLOR_HEADER} bold]Graph[/]: "
                f"[{COLOR_PATH}]{escape(graph_label)}[/] "
                f"[{COLOR_DIM}]({graph_state})[/]"
            ),
            rule(),
            text(escape(enforcement_note)),
            *extra,
            title="Mementic initialized",
        )
    )
