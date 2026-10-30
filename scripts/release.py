"""Deterministic release script for mementic.

Prepares a release branch from main: bumps the version, updates the changelog,
refreshes the lockfile, commits, and pushes the branch. The tag is created only
after the release pull request merges, with ``--tag-only``. Pushing the tag
triggers the Release workflow, which verifies the tag, builds the wheel and
sdist, and creates the GitHub Release from the version's ``CHANGELOG.md``
section. The sbx kit image is not published; the script only bumps its local
version pin.

Usage:
    python scripts/release.py 0.5.0 --notes-file notes.md  # prepare the branch
    python scripts/release.py 0.5.0 --tag-only              # tag merged main
    python scripts/release.py 0.5.0 --print-notes           # extract release notes
    python scripts/release.py 0.5.0 --dry-run               # preview
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
LOCKFILE = ROOT / "uv.lock"
KIT_DIR = ROOT / "docker" / "sbx-mementic"
KIT_DESCRIPTOR = KIT_DIR / "mementic.yaml"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def die(msg: str) -> NoReturn:
    print(f"ERROR: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"  > {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def dry(msg: str) -> None:
    print(f"  [DRY RUN] {msg}")


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command from the repo root. Exits on failure."""
    try:
        return subprocess.run(args, cwd=ROOT, check=True, capture_output=capture, text=True)
    except subprocess.CalledProcessError as e:
        die(f"Command failed: {' '.join(args)}\n{e.stderr.strip()}")


def get_current_version() -> str:
    content = PYPROJECT.read_text(encoding="utf-8")
    m = VERSION_RE.search(content)
    if not m:
        die('Could not find version = "..." in pyproject.toml [project] section.')
    return m.group(1)


def parse_semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def validate_version(version: str, current: str) -> None:
    if not SEMVER_RE.match(version):
        die(f"'{version}' is not valid semver (X.Y.Z).")
    if parse_semver(version) <= parse_semver(current):
        die(f"{version} <= current version {current}. Must be strictly greater.")


def check_clean_tree() -> None:
    result = run("git", "status", "--porcelain", capture=True)
    if result.stdout.strip():
        die("Working directory is not clean. Commit or stash changes first.")


def check_main_branch(*, dry_run: bool = False) -> None:
    result = run("git", "branch", "--show-current", capture=True)
    branch = result.stdout.strip()
    if branch == "main":
        ok("On main branch")
        return
    if dry_run:
        warn(f"Not on main (current: '{branch}'). Skipped for dry-run.")
        return
    die(f"Must be on 'main' branch to release. Current: '{branch}'.")


def check_tag_absent(version: str) -> None:
    tag = f"v{version}"
    result = run("git", "tag", "--list", tag, capture=True)
    if result.stdout.strip():
        die(f"Tag '{tag}' already exists locally.")


def check_remote_tag_absent(version: str) -> None:
    """Fail when the release tag already exists on origin."""
    tag = f"v{version}"
    result = run("git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}", capture=True)
    if result.stdout.strip():
        die(f"Tag '{tag}' already exists on origin.")


def check_branch_absent(branch: str) -> None:
    result = run("git", "branch", "--list", branch, capture=True)
    if result.stdout.strip():
        die(f"Branch '{branch}' already exists locally.")
    result = run("git", "ls-remote", "--heads", "origin", branch, capture=True)
    if result.stdout.strip():
        die(f"Branch '{branch}' already exists on remote.")


def bump_pyproject(version: str, dry_run: bool) -> None:
    content = PYPROJECT.read_text(encoding="utf-8")
    new_content = VERSION_RE.sub(f'version = "{version}"', content, count=1)
    if new_content == content:
        die("Failed to replace version in pyproject.toml.")
    if dry_run:
        dry(f'Would update pyproject.toml: version = "{version}"')
        return
    PYPROJECT.write_text(new_content, encoding="utf-8")
    ok(f'pyproject.toml -> version = "{version}"')


def refresh_lockfile(dry_run: bool) -> None:
    """Regenerate uv.lock so the workspace package records the new version."""
    if not LOCKFILE.exists():
        warn("uv.lock not found; skipping lockfile refresh.")
        return
    if dry_run:
        dry("Would run 'uv lock' to refresh uv.lock")
        return
    run("uv", "lock")
    ok("uv.lock -> refreshed")


def update_kit_pin(version: str, dry_run: bool) -> None:
    """Keep the sbx mixin's pinned release in sync with pyproject.toml."""
    if not KIT_DESCRIPTOR.exists():
        warn("sbx kit descriptor not found; skipping kit version pin.")
        return
    content = KIT_DESCRIPTOR.read_text(encoding="utf-8")
    new_content = re.sub(
        r'(default:\s*")[^"]+(")',
        rf"\g<1>{version}\g<2>",
        content,
        count=1,
    )
    if new_content == content:
        die("Failed to replace the kit version pin.")
    if dry_run:
        dry(f"Would update sbx kit pin: {version}")
        return
    KIT_DESCRIPTOR.write_text(new_content, encoding="utf-8")
    ok(f"sbx kit -> version {version}")


def get_last_version_tag() -> str | None:
    """Return the most recent v* tag reachable from main, or None."""
    result = run("git", "tag", "--sort=-creatordate", "--merged", "main", capture=True)
    for line in result.stdout.splitlines():
        tag = line.strip()
        if tag.startswith("v") and SEMVER_RE.match(tag[1:]):
            return tag
    return None


def get_merge_messages_since(tag: str) -> list[str]:
    """Return merge commit subjects between tag and main, excluding release PRs."""
    result = run("git", "log", f"{tag}..main", "--merges", "--format=%s", capture=True)
    messages: list[str] = []
    for line in result.stdout.splitlines():
        msg = line.strip()
        if not msg:
            continue
        if "release/" in msg:
            continue
        messages.append(msg)
    return messages


def _resolve_notes_file(notes_file: Path) -> Path:
    """Resolve a notes path relative to the repository root."""
    return notes_file if notes_file.is_absolute() else ROOT / notes_file


def _read_notes(notes_file: Path) -> str:
    """Read curated release notes, failing on a missing or empty file."""
    notes_path = _resolve_notes_file(notes_file)
    if not notes_path.exists():
        die(f"Release notes file not found: {notes_path}")
    notes = notes_path.read_text(encoding="utf-8").strip()
    if not notes:
        die(f"Release notes file is empty: {notes_path}")
    return notes


def update_changelog(version: str, dry_run: bool, notes_file: Path | None = None) -> None:
    """Insert the release section below ``[Unreleased]``.

    Curated notes from ``notes_file`` take precedence over merge-commit
    subjects. Without a notes file, the section is generated from merge commits
    since the last reachable version tag. ``[Unreleased]`` stays first; the new
    section goes above the newest release.
    """
    if not CHANGELOG.exists():
        die("CHANGELOG.md not found.")

    today = date.today().isoformat()
    header = f"## [{version}] - {today}"
    count: int | None

    if notes_file is not None:
        body = _read_notes(notes_file)
        count = None
    else:
        last_tag = get_last_version_tag()
        if last_tag is None:
            die("No previous version tag found; can't determine which commits to include.")
        merge_messages = get_merge_messages_since(last_tag)
        if not merge_messages:
            die(f"No merge commits found since {last_tag}. Nothing to include in changelog.")
        body = "\n".join(f"- {msg}" for msg in merge_messages)
        count = len(merge_messages)

    if dry_run:
        print()
        print("[DRY RUN] Would add changelog section:")
        print(f"  {header}")
        for line in body.splitlines():
            print(f"  {line}")
        return

    content = CHANGELOG.read_text(encoding="utf-8")
    new_section = f"{header}\n\n{body}\n\n"
    next_release = re.search(r"^## \[(?!Unreleased\])", content, re.MULTILINE)
    if next_release:
        new_content = (
            content[: next_release.start()] + new_section + content[next_release.start() :]
        )
    else:
        new_content = content.rstrip() + "\n\n" + new_section

    CHANGELOG.write_text(new_content, encoding="utf-8")
    suffix = f" ({count} merge commits)" if count is not None else ""
    ok(f"CHANGELOG.md -> {header}{suffix}")


def changelog_section(version: str) -> str:
    """Return the changelog body for ``version`` without its heading."""
    content = CHANGELOG.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    if match is None:
        die(f"CHANGELOG.md has no '## [{version}]' section to use as release notes.")
    body = match.group(1).strip()
    if not body:
        die(f"The '## [{version}]' changelog section is empty.")
    return body


def tag_release(version: str, dry_run: bool) -> None:
    """Create and push the annotated release tag on merged main."""
    tag = f"v{version}"
    print("Pre-flight checks")
    check_main_branch(dry_run=dry_run)
    check_clean_tree()
    ok("Working tree clean")

    current = get_current_version()
    if current != version:
        die(f"pyproject.toml is {current}, not {version}.")
    ok(f"Version: {current}")

    run("git", "fetch", "origin", "main")
    head = run("git", "rev-parse", "HEAD", capture=True).stdout.strip()
    remote = run("git", "rev-parse", "origin/main", capture=True).stdout.strip()
    if head != remote:
        die("HEAD is not origin/main. Merge the release PR and pull first.")
    ok("HEAD matches origin/main")

    check_tag_absent(version)
    check_remote_tag_absent(version)
    ok(f"Tag {tag} does not exist")

    if dry_run:
        dry(f"Would create annotated tag {tag} on HEAD")
        dry(f"Would push tag {tag} to origin (the Release workflow publishes it)")
        return

    run("git", "tag", "-a", tag, "-m", f"Release {tag}")
    ok(f"Tagged: {tag}")
    run("git", "push", "origin", tag)
    ok(f"Pushed tag: {tag}")
    print(f"\nRelease {tag} tagged and pushed. The Release workflow will publish it.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Release mementic to a new version.")
    parser.add_argument("version", help="New version, e.g. 0.5.0")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview actions without making changes"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--tag-only",
        action="store_true",
        help="Create and push the release tag; the Release workflow publishes it.",
    )
    mode.add_argument(
        "--print-notes",
        action="store_true",
        help="Print the changelog section for the version and exit.",
    )
    parser.add_argument(
        "--notes-file",
        type=Path,
        help="Markdown file with curated release notes.",
    )
    args = parser.parse_args()

    version = args.version.lstrip("v")
    tag = f"v{version}"
    branch = f"release/{tag}"
    dry_run: bool = args.dry_run

    if args.notes_file and (args.tag_only or args.print_notes):
        parser.error("--notes-file is only used when preparing a release branch")

    if args.print_notes:
        print(changelog_section(version))
        return

    if args.tag_only:
        tag_release(version, dry_run)
        return

    # -- Pre-flight --
    print("Pre-flight checks")
    check_main_branch(dry_run=dry_run)
    check_clean_tree()
    ok("Working tree clean")

    current = get_current_version()
    ok(f"Current version: {current}")
    validate_version(version, current)
    ok(f"{version} > {current}")
    check_tag_absent(version)
    ok(f"Tag {tag} does not exist")
    check_branch_absent(branch)
    ok(f"Branch {branch} does not exist")

    # -- Create branch --
    print()
    if dry_run:
        dry(f"Would create branch: {branch}")
    else:
        run("git", "checkout", "-b", branch)
        ok(f"Created branch: {branch}")

    # -- Bump & changelog --
    bump_pyproject(version, dry_run)
    refresh_lockfile(dry_run)
    update_kit_pin(version, dry_run)
    update_changelog(version, dry_run, args.notes_file)

    if dry_run:
        print()
        print(f"[DRY RUN] Would commit on {branch} and push the branch.")
        print("[DRY RUN] No tag is created; run --tag-only after the PR merges.")
        print("[DRY RUN] Done -- no changes made.")
        return

    # -- Commit --
    release_paths = (
        "pyproject.toml",
        "uv.lock",
        "CHANGELOG.md",
        "docker/sbx-mementic/mementic.yaml",
    )
    staged = [path for path in release_paths if (ROOT / path).exists()]
    run("git", "add", *staged)
    run("git", "commit", "-m", f"Release {tag}")
    ok(f"Committed: Release {tag}")

    # -- Push branch --
    run("git", "push", "-u", "origin", branch)
    ok(f"Pushed branch: {branch}")

    # -- Done --
    print(f"""
+--- Release {version} prepared! ---+

  Branch : {branch}
  Tag    : {tag} (not created yet)

  Next steps:
    1. Create a PR: {branch} -> main
    2. Review & merge using MERGE COMMIT (not squash or rebase)
    3. Delete {branch} after merge
    4. Pull main, then create and push the tag:
       python scripts/release.py {version} --tag-only

  ! The tag is created only after merge; do not tag the release branch.
  ! Pushing the tag triggers the Release workflow to publish it.
""")


if __name__ == "__main__":
    main()
