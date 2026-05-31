from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    GraphPayload,
)


class GraphStore:
    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()

    def add_decision(self, d: Decision) -> None:
        self._g.add_node(d.id, node_type="Decision", payload=d.model_dump())

    def add_constraint(self, c: Constraint) -> None:
        self._g.add_node(c.id, node_type="Constraint", payload=c.model_dump())

    def add_code_entity(self, e: CodeEntity) -> None:
        self._g.add_node(e.id, node_type="CodeEntity", payload=e.model_dump())

    def link(self, source_id: str, target_id: str, relation: str) -> None:
        self._g.add_edge(source_id, target_id, relation=relation)

    def get_constraints_for_entities(self, paths: list[str]) -> list[Constraint]:
        result: list[Constraint] = []
        for _node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "Constraint":
                continue
            bound_entities: list[str] = attrs["payload"].get("bound_entities", [])
            if any(
                any(be == p or be.startswith(p) or p.startswith(be) for p in paths)
                for be in bound_entities
            ):
                result.append(Constraint(**attrs["payload"]))
        return result

    def get_decisions_for_entities(self, paths: list[str]) -> list[Decision]:
        decisions: dict[str, Decision] = {}

        constraints = self.get_constraints_for_entities(paths)
        for c in constraints:
            for pred in self._g.predecessors(c.id):
                attrs = self._g.nodes[pred]
                if attrs.get("node_type") == "Decision":
                    decisions[pred] = Decision(**attrs["payload"])

        for node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "CodeEntity":
                continue
            if not any(node_id == p or node_id.startswith(p) for p in paths):
                continue
            for pred in self._g.predecessors(node_id):
                pred_attrs = self._g.nodes[pred]
                if pred_attrs.get("node_type") == "Decision":
                    decisions[pred] = Decision(**pred_attrs["payload"])

        return list(decisions.values())

    def get_evolution(self, entity_path: str) -> list[Decision]:
        decisions: dict[str, Decision] = {}

        constraints = self.get_constraints_for_entities([entity_path])
        for c in constraints:
            for pred in self._g.predecessors(c.id):
                attrs = self._g.nodes[pred]
                if attrs.get("node_type") == "Decision":
                    decisions[pred] = Decision(**attrs["payload"])

        for node_id, attrs in self._g.nodes(data=True):
            if attrs.get("node_type") != "CodeEntity":
                continue
            if node_id == entity_path or node_id.startswith(entity_path):
                for pred in self._g.predecessors(node_id):
                    pred_attrs = self._g.nodes[pred]
                    if pred_attrs.get("node_type") == "Decision":
                        decisions[pred] = Decision(**pred_attrs["payload"])

        return list(decisions.values())

    def get_all_decisions(self) -> list[Decision]:
        return [
            Decision(**attrs["payload"])
            for _node_id, attrs in self._g.nodes(data=True)
            if attrs.get("node_type") == "Decision"
        ]

    def get_all_constraints(self) -> list[Constraint]:
        return [
            Constraint(**attrs["payload"])
            for _node_id, attrs in self._g.nodes(data=True)
            if attrs.get("node_type") == "Constraint"
        ]

    def get_pending_decisions(self) -> list[Decision]:
        return [
            Decision(**attrs["payload"])
            for _node_id, attrs in self._g.nodes(data=True)
            if attrs.get("node_type") == "Decision"
            and attrs["payload"].get("status") == "proposed"
        ]

    def get_pending_constraints(self) -> list[Constraint]:
        return [
            Constraint(**attrs["payload"])
            for _node_id, attrs in self._g.nodes(data=True)
            if attrs.get("node_type") == "Constraint"
            and attrs["payload"].get("status") == "proposed"
        ]

    def save(self, path: str) -> None:
        nodes_payload: list[dict[str, Any]] = []
        for node_id, attrs in self._g.nodes(data=True):
            nodes_payload.append({
                "id": node_id,
                "type": attrs.get("node_type", ""),
                "data": attrs.get("payload", {}),
            })

        edges_payload: list[dict[str, str]] = []
        for u, v, attrs in self._g.edges(data=True):
            edges_payload.append({
                "source": u,
                "target": v,
                "relation": attrs.get("relation", ""),
            })

        payload = GraphPayload(nodes=nodes_payload, edges=edges_payload)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload.model_dump(mode="json"), indent=2))
        tmp.replace(p)

        audit_path = p.parent / "audit.jsonl"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "save",
            "decisions": sum(
                1 for _, a in self._g.nodes(data=True) if a.get("node_type") == "Decision"
            ),
            "constraints": sum(
                1 for _, a in self._g.nodes(data=True) if a.get("node_type") == "Constraint"
            ),
            "entities": sum(
                1 for _, a in self._g.nodes(data=True) if a.get("node_type") == "CodeEntity"
            ),
            "edges": self._g.number_of_edges(),
        }
        with audit_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    @classmethod
    def load(cls, path: str) -> GraphStore:
        data = json.loads(Path(path).read_text())
        payload = GraphPayload(**data)
        gs = cls()
        for node in payload.nodes:
            node_data: dict[str, Any] = dict(node)
            gs._g.add_node(
                node_data["id"],
                node_type=node_data.get("type", ""),
                payload=node_data.get("data", {}),
            )
        for edge in payload.edges:
            edge_data: dict[str, str] = dict(edge)
            gs._g.add_edge(
                edge_data["source"],
                edge_data["target"],
                relation=edge_data.get("relation", ""),
            )
        return gs

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    def get_node_payload(self, node_id: str) -> dict[str, Any]:
        return dict(self._g.nodes[node_id].get("payload", {}))

    def set_node_payload(self, node_id: str, payload: dict[str, Any]) -> None:
        self._g.nodes[node_id]["payload"] = payload

    def iter_nodes(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Return list of (node_id, node_type, payload) for all nodes."""
        return [
            (nid, attrs.get("node_type", ""), attrs.get("payload", {}))
            for nid, attrs in self._g.nodes(data=True)
        ]

    def has_outgoing_edges(self, node_id: str) -> bool:
        try:
            next(self._g.successors(node_id))
            return True
        except StopIteration:
            return False

    def stats(self) -> dict[str, int]:
        decisions = 0
        constraints = 0
        entities = 0
        for _node_id, attrs in self._g.nodes(data=True):
            nt = attrs.get("node_type", "")
            if nt == "Decision":
                decisions += 1
            elif nt == "Constraint":
                constraints += 1
            elif nt == "CodeEntity":
                entities += 1
        return {
            "decisions": decisions,
            "constraints": constraints,
            "entities": entities,
            "edges": self._g.number_of_edges(),
        }
