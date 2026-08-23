"""Tests for the data models in :mod:`mementic.models`."""

from __future__ import annotations

from mementic.models import Constraint, Decision


def test_decision_defaults() -> None:
    """A Decision carries only v5 fields; paths default to empty."""
    d = Decision(id="d-1", title="Title", rationale="Why",
                 created_at="2024-01-01T00:00:00Z")
    assert d.paths == []


def test_constraint_defaults_and_enforced_by() -> None:
    """A Constraint defaults to standalone (empty enforced_by)."""
    c = Constraint(id="c-1", description="Rule", created_at="2024-01-01T00:00:00Z")
    assert c.enforced_by == ""
    assert c.paths == []


def test_decision_rejects_unknown_fields() -> None:
    """Legacy v4 columns (status/source/modified_at) are gone from the model."""
    d = Decision(id="d-1", title="T", rationale="R",
                 created_at="2024-01-01T00:00:00Z")
    assert "status" not in Decision.model_fields
    assert not hasattr(d, "status")
    assert "status" not in Constraint.model_fields
