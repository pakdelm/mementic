"""Typer application assembly for the mementic CLI."""

from __future__ import annotations

import typer

from mementic.cli import add, browse, deprecate, init, log, query, status

app = typer.Typer(help="Mementic — semantic memory for engineering teams and AI agents")


def _register() -> None:
    """Attach every command module to the root application."""
    init.register(app)
    query.register(app)
    add.register(app)
    deprecate.register(app)
    browse.register(app)
    log.register(app)
    status.register(app)


_register()
