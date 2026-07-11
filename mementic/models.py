"""Data models for the Mementic knowledge graph."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class LLMJsonParseError(Exception):
    """Raised when an LLM response cannot be parsed as valid JSON.

    Contains structured metadata for error reporting without exposing raw
    LLM output in logs or error messages.

    Args:
        message: Human-readable error description.
        source: Name of the extraction function that called the parser
            (e.g. ``"extract_from_freeform"``).
        position: Character position where parsing failed, from
            :class:`json.JSONDecodeError`.
    """

    def __init__(self, message: str, source: str, position: int | None = None):
        self.source = source
        self.position = position
        super().__init__(message)


class DecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class EntityType(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    FILE = "file"


class Decision(BaseModel):
    id: str
    title: str
    rationale: str
    rejected_alternatives: list[str] = Field(default_factory=list)
    source: str
    status: DecisionStatus = DecisionStatus.ACCEPTED
    created_at: str
    modified_at: str
    repo: str = ""
    affected_entities: list[str] = Field(default_factory=list)


class Constraint(BaseModel):
    id: str
    description: str
    bound_entities: list[str] = Field(default_factory=list)
    enforced_by: str = ""
    source: str
    status: str = "active"
    created_at: str
    repo: str = ""


class CodeEntity(BaseModel):
    """A code entity within a repository.

    The ``id`` is a relative file path (e.g., ``src/config.py``).  The ``repo``
    column scopes it to a specific repository.  Together ``(repo, id)`` form
    the primary key.
    """

    id: str
    type: EntityType
    repo: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str


class GraphPayload(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, str]]


class ExtractionResult(BaseModel):
    decisions: list[Decision] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    affected_entities: list[str] = Field(default_factory=list)
    edges_to_create: list[GraphEdge] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_inputs(cls, data: Any) -> Any:
        """Coerce common LLM JSON mistakes before validation.

        LLMs frequently produce:
        - ``null`` / ``None`` for list fields instead of ``[]``
        - Single dicts instead of lists (``{"decisions": {...}}`` vs ``[...]``)
        - ``null`` for nested list fields on Decision / Constraint objects
        - String values for list fields (e.g. ``"affected_entities": "path"``)

        This validator normalises these patterns so ``model_validate``
        succeeds on more real-world LLM outputs.  It does **not** fill in
        missing required scalar fields — those remain validation errors.
        """
        if not isinstance(data, dict):
            return data

        # Top-level list coerce: None → [], dict → [dict], string → [string]
        for list_field in (
            "decisions",
            "constraints",
            "affected_entities",
            "edges_to_create",
        ):
            val = data.get(list_field)
            if val is None:
                data[list_field] = []
            elif isinstance(val, dict):
                data[list_field] = [val]
            elif isinstance(val, str):
                data[list_field] = [val]

        # Nested item coerce for items inside decisions/constraints lists
        for nested_list_name in ("decisions", "constraints"):
            items = data.get(nested_list_name)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Fields that are lists but LLMs often set to null or scalar
                _NESTED_LIST_FIELDS = ("rejected_alternatives", "affected_entities", "bound_entities")
                for field_name in _NESTED_LIST_FIELDS:
                    fv = item.get(field_name)
                    if fv is None:
                        item[field_name] = []
                    elif isinstance(fv, str):
                        item[field_name] = [fv]
                # Optional string fields the LLM cannot know — strip null
                # so Pydantic uses the declared default (pipeline fills real values).
                for str_field in ("enforced_by", "repo"):
                    if str_field in item and item[str_field] is None:
                        del item[str_field]

        return data
