"""Cursor session provider.

Discovers session logs from Cursor IDE's workspaceStorage JSON/log files.
"""

from __future__ import annotations

import platform
from pathlib import Path

from mementic.session import SessionProvider, expand_path

DEFAULT_CURSOR_PATHS: dict[str, list[str]] = {
    "linux": ["~/.config/Cursor/User/workspaceStorage/"],
    "darwin": ["~/Library/Application Support/Cursor/User/workspaceStorage/"],
    "win32": ["%APPDATA%/Cursor/User/workspaceStorage/"],
}


class CursorSessionProvider(SessionProvider):
    """Provider for Cursor IDE agent session logs."""

    @property
    def agent_name(self) -> str:
        return "cursor"

    def find_latest_session(self, log_dirs: list[str] | None = None) -> str | None:
        """Find the most recently modified Cursor session file.

        Scans workspaceStorage subdirectories recursively for session log files.
        Cursor stores sessions as JSON, log, or txt files in workspaceStorage
        subdirectories.

        Args:
            log_dirs: Optional custom directories to search. If None, uses
                the built-in platform-specific defaults.

        Returns:
            The file path of the most recent session, or None if none found.
        """
        system = platform.system().lower()
        paths = log_dirs if log_dirs is not None else DEFAULT_CURSOR_PATHS.get(
            system, DEFAULT_CURSOR_PATHS["linux"]
        )

        latest_file: str | None = None
        latest_mtime: float = 0.0

        for pattern in paths:
            expanded = expand_path(pattern)
            base = Path(expanded)
            if not base.is_dir():
                continue
            for f in base.rglob("*"):
                if f.is_file() and f.suffix in (".json", ".log", ".txt"):
                    try:
                        mtime = f.stat().st_mtime
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest_file = str(f)
                    except OSError:
                        continue

        return latest_file

    def read_session_log(self, path: str) -> str:
        """Read a Cursor session log file.

        Args:
            path: Path to the session log file.

        Returns:
            The file contents as a UTF-8 string.
        """
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def find_session_diffs(self, session_path: str) -> str | None:
        """Cursor does not store session diffs separately.

        Returns None.
        """
        return None
