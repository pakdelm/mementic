"""End-to-end tests for the `mementic check` command.

Every test drives the real Typer app through ``typer.testing.CliRunner``
against a temporary Git repository that owns its own ``.mementic/graph.db``.
The command reads the process working directory, so each test chdirs into
the fixture repository before invoking the CLI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from mementic.cli import app

runner = CliRunner()

_CONSOLE_ENV = {"COLUMNS": "160"}

_GIT_IDENTITY = (
    "-c",
    "core.hooksPath=",
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.com",
    "-c",
    "commit.gpgsign=false",
)


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


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force rich's render width to 160 so tables never ellipsize assertions."""
    monkeypatch.setattr("mementic.console.console._width", 160)


def _run(*args: str) -> Result:
    """Invoke the CLI app with the given argument vector."""
    return runner.invoke(app, list(args), env=_CONSOLE_ENV)


def _ok(*args: str) -> Result:
    """Invoke the CLI app, asserting a zero exit code."""
    result = _run(*args)
    assert result.exit_code == 0, f"command {list(args)} failed:\n{_text(result)}"
    return result


def _git(repo: Path, *args: str) -> None:
    """Run Git in ``repo`` without touching the user's configuration."""
    subprocess.run(
        ["git", *_GIT_IDENTITY, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an empty Git repository and run the CLI from inside it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _git(repo, "init", "-q")
    return repo


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git repository with an initialized graph and a committed baseline."""
    repo = _git_repo(tmp_path, monkeypatch)
    _ok("init")
    _git(repo, "add", "AGENTS.md", ".gitignore")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _commit(repo: Path, relative: str, content: str) -> None:
    """Write ``relative`` with ``content`` and commit it."""
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", f"add {relative}")


def _add_rule(
    description: str,
    entity: str,
    *,
    rule_id: str,
    decision: str | None = None,
) -> None:
    """Seed a constraint, and its enforcing decision when one is given."""
    args = ["add", "constraint", description, "-e", entity, "--id", rule_id]
    if decision is not None:
        _ok("add", "decision", decision, "-r", f"Rationale for {decision}")
        args += ["-d", decision.lower().replace(" ", "-")]
    _ok(*args)


def test_check_lists_constraint_for_working_tree_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unstaged edit lists the rules bound to the changed file."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/auth.py", "x = 1\n")
    _add_rule("Auth handlers must validate tokens", "src/auth.py", rule_id="validate-tokens")
    (repo / "src" / "auth.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check"))

    assert "validate-tokens" in output
    assert "src/auth.py" in output
    assert "[mementic: 1 constraints | 1 changed files]" in output


def test_check_lists_constraint_for_staged_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged edit lists the rules bound to the changed file."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/auth.py", "x = 1\n")
    _add_rule("Auth handlers must validate tokens", "src/auth.py", rule_id="validate-tokens")
    (repo / "src" / "auth.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "src/auth.py")

    output = _text(_ok("check"))

    assert "validate-tokens" in output


def test_check_matches_renamed_file_on_old_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rule bound to the pre-rename path matches the rename."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/old.py", "x = 1\n")
    _add_rule("Legacy module must stay importable", "src/old.py", rule_id="legacy-importable")
    _git(repo, "mv", "src/old.py", "src/new.py")

    output = _text(_ok("check"))

    assert "legacy-importable" in output
    assert "renamed from" in output


def test_check_matches_renamed_file_on_new_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rule bound to the post-rename path matches the rename."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/old.py", "x = 1\n")
    _add_rule("Current module must stay importable", "src/new.py", rule_id="current-importable")
    _git(repo, "mv", "src/old.py", "src/new.py")

    output = _text(_ok("check"))

    assert "current-importable" in output
    assert "renamed from" in output


def test_check_matches_deleted_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deleted file keeps its path and still matches its rules."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/gone.py", "x = 1\n")
    _add_rule("Removals must be audited", "src/gone.py", rule_id="audit-removal")
    _git(repo, "rm", "-q", "src/gone.py")

    output = _text(_ok("check"))

    assert "audit-removal" in output
    assert "src/gone.py" in output


def test_check_matches_directory_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rule bound to a directory matches a file changed inside it."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/auth.py", "x = 1\n")
    _add_rule("Source modules stay importable", "src", rule_id="modules-importable")
    (repo / "src" / "auth.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check"))

    assert "modules-importable" in output
    assert "src/auth.py" in output


def test_check_shows_long_rename_label_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long rename label renders the new and old path in full."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "mementic/cli/old_verify_name.py", "x = 1\n")
    _add_rule(
        "Verify command stays documented",
        "mementic/cli/verify_command_name.py",
        rule_id="verify-documented",
    )
    _git(repo, "mv", "mementic/cli/old_verify_name.py", "mementic/cli/verify_command_name.py")

    output = _text(_ok("check"))

    assert "mementic/cli/verify_command_name.py" in output
    assert "mementic/cli/old_verify_name.py" in output
    assert "(renamed from" in output


def test_check_lists_all_matched_files_for_one_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every matched changed file appears in the one rule's row."""
    repo = _repo(tmp_path, monkeypatch)
    names = ("a", "b", "c", "d", "e")
    for name in names:
        _commit(repo, f"src/{name}.py", "x = 1\n")
    _add_rule("Source modules stay importable", "src", rule_id="modules-importable")
    for name in names:
        (repo / "src" / f"{name}.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check"))

    for name in names:
        assert f"src/{name}.py" in output
    assert output.count("modules-importable") == 1


def test_check_directory_binding_and_rename_render_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory rule matching both rename sides renders one label."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/old.py", "x = 1\n")
    _add_rule("Source modules stay importable", "src", rule_id="modules-importable")
    _git(repo, "mv", "src/old.py", "src/new.py")

    output = _text(_ok("check"))

    assert "renamed from" in output
    assert output.count("(renamed from") == 1


def test_check_shows_each_constraint_once_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One row per constraint even when several changed files match it."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/a.py", "x = 1\n")
    _commit(repo, "src/b.py", "x = 1\n")
    _add_rule("Source modules stay importable", "src", rule_id="modules-importable")
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check"))

    assert output.count("modules-importable") == 1
    assert "src/a.py" in output
    assert "src/b.py" in output
    assert "[mementic: 1 constraints | 2 changed files]" in output


def test_check_without_matching_rules_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No matching rule is a normal outcome, not an error."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/auth.py", "x = 1\n")
    _add_rule("Docs must stay current", "docs/readme.md", rule_id="docs-current")
    (repo / "src" / "auth.py").write_text("x = 2\n", encoding="utf-8")

    result = _run("check")

    assert result.exit_code == 0
    output = _text(result)
    assert "No live rules are bound to the changed files." in output
    assert "docs-current" not in output
    assert "[mementic: 0 constraints | 1 changed files]" in output


def test_check_with_no_changed_files_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean work tree with an untracked file lists nothing and exits 0."""
    repo = _repo(tmp_path, monkeypatch)
    (repo / "src").mkdir()
    (repo / "src" / "untracked.py").write_text("x = 1\n", encoding="utf-8")

    result = _run("check")

    assert result.exit_code == 0
    output = _text(result)
    assert "No changed files to check." in output
    assert "[mementic: 0 constraints | no changed files]" in output


def test_check_without_commits_reports_unreadable_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository with no commits cannot produce a diff and exits 1."""
    _git_repo(tmp_path, monkeypatch)
    _ok("init")

    result = _run("check")

    assert result.exit_code == 1
    assert "Error: " in _text(result)


def test_check_output_states_it_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The output says it lists matches and does not verify the change."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/auth.py", "x = 1\n")
    (repo / "src" / "auth.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check"))

    assert "Lists the live rules bound to changed files." in output
    assert "It does not verify the change." in output


def test_check_hides_enforcing_decision_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enforcing decision stays out of the table without --decisions."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/db.py", "x = 1\n")
    _add_rule(
        "Migrations run before deploys",
        "src/db.py",
        rule_id="migrations-first",
        decision="Adopt Postgres",
    )
    (repo / "src" / "db.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check"))

    assert "migrations-first" in output
    assert "Adopt Postgres" not in output


def test_check_shows_enforcing_decision_with_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--decisions adds the decision enforcing each listed rule."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/db.py", "x = 1\n")
    _add_rule(
        "Migrations run before deploys",
        "src/db.py",
        rule_id="migrations-first",
        decision="Adopt Postgres",
    )
    (repo / "src" / "db.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check", "--decisions"))

    assert "migrations-first" in output
    assert "Adopt Postgres" in output


def test_check_decision_flag_excludes_unrelated_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--decisions shows the enforcing decision, not every live decision."""
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo, "src/db.py", "x = 1\n")
    _add_rule(
        "Migrations run before deploys",
        "src/db.py",
        rule_id="migrations-first",
        decision="Adopt Postgres",
    )
    _ok("add", "decision", "Use Redis", "-r", "Caching keeps reads fast")
    (repo / "src" / "db.py").write_text("x = 2\n", encoding="utf-8")

    output = _text(_ok("check", "--decisions"))

    assert "Adopt Postgres" in output
    assert "Use Redis" not in output


def test_check_help_documents_decisions_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check --help documents --decisions, so the command is registered."""
    _repo(tmp_path, monkeypatch)

    output = _text(_ok("check", "--help"))

    assert "--decisions" in output
    assert "Also show the decision enforcing each listed rule" in output


def test_check_rejects_exit_code_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no gating flag: --exit-code is a usage error."""
    _repo(tmp_path, monkeypatch)

    result = _run("check", "--exit-code")

    assert result.exit_code == 2
    assert "--exit-code" in _text(result)


def test_check_missing_graph_exits_one_without_creating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing graph is an environment error and is not created on read."""
    repo = _git_repo(tmp_path, monkeypatch)

    result = _run("check")

    assert result.exit_code == 1
    assert "no mementic graph" in _text(result)
    assert not (repo / ".mementic").exists()


def test_check_outside_git_work_tree_reports_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph outside a Git work tree reports that the diff cannot be read."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))
    _ok("init")

    result = _run("check")

    assert result.exit_code == 1
    output = _text(result)
    assert outside.resolve().as_posix() in output
    assert "is not inside a Git work tree" in output
    assert "cannot be read" in output
    assert "mementic query file" in output
