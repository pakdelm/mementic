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
from mementic.inject import sync_enforcement
from mementic.store import MementicStore


def init() -> None:
    """Initialize mementic in the current directory.

    Creates the graph database when missing (idempotent) and syncs the
    enforcement section in AGENTS.md. The graph is CWD-local: subdirectories
    do not see it.
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
                f"[{COLOR_HEADER} bold]Graph[/]: "
                f"[{COLOR_PATH}]{escape(str(paths.graph_db))}[/] "
                f"[{COLOR_DIM}]({graph_state})[/]"
            ),
            rule(),
            text(escape(enforcement_note)),
            rule(),
            text(
                f"[{COLOR_DIM} italic]Run mementic from this directory; "
                f"subdirectories do not see this graph. Next: agents record "
                f"rules inline via 'mementic add decision|constraint', then "
                f"query with 'mementic query keywords <kw1> <kw2> ...'[/]"
            ),
            title="Mementic initialized",
        )
    )
