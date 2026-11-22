"""End-to-end regression gate for the mementic CLI command surface.

Every test drives the real Typer app through ``typer.testing.CliRunner``
against an isolated temporary project directory (its own
``.mementic/graph.db``), never a shared or mocked store. MementicStore is
opened directly only to assert resulting state, never to seed it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from mementic import staleness
from mementic.cli import app
from mementic.paths import graph_db_path, resolve_base_dir
from mementic.store import MementicStore

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences so assertions are platform-agnostic."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _text(result: Result) -> str:
    """Return combined, ANSI-stripped stdout+stderr of a CLI invocation."""
    streams = [result.output]
    try:
        streams.append(result.stderr)
    except ValueError:
        pass  # stderr mixed into stdout by this click version
    return _strip_ansi("\n".join(streams))


# Wide fixed console so rich never wraps or ellipsizes asserted content,
# regardless of the host terminal size under pytest-xdist workers.
_CONSOLE_ENV = {"COLUMNS": "160"}


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force rich's render width to 160 for every test in this module.

    Pinning ``Console._width`` directly is deterministic across platforms
    and CI runners, where the ``COLUMNS`` environment variable may be
    pre-set, ignored by the runner, or shadowed by terminal detection.
    """
    monkeypatch.setattr("mementic.console.console._width", 160)


def _run(*args: str) -> Result:
    """Invoke the CLI app with the given argument vector."""
    return runner.invoke(app, list(args), env=_CONSOLE_ENV)


def _ok(*args: str) -> Result:
    """Invoke the CLI app, asserting a zero exit code."""
    result = _run(*args)
    assert result.exit_code == 0, f"command {list(args)} failed:\n{_text(result)}"
    return result


def _store() -> MementicStore:
    """Open the project store at the CWD graph database."""
    return MementicStore(db_path=graph_db_path(resolve_base_dir()))


def _decision(entry_id: str):
    """Open the project store read-only and return one decision (or None)."""
    store = _store()
    try:
        return store.get_decision(entry_id)
    finally:
        store.close()


def _constraint(entry_id: str):
    """Open the project store read-only and return one constraint (or None)."""
    store = _store()
    try:
        return store.get_constraint(entry_id)
    finally:
        store.close()


@pytest.fixture()
def bare_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated empty project directory (no store yet)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def project_dir(bare_dir: Path) -> Path:
    """Isolated project directory with an initialized store."""
    _ok("init")
    return bare_dir


@pytest.mark.parametrize(
    "command",
    ["init", "add", "deprecate", "query", "list", "log", "status", "history"],
)
def test_help_lists_top_level_command(command: str) -> None:
    result = _ok("--help")
    assert command in _text(result)


def test_help_does_not_mention_removed_capture_command() -> None:
    result = _ok("--help")
    assert "capture" not in _text(result)


@pytest.mark.parametrize("subcommand", ["decision", "constraint"])
def test_add_help_lists_subcommands(subcommand: str) -> None:
    result = _ok("add", "--help")
    assert subcommand in _text(result)


@pytest.mark.parametrize("subcommand", ["keywords", "file", "files", "search", "stats"])
def test_query_help_lists_subcommands(subcommand: str) -> None:
    result = _ok("query", "--help")
    assert subcommand in _text(result)


@pytest.mark.parametrize("subcommand", ["decisions", "constraints", "entities"])
def test_list_help_lists_subcommands(subcommand: str) -> None:
    result = _ok("list", "--help")
    assert subcommand in _text(result)


def test_init_fresh_dir_creates_graph_db(bare_dir: Path) -> None:
    result = _ok("init")
    assert "initialized" in _text(result).lower()
    assert (bare_dir / ".mementic" / "graph.db").exists()


def test_init_second_run_is_idempotent(project_dir: Path) -> None:
    result = _ok("init")
    assert "already exists" in _text(result)
    assert (project_dir / ".mementic" / "graph.db").exists()


def test_init_writes_agents_md_enforcement_block(bare_dir: Path) -> None:
    """init always syncs the enforcement block into AGENTS.md."""
    _ok("init")
    agents_md = bare_dir / "AGENTS.md"
    assert agents_md.exists()
    content = agents_md.read_text(encoding="utf-8")
    assert "Mementic Memory" in content
    assert "ALWAYS ACTIVE" in content


def test_init_second_run_updates_section_in_place(bare_dir: Path) -> None:
    """Re-running init updates the section without duplicating it."""
    _ok("init")
    _ok("init")
    content = (bare_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert content.count("<!-- mementic:start -->") == 1
    assert content.count("<!-- mementic:end -->") == 1


def test_add_decision_new_slug_is_accepted(project_dir: Path) -> None:
    result = _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt tokens carry authentication claims")
    assert "added decision use-jwt-auth" in _text(result).lower()
    d = _decision("use-jwt-auth")
    assert d is not None
    # Live entries derive as accepted — no cached status column exists.
    store = _store()
    try:
        assert {x.id for x in store.get_live_decisions()} == {"use-jwt-auth"}
    finally:
        store.close()


def test_add_decision_identical_readd_is_skipped_as_duplicate(
    project_dir: Path,
) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt tokens carry authentication claims")
    result = _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt tokens carry authentication claims")
    output = _text(result)
    assert "skipped" in output.lower()
    assert "duplicate" in output.lower()
    # Original rationale survives untouched.
    d = _decision("use-jwt-auth")
    assert d is not None
    assert "carry" in d.rationale
    assert "rotated" not in d.rationale


def test_add_decision_same_title_new_rationale_deprecates_and_accepts(
    project_dir: Path,
) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt tokens carry authentication claims")
    result = _ok(
        "add",
        "decision",
        "Use Jwt Auth",
        "-r",
        "Rotated keys replace tokens with signed certificates",
    )
    output = _text(result)
    assert "deprecated" in output.lower()
    d = _decision("use-jwt-auth")
    assert d is not None
    assert "certificates" in d.rationale
    # The new incarnation derives as live despite the fresh tombstone row.
    store = _store()
    try:
        assert {x.id for x in store.get_live_decisions()} == {"use-jwt-auth"}
        records = store.get_deprecations_for_entry("decision", "use-jwt-auth")
        assert len(records) == 1
        assert records[0]["replaced_by"] == "use-jwt-auth"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("second_rationale", "expected_outcome"),
    [
        # Identical text: 100% overlap >= threshold -> duplicate skip.
        ("Jwt tokens carry authentication claims across every service boundary", "skipped"),
        # One word changed out of ten: ~82% Jaccard overlap >= threshold
        # -> still a duplicate skip.
        ("Jwt tokens carry authentication claims across every service nightly", "skipped"),
        # Wholly different content -> deprecate old + accept new.
        ("Signed certificates replace bearer tokens entirely", "previous entry deprecated"),
    ],
    ids=["identical-duplicate", "near-duplicate", "changed-content"],
)
def test_add_decision_reports_outcome_per_disposition(
    project_dir: Path,
    second_rationale: str,
    expected_outcome: str,
) -> None:
    _ok(
        "add",
        "decision",
        "Use Jwt Auth",
        "-r",
        "Jwt tokens carry authentication claims across every service boundary",
    )
    result = _ok("add", "decision", "Use Jwt Auth", "-r", second_rationale)
    assert expected_outcome in _text(result).lower()


def test_add_constraint_new_description_is_accepted(project_dir: Path) -> None:
    result = _ok("add", "constraint", "Handlers must remain stateless", "-e", "src/api.py")
    assert "added constraint handlers-must-remain-stateless" in _text(result).lower()
    c = _constraint("handlers-must-remain-stateless")
    assert c is not None


def test_add_constraint_duplicate_description_is_skipped(project_dir: Path) -> None:
    desc = "Handlers must remain stateless"
    _ok("add", "constraint", desc, "-e", "src/api.py")
    result = _ok("add", "constraint", desc, "-e", "src/api.py")
    output = _text(result)
    assert "skipped" in output.lower()
    assert "duplicate" in output.lower()


def test_add_constraint_decision_id_links_constraint_to_decision(
    project_dir: Path,
) -> None:
    _ok(
        "add", "decision", "Adopt Postgres", "-r", "Relational integrity beats document stores here"
    )
    _ok(
        "add",
        "constraint",
        "Schema migrations are mandatory",
        "-e",
        "src/db.py",
        "-d",
        "adopt-postgres",
    )
    c = _constraint("schema-migrations-are-mandatory")
    assert c is not None
    assert c.enforced_by == "adopt-postgres"


def test_add_constraint_dangling_decision_id_fails_cleanly(
    project_dir: Path,
) -> None:
    """FK integrity: a -d referencing no decision errors without traceback."""
    result = _run(
        "add", "constraint", "Orphan rule here", "-e", "src/x.py", "-d", "no-such-decision"
    )
    output = _text(result)
    assert result.exit_code == 1
    assert "Error" in output
    assert "Traceback" not in output


def test_failed_readd_does_not_orphan_tombstone(project_dir: Path) -> None:
    """A constraint that survives a failed re-add stays live.

    Regression: the tombstone used to be committed before the successor
    insert, so a typo'd --decision-id on a re-add killed the original
    entry with no replacement and no revive command.
    """
    _ok("add", "constraint", "Schema migrations are mandatory", "-e", "src/db.py")
    result = _run(
        "add",
        "constraint",
        "Schema migrations must run before deploys",
        "--id",
        "schema-migrations-are-mandatory",
        "-e",
        "src/db.py",
        "-d",
        "no-such-decision",
    )
    output = _text(result)
    assert result.exit_code == 1
    assert "does not exist" in output
    # Original entry is untouched: live, not a tombstone.
    store = _store()
    try:
        assert store.is_deprecated("constraint", "schema-migrations-are-mandatory") is False
        surviving = store.get_constraint("schema-migrations-are-mandatory")
        assert surviving is not None
        assert surviving.description == "Schema migrations are mandatory"
    finally:
        store.close()
    listing = _ok("list", "constraints")
    assert "Schema migrations are mandatory" in _text(listing)


def test_deprecate_decision_by_slug_marks_it_deprecated(project_dir: Path) -> None:
    _ok(
        "add",
        "decision",
        "Use Monolith",
        "-r",
        "Single deployable keeps operations simple early on",
    )
    result = _ok("deprecate", "use-monolith", "-r", "Moved to services")
    assert "Deprecated use-monolith: Moved to services" in _text(result)
    listing = _ok("list", "decisions", "--status", "deprecated")
    assert "use-monolith" in _text(listing)


def test_deprecate_nonexistent_id_fails_cleanly(project_dir: Path) -> None:
    result = _run("deprecate", "no-such-id")
    assert result.exit_code == 1
    assert "not found" in _text(result).lower()


def test_deprecate_already_deprecated_is_graceful(project_dir: Path) -> None:
    _ok(
        "add",
        "decision",
        "Use Monolith",
        "-r",
        "Single deployable keeps operations simple early on",
    )
    _ok("deprecate", "use-monolith")
    result = _ok("deprecate", "use-monolith")
    assert "Skipped use-monolith: already deprecated" in _text(result)
    assert result.exit_code == 0


def test_deprecate_reports_each_result_and_summary(project_dir: Path) -> None:
    """Per-result lines replace the old count-only summary; exit stays 0."""
    _ok("add", "decision", "Alpha Rule", "-r", "Alpha rationale")
    _ok("add", "decision", "Beta Rule", "-r", "Beta rationale")
    result = _run("deprecate", "alpha-rule", "ghost-id", "beta-rule")
    output = _text(result)
    assert "Deprecated alpha-rule." in output
    assert "Deprecated beta-rule." in output
    assert "Error: ghost-id not found" in output
    assert "Summary: 2 deprecated, 1 not found" in output
    assert result.exit_code == 1


def test_deprecate_single_id_skips_summary(project_dir: Path) -> None:
    """A single-ID deprecate prints one line and no summary."""
    _ok("add", "decision", "Solo Rule", "-r", "Solo rationale")
    result = _ok("deprecate", "solo-rule", "-r", "obsolete")
    output = _text(result)
    assert "Deprecated solo-rule: obsolete" in output
    assert "Summary:" not in output
    assert result.exit_code == 0


def test_deprecate_reason_with_markup_characters_renders_literally(
    project_dir: Path,
) -> None:
    """Bracketed reasons print as typed, without Rich escape backslashes."""
    _ok("add", "decision", "Bracket Rule", "-r", "Bracket rationale")
    result = _ok("deprecate", "bracket-rule", "-r", "superseded by [v2] plan")
    output = _text(result)
    assert "superseded by [v2] plan" in output
    assert "\\[" not in output


def test_deprecate_unknown_id_with_markup_characters_renders_literally(
    project_dir: Path,
) -> None:
    """A missing ID with brackets prints as typed on stderr."""
    result = _run("deprecate", "[v2]")
    output = _text(result)
    assert "Error: [v2] not found" in output
    assert "\\[" not in output
    assert result.exit_code == 1


def test_query_search_no_matches_prints_sentence(project_dir: Path) -> None:
    """An empty search prints the echo line and a sentence, not an empty panel."""
    result = _ok("query", "search", "zzqxv")
    output = _text(result)
    assert 'No matches for "zzqxv".' in output
    assert "Search:" not in output


def test_status_reports_empty_graph_as_ok(project_dir: Path) -> None:
    """A freshly initialized graph passes the status check."""
    result = _ok("status")
    output = _text(result)
    assert "2/2 checks passed" in output
    assert "[FAIL]" not in output


def test_status_reports_graph_path_relative_to_cwd(project_dir: Path) -> None:
    """status names the graph relative to the working directory."""
    output = _text(_ok("status"))
    assert ".mementic/graph.db" in output
    assert project_dir.as_posix() not in output


def test_history_marks_empty_reason(project_dir: Path) -> None:
    """A tombstone without a reason shows a placeholder instead of a blank cell."""
    _ok("add", "decision", "Reasonless Rule", "-r", "Reasonless rationale")
    _ok("deprecate", "reasonless-rule")
    result = _ok("history", "reasonless-rule")
    output = _text(result)
    assert "no reason recorded" in output
    assert "2026-" in output  # timestamp uses the log format, not raw ISO
    assert "At (UTC)" in output


def test_entities_mark_live_and_dead_paths(project_dir: Path) -> None:
    """The entities list marks each path with the status icon pattern."""
    target = project_dir / "src" / "live.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1", encoding="utf-8")
    _ok("add", "decision", "Live Binding", "-r", "Live rationale", "-e", "src/live.py")
    _ok("add", "decision", "Dead Binding", "-r", "Dead rationale", "-e", "src/gone.py")
    output = _text(_ok("list", "entities"))
    assert "[OK]" in output
    assert "[FAIL]" in output


def test_log_title_shows_shown_of_total(project_dir: Path) -> None:
    """The log panel title reports shown entries against the total."""
    _ok("add", "decision", "Logged Rule", "-r", "Logged rationale")
    output = _text(_ok("log"))
    assert "Audit log (1 of 1)" in output


def test_log_title_counts_total_when_limit_applies(project_dir: Path) -> None:
    """A limited log run reports shown against total, not against the limit."""
    _ok("add", "decision", "Alpha Rule", "-r", "Alpha rationale")
    _ok("add", "decision", "Beta Rule", "-r", "Beta rationale")
    output = _text(_ok("log", "-n", "1"))
    assert "Audit log (1 of 2)" in output


def test_status_lists_entry_counts(project_dir: Path) -> None:
    """After entries are added the Graph hint shows the counts."""
    _ok("add", "decision", "Counted Rule", "-r", "Counted rationale")
    result = _ok("status")
    output = _text(result)
    assert "1 decisions" in output


def test_status_reports_binding_staleness_counts(project_dir: Path) -> None:
    """The Graph hint counts changed and missing bindings without a third check."""
    live = project_dir / "src" / "live.py"
    gone = project_dir / "src" / "gone.py"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"x = 1\n")
    gone.write_bytes(b"x = 1\n")
    _ok("add", "constraint", "Live File Rule", "-e", "src/live.py")
    _ok("add", "constraint", "Gone File Rule", "-e", "src/gone.py")
    live.write_bytes(b"x = 2\n")
    gone.unlink()
    output = _text(_ok("status"))
    assert "bindings: 1 missing, 1 changed, 0 unknown" in output
    assert "2/2 checks passed" in output


def test_query_keywords_returns_matching_seeded_entries(project_dir: Path) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt authentication guards every endpoint")
    _ok("add", "constraint", "Sessions must stay stateless", "-e", "src/session.py")
    result = _ok("query", "keywords", "jwt", "authentication", "stateless")
    output = _text(result)
    assert "use-jwt-auth" in output
    assert "sessions-must-stay-stateless" in output


def test_query_keywords_nonsense_returns_no_matches_without_crash(
    project_dir: Path,
) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt authentication guards every endpoint")
    result = _ok("query", "keywords", "zzqxv", "wblrmp")
    output = _text(result)
    assert "use-jwt-auth" not in output


def test_query_keywords_max_tokens_override_truncates(project_dir: Path) -> None:
    """--max-tokens overrides both section budgets proportionally (Spec 30)."""
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt tokens carry authentication claims")
    _ok("add", "decision", "Jwt Refresh Flow", "-r", "Refresh tokens rotate jwt sessions nightly")
    _ok(
        "add",
        "decision",
        "Jwt Clock Skew",
        "-r",
        "Jwt token validation tolerates minor clock skew only",
    )
    _ok(
        "add",
        "decision",
        "Jwt Key Rotation",
        "-r",
        "Rotate jwt signing tokens before the quarterly audit window",
    )
    _ok(
        "add",
        "decision",
        "Jwt Audience Binding",
        "-r",
        "Every jwt token must pin its audience claim to one service",
    )
    _ok(
        "add",
        "decision",
        "Unrelated Note",
        "-r",
        "Completely different filler topic for corpus diversity",
    )
    full = _ok("query", "keywords", "jwt", "tokens")
    tiny = _ok("query", "keywords", "jwt", "tokens", "--max-tokens", "50")
    full_text = _text(full)
    tiny_text = _text(tiny)
    # Both runs honor the [mementic: ...] echo contract.
    for text in (full_text, tiny_text):
        assert "[mementic:" in text
    # The tiny budget renders fewer pack items than the default budgets
    # even with the min-shown floor: five long jwt decisions cannot all fit.
    assert tiny_text.count("(#") < full_text.count("(#")
    assert "below threshold" in tiny_text


def test_query_keywords_rejects_nonpositive_max_tokens(
    project_dir: Path,
) -> None:
    """--max-tokens < 1 exits non-zero with an actionable message."""
    result = _run("query", "keywords", "jwt", "--max-tokens", "0")
    assert result.exit_code != 0
    output = _text(result)
    assert "--max-tokens" in output
    assert "at least 1" in output


def test_query_file_rejects_nonpositive_max_tokens(project_dir: Path) -> None:
    """--max-tokens < 1 exits non-zero before opening the store."""
    result = _run("query", "file", "src/auth.py", "--max-tokens", "0")
    assert result.exit_code != 0
    assert "--max-tokens" in _text(result)


def test_query_file_finds_seeded_entry_by_path(project_dir: Path) -> None:
    source = project_dir / "src" / "foo.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1", encoding="utf-8")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/foo.py",
    )
    result = _ok("query", "file", "src/foo.py")
    assert "track-auth-flow" in _text(result)


def test_query_file_unrelated_path_returns_empty(project_dir: Path) -> None:
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/foo.py",
    )
    result = _ok("query", "file", "src/unrelated.py")
    assert result.exit_code == 0
    assert "track-auth-flow" not in _text(result)


def test_query_search_finds_seeded_content_substring(project_dir: Path) -> None:
    _ok("add", "constraint", "Handlers must remain stateless", "-e", "src/api.py")
    result = _ok("query", "search", "stateless")
    output = _text(result)
    assert "handlers-must-remain-stateless" in output
    # No matching decisions -> the Decisions section is omitted entirely.
    assert "Decisions (" not in output


def test_query_stats_runs_clean_on_seeded_graph(project_dir: Path) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt authentication guards every endpoint")
    result = _ok("query", "stats")
    assert "Graph statistics" in _text(result)


def test_cli_command_closes_its_store(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[MementicStore] = []
    original_close = MementicStore.close

    def tracking_close(self: MementicStore) -> None:
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(MementicStore, "close", tracking_close)
    _ok("query", "stats")
    assert closed


def test_list_decisions_shows_seeded_entry(project_dir: Path) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt authentication guards every endpoint")
    result = _ok("list", "decisions")
    assert "use-jwt-auth" in _text(result)


def test_list_constraints_shows_seeded_entry(project_dir: Path) -> None:
    _ok("add", "constraint", "Handlers must remain stateless", "-e", "src/api.py")
    result = _ok("list", "constraints")
    assert "handlers-must-remain-stateless" in _text(result)


def test_list_entities_shows_entity_created_via_add(project_dir: Path) -> None:
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/foo.py",
    )
    result = _ok("list", "entities")
    assert "src/foo.py" in _text(result)


def test_list_rejects_invalid_status(project_dir: Path) -> None:
    result = _run("list", "decisions", "--status", "bogus")
    assert result.exit_code == 1
    assert "Invalid status" in _text(result)


def test_list_decisions_status_filter_excludes_deprecated(project_dir: Path) -> None:
    _ok(
        "add",
        "decision",
        "Keep Api Stable",
        "-r",
        "Public contract changes require a major version bump",
    )
    _ok(
        "add",
        "decision",
        "Drop Legacy Auth",
        "-r",
        "Legacy basic auth is replaced by token sessions",
    )
    _ok("deprecate", "drop-legacy-auth")
    result = _ok("list", "decisions", "--status", "accepted")
    output = _text(result)
    assert "keep-api-stable" in output
    assert "drop-legacy-auth" not in output


def test_status_healthy_bundle_exits_zero_with_checklist(project_dir: Path) -> None:
    result = _ok("status")
    output = _text(result)
    assert "Status" in output
    assert "checks passed" in output


def test_status_missing_agents_md_reports_failure(project_dir: Path) -> None:
    (project_dir / "AGENTS.md").unlink()
    result = _ok("status")
    assert "No enforcement section" in _text(result)


def test_status_corrupt_store_reports_failure(project_dir: Path) -> None:
    (project_dir / ".mementic" / "graph.db").write_bytes(b"not a database")
    result = _ok("status")
    assert "Could not load graph" in _text(result)


def test_status_v5_graph_reports_schema_mismatch(project_dir: Path) -> None:
    """An existing v5 graph explains the version bump instead of claiming no graph."""
    db = project_dir / ".mementic" / "graph.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
    finally:
        conn.close()
    output = _text(_ok("status"))
    assert "schema v5" in output
    assert "requires v6" in output
    assert "Run 'mementic init' here" not in output
    assert "1/2 checks passed" in output


def test_log_lists_changes_newest_first(project_dir: Path) -> None:
    _ok("add", "decision", "Alpha Decision", "-r", "First recorded architectural choice")
    _ok("add", "decision", "Beta Decision", "-r", "Second recorded architectural choice")
    result = _ok("log")
    output = _text(result)
    assert "beta-decision" in output
    assert "alpha-decision" in output
    assert output.index("beta-decision") < output.index("alpha-decision")
    assert "Timestamp (UTC)" in output


def test_log_without_entries_prints_hint_and_exits_zero(project_dir: Path) -> None:
    result = _ok("log")
    assert "No changes recorded yet." in _text(result)


def test_unknown_command_exits_nonzero() -> None:
    result = _run("frobnicate")
    assert result.exit_code != 0


@pytest.mark.parametrize(
    "args",
    [
        ("add", "decision"),
        ("add", "decision", "Title Only"),
        ("add", "constraint"),
        ("query", "file"),
        ("query", "keywords"),
        ("deprecate"),
    ],
    ids=[
        "decision-no-title",
        "decision-no-rationale",
        "constraint-no-description",
        "file-no-path",
        "keywords-no-keywords",
        "deprecate-no-id",
    ],
)
def test_missing_required_arguments_exit_nonzero(
    project_dir: Path,
    args: tuple[str, ...],
) -> None:
    result = _run(*args)
    assert result.exit_code != 0


def test_cross_type_slug_collision_exits_cleanly(project_dir: Path) -> None:
    """A constraint whose slug collides with a decision errors, no traceback."""
    _ok("add", "decision", "Use Postgres Now", "-r", "Proven reliability")
    result = _run("add", "constraint", "Use Postgres Now!", "-e", "src/db.py")
    output = _text(result)
    assert result.exit_code == 1
    assert "Error" in output
    assert "Traceback" not in output


def test_readd_after_deprecate_replaces_tombstone(project_dir: Path) -> None:
    """Deprecated entries never block re-adding; the add replaces them."""
    _ok("add", "decision", "Use Monolith", "-r", "Single deployable simplifies operations")
    _ok("deprecate", "use-monolith")
    result = _ok("add", "decision", "Use Monolith", "-r", "Single deployable simplifies operations")
    output = _text(result)
    assert "replaced deprecated" in output.lower()
    d = _decision("use-monolith")
    assert d is not None
    store = _store()
    try:
        assert {x.id for x in store.get_live_decisions()} == {"use-monolith"}
    finally:
        store.close()


def test_empty_rationale_readd_is_skipped_as_duplicate(project_dir: Path) -> None:
    """Two empty rationales overlap 1.0 → second is a duplicate skip."""
    _ok("add", "decision", "Empty Rationale Case", "-r", "")
    result = _ok("add", "decision", "Empty Rationale Case", "-r", "")
    assert "skipped" in _text(result).lower()


@pytest.mark.parametrize(
    "rationale",
    [
        "使用 JWT 进行身份验证 claims 🎫",
        "Ünïcödé rätionäle with accented characters",
    ],
)
def test_unicode_rationale_round_trips_through_add_and_search(
    project_dir: Path,
    rationale: str,
) -> None:
    """Non-ASCII rationales survive add and are findable via search."""
    _ok("add", "decision", "Unicode Auth", "-r", rationale)
    result = _ok("query", "search", rationale.split()[0])
    assert "Unicode Auth" in _text(result)


def test_query_keywords_prints_mementic_summary_line(project_dir: Path) -> None:
    _ok("add", "decision", "Use Jwt Auth", "-r", "Jwt tokens carry authentication claims")
    # A second unrelated entry keeps the query keyword rare, so its IDF is
    # positive and the pack has a real match to report (Spec 30 Phase 1).
    _ok(
        "add",
        "decision",
        "Unrelated Note",
        "-r",
        "Completely different filler topic for corpus diversity",
    )
    result = _ok("query", "keywords", "jwt", "auth")
    text = _text(result)
    line = next((ln for ln in text.splitlines() if "[mementic:" in ln), None)
    assert line is not None, f"no [mementic: ...] summary line in:\n{text}"
    assert re.search(
        r"\d+ of \d+ constraints, \d+ of \d+ decisions \| .+",
        line,
    ), f"summary line lacks compact shown/matched format: {line}"
    assert "1 of 1 decisions" in line


def test_query_file_prints_mementic_summary_line(project_dir: Path) -> None:
    (project_dir / "src").mkdir()
    (project_dir / "src" / "auth.py").write_text("# auth module\n")
    _ok(
        "add",
        "decision",
        "Guard Auth Module",
        "-r",
        "All requests pass through the auth module",
        "-e",
        "src/auth.py",
    )
    result = _ok("query", "file", "src/auth.py")
    text = _text(result)
    line = next((ln for ln in text.splitlines() if "[mementic:" in ln), None)
    assert line is not None, f"no [mementic: ...] summary line in:\n{text}"
    assert re.search(r"\d+ constraints, \d+ decisions \| src/auth\.py\]", line), (
        f"file summary line lacks compact format: {line}"
    )


def test_query_search_prints_mementic_summary_line(project_dir: Path) -> None:
    _ok("add", "constraint", "Handlers must remain stateless", "-e", "src/api.py")
    result = _ok("query", "search", "stateless")
    text = _text(result)
    line = next((ln for ln in text.splitlines() if "[mementic:" in ln), None)
    assert line is not None, f"no [mementic: ...] summary line in:\n{text}"
    assert re.search(r"\d+ constraints, \d+ decisions \| stateless\]", line), (
        f"search summary line lacks compact format: {line}"
    )


def test_history_without_id_lists_full_table_newest_first(
    project_dir: Path,
) -> None:
    _ok("add", "decision", "First Entry", "-r", "Alpha rationale for history")
    _ok("add", "decision", "Second Entry", "-r", "Beta rationale for history")
    _ok("deprecate", "first-entry", "-r", "obsolete now")
    _ok("deprecate", "second-entry", "-r", "also obsolete")
    output = _text(_ok("history"))
    assert "Deprecation history" in output
    assert "second-entry" in output and "first-entry" in output
    # Newest first: the later tombstone appears before the earlier one.
    assert output.index("second-entry") < output.index("first-entry")


def test_history_with_id_shows_reason_and_successor_chain(
    project_dir: Path,
) -> None:
    _ok("add", "decision", "Legacy Auth", "-r", "Basic auth guards every endpoint")
    _ok("add", "decision", "Token Auth", "-r", "Signed tokens replace basic auth entirely")
    _ok("deprecate", "legacy-auth", "-r", "insecure scheme")
    output = _text(_ok("history", "legacy-auth"))
    assert "legacy-auth" in output
    assert "insecure scheme" in output  # the written reason is echoed back
    assert "none" in output.lower()  # replaced_by NULL renders as none


def test_history_transitive_chain_prints_arrows(project_dir: Path) -> None:
    """Re-add cycles accumulate tombstone rows; history lists them all."""
    _ok("add", "decision", "Cache Layer", "-r", "In-process caching serves reads")
    _ok("deprecate", "cache-layer", "-r", "v1 out")
    # Re-add same slug: replace-path succession tombstone.
    _ok("add", "decision", "Cache Layer", "-r", "Distributed cache tier replaces process memory")
    output = _text(_ok("history", "cache-layer"))
    assert "cache-layer" in output
    assert "v1 out" in output  # prior incarnation's reason still visible
    assert "superseded by re-added entry" in output


def test_history_unknown_id_exits_zero_with_message(project_dir: Path) -> None:
    output = _text(_ok("history", "no-such-id"))
    assert "no-such-id" in output


def test_deprecated_entry_excluded_from_context_pack(project_dir: Path) -> None:
    """Derived liveness: tombstoned entries never reach keyword packs."""
    _ok("add", "decision", "Zebra Protocol", "-r", "Zebra routing handles every packet flow")
    _ok("deprecate", "zebra-protocol", "-r", "superseded")
    result = _ok("query", "keywords", "zebra")
    output = _text(result)
    assert "zebra-protocol" not in output
    # Compact echo (Spec 33 Phase 1): nothing live, nothing shown.
    assert "[mementic: 0 of 0 constraints, 0 of 0 decisions | zebra]" in output


def test_add_constraint_records_content_hash(project_dir: Path) -> None:
    """The write path records the sha256 of the bound file's bytes."""
    target = project_dir / "src" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"def handler():\n    return 1\n")
    _ok("add", "constraint", "Handlers return ints", "-e", "src/auth.py")
    store = _store()
    try:
        bindings = store.get_path_bindings("src/auth.py")
    finally:
        store.close()
    assert [b.content_hash for b in bindings] == [hashlib.sha256(target.read_bytes()).hexdigest()]


def _bind_constraint_to_existing_file(project_dir: Path) -> Path:
    """Create src/auth.py, bind a constraint to it, and return the file path."""
    target = project_dir / "src" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"def handler():\n    return 1\n")
    _ok("add", "constraint", "Rules must hold", "-e", "src/auth.py")
    return target


def test_query_file_marks_changed_binding_stale(project_dir: Path) -> None:
    target = _bind_constraint_to_existing_file(project_dir)
    target.write_bytes(b"def handler():\n    return 2\n")
    output = _text(_ok("query", "file", "src/auth.py", "--compact"))
    assert (
        "- Rules must hold (#rules-must-hold, bound: src/auth.py) [stale: src/auth.py changed]"
        in output
    )


def test_query_file_marks_missing_binding_stale(project_dir: Path) -> None:
    target = _bind_constraint_to_existing_file(project_dir)
    target.unlink()
    output = _text(_ok("query", "file", "src/auth.py", "--compact"))
    assert "rules-must-hold" in output
    assert (
        "- Rules must hold (#rules-must-hold, bound: src/auth.py) [stale: src/auth.py missing]"
        in output
    )


def test_query_file_unchanged_binding_has_no_marker(project_dir: Path) -> None:
    _bind_constraint_to_existing_file(project_dir)
    output = _text(_ok("query", "file", "src/auth.py", "--compact"))
    assert "rules-must-hold" in output
    assert "[stale:" not in output


def test_query_file_crlf_binding_is_not_stale(project_dir: Path) -> None:
    """CRLF bytes hash identically at write and read time, so no stale marker appears."""
    target = project_dir / "src" / "crlf.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"a = 1\r\nb = 2\r\n")
    _ok("add", "constraint", "CRLF rule", "-e", "src/crlf.py")
    output = _text(_ok("query", "file", "src/crlf.py", "--compact"))
    assert "crlf-rule" in output
    assert "[stale:" not in output


def test_query_file_json_does_not_hash_bindings(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON mode never classifies bindings, so it performs no file hashing."""
    target = project_dir / "src" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"def handler():\n    return 1\n")
    _ok("add", "constraint", "Rules must hold", "-e", "src/auth.py")
    calls: list[tuple[object, ...]] = []

    def record(*args: object, **kwargs: object) -> list:
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(staleness, "classify_bindings", record)
    result = _ok("query", "file", "src/auth.py", "--json")
    data = json.loads(_text(result))
    assert calls == []
    assert set(data) == {"summary", "file", "decisions", "constraints"}
    assert "[stale:" not in _text(result)


def test_query_file_directory_binding_has_no_marker(project_dir: Path) -> None:
    """Directory bindings record no hash, so they never render a stale marker."""
    (project_dir / "src" / "api").mkdir(parents=True)
    _ok("add", "constraint", "Api rules apply", "-e", "src/api")
    output = _text(_ok("query", "file", "src/api", "--compact"))
    assert "api-rules-apply" in output
    assert "[stale:" not in output


def test_stale_binding_keeps_exit_code_zero(project_dir: Path) -> None:
    target = _bind_constraint_to_existing_file(project_dir)
    target.write_bytes(b"def handler():\n    return 3\n")
    result = _run("query", "file", "src/auth.py", "--compact")
    output = _text(result)
    assert result.exit_code == 0
    assert "[stale: src/auth.py changed]" in output


def test_query_file_does_not_mark_decisions(project_dir: Path) -> None:
    """Decision lines never carry a stale marker, even when their file changed."""
    target = project_dir / "src" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"def handler():\n    return 1\n")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/auth.py",
    )
    target.write_bytes(b"def handler():\n    return 2\n")
    output = _text(_ok("query", "file", "src/auth.py", "--compact"))
    assert "track-auth-flow" in output
    assert "[stale:" not in output


def _create_files(project_dir: Path, *names: str) -> None:
    """Create placeholder ``src/<name>`` files so batch matches stay live."""
    for name in names:
        source = project_dir / "src" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("x = 1", encoding="utf-8")


def test_query_files_two_paths_merges_shared_rule_once(project_dir: Path) -> None:
    """A rule bound to both paths renders once, naming both matches."""
    _create_files(project_dir, "a.py", "b.py")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/a.py",
        "-e",
        "src/b.py",
    )
    text = _text(_ok("query", "files", "src/a.py", "src/b.py", "--compact"))
    assert text.count("(#track-auth-flow)") == 1
    assert "(matched: src/a.py, src/b.py)" in text


def test_query_files_ten_paths_returns_merged_pack(project_dir: Path) -> None:
    """Ten paths produce one pack, one summary line, and every decision."""
    names = [f"f{index}.py" for index in range(1, 11)]
    _create_files(project_dir, *names)
    for index in range(1, 11):
        _ok(
            "add",
            "decision",
            f"Track F{index}",
            "-r",
            f"Rationale for file number {index}",
            "-e",
            f"src/f{index}.py",
        )
    text = _text(_ok("query", "files", *(f"src/{name}" for name in names), "--compact"))
    for index in range(1, 11):
        assert f"(#track-f{index})" in text
    assert "## Files queried (10)" in text
    summary = [line for line in text.splitlines() if "[mementic:" in line]
    assert len(summary) == 1
    assert "10 decisions | 10 paths: src/f1.py, src/f2.py, src/f3.py (+7 more)]" in summary[0]


def test_query_files_no_rule_path_reported(project_dir: Path) -> None:
    """A batch path that matches nothing is listed under its own heading."""
    _create_files(project_dir, "a.py", "other.py")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/a.py",
    )
    result = _run("query", "files", "src/a.py", "src/other.py", "--compact")
    assert result.exit_code == 0
    _, unmatched_section = _text(result).split("### No rules matched", 1)
    assert "- src/other.py" in unmatched_section


def test_query_files_duplicate_path_counted_once(project_dir: Path) -> None:
    """A repeated path collapses to one entry before the store is queried."""
    _create_files(project_dir, "a.py")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/a.py",
    )
    text = _text(_ok("query", "files", "src/a.py", "./src/a.py", "--compact"))
    assert "## Files queried (1)" in text
    assert text.count("1 path: src/a.py") == 1


def test_query_files_prints_mementic_summary_line(project_dir: Path) -> None:
    """The merged pack reports counts and paths in the single-path format."""
    _create_files(project_dir, "a.py", "b.py")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/a.py",
        "-e",
        "src/b.py",
    )
    text = _text(_ok("query", "files", "src/a.py", "src/b.py", "--compact"))
    line = next((ln for ln in text.splitlines() if "[mementic:" in ln), None)
    assert line is not None, f"no [mementic: ...] summary line in:\n{text}"
    assert re.search(r"\d+ constraints, \d+ decisions \| 2 paths: src/a\.py, src/b\.py\]", line), (
        f"batch summary line lacks compact format: {line}"
    )


def test_query_files_opens_store_once(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten paths open the graph once instead of reopening it per path."""
    from mementic.cli import context as cli_context
    from mementic.paths import ProjectPaths

    names = [f"f{index}.py" for index in range(1, 11)]
    _create_files(project_dir, *names)
    opens: list[int] = []
    original = cli_context.resolve_store

    def counting_resolve_store(paths: ProjectPaths | None = None) -> MementicStore:
        opens.append(1)
        return original(paths)

    monkeypatch.setattr("mementic.cli.context.resolve_store", counting_resolve_store)
    _ok("query", "files", *(f"src/{name}" for name in names), "--compact")
    assert len(opens) == 1


def test_query_files_rejects_foreign_graph_path(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One foreign path refuses the whole batch before any store opens."""
    from mementic.cli import context as cli_context
    from mementic.paths import ProjectPaths

    _create_files(project_dir, "a.py")
    sibling = project_dir / "repo-b"
    sibling.mkdir()
    monkeypatch.chdir(sibling)
    _ok("init")
    monkeypatch.chdir(project_dir)
    opens: list[int] = []
    original = cli_context.resolve_store

    def counting_resolve_store(paths: ProjectPaths | None = None) -> MementicStore:
        opens.append(1)
        return original(paths)

    monkeypatch.setattr("mementic.cli.context.resolve_store", counting_resolve_store)
    result = _run("query", "files", "src/a.py", "repo-b/src/other.py")
    assert result.exit_code == 1
    text = _text(result)
    assert "Run: cd" in text
    assert "repo-b" in text
    assert "[mementic:" not in text
    assert opens == []


def test_query_files_single_path_returns_merged_pack(project_dir: Path) -> None:
    """One path is a valid batch and renders the merged-pack heading."""
    _create_files(project_dir, "a.py")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/a.py",
    )
    result = _run("query", "files", "src/a.py", "--compact")
    assert result.exit_code == 0
    assert "## Files queried (1)" in _text(result)


def test_query_files_requires_at_least_one_path(project_dir: Path) -> None:
    """The variadic argument is required, so an empty batch is a usage error."""
    result = _run("query", "files")
    assert result.exit_code == 2
    assert "Missing argument" in _text(result)


def test_query_files_rejects_nonpositive_max_tokens(
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--max-tokens < 1 exits non-zero before opening the store."""
    from mementic.cli import context as cli_context
    from mementic.paths import ProjectPaths

    opens: list[int] = []
    original = cli_context.resolve_store

    def counting_resolve_store(paths: ProjectPaths | None = None) -> MementicStore:
        opens.append(1)
        return original(paths)

    monkeypatch.setattr("mementic.cli.context.resolve_store", counting_resolve_store)
    result = _run("query", "files", "src/a.py", "--max-tokens", "0")
    assert result.exit_code != 0
    assert "--max-tokens" in _text(result)
    assert opens == []


def test_query_files_default_output_uses_panel(project_dir: Path) -> None:
    """Without --compact the merged pack renders through the console helpers."""
    _create_files(project_dir, "a.py", "b.py")
    _ok(
        "add",
        "decision",
        "Track Auth Flow",
        "-r",
        "Central middleware owns the auth handshake",
        "-e",
        "src/a.py",
        "-e",
        "src/b.py",
    )
    text = _text(_ok("query", "files", "src/a.py", "src/b.py"))
    assert "Files: 2 paths" in text


def test_query_files_marks_changed_binding_stale(project_dir: Path) -> None:
    """A stale binding keeps its marker after batching, at the line end."""
    target = _bind_constraint_to_existing_file(project_dir)
    target.write_bytes(b"def handler():\n    return 2\n")
    output = _text(_ok("query", "files", "src/auth.py", "--compact"))
    assert (
        "- Rules must hold (#rules-must-hold, bound: src/auth.py) (matched: src/auth.py)"
        " [stale: src/auth.py changed]"
    ) in output


def test_query_files_missing_binding_keeps_stale_marker(project_dir: Path) -> None:
    """A deleted bound file keeps its rule and marker, agreeing with query file."""
    target = _bind_constraint_to_existing_file(project_dir)
    target.unlink()
    single = _text(_ok("query", "file", "src/auth.py", "--compact"))
    batch = _text(_ok("query", "files", "src/auth.py", "--compact"))
    for output in (single, batch):
        assert "[stale: src/auth.py missing]" in output
    assert "rules-must-hold" in batch
    assert "### No rules matched" not in batch


def test_query_files_rule_bound_to_directory_and_files_appears_once(project_dir: Path) -> None:
    """A rule bound to a directory and two files renders once with both matches."""
    _create_files(project_dir, "a.py", "b.py")
    _ok(
        "add",
        "decision",
        "Dir Rule",
        "-r",
        "Applies to the tree and both files",
        "-e",
        "src",
        "-e",
        "src/a.py",
        "-e",
        "src/b.py",
    )
    text = _text(_ok("query", "files", "src/a.py", "src/b.py", "--compact"))
    assert text.count("(#dir-rule)") == 1
    assert "(matched: src/a.py, src/b.py)" in text
    assert "\n- src\n" not in text
    assert "2 paths" in text
