"""Git work-tree detection and change listing for mementic.

``changed_files`` reads the paths touched since ``HEAD`` in one invocation, so
staged and working-tree changes share a single code path. Git runs with the
caller's directory as its working directory, which keeps returned paths
relative to the graph root.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

_STATUS_LETTERS: frozenset[str] = frozenset("AMDRCTU")
_RENAME_OR_COPY: frozenset[str] = frozenset("RC")


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
        result = _run_git(["rev-parse", "--is-inside-work-tree"], directory)
    except GitDiffError:
        return False
    return result.returncode == 0 and result.stdout.strip() == b"true"


def changed_files(directory: Path) -> list[ChangedFile]:
    """List paths changed since ``HEAD``, relative to ``directory``.

    Staged and working-tree changes are both reported, renames are detected,
    and paths outside ``directory`` are excluded. An untracked file is not a
    change and does not appear.

    Args:
        directory: Directory to run Git in; returned paths are relative to it.

    Returns:
        One entry per changed path, in the order Git reports them.

    Raises:
        NotAWorkTreeError: When ``directory`` is not a Git work tree.
        GitDiffError: When ``directory`` has no commits yet, or the diff
            cannot be read.
    """
    inside = _run_git(["rev-parse", "--is-inside-work-tree"], directory)
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        raise NotAWorkTreeError(f"{directory} is not a Git work tree; cannot read changes")
    head = _run_git(["rev-parse", "--verify", "--quiet", "HEAD"], directory)
    if head.returncode != 0:
        raise GitDiffError(f"{directory} has no commits; there is nothing to diff against")
    result = _run_git(
        ["-c", "core.hooksPath=", "diff", "--relative", "--name-status", "-z", "-M", "HEAD"],
        directory,
    )
    if result.returncode != 0:
        detail = _decode(result.stderr).strip()
        raise GitDiffError(f"cannot read changes in {directory}: {detail}")
    return _parse_name_status(result.stdout)


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
    """Parse NUL-separated ``--name-status -z`` output.

    Raises:
        GitDiffError: When a status token is unknown or the stream ends before
            all paths for a token are present.
    """
    tokens = raw.split(b"\0")
    if tokens and not tokens[-1]:
        tokens.pop()
    changes: list[ChangedFile] = []
    index = 0
    while index < len(tokens):
        token = _decode(tokens[index])
        letter = token[:1]
        if letter not in _STATUS_LETTERS:
            raise GitDiffError(f"unexpected git diff status token: {token!r}")
        has_second_path = letter in _RENAME_OR_COPY
        paths_needed = 2 if has_second_path else 1
        if index + paths_needed >= len(tokens):
            raise GitDiffError("unexpected git diff output")
        if has_second_path:
            changes.append(
                ChangedFile(
                    status=letter,
                    path=_decode(tokens[index + 2]),
                    previous_path=_decode(tokens[index + 1]),
                )
            )
            index += 3
        else:
            changes.append(ChangedFile(status=letter, path=_decode(tokens[index + 1])))
            index += 2
    return changes


def _decode(token: bytes) -> str:
    """Decode a NUL-delimited Git path without losing undecodable bytes."""
    return token.decode("utf-8", errors="surrogateescape")
