"""Enforcement section sync for AGENTS.md and memory-directory hygiene.

The mementic enforcement block is marker-wrapped and synced into AGENTS.md:
created when absent, replaced in place when present, and everything outside
the section is preserved byte-for-byte. Re-running sync with a newer
template upgrades the section in existing files.

Writes go through a temporary file in the same directory and ``os.replace``,
so a failed write cannot leave a half-written instruction file. Read-only
targets return a warning instead of a traceback.
"""

from __future__ import annotations

import importlib.resources
import os
import subprocess
import tempfile
from pathlib import Path

from mementic.paths import MEMORY_DIR_NAME

START_MARKER = "<!-- mementic:start -->"
END_MARKER = "<!-- mementic:end -->"


def load_template() -> str:
    """Load the enforcement template from package resources."""
    ref = importlib.resources.files("mementic") / "templates" / "enforcement.md"
    return ref.read_text(encoding="utf-8")


def _rendered_block() -> str:
    """The marker-wrapped enforcement block without a trailing newline."""
    return load_template().strip()


def _write_text_atomic(path: Path, content: str) -> None:
    """Write text through a temporary file in the same directory.

    ``os.replace`` swaps the finished file into place, and a failed write
    removes the temporary file. The existing mode is preserved when the
    target already exists.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if path.exists():
            os.chmod(tmp_name, path.stat().st_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _is_git_work_tree(directory: Path) -> bool:
    """Return True when ``directory`` is inside a Git work tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def sync_gitignore(base_dir: Path, *, label: str | None = None) -> str | None:
    """Ensure the memory directory is ignored by Git.

    Does nothing outside a Git work tree or when the entry already exists.
    Returns a status or warning message in the other cases.

    Args:
        base_dir: Directory that owns ``.mementic/``.
        label: Path to show in the status message. Defaults to the full path.

    Returns:
        A status message when the file changed or could not be written, and
        ``None`` when the directory is not a Git work tree or the entry is
        already present.
    """
    if not _is_git_work_tree(base_dir):
        return None

    gitignore = base_dir / ".gitignore"
    entry = f"{MEMORY_DIR_NAME}/"
    shown = label or str(gitignore)

    try:
        content = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    except OSError as exc:
        return f"Warning: could not read {shown}: {exc}. Add '{entry}' manually."

    existing = {line.strip() for line in content.splitlines()}
    if entry in existing or MEMORY_DIR_NAME in existing:
        return None

    separator = ""
    if content:
        separator = "\n" if content.endswith("\n") else "\n\n"

    try:
        _write_text_atomic(gitignore, f"{content}{separator}{entry}\n")
    except OSError as exc:
        return f"Warning: could not write {shown}: {exc}. Add '{entry}' manually."

    return f"Added {entry} to {shown}."


def sync_enforcement(agents_md_path: Path, *, label: str | None = None) -> str:
    """Create or replace the mementic enforcement section in AGENTS.md.

    A marker-wrapped section is replaced between its markers; otherwise the
    block is appended, creating the file when missing. Content outside the
    section is preserved byte-for-byte.

    Args:
        agents_md_path: Path to AGENTS.md.
        label: Path to show in the status message. Defaults to ``agents_md_path``
            so callers can print a project-relative form.

    Returns:
        Status message describing the action taken, or a warning when the
        file cannot be read or written.
    """
    block = _rendered_block()
    shown = label or str(agents_md_path)

    try:
        if not agents_md_path.exists():
            _write_text_atomic(agents_md_path, f"{block}\n")
            return f"Created {shown} with enforcement block."

        content = agents_md_path.read_text(encoding="utf-8")

        if START_MARKER in content:
            if END_MARKER not in content:
                return (
                    f"Warning: mementic start marker without end marker in "
                    f"{shown}; manual fix required, no changes written."
                )
            head, _, tail = content.partition(START_MARKER)
            _, _, tail = tail.partition(END_MARKER)
            new_content = f"{head}{block}{tail}"
            if new_content == content:
                return f"Enforcement section already up to date in {shown}."
            _write_text_atomic(agents_md_path, new_content)
            return f"Updated enforcement section in {shown}."

        if content and not content.endswith("\n\n"):
            separator = "\n" if content.endswith("\n") else "\n\n"
            new_content = f"{content}{separator}{block}\n"
        else:
            new_content = f"{content}{block}\n"
        _write_text_atomic(agents_md_path, new_content)
        return f"Appended enforcement block to {shown}."
    except OSError as exc:
        return f"Warning: could not update {shown}: {exc}. Check file permissions."
