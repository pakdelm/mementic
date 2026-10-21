"""Static checks for the sbx mixin kit (no Docker required)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / "docker" / "sbx-mementic"


def test_kit_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    descriptor = (KIT / "mementic.yaml").read_text(encoding="utf-8")

    match = re.search(r'default:\s*"([^"]+)"', descriptor)

    assert match is not None, "descriptor has no args.version default"
    assert match.group(1) == pyproject["project"]["version"]


def test_descriptor_is_a_v3_mixin() -> None:
    descriptor = (KIT / "mementic.yaml").read_text(encoding="utf-8")

    assert 'schemaVersion: "3"' in descriptor
    assert "kind: mixin" in descriptor
    assert "buildArg: MEMENTIC_VERSION" in descriptor


def test_dockerfile_installs_tagged_release_and_entry_point() -> None:
    dockerfile = (KIT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG MEMENTIC_VERSION" in dockerfile
    assert "/archive/refs/tags/v${MEMENTIC_VERSION}.tar.gz" in dockerfile
    assert "/opt/mementic/entry.py" in dockerfile
    assert "FROM scratch" in dockerfile


def test_wrapper_checks_python_and_invokes_entry_point() -> None:
    wrapper = (KIT / "mementic").read_text(encoding="utf-8")

    assert "command -v python3" in wrapper
    assert "/opt/mementic/entry.py" in wrapper
