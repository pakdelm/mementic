"""Graph linting — deterministic rules for graph health checks.

Seven rules, zero API cost:
- orphaned-constraint: Constraint with no enforcing decision.
- unlinked-decision: Decision with no affected entities.
- stale-entity: Entity referencing a deleted file.
- duplicate-slug: Two decisions with same ID.
- status-inconsistency: Deprecated decision with active constraints.
- orphaned-entity: Entity with no incoming edges.
- untagged-decision: Decision with no tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mementic.graph import GraphStore
from mementic.models import Constraint


Severity = Literal["error", "warning", "info"]


@dataclass
class LintFinding:
    """A single lint issue found in the graph."""

    rule: str
    severity: Severity
    message: str
    node_id: str = ""
    related_id: str = ""


@dataclass
class LintResult:
    """Collection of lint findings with statistics."""

    findings: list[LintFinding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")


def _get_decision_node_ids(graph: GraphStore) -> set[str]:
    """Get all decision node IDs."""
    return {
        nid
        for nid, ntype, _ in graph.iter_nodes()
        if ntype == "Decision"
    }


def _get_constraint_nodes(graph: GraphStore) -> list[tuple[str, Constraint]]:
    """Get all constraint nodes as (node_id, Constraint) pairs."""
    result: list[tuple[str, Constraint]] = []
    for nid, ntype, payload in graph.iter_nodes():
        if ntype == "Constraint":
            result.append((nid, Constraint(**payload)))
    return result


def _get_entity_ids(graph: GraphStore) -> set[str]:
    """Get all code entity node IDs."""
    return {
        nid
        for nid, ntype, _ in graph.iter_nodes()
        if ntype == "CodeEntity"
    }


def lint(graph: GraphStore) -> LintResult:
    """Run all lint rules against the given graph.

    Args:
        graph: The knowledge graph to lint.

    Returns:
        LintResult with all findings.
    """
    findings: list[LintFinding] = []

    # ── Rule: orphaned-constraint (warning) ──
    # Constraint with no enforcing decision
    decision_ids = _get_decision_node_ids(graph)
    for cid, constraint in _get_constraint_nodes(graph):
        if constraint.enforced_by and constraint.enforced_by not in decision_ids:
            findings.append(LintFinding(
                rule="orphaned-constraint",
                severity="warning",
                message=(
                    f"Constraint '{cid}' references enforcing decision "
                    f"'{constraint.enforced_by}' which does not exist"
                ),
                node_id=cid,
                related_id=constraint.enforced_by,
            ))

    # ── Rule: unlinked-decision (warning) ──
    # Decision with no affected entities (no outgoing edges)
    for nid in decision_ids:
        if not graph.has_outgoing_edges(nid):
            payload = graph.get_node_payload(nid)
            findings.append(LintFinding(
                rule="unlinked-decision",
                severity="warning",
                message=f"Decision '{nid}' has no outgoing edges (no affected entities)",
                node_id=nid,
            ))

    # ── Rule: stale-entity (info) ──
    # Entity referencing a deleted file
    entity_ids = _get_entity_ids(graph)
    for eid in entity_ids:
        # Only check FILE-type entities (skip CLASS, FUNCTION, MODULE)
        payload = graph.get_node_payload(eid)
        if payload.get("type") == "file" and not Path(eid).exists():
            findings.append(LintFinding(
                rule="stale-entity",
                severity="info",
                message=f"Entity '{eid}' references a file that no longer exists",
                node_id=eid,
            ))

    # ── Rule: duplicate-slug (error) ──
    # Two decisions with same ID
    # This is caught by idempotent add; check all decisions for duplicates
    seen_ids: dict[str, str] = {}
    for nid, ntype, payload in graph.iter_nodes():
        if ntype != "Decision":
            continue
        if nid in seen_ids:
            findings.append(LintFinding(
                rule="duplicate-slug",
                severity="error",
                message=f"Duplicate decision ID '{nid}'",
                node_id=nid,
                related_id=seen_ids[nid],
            ))
        else:
            seen_ids[nid] = nid

    # ── Rule: status-inconsistency (warning) ──
    # Deprecated decision with active constraints
    for nid, ntype, payload in graph.iter_nodes():
        if ntype != "Decision":
            continue
        status = payload.get("status", "")
        if status != "deprecated":
            continue
        # Check for active constraints that reference this decision via enforced_by
        for cid, constraint in _get_constraint_nodes(graph):
            if constraint.enforced_by == nid and constraint.status == "active":
                findings.append(LintFinding(
                    rule="status-inconsistency",
                    severity="warning",
                    message=(
                        f"Deprecated decision '{nid}' still has active constraint "
                        f"'{cid}'"
                    ),
                    node_id=nid,
                    related_id=cid,
                ))

    # ── Rule: orphaned-entity (info) ──
    # Entity with no incoming edges
    for eid in entity_ids:
        has_incoming = False
        for _, target, _ in graph.iter_edges():
            if target == eid:
                has_incoming = True
                break
        if not has_incoming:
            findings.append(LintFinding(
                rule="orphaned-entity",
                severity="info",
                message=f"Entity '{eid}' has no incoming edges",
                node_id=eid,
            ))

    # ── Rule: untagged-decision (info) ──
    # Decision with no tags
    for nid, ntype, payload in graph.iter_nodes():
        if ntype != "Decision":
            continue
        tags = payload.get("tags", [])
        if not tags:
            findings.append(LintFinding(
                rule="untagged-decision",
                severity="info",
                message=f"Decision '{nid}' has no tags",
                node_id=nid,
            ))

    return LintResult(findings=findings)


def lint_fix(graph: GraphStore, result: LintResult) -> int:
    """Auto-fix resolvable lint issues in-place.

    Args:
        graph: The knowledge graph to fix (mutated in-place).
        result: Lint findings from a prior lint() call.

    Returns:
        Number of findings resolved.
    """
    resolved = 0

    for finding in result.findings:
        if finding.rule == "stale-entity":
            # Remove stale entity nodes (NetworkX removal)
            if finding.node_id in graph._g:
                graph._g.remove_node(finding.node_id)
                resolved += 1

        elif finding.rule == "status-inconsistency":
            # Cascade: if decision is deprecated, deprecate its active constraints too
            succ_payload = graph.get_node_payload(finding.related_id)
            if succ_payload.get("status") == "active":
                succ_payload["status"] = "deprecated"
                graph.set_node_payload(finding.related_id, succ_payload)
                resolved += 1

        elif finding.rule == "orphaned-entity":
            # Remove orphaned entity nodes
            if finding.node_id in graph._g:
                graph._g.remove_node(finding.node_id)
                resolved += 1

    return resolved
