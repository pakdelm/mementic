"""Tests for mementic.paths — constants, CWD resolution, and path references."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mementic.paths import (
    GRAPH_DB_NAME,
    MEMORY_DIR_NAME,
    ProjectPaths,
    canonical_entity_path,
    clean_entity_path,
    display_path,
    graph_db_path,
    memory_dir,
    resolve_base_dir,
)


def test_name_constants_match_layout() -> None:
    """Memory dir name composes into the graph database path."""
    base = Path("/some/repo")
    assert memory_dir(base) == base / MEMORY_DIR_NAME == base / ".mementic"
    assert graph_db_path(base) == base / ".mementic" / GRAPH_DB_NAME


def test_resolve_base_dir_is_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The graph is anchored at the process CWD."""
    monkeypatch.chdir(tmp_path)

    assert resolve_base_dir() == tmp_path.resolve()


def test_resolve_base_dir_ignores_ancestor_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ancestor .git or .mementic/graph.db does not move the anchor."""
    (tmp_path / ".git").mkdir()
    (tmp_path / MEMORY_DIR_NAME).mkdir()
    (tmp_path / MEMORY_DIR_NAME / GRAPH_DB_NAME).write_text("", encoding="utf-8")
    nested = tmp_path / "repo-a" / "src"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert resolve_base_dir() == nested.resolve()


def test_display_path_is_relative_under_base(tmp_path: Path) -> None:
    """Paths under the project render relative to it with / separators."""
    path = tmp_path / MEMORY_DIR_NAME / GRAPH_DB_NAME

    assert display_path(path, tmp_path) == f"{MEMORY_DIR_NAME}/{GRAPH_DB_NAME}"


def test_display_path_keeps_outside_paths_absolute(tmp_path: Path) -> None:
    """Paths outside the project stay absolute instead of using .. segments."""
    outside = tmp_path.parent / "elsewhere" / "AGENTS.md"

    assert display_path(outside, tmp_path) == outside.as_posix()


def test_resolve_base_dir_ignores_scope_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deprecated scope environment variables have no effect."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMENTIC_WORKSPACE_ROOT", str(tmp_path.parent))
    monkeypatch.setenv("MEMENTIC_ROOT", str(tmp_path.parent))

    assert resolve_base_dir() == tmp_path.resolve()


def test_project_paths_from_cwd_uses_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ProjectPaths anchors both the store and entity paths at CWD."""
    monkeypatch.chdir(tmp_path)

    paths = ProjectPaths.from_cwd()

    assert paths.base_dir == tmp_path.resolve()
    assert paths.store_root == tmp_path.resolve()
    assert paths.graph_db == tmp_path / ".mementic" / "graph.db"
    assert paths.agents_md == tmp_path / "AGENTS.md"


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


def test_project_paths_caches_entity_exists(tmp_path: Path) -> None:
    """Liveness checks are cached for the lifetime of one invocation."""
    paths = ProjectPaths(base_dir=tmp_path, store_root=tmp_path)

    assert paths.entity_exists("late.py") is False
    (tmp_path / "late.py").write_text("", encoding="utf-8")
    assert paths.entity_exists("late.py") is False

    fresh = ProjectPaths(base_dir=tmp_path, store_root=tmp_path)
    assert fresh.entity_exists("late.py") is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/foo.py", "src/foo.py"),
        ("src/foo.py::handler", "src/foo.py"),
        ("src/foo.py::Handler.handle", "src/foo.py"),
        ("src/foo.py:handler", "src/foo.py"),
        ("src/foo.py:42", "src/foo.py"),
        ("src/foo.py:42:7", "src/foo.py"),
        ("src/foo:bar.py", "src/foo:bar.py"),
        ("Makefile:build", "Makefile:build"),
        ("https://example.com/a.py", "https://example.com/a.py"),
        ("C:\\src\\foo.py", "C:\\src\\foo.py"),
        ("C:/src/foo.py", "C:/src/foo.py"),
        ("C:\\src\\foo.py:handler", "C:\\src\\foo.py"),
        ("C:\\src\\foo.py::Handler.handle", "C:\\src\\foo.py"),
        ("C:\\src\\foo.py:42", "C:\\src\\foo.py"),
        ("C:\\src\\foo.py:handler:42", "C:\\src\\foo.py"),
        ("C:relative.py", "C:relative.py"),
    ],
)
def test_clean_entity_path_strips_qualifiers(raw: str, expected: str) -> None:
    assert clean_entity_path(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/auth.py", "src/auth.py"),
        ("src/auth.py:42::handler", "src/auth.py"),
        ("src/auth.py::handler:42", "src/auth.py"),
        ("src/auth.py:42:7", "src/auth.py"),
        ("src/auth.py:handler", "src/auth.py"),
    ],
)
def test_clean_entity_path_strips_qualifiers_in_either_order(raw: str, expected: str) -> None:
    """A position and a symbol suffix are removed regardless of their order."""
    assert clean_entity_path(raw) == expected


def test_canonical_entity_path_keeps_relative_paths(tmp_path: Path) -> None:
    assert canonical_entity_path(tmp_path, "src/auth.py") == "src/auth.py"


def test_canonical_entity_path_normalizes_dot_segments(tmp_path: Path) -> None:
    assert canonical_entity_path(tmp_path, "./src/../src/auth.py") == "src/auth.py"


def test_canonical_entity_path_rebases_absolute_paths_under_base(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "auth.py"

    assert canonical_entity_path(tmp_path, str(target)) == "src/auth.py"


def test_canonical_entity_path_keeps_absolute_paths_outside_base(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.py"

    assert canonical_entity_path(tmp_path, str(outside)) == outside.as_posix()


def test_canonical_entity_path_stores_escaping_paths_absolute(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside.py"

    assert canonical_entity_path(tmp_path, "../outside.py") == outside.as_posix()


def test_canonical_entity_path_strips_symbol_and_line_suffixes(
    tmp_path: Path,
) -> None:
    assert canonical_entity_path(tmp_path, "src/auth.py:handler") == "src/auth.py"
    assert canonical_entity_path(tmp_path, "src/auth.py:42") == "src/auth.py"
    assert canonical_entity_path(tmp_path, "src/auth.py:42:7") == "src/auth.py"
    assert canonical_entity_path(tmp_path, "src/auth.py:42::handler") == "src/auth.py"
    assert canonical_entity_path(tmp_path, "src/auth.py::handler:42") == "src/auth.py"


def test_canonical_entity_path_preserves_posix_colon_filename(
    tmp_path: Path,
) -> None:
    assert canonical_entity_path(tmp_path, "src/foo:bar.py") == "src/foo:bar.py"


def test_canonical_entity_path_preserves_urls(tmp_path: Path) -> None:
    url = "https://example.com/a.py"

    assert canonical_entity_path(tmp_path, url) == url


def test_canonical_entity_path_preserves_drive_relative_paths(
    tmp_path: Path,
) -> None:
    assert canonical_entity_path(tmp_path, "C:relative.py") == "C:relative.py"


@pytest.mark.parametrize("raw", ["C:\\src\\auth.py", "C:/src/auth.py"])
def test_canonical_entity_path_rejects_windows_drive_on_posix(tmp_path: Path, raw: str) -> None:
    if os.name == "nt":
        assert canonical_entity_path(tmp_path, raw).startswith("C:/")
    else:
        with pytest.raises(ValueError, match="not portable"):
            canonical_entity_path(tmp_path, raw)


def test_project_paths_canonical_entity_path_method(tmp_path: Path) -> None:
    paths = ProjectPaths(base_dir=tmp_path, store_root=tmp_path)

    assert paths.canonical_entity_path("src/auth.py:handler") == "src/auth.py"
