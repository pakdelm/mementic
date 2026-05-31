"""OpenCode session provider.

Discovers session logs from OpenCode's platform-specific storage directories.
"""

from __future__ import annotations

import platform
from pathlib import Path

from mementic.session import SessionProvider, expand_path

DEFAULT_SESSION_PATHS: dict[str, list[str]] = {
    "linux": [
        "~/.local/share/opencode/log/",
        "~/.local/share/opencode/storage/session_diff/",
    ],
    "darwin": [
        "~/.local/share/opencode/log/",
        "~/.local/share/opencode/storage/session_diff/",
    ],
    "win32": [
        "%LOCALAPPDATA%/opencode/log/",
        "%USERPROFILE%/.local/share/opencode/log/",
    ],
}


class OpenCodeSessionProvider(SessionProvider):
    """Provider for OpenCode agent session logs."""

    @property
    def agent_name(self) -> str:
        return "opencode"

    def find_latest_session(self, log_dirs: list[str] | None = None) -> str | None:
        """Find the most recently modified OpenCode session file.

        Args:
            log_dirs: Optional custom directories to search. If None, uses
                the built-in platform-specific defaults.

        Returns:
            The file path of the most recent session, or None if none found.
        """
        system = platform.system().lower()
        paths = log_dirs if log_dirs is not None else DEFAULT_SESSION_PATHS.get(
            system, DEFAULT_SESSION_PATHS["linux"]
        )

        latest_file: str | None = None
        latest_mtime: float = 0.0

        for pattern in paths:
            expanded = expand_path(pattern)
            expanded_path = Path(expanded)
            if expanded_path.is_dir():
                for f in expanded_path.iterdir():
                    if f.is_file():
                        try:
                            st = f.stat()
                            if st.st_mtime > latest_mtime:
                                latest_mtime = st.st_mtime
                                latest_file = str(f)
                        except OSError:
                            continue

        return latest_file

    def read_session_log(self, path: str) -> str:
        """Read an OpenCode session log file.

        Args:
            path: Path to the session log file.

        Returns:
            The file contents as a UTF-8 string.
        """
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def find_session_diffs(self, session_path: str) -> str | None:
        """OpenCode diffs are handled via git in the capture command.

        Returns None — session diff discovery is delegated to git diff,
        not OpenCode's internal diff storage.
        """
        return None
