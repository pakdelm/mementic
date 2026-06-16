"""Health check helper module.

Provides HealthItem and check_health() for diagnosing mementic setup.
No network calls. No LLM cost. All checks are presence-only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from mementic.graph import GraphStore


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


def check_health(graph: GraphStore | None = None) -> list[HealthItem]:
    """Run all health checks. No network calls. No LLM cost.

    Args:
        graph: Optional GraphStore instance. When provided, checks graph
            integrity. When None, shows graph check as failed.

    Returns:
        List of HealthItem results, one per check.
    """
    items: list[HealthItem] = []

    # 1. Config check (project-aware: global config or legacy)
    from mementic.config import get_global_config_path

    global_config = get_global_config_path()
    legacy_config = Path(".mementic.json")
    if global_config.exists() or legacy_config.exists():
        items.append(HealthItem(label="Config", status="ok"))
    else:
        items.append(
            HealthItem(
                label="Config",
                status="fail",
                hint="No config found. Run 'mementic init' first.",
            )
        )

    # 2. API key check (presence only — no validation call)
    api_key = (
        os.environ.get("MEMENTIC_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
    )
    if api_key:
        items.append(
            HealthItem(
                label="API key",
                status="ok",
                hint="present — run 'mementic capture' to verify",
            )
        )
    else:
        items.append(
            HealthItem(
                label="API key",
                status="fail",
                hint="not set. Set MEMENTIC_API_KEY in env or ~/.mementic/config.json",
            )
        )

    # 3. Graph check
    if graph is not None:
        stats = graph.stats()
        if stats.get("constraints", 0) > 0 or stats.get("decisions", 0) > 0:
            items.append(
                HealthItem(
                    label="Graph",
                    status="ok",
                    hint=f"{stats.get('entities', 0)} entities, "
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

    # 4. Session provider check
    try:
        from mementic.session import get_registry

        registry = get_registry()
        provider, detected = registry.detect()
        if provider and detected:
            items.append(
                HealthItem(
                    label="Session provider",
                    status="ok",
                    hint=f"{provider.agent_name} detected",
                )
            )
        else:
            items.append(
                HealthItem(
                    label="Session provider",
                    status="fail",
                    hint="No session provider detected. "
                    "Run 'mementic capture --agent opencode' after your first session.",
                )
            )
    except Exception:
        items.append(
            HealthItem(
                label="Session provider",
                status="fail",
                hint="Could not detect session provider.",
            )
        )

    return items
