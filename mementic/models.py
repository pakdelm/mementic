"""Data models for the Mementic memory store (schema v5).

Decisions and constraints are flat relational rows; file associations ride
along as ``paths`` (materialized from ``entry_paths``). Liveness is derived
from the ``deprecations`` table at query time — there is deliberately no
status field on either model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class Decision:
    """An architectural decision.

    Attributes:
        id: Slug ID (stable reference used by deprecate/history/CLI output).
        title: Short human-readable title.
        rationale: Why the decision was made; input to keyword scoring.
        created_at: ISO 8601 creation timestamp (drives recency ranking).
        paths: File paths this decision affects (from ``entry_paths``).
    """

    id: str
    title: str
    rationale: str
    created_at: str
    paths: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class Constraint:
    """A rule that constrains how code is written.

    Attributes:
        id: Slug ID (stable reference used by deprecate/history/CLI output).
        description: What must (or must not) be done; input to keyword scoring.
        enforced_by: ID of the enforcing decision, or empty for standalone.
        created_at: ISO 8601 creation timestamp (drives recency ranking).
        paths: File paths this constraint binds (from ``entry_paths``).
    """

    id: str
    description: str
    enforced_by: str = ""
    created_at: str
    paths: list[str] = field(default_factory=list)
