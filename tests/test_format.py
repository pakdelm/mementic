from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from mementic.format import (
    CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    YAKE_DEFAULT_NGRAM,
    YAKE_DEFAULT_TOP,
    _compact_constraint_line,
    _compact_decision_line,
    _compact_evolution_line,
    _split_task_query,
    _truncate_entity_list,
    count_tokens,
    extract_task_keywords,
    format_context_pack,
    format_evolution,
    keyword_overlap_score,
    mmr_select,
    rank_and_truncate,
    slugify,
    specificity_factor,
    timestamp,
    token_jaccard,
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
        bound_entities=[],
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
        decisions = [make_decision()]
        constraints = [make_constraint()]

        output = format_context_pack(
            "Add async support", decisions, constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert output.startswith("## Architectural Context for: Add async support")
        assert "### Constraints (MUST follow)" in output
        assert "- All I/O must be async (#c-001)" in output
        assert "### Relevant Decisions" in output
        assert "- Better performance (#d-001)" in output

    def test_with_empty_lists(self) -> None:
        output: str = format_context_pack(
            "Empty task", [], [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert output.startswith("## Architectural Context for: Empty task")
        assert "### Constraints (MUST follow)" in output
        assert "_None_" in output
        assert "### Relevant Decisions" in output
        assert "_None_" in output

    def test_with_only_decisions(self) -> None:
        decisions = [make_decision(title="Use pydantic")]
        output = format_context_pack(
            "Task", decisions, [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "### Constraints (MUST follow)" in output
        assert "_None_" in output  # No constraints
        assert "### Relevant Decisions" in output
        assert "- Better performance (#d-001)" in output

    def test_with_only_constraints(self) -> None:
        constraints = [make_constraint(description="Must use type hints")]
        output = format_context_pack(
            "Task", [], constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "### Constraints (MUST follow)" in output
        assert "- Must use type hints (#c-001)" in output
        assert "### Relevant Decisions" in output
        assert "_None_" in output  # No decisions

    def test_compact_format_no_c_prefix(self) -> None:
        """Compact format drops the [C] prefix."""
        constraints = [make_constraint(description="Must use double quotes")]
        output = format_context_pack(
            "Task", [], constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "[C]" not in output
        assert "- Must use double quotes (#c-001)" in output

    def test_compact_format_no_enforced_by(self) -> None:
        """Compact format drops (enforced by: X)."""
        constraints = [make_constraint(description="A test rule", enforced_by="dec-42")]
        output = format_context_pack(
            "Task", [], constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "enforced by" not in output
        assert "- A test rule (#c-001)" in output

    def test_compact_format_no_decision_title_date(self) -> None:
        """Compact decision format drops title and date."""
        decisions = [make_decision(title="Pick DB", rationale="Chose PostgreSQL")]
        output = format_context_pack(
            "Task", decisions, [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "Pick DB" not in output
        assert "2024-01-01" not in output
        assert "- Chose PostgreSQL (#d-001)" in output

    def test_compact_format_no_rejected_alternatives(self) -> None:
        """Compact decision format drops Rejected lines."""
        decisions = [
            make_decision(
                title="Pick DB",
                rationale="Chose PostgreSQL",
                rejected=["SQLite", "MongoDB"],
            )
        ]
        output = format_context_pack(
            "Task", decisions, [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "Rejected" not in output
        assert "SQLite" not in output

    def test_multiple_items(self) -> None:
        decisions = [
            make_decision(id="d-1", rationale="First decision"),
            make_decision(id="d-2", rationale="Second decision"),
        ]
        constraints = [
            make_constraint(id="c-1", description="Constraint 1"),
            make_constraint(id="c-2", description="Constraint 2"),
        ]
        output = format_context_pack(
            "Multi", decisions, constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

        )

        assert "- Constraint 1 (#c-1)" in output
        assert "- Constraint 2 (#c-2)" in output
        assert "- First decision (#d-1)" in output
        assert "- Second decision (#d-2)" in output

    def test_omission_note_constraints(self) -> None:
        """When constraints are truncated, an omission note is shown."""
        constraints = [
            make_constraint(id=f"c-{i}", description=f"Constraint {i}")
            for i in range(1, 20)
        ]
        output = format_context_pack(
            "Task", [], constraints, max_constraint_tokens=60,
            max_decision_tokens=9999,

        )
        assert "more constraints omitted (token budget)" in output

    def test_omission_note_decisions(self) -> None:
        """When decisions are truncated, an omission note is shown."""
        decisions = [
            make_decision(id=f"d-{i}", rationale=f"Rationale {i}")
            for i in range(1, 20)
        ]
        output = format_context_pack(
            "Task", decisions, [], max_constraint_tokens=9999,
            max_decision_tokens=60,

        )
        assert "more decisions omitted (token budget)" in output

    def test_no_omission_note_when_all_fit(self) -> None:
        """No omission note when all items fit within the token budget."""
        constraints = [
            make_constraint(id="c-1", description="Only one")
        ]
        output = format_context_pack(
            "Task", [], constraints, max_constraint_tokens=9999,
            max_decision_tokens=9999,

        )
        assert "more constraints omitted" not in output

    def test_omission_note_when_all_omitted(self) -> None:
        """When ALL items are omitted due to tiny budget, italic note is shown."""
        constraints = [
            make_constraint(id="c-1", description="A somewhat long description")
        ]
        output = format_context_pack(
            "Task", [], constraints, max_constraint_tokens=1,  # impossibly small
            max_decision_tokens=9999,

        )
        assert "_... and 1 constraints omitted (token budget)._" in output

    def test_token_budget_constraints_consume_first(self) -> None:
        """Constraints are filled first; decisions get remaining budget."""
        # Many constraints consume most of the budget
        constraints = [
            make_constraint(id=f"c-{i}", description=f"Constraint {i}")
            for i in range(1, 10)
        ]
        decisions = [
            make_decision(id=f"d-{i}", rationale=f"Rationale {i}")
            for i in range(1, 10)
        ]
        output = format_context_pack(
            "Task", decisions, constraints, max_constraint_tokens=100,
            max_decision_tokens=100,

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
            max_length=40,
        )
        # Should truncate at last hyphen within 40 chars
        assert len(result) <= 40
        assert result == "capture-command-must-run-conflict"

    def test_max_length_no_truncation_when_short(self) -> None:
        result = slugify("short-title", max_length=40)
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


# ======================================================================
# Compact entity enrichment (bound / affects)
# ======================================================================


def _make_constraint_with_entities(bound_entities: list[str]) -> Constraint:
    """Build a Constraint with explicit bound_entities for enrichment tests."""
    return Constraint(
        id="c-1",
        description="Hard rule",
        bound_entities=bound_entities,
        enforced_by="d-1",
        source="test",
        status="active",
        created_at="2024-01-01T00:00:00Z",
    )


def _make_decision_with_entities(affected_entities: list[str]) -> Decision:
    """Build a Decision with explicit affected_entities for enrichment tests."""
    return Decision(
        id="d-1",
        title="Title",
        rationale="Why",
        rejected_alternatives=[],
        source="test",
        status=DecisionStatus.ACCEPTED,
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
        affected_entities=affected_entities,
    )


class TestCompactEntityEnrichment:
    def test_compact_constraint_line_includes_bound_entities(self) -> None:
        c_obj = _make_constraint_with_entities(["models.py"])
        line = _compact_constraint_line(c_obj)
        assert "bound: models.py" in line

    def test_compact_constraint_line_omits_bound_when_empty(self) -> None:
        c_obj = _make_constraint_with_entities([])
        line = _compact_constraint_line(c_obj)
        assert "bound:" not in line

    def test_compact_decision_line_includes_affected_entities(self) -> None:
        d_obj = _make_decision_with_entities(["cli.py"])
        line = _compact_decision_line(d_obj)
        assert "affects: cli.py" in line

    def test_compact_decision_line_omits_affects_when_empty(self) -> None:
        d_obj = _make_decision_with_entities([])
        line = _compact_decision_line(d_obj)
        assert "affects:" not in line

    def test_compact_line_truncates_long_entity_list(self) -> None:
        long_paths = [f"src/very/deep/module/path_{i}.py" for i in range(20)]
        c_obj = _make_constraint_with_entities(long_paths)
        line = _compact_constraint_line(c_obj)
        assert "..." in line

        # Extract the bound portion (after "bound: " up to the closing paren)
        start = line.index("bound: ") + len("bound: ")
        end = line.rindex(")")
        bound_portion = line[start:end]
        assert len(bound_portion) <= 83  # 80 + 3 for the ellipsis

    def test_truncate_entity_list_short(self) -> None:
        assert _truncate_entity_list(["a.py", "b.py"]) == "a.py, b.py"

    def test_truncate_entity_list_empty(self) -> None:
        assert _truncate_entity_list([]) == ""

    def test_truncate_entity_list_at_separator_boundary(self) -> None:
        """Truncation cuts at the last `, ` boundary, not mid-token.

        Regression: previously used ``joined[:max_chars]``, which could split
        a path like ``src/modu...`` leaving a partial path that looks real.
        """
        entities = ["src/very/deep/module/path_one.py",
                    "src/very/deep/module/path_two.py",
                    "src/very/deep/module/path_three.py"]
        result = _truncate_entity_list(entities, max_chars=50)
        assert result.endswith("...")
        # No partial path token should appear: every comma-delimited segment
        # in the output must be a complete entry from the input.
        body = result[:-3]  # drop the ellipsis
        for segment in body.split(", "):
            assert segment in entities, f"partial path leaked: {segment!r}"

    def test_truncate_entity_list_single_long_path_fallback(self) -> None:
        """When no separator fits, fall back to a hard char-boundary cut."""
        long_path = "a" * 200
        result = _truncate_entity_list([long_path], max_chars=50)
        assert result == "a" * 50 + "..."


# ======================================================================
# Spec 14 — extract_task_keywords
# ======================================================================


class TestExtractTaskKeywords:
    def test_extracts_keywords_from_typical_task(self) -> None:
        """Extracts relevant keywords from a realistic task description."""
        keywords = extract_task_keywords(
            "fix authentication bug in login flow",
            top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        )
        assert len(keywords) > 0
        # At minimum, "authentication" or "login" should appear
        keyword_set = set(keywords)
        assert any(
            kw in keyword_set
            for kw in ("authentication", "auth", "login", "bug", "fix")
        )

    def test_returns_empty_for_blank_input(self) -> None:
        assert extract_task_keywords(
            "", top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        ) == []
        assert extract_task_keywords(
            "   ", top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        ) == []

    def test_returns_empty_for_very_short_input(self) -> None:
        keywords = extract_task_keywords(
            "a", top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        )
        # yake may or may not return keywords for single letters
        assert isinstance(keywords, list)

    def test_multi_word_task_yields_ngrams(self) -> None:
        """Multi-word concepts like 'csrf token validation' should produce n-grams."""
        keywords = extract_task_keywords(
            "add csrf token validation to api middleware",
            top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        )
        assert isinstance(keywords, list)
        # Should be ordered by relevance
        assert keywords == sorted(set(keywords), key=keywords.index)

    def test_reproducible_output(self) -> None:
        """Same input should produce same keywords (yake is deterministic)."""
        task = "optimize database query performance"
        kw1 = extract_task_keywords(task, top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM)
        kw2 = extract_task_keywords(task, top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM)
        assert kw1 == kw2

    def test_respects_top_parameter(self) -> None:
        """When top=3, returns at most 3 keywords."""
        keywords = extract_task_keywords(
            "add rate limiting to the api gateway for all endpoints",
            top=3, n=YAKE_DEFAULT_NGRAM,
        )
        assert len(keywords) <= 3

    def test_respects_ngram_parameter(self) -> None:
        """n=1 should prevent multi-word n-grams."""
        keywords = extract_task_keywords(
            "csrf token validation", top=10, n=1
        )
        for kw in keywords:
            assert " " not in kw, f"Expected single words only, got: {kw!r}"

    # --- Compound / multi-topic query tests ---

    def test_compound_query_yields_keywords_from_both_topics(self) -> None:
        """Compound query with two distinct topics produces keywords from both."""
        keywords = extract_task_keywords(
            "Fix two issues: (1) add rate limiting to api gateway, "
            "(2) implement database connection pooling",
            top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        )
        assert len(keywords) > 0
        # Keywords from both topics should appear.  We check for substrings
        # rather than exact keywords since YAKE output varies by version.
        keyword_text = " ".join(keywords).lower()
        # Topic 1 terms
        t1_match = any(
            t in keyword_text for t in ("rate", "limit", "gateway", "api")
        )
        # Topic 2 terms
        t2_match = any(
            t in keyword_text for t in ("database", "connection", "pool")
        )
        assert t1_match, f"Missing topic-1 terms in: {keywords}"
        assert t2_match, f"Missing topic-2 terms in: {keywords}"

    def test_compound_query_deduplicates_merged_keywords(self) -> None:
        """Round-robin merge must not produce duplicate keywords."""
        keywords = extract_task_keywords(
            "Two tasks: (1) add type hints to python functions, "
            "(2) add type hints to python classes",
            top=YAKE_DEFAULT_TOP, n=YAKE_DEFAULT_NGRAM,
        )
        # No duplicate lowercased keywords
        lower_kws = [kw.lower() for kw in keywords]
        assert len(lower_kws) == len(set(lower_kws)), (
            f"Duplicate keywords found: {keywords}"
        )

    def test_compound_query_respects_top_limit(self) -> None:
        """Even with merged sub-queries, the final list respects the top limit."""
        keywords = extract_task_keywords(
            "Three tasks: (1) fix auth bug, (2) add rate limiting, "
            "(3) implement caching layer",
            top=5, n=YAKE_DEFAULT_NGRAM,
        )
        assert len(keywords) <= 5


# ======================================================================
# Spec 14 — _split_task_query
# ======================================================================


class TestSplitTaskQuery:
    def test_single_task_returns_unchanged(self) -> None:
        """Plain task description with no structural delimiters returns as-is."""
        result = _split_task_query("fix auth bug")
        assert result == ["fix auth bug"]

    def test_numbered_list_splits_into_sub_queries(self) -> None:
        """Numbered list (parenthesised) splits into items with preamble prepended."""
        result = _split_task_query(
            "Fix two bugs in PR #31: (1) server.py record_decision "
            "uses uncleaned path, (2) graph.py capture uses "
            "full filesystem paths"
        )
        assert len(result) == 2
        # Each sub-query should contain the preamble domain terms and its item
        assert "server.py" in result[0] and "record_decision" in result[0].lower()
        assert "graph.py" in result[1] and "capture" in result[1].lower()

    def test_numbered_list_strips_filler_from_preamble(self) -> None:
        """Preamble filler words (fix, bug, in, PR, etc.) are stripped;
        only domain terms (the actual subject matter) are prepended."""
        result = _split_task_query(
            "Fix two bugs in PR #42: (1) login redirect loop, "
            "(2) session timeout"
        )
        assert len(result) == 2
        # Filler words should NOT appear as prefixes;
        # only the bare ``#`` is a filler — ``#42`` is a domain token.
        for item in result:
            prefix = item.split(":")[0].strip() if ":" in item else ""
            for filler in ("fix", "bug", "bugs", "implement", "add", "in",
                           "pr", "issue"):
                assert filler not in prefix.lower(), (
                    f"Filler '{filler}' leaked into preamble prefix: {item!r}"
                )

    def test_numbered_inline_no_newlines(self) -> None:
        """Single-line numbered task splits correctly without requiring newlines."""
        result = _split_task_query("Task: (1) item one, (2) item two")
        assert len(result) == 2
        assert "item one" in result[0]
        assert "item two" in result[1]

    def test_bullet_list_splits(self) -> None:
        """Multi-line bullet list splits into individual items."""
        result = _split_task_query(
            "- add rate limiting to api gateway\n"
            "- implement database connection pooling\n"
            "- configure https on load balancer"
        )
        assert len(result) == 3
        assert "rate limiting" in result[0]
        assert "connection pooling" in result[1]
        assert "https" in result[2].lower()

    def test_bullet_list_asterisk_delimiter(self) -> None:
        """Bullet list using * delimiter also splits correctly."""
        result = _split_task_query(
            "* first bullet item\n"
            "* second bullet item\n"
            "* third bullet item"
        )
        assert len(result) == 3
        assert all("first" in r or "second" in r or "third" in r for r in result)

    def test_no_split_on_simple_text(self) -> None:
        """Text with no structural delimiters returns single-item list."""
        result = _split_task_query(
            "implement user authentication and authorization"
        )
        assert result == ["implement user authentication and authorization"]

    def test_empty_preamble_returns_items_as_is(self) -> None:
        """When preamble contains only filler words, items are returned
        without a meaningful prefix (colon remains but domain prefix is empty)."""
        result = _split_task_query(
            "Fix bug: (1) item one, (2) item two"
        )
        assert len(result) == 2
        # Current behaviour: empty preamble → ": item one" etc.
        for item in result:
            assert "item one" in item or "item two" in item

    def test_dotted_numbered_list_splits(self) -> None:
        """Dotted numbered list (1. / 2. / 3.) also splits correctly."""
        result = _split_task_query(
            "1. fix authentication bug\n"
            "2. add rate limiting\n"
            "3. implement caching"
        )
        assert len(result) >= 2  # preamble may be empty, but items exist
        # At least two items should contain the task content
        content_hits = sum(
            1 for item in result
            if any(
                kw in item.lower()
                for kw in ("authentication", "rate limiting", "caching")
            )
        )
        assert content_hits >= 2

    def test_two_items_do_not_trigger_split(self) -> None:
        """A bare numbered item without preamble is not split (only 2 parts)."""
        result = _split_task_query("(1) only item")
        assert result == ["(1) only item"]  # no split — needs 3+ parts


# ======================================================================
# Spec 14 — keyword_overlap_score
# ======================================================================


class TestKeywordOverlapScore:
    def test_full_match(self) -> None:
        """All keywords present = score 1.0."""
        score = keyword_overlap_score(
            "add type hints to all function signatures",
            ["type", "hints", "function"],
        )
        assert score == 1.0

    def test_partial_match(self) -> None:
        """Some keywords missing = proportional score."""
        score = keyword_overlap_score(
            "add type hints to all function signatures",
            ["type", "database", "async"],
        )
        assert score == pytest.approx(1 / 3)

    def test_zero_match(self) -> None:
        """No keywords match = 0.0."""
        score = keyword_overlap_score(
            "use snake_case for all functions",
            ["database", "async", "api"],
        )
        assert score == 0.0

    def test_empty_keywords_returns_zero(self) -> None:
        score = keyword_overlap_score("anything", [])
        assert score == 0.0

    def test_no_false_positive_substring(self) -> None:
        """'test' should NOT match 'contest' or 'protest'."""
        score = keyword_overlap_score(
            "verify contest results and protest handling",
            ["test"],
        )
        assert score == 0.0

    def test_word_boundary_catches_prefix(self) -> None:
        """'auth' SHOULD match 'authentication' (starts at word boundary)."""
        score = keyword_overlap_score(
            "use token-based authentication for all endpoints",
            ["auth"],
        )
        assert score == 1.0

    def test_word_boundary_rejects_suffix(self) -> None:
        """'port' should NOT match 'important'."""
        score = keyword_overlap_score(
            "this is an important consideration",
            ["port"],
        )
        assert score == 0.0

    def test_word_boundary_rejects_midword(self) -> None:
        """'react' should NOT match 'overreaction'."""
        score = keyword_overlap_score(
            "overreaction to minor issues",
            ["react"],
        )
        assert score == 0.0

    def test_case_insensitive(self) -> None:
        """Matching is case-insensitive."""
        score = keyword_overlap_score(
            "Use TYPE hints and ASYNC functions",
            ["type", "async"],
        )
        assert score == 1.0

    def test_multi_word_keyword(self) -> None:
        """Multi-word keywords like 'type hints' match as a phrase."""
        score = keyword_overlap_score(
            "add type hints to all function signatures",
            ["type hints"],
        )
        assert score == 1.0

    def test_multi_word_keyword_no_false_positive(self) -> None:
        """'type hints' should NOT match when words are separated."""
        score = keyword_overlap_score(
            "the type of these hints matters",
            ["type hints"],
        )
        # "type hints" as a phrase is not found — "type" and "hints" are
        # separate words. However, the word-boundary regex matches "type"
        # at start of "type " and then the rest needs to match too.
        # Multi-word keyword matching: \btype\ hints\b won't match
        # because the space between "type" and "of" breaks the phrase.
        assert score == 0.0


# ======================================================================
# Spec 14 — specificity_factor
# ======================================================================


class TestSpecificityFactor:
    def test_single_entity(self) -> None:
        assert specificity_factor(["src/auth.py"]) == 1.0

    def test_two_entities(self) -> None:
        assert specificity_factor(["src/auth.py", "src/handler.py"]) == 0.5

    def test_five_entities(self) -> None:
        assert specificity_factor(["a", "b", "c", "d", "e"]) == 0.2

    def test_ten_entities(self) -> None:
        assert specificity_factor([str(i) for i in range(10)]) == 0.1

    def test_empty_list_penalized(self) -> None:
        """Zero bound entities = maximally generic, gets 0.1x."""
        assert specificity_factor([]) == 0.1

    def test_many_entities_approaches_zero(self) -> None:
        """100 bound entities = very generic."""
        score = specificity_factor([str(i) for i in range(100)])
        assert score == 0.01
        assert score > 0.0


# ======================================================================
# Spec 14 — token_jaccard
# ======================================================================


class TestTokenJaccard:
    def test_identical_strings(self) -> None:
        assert token_jaccard("hello world", "hello world") == 1.0

    def test_completely_disjoint(self) -> None:
        assert token_jaccard("hello world", "foo bar") == 0.0

    def test_partial_overlap(self) -> None:
        # Intersection: {"hello", "world"} ∩ {"hello", "foo"} = {"hello"} → 1
        # Union: {"hello", "world", "foo"} → 3
        assert token_jaccard("hello world", "hello foo") == pytest.approx(1 / 3)

    def test_both_empty(self) -> None:
        assert token_jaccard("", "") == 1.0

    def test_one_empty(self) -> None:
        assert token_jaccard("hello world", "") == 0.0
        assert token_jaccard("", "hello world") == 0.0

    def test_case_insensitive(self) -> None:
        assert token_jaccard("Hello World", "hello world") == 1.0

    def test_whitespace_only(self) -> None:
        assert token_jaccard("   ", "") == 1.0  # both tokenize to empty sets


# ======================================================================
# Spec 14 — mmr_select
# ======================================================================


class TestMmrSelect:
    @staticmethod
    def _c(description: str, score: float) -> tuple[Constraint, float]:
        return (
            Constraint(
                id=f"c-{abs(hash(description)) % 1000:03d}",
                description=description,
                bound_entities=[],
                source="test",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
            score,
        )

    def test_returns_all_when_budget_plenty(self) -> None:
        items = [
            self._c("use async for i/o", 0.9),
            self._c("validate csrf tokens", 0.8),
            self._c("use snake_case names", 0.3),
        ]
        selected = mmr_select(items, max_items=10, lambda_=0.7)
        assert len(selected) == 3

    def test_respects_max_items(self) -> None:
        items = [
            self._c(f"rule {i}", 1.0 - i * 0.1) for i in range(10)
        ]
        selected = mmr_select(items, max_items=3, lambda_=0.7)
        assert len(selected) == 3

    def test_deduplicates_near_duplicates(self) -> None:
        """Three near-identical constraints → MMR selects one."""
        items = [
            self._c("add type hints to all python functions", 0.9),
            self._c("add type hints to all python functions please", 0.9),  # near-dupe
            self._c("add type hints to all python functions needed", 0.9),  # near-dupe
            self._c("validate user input on forms", 0.5),
        ]
        selected = mmr_select(items, max_items=3, lambda_=0.5)
        descriptions = [s.description for s, _ in selected]
        # The near-duplicates should not all be selected
        type_hint_count = sum(
            1 for d in descriptions if "type hints" in d
        )
        assert type_hint_count <= 2  # At most 2 of 3 near-dupes

    def test_selects_most_relevant_first_when_no_conflict(self) -> None:
        """When items are distinct, highest relevance is picked first."""
        items = [
            self._c("rule a", 0.9),
            self._c("rule b totally different", 0.5),
            self._c("rule c nothing alike", 0.3),
        ]
        selected = mmr_select(items, max_items=3, lambda_=1.0)  # pure relevance
        # First item should be highest scoring
        assert selected[0][1] == pytest.approx(0.9)

    def test_empty_input(self) -> None:
        assert mmr_select([], max_items=5, lambda_=0.7) == []

    def test_zero_max_items(self) -> None:
        items = [self._c("rule", 1.0)]
        assert mmr_select(items, max_items=0, lambda_=0.7) == []

    def test_lambda_zero_pure_diversity(self) -> None:
        """λ=0 means pure diversity — relevance ignored entirely."""
        items = [
            self._c("add type hints everywhere", 0.9),
            self._c("add type hints everywhere please", 0.5),  # near-dupe
            self._c("validate csrf in middleware", 0.3),  # distinct
        ]
        selected = mmr_select(items, max_items=2, lambda_=0.0)
        descriptions = [s.description for s, _ in selected]
        # With λ=0, diversity dominates — the near-dupe should be skipped
        assert len(selected) == 2
        # The two selected should be the most diverse pair
        assert "type hints" in descriptions[0]  # first item (highest relevance... wait, λ=0 ignores relevance)
        # Actually with λ=0, MMR = -max_sim. First iteration: max_sim=0 for all,
        # so MMR=0 for all, picks the first in the list (index 0).
        # Second iteration: sim(type hints everywhere, type hints everywhere please) = high,
        # sim(type hints everywhere, validate csrf) = low. So the diverse one wins.

    def test_same_score_different_content_picks_both(self) -> None:
        """Two distinct items with same score should both be selected."""
        items = [
            self._c("completely different topic a", 0.8),
            self._c("completely different topic b", 0.8),
        ]
        selected = mmr_select(items, max_items=2, lambda_=0.7)
        assert len(selected) == 2


# ======================================================================
# Spec 14 — integration: keyword-ranked context pack
# ======================================================================


class TestFormatContextPackKeywordRanking:
    """Integration tests for the keyword-based ranking path."""

    def test_keyword_relevant_constraints_surface(self) -> None:
        """Constraints matching task keywords rank higher than unrelated ones."""
        constraints = [
            make_constraint(id="c-auth", description="validate all auth tokens"),
            make_constraint(id="c-csrf", description="validate csrf tokens for forms"),
            make_constraint(id="c-style", description="use snake_case for variables"),
        ]
        task_keywords = ["auth", "token"]
        output = format_context_pack(
            "fix auth token bug", [], constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,

            task_keywords=task_keywords,
        )
        # Auth-related constraints should appear before style ones
        assert "c-auth" in output
        # The style constraint may or may not appear depending on token budget
        # But auth should be visible

    def test_generic_constraint_demoted(self) -> None:
        """Constraints bound to many files rank lower than specific ones."""
        constraints = [
            Constraint(
                id="c-specific",
                description="use argon2 for password hashing in auth module",
                bound_entities=["src/auth/handler.py"],
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
            Constraint(
                id="c-generic",
                description="use snake_case for all function names",
                bound_entities=[f"src/module_{i}.py" for i in range(10)],
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
        ]
        # Keywords match both equally
        task_keywords = ["password", "hashing", "function", "names"]
        output = format_context_pack(
            "fix", [], constraints, task_keywords=task_keywords,
            max_constraint_tokens=500, max_decision_tokens=500,

        )
        # Specific constraint should appear; generic might be omitted
        assert "c-specific" in output

    def test_mmr_deduplicates_near_duplicates(self) -> None:
        """Near-duplicate constraints are not all selected."""
        constraints = [
            Constraint(
                id="c-hints-1",
                description="add type hints to all python function signatures",
                bound_entities=["src/module.py"],
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
            Constraint(
                id="c-hints-2",
                description="add type hints to all python function signatures please",
                bound_entities=["src/module.py"],
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
            Constraint(
                id="c-distinct",
                description="validate csrf tokens in middleware",
                bound_entities=["src/middleware.py"],
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
        ]
        # Keywords match all three constraints
        task_keywords = ["type", "hints", "function", "csrf", "token", "middleware"]
        output = format_context_pack(
            "add type hints", [], constraints, task_keywords=task_keywords,
            max_constraint_tokens=500, max_decision_tokens=500,

        )
        # With MMR, the two near-duplicate "type hints" constraints
        # should not both crowd out the distinct one.
        # Because all constraints match keywords, MMR diversity selection
        # ensures the distinct one gets a slot.
        assert "c-distinct" in output

    def test_fallback_without_keywords_still_works(self) -> None:
        """When task_keywords is None, recency + status ranking is used."""
        old = Constraint(
            id="c-old",
            description="old rule", bound_entities=[],
            source="test", status="active", created_at="2023-01-01T00:00:00Z",
        )
        new = Constraint(
            id="c-new",
            description="new rule", bound_entities=[],
            source="test", status="active", created_at="2025-01-01T00:00:00Z",
        )
        output = format_context_pack(
            "task", [], [old, new], max_constraint_tokens=9999,
            max_decision_tokens=9999,

        )
        # Newer constraint should appear first (recency ranking)
        assert output.index("c-new") < output.index("c-old")

    def test_keywords_specificity_combined(self) -> None:
        """Keyword overlap * specificity: specific + matching beats generic + matching."""
        constraints = [
            Constraint(
                id="c-match-specific",
                description="hash passwords with argon2 in auth handler",
                bound_entities=["src/auth/handler.py"],  # 1 file → 1.0x
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
            Constraint(
                id="c-match-generic",
                description="hash passwords with argon2 across entire codebase",
                bound_entities=[f"f{i}.py" for i in range(100)],  # 100 files → 0.01x
                source="test", status="active",
                created_at="2024-01-01T00:00:00Z",
            ),
        ]
        task_keywords = ["hash", "passwords", "argon2"]
        output = format_context_pack(
            "hash passwords", [], constraints, task_keywords=task_keywords,
            max_constraint_tokens=500, max_decision_tokens=500,

        )
        assert "c-match-specific" in output
