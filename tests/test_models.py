"""Tests for the data models in :mod:`mementic.models`."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from mementic.models import Decision, DecisionStatus, ExtractionResult


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


def test_decision_validator_keeps_valid_status() -> None:
    """A valid status string passes through unchanged."""
    result = Decision(**_base_kwargs(), status="rejected")  # type: ignore[arg-type]
    assert result.status == DecisionStatus.REJECTED


def test_decision_validator_handles_non_string_status() -> None:
    """Passing a ``DecisionStatus`` enum directly does not crash the validator."""
    result = Decision(**_base_kwargs(), status=DecisionStatus.DEPRECATED)
    assert result.status == DecisionStatus.DEPRECATED


class TestExtractionResultCoercion:
    """Tests for ExtractionResult._coerce_llm_inputs (model_validator)."""

    def test_null_lists_coerced_to_empty(self) -> None:
        """None for all list fields is coerced to empty lists."""
        data = {"decisions": None, "constraints": None, "affected_entities": None, "edges_to_create": None}
        result = ExtractionResult.model_validate(data)
        assert result.decisions == []
        assert result.constraints == []
        assert result.affected_entities == []
        assert result.edges_to_create == []

    def test_single_dict_coerced_to_list(self) -> None:
        """A single dict for decisions is wrapped in a list."""
        data = {"decisions": {"id": "x", "title": "T", "rationale": "R", "source": "s",
                              "created_at": "t", "modified_at": "t"}}
        result = ExtractionResult.model_validate(data)
        assert len(result.decisions) == 1
        assert result.decisions[0].id == "x"

    def test_single_string_coerced_to_list(self) -> None:
        """A string for affected_entities is wrapped in a list."""
        result = ExtractionResult.model_validate({"affected_entities": "src/main.py"})
        assert result.affected_entities == ["src/main.py"]

    def test_nested_null_coerced_to_empty(self) -> None:
        """None for rejected_alternatives on a nested Decision is coerced to []."""
        data = {
            "decisions": [
                {"id": "d1", "title": "T", "rationale": "R", "rejected_alternatives": None,
                 "source": "s", "created_at": "t", "modified_at": "t"}
            ]
        }
        result = ExtractionResult.model_validate(data)
        assert result.decisions[0].rejected_alternatives == []

    def test_nested_single_string_coerced_to_list(self) -> None:
        """A string for bound_entities on a nested Constraint is coerced to [string]."""
        data = {
            "constraints": [
                {"id": "c1", "description": "D", "bound_entities": "src/x.py",
                 "source": "s", "created_at": "t"}
            ]
        }
        result = ExtractionResult.model_validate(data)
        assert result.constraints[0].bound_entities == ["src/x.py"]

    def test_coerced_decisions_still_validate_missing_fields(self) -> None:
        """Coercion does not mask truly invalid data — missing required fields fail."""
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate({"decisions": [{"id": "d1"}]})  # missing most fields

    def test_non_dict_input_passthrough(self) -> None:
        """Non-dict input is returned unchanged (Pydantic will reject it naturally)."""
        # The validator returns data unchanged for non-dicts; Pydantic's own
        # validation then raises ValidationError on the list type mismatch.
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate("not a dict")  # type: ignore[arg-type]

    def test_empty_dict_valid(self) -> None:
        """An empty dict validates successfully (all fields have defaults)."""
        result = ExtractionResult.model_validate({})
        assert result.decisions == []
        assert result.constraints == []

    def test_strips_null_enforced_by_and_repo(self) -> None:
        """None for enforced_by/repo on nested items is stripped; Pydantic uses defaults."""
        data = {
            "decisions": [
                {"id": "d1", "title": "T", "rationale": "R", "rejected_alternatives": None,
                 "source": "s", "status": "accepted", "created_at": "t", "modified_at": "t",
                 "repo": None}
            ],
            "constraints": [
                {"id": "c1", "description": "D", "enforced_by": None, "bound_entities": None,
                 "source": "s", "status": "active", "created_at": "t", "repo": None}
            ],
        }
        result = ExtractionResult.model_validate(data)
        assert result.decisions[0].repo == ""
        assert result.constraints[0].enforced_by == ""
        assert result.constraints[0].repo == ""
