"""Formatting and ranking for context packs, evolution traces, and JSON views.

This module owns the presentation layer over live graph entries: IDF-weighted
keyword scoring, dead-path filtering, deterministic ranking, token-budget
truncation, and the markdown/JSON renderers built on top of them. It holds no
state and performs no I/O beyond optional filesystem liveness checks threaded
in by callers.
"""

from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from mementic.models import Constraint, Decision
from mementic.store import utc_now

DECISION_ID_MAX_LENGTH: int = 40
CONSTRAINT_ID_MAX_LENGTH: int = 40

CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS: int = 500
CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS: int = 500

FIELD_WEIGHT_TITLE: float = 3.0
FIELD_WEIGHT_DESCRIPTION: float = 2.0
FIELD_WEIGHT_RATIONALE: float = 1.0

DEAD_PATH_GRACE_HOURS: int = 24

MIN_SHOWN_PER_SECTION: int = 3

_MAX_ENTITY_LIST_CHARS: int = 80
_RATIONALE_MAX_CHARS: int = 200
_TRUNCATION_HINT: str = "refine keywords or raise --max-tokens"

EntryT = TypeVar("EntryT", bound="Constraint | Decision")
ItemT = TypeVar("ItemT", bound="Constraint | Decision")


def count_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses the coarse ``len(text) // 4`` heuristic (reasonable estimate for
    English text). Applied uniformly to every line, so ranking order and
    truncation semantics are unaffected; budgets coarsen slightly versus
    exact tokenization.

    Args:
        text: The text to count tokens for.

    Returns:
        Estimated token count as an integer.
    """
    return len(text) // 4


def slugify(text: str, max_length: int | None = None) -> str:
    """Convert text to URL-friendly ID. Lowercase, hyphens, strip special chars.

    Args:
        text: The text to slugify.
        max_length: If set, truncate the slug to this length at a word boundary.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = text.strip("-")
    if max_length is not None and len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0].strip("-")
    return text


def timestamp() -> str:
    """Return ISO 8601 timestamp string.

    Delegates to the store's canonical clock so every timestamp in the
    system shares one format (lexicographically comparable).
    """
    return utc_now()


def resolve_repo_name(repo_path: str) -> str:
    """Resolve a short display name for a repository.

    Returns the directory basename of the repo path.

    Args:
        repo_path: Absolute path to the repository root.

    Returns:
        Short repo name (last path component).
    """
    return Path(repo_path).resolve().name


def detect_repo_path() -> str:
    """Detect the repository root path.

    Tries ``git rev-parse --show-toplevel`` to find the Git repository
    root directory. Falls back to the current working directory if Git
    is not available or the directory is not a Git repository.

    Returns:
        The detected repo path as a string.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass

    return str(Path.cwd())


def split_pack_budget(total: int | None) -> tuple[int, int]:
    """Split a total pack token budget into ``(constraints, decisions)``.

    ``None`` returns the empirical defaults unchanged. Otherwise the total
    is divided proportionally to the default split so a ``--max-tokens``
    override keeps the evidence-chosen constraint/decision ratio. The
    constraint share is floored and the rounding remainder goes to the
    decisions section, so no zero-token section is reachable.

    This is the canonical split formula; the CLI and the probe harness both
    derive from it so their logic cannot drift.

    Args:
        total: Combined token budget, or ``None`` for the defaults.

    Returns:
        ``(constraint_tokens, decision_tokens)``.
    """
    if total is None:
        return (
            CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )
    share = CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS / (
        CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS + CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS
    )
    constraint_tokens = math.floor(total * share)
    return (constraint_tokens, total - constraint_tokens)


def _truncate_entity_list(entities: list[str], max_chars: int = _MAX_ENTITY_LIST_CHARS) -> str:
    """Join entity paths with ``", "``, truncating with ``"..."`` past a limit.

    Guards against a single item with many bound files blowing the token
    budget. Truncation happens at the last complete ``, `` separator at or
    before ``max_chars`` so a path is never split mid-token — a partial path
    like ``src/modu...`` could be mistaken for a real file by the LLM. If no
    separator exists before ``max_chars`` (a single very long path), a hard
    character-boundary cut is used as a fallback.

    The ellipsis is appended *after* truncation, so the returned string may
    be up to ``max_chars + 3`` characters long.

    Args:
        entities: List of entity path strings.
        max_chars: Maximum character count of the joined string before
            truncation is applied.

    Returns:
        The joined string, or a truncated copy ending in ``"..."`` if the
        joined form exceeds ``max_chars``. Returns ``""`` for an empty list.
    """
    joined = ", ".join(entities)
    if len(joined) <= max_chars:
        return joined
    cut = joined.rfind(", ", 0, max_chars)
    if cut == -1:
        return joined[:max_chars] + "..."
    return joined[:cut] + "..."


def _compact_constraint_line(constraint: Constraint) -> str:
    """Format a constraint as a compact single-line marker.

    Drops the ``[C]`` prefix and ``(enforced by: X)`` suffix to save tokens
    while preserving the description and ID reference. When the constraint
    has bound paths, they are appended as ``(bound: a.py, b.py)`` so agents
    can see *where* the rule applies without reading files. Long path lists
    are truncated via :func:`_truncate_entity_list`.

    Returns:
        ``- {description} (#{id})`` when no bound paths, otherwise
        ``- {description} (#{id}, bound: {paths})``.
    """
    bound_suffix = ""
    if constraint.paths:
        entities = _truncate_entity_list(constraint.paths)
        bound_suffix = f", bound: {entities}"
    return f"- {constraint.description} (#{constraint.id}{bound_suffix})"


def _compact_decision_line(decision: Decision) -> str:
    """Format a decision as a compact single-line marker.

    Drops title, date, and rejected alternatives to save tokens while
    preserving the rationale and ID reference. When the decision has
    affected paths, they are appended as ``(affects: a.py, b.py)`` so
    agents can see *where* the decision applies without reading files.
    Long path lists are truncated via :func:`_truncate_entity_list`.

    Returns:
        ``- {rationale} (#{id})`` when no affected paths, otherwise
        ``- {rationale} (#{id}, affects: {paths})``.
    """
    affects_suffix = ""
    if decision.paths:
        entities = _truncate_entity_list(decision.paths)
        affects_suffix = f", affects: {entities}"
    return f"- {decision.rationale} (#{decision.id}{affects_suffix})"


def _compact_evolution_line(d: Decision) -> str:
    """Format a decision with provenance for evolution traces.

    Keeps title, date, and rationale. Drops only the Rejected line.

    Returns:
        ``- [{created_at}] {title}: {rationale} (#{id})``
    """
    return f"- [{d.created_at}] {d.title}: {d.rationale} (#{d.id})"


def _matches_keyword(field_text: str, keyword: str) -> bool:
    """Return True when ``keyword`` occurs in ``field_text``.

    Matching is case-insensitive and ``\\b`` word-boundary anchored at the
    start, so "auth" matches "authentication" but not "reauth". This keeps
    the match semantics of the former ``keyword_overlap_score`` .
    """
    return re.search(rf"\b{re.escape(keyword.lower())}", field_text.lower()) is not None


def _searchable_fields(entry: Constraint | Decision) -> list[tuple[str, float]]:
    """Return ``(field_text, field_weight)`` pairs for one entry.

    Constraints expose only their description; decisions expose title and
    rationale. Weights come from the :data:`FIELD_WEIGHT_*` constants.
    A keyword matched in several fields counts its highest matching field
    weight once.
    """
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
    """Count, per keyword, how many entries' searchable text contains it.

    Document frequencies feed :func:`idf_weights`. An entry counts once per
    keyword regardless of which or how many of its fields matched.

    Args:
        entries: The live corpus (constraints and decisions combined).
        keywords: Query keywords to count.

    Returns:
        Map of keyword to document frequency (0 when unmatched everywhere).
    """
    counts: dict[str, int] = dict.fromkeys(keywords, 0)
    for entry in entries:
        for kw in keywords:
            if any(_matches_keyword(text, kw) for text, _ in _searchable_fields(entry)):
                counts[kw] += 1
    return counts


def idf_weights(n_docs: int, doc_freq: dict[str, int]) -> dict[str, float]:
    """Compute smoothed ``ln(1 + N / (1 + df(kw)))`` for each keyword.

    Smoothed IDF is strictly positive: a keyword present in every document
    still scores ``ln(2)``, a keyword seen nowhere scores the maximum
    ``ln(1 + N)``. Rare keywords still dominate and weights decrease
    monotonically with document frequency. With ``n_docs <= 0`` every
    weight is 0.0 (no corpus to discriminate).

    Args:
        n_docs: Size of the live corpus (constraints plus decisions).
        doc_freq: Per-keyword document frequency from
            :func:`document_frequencies`.

    Returns:
        Map of keyword to inverse document frequency (always > 0 for a
        non-empty corpus).
    """
    if n_docs <= 0:
        return dict.fromkeys(doc_freq, 0.0)
    return {kw: math.log(1 + n_docs / (df + 1)) for kw, df in doc_freq.items()}


def keyword_idf_score(
    entry: Constraint | Decision,
    keywords: list[str],
    idf: dict[str, float],
) -> float:
    """Score one entry by IDF-weighted keyword matches across its fields.

    ``score = sum(idf(kw) * field_weight(kw) for matched kw) / len(keywords)``
    . A keyword matched in multiple fields contributes its
    highest field weight exactly once; normalization by query length keeps
    scores comparable across queries of different sizes.

    Args:
        entry: The constraint or decision to score.
        keywords: Query keywords.
        idf: Per-keyword IDF values from :func:`idf_weights`.

    Returns:
        Non-negative score; 0.0 only when no keyword matches the entry.
    """
    if not keywords:
        return 0.0
    total = 0.0
    for kw in keywords:
        weight = max(
            (w for text, w in _searchable_fields(entry) if _matches_keyword(text, kw)),
            default=0.0,
        )
        if weight > 0.0:
            total += idf.get(kw, 0.0) * weight
    return total / len(keywords)


def rank_and_truncate(
    items: Sequence[tuple[ItemT, int]],
    max_tokens: int,
    *,
    line_formatter: Callable[[Any], str],
) -> tuple[list[ItemT], int, int]:
    """Rank items by recency then overlap, truncate to token budget.

    Sorting (two-pass stable sort, least-significant first):

    1. **Recency** — ``created_at`` descending (newer first).
    2. **Overlap score** — higher first.

    Deprecated entries never reach this function: callers pass the derived
    live-set from the store, so no status sort exists.

    Walks the sorted items, computes the token count of each compact format
    line, and stops **before** the item that would push the total over
    ``max_tokens`` (whole-item cut — never mid-line).

    Args:
        items: List of ``(item, overlap_score)`` tuples.
        max_tokens: Maximum token budget for the compact-formatted lines.
        line_formatter: Function to format an item as a compact line (one
            of ``_compact_*_line``).

    Returns:
        ``(selected_items, shown_count, total_count)``.
    """
    total = len(items)

    sorted_items = sorted(items, key=lambda x: x[0].created_at, reverse=True)
    sorted_items = sorted(sorted_items, key=lambda x: x[1], reverse=True)

    selected: list[ItemT] = []
    tokens_used = 0
    for item, _overlap in sorted_items:
        line = line_formatter(item)
        line_tokens = count_tokens(line)
        if tokens_used + line_tokens > max_tokens:
            break
        selected.append(item)
        tokens_used += line_tokens

    return selected, len(selected), total


def _rank_keyword_section(
    items: Sequence[EntryT],
    keywords: list[str],
    idf: dict[str, float],
) -> list[tuple[EntryT, float]]:
    """Score items against the query, gate zero scores, sort deterministically.

    Ordering: score descending, then ``created_at``
    descending, then ``id`` ascending — implemented as three stable
    least-significant-first passes.

    Returns:
        ``(item, score)`` tuples with score > 0 only.
    """
    scored = [(it, keyword_idf_score(it, keywords, idf)) for it in items]
    gated = [(it, s) for it, s in scored if s > 0.0]
    gated.sort(key=lambda pair: pair[0].id)
    gated.sort(key=lambda pair: pair[0].created_at, reverse=True)
    gated.sort(key=lambda pair: pair[1], reverse=True)
    return gated


def _truncate_ranked(
    ranked: Sequence[tuple[EntryT, float]],
    max_tokens: int,
    line_formatter: Callable[[Any], str],
) -> list[EntryT]:
    """Select ranked items under a token budget, with floor and backfill.

    Walks the items in deterministic ranked order and:

    - admits any item whose line fits the remaining budget;
    - **floor**: while fewer than ``min(len(ranked), MIN_SHOWN_PER_SECTION)``
      items are selected, admits even over-budget items — so a single long
      top-ranked line can neither empty the section nor reduce it to one
      line when dozens of entries matched;
    - **backfill**: skips an over-budget item instead of breaking, so
      shorter lower-ranked lines further down still fit. Output preserves
      strict score-descending order because selection follows ranked order.

    The truncation marker is computed by callers as
    ``len(ranked) - len(selected)`` and stays exact under all three rules.
    """
    selected: list[EntryT] = []
    tokens_used = 0
    floor = min(len(ranked), MIN_SHOWN_PER_SECTION)
    for item, _score in ranked:
        line_tokens = count_tokens(line_formatter(item))
        if tokens_used + line_tokens > max_tokens and len(selected) >= floor:
            continue
        selected.append(item)
        tokens_used += line_tokens
    return selected


def _try_parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO 8601 timestamp; ``None`` when unparseable."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into an aware UTC datetime.

    Falls back to ``datetime.min`` (UTC) for unparseable input so malformed
    clock readings are treated as maximally old rather than crashing pack
    rendering.
    """
    parsed = _try_parse_timestamp(value)
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed


def drop_dead_path_entries(
    entries: Sequence[EntryT],
    *,
    path_exists: Callable[[str], bool] | None = None,
    now: str | None = None,
    base_dir: str | Path | None = None,
) -> list[EntryT]:
    """Drop entries whose every bound path has vanished from disk.

    Ghosts bound only to deleted files never rank usefully,
    so they are excluded at render time. Semantics:

    - Entries with no bound paths are never dropped by this filter.
    - An entry with at least one existing path survives.
    - Entries created within :data:`DEAD_PATH_GRACE_HOURS` of ``now`` are
      exempt (fresh work whose files are not committed yet). Unparseable
      ``created_at`` stamps count as inside the grace window — fail open,
      because fresh-work protection is the point of the guard.

    Context packs require resolution of repo-relative bound paths against
    the **repo root, not the process working directory**: the CLI may run
    from any directory, so callers must thread the stable base (the parent
    of the directory containing ``graph.db``) via ``base_dir``.

    Reuses the store's lazy ``path_exists`` predicate pattern: checks run per
    path during rendering, with no cleanup scans and no stored state.

    Args:
        entries: Live constraints or decisions to filter.
        path_exists: Filesystem-liveness predicate; when omitted, the
            default checks ``Path(p).exists()``, resolving relative paths
            against ``base_dir`` when given.
        now: Canonical clock reading (ISO 8601); defaults to
            :func:`mementic.store.utc_now`.
        base_dir: Stable base for resolving repo-relative bound paths (the
            directory containing the ``.mementic`` dir). Ignored when
            ``path_exists`` is supplied.

    Returns:
        The surviving entries, in input order.
    """
    if path_exists is not None:
        exists = path_exists
    elif base_dir is not None:
        base = Path(base_dir)

        def exists(p: str) -> bool:
            return (base / p).exists()
    else:

        def exists(p: str) -> bool:
            return Path(p).exists()

    now_dt = _parse_timestamp(now if now is not None else utc_now())
    cutoff = now_dt - timedelta(hours=DEAD_PATH_GRACE_HOURS)
    kept: list[EntryT] = []
    for entry in entries:
        if not entry.paths:
            kept.append(entry)
            continue
        if any(exists(p) for p in entry.paths):
            kept.append(entry)
            continue
        created = _try_parse_timestamp(entry.created_at)
        if created is not None and created < cutoff:
            continue
        kept.append(entry)
    return kept


class ContextPackStats(BaseModel):
    """Shown-vs-matched accounting for one rendered context pack.

    Attributes:
        matched_constraints: Constraints passing the relevance gate before
            truncation.
        shown_constraints: Constraints actually rendered within budget.
        matched_decisions: Decisions passing the relevance gate before
            truncation.
        shown_decisions: Decisions actually rendered within budget.
    """

    matched_constraints: int = 0
    shown_constraints: int = 0
    matched_decisions: int = 0
    shown_decisions: int = 0


class ContextPackResult(BaseModel):
    """Rendered context pack text plus its accounting and selected items.

    Attributes:
        text: Markdown pack body (same content ``format_memory_context``
            returns).
        stats: Shown/matched counts powering honest headers and summary
            lines.
        constraints: The rendered constraint items (post gate + budget).
        decisions: The rendered decision items (post gate + budget).
    """

    text: str
    stats: ContextPackStats
    constraints: list[Constraint] = []
    decisions: list[Decision] = []


def _render_keyword_section(
    heading: str,
    total_in: int,
    matched: int,
    selected: Sequence[Any],
    line_formatter: Callable[[Any], str],
    noun: str,
) -> list[str]:
    """Render one keyword-path section with honest accounting markers."""
    lines = ["", heading]
    if matched == 0:
        if total_in == 0:
            lines.append("_None_")
        else:
            lines.append(f"_0 of {total_in} {noun} matched your keywords._")
        return lines
    if not selected:
        lines.append(f"+{matched} more below threshold - {_TRUNCATION_HINT}")
        return lines
    for item in selected:
        lines.append(line_formatter(item))
    if len(selected) < matched:
        lines.append(f"+{matched - len(selected)} more below threshold - {_TRUNCATION_HINT}")
    return lines


def _render_recency_fallback(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int,
    max_decision_tokens: int,
) -> ContextPackResult:
    """Render the legacy no-keywords pack: recency ranking, budget caps.

    Both sections always appear; each reports an honest omission note when
    the token budget cut items.
    """
    stats = ContextPackStats()

    constraint_tuples = [(c, 0) for c in constraints]
    selected_c, shown_c, _ = rank_and_truncate(
        constraint_tuples,
        max_constraint_tokens,
        line_formatter=_compact_constraint_line,
    )

    decision_tuples = [(d, 0) for d in decisions]
    selected_d, shown_d, _ = rank_and_truncate(
        decision_tuples,
        max_decision_tokens,
        line_formatter=_compact_decision_line,
    )

    stats.matched_constraints = len(constraints)
    stats.shown_constraints = shown_c
    stats.matched_decisions = len(decisions)
    stats.shown_decisions = shown_d

    lines = ["## Architectural Context"]
    lines.append("")
    lines.append("### Constraints (MUST follow)")
    if shown_c == 0:
        if len(constraints) == 0:
            lines.append("_None_")
        else:
            lines.append(f"_... and {len(constraints)} constraints omitted (token budget)._")
    else:
        for c in selected_c:
            lines.append(_compact_constraint_line(c))
        if shown_c < len(constraints):
            omitted = len(constraints) - shown_c
            lines.append(f"... and {omitted} more constraints omitted (token budget).")

    lines.append("")
    lines.append("### Relevant Decisions")
    if shown_d == 0:
        if len(decisions) == 0:
            lines.append("_None_")
        else:
            lines.append(f"_... and {len(decisions)} decisions omitted (token budget)._")
    else:
        for d in selected_d:
            lines.append(_compact_decision_line(d))
        if shown_d < len(decisions):
            omitted = len(decisions) - shown_d
            lines.append(f"... and {omitted} more decisions omitted (token budget).")

    return ContextPackResult(
        text="\n".join(lines),
        stats=stats,
        constraints=selected_c,
        decisions=selected_d,
    )


def build_context_pack(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int,
    max_decision_tokens: int,
    *,
    keywords: list[str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    base_dir: str | Path | None = None,
) -> ContextPackResult:
    """Build the context pack body plus shown/matched statistics.

    Keyword path: dead-path drop, IDF scoring over the surviving
    corpus, relevance gate (score > 0), deterministic ordering, token-budget
    truncation with a minimum-shown floor and backfill, honest header and
    section markers. Without keywords the legacy recency fallback renders
    unchanged.

    Args:
        decisions: Live decisions.
        constraints: Live constraints.
        max_constraint_tokens: Token budget for constraint lines.
        max_decision_tokens: Token budget for decision lines.
        keywords: Query keywords; ``None`` selects the recency fallback.
        path_exists: Optional liveness predicate for the dead-path drop;
            defaults to real filesystem checks.
        base_dir: Stable base for resolving repo-relative bound paths in
            the default dead-path predicate (the repo root, i.e. the parent
            of the ``.mementic`` dir) — packs must resolve against the repo
            root, not the process CWD. Ignored when ``path_exists`` is
            supplied.

    Returns:
        :class:`ContextPackResult` with the markdown body and stats.
    """
    stats = ContextPackStats()

    if keywords:
        live_constraints = drop_dead_path_entries(
            constraints,
            path_exists=path_exists,
            base_dir=base_dir,
        )
        live_decisions = drop_dead_path_entries(
            decisions,
            path_exists=path_exists,
            base_dir=base_dir,
        )

        n_docs = len(live_constraints) + len(live_decisions)
        doc_freq = document_frequencies(
            [*live_constraints, *live_decisions],
            keywords,
        )
        idf = idf_weights(n_docs, doc_freq)

        ranked_c = _rank_keyword_section(live_constraints, keywords, idf)
        ranked_d = _rank_keyword_section(live_decisions, keywords, idf)
        selected_c = _truncate_ranked(
            ranked_c,
            max_constraint_tokens,
            _compact_constraint_line,
        )
        selected_d = _truncate_ranked(
            ranked_d,
            max_decision_tokens,
            _compact_decision_line,
        )

        stats.matched_constraints = len(ranked_c)
        stats.shown_constraints = len(selected_c)
        stats.matched_decisions = len(ranked_d)
        stats.shown_decisions = len(selected_d)

        kw_summary = ", ".join(keywords)
        header = f"## Architectural Context for: [{kw_summary}]"
        total_shown = stats.shown_constraints + stats.shown_decisions
        total_matched = stats.matched_constraints + stats.matched_decisions
        header += f" - shown {total_shown} of {total_matched} matched"

        lines = [header]
        lines.extend(
            _render_keyword_section(
                "### Constraints (MUST follow)",
                len(constraints),
                stats.matched_constraints,
                selected_c,
                _compact_constraint_line,
                "constraints",
            )
        )
        lines.extend(
            _render_keyword_section(
                "### Relevant Decisions",
                len(decisions),
                stats.matched_decisions,
                selected_d,
                _compact_decision_line,
                "decisions",
            )
        )
        return ContextPackResult(
            text="\n".join(lines),
            stats=stats,
            constraints=selected_c,
            decisions=selected_d,
        )

    return _render_recency_fallback(
        decisions,
        constraints,
        max_constraint_tokens,
        max_decision_tokens,
    )


def format_memory_context(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int = CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    max_decision_tokens: int = CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    *,
    keywords: list[str] | None = None,
) -> str:
    """Produces structured, token-budgeted markdown for agent context injection.

    When ``keywords`` is provided, constraints *and* decisions are ranked by
    IDF-weighted keyword matches: rare keywords dominate,
    field weights break ties (title > description > rationale), scores are
    normalized by query length, and ordering is deterministic. Entries whose
    every bound path is gone are dropped first; items with zero
    score are gated out; the token budget controls how many are shown.

    When ``keywords`` is ``None``, falls back to recency ranking
    (backward compatible).

    Constraints and decisions each get their own independent token budget.
    Both sections always appear (even when empty).

    Args:
        decisions: List of Decision objects.
        constraints: List of Constraint objects.
        max_constraint_tokens: Maximum token budget for constraint lines.
        max_decision_tokens: Maximum token budget for decision lines.
        keywords: Keywords for matching (agent-generated).
            When ``None``, constraints are ranked by recency.

    Returns:
        Formatted markdown string.
    """
    return build_context_pack(
        decisions,
        constraints,
        max_constraint_tokens,
        max_decision_tokens,
        keywords=keywords,
    ).text


def format_evolution(
    entity_path: str,
    decisions: list[tuple[Decision, int]],
    max_tokens: int = 1500,
) -> str:
    """Format evolution trace for a code entity using compact provenance lines.

    Uses :func:`_compact_evolution_line` which preserves ``title`` and
    ``created_at`` for provenance, dropping only the ``Rejected`` line.
    Ranking and truncation follows the same logic as
    :func:`rank_and_truncate`.

    Args:
        entity_path: Path of the code entity (e.g. ``mementic/format.py``).
        decisions: List of ``(Decision, overlap_score)`` tuples.
        max_tokens: Maximum token budget for the compact-formatted lines.

    Returns:
        Formatted markdown string.
    """
    selected, shown, total = rank_and_truncate(
        decisions, max_tokens, line_formatter=_compact_evolution_line
    )

    lines = [f"## Evolution of {entity_path}"]
    if shown == 0:
        lines.append(f"No architectural decisions found for {entity_path}.")
    else:
        for d in selected:
            lines.append(_compact_evolution_line(d))
        if shown < total:
            lines.append(f"... and {total - shown} more decisions omitted (token budget).")

    return "\n".join(lines)


def format_context_pack_json(
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int = CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    max_decision_tokens: int = CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    *,
    keywords: list[str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
    base_dir: str | Path | None = None,
) -> dict:
    """Produces a JSON-serializable dict for agent context injection.

    Uses the same IDF-weighted keyword scoring, dead-path drop, relevance
    gate, deterministic ordering, and token budget truncation as
    :func:`format_memory_context`. Without keywords, falls back
    to recency ranking.

    Args:
        decisions: List of ``Decision`` objects.
        constraints: List of ``Constraint`` objects.
        max_constraint_tokens: Maximum token budget for constraint lines.
        max_decision_tokens: Maximum token budget for decision lines.
        keywords: Keywords for matching (agent-generated). When ``None``,
            items are ranked by recency.
        path_exists: Optional liveness predicate for the dead-path drop;
            defaults to real filesystem checks.
        base_dir: Stable base for resolving repo-relative bound paths in
            the default dead-path predicate (the repo root). Ignored when
            ``path_exists`` is supplied.

    Returns:
        A JSON-serializable dict with ``summary``, ``constraints``, and
        ``decisions`` keys.
    """
    result = build_context_pack(
        decisions,
        constraints,
        max_constraint_tokens,
        max_decision_tokens,
        keywords=keywords,
        path_exists=path_exists,
        base_dir=base_dir,
    )
    stats = result.stats

    c_label = f"{stats.shown_constraints} of {stats.matched_constraints}"
    d_label = f"{stats.shown_decisions} of {stats.matched_decisions}"

    if keywords:
        kw_summary = ", ".join(keywords)
        summary = f"{c_label} constraints, {d_label} decisions matched for [{kw_summary}]"
    else:
        summary = f"{stats.shown_constraints} constraints, {stats.shown_decisions} decisions"

    truncated = (
        stats.shown_constraints < stats.matched_constraints
        or stats.shown_decisions < stats.matched_decisions
    )
    if truncated:
        summary += " (token budget)"

    return {
        "summary": summary,
        "constraints": [
            {
                "id": c.id,
                "description": c.description,
                "entities": c.paths,
            }
            for c in result.constraints
        ],
        "decisions": [
            {
                "id": d.id,
                "title": d.title,
                "rationale": d.rationale,
                "entities": d.paths,
            }
            for d in result.decisions
        ],
    }


def format_file_history_json(
    entity_path: str,
    decisions: list[tuple[Decision, int]],
    constraints: list[Constraint],
) -> dict:
    """Produces a JSON-serializable dict for the ``query file`` command.

    Formats decisions with ``id``, ``title``, ``rationale`` (truncated to
    :data:`_RATIONALE_MAX_CHARS` chars), and ``created_at``. Formats
    constraints with ``id``, ``description``, and ``created_at``.

    Args:
        entity_path: Path of the code entity (e.g. ``mementic/format.py``).
        decisions: List of ``(Decision, overlap_score)`` tuples from the
            path query.
        constraints: List of ``Constraint`` objects bound to the entity.

    Returns:
        A JSON-serializable dict with ``summary``, ``file``, ``decisions``,
        and ``constraints`` keys.
    """
    decisions_json: list[dict] = []
    for d, _ in decisions:
        rationale = d.rationale
        if len(rationale) > _RATIONALE_MAX_CHARS:
            rationale = rationale[:_RATIONALE_MAX_CHARS] + "..."
        decisions_json.append(
            {
                "id": d.id,
                "title": d.title,
                "rationale": rationale,
                "created_at": d.created_at,
            }
        )

    constraints_json: list[dict] = []
    for c in constraints:
        constraints_json.append(
            {
                "id": c.id,
                "description": c.description,
                "created_at": c.created_at,
            }
        )

    total_d = len(decisions)
    total_c = len(constraints)
    summary = f"{total_d} decisions, {total_c} constraints affecting {entity_path}"

    return {
        "summary": summary,
        "file": entity_path,
        "decisions": decisions_json,
        "constraints": constraints_json,
    }
