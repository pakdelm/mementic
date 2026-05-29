"""Tests for ADR and OpenCode source readers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mementic.sources.adr import ADRDocument, scan_adr_dir
from mementic.sources.opencode import InstructionDocument, scan_opencode_instructions


# ---------------------------------------------------------------------------
# ADR tests
# ---------------------------------------------------------------------------

ADR_WITH_SECTIONS = """\
# ADR-0001: Use PostgreSQL as the primary database

## Status
Accepted

## Context
The application needs a relational database with ACID guarantees.

## Decision
We will use PostgreSQL 16 hosted on RDS.

## Consequences
The team must learn PostgreSQL-specific tooling and handle connection pooling.
"""


def test_adr_section_extraction(tmp_path: Path) -> None:
    """ADR files with standard ## sections should be fully parsed."""
    adr_file = tmp_path / "0001-use-postgres.md"
    adr_file.write_text(ADR_WITH_SECTIONS)

    results = scan_adr_dir(str(tmp_path))

    assert len(results) == 1
    doc = results[0]
    assert doc.title == "Use PostgreSQL as the primary database"
    assert doc.status == "Accepted"
    assert "relational database" in doc.context
    assert "PostgreSQL 16" in doc.decision
    assert "connection pooling" in doc.consequences
    assert doc.timestamp


def test_adr_title_with_title_prefix(tmp_path: Path) -> None:
    """Title: prefix should be stripped from the title."""
    content = "# Title: Cache Layer Design\n\n## Status\nProposed\n"
    adr_file = tmp_path / "cache.md"
    adr_file.write_text(content)

    results = scan_adr_dir(str(tmp_path))

    assert len(results) == 1
    assert results[0].title == "Cache Layer Design"


def test_adr_fallback_no_sections(tmp_path: Path) -> None:
    """Files without ## sections should fall back to freeform body as context."""
    content = "# My Notes\n\nJust a freeform document about architecture.\n"
    adr_file = tmp_path / "notes.md"
    adr_file.write_text(content)

    results = scan_adr_dir(str(tmp_path))

    assert len(results) == 1
    doc = results[0]
    assert doc.title == "My Notes"
    assert doc.status == ""
    assert doc.decision == ""
    assert doc.consequences == ""
    assert "freeform document" in doc.context


def test_adr_missing_some_sections(tmp_path: Path) -> None:
    """Files with only some ## sections should have empty strings for missing ones."""
    content = "# ADR-0002\n\n## Status\nDeprecated\n\n## Context\nWas a bad idea.\n"
    adr_file = tmp_path / "0002.md"
    adr_file.write_text(content)

    results = scan_adr_dir(str(tmp_path))

    assert len(results) == 1
    doc = results[0]
    assert doc.status == "Deprecated"
    assert doc.context == "Was a bad idea."
    assert doc.decision == ""
    assert doc.consequences == ""


def test_adr_empty_directory(tmp_path: Path) -> None:
    """Scanning an empty directory should return an empty list."""
    results = scan_adr_dir(str(tmp_path))
    assert results == []


def test_adr_skips_non_md_files(tmp_path: Path) -> None:
    """Non-.md files should be ignored."""
    (tmp_path / "README.txt").write_text("not an adr")
    (tmp_path / "config.yaml").write_text("key: value")

    results = scan_adr_dir(str(tmp_path))
    assert results == []


def test_adr_multiple_files(tmp_path: Path) -> None:
    """Multiple ADR files should all be parsed."""
    (tmp_path / "adr-1.md").write_text(ADR_WITH_SECTIONS)
    content2 = "# ADR-0003\n\n## Status\nSuperseded\n\n## Context\nOld.\n"
    (tmp_path / "adr-2.md").write_text(content2)

    results = scan_adr_dir(str(tmp_path))

    assert len(results) == 2
    titles = {r.title for r in results}
    assert "Use PostgreSQL as the primary database" in titles
    assert "ADR-0003" in titles


def test_adr_file_without_heading(tmp_path: Path) -> None:
    """A file with no # heading at all should get an empty title."""
    (tmp_path / "noheading.md").write_text("Just body text, no headings.")

    results = scan_adr_dir(str(tmp_path))

    assert len(results) == 1
    assert results[0].title == ""
    assert "body text" in results[0].context


# ---------------------------------------------------------------------------
# OpenCode tests
# ---------------------------------------------------------------------------

INSTRUCTION_CONTENT = """\
# Python Code Style

## General Principles

- Write clear, readable code.
- Follow PEP 8.
"""


def test_opencode_extracts_content(tmp_path: Path) -> None:
    """Should read the full markdown body and extract the title."""
    inst_file = tmp_path / "python-style.md"
    inst_file.write_text(INSTRUCTION_CONTENT)

    results = scan_opencode_instructions(str(tmp_path))

    assert len(results) == 1
    doc = results[0]
    assert doc.title == "Python Code Style"
    assert doc.content == INSTRUCTION_CONTENT
    assert "PEP 8" in doc.content


def test_opencode_empty_directory(tmp_path: Path) -> None:
    """Scanning an empty directory should return an empty list."""
    results = scan_opencode_instructions(str(tmp_path))
    assert results == []


def test_opencode_skips_non_md(tmp_path: Path) -> None:
    """Non-.md files should be ignored."""
    (tmp_path / "notes.txt").write_text("some notes")
    (tmp_path / "data.json").write_text("{}")

    results = scan_opencode_instructions(str(tmp_path))
    assert results == []


def test_opencode_multiple_files(tmp_path: Path) -> None:
    """Multiple instruction files should all be read."""
    (tmp_path / "style.md").write_text("# Style Guide\n\nContent A.\n")
    (tmp_path / "testing.md").write_text("# Testing Guide\n\nContent B.\n")

    results = scan_opencode_instructions(str(tmp_path))

    assert len(results) == 2
    titles = {r.title for r in results}
    assert titles == {"Style Guide", "Testing Guide"}


def test_opencode_no_heading(tmp_path: Path) -> None:
    """Files without a # heading should get an empty title."""
    (tmp_path / "plain.md").write_text("Just some text without a heading.")

    results = scan_opencode_instructions(str(tmp_path))

    assert len(results) == 1
    assert results[0].title == ""
    assert results[0].content == "Just some text without a heading."
