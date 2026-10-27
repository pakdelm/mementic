"""Run the mementic CLI with ``python -m mementic``."""

from __future__ import annotations

from mementic.cli import app


def main() -> None:
    """Invoke the Typer application."""
    app()


if __name__ == "__main__":
    main()
