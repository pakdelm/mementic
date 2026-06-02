"""Tests for the Windsurf session provider."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mementic.session.windsurf import (
    DEFAULT_WINDSURF_PATHS,
    WindsurfSessionProvider,
)


class TestWindsurfProvider:
    """Tests for WindsurfSessionProvider."""

    @pytest.fixture
    def provider(self) -> WindsurfSessionProvider:
        return WindsurfSessionProvider()

    def test_agent_name_is_windsurf(self, provider: WindsurfSessionProvider) -> None:
        """agent_name returns 'windsurf'."""
        assert provider.agent_name == "windsurf"

    def test_find_latest_returns_none_when_no_dirs(
        self, provider: WindsurfSessionProvider
    ) -> None:
        """find_latest_session returns None when no directories exist."""
        fake_paths = {"linux": ["/nonexistent/windsurf/path/"]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_returns_none_when_dir_empty(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session returns None when directory has no files."""
        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_finds_json_file(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session finds .json session files."""
        session_dir = tmp_path / "workspaceStorage" / "abc123"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "session.json"
        session_file.write_text('{"messages": []}', encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert "session.json" in str(result)

    def test_find_latest_finds_log_file(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session finds .log session files."""
        session_dir = tmp_path / "workspaceStorage" / "def456"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "agent.log"
        session_file.write_text("log content", encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert "agent.log" in str(result)

    def test_find_latest_finds_txt_file(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session finds .txt session files."""
        session_dir = tmp_path / "workspaceStorage" / "ghi789"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "chat.txt"
        session_file.write_text("chat content", encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert "chat.txt" in str(result)

    def test_find_latest_picks_most_recent(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session returns the file with the highest mtime."""
        session_dir = tmp_path / "workspaceStorage" / "proj"
        session_dir.mkdir(parents=True)

        old_file = session_dir / "old.json"
        new_file = session_dir / "new.log"
        old_file.write_text("old", encoding="utf-8")
        new_file.write_text("new", encoding="utf-8")

        os.utime(str(old_file), (1000, 1000))
        os.utime(str(new_file), (2000, 2000))

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert "new.log" in str(result)

    def test_find_latest_scans_recursively(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session scans nested workspaceStorage subdirectories."""
        nested = tmp_path / "deep" / "nested" / "dir"
        nested.mkdir(parents=True)
        session = nested / "session.json"
        session.write_text("nested data", encoding="utf-8")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is not None
        assert "session.json" in str(result)

    def test_find_latest_ignores_non_matching_extensions(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session ignores files that are not .json/.log/.txt."""
        session_dir = tmp_path / "workspaceStorage" / "proj"
        session_dir.mkdir(parents=True)
        (session_dir / "data.csv").write_text("csv", encoding="utf-8")
        (session_dir / "image.png").write_bytes(b"png")

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict(DEFAULT_WINDSURF_PATHS, fake_paths, clear=True):
            result = provider.find_latest_session()
        assert result is None

    def test_find_latest_with_log_dirs_override(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """find_latest_session uses explicit log_dirs when provided."""
        session_dir = tmp_path / "custom_sessions"
        session_dir.mkdir()
        session_file = session_dir / "custom.json"
        session_file.write_text('{"custom": true}', encoding="utf-8")

        result = provider.find_latest_session(log_dirs=[str(tmp_path)])
        assert result == str(session_file)

    def test_read_session_log_reads_text(
        self, provider: WindsurfSessionProvider, tmp_path: Path
    ) -> None:
        """read_session_log reads and returns file contents."""
        session_file = tmp_path / "test.json"
        session_file.write_text("hello world", encoding="utf-8")

        content = provider.read_session_log(str(session_file))
        assert content == "hello world"

    def test_find_session_diffs_returns_none(
        self, provider: WindsurfSessionProvider
    ) -> None:
        """find_session_diffs always returns None for windsurf."""
        result = provider.find_session_diffs("/some/path.json")
        assert result is None
