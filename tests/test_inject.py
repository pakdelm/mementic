"""Tests for mementic.inject — template loading and enforcement section sync."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mementic.inject import (
    END_MARKER,
    START_MARKER,
    load_template,
    sync_enforcement,
    sync_gitignore,
)


def _block() -> str:
    """The current marker-wrapped enforcement block with trailing newline."""
    return load_template().strip() + "\n"


def _stale_block() -> str:
    """A marker-wrapped block from an older mementic version."""
    return (
        f"{START_MARKER}\n## Mementic Memory (HARD CONSTRAINT — ALWAYS ACTIVE)\n\n"
        f"Outdated enforcement content.\n{END_MARKER}\n"
    )


def test_load_template_returns_string() -> None:
    """load_template returns a non-empty string."""
    result = load_template()
    assert isinstance(result, str)
    assert len(result) > 0


def test_load_template_contains_markers() -> None:
    """The loaded template is wrapped in sync markers."""
    template = load_template()
    assert template.startswith(START_MARKER)
    assert template.strip().endswith(END_MARKER)


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
    assert content.count(START_MARKER) == 1
    assert content.count(END_MARKER) == 1


def test_sync_warns_on_corrupt_markers(tmp_path: Path) -> None:
    """A start marker without an end marker produces a warning and no write."""
    md = tmp_path / "AGENTS.md"
    original = f"# Project\n\n{START_MARKER}\nBroken section.\n"
    md.write_text(original, encoding="utf-8")

    result = sync_enforcement(md)

    assert "Warning" in result
    assert "manual fix" in result
    assert md.read_text(encoding="utf-8") == original


def test_sync_enforcement_warns_when_write_fails(tmp_path: Path, monkeypatch) -> None:
    """A read-only target returns a warning instead of raising."""
    md = tmp_path / "AGENTS.md"

    def fail(path: Path, content: str) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr("mementic.inject._write_text_atomic", fail)

    result = sync_enforcement(md)

    assert "Warning" in result
    assert "permissions" in result
    assert not md.exists()


def test_sync_enforcement_leaves_target_untouched_on_replace_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed replace leaves the existing file and no temporary file."""
    md = tmp_path / "AGENTS.md"
    original = "# Project\n"
    md.write_text(original, encoding="utf-8")

    def fail(src: str, dst: Path) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr("mementic.inject.os.replace", fail)

    result = sync_enforcement(md)

    assert "Warning" in result
    assert md.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".AGENTS.md.*.tmp")) == []


def test_sync_gitignore_adds_entry_in_git_work_tree(tmp_path: Path) -> None:
    """A Git work tree gets .mementic/ appended to .gitignore."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    result = sync_gitignore(tmp_path)

    assert result is not None
    assert "Added" in result
    assert ".mementic/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_sync_gitignore_is_idempotent(tmp_path: Path) -> None:
    """A second sync does not duplicate the entry."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    sync_gitignore(tmp_path)

    assert sync_gitignore(tmp_path) is None
    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert content.count(".mementic/") == 1


def test_sync_gitignore_skips_non_git_directory(tmp_path: Path) -> None:
    """Outside a Git work tree no .gitignore is created."""
    assert sync_gitignore(tmp_path) is None
    assert not (tmp_path / ".gitignore").exists()


def test_sync_gitignore_warns_when_write_fails(tmp_path: Path, monkeypatch) -> None:
    """A read-only .gitignore returns the exact line to add."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    def fail(path: Path, content: str) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr("mementic.inject._write_text_atomic", fail)

    result = sync_gitignore(tmp_path)

    assert result is not None
    assert "Warning" in result
    assert ".mementic/" in result


@pytest.mark.parametrize("entry", ["/.mementic/", ".mementic", ".mementic/**", "**/.mementic/"])
def test_sync_gitignore_recognizes_equivalent_entries(tmp_path: Path, entry: str) -> None:
    """Equivalent ignore entries are not duplicated."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    gitignore = tmp_path / ".gitignore"
    original = f"# Python\n{entry}\n".encode()
    gitignore.write_bytes(original)

    assert sync_gitignore(tmp_path) is None
    assert gitignore.read_bytes() == original


def test_sync_gitignore_preserves_line_endings(tmp_path: Path) -> None:
    """Appending an entry keeps the file's CRLF endings."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_bytes(b"# Python\r\n*.pyc\r\n")

    sync_gitignore(tmp_path)

    assert gitignore.read_bytes() == b"# Python\r\n*.pyc\r\n\r\n.mementic/\r\n"


def test_sync_gitignore_preserves_non_utf8_bytes(tmp_path: Path) -> None:
    """A non-UTF-8 comment survives the append unchanged."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_bytes(b"# caf\xe9\n")

    result = sync_gitignore(tmp_path)

    assert result is not None
    assert gitignore.read_bytes() == b"# caf\xe9\n\n.mementic/\n"


def test_sync_enforcement_preserves_crlf(tmp_path: Path) -> None:
    """Updating a CRLF AGENTS.md keeps every line ending CRLF."""
    md = tmp_path / "AGENTS.md"
    md.write_bytes(b"# Project\r\n\r\n" + _stale_block().replace("\n", "\r\n").encode("utf-8"))

    sync_enforcement(md)

    content = md.read_bytes()
    assert content.startswith(b"# Project\r\n\r\n")
    assert content.count(b"\n") == content.count(b"\r\n")
