"""Build the sbx mixin kit from the committed kit directory.

The committed kit directory holds the descriptor, the recipe, and the wrapper,
and the recipe installs mementic from the pinned public tag archive, so the
directory is a complete build context on its own.

Usage:
    python scripts/build_kit.py --output cacheonly
    python scripts/build_kit.py --output cacheonly --version 0.5.0
    python scripts/build_kit.py --output push --tag docker.io/pakdelm/sbx-kit-mementic:0.5.0
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = ROOT / "docker" / "sbx-mementic"
DESCRIPTOR = KIT_DIR / "mementic.yaml"


def build_kit(*, output: str, tag: str | None, platform: str | None, version: str | None) -> int:
    """Build the committed kit directory and return the ``docker buildx`` exit code.

    Args:
        output: ``cacheonly``, ``load``, or ``push``.
        tag: Image reference; required for ``load`` and ``push``.
        platform: Optional comma-separated platform list.
        version: Optional kit ``version`` argument override.

    Returns:
        The ``docker buildx build`` exit code.
    """
    command = ["docker", "buildx", "build", str(KIT_DIR), "-f", str(DESCRIPTOR)]
    if version:
        command += ["--build-arg", f"version={version}"]
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
    """Parse arguments and build the kit image."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        choices=("cacheonly", "load", "push"),
        default="cacheonly",
        help="Build output (default: cacheonly)",
    )
    parser.add_argument("--tag", help="Image reference for load or push")
    parser.add_argument("--platform", help="Comma-separated platform list")
    parser.add_argument(
        "--version",
        help="Kit version override for args.version (default: descriptor pin)",
    )

    args = parser.parse_args()
    if args.output in ("load", "push") and not args.tag:
        parser.error("--tag is required with --output load or --output push")
    raise SystemExit(
        build_kit(
            output=args.output,
            tag=args.tag,
            platform=args.platform,
            version=args.version,
        )
    )


if __name__ == "__main__":
    main()
