"""Docker contract test for the CWD-local graph and the installed CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE = "python:3.13-slim"


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _build_wheel(tmp_path: Path) -> Path:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    _run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir)], cwd=REPO_ROOT)
    wheels = list(wheel_dir.glob("mementic-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, found {wheels}"
    return wheels[0]


def _make_workspace(tmp_path: Path, layout: str) -> tuple[Path, Path, str, str]:
    workspace = tmp_path / layout
    if layout == "single":
        entity_rel = "src/auth.py"
        child_rel = "src"
    else:
        entity_rel = "repo-a/src/auth.py"
        child_rel = "repo-a"
    entity = workspace / entity_rel
    entity.parent.mkdir(parents=True)
    entity.write_text("x = 1\n", encoding="utf-8")
    if layout != "single":
        other = workspace / "repo-b" / "src" / "api.py"
        other.parent.mkdir(parents=True)
        other.write_text("x = 2\n", encoding="utf-8")
    return workspace, workspace / child_rel, entity_rel, child_rel


@pytest.mark.parametrize("layout", ["single", "multi"])
def test_cwd_local_graph_in_container(tmp_path: Path, layout: str) -> None:
    if not _docker_available():
        pytest.fail("Docker is required for -m docker tests")
    wheel = _build_wheel(tmp_path)
    workspace, _child, entity_rel, child_rel = _make_workspace(tmp_path, layout)

    script = f"""
set -eu
pip install --no-cache-dir /wheels/*.whl
mementic init
test -f AGENTS.md
test -f .mementic/graph.db
mementic add decision "Use JWT Auth" -r "Token-based authentication" -e {entity_rel}
mementic query file {entity_rel} --json > /tmp/result.json
grep -q 'use-jwt-auth' /tmp/result.json
cd {child_rel}
if mementic query stats > /tmp/child.out 2>&1; then
  echo "child query unexpectedly succeeded" >&2
  exit 1
fi
grep -qi "no mementic graph" /tmp/child.out
test ! -e .mementic
"""

    _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{workspace}:/workspace",
            "-v",
            f"{wheel.parent}:/wheels",
            "-w",
            "/workspace",
            IMAGE,
            "sh",
            "-lc",
            script,
        ],
        cwd=tmp_path,
    )

    assert (workspace / ".mementic" / "graph.db").exists()
    assert (workspace / "AGENTS.md").exists()
