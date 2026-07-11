"""Centralized Rich Console styling for consistent CLI output.

Every color, panel, table, and progress bar setting is defined here so all
commands share the same visual language.  Uses the GitHub Dark palette.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from rich.box import SIMPLE_HEAD
from rich.console import Console, Group as _Group
from rich.panel import Panel as _Panel
from rich.rule import Rule as _Rule
from rich.table import Table as _Table
from rich.text import Text as _Text

# Reconfigure stdout/stderr for UTF-8 so Rich output containing graph
# data (descriptions, rationales from ADRs) does not crash legacy
# Windows codepages (cp1252).  ``errors="replace"`` ensures any truly
# unencodable bytes degrade to ``?`` instead of raising UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


# Map of Unicode chars that can crash cp1252 terminals -> ASCII fallbacks.
# Keep in sync if new problematic characters are discovered.
_UNICODE_FALLBACKS: dict[str, str] = {
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
    for uni, ascii_fb in _UNICODE_FALLBACKS.items():
        text = text.replace(uni, ascii_fb)
    return text


C_SUCCESS: str = "#3fb950"
C_FAIL: str = "#f85149"
C_WARN: str = "#d29922"
C_ID: str = "#58a6ff"
C_PATH: str = "#79c0ff"
C_HEADER: str = "#e6edf3"
C_DIM: str = "#8b949e"
C_HINT: str = "#484f58"
C_PROGRESS: str = "#58a6ff"
C_EMPTY_BAR: str = "#21262d"
C_BORDER: str = "#30363d"


PANEL_KWARGS: dict[str, Any] = {
    "border_style": C_BORDER,
    "safe_box": True,
}

TABLE_KWARGS: dict[str, Any] = {
    "box": SIMPLE_HEAD,
    "show_header": True,
}

# Table for key-value data (no header row, two columns)
KV_TABLE_KWARGS: dict[str, Any] = {
    "box": SIMPLE_HEAD,
    "show_header": False,
    "show_edge": False,
    "padding": (0, 2),
}


def panel(
    *renderables: Any,
    title: str = "",
    border_style: str = C_BORDER,
    **kwargs: Any,
) -> _Panel:
    """Build a Panel with project-default styling.

    The ``title`` string may contain Rich markup.  It is automatically
    wrapped in ``[bold C_HEADER]`` if it does not already contain
    square-bracket markup.
    """
    styled_title = title
    if "[" not in title:
        styled_title = f"[bold {C_HEADER}]{title}[/]"
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


def rule(style: str = C_BORDER) -> _Rule:
    """Build a horizontal Rule separator."""
    return _Rule(style=style)


def text(markup: str, **kwargs: Any) -> _Text:
    """Build a Rich Text object from markup string."""
    return _Text.from_markup(markup, **kwargs)



STATUS_OK: str = "[OK]"
STATUS_FAIL: str = "[FAIL]"
STATUS_DEPRECATED: str = "[DEPR]"
STATUS_STALE: str = "~"


def status_ok(label: str, detail: str = "") -> str:
    """Rich markup for a passed check."""
    base = f"[{C_SUCCESS}]{STATUS_OK}[/] [{C_HEADER}]{label}[/]"
    if detail:
        base += f"  [{C_DIM}]{detail}[/]"
    return base


def status_fail(label: str, hint: str = "") -> str:
    """Rich markup for a failed check."""
    base = f"[{C_FAIL}]{STATUS_FAIL}[/] [{C_HEADER}]{label}[/]"
    if hint:
        base += f"  [{C_HINT}]{hint}[/]"
    return base


def status_warn(label: str, detail: str = "") -> str:
    """Rich markup for a warning."""
    base = f"[{C_WARN}]{STATUS_DEPRECATED}[/] [{C_HEADER}]{label}[/]"
    if detail:
        base += f"  [{C_DIM}]{detail}[/]"
    return base



def make_progress() -> Any:
    """Return a Rich Progress bar configured with the project theme."""
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    return Progress(
        SpinnerColumn(spinner_name="dots", style=C_PROGRESS),
        TextColumn(f"[{C_HEADER}]{{task.description}}"),
        BarColumn(
            bar_width=None,
            complete_style=C_PROGRESS,
            finished_style=C_PROGRESS,
            pulse_style=C_PROGRESS,
            style=C_EMPTY_BAR,
        ),
        TaskProgressColumn(style=C_DIM, show_speed=False),
        TimeElapsedColumn(),
        console=console,
    )



def make_step_tracker(
    progress: Any, task_id: Any, *, preamble: str = ""
) -> Callable[[str, int], None]:
    """Return an ``advance(description, count)`` callback.

    Every call updates the progress bar description and advances by
    ``count`` ticks (default 1).  ``preamble`` is prepended to the
    description — use it for a phase prefix like ``"Init"``.
    """

    def _advance(description: str, count: int = 1) -> None:
        desc = f"{preamble}  {description}" if preamble else description
        progress.update(task_id, advance=count, description=desc)

    return _advance
