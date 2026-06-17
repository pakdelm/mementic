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
    author: str = "unknown"
    created_at: str
    modified_at: str
    repo: str = ""
    affected_entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


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

    The `id` field uses the namespaced format:
        {normalized_repo_path}::{relative_file_path}
    e.g., /home/me/dev/auth-service::src/config.py
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


class UsageStats(BaseModel):
    """Raw token counts from a single LLM API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    status: str = "success"


class MetricsEntry(BaseModel):
    """One row in the metrics log, representing a single mementic operation."""

    timestamp: str
    operation: str  # init_freeform / init_adr / capture / mcp_context_pack
    source_label: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    status: str = "success"
