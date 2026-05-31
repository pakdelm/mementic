"""Data models for the Mementic knowledge graph."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
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
    timestamp: str
    tags: list[str] = Field(default_factory=list)


class Constraint(BaseModel):
    id: str
    description: str
    bound_entities: list[str] = Field(default_factory=list)
    enforced_by: str = ""
    source: str
    status: str = "active"
    timestamp: str


class CodeEntity(BaseModel):
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
    """One row in the metrics log, representing a single mementic operation.

    Benchmark fields (baseline_tokens, context_pack_tokens, savings_ratio) are
    only populated for 'enforce_benchmark' entries.
    """

    timestamp: str
    operation: str  # init_freeform / init_adr / capture / enforce_benchmark / mcp_context_pack
    source_label: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    status: str = "success"
    baseline_tokens: int | None = None
    context_pack_tokens: int | None = None
    savings_ratio: float | None = None
