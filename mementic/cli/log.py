"""The log command."""

from __future__ import annotations

import json

import typer

from mementic.cli import context
from mementic.cli.views import ellipsize
from mementic.console import (
    C_DIM,
    C_FAIL,
    C_ID,
    C_SUCCESS,
    C_WARN,
    console,
    escape,
    panel,
    table,
    text,
)

_ACTION_STYLES = {
    "add": C_SUCCESS,
    "remove": C_FAIL,
    "deprecate": C_WARN,
    "update": C_SUCCESS,
}


def _summary_from_details(details: object) -> str:
    """Extract a one-line summary from an audit-log details payload."""
    if isinstance(details, dict) and "title" in details:
        return str(details["title"])
    if isinstance(details, dict) and "description" in details:
        return str(details["description"])
    if isinstance(details, dict) and "relation" in details:
        return (
            f"{details.get('source', '')} -> "
            f"{details.get('target', '')} ({details['relation']})"
        )
    if isinstance(details, str):
        return details
    return "-"


def log(
    n: int = typer.Option(
        context.DEFAULT_PAGE_SIZE, "--n", "-n", help="Number of log entries to show"
    ),
) -> None:
    """Show recent graph mutations from the audit log."""
    with context.resolve_store() as store:
        rows = store.get_audit_entries(limit=n)
    if not rows:
        console.print(text("No audit log found. Run init or add first."))
        raise typer.Exit()

    audit_table = table()
    audit_table.add_column("Timestamp", style=C_DIM, no_wrap=True, width=16)
    audit_table.add_column("Action", no_wrap=True, width=10)
    audit_table.add_column(
        "Rule ID", style=C_ID, no_wrap=True, overflow="ellipsis", width=18
    )
    audit_table.add_column(
        "Summary",
        no_wrap=True,
        overflow="ellipsis",
        width=context.DEFAULT_LOG_SUMMARY_WIDTH,
    )
    for row in rows:
        action = row["action"]
        try:
            details = json.loads(row["details"])
        except (json.JSONDecodeError, TypeError):
            details = row["details"]
        action_style = _ACTION_STYLES.get(action, "")
        action_text = f"[{action_style}]{action}[/]" if action_style else action
        audit_table.add_row(
            str(row["timestamp"])[:16].replace("T", " "),
            action_text,
            escape(row["entity_id"]),
            ellipsize(
                escape(_summary_from_details(details)),
                context.DEFAULT_LOG_SUMMARY_WIDTH,
            ),
        )
    console.print(panel(audit_table, title=f"Audit log (last {n})"))


def register(app: typer.Typer) -> None:
    """Register the log command."""
    app.command()(log)
