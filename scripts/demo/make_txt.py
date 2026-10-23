"""Capture the current CLI output for the README demo transcript.

Writes ``terminal.txt`` beside this script: one block per command, box panels
width-padded, the working directory shown as ``~/dev/shop``. Regenerate whenever
CLI output changes, then run ``make_svg.py`` to rebuild the SVG.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / ".venv" / "Scripts" / "mementic.exe"
POSIX_CLI = ROOT / ".venv" / "bin" / "mementic"
ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
OUT = Path(__file__).resolve().parent / "terminal.txt"
COLUMNS = "104"

STEPS: list[list[str]] = [
    ["init"],
    [
        "add",
        "decision",
        "Use JWT Auth",
        "--rationale",
        "Tokens carry the session",
        "--entity",
        "src/auth.py",
    ],
    ["add", "constraint", "Handlers must stay stateless", "--entity", "src/auth.py"],
    ["query", "keywords", "jwt", "handlers"],
    ["deprecate", "use-jwt-auth", "--reason", "Replaced by short lived tokens"],
]

_QUOTED_OPTIONS = {"--rationale", "--entity", "--reason"}


def display(argv: list[str]) -> str:
    """Quote option values that contain spaces, as a user would type them."""
    parts: list[str] = []
    quote_next = False
    for arg in argv:
        if quote_next or " " in arg:
            parts.append(f'"{arg}"')
            quote_next = False
        elif arg in _QUOTED_OPTIONS:
            parts.append(arg)
            quote_next = True
        else:
            parts.append(arg)
    return " ".join(parts)


def cli_path() -> Path:
    """Return the installed console script for this platform."""
    return CLI if CLI.exists() else POSIX_CLI


def normalize(text: str, base: Path) -> list[str]:
    """Strip ANSI, mask the working directory, and pad box panels.

    Shorter ``│`` rows are padded to the panel's opening width so the right
    border lines up in the SVG: a panel re-wraps its rows at the capture
    width, and the closing ``┐`` row sets the true panel width.
    """
    text = ANSI.sub("", text).replace(str(base), "~/dev/shop").replace("\\", "/")
    lines: list[str] = []
    width = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("┌"):
            width = len(line)
        elif line.startswith("│") and line.endswith("│") and len(line) < width:
            line = line[:-1] + " " * (width - len(line)) + "│"
        lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def main() -> None:
    """Run every step in a throwaway project and write the transcript."""
    if not cli_path().exists():
        raise SystemExit(f"mementic console script not found at {cli_path()}")
    base = Path(tempfile.mkdtemp(prefix="mementic-demo-"))
    try:
        (base / "src").mkdir()
        (base / "src" / "auth.py").write_text("x = 1\n", encoding="utf-8")
        env = dict(os.environ, COLUMNS=COLUMNS, PYTHONIOENCODING="utf-8")

        blocks: list[str] = []
        for argv in STEPS:
            proc = subprocess.run([str(cli_path()), *argv], cwd=base, env=env, capture_output=True)
            text = proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode(
                "utf-8", errors="replace"
            )
            blocks.append("$ mementic " + display(argv))
            blocks.extend(normalize(text, base))
            blocks.append("")

        while blocks and blocks[-1] == "":
            blocks.pop()
        blocks.append("")

        OUT.write_text("\n".join(blocks) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {OUT} ({len(blocks)} lines)")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
