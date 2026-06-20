from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yake

from mementic.models import Constraint, Decision, DecisionStatus

# ID length constants for slugify(). Both decision and constraint IDs
# share the same max length for consistency.
DECISION_ID_MAX_LENGTH: int = 40
CONSTRAINT_ID_MAX_LENGTH: int = 40


def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string.

    Uses tiktoken if installed for accurate counting; falls back to
    ``len(text) // 4`` (reasonable estimate for English text).

    Args:
        text: The text to count tokens for.

    Returns:
        Estimated token count as an integer.
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, Exception):
        return len(text) // 4


def find_file_paths(text: str) -> list[str]:
    """Find potential file paths in text matching common source file patterns.

    Args:
        text: The text to scan for file paths.

    Returns:
        A list of unique file path strings found in the text.
    """
    pattern = r"[\w/\-]+\.(?:py|ts|js|java|go|rs|rb|yaml|yml|json|toml|md)\b"
    return list(set(re.findall(pattern, text)))


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
        # Truncate at word boundary (last hyphen within the limit)
        text = text[:max_length].rsplit("-", 1)[0].strip("-")
    return text


def timestamp() -> str:
    """Return ISO 8601 timestamp string."""
    return datetime.now(timezone.utc).isoformat()


def resolve_repo_name(repo_path: str) -> str:
    """Resolve a human-readable repo name from a full filesystem path.

    Checks the global config for a ``RepoEntry`` whose resolved ``path``
    matches ``repo_path``.  Returns the entry's ``label`` when set,
    otherwise the directory basename.

    Args:
        repo_path: Absolute path to the repository root.

    Returns:
        Short repo name (label or last path component).
    """
    resolved = Path(repo_path).resolve()
    try:
        from mementic.config import load_global_config

        global_cfg = load_global_config()
        for proj_entry in global_cfg.projects.values():
            for repo_entry in proj_entry.repos.values():
                try:
                    if Path(repo_entry.path).resolve() == resolved:
                        return repo_entry.label or resolved.name
                except (OSError, ValueError):
                    continue
    except Exception:
        pass
    return resolved.name


def detect_repo_path(*, check_global_config: bool = False) -> str:
    """Detect the repository root path.

    When ``check_global_config`` is ``True``, checks the global config
    first (matching CWD against registered repos) before falling back
    to git root.  This is useful for the MCP server which needs a
    *registered* repo path for proper config resolution.

    Args:
        check_global_config: If True, check ``~/.mementic/config.json``
            for a registered repo matching CWD before trying git.

    Returns:
        The detected repo path, or ``""`` when ``check_global_config``
        is True and no repo is found.
    """
    if check_global_config:
        try:
            from mementic.config import load_global_config

            global_cfg = load_global_config()
            cwd = Path.cwd().resolve()
            for proj_entry in global_cfg.projects.values():
                for repo_entry in proj_entry.repos.values():
                    try:
                        if Path(repo_entry.path).resolve() == cwd:
                            return repo_entry.path
                    except (OSError, ValueError):
                        continue
        except Exception:
            pass

    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        pass

    return "" if check_global_config else str(Path.cwd())


# ---------------------------------------------------------------------------
# Compact format helpers
# ---------------------------------------------------------------------------

# Max character width for the joined entity list in compact format lines.
# Truncation happens at a ``, `` boundary so a path is never split mid-token.
_MAX_ENTITY_LIST_CHARS = 80


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
    if len(joined) > max_chars:
        cut = joined.rfind(", ", 0, max_chars)
        if cut == -1:
            # No separator before the limit: single very long path. Fall
            # back to a hard character-boundary cut.
            return joined[:max_chars] + "..."
        return joined[:cut] + "..."
    return joined


def _compact_constraint_line(constraint: Constraint) -> str:
    """Format a constraint as a compact single-line marker.

    Drops the ``[C]`` prefix and ``(enforced by: X)`` suffix to save tokens
    while preserving the description and ID reference. When the constraint
    has bound entities, they are appended as ``(bound: a.py, b.py)`` so agents
    can see *where* the rule applies without reading files. Long entity lists
    are truncated via :func:`_truncate_entity_list`.

    Returns:
        ``- {description} (#{id})`` when no bound entities, otherwise
        ``- {description} (#{id}, bound: {entities})``.
    """
    bound_suffix = ""
    if constraint.bound_entities:
        entities = _truncate_entity_list(constraint.bound_entities)
        bound_suffix = f", bound: {entities}"
    return f"- {constraint.description} (#{constraint.id}{bound_suffix})"


def _compact_decision_line(decision: Decision) -> str:
    """Format a decision as a compact single-line marker.

    Drops title, date, and rejected alternatives to save tokens while
    preserving the rationale and ID reference. When the decision has
    affected entities, they are appended as ``(affects: a.py, b.py)`` so
    agents can see *where* the decision applies without reading files. Long
    entity lists are truncated via :func:`_truncate_entity_list`.

    Returns:
        ``- {rationale} (#{id})`` when no affected entities, otherwise
        ``- {rationale} (#{id}, affects: {entities})``.
    """
    affects_suffix = ""
    if decision.affected_entities:
        entities = _truncate_entity_list(decision.affected_entities)
        affects_suffix = f", affects: {entities}"
    return f"- {decision.rationale} (#{decision.id}{affects_suffix})"


def _compact_evolution_line(d: Decision) -> str:
    """Format a decision with provenance for evolution traces.

    Keeps title, date, and rationale. Drops only the Rejected line.

    Returns:
        ``- [{created_at}] {title}: {rationale} (#{id})``
    """
    return f"- [{d.created_at}] {d.title}: {d.rationale} (#{d.id})"


# ---------------------------------------------------------------------------
# Keyword extraction & scoring (Zero-LLM, Spec 14)
# ---------------------------------------------------------------------------

YAKE_DEFAULT_TOP: int = 30
YAKE_DEFAULT_NGRAM: int = 3
CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS: int = 10000
CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS: int = 15000


_FILLER_WORDS: frozenset[str] = frozenset({
    "fix", "bug", "bugs", "implement", "add", "issue", "issues",
    "feature", "features", "in", "for", "pr", "#",
})


def _split_task_query(task: str) -> list[str]:
    """Split a compound task description into sub-queries.

    Detects numbered lists (``(1) … (2) …`` or ``1. … 2. …``) and
    bullet lists (``- `` / ``* `` prefix).  For numbered lists the
    preamble is cleaned of filler words and prepended to each item so
    sub-queries retain domain context.

    Args:
        task: The full task description string, possibly multi-line.

    Returns:
        Sub-query strings.  Returns ``[task]`` when no splitting
        pattern is detected.
    """
    # --- Numbered list: (1) ... (2) ... (parenthesised) ---
    numbered = re.split(r"\s*\(\d+\)\s+", task)
    if len(numbered) >= 3:
        preamble = numbered[0].strip()
        items = [s.strip() for s in numbered[1:] if s.strip()]
        if items:
            preamble_words = preamble.split()
            domain_words = [
                w for w in preamble_words
                if w.rstrip(":,;.").lower() not in _FILLER_WORDS
                and not w.rstrip(":,;.").isdigit()
            ]
            preamble_clean = " ".join(
                w.rstrip(":,;.").lower() for w in domain_words
            )
            return [f"{preamble_clean}: {item}" for item in items]

    # --- Numbered list: 1. ... 2. ... (dot-separated) ---
    dotted = re.split(r"\s*\d+\.\s+", task)
    if len(dotted) >= 3:
        preamble = dotted[0].strip()
        items = [s.strip() for s in dotted[1:] if s.strip()]
        if items:
            preamble_words = preamble.split()
            domain_words = [
                w for w in preamble_words
                if w.rstrip(":,;.").lower() not in _FILLER_WORDS
                and not w.rstrip(":,;.").isdigit()
            ]
            preamble_clean = " ".join(
                w.rstrip(":,;.").lower() for w in domain_words
            )
            return [f"{preamble_clean}: {item}" for item in items]

    # --- Bullet list: lines starting with  -  or  *  ---
    lines = task.strip().splitlines()
    bullets = [
        re.sub(r"^[-*]\s+", "", line).strip()
        for line in lines
        if re.match(r"^[-*]\s+", line)
    ]
    if len(bullets) >= 3:
        return bullets

    # --- No recognised splitting pattern ---
    return [task]


def extract_task_keywords(
    task_description: str,
    *,
    top: int,
    n: int,
) -> list[str]:
    """Extract scored keywords from a task description using yake.

    Uses yake's statistical features (word position, frequency, casing,
    sentence context) to identify the most salient terms.  No LLM calls,
    no model downloads — pure unsupervised keyword extraction.

    For compound / multi-topic task descriptions (numbered lists, bullet
    lists) the description is split into sub-queries, keywords are
    extracted independently from each, and results are merged via
    round-robin interleaving with deduplication.

    Args:
        task_description: The agent's task description string.
        top: Maximum number of keywords to extract.
        n: Maximum n-gram size for keyword extraction.

    Returns:
        List of keyword strings, ordered by relevance (most relevant
        first).  Empty list for empty input.
    """
    if not task_description.strip():
        return []

    sub_queries = _split_task_query(task_description)

    # Single YAKE extractor instance (avoid re-initialising the
    # statistical model per sub-query).
    extractor = yake.KeywordExtractor(
        lan="en",
        n=n,  # max ngram size — captures "auth bug fix", "csrf token"
        dedupLim=0.9,  # deduplication threshold for similar keywords
        dedupFunc="seqm",  # sequence matcher deduplication
        windowsSize=1,  # context window
        top=top,  # number of keywords
    )

    if len(sub_queries) == 1:
        # Single query — existing behaviour.
        keywords_with_scores = extractor.extract_keywords(task_description)
        # yake returns (keyword, score) — lower score = more relevant
        return [kw for kw, _score in keywords_with_scores]

    # Multi-query: extract keywords per sub-query …
    all_keywords: list[list[str]] = []
    for sq in sub_queries:
        kws = extractor.extract_keywords(sq)
        all_keywords.append([kw for kw, _score in kws])

    # … then round-robin interleave with deduplication.
    seen: set[str] = set()
    merged: list[str] = []
    max_len = max((len(kws) for kws in all_keywords), default=0)

    for i in range(max_len):
        for kws in all_keywords:
            if i < len(kws):
                kw = kws[i]
                kw_lower = kw.lower()
                if kw_lower not in seen:
                    seen.add(kw_lower)
                    merged.append(kw)

    # Truncate to the caller-requested *top* count.
    return merged[:top]


def keyword_overlap_score(
    constraint_description: str,
    task_keywords: list[str],
) -> float:
    """Score a constraint by binary keyword overlap with the task description.

    For each task keyword, checks if it appears as a whole-word or
    word-beginning match in the constraint description.  Uses ``\\b``
    word-boundary matching to avoid false positives: "test" matches
    "testing flow" but NOT "contest results".

    Args:
        constraint_description: The constraint's description text.
        task_keywords: Keywords extracted from the task description.

    Returns:
        Score in [0, 1].  0 = no overlap, 1 = all keywords match.
        Returns 0.0 when task_keywords is empty.
    """
    if not task_keywords:
        return 0.0

    desc_lower = constraint_description.lower()
    matched = 0
    for kw in task_keywords:
        kw_lower = kw.lower()
        # ``\\b`` anchor: keyword must start at a word boundary so "auth"
        # matches "authentication" but NOT "reauth".
        if re.search(rf"\b{re.escape(kw_lower)}", desc_lower):
            matched += 1
    return matched / len(task_keywords)


def specificity_factor(bound_entities: list[str]) -> float:
    """Score multiplier inversely proportional to bound entity count.

    1 file → 1.0x, 2 files → 0.5x, 5 files → 0.2x, 10 files → 0.1x.
    Zero bound entities → 0.1x (penalized as maximally generic).

    Args:
        bound_entities: The constraint's bound entity list.

    Returns:
        0.1 when ``len(bound_entities) == 0``, otherwise ``1.0 / len(bound_entities)``.
    """
    if not bound_entities:
        return 0.1
    return 1.0 / len(bound_entities)


def token_jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity between two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        ``|intersection| / |union|`` of their lowercased token sets.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def mmr_select(
    scored: list[tuple[Any, float]],
    max_items: int,
    lambda_: float,
    *,
    text: Callable[[Any], str] = lambda c: c.description,
) -> list[tuple[Any, float]]:
    """Select diverse items using Maximum Marginal Relevance.

    MMR = λ * relevance(c) - (1-λ) * max(similarity(c, already_selected))

    Args:
        scored: List of ``(entity, combined_score)`` sorted descending.
        max_items: Maximum number of items to select.
        lambda_: Trade-off between relevance (1.0) and diversity (0.0).
        text: Callable that extracts a comparison string from an entity
            (default: ``c.description`` for Constraint objects).

    Returns:
        Selected list of ``(entity, mmr_score)`` in selection order.
    """
    if not scored or max_items <= 0:
        return []

    selected: list[tuple[Any, float]] = []
    remaining = list(scored)

    while remaining and len(selected) < max_items:
        best_idx = -1
        best_mmr = -1.0

        for i, (candidate, relevance) in enumerate(remaining):
            # Similarity to already-selected items
            if selected:
                max_sim = max(
                    token_jaccard(text(candidate), text(s))
                    for s, _ in selected
                )
            else:
                max_sim = 0.0

            mmr_score = lambda_ * relevance - (1 - lambda_) * max_sim
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_idx >= 0:
            selected.append(remaining.pop(best_idx))

    return selected


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------


def _status_priority(status: DecisionStatus | str) -> int:
    """Convert a status value to a sort priority integer.

    Lower values are shown first.  Priority: ``accepted`` / ``active`` (0),
    everything else (1).

    Args:
        status: A ``DecisionStatus`` enum value or a raw string (Constraints
            store status as a plain string).

    Returns:
        An integer priority where 0 = highest.
    """
    s = status.value if isinstance(status, DecisionStatus) else str(status).lower()
    if s in ("accepted", "active"):
        return 0
    return 1


def rank_and_truncate(
    items: Sequence[tuple[Constraint | Decision, int]],
    max_tokens: int,
    *,
    line_formatter: Callable[[Any], str],
) -> tuple[list[Constraint | Decision], int, int]:
    """Rank items by overlap then recency then status, truncate to token budget.

    Sorting (three-pass stable sort, least-significant first):

    1. **Status** — ``accepted`` / ``active`` before ``deprecated``.
    2. **Recency** — ``created_at`` descending (newer first).
    3. **Overlap score** — higher first.

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

    # Three-pass stable sort: least significant first, most significant last
    sorted_items = sorted(items, key=lambda x: _status_priority(x[0].status))
    sorted_items = sorted(sorted_items, key=lambda x: x[0].created_at, reverse=True)
    sorted_items = sorted(sorted_items, key=lambda x: x[1], reverse=True)

    selected: list[Constraint | Decision] = []
    tokens_used = 0
    for item, _overlap in sorted_items:
        line = line_formatter(item)
        line_tokens = count_tokens(line)
        if tokens_used + line_tokens > max_tokens:
            break
        selected.append(item)
        tokens_used += line_tokens

    return selected, len(selected), total


# ---------------------------------------------------------------------------
# Context pack & evolution formatting
# ---------------------------------------------------------------------------


def format_context_pack(
    task: str,
    decisions: list[Decision],
    constraints: list[Constraint],
    max_constraint_tokens: int,
    max_decision_tokens: int,
    *,
    task_keywords: list[str] | None = None,
) -> str:
    """Produces structured, token-budgeted markdown for agent context injection.

    When ``task_keywords`` is provided, constraints *and* decisions are
    ranked by keyword overlap * specificity factor.  Both entity types
    use the same pipeline.  Items with zero keyword overlap sink to
    the bottom; token budget controls how many are shown.

    When ``task_keywords`` is ``None``, falls back to recency + status
    ranking for both (backward compatible).

    Constraints and decisions each get their own independent token budget.
    Both sections always appear (even when empty, with an omission note).

    Args:
        task: Description of the task requesting context.
        decisions: List of Decision objects.
        constraints: List of Constraint objects.
        max_constraint_tokens: Maximum token budget for constraint lines.
        max_decision_tokens: Maximum token budget for decision lines.
        task_keywords: Keywords extracted from the task description.
            When ``None``, constraints are ranked by recency + status.

    Returns:
        Formatted markdown string.
    """
    lines = [f"## Architectural Context for: {task}"]

    # --- Constraints section (always present) ---
    total_c = len(constraints)
    shown_c: int
    selected_c: list[Constraint]

    if task_keywords:
        # Keyword-based ranking: score all items — no gate.
        # Items with zero overlap sink to the bottom; token budget
        # determines how many are shown.
        scored: list[tuple[Constraint, float]] = []
        for c in constraints:
            score = keyword_overlap_score(c.description, task_keywords)
            score *= specificity_factor(c.bound_entities)
            scored.append((c, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        selected_tuples = list(scored)

        # Truncate to token budget
        selected_c = []
        tokens_used_c = 0
        for c, _ in selected_tuples:
            line_tokens = count_tokens(_compact_constraint_line(c))
            if tokens_used_c + line_tokens > max_constraint_tokens:
                break
            selected_c.append(c)
            tokens_used_c += line_tokens
        shown_c = len(selected_c)
    else:
        # Fallback: recency + status ranking (no keywords available)
        constraint_tuples = [(c, 0) for c in constraints]
        selected_c_tuples, shown_c = rank_and_truncate(
            constraint_tuples, max_constraint_tokens,
            line_formatter=_compact_constraint_line,
        )[:2]
        selected_c = selected_c_tuples  # type: ignore[assignment]
        tokens_used_c = sum(
            count_tokens(_compact_constraint_line(c)) for c in selected_c  # type: ignore[arg-type]
        )

    lines.append("")
    lines.append("### Constraints (MUST follow)")
    if shown_c == 0:
        if total_c == 0:
            lines.append("_None_")
        else:
            lines.append(
                f"_... and {total_c} constraints omitted (token budget)._"
            )
    else:
        for c in selected_c:
            lines.append(_compact_constraint_line(c))
        if shown_c < total_c:
            omitted = total_c - shown_c
            lines.append(
                f"... and {omitted} more constraints omitted "
                f"(token budget)."
            )

    # --- Decisions section (always present) ---
    total_d = len(decisions)
    shown_d: int
    selected_d: list[Decision]

    if task_keywords:
        # Keyword-based ranking: score all items — no gate.
        scored_d: list[tuple[Decision, float]] = []
        for d in decisions:
            score = keyword_overlap_score(d.rationale, task_keywords)
            score *= specificity_factor(d.affected_entities)
            scored_d.append((d, score))
        scored_d.sort(key=lambda x: x[1], reverse=True)
        selected_d_tuples = list(scored_d)

        # Truncate to token budget
        selected_d = []
        tokens_used_d = 0
        for d, _ in selected_d_tuples:
            line_tokens = count_tokens(_compact_decision_line(d))
            if tokens_used_d + line_tokens > max_decision_tokens:
                break
            selected_d.append(d)
            tokens_used_d += line_tokens
        shown_d = len(selected_d)
    else:
        # Fallback: recency + status ranking (no keywords available)
        if decisions:
            decision_tuples = [(d, 0) for d in decisions]
            fallback_selected, shown_d = rank_and_truncate(
                decision_tuples, max_decision_tokens,
                line_formatter=_compact_decision_line,
            )[:2]
            selected_d = fallback_selected  # type: ignore[assignment]
        else:
            selected_d, shown_d = [], 0

    lines.append("")
    lines.append("### Relevant Decisions")
    if shown_d == 0:
        if total_d == 0:
            lines.append("_None_")
        else:
            lines.append(
                f"_... and {total_d} decisions omitted (token budget)._"
            )
    else:
        for d in selected_d:
            lines.append(_compact_decision_line(d))  # type: ignore[arg-type]
        if shown_d < total_d:
            omitted = total_d - shown_d
            lines.append(
                f"... and {omitted} more decisions omitted "
                f"(token budget)."
            )

    return "\n".join(lines)


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
            lines.append(_compact_evolution_line(d))  # type: ignore[arg-type]
        if shown < total:
            lines.append(
                f"... and {total - shown} more decisions omitted "
                f"(token budget)."
            )

    return "\n".join(lines)
