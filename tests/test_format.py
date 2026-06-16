from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from mementic.format import (
    _compact_constraint_line,
    _compact_decision_line,
    _compact_evolution_line,
    count_tokens,
    format_context_pack,
    format_evolution,
    rank_and_truncate,
    slugify,
    timestamp,
)
from mementic.models import Constraint, Decision, DecisionStatus


def make_decision(
    id: str = "d-001",
    title: str = "Use async",
    rationale: str = "Better performance",
    rejected: list[str] | None = None,
    source: str = "ADR-001",
    ts: str = "2024-01-01T00:00:00Z",
    status: DecisionStatus = DecisionStatus.ACCEPTED,
) -> Decision:
    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        rejected_alternatives=rejected or [],
        source=source,
        status=status,
        author="test-author",
        created_at=ts,
        modified_at=ts,
    )


def make_constraint(
    id: str = "c-001",
    description: str = "All I/O must be async",
    enforced_by: str = "d-001",
    source: str = "ADR-001",
    ts: str = "2024-01-01T00:00:00Z",
    status: str = "active",
) -> Constraint:
    return Constraint(
        id=id,
        description=description,
        bound_entities=["src/io.py"],
        enforced_by=enforced_by,
        source=source,
        status=status,
        created_at=ts,
    )


# ---- helpers for tuple lists ----

def d(d: Decision, score: int = 1) -> tuple[Decision, int]:
    """Wrap a Decision with overlap score."""
    return (d, score)


def c(c: Constraint, score: int = 1) -> tuple[Constraint, int]:
    """Wrap a Constraint with overlap score."""
    return (c, score)


# ======================================================================
# rank_and_truncate
# ======================================================================


class TestRankAndTruncate:
    def test_returns_selected_shown_total(self) -> None:
        items = [c(make_constraint(id="c-1"), 3)]
        selected, shown, total = rank_and_truncate(
            items, max_tokens=9999, line_formatter=_compact_constraint_line
        )
        assert len(selected) == 1
        assert shown == 1
        assert total == 1

    def test_sorts_by_overlap_descending(self) -> None:
        items = [
            c(make_constraint(id="c-low", description="Low"), 1),
            c(make_constraint(id="c-high", description="High"), 10),
            c(make_constraint(id="c-mid", description="Mid"), 5),
        ]
        selected, _, _ = rank_and_truncate(
            items, max_tokens=9999, line_formatter=_compact_constraint_line
        )
        ids = [x.id for x in selected]  # type: ignore[attr-defined]
        assert ids == ["c-high", "c-mid", "c-low"]

    def test_sorts_by_recency_for_equal_overlap(self) -> None:
        items = [
            c(make_constraint(id="c-old", ts="2023-01-01T00:00:00Z"), 1),
            c(make_constraint(id="c-new", ts="2025-01-01T00:00:00Z"), 1),
            c(make_constraint(id="c-mid", ts="2024-01-01T00:00:00Z"), 1),
        ]
        selected, _, _ = rank_and_truncate(
            items, max_tokens=9999, line_formatter=_compact_constraint_line
        )
        ids = [x.id for x in selected]  # type: ignore[attr-defined]
        assert ids == ["c-new", "c-mid", "c-old"]

    def test_sorts_active_before_deprecated_for_equal_overlap_and_recency(
        self,
    ) -> None:
        ts = "2024-01-01T00:00:00Z"
        items = [
            c(make_constraint(id="c-dep", status="deprecated", ts=ts), 1),
            c(make_constraint(id="c-act", status="active", ts=ts), 1),
            c(make_constraint(id="c-act2", status="active", ts=ts), 1),
        ]
        selected, _, _ = rank_and_truncate(
            items, max_tokens=9999, line_formatter=_compact_constraint_line
        )
        ids = [x.id for x in selected]  # type: ignore[attr-defined]
        # active first, then deprecated
        assert ids[0] == "c-act"
        assert ids[-1] == "c-dep"

    def test_truncates_at_token_budget(self) -> None:
        """Items that would exceed token budget are excluded (whole-item cut)."""
        items = [
            c(make_constraint(id="c-a", description="Alpha"), 5),
            c(make_constraint(id="c-b", description="Beta"), 3),
            c(make_constraint(id="c-c", description="Gamma"), 1),
        ]
        # Budget of 1 token — can't fit any compact line
        selected, shown, total = rank_and_truncate(
            items, max_tokens=1, line_formatter=_compact_constraint_line
        )
        assert shown == 0
        assert total == 3

    def test_truncates_at_token_budget_second_item_excluded(self) -> None:
        """Token budget that fits one item but not two."""
        items = [
            c(make_constraint(id="c-a", description="Alpha"), 5),
            c(make_constraint(id="c-b", description="Beta"), 3),
        ]
        # First line is ~12 tokens; max_tokens=20 fits first but not both.
        line_a = _compact_constraint_line(items[0][0])
        tokens_a = count_tokens(line_a)
        line_b = _compact_constraint_line(items[1][0])
        tokens_b = count_tokens(line_b)
        budget = tokens_a + (tokens_b // 2)  # fits first, not second
        selected, shown, _ = rank_and_truncate(
            items, max_tokens=budget, line_formatter=_compact_constraint_line
        )
        assert shown == 1
        assert selected[0].id == "c-a"  # type: ignore[attr-defined]

    def test_decision_status_sort(self) -> None:
        """DecisionStatus enum values are sorted correctly."""
        ts = "2024-01-01T00:00:00Z"
        items = [
            d(make_decision(id="d-dep", status=DecisionStatus.DEPRECATED, ts=ts), 1),
            d(make_decision(id="d-rej", status=DecisionStatus.REJECTED, ts=ts), 1),
            d(make_decision(id="d-acc", status=DecisionStatus.ACCEPTED, ts=ts), 1),
        ]
        selected, _, _ = rank_and_truncate(
            items, max_tokens=9999, line_formatter=_compact_decision_line
        )
        ids = [x.id for x in selected]  # type: ignore[attr-defined]
        assert ids[0] == "d-acc"  # accepted first
        assert "d-dep" in ids
        assert "d-rej" in ids

    def test_empty_items(self) -> None:
        selected, shown, total = rank_and_truncate(
            [], max_tokens=1000, line_formatter=_compact_constraint_line
        )
        assert selected == []
        assert shown == 0
        assert total == 0

    def test_all_items_fit_within_budget(self) -> None:
        items = [c(make_constraint(id=f"c-{i}"), 1) for i in range(3)]
        selected, shown, total = rank_and_truncate(
            items, max_tokens=9999, line_formatter=_compact_constraint_line
        )
        assert shown == 3
        assert total == 3


# ======================================================================
# format_context_pack
# ======================================================================


class TestFormatContextPack:
    def test_produces_expected_structure(self) -> None:
        decisions = [d(make_decision())]
        constraints = [c(make_constraint())]

        output = format_context_pack("Add async support", decisions, constraints)

        assert output.startswith("## Architectural Context for: Add async support")
        assert "### Constraints (MUST follow)" in output
        assert "- All I/O must be async (#c-001)" in output
        assert "### Relevant Decisions" in output
        assert "- Better performance (#d-001)" in output

    def test_with_empty_lists(self) -> None:
        output: str = format_context_pack("Empty task", [], [])

        assert output.startswith("## Architectural Context for: Empty task")
        assert "### Constraints (MUST follow)" in output
        assert "_None_" in output
        assert "### Relevant Decisions" in output
        assert "_None_" in output

    def test_with_only_decisions(self) -> None:
        decisions = [d(make_decision(title="Use pydantic"))]
        output = format_context_pack("Task", decisions, [])

        assert "### Constraints (MUST follow)" in output
        assert "_None_" in output  # No constraints
        assert "### Relevant Decisions" in output
        assert "- Better performance (#d-001)" in output

    def test_with_only_constraints(self) -> None:
        constraints = [c(make_constraint(description="Must use type hints"))]
        output = format_context_pack("Task", [], constraints)

        assert "### Constraints (MUST follow)" in output
        assert "- Must use type hints (#c-001)" in output
        assert "### Relevant Decisions" in output
        assert "_None_" in output  # No decisions

    def test_compact_format_no_c_prefix(self) -> None:
        """Compact format drops the [C] prefix."""
        constraints = [c(make_constraint(description="Must use double quotes"))]
        output = format_context_pack("Task", [], constraints)

        assert "[C]" not in output
        assert "- Must use double quotes (#c-001)" in output

    def test_compact_format_no_enforced_by(self) -> None:
        """Compact format drops (enforced by: X)."""
        constraints = [c(make_constraint(description="A test rule", enforced_by="dec-42"))]
        output = format_context_pack("Task", [], constraints)

        assert "enforced by" not in output
        assert "- A test rule (#c-001)" in output

    def test_compact_format_no_decision_title_date(self) -> None:
        """Compact decision format drops title and date."""
        decisions = [d(make_decision(title="Pick DB", rationale="Chose PostgreSQL"))]
        output = format_context_pack("Task", decisions, [])

        assert "Pick DB" not in output
        assert "2024-01-01" not in output
        assert "- Chose PostgreSQL (#d-001)" in output

    def test_compact_format_no_rejected_alternatives(self) -> None:
        """Compact decision format drops Rejected lines."""
        decisions = [
            d(make_decision(
                title="Pick DB",
                rationale="Chose PostgreSQL",
                rejected=["SQLite", "MongoDB"],
            ))
        ]
        output = format_context_pack("Task", decisions, [])

        assert "Rejected" not in output
        assert "SQLite" not in output

    def test_multiple_items(self) -> None:
        decisions = [
            d(make_decision(id="d-1", rationale="First decision"), 2),
            d(make_decision(id="d-2", rationale="Second decision"), 1),
        ]
        constraints = [
            c(make_constraint(id="c-1", description="Constraint 1"), 3),
            c(make_constraint(id="c-2", description="Constraint 2"), 2),
        ]
        output = format_context_pack("Multi", decisions, constraints)

        assert "- Constraint 1 (#c-1)" in output
        assert "- Constraint 2 (#c-2)" in output
        assert "- First decision (#d-1)" in output
        assert "- Second decision (#d-2)" in output

    def test_omission_note_constraints(self) -> None:
        """When constraints are truncated, an omission note is shown."""
        constraints = [
            c(make_constraint(id=f"c-{i}", description=f"Constraint {i}"), 1)
            for i in range(1, 20)
        ]
        output = format_context_pack(
            "Task", [], constraints, max_tokens=60
        )
        assert "more constraints omitted (token budget)" in output

    def test_omission_note_decisions(self) -> None:
        """When decisions are truncated, an omission note is shown."""
        decisions = [
            d(make_decision(id=f"d-{i}", rationale=f"Rationale {i}"), 1)
            for i in range(1, 20)
        ]
        output = format_context_pack(
            "Task", decisions, [], max_tokens=60
        )
        assert "more decisions omitted (token budget)" in output

    def test_no_omission_note_when_all_fit(self) -> None:
        """No omission note when all items fit within the token budget."""
        constraints = [
            c(make_constraint(id="c-1", description="Only one"), 1)
        ]
        output = format_context_pack(
            "Task", [], constraints, max_tokens=9999
        )
        assert "more constraints omitted" not in output

    def test_omission_note_when_all_omitted(self) -> None:
        """When ALL items are omitted due to tiny budget, italic note is shown."""
        constraints = [
            c(make_constraint(id="c-1", description="A somewhat long description"), 1)
        ]
        output = format_context_pack(
            "Task", [], constraints, max_tokens=1  # impossibly small
        )
        assert "_... and 1 constraints omitted (token budget)._" in output

    def test_token_budget_constraints_consume_first(self) -> None:
        """Constraints are filled first; decisions get remaining budget."""
        # Many constraints consume most of the budget
        constraints = [
            c(make_constraint(id=f"c-{i}", description=f"Constraint {i}"), 1)
            for i in range(1, 10)
        ]
        decisions = [
            d(make_decision(id=f"d-{i}", rationale=f"Rationale {i}"), 1)
            for i in range(1, 10)
        ]
        output = format_context_pack(
            "Task", decisions, constraints, max_tokens=100
        )

        # Some constraints should be shown
        assert "Constraint" in output
        # Decisions may or may not appear depending on remaining budget
        # At minimum the sections exist
        assert "### Relevant Decisions" in output


# ======================================================================
# format_evolution
# ======================================================================


class TestFormatEvolution:
    def test_basic_output(self) -> None:
        decisions = [d(make_decision(id="d-1", title="Title 1", rationale="Rationale 1"))]
        output = format_evolution("mementic/format.py", decisions)

        assert output.startswith("## Evolution of mementic/format.py")
        assert "Title 1" in output
        assert "Rationale 1" in output
        assert "2024-01-01" in output
        assert "(#d-1)" in output

    def test_preserves_title_and_date(self) -> None:
        """_compact_evolution_line keeps title and created_at for provenance."""
        decisions = [
            d(make_decision(
                id="d-1",
                title="Use Pydantic",
                rationale="Strong validation",
                ts="2025-06-01T12:00:00Z",
            ))
        ]
        output = format_evolution("test.py", decisions)

        assert "[2025-06-01T12:00:00Z] Use Pydantic" in output

    def test_drops_rejected_alternatives(self) -> None:
        """Evolution format drops Rejected lines."""
        decisions = [
            d(make_decision(
                id="d-1",
                title="Use async",
                rationale="Better perf",
                rejected=["sync", "threads"],
            ))
        ]
        output = format_evolution("test.py", decisions)
        assert "Rejected" not in output

    def test_no_decisions_found(self) -> None:
        output = format_evolution("nonexistent.py", [])

        assert "## Evolution of nonexistent.py" in output
        assert "No architectural decisions found for nonexistent.py." in output

    def test_multiple_decisions_sorted(self) -> None:
        """Decisions are ranked by overlap, recency, and status."""
        decisions = [
            d(make_decision(id="d-old", ts="2022-01-01T00:00:00Z"), 1),
            d(make_decision(id="d-new", ts="2024-01-01T00:00:00Z"), 1),
        ]
        output = format_evolution("test.py", decisions)

        # Newer should appear first
        idx_new = output.index("d-new")
        idx_old = output.index("d-old")
        assert idx_new < idx_old

    def test_omission_note_when_truncated(self) -> None:
        """Omission note appears when some decisions are dropped due to budget."""
        decisions = [
            d(make_decision(id=f"d-{i}", rationale=f"Rationale {i}"), 1)
            for i in range(1, 20)
        ]
        output = format_evolution("test.py", decisions, max_tokens=60)

        assert "more decisions omitted (token budget)" in output

    def test_no_omission_note_when_all_fit(self) -> None:
        """No omission note when all decisions fit within budget."""
        decisions = [
            d(make_decision(id="d-1", rationale="Short"), 1)
        ]
        output = format_evolution("test.py", decisions, max_tokens=9999)

        assert "more decisions omitted" not in output


# ======================================================================
# Compact helper lines
# ======================================================================


class TestCompactConstraintLine:
    def test_format(self) -> None:
        c_obj = make_constraint(id="c-1", description="Use async everywhere")
        line = _compact_constraint_line(c_obj)
        assert line == "- Use async everywhere (#c-1)"

    def test_no_c_prefix(self) -> None:
        c_obj = make_constraint(id="c-1", description="A rule")
        assert "[C]" not in _compact_constraint_line(c_obj)

    def test_no_enforced_by(self) -> None:
        c_obj = make_constraint(id="c-1", description="A rule", enforced_by="d-1")
        assert "enforced by" not in _compact_constraint_line(c_obj)


class TestCompactDecisionLine:
    def test_format(self) -> None:
        d_obj = make_decision(id="d-1", rationale="Use async")
        line = _compact_decision_line(d_obj)
        assert line == "- Use async (#d-1)"

    def test_no_title(self) -> None:
        d_obj = make_decision(id="d-1", title="Some Title", rationale="Reason")
        assert "Some Title" not in _compact_decision_line(d_obj)


class TestCompactEvolutionLine:
    def test_format(self) -> None:
        d_obj = make_decision(
            id="d-1",
            title="Use Pydantic",
            rationale="Strong validation",
            ts="2024-06-01T00:00:00Z",
        )
        line = _compact_evolution_line(d_obj)
        assert line == (
            "- [2024-06-01T00:00:00Z] Use Pydantic: Strong validation (#d-1)"
        )

    def test_preserves_title_and_date(self) -> None:
        d_obj = make_decision(
            id="d-2",
            title="Pick DB",
            rationale="PostgreSQL chosen",
            ts="2025-01-15T00:00:00Z",
        )
        line = _compact_evolution_line(d_obj)
        assert "[2025-01-15T00:00:00Z]" in line
        assert "Pick DB" in line

    def test_no_rejected_line(self) -> None:
        d_obj = make_decision(
            id="d-3",
            title="A",
            rationale="B",
            rejected=["X", "Y"],
        )
        line = _compact_evolution_line(d_obj)
        assert "Rejected" not in line


# ======================================================================
# slugify / timestamp / count_tokens (unchanged, kept for regression)
# ======================================================================


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
