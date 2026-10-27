"""Build the wheel and sdist and verify their contents.

The public launch must not ship private material. This script fails when a
forbidden path appears in the sdist, when the wheel contains anything outside
the package and its metadata, or when the built wheel does not install and run
in an isolated environment.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

FORBIDDEN_SDIST_PARTS: tuple[str, ...] = (
    ".opencode",
    ".mementic",
    ".venv",
    "node_modules",
    "nico.md",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".env",
)


def _fail(message: str) -> int:
    """Print a failure and return a non-zero exit code."""
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def _sdist_paths() -> list[str]:
    """Return every member path in the single built sdist."""
    sdists = sorted(DIST.glob("*.tar.gz"))
    if len(sdists) != 1:
        raise RuntimeError(f"expected one sdist, found {[p.name for p in sdists]}")
    with tarfile.open(sdists[0]) as archive:
        return archive.getnames()


def _wheel_paths() -> list[str]:
    """Return every member path in the single built wheel."""
    wheels = sorted(DIST.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {[p.name for p in wheels]}")
    with zipfile.ZipFile(wheels[0]) as archive:
        return archive.namelist()


def _check_sdist(paths: list[str]) -> int:
    """Reject private or generated material in the sdist."""
    bad = [path for path in paths if any(part in path.split("/") for part in FORBIDDEN_SDIST_PARTS)]
    if bad:
        return _fail(f"forbidden sdist entries: {bad[:10]}")
    return 0


def _check_wheel(paths: list[str]) -> int:
    """Allow only the package tree and its dist-info directory."""
    bad = [
        path for path in paths if not (path.startswith("mementic/") or path.startswith("mementic-"))
    ]
    if bad:
        return _fail(f"unexpected wheel entries: {bad[:10]}")
    return 0


def _check_install() -> int:
    """Install the built wheel in an isolated environment and run it."""
    wheels = sorted(DIST.glob("*.whl"))
    try:
        subprocess.run(
            ["uv", "tool", "run", "--from", str(wheels[0]), "mementic", "--version"],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return _fail(f"wheel install/run failed: {exc}")
    return 0


def main() -> int:
    """Build the artifacts and run every check."""
    subprocess.run(["uv", "build"], cwd=ROOT, check=True)
    for check in (
        _check_sdist(_sdist_paths()),
        _check_wheel(_wheel_paths()),
        _check_install(),
    ):
        if check != 0:
            return check
    print("Artifacts OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
