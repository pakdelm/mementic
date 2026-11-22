"""Tests for mementic CLI commands."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Generator
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mementic.cli import app
from mementic.cli.views import format_successor_cell
from mementic.paths import ProjectPaths
from mementic.store import MementicStore

runner = CliRunner()
_open_stores: list[MementicStore] = []


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force rich's render width to 160 so asserted phrases never wrap."""
    monkeypatch.setattr("mementic.console.console._width", 160)


@pytest.fixture(autouse=True)
def _close_stores() -> Generator[None, None, None]:
    """Close every store created by ``_make_store`` after each test."""
    yield
    while _open_stores:
        _open_stores.pop().close()


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from CLI output for platform-agnostic assertions.

    Covers SGR sequences (colors, bold, etc.) used by Typer/Rich formatting.
    Does not cover OSC sequences (hyperlinks) or cursor movement — those are
    rare in CLI help output. Use a library like ``strip-ansi`` if this becomes
    a recurring pattern across tests.
    """
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _make_store() -> MementicStore:
    """Build an empty in-memory store (schema v5)."""
    store = MementicStore(db_path=":memory:")
    _open_stores.append(store)
    return store


def _seed_decision(
    store,
    entry_id: str,
    title: str = "Title",
    rationale: str = "...",
    created_at: str = "2024-01-01T00:00:00Z",
    paths: list[str] | None = None,
):
    """Seed one decision with sane defaults."""
    from mementic.models import Decision

    d = Decision(
        id=entry_id,
        title=title,
        rationale=rationale,
        created_at=created_at,
        paths=paths or [],
    )
    store.add_decision(
        entry_id=d.id,
        title=d.title,
        rationale=d.rationale,
        created_at=d.created_at,
        paths=d.paths,
    )
    return d


def _seed_constraint(
    store,
    entry_id: str,
    description: str = "...",
    enforced_by: str = "",
    created_at: str = "2024-01-01T00:00:00Z",
    paths: list[str] | None = None,
):
    """Seed one constraint with sane defaults."""
    from mementic.models import Constraint

    c = Constraint(
        id=entry_id,
        description=description,
        enforced_by=enforced_by,
        created_at=created_at,
        paths=paths or [],
    )
    store.add_constraint(
        entry_id=c.id,
        description=c.description,
        enforced_by=c.enforced_by,
        created_at=c.created_at,
        paths=c.paths,
    )
    return c


class TestListCommand:
    """Tests for mementic list command."""

    def test_list_decisions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _make_store()
        _seed_decision(store, "use-jwt", title="Use JWT Auth", rationale="Tokens")
        _seed_decision(store, "use-async", title="Use async I/O", rationale="Performance")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "decisions"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "use-jwt" in output
        assert "use-async" in output
        assert "Use JWT Auth" in output

    def test_list_decisions_by_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "d-accepted", rationale="...")
        _seed_decision(store, "d-dead", rationale="...")
        store.deprecate("decision", "d-dead")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "decisions", "--status", "deprecated"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "d-dead" in output
        assert "d-accepted" not in output

    def test_list_decisions_dead_entries_show_superseded_by(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "old-way", rationale="...")
        _seed_decision(store, "new-way", created_at="2024-02-01T00:00:00Z")
        store.deprecate("decision", "old-way")  # no successor: replaced_by NULL

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "decisions", "--status", "deprecated"])

        output = _strip_ansi(result.stdout)
        assert "none" in output.lower()
        assert "Superseded" in output

    def test_list_decisions_last_n(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _make_store()
        for i in range(1, 6):
            _seed_decision(store, f"d-{i}", created_at=f"2024-01-{i:02d}T00:00:00Z")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "decisions", "-n", "2"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "d-4" in output
        assert "d-5" in output
        assert "d-1" not in output

    def test_list_constraints(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _make_store()
        _seed_decision(store, "use-jwt")
        _seed_constraint(
            store, "c-stateless", description="Services must be stateless", enforced_by="use-jwt"
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "constraints"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "c-stateless" in output
        assert "Services must be stateless" in output
        assert "via use-jwt" in output

    def test_list_entities_shows_stored_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "d-paths", paths=["src/auth.py", "src/models/user.py"])

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "entities"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "src/auth.py" in output
        assert "src/models/user.py" in output

    def test_list_entities_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        stale_path = str(tmp_path / "does_not_exist.py")
        store = _make_store()
        _seed_decision(store, "d-stale", paths=[stale_path])

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["list", "entities", "--stale"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "does_not_exist.py" in output


class TestDeprecateCommand:
    """Tests for mementic deprecate command."""

    def test_deprecate_marks_decision_deprecated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "use-old-auth", rationale="Basic session auth")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["deprecate", "use-old-auth"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Deprecated" in output or "deprecated" in output.lower()
        assert "use-old-auth" in output
        assert [d.id for d in store.get_live_decisions()] == []

    def test_deprecate_with_reason_writes_tombstone_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "use-old-auth", rationale="Basic session auth")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app, ["deprecate", "use-old-auth", "--reason", "Migrated to JWT"]
            )

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Deprecated" in output
        # Reason lives in the tombstone row, prose is never mutated.
        records = store.get_deprecations_for_entry("decision", "use-old-auth")
        assert len(records) == 1
        assert records[0]["reason"] == "Migrated to JWT"
        d = store.get_decision("use-old-auth")
        assert d is not None and d.rationale == "Basic session auth"

    def test_deprecate_nonexistent_shows_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["deprecate", "nonexistent-id"])

        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "not found" in output.lower()

    def test_deprecate_already_deprecated_shows_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "already-dep", rationale="Old stuff")
        store.deprecate("decision", "already-dep")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["deprecate", "already-dep"])

        output = result.stdout + result.stderr
        assert "Skipped already-dep: already deprecated" in output

    def test_deprecate_replaced_by_writes_successor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "use-old-auth", rationale="Basic session auth")
        _seed_decision(store, "use-new-auth", rationale="Token auth")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                ["deprecate", "use-old-auth", "--replaced-by", "use-new-auth", "-r", "moved"],
            )

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Deprecated use-old-auth (superseded by use-new-auth): moved" in output
        records = store.get_deprecations_for_entry("decision", "use-old-auth")
        assert len(records) == 1
        assert records[0]["replaced_by"] == "use-new-auth"

    def test_deprecate_missing_successor_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "use-old-auth", rationale="Basic session auth")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["deprecate", "use-old-auth", "--replaced-by", "ghost"])

        assert result.exit_code == 1
        output = result.stdout + result.stderr
        assert "Error: successor 'ghost' not found" in output
        assert store.is_deprecated("decision", "use-old-auth") is False

    def test_deprecate_repeat_with_second_successor_reports_also_superseded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "old-a", rationale="Old A")
        _seed_decision(store, "old-b", rationale="Old B")
        _seed_decision(store, "first-successor", rationale="First")
        _seed_decision(store, "second-successor", rationale="Second")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            runner.invoke(app, ["deprecate", "old-a", "old-b", "--replaced-by", "first-successor"])
            result = runner.invoke(
                app,
                ["deprecate", "old-a", "old-b", "--replaced-by", "second-successor", "-r", "split"],
            )

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Recorded successor second-successor for old-a: split" in output
        assert "Recorded successor second-successor for old-b: split" in output
        assert "Summary: 2 also superseded" in output


class TestHistoryCommand:
    """Tests for the mementic history command."""

    def test_history_with_id_prints_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "old-auth", rationale="Basic auth everywhere")
        store.deprecate("decision", "old-auth", reason="insecure")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["history", "old-auth"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "old-auth" in output
        assert "insecure" in output

    def test_history_without_id_lists_all_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()
        _seed_decision(store, "d1")
        _seed_constraint(store, "c1")
        store.deprecate("decision", "d1", reason="first death")
        store.deprecate("constraint", "c1", reason="second death")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "d1" in output and "c1" in output

    def test_history_transitive_chain_prints_arrows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Successor chains (x -> y -> z) render as arrows with records."""
        store = _make_store()
        _seed_decision(store, "x", rationale="first", created_at="2024-01-01T00:00:00Z")
        _seed_decision(store, "y", rationale="second", created_at="2024-02-01T00:00:00Z")
        _seed_decision(store, "z", rationale="third", created_at="2024-03-01T00:00:00Z")
        store.deprecate("decision", "x", reason="v1", replaced_by="y")
        store.deprecate("decision", "y", reason="v2", replaced_by="z")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["history", "x"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "x -> y -> z" in output
        assert "v1" in output and "v2" in output

    def test_history_empty_store_prints_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _make_store()

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        assert "No deprecation history" in _strip_ansi(result.stdout)

    def test_history_shows_successor_chain_for_deprecated_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A linear successor chain renders root-first as x -> y -> z."""
        store = _make_store()
        _seed_decision(store, "x", rationale="first", created_at="2024-01-01T00:00:00Z")
        _seed_decision(store, "y", rationale="second", created_at="2024-02-01T00:00:00Z")
        _seed_decision(store, "z", rationale="third", created_at="2024-03-01T00:00:00Z")
        store.deprecate("decision", "x", reason="v1", replaced_by="y")
        store.deprecate("decision", "y", reason="v2", replaced_by="z")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["history", "x"])

        assert result.exit_code == 0
        assert "x -> y -> z" in _strip_ansi(result.stdout)

    def test_history_split_chain_renders_siblings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two successors of one entry share one comma-joined trail position."""
        store = _make_store()
        _seed_decision(store, "base", rationale="base", created_at="2024-01-01T00:00:00Z")
        _seed_decision(store, "first", rationale="first", created_at="2024-01-02T00:00:00Z")
        _seed_decision(store, "second", rationale="second", created_at="2024-01-03T00:00:00Z")
        store.deprecate("decision", "base", reason="split", replaced_by="first")
        store.deprecate(
            "decision",
            "base",
            reason="split",
            replaced_by="second",
            additional_successor=True,
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["history", "base"])

        assert result.exit_code == 0
        assert "base -> first, second" in _strip_ansi(result.stdout)


_COMPACT_QUERIES = [
    ["query", "keywords", "auth", "--compact"],
    ["query", "file", "src/auth.py", "--compact"],
]

_COMPACT_QUERIES_SHORT = [
    ["query", "keywords", "auth", "-c"],
    ["query", "file", "src/auth.py", "-c"],
]

_DEFAULT_QUERIES = [
    ["query", "keywords", "auth"],
    ["query", "file", "src/auth.py"],
]

_PANEL_TITLES = {"keywords": "Context pack:", "file": "File history:"}


def _panel_title(args: list[str]) -> str:
    """Return the rich panel title expected for a query invocation."""
    return _PANEL_TITLES[args[1]]


def _seed_query_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Seed an in-memory store whose file binding exists on disk.

    Changes into ``tmp_path`` so the relative ``src/auth.py`` binding is
    live for ``query file``, then returns the store for patching.
    """
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "src" / "auth.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# auth module\n", encoding="utf-8")

    store = _make_store()
    _seed_decision(
        store,
        "use-jwt",
        title="Use JWT Auth",
        rationale="Token-based authentication for APIs",
        paths=["src/auth.py"],
    )
    _seed_constraint(
        store,
        "c-stateless",
        description="Services must be stateless",
        enforced_by="use-jwt",
        paths=["src/auth.py"],
    )
    return store


class TestQueryCompactOutput:
    """Tests for undecorated ``--compact`` query output."""

    @pytest.mark.parametrize("args", _COMPACT_QUERIES)
    def test_compact_output_prints_echo_line_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        args: list[str],
    ) -> None:
        """An explicit --compact run emits exactly one [mementic: ...] line."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert _strip_ansi(result.stdout).count("[mementic:") == 1

    @pytest.mark.parametrize("args", _COMPACT_QUERIES)
    def test_compact_output_includes_entity_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        args: list[str],
    ) -> None:
        """--compact still prints the pack body, not just the echo line."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert "use-jwt" in _strip_ansi(result.stdout)

    @pytest.mark.parametrize("args", _COMPACT_QUERIES)
    def test_compact_output_omits_panel(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        args: list[str],
    ) -> None:
        """Undecorated output omits the rich panel entirely."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert _panel_title(args) not in _strip_ansi(result.stdout)

    @pytest.mark.parametrize("args", _COMPACT_QUERIES_SHORT)
    def test_compact_short_alias_matches_long_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        args: list[str],
    ) -> None:
        """-c is an alias for --compact on both query commands."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert output.count("[mementic:") == 1
        assert "use-jwt" in output
        assert _panel_title(args) not in output

    @pytest.mark.parametrize("args", _DEFAULT_QUERIES)
    def test_default_output_keeps_panel(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        args: list[str],
    ) -> None:
        """Without --compact the rich panel is still rendered."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, args)
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert _panel_title(args) in _strip_ansi(result.stdout)

    def test_compact_empty_match_omits_matched_entities(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--compact with no matches keeps the zero-match body but no entities."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["query", "keywords", "zzqxv", "--compact"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert output.count("[mementic:") == 1
        assert "[mementic: 0 of 0 constraints, 0 of 0 decisions | zzqxv]" in output
        assert "0 of 1 constraints matched your keywords" in output
        assert "0 of 1 decisions matched your keywords" in output
        assert "use-jwt" not in output
        assert "Context pack:" not in output

    def test_search_output_prints_echo_line_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query search (no --compact flag) still emits one echo line."""
        store = _seed_query_store(tmp_path, monkeypatch)
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["query", "search", "stateless"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert _strip_ansi(result.stdout).count("[mementic:") == 1


class TestQueryKeywordsJson:
    """Tests for `mementic query keywords --json` output."""

    def test_query_keywords_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query keywords --json returns JSON with matched constraints and decisions."""
        target = tmp_path / "src" / "auth.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1", encoding="utf-8")
        store = _make_store()
        _seed_decision(
            store,
            "use-jwt",
            rationale="Token-based authentication for API endpoints",
            paths=["src/auth.py"],
        )
        _seed_constraint(
            store,
            "c-stateless",
            description="Services must be stateless",
            enforced_by="use-jwt",
            paths=["src/auth.py"],
        )
        paths = ProjectPaths(base_dir=tmp_path, store_root=tmp_path)

        with (
            patch("mementic.cli.context.resolve_paths", return_value=paths),
            patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)),
        ):
            result = runner.invoke(app, ["query", "keywords", "auth", "stateless", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)

        assert [item["id"] for item in data["decisions"]] == ["use-jwt"]
        assert [item["id"] for item in data["constraints"]] == ["c-stateless"]

        for c_data in data["constraints"]:
            assert "description" in c_data
            assert "entities" in c_data

        for d_data in data["decisions"]:
            assert "title" in d_data
            assert "rationale" in d_data
            assert "entities" in d_data

    def test_query_keywords_json_shape_with_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query keywords --json returns matched items when keywords overlap."""
        store = _make_store()
        _seed_decision(store, "use-jwt", rationale="Token-based authentication for APIs")
        # No bound paths: a nonexistent path plus an old timestamp would make
        # this entry a ghost and drop it from packs.
        _seed_constraint(
            store,
            "c-stateless",
            description="All services must be stateless",
            enforced_by="use-jwt",
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["query", "keywords", "auth", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)

        assert isinstance(data["summary"], str)
        assert isinstance(data["constraints"], list)
        assert isinstance(data["decisions"], list)
        # "auth" keyword should match the decision with "Token-based authentication"
        assert len(data["decisions"]) >= 1
        assert data["decisions"][0]["id"] == "use-jwt"

    def test_query_keywords_json_excludes_deprecated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tombstoned entries are excluded from JSON packs (derived liveness)."""
        store = _make_store()
        _seed_decision(store, "dead-kw", rationale="Zebra authentication scheme")
        store.deprecate("decision", "dead-kw")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["query", "keywords", "zebra", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["decisions"] == []
        assert "0 of 0" in data["summary"] or data["summary"].startswith("0")


class TestQueryFileJson:
    """Tests for `mementic query file --json` output."""

    def test_query_file_json_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """query file --json returns matched decisions and constraints."""
        target = tmp_path / "src" / "auth.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1", encoding="utf-8")
        store = _make_store()
        _seed_decision(
            store,
            "use-jwt",
            rationale="Token-based authentication for API endpoints",
            paths=["src/auth.py"],
        )
        _seed_constraint(
            store,
            "c-stateless",
            description="Services must be stateless",
            enforced_by="use-jwt",
            paths=["src/auth.py"],
        )
        paths = ProjectPaths(base_dir=tmp_path, store_root=tmp_path)

        with (
            patch("mementic.cli.context.resolve_paths", return_value=paths),
            patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)),
        ):
            result = runner.invoke(app, ["query", "file", "src/auth.py", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)

        assert data["file"] == "src/auth.py"
        assert isinstance(data["summary"], str)
        assert [decision["id"] for decision in data["decisions"]] == ["use-jwt"]
        assert [constraint["id"] for constraint in data["constraints"]] == ["c-stateless"]

    def test_query_file_json_prefix_opt_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query file matches exactly by default; --prefix opts into prefixes."""
        monkeypatch.chdir(tmp_path)
        # The matched file must exist on disk: dead paths are filtered by a
        # filesystem check at read time.
        target = tmp_path / "src" / "io" / "handlers.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1", encoding="utf-8")

        store = _make_store()
        _seed_decision(
            store, "io-decision", rationale="Async I/O everywhere", paths=["src/io/handlers.py"]
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            exact = runner.invoke(app, ["query", "file", "src/io/", "--json"])
            prefixed = runner.invoke(app, ["query", "file", "src/io/", "--prefix", "--json"])

        assert exact.exit_code == 0
        assert json.loads(exact.stdout)["decisions"] == []
        assert prefixed.exit_code == 0
        assert [d["id"] for d in json.loads(prefixed.stdout)["decisions"]] == ["io-decision"]

    def test_query_file_dead_paths_filtered_by_filesystem_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Paths whose files no longer exist are dropped lazily at read time."""
        monkeypatch.chdir(tmp_path)
        ghost = "ghost.py"
        alive = tmp_path / "alive.py"
        alive.write_text("x = 1", encoding="utf-8")

        store = _make_store()
        _seed_decision(store, "ghost-only", rationale="Only a dead binding", paths=[ghost])
        _seed_decision(
            store,
            "mixed",
            rationale="Dead and live bindings",
            created_at="2024-02-01T00:00:00Z",
            paths=[ghost, "alive.py"],
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["query", "file", ghost, "--json"])
            mixed = runner.invoke(app, ["query", "file", "alive.py", "--json"])

        # Entry bound only to the dead path is filtered out entirely...
        assert json.loads(result.stdout)["decisions"] == []
        # ...while the entry with one surviving binding still matches.
        assert [d["id"] for d in json.loads(mixed.stdout)["decisions"]] == ["mixed"]


class TestDeprecateMultiId:
    """Tests for `mementic deprecate` with multiple IDs."""

    def test_deprecate_multiple_ids(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """deprecate with multiple IDs deprecates all and reports count."""
        store = _make_store()
        _seed_decision(store, "dec-1", rationale="First decision")
        _seed_decision(store, "dec-2", rationale="Second decision")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["deprecate", "dec-1", "dec-2"])

        assert result.exit_code == 0
        output = _strip_ansi(result.stdout)
        assert "Deprecated dec-1." in output
        assert "Deprecated dec-2." in output
        assert "Summary: 2 deprecated" in output
        assert store.get_live_decisions() == []

    def test_deprecate_mixed_valid_and_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deprecate with mixed valid/invalid IDs reports count for each."""
        store = _make_store()
        _seed_decision(store, "dec-valid", rationale="A valid decision")

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["deprecate", "dec-valid", "nonexistent"])

        assert result.exit_code == 1  # errors → exit code 1
        output = _strip_ansi(result.stdout)
        assert "Error: nonexistent not found" in _strip_ansi(result.stderr)
        assert "Deprecated dec-valid." in output
        assert "Summary: 1 deprecated, 1 not found" in output


class TestAddDecisionAutoResolve:
    """Tests for `mementic add decision` auto-resolve rules."""

    def test_new_slug_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A decision with a new slug is accepted and stored."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                ["add", "decision", "Use JWT Auth", "--rationale", "Token-based auth for APIs"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        d = store.get_decision("use-jwt-auth")
        assert d is not None
        assert {x.id for x in store.get_live_decisions()} == {"use-jwt-auth"}

    def test_duplicate_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same slug + same rationale (>=80% overlap) is skipped as duplicate."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        _seed_decision(
            store,
            "use-jwt-auth",
            title="Use JWT Auth",
            rationale="Token-based authentication for APIs",
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                [
                    "add",
                    "decision",
                    "Use JWT Auth",
                    "--rationale",
                    "Token-based authentication for APIs",
                ],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "skipped" in output.lower()
        # Original entry unchanged — still the earlier timestamp
        d = store.get_decision("use-jwt-auth")
        assert d is not None and d.created_at == "2024-01-01T00:00:00Z"

    def test_same_slug_different_content_deprecates_and_accepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + different rationale deprecates existing, accepts new."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        _seed_decision(
            store, "use-jwt-auth", title="Use JWT Auth", rationale="Token-based auth for APIs"
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                [
                    "add",
                    "decision",
                    "Use JWT Auth",
                    "--rationale",
                    "Completely different approach using rotating keys and refresh tokens",
                ],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "deprecated" in output.lower()
        # New entry accepted under the same slug and derives as live.
        d = store.get_decision("use-jwt-auth")
        assert d is not None
        assert "rotating keys" in d.rationale
        assert {x.id for x in store.get_live_decisions()} == {"use-jwt-auth"}
        # A tombstone row documents the succession.
        records = store.get_deprecations_for_entry("decision", "use-jwt-auth")
        assert len(records) == 1
        assert records[0]["reason"] == "superseded by re-added entry"

    def test_entity_flag_writes_entry_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """-e paths land in entry_paths and show up in path queries."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                [
                    "add",
                    "decision",
                    "Track Auth Flow",
                    "-r",
                    "Central middleware owns the handshake",
                    "-e",
                    "src/foo.py::handler",
                ],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        d = store.get_decision("track-auth-flow")
        assert d is not None
        assert d.paths == ["src/foo.py"]

    def test_rejected_flag_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--rejected was deleted; passing it exits nonzero."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                [
                    "add",
                    "decision",
                    "Some Title",
                    "-r",
                    "Because of tradeoffs",
                    "--rejected",
                    "the other thing",
                ],
            )

        assert result.exit_code != 0

    def test_output_reports_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Add output is one line naming the entry, with no outcome suffix."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                ["add", "decision", "Some Decision", "--rationale", "Because"],
            )

        output = _strip_ansi(result.stdout + result.stderr)
        assert "Added decision some-decision" in output
        assert "accepted" not in output.lower()


class TestAddConstraintAutoResolve:
    """Tests for `mementic add constraint` auto-resolve rules."""

    def test_new_constraint_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A constraint with a new slug is accepted and stored."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                ["add", "constraint", "Services must be stateless", "-e", "src/services.py"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        c = store.get_constraint("services-must-be-stateless")
        assert c is not None

    def test_duplicate_constraint_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + same description is skipped as duplicate."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        _seed_constraint(
            store,
            "services-must-be-stateless",
            description="Services must be stateless",
            paths=["src/a.py"],
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                ["add", "constraint", "Services must be stateless", "-e", "src/services.py"],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "skipped" in output.lower()

    def test_same_slug_different_content_deprecates_and_accepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same slug + different description deprecates existing, accepts new."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        _seed_constraint(
            store, "use-https", description="All endpoints must use HTTPS", paths=["src/a.py"]
        )

        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                [
                    "add",
                    "constraint",
                    "All network calls must use TLS 1.3",
                    "-e",
                    "src/net.py",
                    "--id",
                    "use-https",
                ],
            )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout + result.stderr)
        assert "deprecated" in output.lower()
        c = store.get_constraint("use-https")
        assert c is not None
        assert "TLS 1.3" in c.description
        assert {x.id for x in store.get_live_constraints()} == {"use-https"}

    def test_output_reports_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Add-constraint output names the entry, with no outcome suffix."""
        monkeypatch.chdir(tmp_path)

        store = _make_store()
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(
                app,
                ["add", "constraint", "No raw SQL in handlers", "-e", "src/db.py"],
            )

        output = _strip_ansi(result.stdout + result.stderr)
        assert "Added constraint no-raw-sql-in-handlers" in output
        assert "active" not in output.lower()


class TestInitStub:
    """Tests for the collapsed init command."""

    def test_init_creates_graph_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """init creates .mementic/graph.db when missing."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert (tmp_path / ".mementic" / "graph.db").exists()

    def test_init_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running init twice succeeds and keeps the database intact."""
        monkeypatch.chdir(tmp_path)

        first = runner.invoke(app, ["init"])
        assert first.exit_code == 0, f"stderr: {first.stderr}"
        db_path = tmp_path / ".mementic" / "graph.db"
        assert db_path.exists()

        second = runner.invoke(app, ["init"])
        assert second.exit_code == 0, f"stderr: {second.stderr}"
        assert db_path.exists()

    def test_init_adds_memory_dir_to_gitignore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init ignores .mementic/ in a Git work tree."""
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert ".mementic/" in gitignore

    def test_init_reports_paths_relative_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init names the graph and AGENTS.md relative to the working directory."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        output = _strip_ansi(result.stdout + result.stderr)
        assert ".mementic/graph.db" in output
        assert "Created AGENTS.md with enforcement block." in output
        assert tmp_path.as_posix() not in output

    def test_init_help_has_no_provider_flags(self) -> None:
        """init --help no longer shows --provider/--model/--key/--deep flags."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert "--provider" not in output
        assert "--model" not in output
        assert "--key" not in output
        assert "--deep" not in output

    def test_capture_command_removed(self) -> None:
        """The capture command is gone from mementic --help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        output = _strip_ansi(result.stdout)
        assert "capture" not in output


class TestVersion:
    """Version reporting through the root callback."""

    @pytest.mark.parametrize("flag", ["--version", "-V"])
    def test_version_flag(self, flag: str) -> None:
        """The version flag prints the installed package version."""
        from mementic import __version__

        result = runner.invoke(app, [flag])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert __version__ in result.stdout


class TestQuerySearch:
    """Successor rendering in ``mementic query search``."""

    def _search(self, store: MementicStore, term: str) -> str:
        """Run query search against ``store`` and return stripped output."""
        with patch("mementic.cli.context.resolve_store", return_value=nullcontext(store)):
            result = runner.invoke(app, ["query", "search", term])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        return _strip_ansi(result.stdout)

    def test_query_search_shows_successor_id_and_title(self) -> None:
        """A deprecated match carries its successor ID and the successor title."""
        store = _make_store()
        _seed_decision(
            store,
            "old-rule",
            title="Old Rule",
            rationale="legacy authentication rationale",
        )
        _seed_decision(
            store,
            "new-rule",
            title="New Rule",
            rationale="modern authentication rationale",
            created_at="2024-02-01T00:00:00Z",
        )
        store.deprecate("decision", "old-rule", reason="replaced", replaced_by="new-rule")

        output = self._search(store, "legacy")

        assert "[mementic: 0 constraints, 1 decisions | legacy]" in output
        row = next(line for line in output.splitlines() if "old-rule" in line)
        assert "new-rule (New Rule)" in row

    def test_query_search_plain_entry_has_no_successor_cell(self) -> None:
        """A live match renders only the ID and title columns."""
        store = _make_store()
        _seed_decision(
            store,
            "plain-rule",
            title="Plain Rule",
            rationale="unique plaintext marker",
        )

        output = self._search(store, "plaintext")

        assert "Superseded By" not in output
        assert "(missing)" not in output
        row = next(line for line in output.splitlines() if "plain-rule" in line)
        cells = [cell for cell in re.split(r"\s{2,}", row.strip("│ ").strip()) if cell]
        assert cells == ["plain-rule", "Plain Rule"]

    def test_query_search_split_lists_both_successors(self) -> None:
        """Both successors of a split appear on the deprecated entry's row."""
        store = _make_store()
        _seed_decision(
            store,
            "old-rule",
            title="Old Rule",
            rationale="legacy authentication rationale",
        )
        _seed_decision(
            store,
            "first-rule",
            title="First Rule",
            rationale="first replacement",
            created_at="2024-02-01T00:00:00Z",
        )
        _seed_decision(
            store,
            "second-rule",
            title="Second Rule",
            rationale="second replacement",
            created_at="2024-03-01T00:00:00Z",
        )
        store.deprecate("decision", "old-rule", reason="split", replaced_by="first-rule")
        store.deprecate(
            "decision",
            "old-rule",
            reason="split again",
            replaced_by="second-rule",
            additional_successor=True,
        )

        output = self._search(store, "legacy")

        row = next(line for line in output.splitlines() if "old-rule" in line)
        cells = [cell for cell in re.split(r"\s{2,}", row.strip("│ ").strip()) if cell]
        assert cells == [
            "old-rule",
            "Old Rule",
            "first-rule (First Rule), second-rule (Second Rule)",
        ]

    def test_query_search_adds_successor_column_only_where_a_successor_exists(self) -> None:
        """Pins that the Superseded By column is added per section, never globally.

        ``query search`` builds the decisions table before the constraints
        table and adds ``Superseded By`` only to a section that has a
        successor. Headers are recorded at construction, so an erroneously
        added blank column is caught even though it would render as a
        padding-only column and the stripped row text would look unchanged.
        """
        from rich.table import Table as RichTable

        from mementic.console import table as build_table

        store = _make_store()
        _seed_decision(
            store,
            "auth-decision",
            title="Auth Decision",
            rationale="Session authentication for every API",
        )
        _seed_decision(
            store,
            "new-rule",
            title="New Rule",
            rationale="Modern replacement",
            created_at="2024-02-01T00:00:00Z",
        )
        _seed_constraint(
            store,
            "legacy-constraint",
            description="Legacy Constraint requires basic authentication",
        )
        store.deprecate("constraint", "legacy-constraint", reason="moved", replaced_by="new-rule")

        created: list[RichTable] = []

        def recording_table(**kwargs: object) -> RichTable:
            rich_table = build_table(**kwargs)
            created.append(rich_table)
            return rich_table

        with patch("mementic.console.table", recording_table):
            self._search(store, "authentication")

        assert [[column.header for column in rich_table.columns] for rich_table in created] == [
            ["ID", "Title"],
            ["ID", "Description", "Superseded By"],
        ]

    def test_query_search_live_row_keeps_blank_successor_cell_in_a_successor_section(
        self,
    ) -> None:
        """A live match in a successor-bearing section renders a padding-only third cell.

        The section gains ``Superseded By`` because another match has a successor,
        so the live row must still supply a third cell for the table to render.
        The row shape is pinned here: the blank cell contributes no populated
        cell and no ``(missing)`` placeholder.
        """
        store = _make_store()
        _seed_decision(
            store,
            "old-rule",
            title="Old Rule",
            rationale="shared migration marker",
        )
        _seed_decision(
            store,
            "new-rule",
            title="New Rule",
            rationale="replacement",
            created_at="2024-02-01T00:00:00Z",
        )
        _seed_decision(
            store,
            "live-rule",
            title="Live Rule",
            rationale="shared migration marker",
        )
        store.deprecate("decision", "old-rule", reason="moved", replaced_by="new-rule")

        output = self._search(store, "migration")

        old_row = next(line for line in output.splitlines() if "old-rule" in line)
        old_cells = [cell for cell in re.split(r"\s{2,}", old_row.strip("│ ").strip()) if cell]
        live_row = next(line for line in output.splitlines() if "live-rule" in line)
        live_cells = [cell for cell in re.split(r"\s{2,}", live_row.strip("│ ").strip()) if cell]
        assert old_cells == ["old-rule", "Old Rule", "new-rule (New Rule)"]
        assert live_cells == ["live-rule", "Live Rule"]
        assert "(missing)" not in output


class TestFormatSuccessorCell:
    """Rendering rules for the successor table cell."""

    def test_format_successor_cell_empty(self) -> None:
        """An entry without successors renders an empty cell."""
        assert format_successor_cell([]) == ""

    def test_format_successor_cell_split_joins_labels(self) -> None:
        """Split successors render comma-joined as id (label)."""
        assert format_successor_cell([("a", "A"), ("b", "B")]) == "a (A), b (B)"

    def test_format_successor_cell_missing_label(self) -> None:
        """A successor whose row is gone renders id (missing)."""
        assert format_successor_cell([("gone", "")]) == "gone (missing)"
