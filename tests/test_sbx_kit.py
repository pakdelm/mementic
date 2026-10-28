"""Static checks for the sbx mixin kit; they require no Docker."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / "docker" / "sbx-mementic"
KIT_SCRIPT = REPO_ROOT / "scripts" / "kit.py"


def _descriptor_path() -> Path:
    """Return the kit's single YAML descriptor."""
    descriptors = sorted(KIT.glob("*.yaml"))

    assert len(descriptors) == 1, f"expected exactly one kit descriptor, found {descriptors}"
    return descriptors[0]


def _companion_recipe() -> Path:
    """Return the recipe the Kit frontend resolves from the descriptor stem.

    v3 pairs ``<stem>.yaml`` with ``<stem>.dockerfile``. A missing companion is
    not an error: the kit builds as declaration-only and installs nothing.
    """
    return _descriptor_path().with_suffix(".dockerfile")


def _load_kit_module() -> ModuleType:
    """Import ``scripts/kit.py`` without putting it on ``sys.path``."""
    spec = importlib.util.spec_from_file_location("kit_script", KIT_SCRIPT)

    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kit_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    descriptor = _descriptor_path().read_text(encoding="utf-8")

    match = re.search(r'^\s{4}default:\s*"([^"]+)"', descriptor, re.MULTILINE)

    assert match is not None, "descriptor has no args.version default"
    assert match.group(1) == pyproject["project"]["version"]


def test_descriptor_is_a_v3_mixin() -> None:
    descriptor = _descriptor_path().read_text(encoding="utf-8")

    assert 'schemaVersion: "3"' in descriptor
    assert "kind: mixin" in descriptor
    assert "buildArg: MEMENTIC_VERSION" in descriptor


def test_descriptor_provides_the_pinned_version() -> None:
    """``provides`` makes the kit requirable and records the installed version."""
    descriptor = _descriptor_path().read_text(encoding="utf-8")

    assert "mementic@${{ kit.args.version }}" in descriptor


def test_descriptor_has_a_stem_matched_companion_recipe() -> None:
    """A recipe the frontend cannot resolve builds a content-free kit."""
    companion = _companion_recipe()

    assert companion.is_file(), (
        f"{_descriptor_path().name} needs {companion.name} beside it; "
        "an unresolved companion silently produces a declaration-only kit"
    )


def test_recipe_installs_from_the_pinned_tag_archive() -> None:
    """The committed kit downloads the protected tag; nothing is staged locally."""
    recipe = _companion_recipe().read_text(encoding="utf-8")

    assert "ARG MEMENTIC_VERSION" in recipe
    assert (
        "https://github.com/pakdelm/mementic/archive/refs/tags/v${MEMENTIC_VERSION}.tar.gz"
    ) in recipe
    assert "pip install --no-cache-dir --target /opt/mementic" in recipe
    assert 'test -d "/opt/mementic/mementic-${MEMENTIC_VERSION}.dist-info"' in recipe
    assert "/opt/mementic/entry.py" in recipe
    assert "FROM scratch" in recipe
    assert "COPY pyproject.toml" not in recipe
    assert "COPY README.md" not in recipe
    assert "COPY LICENSE" not in recipe
    assert "COPY src/mementic" not in recipe
    assert "pip install --no-cache-dir --target /opt/mementic /src" not in recipe


def test_build_kit_passes_kit_version_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release PR builds a released tag before the new tag exists."""
    module = _load_kit_module()
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.build_kit(output="cacheonly", tag=None, platform=None, version="0.5.0")

    assert "--build-arg" in commands[0]
    assert "version=0.5.0" in commands[0]


def test_wrapper_is_lf_pinned() -> None:
    """The wrapper runs inside a Linux sandbox.

    A CRLF checkout would store the shebang as ``#!/bin/sh\\r``, so the kernel
    would look for an interpreter path that does not exist.
    """
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "docker/sbx-mementic/mementic text eol=lf" in attributes


def test_wrapper_checks_python_and_invokes_entry_point() -> None:
    wrapper = (KIT / "mementic").read_text(encoding="utf-8")

    assert "command -v python3" in wrapper
    assert "/opt/mementic/entry.py" in wrapper
