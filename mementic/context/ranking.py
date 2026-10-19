"""IDF-weighted keyword scoring, deterministic ranking, and budget truncation."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from mementic.models import Constraint, Decision
from mementic.text import estimate_tokens

FIELD_WEIGHT_TITLE: float = 3.0
FIELD_WEIGHT_DESCRIPTION: float = 2.0
FIELD_WEIGHT_RATIONALE: float = 1.0
MIN_SHOWN_PER_SECTION: int = 3

EntryT = TypeVar("EntryT", bound="Constraint | Decision")
ItemT = TypeVar("ItemT", bound="Constraint | Decision")


def _matches_keyword(field_text: str, keyword: str) -> bool:
    """Return True when ``keyword`` occurs at a word boundary in ``field_text``."""
    return re.search(rf"\b{re.escape(keyword.lower())}", field_text.lower()) is not None


def _searchable_fields(entry: Constraint | Decision) -> list[tuple[str, float]]:
    """Return ``(field_text, field_weight)`` pairs for one entry."""
    if isinstance(entry, Constraint):
        return [(entry.description, FIELD_WEIGHT_DESCRIPTION)]
    return [
        (entry.title, FIELD_WEIGHT_TITLE),
        (entry.rationale, FIELD_WEIGHT_RATIONALE),
    ]


def document_frequencies(
    entries: Sequence[Constraint | Decision],
    keywords: list[str],
) -> dict[str, int]:
    """Count, per keyword, how many entries contain it.

    Args:
        entries: Entries to scan.
        keywords: Query keywords.

    Returns:
        Map of keyword to document frequency.
    """
    counts: dict[str, int] = dict.fromkeys(keywords, 0)
    for entry in entries:
        for keyword in keywords:
            if any(
                _matches_keyword(field_text, keyword)
                for field_text, _ in _searchable_fields(entry)
            ):
                counts[keyword] += 1
    return counts


def idf_weights(n_docs: int, doc_freq: dict[str, int]) -> dict[str, float]:
    """Compute smoothed inverse document frequency for each keyword.

    Args:
        n_docs: Size of the live corpus.
        doc_freq: Per-keyword document frequency.

    Returns:
        Map of keyword to IDF weight.
    """
    if n_docs <= 0:
        return dict.fromkeys(doc_freq, 0.0)
    return {
        keyword: math.log(1 + n_docs / (frequency + 1))
        for keyword, frequency in doc_freq.items()
    }


def keyword_idf_score(
    entry: Constraint | Decision,
    keywords: list[str],
    idf: dict[str, float],
) -> float:
    """Score one entry by IDF-weighted keyword matches across its fields.

    Args:
        entry: Entry to score.
        keywords: Query keywords.
        idf: Per-keyword IDF weights.

    Returns:
        Non-negative relevance score.
    """
    if not keywords:
        return 0.0
    total = 0.0
    for keyword in keywords:
        weight = max(
            (
                field_weight
                for field_text, field_weight in _searchable_fields(entry)
                if _matches_keyword(field_text, keyword)
            ),
            default=0.0,
        )
        if weight > 0.0:
            total += idf.get(keyword, 0.0) * weight
    return total / len(keywords)


def rank_and_truncate(
    items: Sequence[tuple[ItemT, int]],
    max_tokens: int,
    *,
    line_formatter: Callable[[Any], str],
) -> tuple[list[ItemT], int, int]:
    """Rank items by recency then overlap and truncate to a token budget.

    Args:
        items: ``(item, overlap)`` tuples.
        max_tokens: Token budget for the selected lines.
        line_formatter: Renders one item as a compact line.

    Returns:
        ``(selected_items, shown_count, total_count)``.
    """
    total = len(items)

    sorted_items = sorted(items, key=lambda pair: pair[0].created_at, reverse=True)
    sorted_items = sorted(sorted_items, key=lambda pair: pair[1], reverse=True)

    selected: list[ItemT] = []
    tokens_used = 0
    for item, _overlap in sorted_items:
        line_tokens = estimate_tokens(line_formatter(item))
        if tokens_used + line_tokens > max_tokens:
            break
        selected.append(item)
        tokens_used += line_tokens

    return selected, len(selected), total


def rank_keyword_section(
    items: Sequence[EntryT],
    keywords: list[str],
    idf: dict[str, float],
) -> list[tuple[EntryT, float]]:
    """Score items against a query, gate zero scores, and sort deterministically.

    Args:
        items: Candidate entries.
        keywords: Query keywords.
        idf: Per-keyword IDF weights.

    Returns:
        ``(item, score)`` tuples with positive scores, ordered deterministically.
    """
    scored = [(item, keyword_idf_score(item, keywords, idf)) for item in items]
    gated = [(item, score) for item, score in scored if score > 0.0]
    gated.sort(key=lambda pair: pair[0].id)
    gated.sort(key=lambda pair: pair[0].created_at, reverse=True)
    gated.sort(key=lambda pair: pair[1], reverse=True)
    return gated


def truncate_ranked(
    ranked: Sequence[tuple[EntryT, float]],
    max_tokens: int,
    line_formatter: Callable[[Any], str],
) -> list[EntryT]:
    """Select ranked items under a token budget with a floor and backfill.

    Args:
        ranked: Ranked ``(item, score)`` tuples.
        max_tokens: Token budget for the selected lines.
        line_formatter: Renders one item as a compact line.

    Returns:
        Selected items in ranked order.
    """
    selected: list[EntryT] = []
    tokens_used = 0
    floor = min(len(ranked), MIN_SHOWN_PER_SECTION)
    for item, _score in ranked:
        line_tokens = estimate_tokens(line_formatter(item))
        if tokens_used + line_tokens > max_tokens and len(selected) >= floor:
            continue
        selected.append(item)
        tokens_used += line_tokens
    return selected
