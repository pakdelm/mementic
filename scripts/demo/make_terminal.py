"""Record the README terminal demo with VHS.

``terminal.tape`` types the demo commands into a real terminal and records the
resulting GIF. This script prepares the project those commands run in, puts the
repository's virtualenv on PATH, and runs VHS from inside that project:

    uv run python scripts/demo/make_terminal.py

The demo commands and the project layout follow the README quick start, so the
recording shows the same flow a reader gets when they try mementic themselves.
Requires ``vhs`` (with ``ttyd`` and ``ffmpeg``) on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TAPE = HERE / "terminal.tape"
OUTPUT = HERE / "terminal.gif"
VENV_BIN = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
DEFAULT_SANDBOX = Path.home() / "dev" / "shop"


def sandbox_dir() -> Path:
    """Return the throwaway project directory, honouring ``MEMENTIC_DEMO_DIR``."""
    override = os.environ.get("MEMENTIC_DEMO_DIR")
    return Path(override).expanduser().resolve() if override else DEFAULT_SANDBOX


def find_vhs() -> str:
    """Return the VHS executable, or exit with an install hint."""
    vhs = shutil.which("vhs")
    if vhs is None:
        raise SystemExit(
            "vhs not found on PATH. Install it from https://github.com/charmbracelet/vhs "
            "(it also needs ttyd and ffmpeg)."
        )
    return vhs


def find_cli() -> Path:
    """Return the mementic console script from this repository's virtualenv."""
    name = "mementic.exe" if os.name == "nt" else "mementic"
    cli = VENV_BIN / name
    if not cli.exists():
        raise SystemExit(f"mementic console script not found at {cli}. Run 'uv sync' first.")
    return cli


def prepare_sandbox(sandbox: Path) -> None:
    """Create the empty demo project, refusing to touch a directory that exists."""
    if sandbox.exists():
        raise SystemExit(
            f"{sandbox} already exists. Remove it, or point MEMENTIC_DEMO_DIR at "
            "another directory outside the repository."
        )
    (sandbox / "src").mkdir(parents=True)
    (sandbox / "src" / "auth.py").write_text("x = 1\n", encoding="utf-8")


def record(sandbox: Path, vhs: str, cli: Path) -> None:
    """Run VHS inside the sandbox and move the recording next to this script."""
    env = dict(os.environ)
    env["PATH"] = str(cli.parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run([vhs, str(TAPE)], cwd=sandbox, env=env, check=False)
    if result.returncode != 0:
        raise SystemExit(f"vhs failed with exit code {result.returncode}")
    gif = sandbox / "terminal.gif"
    if not gif.exists():
        raise SystemExit(f"vhs did not write {gif}")
    shutil.move(str(gif), str(OUTPUT))


def main() -> None:
    """Prepare the demo project, record the tape, and report the result."""
    vhs = find_vhs()
    cli = find_cli()
    sandbox = sandbox_dir()
    prepare_sandbox(sandbox)
    try:
        record(sandbox, vhs, cli)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
