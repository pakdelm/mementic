"""Tests for mementic.session."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from mementic.session import expand_path, find_latest_session, read_session_log


class TestExpandPath:
    def test_expands_tilde_to_home(self) -> None:
        result = expand_path("~/projects/mementic")
        expected = str(Path.home() / "projects/mementic")
        assert result == expected

    def test_expands_tilde_with_trailing_slash(self) -> None:
        result = expand_path("~/")
        assert result == str(Path.home())

    def test_expands_windows_env_var(self) -> None:
        with patch.dict(os.environ, {"MY_CUSTOM_VAR": r"C:\custom\path"}):
            result = expand_path("%MY_CUSTOM_VAR%/subdir")
        assert result == r"C:\custom\path/subdir"

    def test_expands_multiple_env_vars(self) -> None:
        with patch.dict(os.environ, {"VAR1": "alpha", "VAR2": "beta"}):
            result = expand_path("%VAR1%/%VAR2%")
        assert result == "alpha/beta"

    def test_no_expansion_for_regular_path(self) -> None:
        result = expand_path("/usr/local/bin")
        assert result == "/usr/local/bin"

    def test_missing_env_var_replaced_with_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = expand_path("%NONEXISTENT%/file.txt")
        assert result == "/file.txt"


class TestFindLatestSession:
    def test_returns_none_when_no_dirs_exist(self) -> None:
        fake_paths = {"linux": ["/nonexistent/sessions/"]}
        with patch.dict("mementic.session.DEFAULT_SESSION_PATHS", fake_paths, clear=True):
            result = find_latest_session()
        assert result is None

    def test_returns_none_when_dir_empty(self, tmp_path: Path) -> None:
        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict("mementic.session.DEFAULT_SESSION_PATHS", fake_paths, clear=True):
            result = find_latest_session()
        assert result is None

    def test_finds_latest_file_by_mtime(self, tmp_path: Path) -> None:
        older = tmp_path / "older.txt"
        newer = tmp_path / "newer.txt"
        unrelated_dir = tmp_path / "subdir"
        unrelated_dir.mkdir()

        older.write_text("old")
        newer.write_text("new")

        # Ensure newer has a later mtime
        newer.touch()
        older.touch()

        fake_paths = {"linux": [str(tmp_path)]}
        with patch.dict("mementic.session.DEFAULT_SESSION_PATHS", fake_paths, clear=True):
            result = find_latest_session()
        assert result is not None
        assert Path(result).name == "newer.txt"


class TestReadSessionLog:
    def test_reads_temp_file_content(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write('{"session": "test-data"}')

        try:
            content = read_session_log(tmp.name)
            assert content == '{"session": "test-data"}'
        finally:
            Path(tmp.name).unlink()

    def test_reads_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write("")

        try:
            content = read_session_log(tmp.name)
            assert content == ""
        finally:
            Path(tmp.name).unlink()
