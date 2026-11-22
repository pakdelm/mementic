"""Tests for mementic.paths — constants, CWD resolution, and path references."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mementic.paths import (
    GRAPH_DB_NAME,
    MEMORY_DIR_NAME,
    ForeignGraphPathError,
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


def _write_graph(directory: Path) -> None:
    """Create a minimal ``.mementic/graph.db`` marker under ``directory``."""
    graph_db_path(directory).parent.mkdir(parents=True, exist_ok=True)
    graph_db_path(directory).write_text("", encoding="utf-8")


def test_foreign_graph_path_error_is_value_error(tmp_path: Path) -> None:
    """The refusal subclasses ValueError so existing CLI handlers print it."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    target = repo_b / "x.py"

    error = ForeignGraphPathError(repo_b, target, repo_a)

    assert isinstance(error, ValueError)


def test_canonical_entity_path_refuses_sibling_graph(tmp_path: Path) -> None:
    """A sibling graph owns its paths, and its refusal names the graph and command."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    _write_graph(repo_b)

    with pytest.raises(ForeignGraphPathError) as excinfo:
        canonical_entity_path(repo_a, "../repo-b/x.py")

    error = excinfo.value
    assert error.owning_dir == repo_b
    assert error.relative_path == "x.py"
    assert str(error).splitlines() == [
        "../repo-b/x.py belongs to the graph at ../repo-b.",
        "Run: cd ../repo-b; mementic query file x.py",
    ]


def test_canonical_entity_path_accepts_own_graph(tmp_path: Path) -> None:
    """A path under the current graph keeps its current relative key."""
    repo = tmp_path / "repo-a"
    _write_graph(repo)

    assert canonical_entity_path(repo, "src/auth.py") == "src/auth.py"
    assert canonical_entity_path(repo, str(repo / "src" / "auth.py")) == "src/auth.py"


def test_canonical_entity_path_accepts_unowned_outside_path(tmp_path: Path) -> None:
    """A path outside the project that no graph owns stays absolute as before."""
    repo = tmp_path / "repo-a"
    _write_graph(repo)
    outside = tmp_path / "unowned" / "x.py"

    assert canonical_entity_path(repo, str(outside)) == outside.as_posix()


def test_canonical_entity_path_ignores_memory_dir_without_graph(tmp_path: Path) -> None:
    """A ``.mementic`` directory without ``graph.db`` does not own paths."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _write_graph(repo_a)
    (repo_b / MEMORY_DIR_NAME).mkdir(parents=True)

    assert canonical_entity_path(repo_a, "../repo-b/x.py") == (repo_b / "x.py").as_posix()


def test_canonical_entity_path_refuses_nonexistent_target_under_foreign_graph(
    tmp_path: Path,
) -> None:
    """Ownership is decided by the directory layout, not by the target's presence."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    _write_graph(repo_b)

    with pytest.raises(ForeignGraphPathError) as excinfo:
        canonical_entity_path(repo_a, "../repo-b/never-created.py")

    assert excinfo.value.owning_dir == repo_b
    assert excinfo.value.relative_path == "never-created.py"


def test_canonical_entity_path_refuses_nested_graph(tmp_path: Path) -> None:
    """A child directory with its own graph owns its paths too."""
    workspace = tmp_path / "workspace"
    repo_a = workspace / "repo-a"
    _write_graph(workspace)
    _write_graph(repo_a)

    with pytest.raises(ForeignGraphPathError) as excinfo:
        canonical_entity_path(workspace, "repo-a/src/auth.py")

    assert excinfo.value.owning_dir == repo_a
    assert excinfo.value.relative_path == "src/auth.py"


def test_canonical_entity_path_normalizes_trailing_separator_of_foreign_directory(
    tmp_path: Path,
) -> None:
    """A trailing separator is normalized away before the refusal is built.

    ``Path`` drops the separator during canonicalization, so a directory input
    owned by its own graph renders ``relative_path`` as ``.`` and keeps the
    caller-agnostic ``query file .`` hint.
    """
    workspace = tmp_path / "workspace"
    repo_a = workspace / "repo-a"
    _write_graph(workspace)
    _write_graph(repo_a)

    with pytest.raises(ForeignGraphPathError) as excinfo:
        canonical_entity_path(workspace, "repo-a/")

    error = excinfo.value
    assert error.owning_dir == repo_a
    assert error.relative_path == "."
    assert str(error).splitlines() == [
        "repo-a belongs to the graph at repo-a.",
        "Run: cd repo-a; mementic query file .",
    ]


def test_canonical_entity_path_accepts_child_without_graph(tmp_path: Path) -> None:
    """A child directory of base_dir without its own graph does not own its paths."""
    workspace = tmp_path / "workspace"
    _write_graph(workspace)

    assert canonical_entity_path(workspace, "repo-a/src/auth.py") == "repo-a/src/auth.py"


def test_canonical_entity_path_ignores_ancestor_of_base_dir_graph(tmp_path: Path) -> None:
    """A graph in an ancestor of base_dir never owns paths outside base_dir."""
    _write_graph(tmp_path)
    repo_a = tmp_path / "repo-a"
    _write_graph(repo_a)
    sibling = tmp_path / "repo-b" / "x.py"

    assert graph_db_path(tmp_path).exists()
    assert canonical_entity_path(repo_a, "../repo-b/x.py") == sibling.as_posix()


def test_canonical_entity_path_allows_paths_under_an_ancestor_graph(tmp_path: Path) -> None:
    """The ownership walk stops at the CWD and never inspects its ancestors.

    ``parent`` owns a graph, but ``base`` is the CWD, so a sibling file under
    ``parent`` is not judged by ``parent``'s graph: it is allowed and stored as
    an absolute key, matching decision spec-42-d2.
    """
    parent = tmp_path / "workspace"
    base = parent / "repo-a"
    shared = parent / "shared.py"
    _write_graph(parent)
    _write_graph(base)

    assert graph_db_path(parent).exists()
    assert canonical_entity_path(base, "../shared.py") == shared.as_posix()


def test_canonical_entity_path_refuses_directory_target(tmp_path: Path) -> None:
    """A directory input is checked with the same ownership walk from itself."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    _write_graph(repo_b)
    (repo_b / "src").mkdir()

    with pytest.raises(ForeignGraphPathError) as excinfo:
        canonical_entity_path(repo_a, "../repo-b/src")

    assert excinfo.value.owning_dir == repo_b
    assert excinfo.value.relative_path == "src"
