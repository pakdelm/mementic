"""OpenCode instruction file reader.

Scans a directory for instruction markdown files and returns their content.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class InstructionDocument:
    """A parsed OpenCode instruction document."""

    path: str
    title: str
    content: str


def _extract_title(content: str) -> str:
    """Extract the title from the first level-1 heading."""
    match = re.search(r"^# (.+)", content, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()


def _read_file(filepath: str) -> InstructionDocument:
    """Read a single markdown file into an InstructionDocument."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    return InstructionDocument(
        path=filepath,
        title=_extract_title(content),
        content=content,
    )


def scan_opencode_instructions(path: str) -> list[InstructionDocument]:
    """Scan a directory for ``*.md`` files and return their content.

    Args:
        path: Directory path containing OpenCode instruction markdown files.

    Returns:
        A list of parsed InstructionDocument instances.
    """
    results: list[InstructionDocument] = []
    try:
        entries = os.listdir(path)
    except OSError:
        return results

    for entry in sorted(entries):
        if not entry.lower().endswith(".md"):
            continue
        filepath = os.path.join(path, entry)
        if not os.path.isfile(filepath):
            continue
        results.append(_read_file(filepath))

    return results
