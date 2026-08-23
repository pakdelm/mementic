"""Health check helper module.

Provides HealthItem and check_health() for diagnosing mementic setup.
No network calls. No inference. All checks are presence-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mementic.store import MementicStore


@dataclass
class HealthItem:
    """A single health check result.

    Attributes:
        label: Human-readable name of the check (e.g. "Config", "Graph").
        status: Either "ok" if the check passed or "fail" otherwise.
        hint: Additional diagnostic message, may be empty.
    """

    label: str
    status: Literal["ok", "fail"]
    hint: str = ""


def check_health(
    graph: MementicStore | None = None,
    repo_path: Path | None = None,
) -> list[HealthItem]:
    """Run all health checks. No network calls. No inference.

    Args:
        graph: Optional MementicStore instance. When provided, checks store
            integrity. When None, shows store check as failed.
        repo_path: Optional repository root path. Used for AGENTS.md lookup.
            When None, falls back to ``Path.cwd()``.

    Returns:
        List of HealthItem results, one per check.
    """
    items: list[HealthItem] = []

    # 1. Store check
    if graph is not None:
        stats = graph.stats()
        if stats.get("constraints", 0) > 0 or stats.get("decisions", 0) > 0:
            items.append(
                HealthItem(
                    label="Graph",
                    status="ok",
                    hint=f"{stats.get('decisions', 0)} decisions, "
                    f"{stats.get('constraints', 0)} constraints",
                )
            )
        else:
            items.append(
                HealthItem(
                    label="Graph",
                    status="fail",
                    hint="Graph is empty. Run 'mementic init' to build it.",
                )
            )
    else:
        items.append(
            HealthItem(
                label="Graph",
                status="fail",
                hint="Could not load graph.",
            )
        )

    # 2. Enforcement check (AGENTS.md)
    agents_md = (repo_path or Path.cwd()) / "AGENTS.md"
    if agents_md.exists():
        content = agents_md.read_text(encoding="utf-8")
        if "## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)" in content:
            items.append(
                HealthItem(
                    label="Instructions",
                    status="ok",
                    hint="Enforcement section found in AGENTS.md.",
                )
            )
        else:
            items.append(
                HealthItem(
                    label="Instructions",
                    status="fail",
                    hint="No enforcement section in AGENTS.md. Run 'mementic init'.",
                )
            )
    else:
        items.append(
            HealthItem(
                label="Instructions",
                status="fail",
                hint="No enforcement section in AGENTS.md. Run 'mementic init'.",
            )
        )

    return items
