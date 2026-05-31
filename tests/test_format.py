from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from mementic.format import count_tokens, format_context_pack, slugify, timestamp
from mementic.models import Constraint, Decision, DecisionStatus


def make_decision(
    id: str = "d-001",
    title: str = "Use async",
    rationale: str = "Better performance",
    rejected: list[str] | None = None,
    source: str = "ADR-001",
    ts: str = "2024-01-01T00:00:00Z",
) -> Decision:
    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        rejected_alternatives=rejected or [],
        source=source,
        status=DecisionStatus.ACCEPTED,
        author="test-author",
        timestamp=ts,
    )


def make_constraint(
    id: str = "c-001",
    description: str = "All I/O must be async",
    enforced_by: str = "d-001",
    source: str = "ADR-001",
    ts: str = "2024-01-01T00:00:00Z",
) -> Constraint:
    return Constraint(
        id=id,
        description=description,
        bound_entities=["src/io.py"],
        enforced_by=enforced_by,
        source=source,
        status="active",
        timestamp=ts,
    )


class TestFormatContextPack:
    def test_produces_expected_structure(self) -> None:
        decisions = [make_decision()]
        constraints = [make_constraint()]

        output = format_context_pack("Add async support", decisions, constraints)

        assert output.startswith("## Architectural Context for: Add async support")
        assert "### Constraints (MUST follow)" in output
        assert "[C] c-001" in output
        assert '"All I/O must be async"' in output
        assert "Established by: d-001" in output
        assert "### Relevant Decisions" in output
        assert "* Use async (2024-01-01T00:00:00Z)" in output
        assert "  Better performance" in output
        assert "  Rejected:" not in output  # [] means empty, skip line

    def test_with_empty_lists(self) -> None:
        output = format_context_pack("Empty task", [], [])

        assert output == "## Architectural Context for: Empty task"
        assert "### Constraints" not in output
        assert "### Relevant Decisions" not in output

    def test_with_only_decisions(self) -> None:
        decisions = [make_decision(title="Use pydantic")]
        output = format_context_pack("Task", decisions, [])

        assert "### Constraints" not in output
        assert "### Relevant Decisions" in output
        assert "* Use pydantic" in output

    def test_with_only_constraints(self) -> None:
        constraints = [make_constraint(description="Must use type hints")]
        output = format_context_pack("Task", [], constraints)

        assert "### Constraints (MUST follow)" in output
        assert '"Must use type hints"' in output
        assert "### Relevant Decisions" not in output

    def test_rejected_alternatives_rendered(self) -> None:
        decisions = [
            make_decision(
                title="Pick DB",
                rejected=["SQLite", "PostgreSQL", "MongoDB"],
            )
        ]
        output = format_context_pack("Storage choice", decisions, [])

        assert "Rejected: SQLite, PostgreSQL, MongoDB" in output

    def test_enforced_by_unknown_fallback(self) -> None:
        constraints = [make_constraint(enforced_by="")]
        output = format_context_pack("Task", [], constraints)

        assert "Established by: unknown" in output

    def test_multiple_decisions_and_constraints(self) -> None:
        decisions = [
            make_decision(id="d-1", title="Decision 1"),
            make_decision(id="d-2", title="Decision 2"),
        ]
        constraints = [
            make_constraint(id="c-1", description="Constraint 1"),
            make_constraint(id="c-2", description="Constraint 2"),
        ]
        output = format_context_pack("Multi", decisions, constraints)

        assert output.count("[C]") == 2
        assert output.count("* ") == 2  # bullet starts line, but first * is in title


class TestSlugify:
    def test_basic_slug(self) -> None:
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self) -> None:
        assert slugify("Hello! @World#") == "hello-world"

    def test_multiple_spaces(self) -> None:
        assert slugify("Hello   World") == "hello-world"

    def test_trailing_dashes(self) -> None:
        assert slugify("-hello-world-") == "hello-world"

    def test_underscores(self) -> None:
        assert slugify("hello_world") == "hello-world"

    def test_mixed_case(self) -> None:
        assert slugify("Use async for I/O") == "use-async-for-io"

    def test_empty_string(self) -> None:
        assert slugify("") == ""

    def test_only_special_chars(self) -> None:
        assert slugify("!@#$%") == ""

    def test_max_length_truncates_at_word_boundary(self) -> None:
        result = slugify(
            "capture command must run conflict detection before saving",
            max_length=30,
        )
        # Should truncate at last hyphen within 30 chars
        assert len(result) <= 30
        assert result == "capture-command-must-run"

    def test_max_length_no_truncation_when_short(self) -> None:
        result = slugify("short-title", max_length=80)
        assert result == "short-title"

    def test_max_length_single_word_exceeds_limit(self) -> None:
        # Single long word with no hyphens — rsplit("-", 1)[0] returns the whole thing
        # truncated at max_length, minus trailing hyphens
        result = slugify("supercalifragilisticexpialidocious", max_length=10)
        assert len(result) <= 10
        assert result == "supercalif"

    def test_max_length_none_no_truncation(self) -> None:
        long_text = "a" * 200
        result = slugify(long_text)
        assert len(result) == 200


class TestTimestamp:
    ISO_8601_RE = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+(?:\+\d{2}:\d{2}|Z)$"
    )

    def test_returns_iso8601_format(self) -> None:
        ts = timestamp()
        assert self.ISO_8601_RE.match(ts), f"{ts!r} does not match ISO 8601"

    def test_returns_utc_timezone(self) -> None:
        ts = timestamp()
        assert ts.endswith("+00:00") or ts.endswith("Z")

    def test_two_calls_structure_identical(self) -> None:
        ts1 = timestamp()
        ts2 = timestamp()
        assert len(ts1) == len(ts2)
        assert self.ISO_8601_RE.match(ts1)
        assert self.ISO_8601_RE.match(ts2)


class TestCountTokens:
    """Tests for count_tokens()."""

    def test_returns_integer(self) -> None:
        """count_tokens returns an int."""
        result = count_tokens("hello world")
        assert isinstance(result, int)

    def test_short_text(self) -> None:
        """A short text returns a positive token count."""
        result = count_tokens("hello")
        assert result > 0

    def test_empty_string(self) -> None:
        """An empty string returns 0 tokens."""
        result = count_tokens("")
        assert result == 0

    def test_longer_text(self) -> None:
        """A 100-character string returns a reasonable estimate."""
        result = count_tokens("a" * 100)
        # With fallback: len//4 == 25. With tiktoken: ~100 (1 token/char).
        # Both are > 0 and <= 100.
        assert 0 < result <= 100

    def test_fallback_on_tiktoken_error(self) -> None:
        """Falls back to len//4 when tiktoken raises an exception."""
        pytest.importorskip("tiktoken")
        text = "hello " * 20  # 120 chars → 30 tokens
        with patch("tiktoken.get_encoding", side_effect=Exception("fail")):
            result = count_tokens(text)
        assert result == len(text) // 4

    def test_with_tiktoken_accurate(self) -> None:
        """With tiktoken available, returns a positive count for real text."""
        result = count_tokens("the quick brown fox jumps over the lazy dog")
        assert result > 0
