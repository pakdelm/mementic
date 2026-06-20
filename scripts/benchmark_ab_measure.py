"""Parse an OpenCode session log and extract benchmark metrics.

Usage:
    python scripts/benchmark_ab_measure.py <path-to-session.log>
    python scripts/benchmark_ab_measure.py <path-to-session.log> --json

Extracts:
    - files_read: list of file paths from permission=read lines
    - unique_files_read: deduplicated count
    - session_log_tokens: token count of the full log (via count_tokens)
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
    r"service=permission\s+permission=(\w+)\s+pattern=(.+?)\s+action="
)


def _parse_permission_lines(log_text: str) -> list[tuple[str, str]]:
    """Parse permission lines, returning list of (tool_type, pattern)."""
    results: list[tuple[str, str]] = []
    clean = _strip_ansi(log_text)
    for match in _PERMISSION_RE.finditer(clean):
        tool = match.group(1)
        pattern = match.group(2).strip()
        results.append((tool, pattern))
    return results


# -- Main measurement ---------------------------------------------------------


def measure_session(log_path: str | Path) -> dict:
    """Parse a session log and return structured metrics."""
    path = Path(log_path)
    log_text = path.read_text(encoding="utf-8", errors="replace")
    log_size = len(log_text.encode("utf-8"))

    permissions = _parse_permission_lines(log_text)

    files_read = [p for tool, p in permissions if tool == "read"]
    unique_files = sorted(set(files_read))

    tool_counts: dict[str, int] = {}
    for tool, _pattern in permissions:
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    try:
        from mementic.format import count_tokens
        tokens = count_tokens(_strip_ansi(log_text))
    except ImportError:
        tokens = len(log_text) // 4

    return {
        "log_path": str(path),
        "log_size_bytes": log_size,
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
        print("Usage: python scripts/benchmark_ab_measure.py <session.log> [--json]")
        sys.exit(1)

    log_path = sys.argv[1]
    use_json = "--json" in sys.argv

    if not Path(log_path).exists():
        print(f"Error: file not found: {log_path}")
        sys.exit(1)

    result = measure_session(log_path)

    if use_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Session: {result['log_path']}")
        print(f"  Log size: {result['log_size_bytes']:,} bytes")
        print(f"  Session log tokens: {result['session_log_tokens']:,}")
        print(f"  Files read: {result['total_file_reads']} ({result['unique_files_read_count']} unique)")
        print(f"  Total tool calls: {result['total_tool_calls']}")
        print("  Tool breakdown:")
        for tool, count in sorted(result["tool_calls"].items(), key=lambda x: -x[1]):
            print(f"    {tool}: {count}")


if __name__ == "__main__":
    main()
