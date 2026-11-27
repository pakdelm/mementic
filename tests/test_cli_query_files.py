"""Tests for the batched ``mementic query files`` subcommand.

The tests drive the real Typer app through ``typer.testing.CliRunner`` against
an isolated temporary project that owns its own ``.mementic/graph.db``. The
single-path parity test compares ``query files`` with ``query file`` on the
same graph so the merged contract stays pinned to the single-file command.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from mementic.cli import app
from mementic.store import MementicStore

runner = CliRunner()

_CONSOLE_ENV = {"COLUMNS": "160"}

_MODULE_PATH = Path(__file__).resolve().parents[1] / "mementic" / "cli" / "query_files.py"

_SUMMARY_COUNTS = re.compile(r"\[mementic: (\d+) constraints, (\d+) decisions \|")
_MATCHED_SUFFIX = re.compile(r" \(matched: [^)]*\)$")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences so assertions are platform-agnostic."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _text(result: Result) -> str:
    """Return combined, ANSI-stripped stdout+stderr of a CLI invocation."""
    streams = [result.output]
    try:
        streams.append(result.stderr)
    except ValueError:
        pass
    return _strip_ansi("\n".join(streams))


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force rich's render width to 160 so panels never ellipsize assertions."""
    monkeypatch.setattr("mementic.console.console._width", 160)


def _run(*args: str) -> Result:
    """Invoke the CLI app with the given argument vector."""
    return runner.invoke(app, list(args), env=_CONSOLE_ENV)


def _ok(*args: str) -> Result:
    """Invoke the CLI app, asserting a zero exit code."""
    result = _run(*args)
    assert result.exit_code == 0, f"command {list(args)} failed:\n{_text(result)}"
    return result


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create a graph-only project rooted at ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    result = _run("init", "--graph-only")
    assert result.exit_code == 0, _text(result)


def _write(tmp_path: Path, relative: str) -> None:
    """Create a small file on disk so path liveness keeps its bindings live."""
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1", encoding="utf-8")


def _add_decision(*entities: str, title: str = "Bound Decision") -> None:
    """Add a decision bound to every entity in ``entities``."""
    args = ["add", "decision", title, "-r", "Reason"]
    for entity in entities:
        args.extend(["-e", entity])
    _ok(*args)


def _add_constraint(*entities: str, description: str = "Bound Constraint") -> None:
    """Add a constraint bound to every entity in ``entities``."""
    args = ["add", "constraint", description]
    for entity in entities:
        args.extend(["-e", entity])
    _ok(*args)


def _summary_counts(text: str) -> tuple[int, int]:
    """Extract the constraint and decision counts from the summary line."""
    match = _SUMMARY_COUNTS.search(text)
    assert match is not None, f"no summary line in:\n{text}"
    return int(match.group(1)), int(match.group(2))


def _constraint_lines(text: str) -> list[str]:
    """Return rendered constraint lines."""
    return [line for line in text.splitlines() if line.startswith("- ") and ", bound: " in line]


def _decision_lines(text: str) -> list[str]:
    """Return rendered decision lines."""
    return [line for line in text.splitlines() if line.startswith("- [")]


def _strip_matched(line: str) -> str:
    """Remove the merged pack's ``(matched: ...)`` annotation from a line."""
    return _MATCHED_SUFFIX.sub("", line)


def _unmatched_lines(text: str) -> list[str]:
    """Return the bullet lines under the ``### Unmatched paths`` section."""
    lines = text.splitlines()
    if "### Unmatched paths" not in lines:
        return []
    start = lines.index("### Unmatched paths")
    return [line for line in lines[start + 1 :] if line.startswith("- ")]


def _summary_line(text: str) -> str:
    """Return the single ``[mementic: ...]`` line."""
    lines = [line for line in text.splitlines() if "[mementic:" in line]
    assert len(lines) == 1, f"expected exactly one summary line in:\n{text}"
    return lines[0]


@pytest.mark.parametrize("existing", [True, False], ids=["existing", "missing"])
def test_single_path_parity_existing_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    """``query files <path>`` matches ``query file <path>`` for one path."""
    _init(tmp_path, monkeypatch)
    path = "src/a.py" if existing else "src/gone.py"
    if existing:
        _write(tmp_path, path)
    _add_decision(path, title="Parity Decision")
    _add_constraint(path, description="Parity Constraint")

    file_text = _text(_ok("query", "file", path, "-c"))
    files_text = _text(_ok("query", "files", path, "-c"))

    assert _summary_counts(files_text) == _summary_counts(file_text)
    assert sorted(_strip_matched(line) for line in _constraint_lines(files_text)) == sorted(
        _constraint_lines(file_text)
    )
    assert sorted(_strip_matched(line) for line in _decision_lines(files_text)) == sorted(
        _decision_lines(file_text)
    )
    if not existing:
        assert _summary_counts(files_text) == (0, 0)
        assert _unmatched_lines(files_text) == [f"- {path}"]


def test_two_paths_sharing_rule_deduplicates_entry_with_ordered_matched_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry bound to both queried paths appears once, in argument order."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "src/a.py")
    _write(tmp_path, "src/b.py")
    _add_decision("src/a.py", "src/b.py", title="Shared Decision")
    _add_constraint("src/a.py", "src/b.py", description="Shared rule")

    text = _text(_ok("query", "files", "src/a.py", "src/b.py", "-c"))

    shared_constraints = [line for line in text.splitlines() if "Shared rule" in line]
    shared_decisions = [line for line in text.splitlines() if "Shared Decision" in line]
    assert len(shared_constraints) == 1
    assert shared_constraints[0].endswith("(matched: src/a.py, src/b.py)")
    assert len(shared_decisions) == 1
    assert shared_decisions[0].endswith("(matched: src/a.py, src/b.py)")


def test_ten_paths_summary_line_truncates_at_three_with_plus_seven_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten queried paths report three paths plus a ``(+7 more)`` suffix."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "p0.py")
    _add_decision("p0.py", title="First Path Rule")
    paths = [f"p{index}.py" for index in range(10)]

    text = _text(_ok("query", "files", *paths, "-c"))

    assert "## Files queried (10)" in text
    summary = _summary_line(text)
    assert "10 paths: p0.py, p1.py, p2.py (+7 more)" in summary


def test_unmatched_path_and_decisions_only_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only paths matching zero live entries are listed as unmatched."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "src/bound.py")
    _write(tmp_path, "src/decision_only.py")
    _add_constraint("src/bound.py", description="Bound Constraint")
    _add_decision("src/bound.py", title="Bound Decision")
    _add_decision("src/decision_only.py", title="Decision Only")

    text = _text(
        _ok(
            "query",
            "files",
            "src/bound.py",
            "src/ghost.py",
            "src/decision_only.py",
            "-c",
        )
    )

    assert _unmatched_lines(text) == ["- src/ghost.py"]


def test_duplicate_paths_and_qualifiers_counted_once_first_occurrence_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated and qualified paths collapse to their first occurrence."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "src/a.py")
    _write(tmp_path, "src/b.py")
    _add_constraint("src/a.py", "src/b.py", description="Dedup rule")

    text = _text(
        _ok(
            "query",
            "files",
            "src/a.py",
            "src/b.py",
            "src/a.py",
            "src/a.py:12",
            "-c",
        )
    )

    assert "2 paths: src/a.py, src/b.py" in _summary_line(text)
    constraint_line = next(line for line in text.splitlines() if "Dedup rule" in line)
    assert constraint_line.endswith("(matched: src/a.py, src/b.py)")
    matched = constraint_line.split("(matched: ", 1)[1].removesuffix(")")
    assert matched.split(", ") == ["src/a.py", "src/b.py"]


def test_rule_bound_to_directory_and_files_rendered_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rule bound to a directory and its files renders once with all matches."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "src/a.py")
    _write(tmp_path, "src/b.py")
    _add_constraint("src", "src/a.py", "src/b.py", description="Directory rule")

    text = _text(_ok("query", "files", "src", "src/a.py", "src/b.py", "-c"))

    constraint_lines = [line for line in text.splitlines() if "Directory rule" in line]
    assert constraint_lines == [
        "- Directory rule (#directory-rule, bound: src, src/a.py, src/b.py) "
        "(matched: src, src/a.py, src/b.py)"
    ]


def test_all_paths_unmatched_reports_all_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-unmatched invocations still exit 0 and name every queried path."""
    _init(tmp_path, monkeypatch)
    _add_decision("src/gone.py", title="Deprecated Rule")
    _ok("deprecate", "deprecated-rule")

    result = _run("query", "files", "src/gone.py", "src/also_gone.py", "-c")

    assert result.exit_code == 0, _text(result)
    text = _text(result)
    assert _summary_counts(text) == (0, 0)
    assert _unmatched_lines(text) == ["- src/gone.py", "- src/also_gone.py"]


@pytest.mark.parametrize("max_tokens", [0, -1, -50])
def test_non_positive_max_tokens_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_tokens: int
) -> None:
    """``--max-tokens`` below one exits 2 without opening the store."""
    _init(tmp_path, monkeypatch)
    calls: list[None] = []
    original_init = MementicStore.__init__

    def counting_init(self: MementicStore, *args: Any, **kwargs: Any) -> None:
        calls.append(None)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(MementicStore, "__init__", counting_init)

    result = _run(
        "query",
        "files",
        "src/a.py",
        "--max-tokens",
        str(max_tokens),
        "-c",
    )

    assert result.exit_code == 2
    assert f"Error: --max-tokens must be at least 1 (got {max_tokens})." in result.stderr
    assert calls == []


@pytest.mark.parametrize("flag", ["-c", "--compact"])
def test_default_output_renders_rich_panel_and_compact_omits_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """Default mode renders a Rich panel; compact mode prints undecorated markdown."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "src/a.py")
    _add_decision("src/a.py", title="Panel Decision")

    default_result = _ok("query", "files", "src/a.py")
    default_text = _text(default_result)
    assert len([line for line in default_text.splitlines() if "[mementic:" in line]) == 1
    assert "Files history:" in default_text
    assert "Panel Decision" in default_text

    compact_result = _ok("query", "files", "src/a.py", flag)
    compact_text = _text(compact_result)
    assert len([line for line in compact_text.splitlines() if "[mementic:" in line]) == 1
    assert "Files history:" not in compact_text
    assert "## Files queried (1)" in compact_text
    assert "Panel Decision" in compact_text
    assert "\u256d" not in compact_text


def test_opens_store_once_inside_context_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ten-path invocation opens, enters, and exits the store exactly once."""
    _init(tmp_path, monkeypatch)
    _write(tmp_path, "p0.py")
    _add_decision("p0.py", title="Store Decision")
    paths = [f"p{index}.py" for index in range(10)]

    counts = {"opens": 0, "enters": 0, "exits": 0}
    original_init = MementicStore.__init__
    original_enter = MementicStore.__enter__
    original_exit = MementicStore.__exit__

    def counting_init(self: MementicStore, *args: Any, **kwargs: Any) -> None:
        counts["opens"] += 1
        original_init(self, *args, **kwargs)

    def counting_enter(self: MementicStore) -> MementicStore:
        counts["enters"] += 1
        return original_enter(self)

    def counting_exit(
        self: MementicStore,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        counts["exits"] += 1
        original_exit(self, exc_type, exc_value, traceback)

    monkeypatch.setattr(MementicStore, "__init__", counting_init)
    monkeypatch.setattr(MementicStore, "__enter__", counting_enter)
    monkeypatch.setattr(MementicStore, "__exit__", counting_exit)

    _ok("query", "files", *paths, "-c")

    assert counts == {"opens": 1, "enters": 1, "exits": 1}


def test_no_staleness_or_hashing_imports_or_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module imports no staleness/hashing helpers and prints no markers."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("hash" in name or "stale" in name for name in imported), imported

    _init(tmp_path, monkeypatch)
    _write(tmp_path, "src/a.py")
    _add_decision("src/a.py", title="Marker Decision")
    text = _text(_ok("query", "files", "src/a.py", "-c")).lower()
    assert "stale" not in text
    assert "hash" not in text
