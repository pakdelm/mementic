"""Tests for entry lifecycle operations."""

from __future__ import annotations

import sqlite3

import pytest

from mementic.entries import (
    AddOutcome,
    DeprecateOutcome,
    MISSING_DECISION_MESSAGE,
    clean_entity_path,
    EntryWriter,
    MissingDecisionError,
    word_overlap,
)
from mementic.store import MementicStore


@pytest.fixture
def store() -> MementicStore:
    """Fresh in-memory store."""
    return MementicStore(db_path=":memory:")


def _seed_decision(
    store: MementicStore,
    entry_id: str = "use-jwt-auth",
    title: str = "Use JWT Auth",
    rationale: str = "Token-based authentication for APIs",
) -> None:
    store.add_decision(
        entry_id=entry_id,
        title=title,
        rationale=rationale,
        created_at="2024-01-01T00:00:00+00:00",
    )


def test_word_overlap_identical_text() -> None:
    assert word_overlap("one two three", "one two three") == 1.0


def test_word_overlap_empty_texts() -> None:
    assert word_overlap("", "") == 1.0


def test_word_overlap_one_empty_text() -> None:
    assert word_overlap("one two", "") == 0.0


def test_word_overlap_partial_sets() -> None:
    assert word_overlap("one two three", "one two four") == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/foo.py::handler", "src/foo.py"),
        ("src/foo.py::Handler.handle", "src/foo.py"),
        ("src/foo.py:handler", "src/foo.py"),
        ("C:\\src\\foo.py", "C:\\src\\foo.py"),
        ("C:/src/foo.py", "C:/src/foo.py"),
    ],
)
def test_clean_entity_path_strips_symbol_qualifiers(
    raw: str, expected: str
) -> None:
    assert clean_entity_path(raw) == expected


def test_add_decision_creates_new_entry(store: MementicStore) -> None:
    result = EntryWriter(store).add_decision(
        title="Use JWT Auth",
        rationale="Token-based authentication for APIs",
        paths=["src/auth.py::login"],
    )

    assert result.outcome is AddOutcome.CREATED
    assert result.entry_id == "use-jwt-auth"
    stored = store.get_decision(result.entry_id)
    assert stored is not None
    assert stored.paths == ["src/auth.py"]


def test_add_decision_skips_duplicate(store: MementicStore) -> None:
    _seed_decision(store)

    result = EntryWriter(store).add_decision(
        title="Use JWT Auth",
        rationale="Token-based authentication for APIs",
        paths=[],
    )

    assert result.outcome is AddOutcome.DUPLICATE
    stored = store.get_decision("use-jwt-auth")
    assert stored is not None
    assert stored.created_at == "2024-01-01T00:00:00+00:00"


def test_add_decision_replaces_changed_content(store: MementicStore) -> None:
    _seed_decision(store)

    result = EntryWriter(store).add_decision(
        title="Use JWT Auth",
        rationale="Rotated keys replace tokens with signed certificates",
        paths=[],
    )

    assert result.outcome is AddOutcome.REPLACED
    assert result.tombstone_at is not None
    stored = store.get_decision("use-jwt-auth")
    assert stored is not None
    assert "certificates" in stored.rationale
    assert {entry.id for entry in store.get_live_decisions()} == {"use-jwt-auth"}


def test_add_decision_revives_deprecated_entry(store: MementicStore) -> None:
    _seed_decision(store)
    store.deprecate("decision", "use-jwt-auth", reason="old")

    result = EntryWriter(store).add_decision(
        title="Use JWT Auth",
        rationale="Token-based authentication for APIs",
        paths=[],
    )

    assert result.outcome is AddOutcome.REVIVED
    assert result.tombstone_at is not None
    assert {entry.id for entry in store.get_live_decisions()} == {"use-jwt-auth"}


def test_add_constraint_requires_existing_decision(store: MementicStore) -> None:
    with pytest.raises(MissingDecisionError) as excinfo:
        EntryWriter(store).add_constraint(
            description="Services must be stateless",
            paths=["src/services.py"],
            enforced_by="no-such-decision",
        )

    assert "does not exist" in str(excinfo.value)
    assert str(excinfo.value) == MISSING_DECISION_MESSAGE.format(
        decision_id="no-such-decision"
    )


def test_missing_decision_does_not_deprecate_existing_constraint(
    store: MementicStore,
) -> None:
    store.add_constraint(
        entry_id="services-must-be-stateless",
        description="Services must be stateless",
        created_at="2024-01-01T00:00:00+00:00",
    )

    with pytest.raises(MissingDecisionError):
        EntryWriter(store).add_constraint(
            description="Services must remain stateless",
            paths=["src/services.py"],
            enforced_by="no-such-decision",
        )

    assert store.is_deprecated("constraint", "services-must-be-stateless") is False


def test_add_decision_rejects_empty_slug(store: MementicStore) -> None:
    with pytest.raises(ValueError, match="letter or digit"):
        EntryWriter(store).add_decision(title="!!!", rationale="x", paths=[])


def test_add_constraint_rejects_empty_slug(store: MementicStore) -> None:
    with pytest.raises(ValueError, match="letter or digit"):
        EntryWriter(store).add_constraint(description="!!!", paths=["src/a.py"])


def test_add_decision_rejects_existing_constraint_slug(store: MementicStore) -> None:
    store.add_constraint(
        entry_id="shared-id",
        description="Existing constraint",
        created_at="2024-01-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="already exists as a constraint"):
        EntryWriter(store).add_decision(
            title="Shared Id", rationale="Decision text", paths=[]
        )


def test_failed_successor_write_rolls_back_tombstone(
    store: MementicStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_decision(store)

    def fail_add(**kwargs: object) -> str:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store, "add_decision", fail_add)
    with pytest.raises(sqlite3.OperationalError):
        EntryWriter(store).add_decision(
            title="Use JWT Auth",
            rationale="Rotated keys replace tokens with signed certificates",
            paths=[],
        )

    assert store.is_deprecated("decision", "use-jwt-auth") is False
    stored = store.get_decision("use-jwt-auth")
    assert stored is not None
    assert "Token-based" in stored.rationale


def test_deprecate_many_reports_each_outcome(store: MementicStore) -> None:
    _seed_decision(store)
    store.add_constraint(
        entry_id="services-must-be-stateless",
        description="Services must be stateless",
        created_at="2024-01-01T00:00:00+00:00",
    )

    results = EntryWriter(store).deprecate_many(
        ["use-jwt-auth", "services-must-be-stateless", "no-such-id"],
        reason="cleanup",
    )

    assert [result.outcome for result in results] == [
        DeprecateOutcome.DEPRECATED,
        DeprecateOutcome.DEPRECATED,
        DeprecateOutcome.NOT_FOUND,
    ]
    assert "not found" in results[-1].message.lower()
