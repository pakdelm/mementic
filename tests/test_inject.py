"""Tests for mementic.inject — template loading, agent detection, and enforcement injection."""

from __future__ import annotations

from pathlib import Path

import pytest

from mementic.inject import (
    _ENFORCEMENT_HEADING,
    detect_agents,
    get_mcp_config_file,
    get_rules_file,
    has_enforcement,
    inject_enforcement,
    load_template,
)


# ---------------------------------------------------------------------------
# load_template
# ---------------------------------------------------------------------------


def test_load_template_returns_string() -> None:
    """load_template returns a non-empty string."""
    result = load_template()
    assert isinstance(result, str)
    assert len(result) > 0


def test_load_template_contains_heading() -> None:
    """The loaded template contains the enforcement heading."""
    template = load_template()
    assert _ENFORCEMENT_HEADING in template


def test_load_template_contains_tool_names() -> None:
    """The template references all five mementic MCP tool names."""
    template = load_template()
    for tool in (
        "mementic_get_rules",
        "mementic_record_decision",
        "mementic_record_constraint",
        "mementic_deprecate",
        "mementic_get_file_history",
    ):
        assert tool in template, f"Template missing reference to {tool}"


# ---------------------------------------------------------------------------
# detect_agents
# ---------------------------------------------------------------------------


def test_detect_agents_finds_opencode(tmp_path: Path) -> None:
    """An .opencode directory is detected as 'opencode'."""
    (tmp_path / ".opencode").mkdir()
    assert detect_agents(tmp_path) == ["opencode"]


def test_detect_agents_finds_multiple(tmp_path: Path) -> None:
    """Multiple agent markers are all detected."""
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".cursor").mkdir()
    result = detect_agents(tmp_path)
    assert "opencode" in result
    assert "cursor" in result


def test_detect_agents_empty_dir(tmp_path: Path) -> None:
    """An empty directory yields no agents."""
    assert detect_agents(tmp_path) == []


def test_detect_agents_no_markers(tmp_path: Path) -> None:
    """A directory with unrelated subdirs yields no agents."""
    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    assert detect_agents(tmp_path) == []


# ---------------------------------------------------------------------------
# has_enforcement
# ---------------------------------------------------------------------------


def test_has_enforcement_true(tmp_path: Path) -> None:
    """A file containing the heading returns True."""
    md = tmp_path / "AGENTS.md"
    md.write_text(
        f"# My Project\n\n{_ENFORCEMENT_HEADING}\n\nMore content.\n",
        encoding="utf-8",
    )
    assert has_enforcement(md) is True


def test_has_enforcement_false_no_heading(tmp_path: Path) -> None:
    """A file without the heading returns False."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# My Project\n\nSome other content.\n", encoding="utf-8")
    assert has_enforcement(md) is False


def test_has_enforcement_false_no_file(tmp_path: Path) -> None:
    """A non-existent file returns False."""
    md = tmp_path / "AGENTS.md"
    assert has_enforcement(md) is False


def test_has_enforcement_empty_file(tmp_path: Path) -> None:
    """An empty file returns False."""
    md = tmp_path / "AGENTS.md"
    md.write_text("", encoding="utf-8")
    assert has_enforcement(md) is False


# ---------------------------------------------------------------------------
# get_rules_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("claude-code", "CLAUDE.md"),
        ("cursor", ".cursorrules"),
        ("windsurf", ".windsurfrules"),
    ],
)
def test_get_rules_file_known(agent: str, expected: str) -> None:
    """Known non-OpenCode agents return their rules file path."""
    assert get_rules_file(agent) == expected


def test_get_rules_file_opencode() -> None:
    """OpenCode has no separate rules file."""
    assert get_rules_file("opencode") is None


def test_get_rules_file_unknown() -> None:
    """An unknown agent returns None."""
    assert get_rules_file("nonexistent-agent") is None


# ---------------------------------------------------------------------------
# get_mcp_config_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ("opencode", "opencode.json"),
        ("claude-code", ".claude/mcp.json"),
        ("cursor", ".cursor/mcp.json"),
        ("windsurf", ".windsurf/mcp.json"),
    ],
)
def test_get_mcp_config_known(agent: str, expected: str) -> None:
    """Known agents return their MCP config file path."""
    assert get_mcp_config_file(agent) == expected


def test_get_mcp_config_unknown() -> None:
    """An unknown agent returns None."""
    assert get_mcp_config_file("nonexistent-agent") is None


# ---------------------------------------------------------------------------
# inject_enforcement — live writes
# ---------------------------------------------------------------------------


def test_inject_creates_new_file(tmp_path: Path) -> None:
    """When AGENTS.md doesn't exist, inject_enforcement creates it."""
    md = tmp_path / "AGENTS.md"
    result = inject_enforcement(md)

    assert "Created" in result
    assert md.exists()
    content = md.read_text(encoding="utf-8")
    assert _ENFORCEMENT_HEADING in content


def test_inject_appends_to_existing(tmp_path: Path) -> None:
    """Existing AGENTS.md gets the enforcement block appended."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# My Project\n", encoding="utf-8")

    result = inject_enforcement(md)

    assert "Appended" in result
    content = md.read_text(encoding="utf-8")
    assert content.startswith("# My Project\n")
    assert _ENFORCEMENT_HEADING in content


def test_inject_appends_with_newline(tmp_path: Path) -> None:
    """Existing file ending with a single newline gets proper spacing."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# Title\n", encoding="utf-8")

    inject_enforcement(md)
    content = md.read_text(encoding="utf-8")

    # There should be a blank line between the original content and the heading
    assert "\n\n" + _ENFORCEMENT_HEADING in content


def test_inject_skips_if_exists(tmp_path: Path) -> None:
    """If the enforcement heading is already present, injection is skipped."""
    md = tmp_path / "AGENTS.md"
    md.write_text(f"# Project\n\n{_ENFORCEMENT_HEADING}\n", encoding="utf-8")

    result = inject_enforcement(md)

    assert "already exists" in result
    assert "Skipped" in result
    # File content should be unchanged
    assert md.read_text(encoding="utf-8") == f"# Project\n\n{_ENFORCEMENT_HEADING}\n"


# ---------------------------------------------------------------------------
# inject_enforcement — dry_run
# ---------------------------------------------------------------------------


def test_inject_dry_run_new_file(tmp_path: Path) -> None:
    """Dry run for a non-existent file returns what would be created."""
    md = tmp_path / "AGENTS.md"
    result = inject_enforcement(md, dry_run=True)

    assert "Would create" in result
    assert _ENFORCEMENT_HEADING in result
    assert not md.exists(), "Dry run must not create the file"


def test_inject_dry_run_existing(tmp_path: Path) -> None:
    """Dry run for an existing file returns what would be appended."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# Project\n", encoding="utf-8")

    result = inject_enforcement(md, dry_run=True)

    assert "Would append" in result
    assert _ENFORCEMENT_HEADING in result
    # File content should be unchanged
    assert md.read_text(encoding="utf-8") == "# Project\n"


def test_inject_dry_run_skips_if_exists(tmp_path: Path) -> None:
    """Dry run returns skip message if enforcement already present."""
    md = tmp_path / "AGENTS.md"
    md.write_text(
        f"# Project\n\n{_ENFORCEMENT_HEADING}\n", encoding="utf-8"
    )

    result = inject_enforcement(md, dry_run=True)

    assert "already exists" in result
    assert "Skipped" in result


def test_inject_result_contains_heading(tmp_path: Path) -> None:
    """The injected file content always contains the enforcement heading."""
    md = tmp_path / "AGENTS.md"
    inject_enforcement(md)

    content = md.read_text(encoding="utf-8")
    assert _ENFORCEMENT_HEADING in content


def test_inject_appends_double_newline_ending(tmp_path: Path) -> None:
    """File already ending with \\n\\n gets template appended without extra blank lines."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# Title\n\n", encoding="utf-8")

    inject_enforcement(md)
    content = md.read_text(encoding="utf-8")

    # Should not have three consecutive newlines
    assert "\n\n\n" not in content
    assert _ENFORCEMENT_HEADING in content


def test_inject_appends_no_trailing_newline(tmp_path: Path) -> None:
    """File with no trailing newline gets two newlines before the block."""
    md = tmp_path / "AGENTS.md"
    md.write_text("# Title", encoding="utf-8")

    inject_enforcement(md)
    content = md.read_text(encoding="utf-8")

    assert "\n\n" + _ENFORCEMENT_HEADING in content
