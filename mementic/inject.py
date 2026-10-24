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


def sync_enforcement(agents_md_path: Path, *, label: str | None = None) -> str:
    """Create or replace the mementic enforcement section in AGENTS.md.

    A marker-wrapped section is replaced between its markers; otherwise the
    block is appended, creating the file when missing. Content outside the
    section is preserved byte-for-byte.

    Args:
        agents_md_path: Path to AGENTS.md.
        label: Path to show in the status message. Defaults to ``agents_md_path``
            so callers can print a project-relative form.

    Returns:
        Status message describing the action taken.
    """
    block = _rendered_block()
    shown = label or str(agents_md_path)

    if not agents_md_path.exists():
        agents_md_path.write_text(f"{block}\n", encoding="utf-8")
        return f"Created {shown} with enforcement block."

    content = agents_md_path.read_text(encoding="utf-8")

    if START_MARKER in content:
        if END_MARKER not in content:
            return (
                f"Warning: mementic start marker without end marker in "
                f"{shown}; manual fix required, no changes written."
            )
        head, _, tail = content.partition(START_MARKER)
        _, _, tail = tail.partition(END_MARKER)
        new_content = f"{head}{block}{tail}"
        if new_content == content:
            return f"Enforcement section already up to date in {shown}."
        agents_md_path.write_text(new_content, encoding="utf-8")
        return f"Updated enforcement section in {shown}."

    if content and not content.endswith("\n\n"):
        separator = "\n" if content.endswith("\n") else "\n\n"
        new_content = f"{content}{separator}{block}\n"
    else:
        new_content = f"{content}{block}\n"
    agents_md_path.write_text(new_content, encoding="utf-8")
    return f"Appended enforcement block to {shown}."
