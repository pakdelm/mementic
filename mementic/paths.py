"""Project path constants and CWD-local graph resolution for mementic.

The graph is directory-local: the process working directory owns
``.mementic/graph.db``. There is no ancestor walk, no Git lookup, and no
environment override. Entity paths are interpreted relative to that directory.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MEMORY_DIR_NAME: str = ".mementic"
GRAPH_DB_NAME: str = "graph.db"

_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".c",
        ".cc",
        ".cfg",
        ".clj",
        ".cljs",
        ".cmake",
        ".conf",
        ".cpp",
        ".cs",
        ".css",
        ".cxx",
        ".dart",
        ".ex",
        ".exs",
        ".fish",
        ".fs",
        ".go",
        ".gradle",
        ".groovy",
        ".h",
        ".hh",
        ".hpp",
        ".hrl",
        ".htm",
        ".html",
        ".hxx",
        ".ini",
        ".java",
        ".jl",
        ".js",
        ".json",
        ".json5",
        ".jsonc",
        ".jsx",
        ".kt",
        ".kts",
        ".less",
        ".lua",
        ".m",
        ".md",
        ".mdx",
        ".mjs",
        ".mm",
        ".php",
        ".pl",
        ".pm",
        ".ps1",
        ".py",
        ".pyi",
        ".pyx",
        ".r",
        ".rb",
        ".rs",
        ".rst",
        ".sass",
        ".scala",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".vue",
        ".xml",
        ".yaml",
        ".yml",
        ".zsh",
    }
)
_LINE_SUFFIX = re.compile(r":\d+(?::\d+)?$")
_DOUBLE_COLON_SYMBOL = re.compile(r"::[A-Za-z_][\w.]*$")
_SINGLE_COLON_SYMBOL = re.compile(r":[A-Za-z_][\w.]*$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:[^\\/]")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def resolve_base_dir() -> Path:
    """Return the process working directory as the graph anchor.

    The graph is directory-local: the directory where mementic runs owns
    ``.mementic/graph.db``. There is no ancestor walk, no Git lookup, and no
    environment override.

    Returns:
        The resolved process working directory.
    """
    return Path.cwd().resolve()


def clean_entity_path(raw: str) -> str:
    """Strip symbol and position qualifiers from a path reference.

    Accepted suffixes are ``::symbol``, ``:line``, ``:line:column``, and
    ``:symbol`` when the prefix ends in a known source-file extension. A
    reference may carry a position and a symbol together, so these suffixes
    are stripped in either order. The extension guard preserves legitimate
    POSIX filenames containing a colon. Windows drive prefixes (``C:\\``,
    ``C:/``, ``C:relative``) are preserved.

    Args:
        raw: Path reference supplied by a user or agent.

    Returns:
        The path without its symbol and position suffixes.
    """
    path = raw.strip()
    while True:
        stripped = _strip_qualifiers_once(path)
        if stripped == path:
            return path
        path = stripped


def _strip_qualifiers_once(path: str) -> str:
    """Strip at most one position and one symbol suffix from ``path``.

    One pass is not enough for every ordering: ``a.py:42::handler`` only
    exposes its position once the symbol is gone, so the caller repeats the
    pass until the reference stops shrinking.
    """
    path = _LINE_SUFFIX.sub("", path)
    path = _DOUBLE_COLON_SYMBOL.sub("", path)
    match = _SINGLE_COLON_SYMBOL.search(path)
    if match is not None and _looks_like_source_path(path[: match.start()]):
        path = path[: match.start()]
    return path


def _looks_like_source_path(prefix: str) -> bool:
    """Return True when ``prefix`` ends in a known source-file extension."""
    return Path(prefix).suffix.lower() in _SOURCE_EXTENSIONS


def canonical_entity_path(base_dir: Path, raw: str) -> str:
    """Return an entity path relative to ``base_dir`` with ``/`` separators.

    Args:
        base_dir: Directory that owns the graph (the process CWD).
        raw: Path reference, optionally with a symbol or position suffix.

    Returns:
        A relative path under ``base_dir`` when possible, otherwise an
        absolute path.

    Raises:
        ForeignGraphPathError: When the path belongs to another directory's graph.
        ValueError: When a Windows drive-absolute path is used on POSIX.
    """
    cleaned = clean_entity_path(raw)
    if _URL.match(cleaned):
        return cleaned
    if _WINDOWS_DRIVE_RELATIVE.match(cleaned):
        return cleaned
    if os.name != "nt" and _WINDOWS_DRIVE.match(cleaned):
        raise ValueError(f"Windows drive-prefixed path is not portable on this platform: {raw}")
    candidate = Path(cleaned)
    if candidate.is_absolute():
        absolute = Path(os.path.normpath(str(candidate)))
    else:
        absolute = Path(os.path.normpath(str(base_dir / candidate)))
    owner = _owning_graph_dir(absolute, base_dir)
    if owner is not None:
        raise ForeignGraphPathError(owner, absolute, base_dir)
    try:
        return absolute.relative_to(base_dir).as_posix()
    except ValueError:
        return absolute.as_posix()


def memory_dir(base_dir: Path) -> Path:
    """Return the memory directory under ``base_dir``.

    Args:
        base_dir: Directory that owns the graph.

    Returns:
        Path to the ``.mementic`` directory.
    """
    return base_dir / MEMORY_DIR_NAME


def graph_db_path(base_dir: Path) -> Path:
    """Return the graph database path under ``base_dir``.

    Args:
        base_dir: Directory that owns the graph.

    Returns:
        Path to ``graph.db`` inside the memory directory.
    """
    return memory_dir(base_dir) / GRAPH_DB_NAME


def display_path(path: Path, base_dir: Path) -> str:
    """Return ``path`` as it should appear in CLI output.

    Paths under ``base_dir`` are shown relative to it with ``/`` separators, so
    the output does not leak the machine's directory layout. Paths outside the
    project keep their absolute form rather than escaping the project with
    ``..`` segments.

    Args:
        path: Path to render.
        base_dir: Directory the graph and entity paths anchor at.

    Returns:
        A project-relative path when possible, otherwise an absolute path.
    """
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _cwd_relative_display(path: Path, base_dir: Path) -> str:
    """Return ``path`` relative to ``base_dir``, keeping ``..`` segments.

    Unlike :func:`display_path`, a path outside ``base_dir`` is rendered as a
    relative reference such as ``../repo-b`` rather than an absolute path, so
    the refusal message names a directory the user can reach from here.

    Args:
        path: Path to render.
        base_dir: Directory to render relative to.

    Returns:
        A POSIX-relative path, or the absolute POSIX path when the two paths
        sit on different Windows drives and no relative form exists.
    """
    try:
        return Path(os.path.relpath(path, base_dir)).as_posix()
    except ValueError:
        return path.as_posix()


def _owning_graph_dir(absolute: Path, base_dir: Path) -> Path | None:
    """Return the nearest directory around ``absolute`` that owns a graph.

    The walk starts at the target itself, so a directory input is checked as a
    directory, and moves to its parents. It stops before ``base_dir`` and its
    ancestors: paths owned by the current graph are never refused, and an
    uninitialized sibling cannot inherit a graph from a parent directory. A
    ``.mementic`` directory without ``graph.db`` does not own anything.

    Args:
        absolute: Normalized absolute target path; the target need not exist.
        base_dir: Directory that owns the current invocation's graph.

    Returns:
        The nearest directory whose ``.mementic/graph.db`` exists, or ``None``
        when no directory between the target and ``base_dir`` owns a graph.
    """
    candidate = absolute
    while True:
        if candidate == base_dir or base_dir.is_relative_to(candidate):
            return None
        if (memory_dir(candidate) / GRAPH_DB_NAME).exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


class ForeignGraphPathError(ValueError):
    """A path input belongs to another directory's graph.

    The message names the owning graph and the command to run from there.
    Existing CLI handlers catch ``ValueError`` and print the message to stderr
    with exit code 1, so refusing never writes to either graph.

    Attributes:
        owning_dir: Absolute directory that contains ``.mementic/graph.db``.
        relative_path: POSIX path of the target relative to ``owning_dir``.
    """

    owning_dir: Path
    relative_path: str

    def __init__(self, owning_dir: Path, target: Path, base_dir: Path) -> None:
        """Build the refusal message for a target owned by another graph.

        Args:
            owning_dir: Absolute directory that owns the target's graph.
            target: Normalized absolute target path.
            base_dir: Directory that owns the current invocation's graph.
        """
        self.owning_dir = owning_dir
        self.relative_path = target.relative_to(owning_dir).as_posix()
        owner_display = _cwd_relative_display(owning_dir, base_dir)
        target_display = _cwd_relative_display(target, base_dir)
        message = (
            f"{target_display} belongs to the graph at {owner_display}.\n"
            f"Run: cd {owner_display}; mementic query file {self.relative_path}"
        )
        super().__init__(message)


@dataclass(frozen=True, kw_only=True)
class ProjectPaths:
    """Resolved paths for one CLI invocation.

    ``base_dir`` anchors entity paths. ``store_root`` owns the memory
    directory; both are the process CWD in the CWD-local model, while keeping
    them separate leaves room for a store outside the work tree later.
    """

    base_dir: Path
    store_root: Path
    _exists_cache: dict[str, bool] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @classmethod
    def from_cwd(cls) -> ProjectPaths:
        """Resolve paths from the process working directory."""
        base = resolve_base_dir()
        return cls(base_dir=base, store_root=base)

    @property
    def memory_path(self) -> Path:
        """Return the memory directory under the store root."""
        return memory_dir(self.store_root)

    @property
    def graph_db(self) -> Path:
        """Return the graph database path under the store root."""
        return self.memory_path / GRAPH_DB_NAME

    @property
    def agents_md(self) -> Path:
        """Return the AGENTS.md path under the entity-path anchor."""
        return self.base_dir / "AGENTS.md"

    def entity_path(self, stored: str) -> Path:
        """Resolve a stored entity path against ``base_dir``.

        Absolute stored paths are returned unchanged.
        """
        path = Path(stored)
        return path if path.is_absolute() else self.base_dir / path

    def entity_exists(self, stored: str) -> bool:
        """Return True when a stored entity path exists on disk.

        Results are cached for the lifetime of one invocation; the CLI never
        mutates the work tree mid-command, and context packs check the same
        paths across decisions and constraints.
        """
        if stored in self._exists_cache:
            return self._exists_cache[stored]
        exists = self.entity_path(stored).exists()
        self._exists_cache[stored] = exists
        return exists

    def canonical_entity_path(self, raw: str) -> str:
        """Return ``raw`` canonicalized against ``base_dir``."""
        return canonical_entity_path(self.base_dir, raw)
