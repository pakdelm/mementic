"""Project path constants and repo-root resolution for mementic.

All project-local paths derive from a single repo-root anchor so the globally
installed tool resolves the same memory directory regardless of the working
directory it was invoked from.
"""

from __future__ import annotations

from pathlib import Path

MEMORY_DIR_NAME: str = ".mementic"
GRAPH_DB_NAME: str = "graph.db"
AUDIT_LOG_NAME: str = "audit.jsonl"


def resolve_base_dir() -> Path:
    """Return the stable repo root that owns this project's memory directory.

    Delegates to :func:`mementic.format.detect_repo_path` (Git toplevel with
    a CWD fallback outside repositories) and resolves the result to an
    absolute path. Callers thread the returned root downward as ``base_dir``
    so downstream consumers never depend on the process CWD.
    """
    from mementic.format import detect_repo_path

    return Path(detect_repo_path()).resolve()


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
