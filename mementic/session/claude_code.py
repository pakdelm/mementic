"""Claude Code session provider.

Discovers session logs from Claude Code's project-based JSONL storage.
"""

from __future__ import annotations

import platform
from pathlib import Path

from mementic.session import SessionProvider, expand_path

DEFAULT_CLAUDECODE_PATHS: dict[str, list[str]] = {
    "linux": ["~/.claude/projects/"],
    "darwin": ["~/.claude/projects/"],
    "win32": ["%USERPROFILE%/.claude/projects/"],
}


class ClaudeCodeSessionProvider(SessionProvider):
    """Provider for Claude Code agent session logs (.jsonl files)."""

    @property
    def agent_name(self) -> str:
        return "claude-code"

    def find_latest_session(self, log_dirs: list[str] | None = None) -> str | None:
        """Find the most recently modified Claude Code session file.

        Recursively scans project directories for ``*.jsonl`` files.

        Args:
            log_dirs: Optional custom directories to search. If None, uses
                the built-in platform-specific defaults.

        Returns:
            The file path of the most recent session, or None if none found.
        """
        system = platform.system().lower()
        paths = log_dirs if log_dirs is not None else DEFAULT_CLAUDECODE_PATHS.get(
            system, DEFAULT_CLAUDECODE_PATHS["linux"]
        )

        latest_file: str | None = None
        latest_mtime: float = 0.0

        for pattern in paths:
            expanded = expand_path(pattern)
            base = Path(expanded)
            if not base.is_dir():
                continue
            for f in base.rglob("*.jsonl"):
                if f.is_file():
                    try:
                        mtime = f.stat().st_mtime
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest_file = str(f)
                    except OSError:
                        continue

        return latest_file

    def read_session_log(self, path: str) -> str:
        """Read a Claude Code session log file.

        Args:
            path: Path to the session log file.

        Returns:
            The file contents as a UTF-8 string.
        """
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def find_session_diffs(self, session_path: str) -> str | None:
        """Claude Code does not store session diffs separately.

        Returns None.
        """
        return None
