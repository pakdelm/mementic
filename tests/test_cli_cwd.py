"""CWD-local graph behavior at the CLI boundary."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from mementic.cli import app
from mementic.store import MementicStore

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _text(result: Result) -> str:
    streams = [result.output]
    try:
        streams.append(result.stderr)
    except ValueError:
        pass
    return _strip_ansi("\n".join(streams))


def _run(*args: str) -> Result:
    return runner.invoke(app, list(args))


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mementic.console.console._width", 160)


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = _run("init")
    assert result.exit_code == 0, _text(result)


def _add_decision(title: str, entity: str) -> None:
    result = _run("add", "decision", title, "-r", "Reason", "-e", entity)
    assert result.exit_code == 0, _text(result)


def test_init_creates_graph_and_agents_at_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)

    assert (tmp_path / ".mementic" / "graph.db").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert str(tmp_path / ".mementic" / "graph.db") in _text(_run("status"))


def test_query_from_subdirectory_fails_without_creating_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    child = tmp_path / "repo-a"
    child.mkdir()
    monkeypatch.chdir(child)

    result = _run("query", "stats")

    assert result.exit_code == 1
    assert "no mementic graph" in _text(result).lower()
    assert not (child / ".mementic").exists()


def test_add_from_subdirectory_fails_without_creating_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    child = tmp_path / "repo-a"
    child.mkdir()
    monkeypatch.chdir(child)

    result = _run("add", "decision", "Use JWT", "-r", "Tokens")

    assert result.exit_code == 1
    assert "no mementic graph" in _text(result).lower()
    assert not (child / ".mementic").exists()


def test_status_from_subdirectory_reports_no_graph_without_creating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    child = tmp_path / "repo-a"
    child.mkdir()
    monkeypatch.chdir(child)

    result = _run("status")

    assert result.exit_code == 0
    assert "not initialized" in _text(result).lower()
    assert not (child / ".mementic").exists()


def test_init_in_subdirectory_creates_graph_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    child = tmp_path / "repo-a"
    child.mkdir()
    monkeypatch.chdir(child)

    result = _run("init")

    assert result.exit_code == 0, _text(result)
    assert (child / ".mementic" / "graph.db").exists()
    assert (tmp_path / ".mementic" / "graph.db").exists()


def test_add_canonicalizes_absolute_path_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    target = tmp_path / "src" / "auth.py"
    target.parent.mkdir()
    target.write_text("x = 1", encoding="utf-8")

    result = _run("add", "decision", "Use JWT", "-r", "Tokens", "-e", str(target))

    assert result.exit_code == 0, _text(result)
    store = MementicStore(db_path=tmp_path / ".mementic" / "graph.db")
    try:
        decision = store.get_decision("use-jwt")
    finally:
        store.close()
    assert decision is not None
    assert decision.paths == ["src/auth.py"]


def test_query_file_strips_symbol_and_line_suffixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    target = tmp_path / "src" / "auth.py"
    target.parent.mkdir()
    target.write_text("x = 1", encoding="utf-8")
    assert _run("add", "decision", "Use JWT", "-r", "Tokens", "-e", "src/auth.py").exit_code == 0

    for query in ("src/auth.py:handler", "src/auth.py:42", "src/auth.py:42:7"):
        result = _run("query", "file", query, "--json")
        assert result.exit_code == 0, _text(result)
        data = json.loads(result.stdout)
        assert data["file"] == "src/auth.py"
        assert [decision["id"] for decision in data["decisions"]] == ["use-jwt"]


def test_query_file_prefix_matches_cwd_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    target = tmp_path / "repo-a" / "src" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1", encoding="utf-8")
    assert (
        _run(
            "add",
            "decision",
            "Repo A Auth",
            "-r",
            "Tokens",
            "-e",
            "repo-a/src/auth.py",
        ).exit_code
        == 0
    )

    exact = _run("query", "file", "repo-a/src", "--json")
    prefixed = _run("query", "file", "repo-a/src", "--prefix", "--json")

    assert exact.exit_code == 0, _text(exact)
    assert json.loads(exact.stdout)["decisions"] == []
    assert prefixed.exit_code == 0, _text(prefixed)
    assert [decision["id"] for decision in json.loads(prefixed.stdout)["decisions"]] == [
        "repo-a-auth"
    ]


def test_query_file_prefix_with_trailing_separator_stays_within_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    for relative in ("repo-a/src/auth.py", "repo-a/src-legacy/x.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1", encoding="utf-8")
    _add_decision("Repo A Auth", "repo-a/src/auth.py")
    _add_decision("Repo A Legacy", "repo-a/src-legacy/x.py")

    directory = _run("query", "file", "repo-a/src/", "--prefix", "--json")
    bare = _run("query", "file", "repo-a/src", "--prefix", "--json")

    assert directory.exit_code == 0, _text(directory)
    assert [decision["id"] for decision in json.loads(directory.stdout)["decisions"]] == [
        "repo-a-auth"
    ]
    assert bare.exit_code == 0, _text(bare)
    assert {decision["id"] for decision in json.loads(bare.stdout)["decisions"]} == {
        "repo-a-auth",
        "repo-a-legacy",
    }


@pytest.mark.parametrize(
    "query",
    ["repo-a/src/auth.py:handler", "repo-a/src/auth.py:42", "repo-a/src/auth.py:42:7"],
)
def test_query_file_prefix_keeps_symbol_and_line_suffixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    _init(tmp_path, monkeypatch)
    target = tmp_path / "repo-a" / "src" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1", encoding="utf-8")
    _add_decision("Repo A Auth", "repo-a/src/auth.py")

    result = _run("query", "file", query, "--prefix", "--json")

    assert result.exit_code == 0, _text(result)
    data = json.loads(result.stdout)
    assert data["file"] == "repo-a/src/auth.py"
    assert [decision["id"] for decision in data["decisions"]] == ["repo-a-auth"]
