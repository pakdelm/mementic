"""Entry lifecycle operations for decisions and constraints."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from mementic.models import Decision
from mementic.schema import successor_timestamp, utc_now
from mementic.store import MementicStore
from mementic.text import slugify

DECISION_ID_MAX_LENGTH: int = 40
CONSTRAINT_ID_MAX_LENGTH: int = 40
DUPLICATE_OVERLAP_THRESHOLD: float = 0.8
MISSING_DECISION_MESSAGE: str = "enforcing decision '{decision_id}' does not exist"


class EntryKind(StrEnum):
    """The two entry types stored in the graph."""

    DECISION = "decision"
    CONSTRAINT = "constraint"


class AddOutcome(StrEnum):
    """What happened when an entry was added."""

    CREATED = "created"
    DUPLICATE = "duplicate"
    REPLACED = "replaced"
    REVIVED = "revived"


class DeprecateOutcome(StrEnum):
    """What happened when an entry was deprecated."""

    DEPRECATED = "deprecated"
    ALREADY_DEPRECATED = "already_deprecated"
    NOT_FOUND = "not_found"


class AddResult(BaseModel):
    """Outcome of one add operation."""

    model_config = ConfigDict(frozen=True)

    kind: EntryKind
    entry_id: str
    outcome: AddOutcome
    tombstone_at: str | None = None


class DeprecateResult(BaseModel):
    """Outcome of one deprecate operation."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    outcome: DeprecateOutcome
    message: str = ""


class MissingDecisionError(ValueError):
    """Raised when a constraint names an enforcing decision that does not exist."""

    def __init__(self, decision_id: str) -> None:
        super().__init__(MISSING_DECISION_MESSAGE.format(decision_id=decision_id))
        self.decision_id = decision_id


def word_overlap(text_a: str, text_b: str) -> float:
    """Return the Jaccard overlap of two lowercase word sets.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Overlap ratio between 0.0 and 1.0.
    """
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def clean_entity_path(raw: str) -> str:
    """Strip trailing ``::symbol`` or ``:symbol`` qualifiers from a path.

    Qualified symbols may contain dots (``Handler.handle``). A leading
    ``C:`` is a Windows drive reference, not a qualifier, so a drive-relative
    path such as ``C:src`` is preserved.

    Args:
        raw: Stored entity path, optionally suffixed with a symbol.

    Returns:
        The path without its symbol qualifier.
    """
    clean = re.sub(r"::[A-Za-z_][\w.]*$", "", raw)
    match = re.search(r":[A-Za-z_][\w.]*$", clean)
    if match is None:
        return clean
    if match.start() == 1 and clean[0].isalpha():
        return clean
    return clean[: match.start()]


class EntryWriter:
    """Record decisions, constraints, and deprecations against a store.

    The writer owns the deterministic auto-resolve rules: a new slug is
    accepted, a same-slug entry with near-identical content is skipped as a
    duplicate, and a same-slug entry with different content is deprecated
    before the successor is written.
    """

    def __init__(self, store: MementicStore) -> None:
        self._store = store

    def add_decision(
        self,
        *,
        title: str,
        rationale: str,
        paths: Sequence[str],
    ) -> AddResult:
        """Add a decision, auto-resolving against an existing slug.

        Args:
            title: Short decision title; its slug becomes the entry ID.
            rationale: Justification text.
            paths: Affected file paths.

        Returns:
            The add outcome and entry ID.

        Raises:
            ValueError: If the title produces an empty slug or collides with
                an existing constraint ID.
            sqlite3.IntegrityError: If the store rejects the write.
        """
        entry_id = slugify(title, max_length=DECISION_ID_MAX_LENGTH)
        if not entry_id:
            raise ValueError("title must contain at least one letter or digit")
        with self._store.transaction():
            outcome, tombstone_at = self._auto_resolve(
                EntryKind.DECISION, entry_id, rationale
            )
            if outcome is not AddOutcome.DUPLICATE:
                self._store.add_decision(
                    entry_id=entry_id,
                    title=title,
                    rationale=rationale,
                    created_at=self._successor_created_at(tombstone_at),
                    paths=[clean_entity_path(path) for path in paths],
                )
        return AddResult(
            kind=EntryKind.DECISION,
            entry_id=entry_id,
            outcome=outcome,
            tombstone_at=tombstone_at,
        )

    def add_constraint(
        self,
        *,
        description: str,
        paths: Sequence[str],
        enforced_by: str = "",
        rule_id: str | None = None,
    ) -> AddResult:
        """Add a constraint, auto-resolving against an existing slug.

        Args:
            description: Constraint text; its slug becomes the entry ID.
            paths: Bound file paths.
            enforced_by: Optional enforcing decision ID.
            rule_id: Optional explicit rule ID.

        Returns:
            The add outcome and entry ID.

        Raises:
            MissingDecisionError: If ``enforced_by`` names no decision.
            ValueError: If the description produces an empty slug or collides
                with an existing decision ID.
            sqlite3.IntegrityError: If the store rejects the write.
        """
        entry_id = (
            slugify(rule_id, max_length=CONSTRAINT_ID_MAX_LENGTH)
            if rule_id
            else slugify(description, max_length=CONSTRAINT_ID_MAX_LENGTH)
        )
        if not entry_id:
            raise ValueError("description or --id must contain a letter or digit")
        if enforced_by and self._store.get_decision(enforced_by) is None:
            raise MissingDecisionError(enforced_by)

        with self._store.transaction():
            outcome, tombstone_at = self._auto_resolve(
                EntryKind.CONSTRAINT, entry_id, description
            )
            if outcome is not AddOutcome.DUPLICATE:
                self._store.add_constraint(
                    entry_id=entry_id,
                    description=description,
                    enforced_by=enforced_by,
                    created_at=self._successor_created_at(tombstone_at),
                    paths=[clean_entity_path(path) for path in paths],
                )
        return AddResult(
            kind=EntryKind.CONSTRAINT,
            entry_id=entry_id,
            outcome=outcome,
            tombstone_at=tombstone_at,
        )

    def deprecate_many(
        self,
        entry_ids: Sequence[str],
        reason: str = "",
    ) -> list[DeprecateResult]:
        """Deprecate each entry.

        Args:
            entry_ids: Decision or constraint IDs to deprecate.
            reason: Optional human-readable reason.

        Returns:
            One result per input ID, in input order.
        """
        results: list[DeprecateResult] = []
        for entry_id in entry_ids:
            entry_type = self._store.get_entry_type(entry_id)
            if entry_type is None:
                results.append(
                    DeprecateResult(
                        entry_id=entry_id,
                        outcome=DeprecateOutcome.NOT_FOUND,
                        message=f"Entity '{entry_id}' not found.",
                    )
                )
                continue
            result = self._store.deprecate(entry_type, entry_id, reason)
            results.append(
                DeprecateResult(
                    entry_id=entry_id,
                    outcome=DeprecateOutcome(result["status"]),
                    message=result.get("message", ""),
                )
            )
        return results

    def _auto_resolve(
        self,
        kind: EntryKind,
        entry_id: str,
        content: str,
    ) -> tuple[AddOutcome, str | None]:
        """Apply the deterministic auto-resolve rules for an incoming entry."""
        existing_type = self._store.get_entry_type(entry_id)
        if existing_type is None:
            return AddOutcome.CREATED, None
        if existing_type != kind.value:
            return AddOutcome.CREATED, None

        existing = (
            self._store.get_decision(entry_id)
            if kind is EntryKind.DECISION
            else self._store.get_constraint(entry_id)
        )
        if existing is None:
            raise RuntimeError(f"entry {entry_id} vanished during auto-resolve")

        if self._store.is_deprecated(kind.value, entry_id):
            stamp = self._store.record_succession(
                kind.value,
                entry_id,
                replaced_by=entry_id,
                reason="superseded by re-added entry",
            )
            return AddOutcome.REVIVED, stamp

        stored_content = (
            existing.rationale
            if isinstance(existing, Decision)
            else existing.description
        )
        if word_overlap(content, stored_content) >= DUPLICATE_OVERLAP_THRESHOLD:
            return AddOutcome.DUPLICATE, None

        result = self._store.deprecate(
            kind.value,
            entry_id,
            reason="superseded by re-added entry",
            replaced_by=entry_id,
        )
        return AddOutcome.REPLACED, result["at"]

    def _successor_created_at(self, tombstone_at: str | None) -> str:
        """Stamp a fresh incarnation one microsecond after its tombstone."""
        if tombstone_at is None:
            return utc_now()
        return successor_timestamp(tombstone_at)
