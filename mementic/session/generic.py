"""Generic session provider.

Reads any text file as a session log. No auto-discovery — paths must be
provided explicitly via ``--session`` or configured in ``.mementic/config.json``.
"""

from __future__ import annotations

from pathlib import Path

from mementic.session import SessionProvider, expand_path


class GenericSessionProvider(SessionProvider):
    """Provider that reads any text file as a session log.

    Has no built-in defaults. Session discovery requires either an explicit
    ``--session`` flag or ``sessions.generic.log_dirs`` in ``.mementic/config.json``.
    """

    @property
    def agent_name(self) -> str:
        return "generic"

    def find_latest_session(self, log_dirs: list[str] | None = None) -> str | None:
        """Find the most recently modified file in configured directories.

        Args:
            log_dirs: Directories to search. If None or empty, returns None
                (generic has no built-in defaults).

        Returns:
            The file path of the most recent file, or None if no directories
            are configured or no files found.
        """
        if not log_dirs:
            return None

        latest_file: str | None = None
        latest_mtime: float = 0.0

        for pattern in log_dirs:
            expanded = expand_path(pattern)
            expanded_path = Path(expanded)
            if expanded_path.is_dir():
                for f in expanded_path.iterdir():
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
        """Read a generic session log file.

        Args:
            path: Path to the session log file.

        Returns:
            The file contents as a UTF-8 string.
        """
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def find_session_diffs(self, session_path: str) -> str | None:
        """Generic provider does not store session diffs.

        Returns None.
        """
        return None
