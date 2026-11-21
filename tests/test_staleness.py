"""Tests for binding staleness hashing and classification."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from mementic.models import PathBinding
from mementic.staleness import (
    BindingCheck,
    BindingState,
    classify_binding,
    classify_bindings,
    content_hash,
    hash_provider,
    markers_by_entry,
    stale_marker,
    staleness_counts,
)


def _resolve(base_dir: Path) -> Callable[[str], Path]:
    """Return a resolver mapping stored paths under ``base_dir``."""
    return lambda stored: base_dir / stored


def _write(base_dir: Path, name: str, text: str = "x = 1\n") -> str:
    """Write one file and return the sha256 digest of its bytes."""
    payload = text.encode()
    (base_dir / name).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _binding(path: str, digest: str | None, entry_id: str = "c1") -> PathBinding:
    """Build a constraint binding row for one path."""
    return PathBinding(entry_type="constraint", entry_id=entry_id, path=path, content_hash=digest)


def test_content_hash_matches_known_sha256(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"x = 1\n")
    assert (
        content_hash(target) == "9e26bf369911c45c243c684147b23fc9e1dcfcf257d299a1c632016a6fcd33f4"
    )


def test_content_hash_returns_none_for_directory(tmp_path: Path) -> None:
    assert content_hash(tmp_path) is None


def test_content_hash_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert content_hash(tmp_path / "gone.py") is None


def test_unchanged_file_classifies_current(tmp_path: Path) -> None:
    digest = _write(tmp_path, "a.py")
    assert (
        classify_binding(_binding("a.py", digest), resolve=_resolve(tmp_path))
        is BindingState.CURRENT
    )


def test_changed_file_classifies_changed(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 2\n")
    assert (
        classify_binding(_binding("a.py", "0" * 64), resolve=_resolve(tmp_path))
        is BindingState.CHANGED
    )


def test_read_failure_classifies_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present regular file whose bytes cannot be read is unknown, not missing."""
    _write(tmp_path, "a.py")
    monkeypatch.setattr("mementic.staleness.content_hash", lambda path: None)
    assert (
        classify_binding(_binding("a.py", "0" * 64), resolve=_resolve(tmp_path))
        is BindingState.UNKNOWN
    )


def test_missing_file_classifies_missing(tmp_path: Path) -> None:
    assert (
        classify_binding(_binding("gone.py", "0" * 64), resolve=_resolve(tmp_path))
        is BindingState.MISSING
    )


def test_null_hash_classifies_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "a.py")
    assert (
        classify_binding(_binding("a.py", None), resolve=_resolve(tmp_path)) is BindingState.UNKNOWN
    )


def test_null_hash_wins_over_missing_file(tmp_path: Path) -> None:
    """No recorded hash short-circuits classification before any file access."""

    def unreachable(stored: str) -> Path:
        raise AssertionError(f"resolve called for hashless binding {stored}")

    binding = _binding("gone.py", None)
    assert classify_binding(binding, resolve=unreachable) is BindingState.UNKNOWN


def test_stale_marker_text_for_changed() -> None:
    check = BindingCheck(binding=_binding("src/a.py", "0" * 64), state=BindingState.CHANGED)
    assert stale_marker(check) == " [stale: src/a.py changed]"


def test_stale_marker_text_for_missing() -> None:
    check = BindingCheck(binding=_binding("src/a.py", "0" * 64), state=BindingState.MISSING)
    assert stale_marker(check) == " [stale: src/a.py missing]"


def test_stale_marker_empty_for_current_and_unknown() -> None:
    current = BindingCheck(binding=_binding("a.py", None), state=BindingState.CURRENT)
    unknown = BindingCheck(binding=_binding("a.py", None), state=BindingState.UNKNOWN)
    assert (stale_marker(current), stale_marker(unknown)) == ("", "")


def test_staleness_counts_groups_states(tmp_path: Path) -> None:
    current_digest = _write(tmp_path, "current.py")
    _write(tmp_path, "changed.py", "x = 2\n")
    bindings = [
        _binding("current.py", current_digest),
        _binding("changed.py", "0" * 64, entry_id="c2"),
        _binding("gone.py", "1" * 64, entry_id="c3"),
        _binding("unhashed.py", None, entry_id="c4"),
    ]
    checks = classify_bindings(bindings, resolve=_resolve(tmp_path))
    assert staleness_counts(checks) == {
        "current": 1,
        "changed": 1,
        "missing": 1,
        "unknown": 1,
    }
    assert staleness_counts([]) == {
        "current": 0,
        "changed": 0,
        "missing": 0,
        "unknown": 0,
    }


def test_markers_by_entry_orders_missing_before_changed() -> None:
    checks = [
        BindingCheck(binding=_binding("a.py", "h"), state=BindingState.CHANGED),
        BindingCheck(binding=_binding("b.py", "h"), state=BindingState.MISSING),
        BindingCheck(binding=_binding("c.py", "h", entry_id="c2"), state=BindingState.CURRENT),
    ]
    assert markers_by_entry(checks) == {"c1": " [stale: b.py missing, a.py changed]"}


def test_hash_provider_hashes_resolved_files(tmp_path: Path) -> None:
    digest = _write(tmp_path, "a.py")
    provider = hash_provider(_resolve(tmp_path))
    assert (provider("a.py"), provider("gone.py")) == (digest, None)
