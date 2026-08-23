"""Instruction injection for agent enforcement blocks.

Detects agents in the repo and injects the mementic enforcement block
into AGENTS.md.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path


# Agent detection: directory name -> agent identifier
_AGENT_MARKERS: dict[str, str] = {
    ".opencode": "opencode",
    ".claude": "claude-code",
    ".cursor": "cursor",
    ".windsurf": "windsurf",
}

# Agent -> rules file path (relative to repo root)
_AGENT_RULES_FILES: dict[str, str] = {
    "claude-code": "CLAUDE.md",
    "cursor": ".cursorrules",
    "windsurf": ".windsurfrules",
}

_ENFORCEMENT_HEADING = "## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)"


def load_template() -> str:
    """Load the enforcement template from package resources."""
    ref = importlib.resources.files("mementic") / "templates" / "enforcement.md"
    return ref.read_text(encoding="utf-8")


def detect_agents(repo_root: Path) -> list[str]:
    """Detect agents present in the repo by directory markers.

    Args:
        repo_root: Path to the repository root.

    Returns:
        List of agent identifiers found (e.g. ["opencode", "cursor"]).
    """
    found: list[str] = []
    for marker, agent in _AGENT_MARKERS.items():
        if (repo_root / marker).is_dir():
            found.append(agent)
    return found


def has_enforcement(agents_md_path: Path) -> bool:
    """Check if AGENTS.md already contains the enforcement block.

    Args:
        agents_md_path: Path to AGENTS.md.

    Returns:
        True if the enforcement heading is found.
    """
    if not agents_md_path.exists():
        return False
    content = agents_md_path.read_text(encoding="utf-8")
    return _ENFORCEMENT_HEADING in content


def get_rules_file(agent: str) -> str | None:
    """Get the rules file path for a non-OpenCode agent.

    Args:
        agent: Agent identifier.

    Returns:
        Relative path to the rules file, or None if agent doesn't need one.
    """
    return _AGENT_RULES_FILES.get(agent)


def inject_enforcement(
    agents_md_path: Path,
    dry_run: bool = False,
) -> str:
    """Append the enforcement block to AGENTS.md.

    Args:
        agents_md_path: Path to AGENTS.md.
        dry_run: If True, return the content that would be written without
            modifying the file.

    Returns:
        Status message describing what was done (or would be done).
    """
    template = load_template()

    if has_enforcement(agents_md_path):
        return "Enforcement block already exists in AGENTS.md. Skipped."

    if dry_run:
        if agents_md_path.exists():
            return f"Would append enforcement block to {agents_md_path}:\n\n{template}"
        return f"Would create {agents_md_path} with enforcement block:\n\n{template}"

    if agents_md_path.exists():
        existing = agents_md_path.read_text(encoding="utf-8")
        # Ensure blank line before enforcement block
        if existing and not existing.endswith("\n\n"):
            if existing.endswith("\n"):
                new_content = existing + "\n" + template
            else:
                new_content = existing + "\n\n" + template
        else:
            new_content = existing + template
        agents_md_path.write_text(new_content, encoding="utf-8")
        return f"Appended enforcement block to {agents_md_path}."
    else:
        agents_md_path.write_text(template, encoding="utf-8")
        return f"Created {agents_md_path} with enforcement block."