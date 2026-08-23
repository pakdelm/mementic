"""Data models for the Mementic knowledge graph."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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

    The ``id`` is a relative file path (e.g., ``src/models.py``).  The ``repo``
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
