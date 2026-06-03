"""One-off migration: graph.json + metrics.jsonl → project SQLite database.

Usage:
    python scripts/migrate_to_project_db.py [--project NAME] [--repo PATH]

Migrates .mementic/graph.json and .mementic/metrics.jsonl from
the current (or specified) repo into the project's SQLite database.
Does NOT delete or modify the original JSON files.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# Add project root to path so we can import mementic
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mementic.config import (
    resolve_project,
    save_global_config,
)
from mementic.graph import GraphStore


def migrate_graph_json(graph_path: str, db_path: str) -> int:
    """Migrate a single graph.json file into the project database.

    Args:
        graph_path: Path to .mementic/graph.json.
        db_path: Path to the project SQLite database.

    Returns:
        Number of nodes migrated.
    """
    graph_file = Path(graph_path)
    if not graph_file.exists():
        print(f"  Skipping: {graph_path} not found.")
        return 0

    # Load JSON into a temporary in-memory GraphStore
    # GraphStore.load() handles JSON parsing and validation
    in_memory = GraphStore.load(str(graph_file))

    # Open project database
    db = GraphStore(db_path=db_path)

    # Migrate nodes
    node_count = 0
    for node_id, node_type, payload in in_memory.iter_nodes():
        if node_type == "Decision":
            from mementic.models import Decision
            d = Decision(**payload)
            db.add_decision(d)
            node_count += 1
        elif node_type == "Constraint":
            from mementic.models import Constraint
            c = Constraint(**payload)
            db.add_constraint(c)
            node_count += 1
        elif node_type == "CodeEntity":
            from mementic.models import CodeEntity
            e = CodeEntity(**payload)
            db.add_code_entity(e)
            node_count += 1

    # Migrate edges
    edge_count = 0
    for source, target, relation in in_memory.iter_edges():
        db.link(source, target, relation)
        edge_count += 1

    print(f"  Migrated {node_count} nodes, {edge_count} edges.")
    return node_count


def migrate_metrics_jsonl(metrics_path: str, db_path: str) -> int:
    """Migrate metrics.jsonl entries into the project database extraction_log.

    Args:
        metrics_path: Path to .mementic/metrics.jsonl.
        db_path: Path to the project SQLite database.

    Returns:
        Number of entries migrated.
    """
    metrics_file = Path(metrics_path)
    if not metrics_file.exists():
        print(f"  Skipping: {metrics_path} not found.")
        return 0

    conn = sqlite3.connect(db_path)
    count = 0

    try:
        with open(metrics_file, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                conn.execute(
                    """INSERT INTO extraction_log
                       (operation, source_label, input_tokens, output_tokens,
                        total_tokens, model, status, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.get("operation", "unknown"),
                        entry.get("source_label", ""),
                        entry.get("input_tokens", 0),
                        entry.get("output_tokens", 0),
                        entry.get("total_tokens", 0),
                        entry.get("model", ""),
                        entry.get("status", "success"),
                        entry.get("timestamp", ""),
                    ),
                )
                count += 1

        conn.commit()
    finally:
        conn.close()

    print(f"  Migrated {count} metrics entries.")
    return count


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate graph.json + metrics.jsonl to project SQLite database"
    )
    parser.add_argument(
        "--project", "-p",
        help="Target project name (auto-resolved if omitted)",
    )
    parser.add_argument(
        "--repo", "-r",
        default=".",
        help="Path to repo containing .mementic/ (default: current directory)",
    )
    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()
    if not repo_path.is_dir():
        print(f"Error: {repo_path} is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Resolve project
    try:
        proj_name, global_cfg = resolve_project(args.project)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    proj_entry = global_cfg.projects[proj_name]
    db_path = str(Path(proj_entry.db_path).expanduser())

    print(f"Project: {proj_name}")
    print(f"Database: {db_path}")
    print(f"Repo: {repo_path}")
    print()

    # Register repo in project config if not already present
    repo_str = str(repo_path)
    if repo_str not in proj_entry.repos:
        from mementic.config import RepoEntry
        proj_entry.repos[repo_str] = RepoEntry(path=repo_str, label=repo_path.name)
        save_global_config(global_cfg)
        print(f"Registered repo '{repo_path.name}' in project '{proj_name}'.")
    else:
        print(f"Repo '{repo_path.name}' already registered.")

    print()

    graph_json = repo_path / ".mementic" / "graph.json"
    metrics_jsonl = repo_path / ".mementic" / "metrics.jsonl"

    print("Migrating graph...")
    nodes = migrate_graph_json(str(graph_json), db_path)

    print("Migrating metrics...")
    migrate_metrics_jsonl(str(metrics_jsonl), db_path)

    print()
    print(f"Migration complete — {nodes} nodes migrated to project '{proj_name}'.")
    print("Original .mementic/ files were NOT modified or deleted.")


if __name__ == "__main__":
    main()
