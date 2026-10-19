"""Small text helpers shared by entry writes and context rendering."""

from __future__ import annotations

import re

_MAX_ENTITY_LIST_CHARS: int = 80


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
