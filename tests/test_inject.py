"""Tests for mementic.inject — template loading and enforcement section sync."""

from __future__ import annotations

from pathlib import Path

from mementic.inject import (
    _END_MARKER,
    _START_MARKER,
    load_template,
    sync_enforcement,
)


def _block() -> str:
    """The current marker-wrapped enforcement block with trailing newline."""
    return load_template().strip() + "\n"


def _stale_block() -> str:
    """A marker-wrapped block from an older mementic version."""
    return (
        f"{_START_MARKER}\n## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)\n\n"
        f"Outdated enforcement content.\n{_END_MARKER}\n"
    )


def test_load_template_returns_string() -> None:
    """load_template returns a non-empty string."""
    result = load_template()
    assert isinstance(result, str)
    assert len(result) > 0


def test_load_template_contains_markers() -> None:
    """The loaded template is wrapped in sync markers."""
    template = load_template()
    assert template.startswith(_START_MARKER)
    assert template.strip().endswith(_END_MARKER)


def test_load_template_contains_heading() -> None:
    """The loaded template contains the enforcement heading."""
    template = load_template()
    assert "## Mementic Memory (HARD CONSTRAINT" in template


def test_load_template_contains_cli_commands() -> None:
    """The template references all mementic CLI commands."""
    template = load_template()
    for command in (
        "query keywords",
        "add decision",
        "add constraint",
        "deprecate",
        "query file",
    ):
        assert command in template, f"Template missing reference to {command}"


def test_sync_creates_missing_file(tmp_path: Path) -> None:
    """When AGENTS.md does not exist, sync creates it with the block."""
    md = tmp_path / "AGENTS.md"
    result = sync_enforcement(md)

    assert "Created" in result
    assert md.exists()
    assert md.read_text(encoding="utf-8") == _block()


def test_sync_appends_to_existing_preserving_user_content(tmp_path: Path) -> None:
    """A file without a section gets the block appended; user content stays."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# My Project\n\nSome user rules.\n", encoding="utf-8")

    result = sync_enforcement(md)

    assert "Appended" in result
    content = md.read_text(encoding="utf-8")
    assert content.startswith("# My Project\n\nSome user rules.\n")
    assert "## Mementic Memory (HARD CONSTRAINT" in content


def test_sync_replaces_between_markers_preserving_surrounding_content(
    tmp_path: Path,
) -> None:
    """User content above and below the marker section survives byte-for-byte."""
    md = tmp_path / "AGENTS.md"
    above = "# My Project\n\nUser rules above.\n\n"
    below = "\n## User Section\n\nContent below the section.\n"
    md.write_text(f"{above}{_stale_block()}{below}", encoding="utf-8")

    result = sync_enforcement(md)

    assert "Updated" in result
    content = md.read_text(encoding="utf-8")
    assert content.startswith(above)
    assert content.endswith(below)
    assert _stale_block() not in content
    assert _block() in content


def test_sync_is_idempotent(tmp_path: Path) -> None:
    """Syncing twice with the same template yields a byte-identical file."""
    md = tmp_path / "AGENTS.md"
    sync_enforcement(md)
    first = md.read_text(encoding="utf-8")

    result = sync_enforcement(md)

    assert "already up to date" in result
    assert md.read_text(encoding="utf-8") == first


def test_sync_updates_older_section_in_place(tmp_path: Path) -> None:
    """A newer mementic template replaces an older section between markers."""
    md = tmp_path / "AGENTS.md"
    md.write_text(f"# Project\n\n{_stale_block()}", encoding="utf-8")

    sync_enforcement(md)

    content = md.read_text(encoding="utf-8")
    assert "Outdated enforcement content." not in content
    assert content.count(_START_MARKER) == 1
    assert content.count(_END_MARKER) == 1


def test_sync_warns_on_corrupt_markers(tmp_path: Path) -> None:
    """A start marker without an end marker produces a warning and no write."""
    md = tmp_path / "AGENTS.md"
    original = f"# Project\n\n{_START_MARKER}\nBroken section.\n"
    md.write_text(original, encoding="utf-8")

    result = sync_enforcement(md)

    assert "Warning" in result
    assert "manual fix" in result
    assert md.read_text(encoding="utf-8") == original


def test_sync_dry_run_does_not_write(tmp_path: Path) -> None:
    """Dry run returns the action description without touching the file."""
    md = tmp_path / "AGENTS.md"
    md.write_text(f"# Project\n\n{_stale_block()}", encoding="utf-8")
    original = md.read_text(encoding="utf-8")

    result = sync_enforcement(md, dry_run=True)

    assert "Would update" in result
    assert md.read_text(encoding="utf-8") == original


def test_sync_dry_run_missing_file(tmp_path: Path) -> None:
    """Dry run on a missing file reports creation without writing."""
    md = tmp_path / "AGENTS.md"

    result = sync_enforcement(md, dry_run=True)

    assert "Would create" in result
    assert not md.exists()
