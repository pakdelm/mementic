"""Tests for the deterministic (no-LLM) extractors in mementic.deterministic."""

from __future__ import annotations

import pytest

from mementic.deterministic import (
    extract_constraints_from_consequences,
    extract_from_markdown,
    extract_from_session_deterministic,
)
from mementic.models import DecisionStatus, ExtractionResult


# ── Markdown extractor ───────────────────────────────────────────────


def test_markdown_extracts_section_headers_as_decisions() -> None:
    """Each ``## `` section becomes a Decision with the header as title."""
    content = (
        "## Use FastAPI\n"
        "Body A.\n\n"
        "## Adopt Polars\n"
        "Body B.\n\n"
        "## Use Pydantic\n"
        "Body C.\n"
    )
    result = extract_from_markdown(content, source_label="docs/test.md")

    assert len(result.decisions) == 3
    titles = [d.title for d in result.decisions]
    assert titles == ["Use FastAPI", "Adopt Polars", "Use Pydantic"]


def test_markdown_extracts_modal_language_as_constraints() -> None:
    """Lines with LOOSE modal signals inside a section become constraints."""
    content = (
        "## Rules\n"
        "- must use type hints\n"
        "- never commit secrets\n"
        "- use pydantic for models\n"
    )
    result = extract_from_markdown(content, source_label="docs/test.md")

    descriptions = [c.description for c in result.constraints]
    assert "must use type hints" in descriptions
    assert "never commit secrets" in descriptions
    assert "use pydantic for models" in descriptions


def test_markdown_binds_constraints_to_file_paths() -> None:
    """File paths mentioned in a section are captured in bound_entities."""
    content = (
        "## Models\n"
        "- must keep src/models.py in sync with the schema\n"
    )
    result = extract_from_markdown(content, source_label="docs/test.md")

    assert result.constraints
    all_bound = [e for c in result.constraints for e in c.bound_entities]
    assert "src/models.py" in all_bound


def test_markdown_top_level_modal_language() -> None:
    """Modal language before the first ``## `` header has enforced_by == ''."""
    content = (
        "Preamble.\n"
        "- must never commit the .env file\n\n"
        "## First Section\n"
        "Body.\n"
    )
    result = extract_from_markdown(content, source_label="docs/test.md")

    top_level_constraints = [c for c in result.constraints if c.enforced_by == ""]
    assert top_level_constraints
    assert any("must never" in c.description for c in top_level_constraints)
    assert top_level_constraints[0].enforced_by == ""


def test_markdown_empty_content() -> None:
    """Empty input yields an empty ExtractionResult."""
    result = extract_from_markdown("", source_label="docs/test.md")

    assert isinstance(result, ExtractionResult)
    assert result.decisions == []
    assert result.constraints == []
    assert result.affected_entities == []


def test_markdown_loose_matching_includes_should() -> None:
    """``should`` is a LOOSE signal — captured by design."""
    content = "## Style\n- should use type hints everywhere\n"
    result = extract_from_markdown(content, source_label="docs/test.md")

    assert any("should use type hints" in c.description for c in result.constraints)


def test_markdown_constraint_enforced_by_section_decision() -> None:
    """A constraint inside a section is enforced by that section's Decision."""
    content = "## API Layer\n- must use FastAPI for the API\n"
    result = extract_from_markdown(content, source_label="docs/test.md")

    assert result.decisions
    assert result.constraints
    assert result.constraints[0].enforced_by == result.decisions[0].id


def test_markdown_decision_status_accepted() -> None:
    """All extracted decisions have status ACCEPTED."""
    content = "## A\nbody\n## B\nbody\n"
    result = extract_from_markdown(content, source_label="docs/test.md")

    assert result.decisions
    assert all(d.status is DecisionStatus.ACCEPTED for d in result.decisions)


# ── Consequences extractor ───────────────────────────────────────────


def test_consequences_extracts_modal_constraints() -> None:
    """Modal lines in consequences become constraints bound to decision_id."""
    text = "- must not commit secrets\n- never break the build\n"
    decision_id = "use-fastapi"

    constraints = extract_constraints_from_consequences(
        text, source_label="adr/0001.md", decision_id=decision_id
    )

    assert len(constraints) == 2
    assert all(c.enforced_by == decision_id for c in constraints)
    descriptions = [c.description for c in constraints]
    assert "must not commit secrets" in descriptions
    assert "never break the build" in descriptions


def test_consequences_empty() -> None:
    """Empty consequences text yields an empty list."""
    constraints = extract_constraints_from_consequences(
        "", source_label="adr/0001.md", decision_id="some-decision"
    )
    assert constraints == []


def test_consequences_binds_file_paths() -> None:
    """File paths in consequences text populate bound_entities."""
    text = "- must keep src/models.py and tests/test_models.py green\n"
    decision_id = "schema-decision"

    constraints = extract_constraints_from_consequences(
        text, source_label="adr/0001.md", decision_id=decision_id
    )

    assert constraints
    bound = constraints[0].bound_entities
    assert "src/models.py" in bound
    assert "tests/test_models.py" in bound


# ── Session extractor (TIGHT matching) ───────────────────────────────


def test_session_extracts_decision_signals() -> None:
    """A line with an explicit decision phrase yields a Decision."""
    log = "[user]\nwe chose fastapi for the API\n[agent]\nSounds good.\n"
    result = extract_from_session_deterministic(log, "", "")

    assert len(result.decisions) == 1
    assert "fastapi" in result.decisions[0].title.lower()


def test_session_ignores_weak_signals() -> None:
    """``maybe we should try`` is not a decision signal (tight matching)."""
    log = "[user]\nmaybe we should try fastapi\n[agent]\nOkay.\n"
    result = extract_from_session_deterministic(log, "", "")

    assert result.decisions == []


def test_session_extracts_strict_constraints() -> None:
    """A line with a TIGHT modal signal yields a Constraint."""
    log = "[user]\nWe must not commit secrets to the repo.\n"
    result = extract_from_session_deterministic(log, "", "")

    assert len(result.constraints) == 1
    assert "must not commit secrets" in result.constraints[0].description


def test_session_ignores_loose_constraints() -> None:
    """``should`` is excluded from the TIGHT set — no constraint extracted."""
    log = "[user]\nWe should probably avoid globals in the helpers.\n"
    result = extract_from_session_deterministic(log, "", "")

    assert result.constraints == []


def test_session_uses_keyword_windowing() -> None:
    """Only the keyword-windowed portion of the log is scanned for signals."""
    # Line 0: a decision phrase with NO architecture keyword nearby.
    # 30 filler lines (no keywords, no signals) push it >20 lines from any
    # keyword match, so it falls outside the ±20-line window.
    lines: list[str] = ["we chose fastapi for the API"]
    lines.extend(f"filler line number {i}" for i in range(30))
    # Middle line: contains the "ADR" keyword AND a decision phrase.
    lines.append("ADR: we chose postgres for the database")
    lines.extend(f"trailing filler {i}" for i in range(30))
    log = "\n".join(lines)

    result = extract_from_session_deterministic(log, "", "")

    # Only the postgres decision is in the windowed region; fastapi is not.
    assert len(result.decisions) == 1
    assert "postgres" in result.decisions[0].title.lower()


def test_session_git_diff_provides_entities() -> None:
    """File paths in the git diff populate affected_entities."""
    log = ""
    diff = "modified: src/main.py\n+print('hi')\n"

    result = extract_from_session_deterministic(log, git_diff=diff, user_notes="")

    assert "src/main.py" in result.affected_entities


def test_session_user_notes_extracted() -> None:
    """TIGHT modal language in user notes yields a constraint."""
    notes = "must use type hints in all public functions\n"

    result = extract_from_session_deterministic("", "", user_notes=notes)

    assert len(result.constraints) == 1
    assert "must use type hints" in result.constraints[0].description


def test_session_empty_inputs() -> None:
    """All-empty inputs yield an empty ExtractionResult."""
    result = extract_from_session_deterministic("", "", "")

    assert isinstance(result, ExtractionResult)
    assert result.decisions == []
    assert result.constraints == []
    assert result.affected_entities == []


@pytest.mark.parametrize(
    "phrase",
    [
        "we chose fastapi for the API",
        "decided to use postgres",
        "switched to polars",
        "migrated from requests to httpx",
        "adopted pydantic for models",
        "settled on ruff for linting",
        "went with uv for packaging",
    ],
)
def test_session_decision_signal_phrases(phrase: str) -> None:
    """Each recognized decision phrase triggers a Decision."""
    result = extract_from_session_deterministic(phrase, "", "")

    assert len(result.decisions) == 1


@pytest.mark.parametrize(
    "phrase",
    [
        "must not commit secrets",
        "never break the build",
        "cannot skip the tests",
        "do not commit .env files",
        "forbidden to disable linting",
        "required to use type hints",
    ],
)
def test_session_tight_constraint_keywords(phrase: str) -> None:
    """Each TIGHT modal keyword triggers a Constraint in user notes."""
    result = extract_from_session_deterministic("", "", user_notes=phrase)

    assert len(result.constraints) == 1
