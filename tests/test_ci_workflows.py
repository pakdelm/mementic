"""Static checks for the CI wiring of the sbx kit workflows."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RESOLVE = WORKFLOWS / "resolve-kit-version.yml"
BUILD = WORKFLOWS / "build-kit.yml"


def test_ci_calls_the_kit_workflows() -> None:
    """Version resolution and the kit build are reusable workflows, not steps."""
    ci = CI.read_text(encoding="utf-8")

    assert "uses: ./.github/workflows/resolve-kit-version.yml" in ci
    assert "uses: ./.github/workflows/build-kit.yml" in ci


def test_kit_workflows_are_reusable_and_linked() -> None:
    """The resolver publishes the version and the builder consumes it."""
    resolve = RESOLVE.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8")

    assert "workflow_call:" in resolve
    assert "value: ${{ jobs.resolve.outputs.version }}" in resolve
    assert "workflow_call:" in build
    assert "required: true" in build
    assert '--version "${{ inputs.version }}"' in build
