"""Tests for mementic.paths — constants and repo-root resolution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mementic.paths import (
    AUDIT_LOG_NAME,
    GRAPH_DB_NAME,
    MEMORY_DIR_NAME,
    audit_log_path,
    graph_db_path,
    memory_dir,
    resolve_base_dir,
)

_GIT_AVAILABLE = shutil.which("git") is not None


def test_name_constants_match_layout() -> None:
    """Memory dir name composes into db and audit log paths."""
    base = Path("/some/repo")
    assert memory_dir(base) == base / MEMORY_DIR_NAME == base / ".mementic"
    assert graph_db_path(base) == base / ".mementic" / GRAPH_DB_NAME
    assert audit_log_path(base) == base / ".mementic" / AUDIT_LOG_NAME


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
