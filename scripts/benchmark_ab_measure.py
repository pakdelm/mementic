"""Parse an OpenCode session log and extract benchmark metrics.

Usage:
    python scripts/benchmark_ab_measure.py <path-to-session.log>
    python scripts/benchmark_ab_measure.py <path-to-session.log> --json
    python scripts/benchmark_ab_measure.py <path-to-session.log> --from-line N

Extracts:
    - files_read: list of file paths from permission=read lines
    - unique_files_read: deduplicated count
    - session_log_tokens: token count of the measured scope (via count_tokens)
    - tool_calls: dict of tool-type -> count
    - log_path: the file that was measured
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# -- ANSI stripping -----------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


# -- Permission line parsing --------------------------------------------------

_PERMISSION_RE = re.compile(
    r"message=evaluated\s+permission=(\w+)\s+pattern=(\"\"|\".*?\"|\S+)\s+action\."
)


def _unquote(pattern: str) -> str:
    """Strip surrounding double-quotes from a pattern value."""
    if len(pattern) >= 2 and pattern.startswith('"') and pattern.endswith('"'):
        return pattern[1:-1]
    return pattern


def _parse_permission_lines(log_text: str) -> list[tuple[str, str]]:
    """Parse permission lines, returning list of (tool_type, pattern)."""
    results: list[tuple[str, str]] = []
    clean = _strip_ansi(log_text)
    for match in _PERMISSION_RE.finditer(clean):
        tool = match.group(1)
        pattern = _unquote(match.group(2))
        results.append((tool, pattern))
    return results


# -- Session scoping ----------------------------------------------------------

_START_RE = re.compile(r"message=created\s+.*?\bparentID=undefined\b")


def _find_last_session_start(log_text: str) -> int:
    """Find the character offset where the last top-level session starts.

    Returns 0 if no session start is found.
    """
    clean = _strip_ansi(log_text)
    last_start = 0
    for match in _START_RE.finditer(clean):
        last_start = match.start()
    return last_start


# -- Main measurement ---------------------------------------------------------


def measure_session(log_path: str | Path, from_line: int = 0) -> dict:
    """Parse a session log and return structured metrics.

    Args:
        log_path: Path to the OpenCode session log file.
        from_line: 1-indexed line number to start measurement from.
                    Use 0 (default) to auto-detect the last top-level session.
    """
    path = Path(log_path)
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    log_size = len(raw_text.encode("utf-8"))

    if from_line <= 0:
        scope_start = _find_last_session_start(_strip_ansi(raw_text))
        scoped_text = raw_text[scope_start:]
    else:
        lines = raw_text.splitlines(keepends=True)
        if from_line > len(lines):
            scoped_text = raw_text
        else:
            scoped_text = "".join(lines[from_line - 1:])

    clean = _strip_ansi(scoped_text)
    permissions = _parse_permission_lines(scoped_text)

    files_read = [p for tool, p in permissions if tool == "read"]
    unique_files = sorted(set(files_read))

    tool_counts: dict[str, int] = {}
    for tool, _pattern in permissions:
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    try:
        from mementic.format import count_tokens
        tokens = count_tokens(clean)
    except ImportError:
        tokens = len(clean) // 4

    return {
        "log_path": str(path),
        "log_size_bytes": log_size,
        "scoped": from_line > 0,
        "from_line": from_line if from_line > 0 else None,
        "files_read": files_read,
        "unique_files_read_count": len(unique_files),
        "unique_files_read": unique_files,
        "total_file_reads": len(files_read),
        "session_log_tokens": tokens,
        "tool_calls": tool_counts,
        "total_tool_calls": len(permissions),
    }


# -- CLI ----------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/benchmark_ab_measure.py <session.log> [--json] [--from-line N]")
        sys.exit(1)

    log_path = sys.argv[1]
    use_json = "--json" in sys.argv
    from_line = 0

    if "--from-line" in sys.argv:
        idx = sys.argv.index("--from-line")
        if idx + 1 < len(sys.argv):
            from_line = int(sys.argv[idx + 1])

    if not Path(log_path).exists():
        print(f"Error: file not found: {log_path}")
        sys.exit(1)

    result = measure_session(log_path, from_line=from_line)

    if use_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Session: {result['log_path']}")
        print(f"  Log size: {result['log_size_bytes']:,} bytes")
        if result.get("scoped"):
            print(f"  Scope: from line {result['from_line']}")
        print(f"  Session log tokens: {result['session_log_tokens']:,}")
        print(f"  Files read: {result['total_file_reads']} ({result['unique_files_read_count']} unique)")
        print(f"  Total tool calls: {result['total_tool_calls']}")
        print("  Tool breakdown:")
        for tool, count in sorted(result["tool_calls"].items(), key=lambda x: -x[1]):
            print(f"    {tool}: {count}")


if __name__ == "__main__":
    main()
