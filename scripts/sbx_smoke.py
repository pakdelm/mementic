"""Manual Docker Sandboxes (sbx) smoke test for the mementic mixin.

Run from the repository root:

    uv run python scripts/sbx_smoke.py --layout both

Requires the ``sbx`` CLI and Docker with Buildx. The script creates a temporary
workspace, starts a sandbox with the local mixin, initializes the graph, writes
one decision, and checks that the root graph persists on the host while a child
repository reports no graph. It removes the sandbox and temporary workspace unless
``--keep`` is given.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_WORKLOAD = "docker.io/docker/sbx-kit-shell:1.0.0"
KIT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kit.py"


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return its captured result."""
    print("+", " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed with exit {result.returncode}: {command}")
    return result


def _make_layout(root: Path, layout: str) -> tuple[Path, Path, str, str]:
    """Create one layout and return workspace, child, entity path, child relpath."""
    workspace = root / layout
    if layout == "single":
        entity = workspace / "src" / "auth.py"
        entity.parent.mkdir(parents=True)
        entity.write_text("x = 1\n", encoding="utf-8")
        return workspace, workspace / "src", "src/auth.py", "src"

    repo_a = workspace / "repo-a" / "src" / "auth.py"
    repo_a.parent.mkdir(parents=True)
    repo_a.write_text("x = 1\n", encoding="utf-8")
    repo_b = workspace / "repo-b" / "src" / "api.py"
    repo_b.parent.mkdir(parents=True)
    repo_b.write_text("x = 1\n", encoding="utf-8")
    return workspace, workspace / "repo-a", "repo-a/src/auth.py", "repo-a"


def _smoke_one(
    *,
    root: Path,
    layout: str,
    workload: str,
    kit: Path,
    keep: bool,
) -> None:
    """Run one single or multi-repo sandbox smoke check."""
    workspace, child, entity, child_rel = _make_layout(root, layout)
    name = f"mementic-smoke-{layout}"
    try:
        _run(
            [
                "sbx",
                "create",
                "--name",
                name,
                workload,
                "--kit",
                str(kit),
                str(workspace),
            ]
        )
        _run(["sbx", "exec", name, "mementic", "init"])
        _run(
            [
                "sbx",
                "exec",
                name,
                "mementic",
                "add",
                "decision",
                "Use JWT Auth",
                "-r",
                "Token-based authentication",
                "-e",
                entity,
            ]
        )
        _run(
            [
                "sbx",
                "exec",
                name,
                "mementic",
                "query",
                "file",
                entity,
                "--json",
            ]
        )
        host_graph = workspace / ".mementic" / "graph.db"
        if not host_graph.exists():
            raise RuntimeError(f"host graph missing after sandbox write: {host_graph}")

        child_result = _run(
            [
                "sbx",
                "exec",
                "--workdir",
                str(child),
                name,
                "mementic",
                "query",
                "stats",
            ],
            check=False,
        )
        if child_result.returncode == 0:
            raise RuntimeError(f"child repo {child_rel} unexpectedly found a graph")
        child_output = f"{child_result.stdout}\n{child_result.stderr}".lower()
        if "no mementic graph" not in child_output:
            raise RuntimeError(f"child repo {child_rel} failed without the no-graph message")
        if (child / ".mementic").exists():
            raise RuntimeError(f"child repo {child_rel} created .mementic")
        print(f"ok: {layout} layout")
    finally:
        if not keep:
            _run(["sbx", "rm", "-f", name], check=False)


def main() -> None:
    """Parse arguments and run the requested layouts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layout",
        choices=("single", "multi", "both"),
        default="both",
        help="Workspace layout to exercise (default: both).",
    )
    parser.add_argument(
        "--workload",
        default=DEFAULT_WORKLOAD,
        help=f"Workload image reference (default: {DEFAULT_WORKLOAD}).",
    )
    parser.add_argument(
        "--kit",
        type=Path,
        default=None,
        help="Pre-staged mixin directory (default: stage the checkout).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the sandbox and temporary workspace for inspection.",
    )
    args = parser.parse_args()

    if shutil.which("sbx") is None:
        raise SystemExit("sbx is required for this smoke test")

    layouts = ("single", "multi") if args.layout == "both" else (args.layout,)
    root = Path(tempfile.mkdtemp(prefix="mementic-sbx-"))
    try:
        kit = args.kit
        if kit is None:
            kit = root / "kit"
            _run([sys.executable, str(KIT_SCRIPT), "stage", str(kit)])
        elif not kit.exists():
            raise SystemExit(f"mixin directory not found: {kit}")
        for layout in layouts:
            _smoke_one(
                root=root,
                layout=layout,
                workload=args.workload,
                kit=kit.resolve(),
                keep=args.keep,
            )
        if args.keep:
            print(f"workspace kept at {root}")
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
