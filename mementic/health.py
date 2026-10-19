"""Health check helpers for diagnosing a mementic setup.

All checks are presence-only: no network calls, no inference.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from mementic.inject import START_MARKER

if TYPE_CHECKING:
    from mementic.store import MementicStore


class HealthItem(BaseModel):
    """A single health check result."""

    label: str
    status: Literal["ok", "fail"]
    hint: str = ""


def _graph_item(graph: MementicStore | None) -> HealthItem:
    """Check that the store opens and contains at least one entry."""
    if graph is None:
        return HealthItem(label="Graph", status="fail", hint="Could not load graph.")

    stats = graph.stats()
    if stats.get("constraints", 0) > 0 or stats.get("decisions", 0) > 0:
        return HealthItem(
            label="Graph",
            status="ok",
            hint=(
                f"{stats.get('decisions', 0)} decisions, "
                f"{stats.get('constraints', 0)} constraints"
            ),
        )
    return HealthItem(
        label="Graph",
        status="fail",
        hint="Graph is empty. Record rules with 'mementic add'.",
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
) -> list[HealthItem]:
    """Run all health checks.

    Args:
        graph: Optional open store. When None, the graph check fails.
        repo_path: Optional repository root for the AGENTS.md lookup.

    Returns:
        One HealthItem per check.
    """
    return [_graph_item(graph), _instructions_item(repo_path)]
