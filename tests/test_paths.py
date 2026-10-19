"""Tests for mementic.paths — constants and repo-root resolution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mementic.paths import (
    GRAPH_DB_NAME,
    MEMORY_DIR_NAME,
    ProjectPaths,
    graph_db_path,
    memory_dir,
    resolve_base_dir,
)

_GIT_AVAILABLE = shutil.which("git") is not None


def test_name_constants_match_layout() -> None:
    """Memory dir name composes into the graph database path."""
    base = Path("/some/repo")
    assert memory_dir(base) == base / MEMORY_DIR_NAME == base / ".mementic"
    assert graph_db_path(base) == base / ".mementic" / GRAPH_DB_NAME


def test_resolve_base_dir_outside_git_falls_back_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without Git, resolution anchors at the resolved process CWD."""
    monkeypatch.chdir(tmp_path)
    assert resolve_base_dir() == tmp_path.resolve()


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not available")
def test_resolve_base_dir_uses_git_toplevel_from_subdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invoking from a subdirectory still resolves the repository root."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    resolved = resolve_base_dir()

    assert resolved.is_absolute()
    assert resolved == tmp_path.resolve()
    assert graph_db_path(resolved) == tmp_path / ".mementic" / "graph.db"


def test_project_paths_resolves_store_paths_against_store_root(
    tmp_path: Path,
) -> None:
    """Store artifacts live under store_root while entities anchor at base_dir."""
    base = tmp_path / "workspace"
    store_root = tmp_path / "memory"
    paths = ProjectPaths(base_dir=base, store_root=store_root)

    assert paths.memory_path == store_root / MEMORY_DIR_NAME
    assert paths.graph_db == store_root / ".mementic" / "graph.db"
    assert paths.agents_md == base / "AGENTS.md"


def test_project_paths_resolves_relative_entities_against_base_dir(
    tmp_path: Path,
) -> None:
    """Relative stored paths resolve against base_dir, not the process CWD."""
    target = tmp_path / "src" / "auth.py"
    target.parent.mkdir()
    target.write_text("x = 1", encoding="utf-8")
    paths = ProjectPaths(base_dir=tmp_path, store_root=tmp_path)

    assert paths.entity_path("src/auth.py") == target
    assert paths.entity_exists("src/auth.py") is True


def test_project_paths_returns_absolute_entities_unchanged(tmp_path: Path) -> None:
    """Absolute stored paths bypass the base_dir anchor."""
    absolute = tmp_path / "outside.py"
    paths = ProjectPaths(base_dir=tmp_path / "repo", store_root=tmp_path / "repo")

    assert paths.entity_path(str(absolute)) == absolute
