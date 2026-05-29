"""Session log discovery and reader for OpenCode session files."""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

DEFAULT_SESSION_PATHS: dict[str, list[str]] = {
    "linux": ["~/.local/share/opencode/log/", "~/.local/share/opencode/storage/session_diff/"],
    "darwin": ["~/.local/share/opencode/log/", "~/.local/share/opencode/storage/session_diff/"],
    "win32": ["%LOCALAPPDATA%/opencode/log/", "%USERPROFILE%/.local/share/opencode/log/"],
}


def expand_path(path: str) -> str:
    """Expand ~ and %ENV_VAR% in a path string.

    Args:
        path: A path string that may contain ~ or %VAR% patterns.

    Returns:
        The expanded path string with home directory and env vars resolved.
    """
    if path.startswith("~"):
        path = str(Path.home() / path[2:])
    if "%" in path:
        while match := re.search(r"%(\w+)%", path):
            var_name = match.group(1)
            var_value = os.environ.get(var_name, "")
            path = path[: match.start()] + var_value + path[match.end() :]
    return path


def find_latest_session() -> str | None:
    """Find the most recently modified session file in default paths.

    Returns:
        The file path of the most recent session, or None if no sessions found.
    """
    system = platform.system().lower()
    paths = DEFAULT_SESSION_PATHS.get(system, DEFAULT_SESSION_PATHS["linux"])

    latest_file: str | None = None
    latest_mtime: float = 0.0

    for pattern in paths:
        expanded = expand_path(pattern)
        expanded_path = Path(expanded)
        if expanded_path.is_dir():
            for f in expanded_path.iterdir():
                if f.is_file() and f.stat().st_mtime > latest_mtime:
                    latest_mtime = f.stat().st_mtime
                    latest_file = str(f)

    return latest_file


def read_session_log(path: str) -> str:
    """Read a session log file and return its content.

    Args:
        path: Path to the session log file.

    Returns:
        The file contents as a UTF-8 string.
    """
    return Path(path).read_text(encoding="utf-8", errors="replace")
