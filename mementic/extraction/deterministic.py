"""Deterministic (no-LLM) extraction functions for keyless onboarding.

These functions mirror the signatures of the LLM-backed extractors in
:mod:`mementic.extraction` so the pipeline can dispatch between them
without changing call sites. They trade recall for cost: no API keys,
no tokens, no non-determinism.

Spec 15.
"""

from __future__ import annotations

import re

from mementic.extraction.llm import (
    ARCHITECTURE_KEYWORDS,
    _extract_architecture_windows,
    _strip_session_noise,
    _truncate_tail,
)
from mementic.format import (
    DECISION_ID_MAX_LENGTH,
    CONSTRAINT_ID_MAX_LENGTH,
    find_file_paths,
    slugify,
    timestamp,
)
from mementic.models import Constraint, Decision, DecisionStatus, ExtractionResult

# LOOSE modal-language keywords for markdown prose and ADR consequences.
# Recall over precision — used on structured documents where false positives
# are cheap to filter downstream.
_LOOSE_CONSTRAINT_KEYWORDS: tuple[str, ...] = (
    "must not",
    "must",
    "never",
    "always",
    "required",
    "forbidden",
    "do not",
    "do NOT",
    "should not",
    "should",
    "cannot",
    "avoid",
    "prefer",
    "enforce",
    "mandatory",
)

# Regex for the "use <X> for <Y>" constraint pattern (case-insensitive).
_USE_X_FOR_Y_PATTERN = re.compile(r"\buse\s+\S+\s+for\s+", re.IGNORECASE)

# TIGHT modal-language keywords for session logs. Sessions are noisy, so we
# drop "should", "avoid", "prefer", and "use X for Y" which produce too many
# false positives.
_TIGHT_CONSTRAINT_KEYWORDS: tuple[str, ...] = (
    "must not",
    "must",
    "never",
    "cannot",
    "do not",
    "forbidden",
    "required",
)

# Decision-signal phrases in session logs. Each indicates an explicit choice.
_SESSION_DECISION_PHRASES: tuple[str, ...] = (
    "decided to",
    "we chose",
    "switched to",
    "migrated from",
    "replaced",
    "adopted",
    "introduced",
    "removed",
    "in favor of",
    "upgraded to",
    "settled on",
    "went with",
    "refactored to",
)


def _line_matches_loose_constraint(line: str) -> bool:
    """Return True if a line contains a LOOSE constraint signal."""
    line_lower = line.lower()
    if any(kw in line_lower for kw in _LOOSE_CONSTRAINT_KEYWORDS):
        return True
    return bool(_USE_X_FOR_Y_PATTERN.search(line))


def _line_matches_tight_constraint(line: str) -> bool:
    """Return True if a line contains a TIGHT constraint signal."""
    line_lower = line.lower()
    return any(kw in line_lower for kw in _TIGHT_CONSTRAINT_KEYWORDS)


def _line_matches_session_decision(line: str) -> bool:
    """Return True if a line contains a session decision signal."""
    line_lower = line.lower()
    return any(phrase in line_lower for phrase in _SESSION_DECISION_PHRASES)


def _clean_constraint_description(line: str) -> str:
    """Strip bullet markers and leading whitespace from a constraint line."""
    stripped = line.strip()
    stripped = re.sub(r"^[-*]\s+", "", stripped)
    return stripped


def _surrounding_context(lines: list[str], index: int, radius: int = 3) -> str:
    """Return ±radius lines around the given index, joined by newlines."""
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(lines[start:end])


def _extract_constraints_from_lines(
    lines: list[str],
    source_label: str,
    enforced_by: str,
    bound_entities_source: str,
) -> list[Constraint]:
    """Extract constraints from a list of lines using the LOOSE heuristic.

    Args:
        lines: Lines to scan.
        source_label: Value for the Constraint ``source`` field.
        enforced_by: Decision ID that enforces these constraints ("" for none).
        bound_entities_source: Text used to derive ``bound_entities`` via
            :func:`find_file_paths`. Typically the surrounding section text.

    Returns:
        List of Constraint objects.
    """
    constraints: list[Constraint] = []
    bound_entities = find_file_paths(bound_entities_source)
    now = timestamp()
    for line in lines:
        if not line.strip():
            continue
        if not _line_matches_loose_constraint(line):
            continue
        description = _clean_constraint_description(line)
        if not description:
            continue
        constraints.append(
            Constraint(
                id=slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH),
                description=description,
                bound_entities=list(bound_entities),
                enforced_by=enforced_by,
                source=source_label,
                status="active",
                created_at=now,
            )
        )
    return constraints


def extract_from_markdown(content: str, source_label: str) -> ExtractionResult:
    """Extract decisions and constraints from markdown prose deterministically.

    Each ``## Section`` becomes a Decision (header → title, body → rationale).
    Within each section, lines containing LOOSE modal-language signals become
    Constraints bound to that section's Decision. Top-level prose before the
    first ``## `` header is scanned for constraints with no parent decision.

    Args:
        content: Markdown document text.
        source_label: Source label attached to extracted nodes.

    Returns:
        ExtractionResult with decisions, constraints, and affected_entities.
        Empty input yields an empty ExtractionResult.
    """
    if not content.strip():
        return ExtractionResult()

    now = timestamp()
    affected_entities = find_file_paths(content)

    # Split into sections. The first element is pre-header prose; subsequent
    # elements start with the header line (no "## " prefix, since the split
    # consumed it).
    parts = re.split(r"^## ", content, flags=re.MULTILINE)

    decisions: list[Decision] = []
    constraints: list[Constraint] = []

    # Top-level prose before any "## " header.
    top_level = parts[0]
    if top_level.strip():
        top_lines = top_level.split("\n")
        constraints.extend(
            _extract_constraints_from_lines(
                lines=top_lines,
                source_label=source_label,
                enforced_by="",
                bound_entities_source=top_level,
            )
        )

    # Section bodies.
    for section in parts[1:]:
        section_lines = section.split("\n")
        if not section_lines:
            continue
        header = section_lines[0].strip()
        if not header:
            continue
        body = "\n".join(section_lines[1:]).strip()
        decision_id = slugify(header, max_length=DECISION_ID_MAX_LENGTH)
        decisions.append(
            Decision(
                id=decision_id,
                title=header,
                rationale=body[:500],
                source=source_label,
                status=DecisionStatus.ACCEPTED,
                created_at=now,
                modified_at=now,
            )
        )
        constraints.extend(
            _extract_constraints_from_lines(
                lines=section_lines[1:],
                source_label=source_label,
                enforced_by=decision_id,
                bound_entities_source=section,
            )
        )

    return ExtractionResult(
        decisions=decisions,
        constraints=constraints,
        affected_entities=affected_entities,
    )


def extract_constraints_from_consequences(
    consequences_text: str,
    source_label: str,
    decision_id: str,
) -> list[Constraint]:
    """Extract constraints from an ADR Consequences section deterministically.

    Uses the same LOOSE modal-language heuristic as the markdown extractor,
    scoped to the consequences text. All extracted constraints are bound to
    the given ``decision_id``.

    Args:
        consequences_text: Freeform text from an ADR's Consequences section.
        source_label: Source label attached to extracted constraints.
        decision_id: ID of the decision that enforces these constraints.

    Returns:
        List of Constraint objects. Empty input yields an empty list.
    """
    if not consequences_text.strip():
        return []

    lines = consequences_text.split("\n")
    return _extract_constraints_from_lines(
        lines=lines,
        source_label=source_label,
        enforced_by=decision_id,
        bound_entities_source=consequences_text,
    )


def extract_from_session_deterministic(
    session_log: str,
    git_diff: str,
    user_notes: str,
    max_messages: int = 50,
    custom_keywords: list[str] | None = None,
) -> ExtractionResult:
    """Extract decisions and constraints from an agent session deterministically.

    Reuses the LLM-free windowing helpers from :mod:`mementic.extraction`
    (architecture keyword scan with tail fallback and opencode noise stripping),
    then applies TIGHT signal sets to the windowed text.

    Decision signals: explicit choice phrases ("decided to", "we chose", ...).
    Constraint signals: hard-rule keywords (must, never, cannot, ...). The
    TIGHT set omits "should"/"avoid"/"prefer"/"use X for Y" because they
    produce too many false positives in session prose.

    Args:
        session_log: Raw agent session log text.
        git_diff: Git diff from the session. Used only for affected_entities.
        user_notes: Optional human-provided notes; scanned with the TIGHT
            constraint heuristic.
        max_messages: Tail size when keyword windowing does not apply.
        custom_keywords: Additional architecture keywords merged with the
            built-in ``ARCHITECTURE_KEYWORDS`` for windowing.

    Returns:
        ExtractionResult with decisions, constraints, and affected_entities
        derived from the session, diff, and notes.
    """
    decisions: list[Decision] = []
    constraints: list[Constraint] = []
    now = timestamp()

    windowed = ""
    if session_log.strip():
        keywords = (
            ARCHITECTURE_KEYWORDS + custom_keywords
            if custom_keywords
            else list(ARCHITECTURE_KEYWORDS)
        )
        windows = _extract_architecture_windows(
            session_log, keywords, context_messages=20
        )
        if windows and len(windows) < 120_000:
            windowed = windows
        elif windows:
            windowed = _truncate_tail(session_log, max_messages)
        else:
            windowed = _truncate_tail(session_log, max_messages)
        windowed = _strip_session_noise(windowed)

    if windowed.strip():
        window_lines = windowed.split("\n")
        for idx, line in enumerate(window_lines):
            if not line.strip():
                continue
            if _line_matches_session_decision(line):
                title = line.strip()[:120]
                rationale = _surrounding_context(window_lines, idx, radius=3)
                decisions.append(
                    Decision(
                        id=slugify(line, max_length=DECISION_ID_MAX_LENGTH),
                        title=title,
                        rationale=rationale,
                        source="capture",
                        status=DecisionStatus.ACCEPTED,
                        created_at=now,
                        modified_at=now,
                    )
                )
            elif _line_matches_tight_constraint(line):
                description = _clean_constraint_description(line)
                if not description:
                    continue
                context_text = _surrounding_context(window_lines, idx, radius=3)
                constraints.append(
                    Constraint(
                        id=slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH),
                        description=description,
                        bound_entities=find_file_paths(context_text),
                        enforced_by="",
                        source="capture",
                        status="active",
                        created_at=now,
                    )
                )

    affected_entities = find_file_paths(git_diff) if git_diff.strip() else []

    if user_notes.strip():
        note_lines = user_notes.split("\n")
        for idx, line in enumerate(note_lines):
            if not line.strip():
                continue
            if not _line_matches_tight_constraint(line):
                continue
            description = _clean_constraint_description(line)
            if not description:
                continue
            context_text = _surrounding_context(note_lines, idx, radius=3)
            constraints.append(
                Constraint(
                    id=slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH),
                    description=description,
                    bound_entities=find_file_paths(context_text),
                    enforced_by="",
                    source="capture",
                    status="active",
                    created_at=now,
                )
            )

    return ExtractionResult(
        decisions=decisions,
        constraints=constraints,
        affected_entities=affected_entities,
    )
