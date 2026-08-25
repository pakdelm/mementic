"""Project path constants and repo-root resolution for mementic.

All project-local paths derive from a single repo-root anchor so the globally
installed tool resolves the same memory directory regardless of the working
directory it was invoked from.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MEMORY_DIR_NAME: str = ".mementic"
GRAPH_DB_NAME: str = "graph.db"
AUDIT_LOG_NAME: str = "audit.jsonl"


def resolve_base_dir() -> Path:
    """Return the stable repo root that owns this project's memory directory.

    Uses ``git rev-parse --show-toplevel`` when available and falls back to
    the resolved process CWD outside Git repositories or when Git is not
    installed. Callers thread the returned root downward as ``base_dir``
    so downstream consumers never depend on the process CWD.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve()
    except FileNotFoundError:
        pass

    return Path.cwd().resolve()


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


def audit_log_path(base_dir: Path) -> Path:
    """Return the legacy JSONL audit log path under ``base_dir``.

    Args:
        base_dir: Stable repo root as returned by :func:`resolve_base_dir`.

    Returns:
        Path to ``audit.jsonl`` inside the memory directory.
    """
    return memory_dir(base_dir) / AUDIT_LOG_NAME
