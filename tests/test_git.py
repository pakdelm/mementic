"""Tests for mementic.git — work-tree detection and change listing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mementic.git import (
    ChangedFile,
    GitDiffError,
    NotAWorkTreeError,
    changed_files,
    is_git_work_tree,
)

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


def _git(repo: Path, *args: str) -> None:
    """Run Git in ``repo`` without touching the user's configuration."""
    subprocess.run(
        ["git", *_GIT_IDENTITY, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    """Create a repository with one committed file and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _main_repo(tmp_path: Path) -> Path:
    """Create a repository whose baseline commit sits on branch ``main``."""
    repo = _repo(tmp_path)
    _git(repo, "branch", "-M", "main")
    return repo


def _commit_change(repo: Path, relative: str, content: str) -> None:
    """Write ``relative`` with ``content`` and commit it."""
    (repo / relative).write_text(content, encoding="utf-8")
    _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", f"touch {relative}")


def _pin_ceiling(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Stop Git from discovering a repository above ``directory``."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(directory.resolve()))


def test_is_git_work_tree_true_inside_repo(tmp_path: Path) -> None:
    """A repository directory and its subdirectories are work trees."""
    repo = _repo(tmp_path)
    (repo / "sub").mkdir()

    assert is_git_work_tree(repo) is True
    assert is_git_work_tree(repo / "sub") is True


def test_is_git_work_tree_false_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain directory outside any repository is not a work tree."""
    outside = tmp_path / "outside"
    outside.mkdir()
    _pin_ceiling(monkeypatch, tmp_path)

    assert is_git_work_tree(outside) is False


def test_changed_files_reports_working_tree_modification(tmp_path: Path) -> None:
    """An unstaged edit appears as a modification."""
    repo = _repo(tmp_path)
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")

    assert changed_files(repo) == [ChangedFile(status="M", path="base.txt")]


def test_changed_files_reports_staged_modification(tmp_path: Path) -> None:
    """A staged edit appears in the same change list as a working-tree edit."""
    repo = _repo(tmp_path)
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "base.txt")

    assert changed_files(repo) == [ChangedFile(status="M", path="base.txt")]


def test_changed_files_reports_rename_with_previous_path(tmp_path: Path) -> None:
    """A rename reports both the new and the old path."""
    repo = _repo(tmp_path)
    _git(repo, "mv", "base.txt", "renamed.txt")

    assert changed_files(repo) == [
        ChangedFile(status="R", path="renamed.txt", previous_path="base.txt")
    ]


def test_changed_files_handles_path_with_spaces(tmp_path: Path) -> None:
    """A path containing spaces survives the NUL-separated parser."""
    repo = _repo(tmp_path)
    spaced = repo / "a file with spaces.txt"
    spaced.write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add spaced file")
    spaced.write_text("changed\n", encoding="utf-8")

    assert changed_files(repo) == [ChangedFile(status="M", path="a file with spaces.txt")]


def test_changed_files_reports_deletion(tmp_path: Path) -> None:
    """A deleted file stays in the change list."""
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", "base.txt")

    assert changed_files(repo) == [ChangedFile(status="D", path="base.txt")]


def test_changed_files_excludes_changes_outside_subdirectory(tmp_path: Path) -> None:
    """Paths outside the chosen directory are omitted and paths stay relative."""
    repo = _repo(tmp_path)
    sub = repo / "sub"
    sub.mkdir()
    (sub / "keep.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add subdirectory")

    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    (sub / "keep.txt").write_text("changed\n", encoding="utf-8")

    assert changed_files(sub) == [ChangedFile(status="M", path="keep.txt")]


def test_changed_files_raises_git_diff_error_outside_work_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain directory cannot produce a change list."""
    outside = tmp_path / "outside"
    outside.mkdir()
    _pin_ceiling(monkeypatch, tmp_path)

    with pytest.raises(GitDiffError, match="not a Git work tree"):
        changed_files(outside)


def test_changed_files_raises_not_a_work_tree_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain directory fails with the distinct not-a-work-tree error."""
    outside = tmp_path / "outside"
    outside.mkdir()
    _pin_ceiling(monkeypatch, tmp_path)

    with pytest.raises(NotAWorkTreeError, match="not a Git work tree"):
        changed_files(outside)


def test_changed_files_raises_git_diff_error_without_commits(tmp_path: Path) -> None:
    """A repository with no commits has nothing to diff against."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    with pytest.raises(GitDiffError, match="no commits"):
        changed_files(repo)


def test_changed_files_default_ignores_committed_branch_work(tmp_path: Path) -> None:
    """Without a base, work committed on the branch is not a change."""
    repo = _main_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit_change(repo, "base.txt", "committed on branch\n")

    assert changed_files(repo) == []


def test_changed_files_with_base_includes_committed_change(tmp_path: Path) -> None:
    """A base ref folds committed branch work into the change list."""
    repo = _main_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit_change(repo, "base.txt", "committed on branch\n")

    assert changed_files(repo, base="main") == [ChangedFile(status="M", path="base.txt")]


def test_changed_files_with_base_reports_committed_rename(tmp_path: Path) -> None:
    """A rename committed on the branch is still reported against a base."""
    repo = _main_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "mv", "base.txt", "renamed.txt")
    _git(repo, "commit", "-q", "-m", "rename")

    assert changed_files(repo, base="main") == [
        ChangedFile(status="R", path="renamed.txt", previous_path="base.txt")
    ]


def test_changed_files_with_base_keeps_uncommitted_edits(tmp_path: Path) -> None:
    """A committed edit and a dirty edit are one change; the untracked file stays out."""
    repo = _main_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit_change(repo, "base.txt", "committed on branch\n")
    (repo / "base.txt").write_text("edited after commit\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    assert changed_files(repo, base="main") == [ChangedFile(status="M", path="base.txt")]


def test_changed_files_with_base_includes_uncommitted_only_edit(tmp_path: Path) -> None:
    """A dirty edit appears even when the branch has no extra commits."""
    repo = _main_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "base.txt").write_text("edited, not committed\n", encoding="utf-8")

    assert changed_files(repo, base="main") == [ChangedFile(status="M", path="base.txt")]


def test_changed_files_with_base_uses_merge_base_on_diverged_branch(tmp_path: Path) -> None:
    """Only changes since the merge-base appear, not changes made on the base."""
    repo = _main_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit_change(repo, "branch-only.txt", "branch\n")
    _git(repo, "checkout", "-q", "main")
    _commit_change(repo, "main-only.txt", "main\n")
    _git(repo, "checkout", "-q", "feature")

    assert changed_files(repo, base="main") == [ChangedFile(status="A", path="branch-only.txt")]


def test_changed_files_with_unknown_base_raises_git_diff_error(tmp_path: Path) -> None:
    """An unknown base names the ref and is not a work-tree failure."""
    repo = _main_repo(tmp_path)

    with pytest.raises(GitDiffError) as exc_info:
        changed_files(repo, base="no-such-ref")

    assert "no-such-ref" in str(exc_info.value)
    assert not isinstance(exc_info.value, NotAWorkTreeError)
