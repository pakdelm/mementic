"""Enforcement section sync for AGENTS.md.

The mementic enforcement block is marker-wrapped and synced into AGENTS.md:
created when absent, replaced in place when present, and everything outside
the section is preserved byte-for-byte. Re-running sync with a newer
template upgrades the section in existing files.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

START_MARKER = "<!-- mementic:start -->"
END_MARKER = "<!-- mementic:end -->"


def load_template() -> str:
    """Load the enforcement template from package resources."""
    ref = importlib.resources.files("mementic") / "templates" / "enforcement.md"
    return ref.read_text(encoding="utf-8")


def _rendered_block() -> str:
    """The marker-wrapped enforcement block without a trailing newline."""
    return load_template().strip()


def sync_enforcement(agents_md_path: Path) -> str:
    """Create or replace the mementic enforcement section in AGENTS.md.

    A marker-wrapped section is replaced between its markers; otherwise the
    block is appended, creating the file when missing. Content outside the
    section is preserved byte-for-byte.

    Args:
        agents_md_path: Path to AGENTS.md.

    Returns:
        Status message describing the action taken.
    """
    block = _rendered_block()

    if not agents_md_path.exists():
        agents_md_path.write_text(f"{block}\n", encoding="utf-8")
        return f"Created {agents_md_path} with enforcement block."

    content = agents_md_path.read_text(encoding="utf-8")

    if START_MARKER in content:
        if END_MARKER not in content:
            return (
                f"Warning: mementic start marker without end marker in "
                f"{agents_md_path}; manual fix required, no changes written."
            )
        head, _, tail = content.partition(START_MARKER)
        _, _, tail = tail.partition(END_MARKER)
        new_content = f"{head}{block}{tail}"
        if new_content == content:
            return f"Enforcement section already up to date in {agents_md_path}."
        agents_md_path.write_text(new_content, encoding="utf-8")
        return f"Updated enforcement section in {agents_md_path}."

    if content and not content.endswith("\n\n"):
        separator = "\n" if content.endswith("\n") else "\n\n"
        new_content = f"{content}{separator}{block}\n"
    else:
        new_content = f"{content}{block}\n"
    agents_md_path.write_text(new_content, encoding="utf-8")
    return f"Appended enforcement block to {agents_md_path}."
