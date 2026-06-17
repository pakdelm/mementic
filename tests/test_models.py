"""Tests for the data models in :mod:`mementic.models`."""

from __future__ import annotations

from typing import Any

from mementic.models import Decision, DecisionStatus


def _base_kwargs() -> dict[str, Any]:
    """Return the minimal required kwargs for constructing a ``Decision``."""
    return {
        "id": "d-1",
        "title": "Title",
        "rationale": "Why",
        "source": "test",
        "created_at": "2024-01-01T00:00:00Z",
        "modified_at": "2024-01-01T00:00:00Z",
    }


def test_decision_validator_coerces_proposed() -> None:
    """``status='proposed'`` is coerced to ACCEPTED (Spec 13 legacy value)."""
    result = Decision(**_base_kwargs(), status="proposed")  # type: ignore[arg-type]
    assert result.status == DecisionStatus.ACCEPTED


def test_decision_validator_coerces_superseded() -> None:
    """``status='superseded'`` is coerced to DEPRECATED (Spec 13 legacy value)."""
    result = Decision(**_base_kwargs(), status="superseded")  # type: ignore[arg-type]
    assert result.status == DecisionStatus.DEPRECATED


def test_decision_validator_coerces_unknown_status() -> None:
    """An unrecognized status string is coerced to ACCEPTED."""
    result = Decision(**_base_kwargs(), status="done")  # type: ignore[arg-type]
    assert result.status == DecisionStatus.ACCEPTED


def test_decision_validator_keeps_valid_status() -> None:
    """A valid status string passes through unchanged."""
    result = Decision(**_base_kwargs(), status="rejected")  # type: ignore[arg-type]
    assert result.status == DecisionStatus.REJECTED


def test_decision_validator_handles_non_string_status() -> None:
    """Passing a ``DecisionStatus`` enum directly does not crash the validator."""
    result = Decision(**_base_kwargs(), status=DecisionStatus.DEPRECATED)
    assert result.status == DecisionStatus.DEPRECATED
