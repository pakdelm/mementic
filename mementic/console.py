"""Centralized Rich Console styling for consistent CLI output.

Every color, panel, table, and progress bar setting is defined here so all
commands share the same visual language.  Uses the GitHub Dark palette.
"""

from __future__ import annotations

import sys
from typing import Any

from rich.box import SIMPLE_HEAD
from rich.console import Console, Group as _Group
from rich.markdown import Markdown as _Markdown
from rich.markup import escape as _escape
from rich.panel import Panel as _Panel
from rich.rule import Rule as _Rule
from rich.table import Table as _Table
from rich.text import Text as _Text

COLOR_SUCCESS: str = "#3fb950"
COLOR_FAIL: str = "#f85149"
COLOR_WARN: str = "#d29922"
COLOR_ID: str = "#58a6ff"
COLOR_PATH: str = "#79c0ff"
COLOR_HEADER: str = "#e6edf3"
COLOR_DIM: str = "#8b949e"
COLOR_BORDER: str = "#30363d"

UNICODE_FALLBACKS: dict[str, str] = {
    "\u2014": "--",   # em dash
    "\u2013": "-",    # en dash
    "\u2018": "'",    # left single quote
    "\u2019": "'",    # right single quote
    "\u201c": '"',    # left double quote
    "\u201d": '"',    # right double quote
    "\u2022": "*",    # bullet
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",    # non-breaking space
    "\u2192": "->",   # right arrow
    "\u2190": "<-",   # left arrow
    "\u2264": "<=",   # less-than-or-equal
    "\u2265": ">=",   # greater-than-or-equal
    "\u00d7": "x",    # multiplication sign
    "\u2020": "+",    # dagger
    "\u2122": "(TM)", # trademark
}

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


def _configure_utf8_streams() -> None:
    """Force UTF-8 output on streams that support reconfiguration.

    Rich output can carry graph-sourced text outside legacy Windows
    codepages (cp1252). ``errors="replace"`` degrades truly unencodable
    characters to ``?`` instead of raising ``UnicodeEncodeError``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_streams()

console = Console(highlight=False)


def sanitize(text: str) -> str:
    """Replace Unicode characters with ASCII equivalents for terminal safety.

    Graph data (descriptions, rationales from ADRs) can contain Unicode
    that crashes legacy Windows codepages (cp1252).  This function
    replaces known problematic characters with ASCII fallbacks before
    the text is handed to Rich renderers.

    Args:
        text: Potentially-Unicode input from graph data.

    Returns:
        ASCII-safe string with fallback substitutions.
    """
    for uni, ascii_fb in UNICODE_FALLBACKS.items():
        text = text.replace(uni, ascii_fb)
    return text


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
    """Build a sanitized Markdown renderable."""
    return _Markdown(sanitize(source))


def escape(value: str) -> str:
    """Escape Rich markup in user-supplied text."""
    return _escape(value)
