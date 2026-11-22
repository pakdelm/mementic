"""Health check helpers for diagnosing a mementic setup.

All checks are local and read-only: no network calls, no inference.
Binding counts hash the bound files.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mementic import staleness
from mementic.inject import START_MARKER
from mementic.schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from mementic.store import MementicStore


_MISSING_GRAPH_HINT = (
    "Could not load graph. Run 'mementic init' here, or cd to the directory that owns .mementic."
)


@dataclass(kw_only=True)
class HealthItem:
    """A single health check result."""

    label: str
    status: Literal["ok", "fail"]
    hint: str = ""


def _bindings_hint(
    graph: MementicStore,
    resolve_path: Callable[[str], Path] | None,
) -> str:
    """Return the binding-staleness suffix for the Graph hint.

    A graph with no entry_paths rows yields no suffix, so its hint stays
    byte-identical to a graph that never stored bindings. Without a
    resolver nothing can be verified, so every binding counts as unknown
    and no file is accessed.

    Args:
        graph: Open store whose bindings are classified.
        resolve_path: Maps a stored path to the file to hash on disk, or
            None when the caller cannot verify bindings.

    Returns:
        The ``"; bindings: ..."`` suffix, or an empty string when the
        graph has no bindings.
    """
    bindings = graph.get_all_bindings()
    if not bindings:
        return ""
    if resolve_path is None:
        counts = {"missing": 0, "changed": 0, "unknown": len(bindings)}
    else:
        checks = staleness.classify_bindings(bindings, resolve=resolve_path)
        counts = staleness.staleness_counts(checks)
    return (
        f"; bindings: {counts['missing']} missing, "
        f"{counts['changed']} changed, {counts['unknown']} unknown"
    )


def _graph_schema_version(graph_path: Path) -> int | None:
    """Read a graph file's schema version without modifying the file.

    Args:
        graph_path: Existing graph database file.

    Returns:
        The stored ``PRAGMA user_version``, or None when the file cannot be
        opened or queried as an SQLite database.
    """
    connection: sqlite3.Connection | None = None
    try:
        uri = f"{graph_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        row = connection.execute("PRAGMA user_version").fetchone()
    except (sqlite3.Error, OSError, ValueError):
        return None
    finally:
        if connection is not None:
            connection.close()
    return int(row[0]) if row is not None else None


def _unopenable_graph_hint(graph_path: Path | None) -> str:
    """Explain why the graph could not be opened for the health check.

    Args:
        graph_path: Graph database location, or None when the caller did not
            supply one.

    Returns:
        A hint naming the cause: a missing graph, an unreadable file, a
        schema version this build does not accept, or a matching schema that
        still failed to load.
    """
    if graph_path is None or not graph_path.exists():
        return _MISSING_GRAPH_HINT
    version = _graph_schema_version(graph_path)
    if version is None or version == 0:
        return (
            "Could not load graph: the file is not a readable mementic database. "
            "Move it aside and run 'mementic init' to start fresh."
        )
    if version != SCHEMA_VERSION:
        return (
            f"Could not load graph: it is schema v{version}, but this build requires "
            f"v{SCHEMA_VERSION}. Old graphs are not upgraded; move the file aside and "
            "run 'mementic init' to start fresh."
        )
    return (
        "Could not load graph: the file could not be read even though its schema matches. "
        "Move it aside and run 'mementic init' to start fresh."
    )


def _graph_item(
    graph: MementicStore | None,
    resolve_path: Callable[[str], Path] | None,
    graph_path: Path | None,
) -> HealthItem:
    """Check that the store opens. An empty graph is healthy and reports a hint."""
    if graph is None:
        return HealthItem(
            label="Graph",
            status="fail",
            hint=_unopenable_graph_hint(graph_path),
        )

    stats = graph.stats()
    if stats.get("constraints", 0) > 0 or stats.get("decisions", 0) > 0:
        hint = (
            f"{stats.get('decisions', 0)} decisions, "
            f"{stats.get('constraints', 0)} constraints"
            f"{_bindings_hint(graph, resolve_path)}"
        )
        return HealthItem(label="Graph", status="ok", hint=hint)
    return HealthItem(
        label="Graph",
        status="ok",
        hint="0 entries recorded. Record rules with 'mementic add'.",
    )


def _instructions_item(repo_path: Path | None) -> HealthItem:
    """Check that AGENTS.md contains the marker-wrapped enforcement section."""
    agents_md = (repo_path or Path.cwd()) / "AGENTS.md"
    if agents_md.exists() and START_MARKER in agents_md.read_text(encoding="utf-8"):
        return HealthItem(
            label="Instructions",
            status="ok",
            hint="Enforcement section found in AGENTS.md.",
        )
    return HealthItem(
        label="Instructions",
        status="fail",
        hint="No enforcement section in AGENTS.md. Run 'mementic init'.",
    )


def check_health(
    graph: MementicStore | None = None,
    repo_path: Path | None = None,
    *,
    resolve_path: Callable[[str], Path] | None = None,
    graph_path: Path | None = None,
) -> list[HealthItem]:
    """Run all health checks.

    Args:
        graph: Optional open store. When None, the graph check fails.
        repo_path: Optional repository root for the AGENTS.md lookup.
        resolve_path: Optional resolver mapping a stored binding path to
            the file on disk. When None, bindings cannot be verified and
            all of them count as unknown.
        graph_path: Optional graph database location. Used only to explain
            why an existing graph could not be opened.

    Returns:
        One HealthItem per check.
    """
    return [_graph_item(graph, resolve_path, graph_path), _instructions_item(repo_path)]
