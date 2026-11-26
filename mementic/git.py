"""Git work-tree detection and change listing for mementic.

``changed_files`` reads the paths touched since a diff point in one
invocation, so staged and working-tree changes share a single code path.
Passing ``base`` moves the diff point to the merge-base with that ref, which
keeps committed branch work in scope without a second diff. Git runs with the
caller's directory as its working directory, which keeps returned paths
relative to the graph root.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_STATUS_LETTERS: frozenset[str] = frozenset("AMDRCTU")
_RENAME_OR_COPY: frozenset[str] = frozenset("RC")
_DIFF_BASE_ARGS: tuple[str, ...] = (
    "-c",
    "core.hooksPath=",
    "diff",
    "--relative",
    "--name-status",
    "-z",
    "-M",
)


class GitDiffError(RuntimeError):
    """The change list cannot be read from this directory."""


class NotAWorkTreeError(GitDiffError):
    """The directory is not inside a Git work tree."""


@dataclass(frozen=True, kw_only=True)
class ChangedFile:
    """One changed path reported by ``git diff --name-status``.

    Attributes:
        status: Git name-status letter: ``A``, ``M``, ``D``, ``R``, ``C``,
            ``T``, or ``U``.
        path: Destination path, POSIX form, relative to the directory the
            diff ran from.
        previous_path: Source path for renames and copies, else ``None``.
    """

    status: str
    path: str
    previous_path: str | None = None


def is_git_work_tree(directory: Path) -> bool:
    """Return True when ``directory`` is inside a Git work tree.

    Args:
        directory: Directory to test.

    Returns:
        True when Git reports the directory inside a work tree, False
        otherwise, including when Git cannot be started.
    """
    try:
        _require_work_tree(directory)
    except GitDiffError:
        return False
    return True


def changed_files(directory: Path, *, base: str | None = None) -> list[ChangedFile]:
    """List paths changed since the diff point, relative to ``directory``.

    The diff point is ``HEAD`` unless ``base`` is given, in which case it is
    the merge-base of ``base`` and ``HEAD``. Staged and working-tree changes
    are both reported, renames are detected, and paths outside ``directory``
    are excluded. An untracked file is not a change and does not appear.

    Args:
        directory: Directory to run Git in; returned paths are relative to it.
        base: Ref whose merge-base with ``HEAD`` becomes the diff point, so
            committed branch work appears next to uncommitted edits. ``None``
            diffs against ``HEAD`` alone.

    Returns:
        One entry per changed path, in the order Git reports them.

    Raises:
        NotAWorkTreeError: When ``directory`` is not a Git work tree.
        GitDiffError: When ``directory`` has no commits yet, ``base`` cannot be
            resolved to a merge-base, or the diff cannot be read.
    """
    _require_work_tree(directory)
    _require_head_commit(directory)
    diff_point = _resolve_diff_point(directory, base)
    raw_diff = _diff_name_status(directory, diff_point)
    return _parse_name_status(raw_diff)


def _require_work_tree(directory: Path) -> None:
    """Raise ``NotAWorkTreeError`` when ``directory`` is outside a Git work tree."""
    result = _run_git(["rev-parse", "--is-inside-work-tree"], directory)
    if result.returncode != 0 or result.stdout.strip() != b"true":
        raise NotAWorkTreeError(f"{directory} is not a Git work tree; cannot read changes")


def _require_head_commit(directory: Path) -> None:
    """Raise ``GitDiffError`` when ``directory`` has no commits yet."""
    result = _run_git(["rev-parse", "--verify", "--quiet", "HEAD"], directory)
    if result.returncode != 0:
        raise GitDiffError(f"{directory} has no commits; there is nothing to diff against")


def _resolve_diff_point(directory: Path, base: str | None) -> str:
    """Return ``HEAD``, or the merge-base of ``base`` and ``HEAD`` when given."""
    if base is None:
        return "HEAD"
    result = _run_git(["merge-base", "--", base, "HEAD"], directory)
    if result.returncode != 0:
        detail = _decode(result.stderr).strip()
        message = f"cannot resolve a merge-base with {base} in {directory}"
        if detail:
            message = f"{message}: {detail}"
        raise GitDiffError(message)
    return _decode(result.stdout).strip()


def _diff_name_status(directory: Path, diff_point: str) -> bytes:
    """Run ``git diff --name-status -z`` against ``diff_point`` and return raw bytes."""
    result = _run_git([*_DIFF_BASE_ARGS, diff_point, "--"], directory)
    if result.returncode != 0:
        detail = _decode(result.stderr).strip()
        raise GitDiffError(f"cannot read changes in {directory}: {detail}")
    return result.stdout


def _run_git(args: list[str], directory: Path) -> subprocess.CompletedProcess[bytes]:
    """Run a Git command in ``directory`` and capture raw output.

    Raises:
        GitDiffError: When the Git executable cannot be started.
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=directory,
            capture_output=True,
            text=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitDiffError("git is not installed") from exc
    except OSError as exc:
        raise GitDiffError(f"cannot run git in {directory}: {exc}") from exc


def _parse_name_status(raw: bytes) -> list[ChangedFile]:
    """Parse NUL-separated ``git diff --name-status -z`` output.

    Each record starts with a status token followed by one path, or two paths
    (old then new) for renames (``R``) and copies (``C``).

    Raises:
        GitDiffError: When a status token is unknown or a record is truncated.
    """
    if not raw:
        return []
    fields = iter(_split_nul_fields(raw))
    changes: list[ChangedFile] = []
    for status_token in fields:
        status = _parse_status_letter(status_token)
        if status in _RENAME_OR_COPY:
            previous_path = _take_path(fields)
            new_path = _take_path(fields)
            changes.append(ChangedFile(status=status, path=new_path, previous_path=previous_path))
        else:
            path = _take_path(fields)
            changes.append(ChangedFile(status=status, path=path))
    return changes


def _split_nul_fields(raw: bytes) -> list[str]:
    """Split NUL-terminated Git output into decoded string fields."""
    return [_decode(token) for token in raw.removesuffix(b"\0").split(b"\0")]


def _parse_status_letter(token: str) -> str:
    """Extract and validate the leading status letter from a diff status token."""
    letter = token[:1]
    if letter not in _STATUS_LETTERS:
        raise GitDiffError(f"unexpected git diff status token: {token!r}")
    return letter


def _take_path(fields: Iterator[str]) -> str:
    """Return the next decoded path field or fail on truncated Git output."""
    try:
        return next(fields)
    except StopIteration as exc:
        raise GitDiffError("unexpected git diff output") from exc


def _decode(token: bytes) -> str:
    """Decode Git output bytes as UTF-8, replacing invalid byte sequences."""
    return token.decode("utf-8", errors="replace")
