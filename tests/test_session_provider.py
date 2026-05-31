"""Tests for mementic.session provider system.

Covers SessionProvider implementations (OpenCode, Claude Code, Generic)
and the SessionProviderRegistry.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mementic.session import (
    get_registry,
)
from mementic.session.claude_code import (
    DEFAULT_CLAUDECODE_PATHS,
    ClaudeCodeSessionProvider,
)
from mementic.session.generic import GenericSessionProvider
from mementic.session.opencode import (
    DEFAULT_SESSION_PATHS,
    OpenCodeSessionProvider,
)


# ── Registry Tests ────────────────────────────────────────────────────


class TestRegistry:
    """Tests for SessionProviderRegistry and auto-registration."""

    def test_registry_has_three_builtin_providers(self) -> None:
        """Verify 'opencode', 'claude-code', 'generic' are auto-registered."""
        registry = get_registry()
        assert registry.get("opencode") is not None
        assert registry.get("claude-code") is not None
        assert registry.get("generic") is not None

    def test_registry_get_returns_instance(self) -> None:
        """get('opencode') returns an OpenCodeSessionProvider."""
        registry = get_registry()
        provider = registry.get("opencode")
        assert provider is not None
        assert isinstance(provider, OpenCodeSessionProvider)

    def test_registry_get_unknown_returns_none(self) -> None:
        """get('nonexistent') returns None."""
        registry = get_registry()
        assert registry.get("nonexistent") is None

    def test_registry_detect_by_agent_explicit_path(self) -> None:
        """detect(agent='opencode', session_path='/some/path') returns provider and path."""
        registry = get_registry()
        provider, found = registry.detect(agent="opencode", session_path="/some/path")
        assert isinstance(provider, OpenCodeSessionProvider)
        assert found == "/some/path"

    def test_registry_detect_by_agent_no_path_uses_find_latest(self) -> None:
        """detect(agent='opencode') calls find_latest_session and returns its result."""
        registry = get_registry()
        provider = registry.get("opencode")
        assert provider is not None
        with patch.object(provider, "find_latest_session", return_value="/fake/session.json"):
            _, found = registry.detect(agent="opencode")
        assert found == "/fake/session.json"

    def test_registry_detect_by_agent_with_log_dirs_override(self) -> None:
        """detect(agent='opencode', log_dirs=...) passes override to find_latest_session."""
        registry = get_registry()
        provider = registry.get("opencode")
        assert provider is not None
        log_dirs = {"opencode": ["/custom/logs"]}
        with patch.object(provider, "find_latest_session", return_value="/custom/logs/session.json") as mock_find:
            _, found = registry.detect(agent="opencode", log_dirs=log_dirs)
        mock_find.assert_called_once_with(["/custom/logs"])
        assert found == "/custom/logs/session.json"

    def test_registry_detect_unknown_agent_raises(self) -> None:
        """detect(agent='nonexistent') raises ValueError."""
        registry = get_registry()
        with pytest.raises(ValueError, match="Unknown agent"):
            registry.detect(agent="nonexistent")

    def test_registry_detect_by_session_path_uses_first_valid_provider(
        self, tmp_path: Path
    ) -> None:
        """detect(session_path=<valid file>) returns first provider that can read it."""
        session_file = tmp_path / "test.json"
        session_file.write_text('{"key": "value"}', encoding="utf-8")

        registry = get_registry()
        provider, found = registry.detect(session_path=str(session_file))
        # First provider in insertion order (opencode) should succeed reading
        assert isinstance(provider, OpenCodeSessionProvider)
        assert found == str(session_file)

    def test_registry_detect_auto_no_sessions_returns_generic(self) -> None:
        """When no sessions exist, returns generic provider with None path."""
        registry = get_registry()
        # Patch all built-in providers to return None
        with (
            patch.object(registry.get("opencode"), "find_latest_session", return_value=None),
            patch.object(registry.get("claude-code"), "find_latest_session", return_value=None),
            patch.object(registry.get("generic"), "find_latest_session", return_value=None),
        ):
            provider, found = registry.detect()
        assert isinstance(provider, GenericSessionProvider)
        assert found is None

    def test_registry_detect_auto_finds_most_recent(
        self, tmp_path: Path
    ) -> None:
        """detect() returns the provider and path of the most recently modified session."""
        # Set up: opencode dir with an older file, claude dir with a newer .jsonl file
        opencode_dir = tmp_path / "opencode_sessions"
        opencode_dir.mkdir()
        opencode_file = opencode_dir / "session_old.json"
        opencode_file.write_text("old", encoding="utf-8")

        claude_dir = tmp_path / "claude_projects" / "project-abc"
        claude_dir.mkdir(parents=True)
        claude_file = claude_dir / "session_new.jsonl"
        claude_file.write_text("new", encoding="utf-8")

        # Set mtimes: opencode older, claude newer
        os.utime(str(opencode_file), (1000, 1000))
        os.utime(str(claude_file), (2000, 2000))

        registry = get_registry()

        # Patch platform defaults to point to our temp dirs
        fake_opencode = {"linux": [str(opencode_dir)]}
        fake_claude = {"linux": [str(claude_dir.parent)]}

        with (
            patch.dict(DEFAULT_SESSION_PATHS, fake_opencode, clear=True),
            patch.dict(DEFAULT_CLAUDECODE_PATHS, fake_claude, clear=True),
            patch.object(registry.get("generic"), "find_latest_session", return_value=None),
        ):
            provider, found = registry.detect()

        assert provider is not None
        assert provider.agent_name == "claude-code"
        assert found is not None
        assert Path(found).name == "session_new.jsonl"


# ── OpenCode Provider Tests ───────────────────────────────────────────


class TestOpenCodeProvider:
    """Tests for OpenCodeSessionProvider."""

    @pytest.fixture
    def provider(self) -> OpenCodeSessionProvider:
        return OpenCodeSessionProvider()

    def test_agent_name(self, provider: OpenCodeSessionProvider) -> None:
        """agent_name returns 'opencode'."""
        assert provider.agent_name == "opencode"

    def test_find_latest_returns_none_when_no_dirs(
        self, provider: OpenCodeSessionProvider
    ) -> None:
        """find_latest_session returns None when no directories exist."""
        fake_paths = {"linux": ["/nonexistent/path/"]}
        with patch.dict(DEFAULT_SESSION_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_returns_none_when_dir_empty(
        self, provider: OpenCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session returns None when directory has no files."""
        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_SESSION_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_finds_newest_file(
        self, provider: OpenCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session returns the file with the highest mtime."""
        older = tmp_path / "older.txt"
        newer = tmp_path / "newer.txt"
        older.write_text("old", encoding="utf-8")
        newer.write_text("new", encoding="utf-8")

        os.utime(str(older), (1000, 1000))
        os.utime(str(newer), (2000, 2000))

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_SESSION_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert Path(result).name == "newer.txt"

    def test_find_latest_with_log_dirs_override(
        self, provider: OpenCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session uses explicit log_dirs when provided."""
        test_file = tmp_path / "custom.json"
        test_file.write_text("custom", encoding="utf-8")

        result = provider.find_latest_session(log_dirs=[str(tmp_path)])
        assert result == str(test_file)

    def test_find_latest_skips_subdirs(
        self, provider: OpenCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session only checks files, not subdirectories."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested", encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_SESSION_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        # subdir is not a file, nested.txt is inside a subdir (not direct child)
        assert result is None

    def test_read_log_reads_file(
        self, provider: OpenCodeSessionProvider, tmp_path: Path
    ) -> None:
        """read_session_log reads and returns file contents."""
        session_file = tmp_path / "session.json"
        session_file.write_text('{"messages": ["hello"]}', encoding="utf-8")

        content = provider.read_session_log(str(session_file))
        assert content == '{"messages": ["hello"]}'

    def test_find_diffs_returns_none(self, provider: OpenCodeSessionProvider) -> None:
        """find_session_diffs always returns None for opencode."""
        result = provider.find_session_diffs("/any/path.json")
        assert result is None


# ── Claude Code Provider Tests ────────────────────────────────────────


class TestClaudeCodeProvider:
    """Tests for ClaudeCodeSessionProvider."""

    @pytest.fixture
    def provider(self) -> ClaudeCodeSessionProvider:
        return ClaudeCodeSessionProvider()

    def test_agent_name(self, provider: ClaudeCodeSessionProvider) -> None:
        """agent_name returns 'claude-code'."""
        assert provider.agent_name == "claude-code"

    def test_find_latest_returns_none_when_no_dirs(
        self, provider: ClaudeCodeSessionProvider
    ) -> None:
        """find_latest_session returns None when no directories exist."""
        fake_paths = {"linux": ["/nonexistent/claude/projects/"]}
        with patch.dict(DEFAULT_CLAUDECODE_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_returns_none_when_dir_empty(
        self, provider: ClaudeCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session returns None when directory has no .jsonl files."""
        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_CLAUDECODE_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_finds_jsonl_files(
        self, provider: ClaudeCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session recursively finds the newest .jsonl file."""
        proj_dir = tmp_path / "project-1"
        proj_dir.mkdir()
        jsonl_file = proj_dir / "session.jsonl"
        jsonl_file.write_text('{"type":"message"}', encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_CLAUDECODE_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert Path(result).name == "session.jsonl"

    def test_find_latest_ignores_non_jsonl_files(
        self, provider: ClaudeCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session ignores .txt and other non-.jsonl files."""
        proj_dir = tmp_path / "project-1"
        proj_dir.mkdir()
        (proj_dir / "notes.txt").write_text("text", encoding="utf-8")
        (proj_dir / "data.csv").write_text("csv", encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_CLAUDECODE_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_picks_newest_among_multiple_jsonl(
        self, provider: ClaudeCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session picks the .jsonl file with highest mtime."""
        proj_a = tmp_path / "project-a"
        proj_b = tmp_path / "project-b"
        proj_a.mkdir()
        proj_b.mkdir()

        older = proj_a / "old.jsonl"
        newer = proj_b / "new.jsonl"
        older.write_text("old", encoding="utf-8")
        newer.write_text("new", encoding="utf-8")

        os.utime(str(older), (1000, 1000))
        os.utime(str(newer), (2000, 2000))

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_CLAUDECODE_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert Path(result).name == "new.jsonl"

    def test_find_latest_with_log_dirs_override(
        self, provider: ClaudeCodeSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session uses explicit log_dirs when provided."""
        proj_dir = tmp_path / "custom-project"
        proj_dir.mkdir()
        jsonl_file = proj_dir / "custom.jsonl"
        jsonl_file.write_text('{"custom": true}', encoding="utf-8")

        result = provider.find_latest_session(log_dirs=[str(tmp_path)])
        assert result == str(jsonl_file)

    def test_read_log_reads_jsonl_file(
        self, provider: ClaudeCodeSessionProvider, tmp_path: Path
    ) -> None:
        """read_session_log reads a .jsonl session file."""
        session_file = tmp_path / "session.jsonl"
        session_file.write_text('{"role":"user","content":"hello"}\n{"role":"assistant","content":"hi"}', encoding="utf-8")

        content = provider.read_session_log(str(session_file))
        assert "hello" in content
        assert "hi" in content

    def test_find_diffs_returns_none(self, provider: ClaudeCodeSessionProvider) -> None:
        """find_session_diffs always returns None for claude-code."""
        result = provider.find_session_diffs("/any/path.jsonl")
        assert result is None


# ── Generic Provider Tests ────────────────────────────────────────────


class TestGenericProvider:
    """Tests for GenericSessionProvider."""

    @pytest.fixture
    def provider(self) -> GenericSessionProvider:
        return GenericSessionProvider()

    def test_agent_name(self, provider: GenericSessionProvider) -> None:
        """agent_name returns 'generic'."""
        assert provider.agent_name == "generic"

    def test_find_latest_returns_none_without_log_dirs(
        self, provider: GenericSessionProvider
    ) -> None:
        """find_latest_session returns None when log_dirs is None."""
        result = provider.find_latest_session()
        assert result is None

    def test_find_latest_returns_none_with_empty_log_dirs(
        self, provider: GenericSessionProvider
    ) -> None:
        """find_latest_session returns None when log_dirs is an empty list."""
        result = provider.find_latest_session(log_dirs=[])
        assert result is None

    def test_find_latest_returns_none_when_dirs_missing(
        self, provider: GenericSessionProvider
    ) -> None:
        """find_latest_session returns None when all directories are nonexistent."""
        result = provider.find_latest_session(log_dirs=["/nonexistent/dir/"])
        assert result is None

    def test_find_latest_returns_none_when_dir_empty(
        self, provider: GenericSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session returns None when dir has no files."""
        result = provider.find_latest_session(log_dirs=[str(tmp_path)])
        assert result is None

    def test_find_latest_finds_newest_file_with_log_dirs(
        self, provider: GenericSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session finds the newest file in configured dirs."""
        older = tmp_path / "older.log"
        newer = tmp_path / "newer.log"
        older.write_text("old", encoding="utf-8")
        newer.write_text("new", encoding="utf-8")

        os.utime(str(older), (1000, 1000))
        os.utime(str(newer), (2000, 2000))

        result = provider.find_latest_session(log_dirs=[str(tmp_path)])
        assert result is not None
        assert Path(result).name == "newer.log"

    def test_read_log_reads_file(
        self, provider: GenericSessionProvider, tmp_path: Path
    ) -> None:
        """read_session_log reads a generic session log file."""
        session_file = tmp_path / "anyfile.txt"
        session_file.write_text("plain text session log", encoding="utf-8")

        content = provider.read_session_log(str(session_file))
        assert content == "plain text session log"

    def test_find_diffs_returns_none(self, provider: GenericSessionProvider) -> None:
        """find_session_diffs always returns None for generic provider."""
        result = provider.find_session_diffs("/any/path.txt")
        assert result is None


# ── Integrated Registry → CLI-like Tests ──────────────────────────────


class TestRegistryDetectEdgeCases:
    """Tests for detect() covering edge cases not in the main Registry class."""

    def test_detect_explicit_session_with_unreadable_file_falls_back_to_generic(
        self, tmp_path: Path
    ) -> None:
        """When no provider can read a file, detect falls back to generic."""
        # Create a file, then mock each provider to fail reading it
        session_file = tmp_path / "bad.json"
        session_file.write_text("content", encoding="utf-8")

        registry = get_registry()
        with (
            patch.object(
                registry.get("opencode"), "read_session_log",
                side_effect=OSError("permission denied"),
            ),
            patch.object(
                registry.get("claude-code"), "read_session_log",
                side_effect=OSError("permission denied"),
            ),
        ):
            provider, found = registry.detect(session_path=str(session_file))
        assert isinstance(provider, GenericSessionProvider)
        assert found == str(session_file)

    def test_detect_auto_skips_oserror_during_stat(self, tmp_path: Path) -> None:
        """detect() skips providers whose found files raise OSError on stat."""
        registry = get_registry()

        # Create a file, then delete it so Path.stat() raises FileNotFoundError
        # (a subclass of OSError), simulating a race condition where a session
        # file disappears between discovery and stat.
        orphan_path = tmp_path / "orphan_session.json"
        orphan_path.write_text("data", encoding="utf-8")

        class RaceConditionProvider:
            agent_name = "racey"

            def find_latest_session(self, log_dirs=None):
                return str(orphan_path)

            def read_session_log(self, path):
                return Path(path).read_text(encoding="utf-8")

            def find_session_diffs(self, session_path):
                return None

        # Delete the file so stat() will fail naturally (FileNotFoundError is OSError)
        orphan_path.unlink()

        registry.register(RaceConditionProvider())
        try:
            with (
                patch.object(registry.get("opencode"), "find_latest_session", return_value=None),
                patch.object(registry.get("claude-code"), "find_latest_session", return_value=None),
                patch.object(registry.get("generic"), "find_latest_session", return_value=None),
            ):
                provider, found = registry.detect()
            # The racey provider's file is gone, so it's skipped via OSError catch
            # All other providers return None, so we get generic + None
            assert isinstance(provider, GenericSessionProvider)
            assert found is None
        finally:
            registry._providers.pop("racey", None)

    def test_detect_with_session_path_tries_all_providers(
        self, tmp_path: Path
    ) -> None:
        """detect(session_path=...) tries each provider in order until one succeeds."""
        # Patch opencode to fail, claude-code to succeed
        session_file = tmp_path / "claude_session.jsonl"
        session_file.write_text('{"role":"user"}', encoding="utf-8")

        registry = get_registry()
        with patch.object(
            registry.get("opencode"), "read_session_log",
            side_effect=OSError("cannot read"),
        ):
            provider, found = registry.detect(session_path=str(session_file))
        # opencode failed, claude-code succeeded
        assert isinstance(provider, ClaudeCodeSessionProvider)
        assert found == str(session_file)

    def test_detect_session_path_all_providers_fail_falls_back_to_generic(
        self, tmp_path: Path
    ) -> None:
        """detect(session_path=...) falls back to generic when no provider can read it."""
        session_file = tmp_path / "unreadable.json"
        session_file.write_text("data", encoding="utf-8")

        registry = get_registry()
        with (
            patch.object(registry.get("opencode"), "read_session_log",
                         side_effect=OSError("cannot read")),
            patch.object(registry.get("claude-code"), "read_session_log",
                         side_effect=OSError("cannot read")),
            patch.object(registry.get("generic"), "read_session_log",
                         side_effect=OSError("cannot read")),
        ):
            provider, found = registry.detect(session_path=str(session_file))
        # All providers fail — falls back to generic (hardcoded on line ~187)
        assert isinstance(provider, GenericSessionProvider)
        assert found == str(session_file)

    def test_detect_log_dirs_none_defaults_to_empty(self) -> None:
        """detect(log_dirs=None) works with providers that have no defaults."""
        registry = get_registry()
        # log_dirs=None should be treated as empty dict
        # generic has no defaults, so it returns None
        # opencode has built-in defaults — but we can patch to None
        with (
            patch.object(registry.get("opencode"), "find_latest_session", return_value=None),
            patch.object(registry.get("claude-code"), "find_latest_session", return_value=None),
            patch.object(registry.get("generic"), "find_latest_session", return_value=None),
        ):
            provider, found = registry.detect(log_dirs=None)
        assert isinstance(provider, GenericSessionProvider)
        assert found is None
