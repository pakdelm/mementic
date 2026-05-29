"""ADR (Architecture Decision Record) reader.

Scans a directory for ADR markdown files and extracts structured fields.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ADRDocument:
    """A parsed ADR document with structured fields."""

    path: str
    title: str
    status: str
    context: str
    decision: str
    consequences: str
    timestamp: str


def _extract_title(content: str) -> str:
    """Extract the ADR title from the first level-1 heading.

    Strips common prefixes like ``ADR-NNNN: `` and ``Title: ``.
    """
    match = re.search(r"^# (.+)", content, re.MULTILINE)
    if not match:
        return ""
    raw = match.group(1).strip()
    raw = re.sub(r"^(?:ADR-\d+: |Title: )", "", raw)
    return raw


def _extract_sections(content: str) -> dict[str, str]:
    """Split markdown content into ``## `` headed sections.

    Returns a dict mapping lowercase header names to their body text.
    """
    parts = re.split(r"^## ", content, flags=re.MULTILINE)
    sections: dict[str, str] = {}
    for part in parts[1:]:
        first_nl = part.index("\n") if "\n" in part else len(part)
        header = part[:first_nl].strip().lower()
        body = part[first_nl:].strip()
        sections[header] = body
    return sections


def _parse_adr_file(filepath: str) -> ADRDocument:
    """Parse a single ADR markdown file into an ADRDocument."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    mtime = os.path.getmtime(filepath)
    timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    title = _extract_title(content)
    sections = _extract_sections(content)

    if sections:
        return ADRDocument(
            path=filepath,
            title=title,
            status=sections.get("status", ""),
            context=sections.get("context", ""),
            decision=sections.get("decision", ""),
            consequences=sections.get("consequences", ""),
            timestamp=timestamp,
        )

    # Fallback: no section headers — treat entire body as freeform
    return ADRDocument(
        path=filepath,
        title=title,
        status="",
        context=content.strip(),
        decision="",
        consequences="",
        timestamp=timestamp,
    )


def scan_adr_dir(path: str) -> list[ADRDocument]:
    """Scan a directory for ``*.md`` files and extract ADR sections.

    Args:
        path: Directory path containing ADR markdown files.

    Returns:
        A list of parsed ADRDocument instances.
    """
    results: list[ADRDocument] = []
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
        results.append(_parse_adr_file(filepath))

    return results
