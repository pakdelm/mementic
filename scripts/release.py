"""Deterministic release script for mementic.

Creates a release branch from main, bumps the version, updates the changelog,
commits, tags, and pushes. You then create a PR for review and merge.

Usage:
    python scripts/release.py 0.2.3          # full release
    python scripts/release.py 0.2.3 --dry-run  # preview only
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import NoReturn

from rich.console import Console
from rich.panel import Panel

console = Console(force_terminal=True)
ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def die(msg: str) -> NoReturn:
    console.print(f"[red]ERROR:[/] {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    console.print(f"  [green]>[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"  [yellow]![/] {msg}")


def dry(msg: str) -> None:
    console.print(f"  [dim][DRY RUN][/] {msg}")


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command from the repo root. Exits on failure."""
    try:
        return subprocess.run(
            args, cwd=ROOT, check=True, capture_output=capture, text=True
        )
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
        # Skip release branch merges (e.g. "Merge pull request #X from .../release/...")
        if "release/" in msg:
            continue
        messages.append(msg)
    return messages


def update_changelog(version: str, dry_run: bool) -> None:
    if not CHANGELOG.exists():
        die("CHANGELOG.md not found.")

    last_tag = get_last_version_tag()
    if last_tag is None:
        die("No previous version tag found. Can't determine which commits to include.")
    merge_messages = get_merge_messages_since(last_tag)
    if not merge_messages:
        die(f"No merge commits found since {last_tag}. Nothing to include in changelog.")

    today = date.today().isoformat()
    header = f"## [{version}] - {today}"

    if dry_run:
        console.print("\n[dim]Would add changelog section:[/]")
        console.print(f"  [bold]{header}[/]")
        for msg in merge_messages:
            console.print(f"  [dim]- {msg}[/]")
        return

    # Build section and insert after preamble, before first existing version header.
    section_lines = [header, ""]
    for msg in merge_messages:
        section_lines.append(f"- {msg}")
    section_lines.append("")
    new_section = "\n".join(section_lines)

    content = CHANGELOG.read_text(encoding="utf-8")
    first_version = re.search(r"^## \[", content, re.MULTILINE)
    if first_version:
        new_content = content[: first_version.start()] + new_section + content[first_version.start() :]
    else:
        new_content = content.rstrip() + "\n\n" + new_section

    CHANGELOG.write_text(new_content, encoding="utf-8")
    ok(f"CHANGELOG.md -> [{version}] - {today} ({len(merge_messages)} merge commits)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Release mementic to a new version.")
    parser.add_argument("version", help="New version, e.g. 0.2.3")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview actions without making changes"
    )
    args = parser.parse_args()

    version = args.version.lstrip("v")
    tag = f"v{version}"
    branch = f"release/{tag}"
    dry_run: bool = args.dry_run

    # ── Pre-flight ──────────────────────────────────────────────────
    console.print("[bold]Pre-flight checks[/]")
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

    # ── Create branch ───────────────────────────────────────────────
    console.print()
    if dry_run:
        dry(f"Would create branch: {branch}")
    else:
        run("git", "checkout", "-b", branch)
        ok(f"Created branch: [bold]{branch}[/]")

    # ── Bump & changelog ────────────────────────────────────────────
    bump_pyproject(version, dry_run)
    update_changelog(version, dry_run)

    if dry_run:
        console.print()
        console.print(f"[dim]Would commit on {branch}, tag {tag}, and push.[/]")
        console.print("[dim]Done -- no changes made.[/]")
        return

    # ── Commit & tag ────────────────────────────────────────────────
    run("git", "add", "pyproject.toml", "CHANGELOG.md")
    run("git", "commit", "-m", f"Release {tag}")
    ok(f"Committed: Release {tag}")
    run("git", "tag", tag)
    ok(f"Tagged: [bold]{tag}[/]")

    # ── Push ────────────────────────────────────────────────────────
    run("git", "push", "-u", "origin", branch)
    ok(f"Pushed branch: [bold]{branch}[/]")
    run("git", "push", "origin", tag)
    ok(f"Pushed tag: [bold]{tag}[/]")

    # ── Done ────────────────────────────────────────────────────────
    summary = (
        f"[bold]Release {version} prepared![/]\n\n"
        f"  Branch : [bold]{branch}[/]\n"
        f"  Tag    : [bold]{tag}[/]\n\n"
        f"[bold]Next steps:[/]\n"
        f"  1. Create a PR: {branch} -> main\n"
        f"  2. Review & merge using [bold]MERGE COMMIT[/] (not squash or rebase)\n"
        f"  3. Delete [bold]{branch}[/] after merge\n\n"
        f"[yellow]!  Squash/rebase will orphan the tag -- use merge commit only.[/]"
    )
    console.print()
    console.print(Panel(summary, title="Release", border_style="green"))


if __name__ == "__main__":
    main()
