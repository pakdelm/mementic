"""Tests for release preparation, changelog insertion, and tag timing."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "release.py"


def _load_release_module() -> ModuleType:
    """Load scripts/release.py without executing its CLI entry point."""
    spec = importlib.util.spec_from_file_location("release_script", RELEASE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load_release_module()


def _completed(args: tuple[str, ...], stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Build a successful subprocess result for monkeypatched runs."""
    return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


def test_update_changelog_uses_curated_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [0.4.3] - 2026-10-26\n\n- old entry\n",
        encoding="utf-8",
    )
    notes = tmp_path / "notes.md"
    notes.write_text(
        "- Added the publication gate.\n- Fixed the release flow.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "CHANGELOG", changelog)

    release.update_changelog("0.5.0", dry_run=False, notes_file=notes)

    content = changelog.read_text(encoding="utf-8")
    assert "## [0.5.0] - " in content
    assert "- Added the publication gate." in content
    assert "- Fixed the release flow." in content
    assert content.index("## [0.5.0]") < content.index("## [0.4.3]")
    assert "- Fixed the release flow.\n\n## [0.4.3]" in content


def test_update_changelog_keeps_unreleased_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[Unreleased]`` stays above the newest release section."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.4.3] - 2026-10-26\n\n- old entry\n",
        encoding="utf-8",
    )
    notes = tmp_path / "notes.md"
    notes.write_text("- Shipped the kit.\n", encoding="utf-8")
    monkeypatch.setattr(release, "CHANGELOG", changelog)

    release.update_changelog("0.5.0", dry_run=False, notes_file=notes)

    content = changelog.read_text(encoding="utf-8")
    assert content.index("## [Unreleased]") < content.index("## [0.5.0]")
    assert content.index("## [0.5.0]") < content.index("## [0.4.3]")


def test_changelog_section_returns_the_version_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.5.1] - 2026-10-30\n\n"
        "### Changed\n\n- Shipped the kit.\n\n"
        "## [0.5.0] - 2026-10-28\n\n- old entry\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "CHANGELOG", changelog)

    body = release.changelog_section("0.5.1")

    assert "Shipped the kit." in body
    assert "old entry" not in body
    assert "## [0.5.1]" not in body


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/pakdelm/mementic.git",
        "git@github.com:pakdelm/mementic.git",
    ],
)
def test_repository_slug_reads_the_origin_remote(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: _completed((), f"{url}\n"))

    assert release.repository_slug() == "pakdelm/mementic"


def test_upload_asset_skips_an_attached_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "mementic-0.5.1-py3-none-any.whl"
    artifact.write_bytes(b"wheel")
    release_data = {"assets": [{"name": artifact.name}], "upload_url": "https://example"}

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("upload should be skipped")

    monkeypatch.setattr(release, "_api", fail)

    release.upload_asset(release_data, artifact, "token")


def test_github_token_requires_an_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        release.github_token()


def test_tag_release_refuses_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "check_main_branch", lambda **_: None)
    monkeypatch.setattr(release, "check_clean_tree", lambda: None)
    monkeypatch.setattr(release, "get_current_version", lambda: "0.4.3")

    with pytest.raises(SystemExit):
        release.tag_release("0.5.0", dry_run=False)


def test_tag_release_refuses_when_head_is_not_origin_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
        if args[:3] == ("git", "rev-parse", "HEAD"):
            return _completed(args, "local\n")
        if args[:3] == ("git", "rev-parse", "origin/main"):
            return _completed(args, "remote\n")
        return _completed(args)

    monkeypatch.setattr(release, "check_main_branch", lambda **_: None)
    monkeypatch.setattr(release, "check_clean_tree", lambda: None)
    monkeypatch.setattr(release, "get_current_version", lambda: "0.5.0")
    monkeypatch.setattr(release, "run", fake_run)

    with pytest.raises(SystemExit):
        release.tag_release("0.5.0", dry_run=False)


def test_tag_release_creates_and_pushes_annotated_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ("git", "rev-parse", "HEAD"):
            return _completed(args, "same\n")
        if args[:3] == ("git", "rev-parse", "origin/main"):
            return _completed(args, "same\n")
        return _completed(args)

    published: list[tuple[str, str]] = []
    monkeypatch.setattr(release, "check_main_branch", lambda **_: None)
    monkeypatch.setattr(release, "check_clean_tree", lambda: None)
    monkeypatch.setattr(release, "get_current_version", lambda: "0.5.0")
    monkeypatch.setattr(release, "run", fake_run)
    monkeypatch.setattr(release, "github_token", lambda: "token")
    monkeypatch.setattr(
        release, "publish_release", lambda version, token: published.append((version, token))
    )

    release.tag_release("0.5.0", dry_run=False)

    assert ("git", "tag", "-a", "v0.5.0", "-m", "Release v0.5.0") in calls
    assert ("git", "push", "origin", "v0.5.0") in calls
    assert published == [("0.5.0", "token")]


def test_tag_release_checks_the_token_before_tagging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ("git", "rev-parse", "HEAD"):
            return _completed(args, "same\n")
        if args[:3] == ("git", "rev-parse", "origin/main"):
            return _completed(args, "same\n")
        return _completed(args)

    def no_token() -> str:
        release.die("no token")
        raise AssertionError("unreachable")

    monkeypatch.setattr(release, "check_main_branch", lambda **_: None)
    monkeypatch.setattr(release, "check_clean_tree", lambda: None)
    monkeypatch.setattr(release, "get_current_version", lambda: "0.5.0")
    monkeypatch.setattr(release, "run", fake_run)
    monkeypatch.setattr(release, "github_token", no_token)

    with pytest.raises(SystemExit):
        release.tag_release("0.5.0", dry_run=False)

    assert not any(call[:3] == ("git", "tag", "-a") for call in calls)


def test_prepare_dry_run_does_not_tag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release, "check_main_branch", lambda **_: None)
    monkeypatch.setattr(release, "check_clean_tree", lambda: None)
    monkeypatch.setattr(release, "get_current_version", lambda: "0.4.3")
    monkeypatch.setattr(release, "check_tag_absent", lambda _: None)
    monkeypatch.setattr(release, "check_branch_absent", lambda _: None)
    monkeypatch.setattr(release, "bump_pyproject", lambda *_: None)
    monkeypatch.setattr(release, "refresh_lockfile", lambda *_: None)
    monkeypatch.setattr(release, "update_kit_pin", lambda *_: None)
    monkeypatch.setattr(release, "update_changelog", lambda *_, **__: None)
    monkeypatch.setattr(release.sys, "argv", ["release.py", "0.5.0", "--dry-run"])

    release.main()

    output = capsys.readouterr().out
    assert "No tag is created" in output
    assert "--tag-only" in output
    assert "Tagged:" not in output


def test_tag_only_dispatches_to_tag_release(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_tag_release(version: str, dry_run: bool) -> None:
        called["version"] = version
        called["dry_run"] = dry_run

    monkeypatch.setattr(release, "tag_release", fake_tag_release)
    monkeypatch.setattr(release.sys, "argv", ["release.py", "0.5.0", "--tag-only", "--dry-run"])

    release.main()

    assert called == {"version": "0.5.0", "dry_run": True}
