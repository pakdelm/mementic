from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from mementic.context import (
    CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
    CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
    DEAD_PATH_GRACE_HOURS,
    FIELD_WEIGHT_DESCRIPTION,
    FIELD_WEIGHT_RATIONALE,
    FIELD_WEIGHT_TITLE,
    ContextPackResult,
    build_context_pack,
    document_frequencies,
    drop_dead_path_entries,
    format_evolution,
    format_file_history_json,
    idf_weights,
    keyword_idf_score,
    rank_and_truncate,
)
from mementic.context.builder import (
    _compact_constraint_line,
    _compact_decision_line,
)
from mementic.context.render import _compact_evolution_line
from mementic.models import Constraint, Decision
from mementic.schema import utc_now
from mementic.text import estimate_tokens, slugify, truncate_entity_list


def build_pack(*args: Any, **kwargs: Any) -> ContextPackResult:
    """Build a pack anchored at the repository root."""
    kwargs.setdefault("base_dir", Path(__file__).resolve().parent.parent)
    return build_context_pack(*args, **kwargs)


def _render_pack(*args: Any, **kwargs: Any) -> str:
    """Render a context pack body through the canonical builder."""
    return build_pack(*args, **kwargs).text


def make_decision(
    id: str = "d-001",
    title: str = "Use async",
    rationale: str = "Better performance",
    ts: str = "2024-01-01T00:00:00Z",
    paths: list[str] | None = None,
) -> Decision:
    return Decision(
        id=id,
        title=title,
        rationale=rationale,
        created_at=ts,
        paths=paths or [],
    )


def make_constraint(
    id: str = "c-001",
    description: str = "All I/O must be async",
    enforced_by: str = "d-001",
    ts: str = "2024-01-01T00:00:00Z",
    paths: list[str] | None = None,
) -> Constraint:
    return Constraint(
        id=id,
        description=description,
        enforced_by=enforced_by,
        created_at=ts,
        paths=paths or [],
    )


def d(d: Decision, score: int = 1) -> tuple[Decision, int]:
    """Wrap a Decision with overlap score."""
    return (d, score)


def c(c: Constraint, score: int = 1) -> tuple[Constraint, int]:
    """Wrap a Constraint with overlap score."""
    return (c, score)


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
        tokens_a = estimate_tokens(line_a)
        line_b = _compact_constraint_line(items[1][0])
        tokens_b = estimate_tokens(line_b)
        budget = tokens_a + (tokens_b // 2)  # fits first, not second
        selected, shown, _ = rank_and_truncate(
            items, max_tokens=budget, line_formatter=_compact_constraint_line
        )
        assert shown == 1
        assert selected[0].id == "c-a"

    def test_empty_items(self) -> None:
        items: list[tuple[Constraint, int]] = []
        selected, shown, total = rank_and_truncate(
            items, max_tokens=1000, line_formatter=_compact_constraint_line
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


class TestFormatContextPack:
    def test_produces_expected_structure(self) -> None:
        decisions = [make_decision()]
        constraints = [make_constraint()]

        output = _render_pack(
            decisions,
            constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )

        assert output.startswith("## Architectural Context")
        assert "### Constraints (MUST follow)" in output
        assert "- All I/O must be async (#c-001)" in output
        assert "### Relevant Decisions" in output
        assert "- Better performance (#d-001)" in output

    def test_with_empty_lists(self) -> None:
        output: str = _render_pack(
            [],
            [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )

        assert output.startswith("## Architectural Context")
        assert "### Constraints (MUST follow)" in output
        assert "_None_" in output
        assert "### Relevant Decisions" in output
        assert "_None_" in output

    def test_with_only_decisions(self) -> None:
        decisions = [make_decision(title="Use pydantic")]
        output = _render_pack(
            decisions,
            [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )

        assert "### Constraints (MUST follow)" in output
        assert "_None_" in output  # No constraints
        assert "### Relevant Decisions" in output
        assert "- Better performance (#d-001)" in output

    def test_with_only_constraints(self) -> None:
        constraints = [make_constraint(description="Must use type hints")]
        output = _render_pack(
            [],
            constraints,
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
        output = _render_pack(
            [],
            constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )

        assert "[C]" not in output
        assert "- Must use double quotes (#c-001)" in output

    def test_compact_format_no_enforced_by(self) -> None:
        """Compact format drops (enforced by: X)."""
        constraints = [make_constraint(description="A test rule", enforced_by="dec-42")]
        output = _render_pack(
            [],
            constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )

        assert "enforced by" not in output
        assert "- A test rule (#c-001)" in output

    def test_compact_format_no_decision_title_date(self) -> None:
        """Compact decision format drops title and date."""
        decisions = [make_decision(title="Pick DB", rationale="Chose PostgreSQL")]
        output = _render_pack(
            decisions,
            [],
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )

        assert "Pick DB" not in output
        assert "2024-01-01" not in output
        assert "- Chose PostgreSQL (#d-001)" in output

    def test_multiple_items(self) -> None:
        decisions = [
            make_decision(id="d-1", rationale="First decision"),
            make_decision(id="d-2", rationale="Second decision"),
        ]
        constraints = [
            make_constraint(id="c-1", description="Constraint 1"),
            make_constraint(id="c-2", description="Constraint 2"),
        ]
        output = _render_pack(
            decisions,
            constraints,
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
            make_constraint(id=f"c-{i}", description=f"Constraint {i}") for i in range(1, 20)
        ]
        output = _render_pack(
            [],
            constraints,
            max_constraint_tokens=60,
            max_decision_tokens=9999,
        )
        assert "more constraints omitted (token budget)" in output

    def test_omission_note_decisions(self) -> None:
        """When decisions are truncated, an omission note is shown."""
        decisions = [make_decision(id=f"d-{i}", rationale=f"Rationale {i}") for i in range(1, 20)]
        output = _render_pack(
            decisions,
            [],
            max_constraint_tokens=9999,
            max_decision_tokens=60,
        )
        assert "more decisions omitted (token budget)" in output

    def test_no_omission_note_when_all_fit(self) -> None:
        """No omission note when all items fit within the token budget."""
        constraints = [make_constraint(id="c-1", description="Only one")]
        output = _render_pack(
            [],
            constraints,
            max_constraint_tokens=9999,
            max_decision_tokens=9999,
        )
        assert "more constraints omitted" not in output

    def test_omission_note_when_all_omitted(self) -> None:
        """When ALL items are omitted due to tiny budget, italic note is shown."""
        constraints = [make_constraint(id="c-1", description="A somewhat long description")]
        output = _render_pack(
            [],
            constraints,
            max_constraint_tokens=1,  # impossibly small
            max_decision_tokens=9999,
        )
        assert "_... and 1 constraints omitted (token budget)._" in output

    def test_token_budget_constraints_consume_first(self) -> None:
        """Constraints are filled first; decisions get remaining budget."""
        # Many constraints consume most of the budget
        constraints = [
            make_constraint(id=f"c-{i}", description=f"Constraint {i}") for i in range(1, 10)
        ]
        decisions = [make_decision(id=f"d-{i}", rationale=f"Rationale {i}") for i in range(1, 10)]
        output = _render_pack(
            decisions,
            constraints,
            max_constraint_tokens=100,
            max_decision_tokens=100,
        )

        # Some constraints should be shown
        assert "Constraint" in output
        # Decisions may or may not appear depending on remaining budget
        # At minimum the sections exist
        assert "### Relevant Decisions" in output


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
            d(
                make_decision(
                    id="d-1",
                    title="Use Pydantic",
                    rationale="Strong validation",
                    ts="2025-06-01T12:00:00Z",
                )
            )
        ]
        output = format_evolution("test.py", decisions)

        assert "[2025-06-01T12:00:00Z] Use Pydantic" in output

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
            d(make_decision(id=f"d-{i}", rationale=f"Rationale {i}"), 1) for i in range(1, 20)
        ]
        output = format_evolution("test.py", decisions, max_tokens=60)

        assert "more decisions omitted (token budget)" in output

    def test_tiny_budget_reports_omission_not_missing_history(self) -> None:
        """An over-budget decision is omitted, not reported as nonexistent."""
        decisions = [d(make_decision(id="d-1", rationale="A very long rationale line"))]
        output = format_evolution("test.py", decisions, max_tokens=1)

        assert "No architectural decisions found" not in output
        assert "more decisions omitted (token budget)" in output

    def test_no_omission_note_when_all_fit(self) -> None:
        """No omission note when all decisions fit within budget."""
        decisions = [d(make_decision(id="d-1", rationale="Short"), 1)]
        output = format_evolution("test.py", decisions, max_tokens=9999)

        assert "more decisions omitted" not in output


class TestFormatFileHistoryJson:
    def test_preserves_caller_decision_order(self) -> None:
        """Decisions serialize in the order the store returned them."""
        decisions = [
            d(make_decision(id="d-old", ts="2023-01-01T00:00:00Z"), 3),
            d(make_decision(id="d-new", ts="2025-01-01T00:00:00Z"), 1),
        ]

        data = format_file_history_json("src/foo.py", decisions, [])

        assert [entry["id"] for entry in data["decisions"]] == ["d-old", "d-new"]
        assert data["file"] == "src/foo.py"


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
        assert line == ("- [2024-06-01T00:00:00Z] Use Pydantic: Strong validation (#d-1)")

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
    ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+(?:\+\d{2}:\d{2}|Z)$")

    def test_returns_iso8601_format(self) -> None:
        ts = utc_now()
        assert self.ISO_8601_RE.match(ts), f"{ts!r} does not match ISO 8601"

    def test_returns_utc_timezone(self) -> None:
        ts = utc_now()
        assert ts.endswith("+00:00") or ts.endswith("Z")

    def test_two_calls_structure_identical(self) -> None:
        ts1 = utc_now()
        ts2 = utc_now()
        assert len(ts1) == len(ts2)
        assert self.ISO_8601_RE.match(ts1)
        assert self.ISO_8601_RE.match(ts2)


class TestCountTokens:
    """Tests for estimate_tokens()."""

    def test_returns_integer(self) -> None:
        """estimate_tokens returns an int."""
        result = estimate_tokens("hello world")
        assert isinstance(result, int)

    def test_short_text(self) -> None:
        """A short text returns a positive token count."""
        result = estimate_tokens("hello")
        assert result > 0

    def test_empty_string(self) -> None:
        """An empty string returns 0 tokens."""
        result = estimate_tokens("")
        assert result == 0

    def test_longer_text(self) -> None:
        """A 100-character string returns a reasonable estimate."""
        result = estimate_tokens("a" * 100)
        # len//4 heuristic: 25 tokens.
        assert result == 25

    def test_exact_quotient(self) -> None:
        """estimate_tokens uses the len(text) // 4 estimate exactly."""
        assert estimate_tokens("hello world foo") == len("hello world foo") // 4


def _make_constraint_with_entities(paths: list[str]) -> Constraint:
    """Build a Constraint with explicit bound paths for enrichment tests."""
    return Constraint(
        id="c-1",
        description="Hard rule",
        enforced_by="d-1",
        created_at="2024-01-01T00:00:00Z",
        paths=paths,
    )


def _make_decision_with_entities(paths: list[str]) -> Decision:
    """Build a Decision with explicit affected paths for enrichment tests."""
    return Decision(
        id="d-1",
        title="Title",
        rationale="Why",
        created_at="2024-01-01T00:00:00Z",
        paths=paths,
    )


class TestCompactEntityEnrichment:
    def test_compact_constraint_line_includes_bound_paths(self) -> None:
        c_obj = _make_constraint_with_entities(["models.py"])
        line = _compact_constraint_line(c_obj)
        assert "bound: models.py" in line

    def test_compact_constraint_line_omits_bound_when_empty(self) -> None:
        c_obj = _make_constraint_with_entities([])
        line = _compact_constraint_line(c_obj)
        assert "bound:" not in line

    def test_compact_decision_line_includes_affected_paths(self) -> None:
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
        assert truncate_entity_list(["a.py", "b.py"]) == "a.py, b.py"

    def test_truncate_entity_list_empty(self) -> None:
        assert truncate_entity_list([]) == ""

    def test_truncate_entity_list_at_separator_boundary(self) -> None:
        """Truncation cuts at the last `, ` boundary, not mid-token.

        Regression: previously used ``joined[:max_chars]``, which could split
        a path like ``src/modu...`` leaving a partial path that looks real.
        """
        entities = [
            "src/very/deep/module/path_one.py",
            "src/very/deep/module/path_two.py",
            "src/very/deep/module/path_three.py",
        ]
        result = truncate_entity_list(entities, max_chars=50)
        assert result.endswith("...")
        # No partial path token should appear: every comma-delimited segment
        # in the output must be a complete entry from the input.
        body = result[:-3]  # drop the ellipsis
        for segment in body.split(", "):
            assert segment in entities, f"partial path leaked: {segment!r}"

    def test_truncate_entity_list_single_long_path_fallback(self) -> None:
        """When no separator fits, fall back to a hard char-boundary cut."""
        long_path = "a" * 200
        result = truncate_entity_list([long_path], max_chars=50)
        assert result == "a" * 50 + "..."


def _corpus() -> list[Constraint | Decision]:
    """Small mixed corpus used to compute document frequencies."""
    return [
        make_constraint(id="c-argon", description="hash passwords with argon2"),
        make_constraint(id="c-api", description="api endpoints validate api input"),
        make_decision(
            id="d-argon-title", title="argon2 adoption", rationale="unrelated rationale text"
        ),
    ]


class TestDocumentFrequencies:
    def test_counts_entries_matching_any_field(self) -> None:
        corpus = _corpus()
        df = document_frequencies(corpus, ["argon2"])
        # c-argon (description) and d-argon-title (title) both contain it.
        assert df == {"argon2": 2}

    def test_entry_counts_once_across_fields(self) -> None:
        entry = make_decision(
            id="d-dup",
            title="argon2 hashing",
            rationale="argon2 again",
        )
        df = document_frequencies([entry], ["argon2"])
        assert df["argon2"] == 1

    def test_unmatched_keyword_has_zero_df(self) -> None:
        df = document_frequencies(_corpus(), ["zzz-nothing"])
        assert df["zzz-nothing"] == 0


class TestIdfWeights:
    def test_rare_keyword_outweighs_common(self) -> None:
        idf = idf_weights(10, {"rare": 1, "common": 10})
        assert idf["rare"] > idf["common"]

    def test_idf_is_strictly_positive(self) -> None:
        """Every keyword in a non-empty corpus gets a positive weight."""
        idf = idf_weights(10, {"unseen": 0, "rare": 1, "everywhere": 10})
        assert all(w > 0.0 for w in idf.values())

    def test_ubiquitous_keyword_scores_ln2(self) -> None:
        """Smoothed IDF: a keyword in every document stays positive and
        approaches ln(2) (exact value ln(1 + N/(N+1)) at df == N)."""
        import math

        idf = idf_weights(10, {"everywhere": 10})
        assert idf["everywhere"] == pytest.approx(math.log(1 + 10 / 11))
        assert idf["everywhere"] > 0.6  # near ln(2) ~= 0.693, never 0.0

    def test_zero_df_gets_max_idf(self) -> None:
        """df=0 scores the maximum ln(1 + N), above the df=1 weight."""
        import math

        idf = idf_weights(5, {"unseen": 0, "seen_once": 1})
        assert idf["unseen"] == pytest.approx(math.log(6))
        assert idf["unseen"] > idf["seen_once"]

    def test_empty_corpus_yields_zero_weights(self) -> None:
        idf = idf_weights(0, {"kw": 1})
        assert idf["kw"] == 0.0


class TestKeywordIdfScore:
    """Match semantics ported from the former keyword_overlap_score."""

    def _score(self, entry: Constraint | Decision, kws: list[str]) -> float:
        corpus = [*_corpus(), entry]
        idf = idf_weights(len(corpus), document_frequencies(corpus, kws))
        return keyword_idf_score(entry, kws, idf)

    def test_no_match_scores_zero(self) -> None:
        entry = make_constraint(id="c-none", description="totally unrelated words")
        assert self._score(entry, ["argon2"]) == 0.0

    def test_empty_keywords_score_zero(self) -> None:
        entry = make_constraint(id="c-any", description="anything")
        assert self._score(entry, []) == 0.0

    def test_no_false_positive_substring(self) -> None:
        """'test' should NOT match 'contest' or 'protest'."""
        entry = make_constraint(
            id="c-contest",
            description="verify contest results and protest handling",
        )
        assert self._score(entry, ["test"]) == 0.0

    def test_word_boundary_catches_prefix(self) -> None:
        """'auth' SHOULD match 'authentication' (starts at word boundary)."""
        entry = make_constraint(
            id="c-auth",
            description="use token-based authentication for endpoints",
        )
        assert self._score(entry, ["auth"]) > 0.0

    def test_word_boundary_rejects_midword(self) -> None:
        entry = make_constraint(id="c-react", description="overreaction to minor issues")
        assert self._score(entry, ["react"]) == 0.0

    def test_case_insensitive(self) -> None:
        entry = make_constraint(
            id="c-case",
            description="Use TYPE hints and ASYNC functions",
        )
        assert self._score(entry, ["type", "async"]) > 0.0

    def test_multi_word_keyword_matches_phrase(self) -> None:
        entry = make_constraint(
            id="c-phrase",
            description="add type hints to all function signatures",
        )
        assert self._score(entry, ["type hints"]) > 0.0

    def test_multi_word_keyword_no_false_positive(self) -> None:
        entry = make_constraint(
            id="c-split",
            description="the type of these hints matters",
        )
        assert self._score(entry, ["type hints"]) == 0.0

    def test_title_match_beats_rationale_match(self) -> None:
        """Field weighting: title (3.0) outweighs rationale (1.0)."""
        title_hit = make_decision(
            id="d-title",
            title="argon2 everywhere",
            rationale="nothing relevant",
        )
        rationale_hit = make_decision(
            id="d-rationale",
            title="boring title",
            rationale="we adopted argon2",
        )
        assert self._score(title_hit, ["argon2"]) == pytest.approx(
            3.0 * self._score(rationale_hit, ["argon2"])
        )

    def test_highest_field_weight_counted_once(self) -> None:
        """A keyword in several fields contributes its best weight once."""
        both = make_decision(
            id="d-both",
            title="argon2 here",
            rationale="argon2 there",
        )
        title_only = make_decision(
            id="d-one",
            title="argon2 here",
            rationale="nothing relevant",
        )
        assert self._score(both, ["argon2"]) == self._score(title_only, ["argon2"])

    def test_description_weight_between_title_and_rationale(self) -> None:
        """Constraint description carries weight 2.0."""
        desc_hit = make_constraint(
            id="c-desc",
            description="argon2 required for passwords",
        )
        corpus = [*_corpus(), desc_hit]
        kws = ["argon2"]
        idf = idf_weights(len(corpus), document_frequencies(corpus, kws))
        expected = FIELD_WEIGHT_DESCRIPTION * idf["argon2"] / len(kws)
        assert keyword_idf_score(desc_hit, kws, idf) == pytest.approx(expected)

    def test_field_weight_constants_ordering(self) -> None:
        assert FIELD_WEIGHT_TITLE > FIELD_WEIGHT_DESCRIPTION > FIELD_WEIGHT_RATIONALE
        assert (FIELD_WEIGHT_TITLE, FIELD_WEIGHT_DESCRIPTION, FIELD_WEIGHT_RATIONALE) == (
            3.0,
            2.0,
            1.0,
        )

    def test_query_length_normalization(self) -> None:
        """Doubling the query with a non-matching keyword halves the score."""
        entry = make_constraint(id="c-norm", description="argon2 for passwords")
        one_kw = self._score(entry, ["argon2"])
        two_kw = self._score(entry, ["argon2", "zzz-unmatched"])
        assert two_kw == pytest.approx(one_kw / 2)


class TestIdfOrderingProperties:
    @staticmethod
    def _id_from_line(line: str) -> str:
        """Extract the entry ID from a compact line's ``(#<id>...)`` marker."""
        tail = line.split("(#", 1)[1]
        return tail.split(",", 1)[0].split(")", 1)[0]

    def _pack_lines(self, constraints: list[Constraint], kws: list[str]) -> list[str]:
        result = build_pack(
            [],
            constraints,
            max_constraint_tokens=9999,
            max_decision_tokens=9999,
            keywords=kws,
        )
        return [ln for ln in result.text.splitlines() if ln.startswith("- ") and "(#" in ln]

    def _pack_ids(self, constraints: list[Constraint], kws: list[str]) -> list[str]:
        return [self._id_from_line(ln) for ln in self._pack_lines(constraints, kws)]

    def test_rare_keyword_beats_common_keyword_match(self) -> None:
        """The entry matching only the rarer keyword ranks first."""
        rare = make_constraint(id="c-rare", description="argon2 hashing for secrets")
        commons = [
            make_constraint(id=f"c-common-{i}", description=f"api contract {i}") for i in range(3)
        ]
        ids = self._pack_ids([*commons, rare], ["argon2", "api"])
        # df(argon2)=1 vs df(api)=3 over this corpus: rare match dominates.
        assert ids[0] == "c-rare"

    def test_deterministic_tie_break_recency_then_id(self) -> None:
        """Equal scores order by created_at desc, then id asc."""
        distractor = make_constraint(
            id="c-zzz",
            description="an unrelated filler topic",
        )
        a = make_constraint(id="c-a", description="argon2 rule", ts="2024-01-01T00:00:00Z")
        b = make_constraint(id="c-b", description="argon2 rule", ts="2025-01-01T00:00:00Z")
        a2 = make_constraint(id="c-a2", description="argon2 rule", ts="2024-01-01T00:00:00Z")
        ids = self._pack_ids([a2, a, b, distractor], ["argon2"])
        # The distractor keeps df(argon2) < N so matches score above zero,
        # but it is itself gated out of the rendered lines.
        assert ids == ["c-b", "c-a", "c-a2"]

    def test_all_keywords_ubiquitous_pack_stays_nonempty(self) -> None:
        """Regression: when EVERY query keyword appears in EVERY live entry,
        smoothed IDF keeps all scores positive, so the pack is non-empty and
        the header honestly reports matches instead of the old lie
        ("0 of N matched your keywords" over an allegedly-matching corpus).
        """
        constraints = [
            make_constraint(id=f"c-{i}", description=f"api rule number {i}") for i in range(3)
        ]
        result = build_pack(
            [],
            constraints,
            max_constraint_tokens=9999,
            max_decision_tokens=9999,
            keywords=["api"],
        )
        assert result.stats.matched_constraints == 3
        assert result.stats.shown_constraints >= min(3, len(constraints))
        assert "#c-0" in result.text
        assert "- shown 3 of 3 matched" in result.text
        assert "matched your keywords" not in result.text

    def test_gate_excludes_zero_score_entries(self) -> None:
        matching = make_constraint(id="c-hit", description="argon2 hashing")
        other = make_constraint(id="c-miss", description="completely different topic")
        result = build_pack(
            [],
            [matching, other],
            9999,
            9999,
            keywords=["argon2"],
        )
        assert result.stats.matched_constraints == 1
        assert "#c-miss" not in result.text


class TestFormatContextPackKeywordRanking:
    """Integration tests for the IDF-weighted keyword path (Spec 30)."""

    def test_keyword_relevant_constraints_surface(self) -> None:
        """Constraints matching task keywords rank higher than unrelated ones."""
        constraints = [
            make_constraint(id="c-auth", description="validate all auth tokens"),
            make_constraint(id="c-csrf", description="validate csrf tokens for forms"),
            make_constraint(id="c-style", description="use snake_case for variables"),
        ]
        task_keywords = ["auth", "token"]
        output = _render_pack(
            [],
            constraints,
            max_constraint_tokens=CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            max_decision_tokens=CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
            keywords=task_keywords,
        )
        assert output.index("c-auth") < output.index("c-csrf")
        assert "#c-style" not in output

    def test_distinct_constraint_not_crowded_out(self) -> None:
        """With IDF scoring, a distinct constraint outranks near-duplicates."""
        constraints = [
            make_constraint(
                id="c-hints-1",
                description="add type hints to all python function signatures",
            ),
            make_constraint(
                id="c-hints-2",
                description="add type hints to all python function signatures please",
            ),
            make_constraint(
                id="c-distinct",
                description="validate csrf tokens in middleware",
            ),
        ]
        task_keywords = ["type", "hints", "function", "csrf", "token", "middleware"]
        output = _render_pack(
            [],
            constraints,
            keywords=task_keywords,
            max_constraint_tokens=500,
            max_decision_tokens=500,
        )
        # The distinct constraint matches three rarer keywords and outranks
        # the near-duplicate pair; the 500-token budget fits all three.
        assert output.index("c-distinct") < output.index("c-hints-1")

    def test_fallback_without_keywords_still_works(self) -> None:
        """When task_keywords is None, recency ranking is used."""
        old = make_constraint(
            id="c-old",
            description="old rule",
            ts="2023-01-01T00:00:00Z",
        )
        new = make_constraint(
            id="c-new",
            description="new rule",
            ts="2025-01-01T00:00:00Z",
        )
        output = _render_pack(
            [],
            [old, new],
            max_constraint_tokens=9999,
            max_decision_tokens=9999,
        )
        # Newer constraint should appear first (recency ranking)
        assert output.index("c-new") < output.index("c-old")


class TestDropDeadPathEntries:
    def _always_missing(self, path: str) -> bool:
        return False

    def _always_present(self, path: str) -> bool:
        return True

    def test_ghost_bound_entry_dropped(self) -> None:
        """Every bound path missing + past grace window -> dropped."""
        ghost = make_constraint(
            id="c-ghost",
            description="old rule on a deleted file",
            ts="2020-01-01T00:00:00Z",
            paths=["mementic/graph.py"],
        )
        kept = drop_dead_path_entries(
            [ghost],
            path_exists=self._always_missing,
            now="2026-01-01T00:00:00+00:00",
        )
        assert kept == []

    def test_fresh_entry_with_uncommitted_path_survives(self) -> None:
        """Grace guard: entries younger than 24h are exempt."""
        fresh_ts = utc_now()
        fresh = make_constraint(
            id="c-fresh",
            description="brand new rule",
            ts=fresh_ts,
            paths=["src/not_committed_yet.py"],
        )
        kept = drop_dead_path_entries(
            [fresh],
            path_exists=self._always_missing,
            now=fresh_ts,
        )
        assert kept == [fresh]

    def test_entry_with_live_path_survives(self) -> None:
        mixed = make_decision(
            id="d-mixed",
            ts="2020-01-01T00:00:00Z",
            paths=["mementic/graph.py", "mementic/format.py"],
        )
        kept = drop_dead_path_entries(
            [mixed],
            path_exists=lambda p: p == "mementic/format.py",
            now="2026-01-01T00:00:00+00:00",
        )
        assert kept == [mixed]

    def test_entry_without_paths_never_dropped(self) -> None:
        unbound = make_decision(
            id="d-unbound",
            ts="2020-01-01T00:00:00Z",
            paths=[],
        )
        kept = drop_dead_path_entries(
            [unbound],
            path_exists=self._always_missing,
            now="2026-01-01T00:00:00+00:00",
        )
        assert kept == [unbound]

    def test_grace_window_boundary_uses_store_clock_constant(self) -> None:
        """An entry just inside the cutoff survives; just outside is dropped."""
        now = datetime.now(timezone.utc)
        inside = make_constraint(
            id="c-inside",
            ts=(now - timedelta(hours=DEAD_PATH_GRACE_HOURS - 1)).isoformat(),
            paths=["gone.py"],
        )
        outside = make_constraint(
            id="c-outside",
            ts=(now - timedelta(hours=DEAD_PATH_GRACE_HOURS + 1)).isoformat(),
            paths=["gone.py"],
        )
        kept = drop_dead_path_entries(
            [inside, outside],
            path_exists=self._always_missing,
            now=now.isoformat(),
        )
        assert [e.id for e in kept] == ["c-inside"]

    def test_unparseable_created_at_fails_open(self) -> None:
        """Garbage created_at counts as inside the grace window (fail open).

        Fresh-work protection is the point of the guard: a malformed stamp
        must never cause a drop.
        """
        garbage = make_constraint(
            id="c-garbage",
            ts="not-a-timestamp",
            paths=["deleted/gone.py"],
        )
        kept = drop_dead_path_entries(
            [garbage],
            path_exists=self._always_missing,
            now="2026-01-01T00:00:00+00:00",
        )
        assert kept == [garbage]

    def test_defaults_to_real_filesystem_checks(self) -> None:
        """With a base_dir anchor, Path.exists decides."""
        existing = make_constraint(
            id="c-real",
            ts="2020-01-01T00:00:00Z",
            paths=["pyproject.toml"],
        )
        missing = make_constraint(
            id="c-missing",
            ts="2020-01-01T00:00:00Z",
            paths=["no/such/file_really.py"],
        )
        repo_root = Path(__file__).resolve().parent.parent
        kept = drop_dead_path_entries([existing, missing], base_dir=repo_root)
        assert [e.id for e in kept] == ["c-real"]


class TestBaseDirPathResolution:
    """Repo-relative bound paths must resolve against base_dir, not CWD."""

    def test_relative_paths_resolve_against_base_dir_not_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "foo.py").write_text("x = 1", encoding="utf-8")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)  # process CWD deliberately lacks the file
        entry = make_constraint(
            id="c-bound",
            ts="2020-01-01T00:00:00Z",
            paths=["src/foo.py"],
        )
        kept = drop_dead_path_entries(
            [entry],
            base_dir=repo,
            now="2026-01-01T00:00:00+00:00",
        )
        assert kept == [entry]

    def test_missing_liveness_anchor_is_rejected(self) -> None:
        entry = make_constraint(
            id="c-bound",
            ts="2020-01-01T00:00:00Z",
            paths=["src/foo.py"],
        )
        with pytest.raises(ValueError, match="path_exists or base_dir"):
            drop_dead_path_entries([entry])

    def test_absolute_bound_paths_bypass_base_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        absolute_file = tmp_path / "abs.py"
        absolute_file.write_text("x = 1", encoding="utf-8")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        entry = make_constraint(
            id="c-abs",
            ts="2020-01-01T00:00:00Z",
            paths=[str(absolute_file)],
        )
        kept = drop_dead_path_entries(
            [entry],
            base_dir=tmp_path / "no-such-repo",
            now="2026-01-01T00:00:00+00:00",
        )
        assert kept == [entry]


class TestDeadPathDropInPacks:
    def test_ghost_bound_entry_excluded_from_pack(self) -> None:
        ghost = make_constraint(
            id="c-ghost",
            description="argon2 rule on a deleted file",
            ts="2020-01-01T00:00:00Z",
            paths=["deleted/ghost.py"],
        )
        result = build_pack(
            [],
            [ghost],
            9999,
            9999,
            keywords=["argon2"],
            path_exists=lambda p: False,
        )
        assert result.stats.matched_constraints == 0
        assert "#c-ghost" not in result.text

    def test_ghost_paths_do_not_count_toward_idf_corpus(self) -> None:
        """Dropped ghosts are excluded before document frequencies."""
        live = make_constraint(
            id="c-live",
            description="argon2 hashing",
            ts="2020-01-01T00:00:00Z",
        )
        other = make_constraint(
            id="c-other",
            description="unrelated filler topic",
            ts="2020-01-01T00:00:00Z",
        )
        ghost = make_constraint(
            id="c-ghost",
            description="argon2 again",
            ts="2020-01-01T00:00:00Z",
            paths=["deleted/ghost.py"],
        )
        result = build_pack(
            [],
            [live, other, ghost],
            9999,
            9999,
            keywords=["argon2"],
            path_exists=lambda p: False,
        )
        # If the ghost counted toward the corpus, df("argon2")=N=2 would
        # zero its idf and gate out the survivor entirely.
        assert [c.id for c in result.constraints] == ["c-live"]
        assert result.stats.matched_constraints == 1


class TestDefaultBudgets:
    def test_defaults_match_probe_evidence(self) -> None:
        """The chosen split must sum near the evidence-chosen 1000 total.

        Revised post-merge: at 200/200 every realistic dogfood query
        clamped at the 3-item floor while dozens-hundreds matched. The
        500/500 split (~1000 total) roughly doubles shown items for
        ~+40% tokens with sub-1-item junk cost (see Spec 30 Evidence).
        """
        total = (
            CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS + CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS
        )
        assert total == 1000

    def test_max_tokens_override_splits_proportionally(self) -> None:
        from mementic.context import split_pack_budget

        c_tokens, d_tokens = split_pack_budget(100)
        assert c_tokens == 50
        assert d_tokens == 50

    def test_max_tokens_none_returns_defaults(self) -> None:
        from mementic.context import split_pack_budget

        assert split_pack_budget(None) == (
            CONTEXT_PACK_DEFAULT_MAX_CONSTRAINT_TOKENS,
            CONTEXT_PACK_DEFAULT_MAX_DECISION_TOKENS,
        )


class TestPackSurface:
    def _keyword_pack(self, budget: int) -> ContextPackResult:
        # The distractor keeps df("argon2") below N so matched items score
        # above zero; it is itself gated out of the rendered lines.
        constraints = [
            make_constraint(
                id=f"c-{i}",
                description="argon2 rule with a reasonably long description line",
            )
            for i in range(5)
        ]
        constraints.append(make_constraint(id="c-other", description="unrelated filler topic"))
        return build_pack(
            [],
            constraints,
            max_constraint_tokens=budget,
            max_decision_tokens=9999,
            keywords=["argon2"],
        )

    def test_header_shows_shown_of_matched(self) -> None:
        result = self._keyword_pack(budget=9999)
        text = str(result.text)
        assert "shown 5 of 5 matched" in text

    def test_header_reflects_truncation(self) -> None:
        result = self._keyword_pack(budget=40)
        text = str(result.text)
        # The min-shown floor keeps a third item even though it overflows
        # the tiny budget (Spec 30 review).
        assert "- shown 3 of 5 matched" in text

    def test_truncation_marker_when_items_cut(self) -> None:
        result = self._keyword_pack(budget=40)
        text = str(result.text)
        assert "+2 more below threshold - refine keywords or raise --max-tokens" in text

    def test_no_marker_when_everything_shown(self) -> None:
        result = self._keyword_pack(budget=9999)
        text = str(result.text)
        assert "below threshold" not in text

    def test_zero_match_section_note_is_honest(self) -> None:
        constraints = [
            make_constraint(id="c-x", description="unrelated topic"),
        ]
        result = build_pack(
            [],
            constraints,
            9999,
            9999,
            keywords=["argon2"],
        )
        assert "_0 of 1 constraints matched your keywords._" in result.text

    def test_empty_input_renders_none(self) -> None:
        result = build_pack([], [], 9999, 9999, keywords=["argon2"])
        assert "_None_" in result.text

    def test_every_rendered_line_carries_inline_id(self) -> None:
        """Chainability contract: item lines always contain ``(#<id>``."""
        decisions = [
            make_decision(id=f"d-{i}", rationale=f"argon2 rationale {i}") for i in range(4)
        ]
        constraints = [
            make_constraint(id=f"c-{i}", description=f"argon2 rule {i}") for i in range(4)
        ]
        distractor_c = make_constraint(id="c-other", description="filler topic")
        distractor_d = make_decision(id="d-other", rationale="filler topic")
        result = build_pack(
            [*decisions, distractor_d],
            [*constraints, distractor_c],
            9999,
            9999,
            keywords=["argon2"],
        )
        item_lines = [ln for ln in result.text.splitlines() if ln.startswith("- ")]
        assert len(item_lines) == 8
        for line in item_lines:
            assert "(#" in line

    def test_stats_counts_are_consistent(self) -> None:
        result = self._keyword_pack(budget=40)
        stats = result.stats
        assert stats.shown_constraints == 3
        assert stats.matched_constraints == 5
        assert stats.shown_decisions == 0
        assert stats.matched_decisions == 0


class TestTruncateFloorAndBackfill:
    """One long top-ranked line must never starve a matched section."""

    def _pack(
        self,
        constraints: list[Constraint],
        budget: int,
    ) -> ContextPackResult:
        distractor = make_constraint(
            id="c-other",
            description="unrelated filler topic",
        )
        return build_pack(
            [],
            [*constraints, distractor],
            budget,
            9999,
            keywords=["argon2"],
        )

    @staticmethod
    def _tok(entry: Constraint) -> int:
        return estimate_tokens(_compact_constraint_line(entry))

    def test_floor_shows_three_with_huge_lines_and_tiny_budget(self) -> None:
        """Dozens matched + one-line-per-item budget -> still >=3 shown,
        and the marker equals matched - shown."""
        constraints = [
            make_constraint(
                id=f"c-{i}",
                description="argon2 rule " + "with a very long description " * 8,
            )
            for i in range(6)
        ]
        result = self._pack(constraints, budget=10)
        assert result.stats.matched_constraints == 6
        assert result.stats.shown_constraints >= min(3, len(constraints))
        shown = result.stats.shown_constraints
        assert f"+{6 - shown} more below threshold" in str(result.text)

    def test_top_item_admitted_when_it_alone_exceeds_budget(self) -> None:
        """Selected is never empty while ranked is non-empty."""
        huge = make_constraint(id="c-huge", description="argon2 " + "x" * 400)
        result = self._pack([huge], budget=1)
        assert [c.id for c in result.constraints] == ["c-huge"]
        assert "- shown 1 of 1 matched" in str(result.text)

    def test_backfill_continues_past_overlong_ranked_line(self) -> None:
        """An over-budget line is skipped, not a break: shorter lower-ranked
        lines behind it are still admitted, preserving score-desc order."""
        ts = {
            "c-r1": "2025-01-05T00:00:00Z",
            "c-r2": "2025-01-04T00:00:00Z",
            "c-r3": "2025-01-03T00:00:00Z",
            "c-r4": "2025-01-02T00:00:00Z",
            "c-r5": "2025-01-01T00:00:00Z",
        }
        constraints = [
            make_constraint(
                id="c-r1",
                description="argon2 short rule one",
                ts=ts["c-r1"],
            ),
            make_constraint(
                id="c-r2",
                description="argon2 short rule two",
                ts=ts["c-r2"],
            ),
            make_constraint(
                id="c-r3",
                description="argon2 short rule three",
                ts=ts["c-r3"],
            ),
            make_constraint(
                id="c-r4",
                description="argon2 " + "long " * 200,
                ts=ts["c-r4"],
            ),
            make_constraint(
                id="c-r5",
                description="argon2 short rule five",
                ts=ts["c-r5"],
            ),
        ]
        # Equal scores: deterministic order is created_at desc then id asc,
        # i.e. r1..r5. Budget fits r1+r2+r3+r5 exactly; the huge r4 alone
        # exceeds whatever remains after the first three.
        budget = sum(self._tok(c) for c in constraints if c.id != "c-r4")
        result = self._pack(constraints, budget)
        ids = [c.id for c in result.constraints]
        assert ids == ["c-r1", "c-r2", "c-r3", "c-r5"]
        stats = result.stats
        assert stats.shown_constraints == 4
        # Truncation marker K must equal matched - shown (Spec 30 review d).
        assert "+1 more below threshold" in str(result.text)
