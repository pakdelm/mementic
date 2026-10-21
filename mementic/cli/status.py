"""The status command."""

from __future__ import annotations

from mementic.cli import context
from mementic.console import (
    COLOR_DIM,
    COLOR_FAIL,
    COLOR_PATH,
    COLOR_SUCCESS,
    STATUS_FAIL,
    STATUS_OK,
    console,
    escape,
    panel,
    rule,
    table,
    text,
)
from mementic.health import check_health


def status() -> None:
    """Show health checklist for mementic."""
    paths = context.resolve_paths()
    graph_state = "present" if paths.graph_db.exists() else "not initialized"
    store = context.try_open_store(paths)
    try:
        items = check_health(store, repo_path=paths.base_dir)
    finally:
        if store is not None:
            store.close()

    health_table = table(show_header=False, show_edge=False, padding=(0, 1))
    health_table.add_column("Status", width=7)
    health_table.add_column("Check", style="bold")
    health_table.add_column("Hint", style=COLOR_DIM)
    ok_count = 0
    for item in items:
        if item.status == "ok":
            health_table.add_row(f"[{COLOR_SUCCESS}]{STATUS_OK}[/]", item.label, "")
            ok_count += 1
        else:
            health_table.add_row(f"[{COLOR_FAIL}]{STATUS_FAIL}[/]", item.label, item.hint or "")
    console.print(
        panel(
            text(
                f"[{COLOR_DIM}]Graph[/]: [{COLOR_PATH}]{escape(str(paths.graph_db))}[/] "
                f"[{COLOR_DIM}]({graph_state})[/]"
            ),
            rule(),
            health_table,
            rule(),
            text(f"[{COLOR_DIM}]{ok_count}/{len(items)} checks passed[/]"),
            title="Status",
        )
    )
