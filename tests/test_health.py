"""Tests for health check helpers."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from mementic.health import HealthItem, check_health
from mementic.store import MementicStore


@pytest.fixture
def store() -> Generator[MementicStore, None, None]:
    """Fresh in-memory store per test."""
    s = MementicStore(db_path=":memory:")
    yield s
    s.close()


def _graph_row(items: list[HealthItem]) -> HealthItem:
    """Return the Graph health item."""
    return next(item for item in items if item.label == "Graph")


def test_graph_hint_counts_missing_and_changed(
    store: MementicStore,
    tmp_path: Path,
) -> None:
    (tmp_path / "changed.py").write_bytes(b"new bytes\n")
    store.add_constraint(
        entry_id="c1",
        description="d",
        created_at="2024-01-01T00:00:00Z",
        paths=["changed.py", "gone.py"],
        path_hashes={"changed.py": "0" * 64, "gone.py": "1" * 64},
    )
    hint = _graph_row(check_health(graph=store, resolve_path=lambda p: tmp_path / p)).hint
    assert hint == "0 decisions, 1 constraints; bindings: 1 missing, 1 changed, 0 unknown"


def test_graph_hint_renders_segment_for_current_binding(
    store: MementicStore,
    tmp_path: Path,
) -> None:
    """An unchanged file still renders the segment, with every count at zero."""
    payload = b"x = 1\n"
    (tmp_path / "a.py").write_bytes(payload)
    store.add_constraint(
        entry_id="c1",
        description="d",
        created_at="2024-01-01T00:00:00Z",
        paths=["a.py"],
        path_hashes={"a.py": hashlib.sha256(payload).hexdigest()},
    )
    hint = _graph_row(check_health(graph=store, resolve_path=lambda p: tmp_path / p)).hint
    assert hint.endswith("; bindings: 0 missing, 0 changed, 0 unknown")


def test_graph_hint_counts_unknown_for_hashless_binding(
    store: MementicStore,
    tmp_path: Path,
) -> None:
    """A NULL hash is unknown before any file access, even when the file is gone."""
    store.add_decision(
        entry_id="d1",
        title="Title d1",
        rationale="r",
        created_at="2024-01-01T00:00:00Z",
        paths=["gone.py"],
    )
    hint = _graph_row(check_health(graph=store, resolve_path=lambda p: tmp_path / p)).hint
    assert "0 missing" in hint
    assert "1 unknown" in hint


def test_graph_hint_counts_all_unknown_without_resolver(
    store: MementicStore,
    tmp_path: Path,
) -> None:
    """Without a resolver no binding can be verified, so all count unknown."""
    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    store.add_constraint(
        entry_id="c1",
        description="d",
        created_at="2024-01-01T00:00:00Z",
        paths=["a.py"],
        path_hashes={"a.py": "0" * 64},
    )
    hint = _graph_row(check_health(graph=store)).hint
    assert "bindings: 0 missing, 0 changed, 1 unknown" in hint


def test_graph_hint_omits_binding_segment_when_no_bindings(
    store: MementicStore,
) -> None:
    store.add_decision(
        entry_id="d1",
        title="Title d1",
        rationale="r",
        created_at="2024-01-01T00:00:00Z",
    )
    item = _graph_row(check_health(graph=store))
    assert item.hint == "1 decisions, 0 constraints"


def test_missing_graph_hint_unchanged() -> None:
    items = check_health(graph=None)
    item = _graph_row(items)
    assert (len(items), item.status, item.label) == (2, "fail", "Graph")
    assert "Could not load graph" in item.hint


def test_graph_hint_names_schema_mismatch_for_existing_old_graph(tmp_path: Path) -> None:
    """An existing v5 graph explains the version bump instead of claiming no graph."""
    db = tmp_path / "graph.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
    finally:
        conn.close()
    items = check_health(graph=None, graph_path=db)
    item = _graph_row(items)
    assert (len(items), item.status) == (2, "fail")
    assert "schema v5" in item.hint
    assert "requires v6" in item.hint
    assert "move the file aside" in item.hint
    assert "Run 'mementic init' here" not in item.hint


def test_graph_hint_reports_unreadable_graph_file(tmp_path: Path) -> None:
    """A garbage file at the graph path is named as unreadable, not as missing."""
    db = tmp_path / "graph.db"
    db.write_bytes(b"not a database")
    item = _graph_row(check_health(graph=None, graph_path=db))
    assert "not a readable mementic database" in item.hint
    assert "Run 'mementic init' here" not in item.hint
