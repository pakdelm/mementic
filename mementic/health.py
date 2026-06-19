"""Health check helper module.

Provides HealthItem and check_health() for diagnosing mementic setup.
No network calls. No LLM cost. All checks are presence-only.
"""

from __future__ import annotations

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


def check_health(
    graph: GraphStore | None = None,
    repo_path: Path | None = None,
) -> list[HealthItem]:
    """Run all health checks. No network calls. No LLM cost.

    Args:
        graph: Optional GraphStore instance. When provided, checks graph
            integrity. When None, shows graph check as failed.
        repo_path: Optional repository root path. Used for AGENTS.md lookup.
            When None, falls back to ``Path.cwd()``.

    Returns:
        List of HealthItem results, one per check.
    """
    items: list[HealthItem] = []

    # 1. Config check (project-aware: global config)
    from mementic.config import get_global_config_path

    global_config = get_global_config_path()
    if global_config.exists():
        items.append(HealthItem(label="Config", status="ok"))
    else:
        items.append(
            HealthItem(
                label="Config",
                status="fail",
                hint="No config found. Run 'mementic init' first.",
            )
        )

    # 2. API key check (via keyring → env fallback)
    try:
        from mementic.config import load_global_config
        cfg = load_global_config()
        api_key = cfg.llm.get_api_key()
    except Exception:
        api_key = None
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
                hint="not set. Mementic runs in deterministic mode with reduced extraction quality. Set a key in ~/.mementic/config.json for full quality, or continue as-is.",
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

    # 5. Enforcement check (AGENTS.md)
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
                    hint="No enforcement section in AGENTS.md. Run 'mementic inject'.",
                )
            )
    else:
        items.append(
            HealthItem(
                label="Instructions",
                status="fail",
                hint="No enforcement section in AGENTS.md. Run 'mementic inject'.",
            )
        )

    return items
