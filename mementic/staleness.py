"""Content-hash staleness detection for entry path bindings.

Hashing and classification live here so the SQL layer stays filesystem-free:
the store records hashes as inert metadata, and this module compares them
against the bytes on disk only when a caller asks for a staleness read.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mementic.models import PathBinding

BINDING_HASH_ALGORITHM: str = "sha256"
_HASH_CHUNK_SIZE = 1024 * 1024


class BindingState(StrEnum):
    """Classification of one recorded binding against the file on disk."""

    CURRENT = "current"
    CHANGED = "changed"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class BindingCheck:
    """One binding paired with its classified staleness state.

    Attributes:
        binding: The stored entry_paths row.
        state: What the recorded hash says about the file on disk.
    """

    binding: PathBinding
    state: BindingState


_STALE_STATE_RANK: dict[BindingState, int] = {
    BindingState.MISSING: 0,
    BindingState.CHANGED: 1,
}


def content_hash(path: Path) -> str | None:
    """Return the sha256 hex digest of a file's bytes.

    Only readable regular files hash: directories, missing paths, and
    unreadable files return None. Bytes are read in binary so newline
    translation never skews a digest.

    Args:
        path: File to hash.

    Returns:
        Lowercase sha256 hex digest, or None when ``path`` is not a
        readable regular file.
    """
    try:
        if not path.is_file():
            return None
        digest = hashlib.new(BINDING_HASH_ALGORITHM)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def classify_binding(binding: PathBinding, *, resolve: Callable[[str], Path]) -> BindingState:
    """Classify one binding against the file it points at.

    A binding that recorded no hash is UNKNOWN and returns before any
    filesystem access, so a hashless binding of a deleted file is unknown
    rather than missing.

    Args:
        binding: The stored binding row.
        resolve: Maps a stored path to the path to hash on disk.

    Returns:
        UNKNOWN when no hash was recorded or a present file's bytes could
        not be read, MISSING when the path is not a regular file (missing,
        directory), CHANGED when the digest differs, and CURRENT otherwise.
    """
    if binding.content_hash is None:
        return BindingState.UNKNOWN
    resolved = resolve(binding.path)
    if not resolved.is_file():
        return BindingState.MISSING
    digest = content_hash(resolved)
    if digest is None:
        return BindingState.UNKNOWN
    if digest != binding.content_hash:
        return BindingState.CHANGED
    return BindingState.CURRENT


def classify_bindings(
    bindings: Iterable[PathBinding],
    *,
    resolve: Callable[[str], Path],
) -> list[BindingCheck]:
    """Classify each binding, preserving input order.

    Args:
        bindings: Binding rows to classify.
        resolve: Maps a stored path to the path to hash on disk.

    Returns:
        One check per input binding, in input order.
    """
    return [
        BindingCheck(binding=binding, state=classify_binding(binding, resolve=resolve))
        for binding in bindings
    ]


def staleness_counts(checks: Iterable[BindingCheck]) -> dict[str, int]:
    """Count classified bindings per state.

    Args:
        checks: Classified bindings.

    Returns:
        Map keyed by every state value; states with no checks count 0.
    """
    counts = {state.value: 0 for state in BindingState}
    for check in checks:
        counts[check.state.value] += 1
    return counts


def hash_provider(resolve: Callable[[str], Path]) -> Callable[[str], str | None]:
    """Return a write-time hash function over stored paths.

    Args:
        resolve: Maps a stored path to the file to hash on disk.

    Returns:
        Callable mapping a stored path to its digest, or None when the file
        is not a readable regular file.
    """
    return lambda stored: content_hash(resolve(stored))


def _stale_phrase(state: BindingState, path: str) -> str | None:
    """Return ``"<path> <state>"`` for a stale state, or None otherwise."""
    if state is BindingState.CHANGED:
        return f"{path} changed"
    if state is BindingState.MISSING:
        return f"{path} missing"
    return None


def stale_marker(check: BindingCheck) -> str:
    """Return the bracketed stale suffix for one binding check.

    Args:
        check: Classified binding.

    Returns:
        ``" [stale: <path> changed]"``, ``" [stale: <path> missing]"``, or
        an empty string for current and unknown bindings.
    """
    phrase = _stale_phrase(check.state, check.binding.path)
    if phrase is None:
        return ""
    return f" [stale: {phrase}]"


def markers_by_entry(checks: Iterable[BindingCheck]) -> dict[str, str]:
    """Return one stale suffix per entry, covering all its stale paths.

    Per entry, missing paths come before changed ones and paths sort
    ascending, so the combined marker renders the same way every time.

    Args:
        checks: Classified bindings.

    Returns:
        Map of entry ID to the combined bracketed suffix; entries with no
        stale path are absent.
    """
    grouped: dict[str, list[tuple[int, str]]] = {}
    for check in checks:
        phrase = _stale_phrase(check.state, check.binding.path)
        if phrase is None:
            continue
        rank = _STALE_STATE_RANK[check.state]
        grouped.setdefault(check.binding.entry_id, []).append((rank, phrase))
    markers: dict[str, str] = {}
    for entry_id, items in grouped.items():
        items.sort()
        markers[entry_id] = f" [stale: {', '.join(phrase for _, phrase in items)}]"
    return markers
