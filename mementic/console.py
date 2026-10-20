"""Centralized Rich Console styling for consistent CLI output.

Every color, panel, table, and progress bar setting is defined here so all
commands share the same visual language.  Uses the GitHub Dark palette.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.box import SIMPLE_HEAD
from rich.console import Console, Group as _Group
from rich.markup import escape as _escape
from rich.panel import Panel as _Panel
from rich.rule import Rule as _Rule
from rich.table import Table as _Table
from rich.text import Text as _Text

from mementic.text import configure_utf8_streams, sanitize

if TYPE_CHECKING:
    from rich.markdown import Markdown as _Markdown

COLOR_SUCCESS: str = "#3fb950"
COLOR_FAIL: str = "#f85149"
COLOR_WARN: str = "#d29922"
COLOR_ID: str = "#58a6ff"
COLOR_PATH: str = "#79c0ff"
COLOR_HEADER: str = "#e6edf3"
COLOR_DIM: str = "#8b949e"
COLOR_BORDER: str = "#30363d"

PANEL_KWARGS: dict[str, Any] = {
    "border_style": COLOR_BORDER,
    "safe_box": True,
}

TABLE_KWARGS: dict[str, Any] = {
    "box": SIMPLE_HEAD,
    "show_header": True,
}

KV_TABLE_KWARGS: dict[str, Any] = {
    "box": SIMPLE_HEAD,
    "show_header": False,
    "show_edge": False,
    "padding": (0, 2),
}

STATUS_OK: str = "[OK]"
STATUS_FAIL: str = "[FAIL]"
STATUS_DEPRECATED: str = "[DEPR]"


configure_utf8_streams()

console = Console(highlight=False)


def panel(
    *renderables: Any,
    title: str = "",
    border_style: str = COLOR_BORDER,
    **kwargs: Any,
) -> _Panel:
    """Build a Panel with project-default styling.

    The ``title`` string may contain Rich markup.  It is automatically
    wrapped in ``[bold COLOR_HEADER]`` if it does not already contain
    square-bracket markup.
    """
    styled_title = title
    if "[" not in title:
        styled_title = f"[bold {COLOR_HEADER}]{title}[/]"
    merged = {**PANEL_KWARGS, "border_style": border_style, **kwargs}
    if len(renderables) == 1:
        content = renderables[0]
    else:
        content = _Group(*renderables)
    return _Panel(
        content,
        title=styled_title,
        **merged,  # type: ignore[arg-type]
    )


def table(**kwargs: Any) -> _Table:
    """Build a Table with project-default styling (header row with separator)."""
    return _Table(**{**TABLE_KWARGS, **kwargs})


def kv_table(**kwargs: Any) -> _Table:
    """Build a two-column key-value table (no header row, no edge lines)."""
    return _Table(**{**KV_TABLE_KWARGS, **kwargs})


def rule(style: str = COLOR_BORDER) -> _Rule:
    """Build a horizontal Rule separator."""
    return _Rule(style=style)


def text(markup: str, **kwargs: Any) -> _Text:
    """Build a Rich Text object from markup string."""
    return _Text.from_markup(markup, **kwargs)


def markdown(source: str) -> _Markdown:
    """Build a sanitized Markdown renderable.

    ``rich.markdown`` pulls in pygments and markdown-it, so it is imported
    here rather than at module scope to keep non-Markdown commands fast.
    """
    from rich.markdown import Markdown

    return Markdown(sanitize(source))


def escape(value: str) -> str:
    """Escape Rich markup in user-supplied text."""
    return _escape(value)
