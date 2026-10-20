"""Project path constants and repo-root resolution for mementic.

All project-local paths derive from a single repo-root anchor so the globally
installed tool resolves the same memory directory regardless of the working
directory it was invoked from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MEMORY_DIR_NAME: str = ".mementic"
GRAPH_DB_NAME: str = "graph.db"


def resolve_base_dir() -> Path:
    """Return the stable repo root that owns this project's memory directory.

    Walks up from the process CWD to the nearest Git or mementic marker.
    Falling back to ``git rev-parse --show-toplevel`` keeps environment
    overrides (for example ``GIT_WORK_TREE``) working when no marker exists.
    Callers thread the returned root downward as ``base_dir`` so downstream
    consumers never depend on the process CWD.
    """
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        memory_db = candidate / MEMORY_DIR_NAME / GRAPH_DB_NAME
        if (candidate / ".git").exists() or memory_db.exists():
            return candidate

    return _git_toplevel() or cwd


def _git_toplevel() -> Path | None:
    """Return the Git toplevel for the current directory when Git knows one."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def memory_dir(base_dir: Path) -> Path:
    """Return the memory directory under ``base_dir``.

    Args:
        base_dir: Stable repo root as returned by :func:`resolve_base_dir`.

    Returns:
        Path to the ``.mementic`` directory.
    """
    return base_dir / MEMORY_DIR_NAME


def graph_db_path(base_dir: Path) -> Path:
    """Return the graph database path under ``base_dir``.

    Args:
        base_dir: Stable repo root as returned by :func:`resolve_base_dir`.

    Returns:
        Path to ``graph.db`` inside the memory directory.
    """
    return memory_dir(base_dir) / GRAPH_DB_NAME


@dataclass(frozen=True, kw_only=True)
class ProjectPaths:
    """Resolved paths for one CLI invocation.

    ``base_dir`` anchors repo-relative entity paths. ``store_root`` owns the
    memory directory; both are the Git toplevel today, while keeping them
    separate lets a store live outside the work tree.
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
