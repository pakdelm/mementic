"""Small text helpers shared by entry writes and context rendering."""

from __future__ import annotations

import re
import sys
from datetime import datetime

_MAX_ENTITY_LIST_CHARS: int = 80

UNICODE_FALLBACKS: dict[str, str] = {
    "\u2014": "--",    # em dash
    "\u2013": "-",     # en dash
    "\u2018": "'",     # left single quote
    "\u2019": "'",     # right single quote
    "\u201c": '"',     # left double quote
    "\u201d": '"',     # right double quote
    "\u2022": "*",     # bullet
    "\u2026": "...",   # ellipsis
    "\u00a0": " ",     # non-breaking space
    "\u2192": "->",    # right arrow
    "\u2190": "<-",    # left arrow
    "\u2264": "<=",    # less-than-or-equal
    "\u2265": ">=",    # greater-than-or-equal
    "\u00d7": "x",     # multiplication sign
    "\u2020": "+",     # dagger
    "\u2122": "(TM)",  # trademark
}


def sanitize(text: str) -> str:
    """Replace Unicode characters with ASCII equivalents for terminal safety.

    Graph data (descriptions, rationales from ADRs) can contain Unicode that
    crashes legacy Windows codepages (cp1252). This function replaces known
    problematic characters with ASCII fallbacks before the text is printed.

    Args:
        text: Potentially-Unicode input from graph data.

    Returns:
        ASCII-safe string with fallback substitutions.
    """
    for uni, ascii_fb in UNICODE_FALLBACKS.items():
        text = text.replace(uni, ascii_fb)
    return text


def configure_utf8_streams() -> None:
    """Force UTF-8 output on streams that support reconfiguration.

    Rich output can carry graph-sourced text outside legacy Windows codepages
    (cp1252). ``errors="replace"`` degrades truly unencodable characters to
    ``?`` instead of raising ``UnicodeEncodeError``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def format_timestamp(value: str) -> str:
    """Render an ISO 8601 timestamp as ``YYYY-MM-DD HH:MM``.

    Args:
        value: ISO 8601 timestamp, optionally ``Z``-suffixed or carrying a
            UTC offset.

    Returns:
        A display timestamp. Unparseable values are returned unchanged so
        legacy rows remain visible instead of rendering blank cells.
    """
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def estimate_tokens(text: str) -> int:
    """Estimate the token budget consumed by a text string.

    Uses the coarse ``len(text) // 4`` heuristic. This is a truncation
    budget estimator, not billing or usage accounting; every line is
    measured with the same heuristic so ranking order is unaffected.

    Args:
        text: The text to estimate.

    Returns:
        Estimated token count.
    """
    return len(text) // 4


def truncate_entity_list(
    entities: list[str],
    max_chars: int = _MAX_ENTITY_LIST_CHARS,
) -> str:
    """Join entity paths, truncating at a delimiter boundary.

    A partial path like ``src/modu...`` is indistinguishable from a real
    file to an agent, so the cut happens at the last complete ``, `` before
    ``max_chars``. A hard cut is the fallback for a single very long path.

    Args:
        entities: Entity path strings.
        max_chars: Maximum joined length before truncation.

    Returns:
        Joined paths, or a truncated copy ending in ``"..."``.
    """
    joined = ", ".join(entities)
    if len(joined) <= max_chars:
        return joined
    cut = joined.rfind(", ", 0, max_chars)
    if cut == -1:
        return joined[:max_chars] + "..."
    return joined[:cut] + "..."


def slugify(text: str, max_length: int | None = None) -> str:
    """Convert text to a URL-friendly ID.

    Lowercases, replaces whitespace and underscores with hyphens, and strips
    characters that cannot appear in an ID. When ``max_length`` is set, the
    slug is truncated at a word boundary.

    Args:
        text: The text to slugify.
        max_length: Optional maximum slug length.

    Returns:
        The slugified text.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = text.strip("-")
    if max_length is not None and len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0].strip("-")
    return text
