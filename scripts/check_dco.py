"""Validate the DCO sign-off in a commit message before the commit is created.

Installed as a commit-msg hook by pre-commit. Mirrors the check in
.github/workflows/dco.yml so contributors find a missing or mismatched
sign-off locally instead of after pushing.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SIGNOFF_RE = re.compile(r"^Signed-off-by:.*<([^>]+)>\s*$", re.IGNORECASE | re.MULTILINE)
MERGE_PREFIX = "Merge "


def git_output(*args: str) -> str:
    """Run a git command and return its stdout."""
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def is_merge_in_progress() -> bool:
    """Return True when the repository has an unfinished merge."""
    return Path(git_output("git", "rev-parse", "--git-path", "MERGE_HEAD").strip()).exists()


def author_email() -> str:
    """Return the email git will record as the commit author."""
    match = re.search(r"<([^>]+)>", git_output("git", "var", "GIT_AUTHOR_IDENT"))
    if match is None:
        raise RuntimeError("Could not parse the git author identity")
    return match.group(1)


def main(argv: list[str]) -> int:
    """Validate one commit message file and return a process exit code."""
    if len(argv) != 1:
        print("usage: check_dco.py <commit-message-file>", file=sys.stderr)
        return 2

    message = Path(argv[0]).read_text(encoding="utf-8")
    if message.startswith(MERGE_PREFIX) or is_merge_in_progress():
        return 0

    signoffs = [match.group(1).strip() for match in SIGNOFF_RE.finditer(message)]
    if not signoffs:
        print("DCO check failed: commit message has no 'Signed-off-by:' trailer.", file=sys.stderr)
        print("Fix with: git commit -s", file=sys.stderr)
        return 1

    try:
        email = author_email()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr.strip(), file=sys.stderr)
        return 2

    if email.lower() not in {signoff.lower() for signoff in signoffs}:
        print(f"DCO check failed: no sign-off matches the author email {email}.", file=sys.stderr)
        print(f"Found sign-offs: {', '.join(signoffs)}", file=sys.stderr)
        print("Fix with: git commit --amend -s", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
