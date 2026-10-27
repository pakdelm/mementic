"""Typer application assembly for the mementic CLI."""

from __future__ import annotations

import importlib
from typing import Any

import typer
import typer.main
from typer.core import TyperGroup

from mementic.text import configure_utf8_streams

configure_utf8_streams()

_LAZY_COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("mementic.cli.init", "init"),
    "query": ("mementic.cli.query", "query_app"),
    "add": ("mementic.cli.add", "add_app"),
    "deprecate": ("mementic.cli.deprecate", "deprecate"),
    "list": ("mementic.cli.browse", "list_app"),
    "history": ("mementic.cli.browse", "history"),
    "log": ("mementic.cli.log", "log"),
    "status": ("mementic.cli.status", "status"),
}

_GROUP_ATTRS: frozenset[str] = frozenset({"query_app", "add_app", "list_app"})


class LazyTyperGroup(TyperGroup):
    """Root command group that imports each command module on demand."""

    def list_commands(self, ctx: Any) -> list[str]:
        """Import every command so help and completion list the full surface."""
        for name in _LAZY_COMMANDS:
            self._load(name)
        return super().list_commands(ctx)

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        """Import the requested command module before resolving it."""
        self._load(cmd_name)
        return super().get_command(ctx, cmd_name)

    def _load(self, name: str) -> None:
        """Import and register one command module when it is first used."""
        target = _LAZY_COMMANDS.get(name)
        if target is None or name in self.commands:
            return

        module_name, attr = target
        source = getattr(importlib.import_module(module_name), attr)
        command: Any
        if attr in _GROUP_ATTRS:
            command = typer.main.get_group(source)
        else:
            wrapper = typer.Typer(add_completion=False)
            wrapper.command(name=name)(source)
            command = typer.main.get_command(wrapper)

        command.name = name
        self.add_command(command)


app = typer.Typer(
    cls=LazyTyperGroup,
    help="Mementic: semantic memory for engineering teams and AI agents",
)


def _version_callback(value: bool) -> None:
    """Print the installed version and exit when ``--version`` is set."""
    if value:
        from mementic import __version__

        typer.echo(f"mementic {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed mementic version and exit.",
    ),
) -> None:
    """Run before any subcommand; present so Typer builds a command group."""
