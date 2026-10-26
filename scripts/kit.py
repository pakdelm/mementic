"""Stage and build the sbx mixin kit from the working tree.

The committed kit directory holds the descriptor, the recipe, and the wrapper.
The recipe installs mementic from its build context, so the context must also
carry ``pyproject.toml`` and the package source. ``stage`` assembles that
context; ``build`` hands it to ``docker buildx``.

Usage:
    python scripts/kit.py stage .sbx-kit
    python scripts/kit.py build --output cacheonly
    python scripts/kit.py build --output push --tag docker.io/pakdelm/sbx-kit-mementic:0.4.1
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = ROOT / "docker" / "sbx-mementic"
DESCRIPTOR = KIT_DIR / "mementic.yaml"
RECIPE = KIT_DIR / "mementic.dockerfile"
WRAPPER = KIT_DIR / "mementic"
PACKAGE = ROOT / "mementic"
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
LICENSE = ROOT / "LICENSE"


def _ignore_build_context(directory: str, names: list[str]) -> set[str]:
    """Skip caches and virtualenvs so the build context stays source-only."""
    return {name for name in names if name.startswith(".") or name == "__pycache__"}


def _copy_script(source: Path, dest: Path) -> None:
    """Copy an executable script with LF endings.

    A CRLF shebang makes the sandbox kernel look for an interpreter named
    ``/bin/sh\\r``, so a wrapper staged from a Windows checkout with
    ``core.autocrlf`` would not run.
    """
    dest.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))


def stage(dest: Path) -> Path:
    """Copy the kit and the source its recipe installs into ``dest``.

    Args:
        dest: Directory to create as the kit build context.

    Returns:
        ``dest``, for chaining.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for source in (DESCRIPTOR, RECIPE):
        shutil.copy2(source, dest / source.name)
    _copy_script(WRAPPER, dest / WRAPPER.name)
    shutil.copy2(PYPROJECT, dest / "pyproject.toml")
    shutil.copy2(README, dest / "README.md")
    shutil.copy2(LICENSE, dest / "LICENSE")
    package_dest = dest / "src" / "mementic"
    if package_dest.exists():
        shutil.rmtree(package_dest)
    shutil.copytree(PACKAGE, package_dest, ignore=_ignore_build_context)
    return dest


def build_kit(*, context: Path, output: str, tag: str | None, platform: str | None) -> int:
    """Build the staged kit and return the ``docker buildx`` exit code.

    Args:
        context: Staged kit build context.
        output: ``cacheonly``, ``load``, or ``push``.
        tag: Image reference; required for ``load`` and ``push``.
        platform: Optional comma-separated platform list.

    Returns:
        The ``docker buildx build`` exit code.
    """
    command = [
        "docker",
        "buildx",
        "build",
        str(context),
        "-f",
        str(context / DESCRIPTOR.name),
    ]
    if output == "cacheonly":
        command += ["--output", "type=cacheonly"]
    elif output == "load":
        command += ["--tag", tag or "", "--load"]
    else:
        command += ["--tag", tag or "", "--push"]
    if platform:
        command += ["--platform", platform]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main() -> None:
    """Parse arguments and run the requested subcommand."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage_parser = subparsers.add_parser("stage", help="Stage a kit build context")
    stage_parser.add_argument("dest", type=Path, help="Directory to create")

    build_parser = subparsers.add_parser("build", help="Stage and build the kit image")
    build_parser.add_argument(
        "--output",
        choices=("cacheonly", "load", "push"),
        default="cacheonly",
        help="Build output (default: cacheonly)",
    )
    build_parser.add_argument("--tag", help="Image reference for load or push")
    build_parser.add_argument("--platform", help="Comma-separated platform list")

    args = parser.parse_args()
    if args.command == "stage":
        print(stage(args.dest))
        return

    if args.output in ("load", "push") and not args.tag:
        parser.error("--tag is required with --output load or --output push")
    with tempfile.TemporaryDirectory(prefix="mementic-kit-") as temp:
        context = stage(Path(temp))
        raise SystemExit(
            build_kit(context=context, output=args.output, tag=args.tag, platform=args.platform)
        )


if __name__ == "__main__":
    main()
