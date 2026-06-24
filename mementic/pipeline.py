from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mementic.config import MementicConfig, load_config, load_global_config
from mementic.extraction import (
    extract_constraints_from_consequences,
    extract_from_adr_consequences,
    extract_from_codebase_summary,
    extract_from_freeform,
    extract_from_markdown,
    extract_from_session,
    extract_from_session_deterministic,
)
from mementic.format import DECISION_ID_MAX_LENGTH, find_file_paths, resolve_repo_name, slugify
from mementic.graph import GraphStore
from mementic.models import (
    CodeEntity,
    Constraint,
    Decision,
    DecisionStatus,
    EntityType,
    ExtractionResult,
    UsageStats,
)
from mementic.sources.adr import scan_adr_dir
from mementic.sources.codebase import collect_markdown_files, scan_codebase_structure
from mementic.sources.opencode import scan_opencode_instructions


def _entity_id(repo_path: str | None, relative_path: str) -> str:
    """Normalize a file-path entity reference.

    Strips trailing ``:function`` / ``::function`` suffixes (common when
    LLMs or agents reference specific symbols instead of files). The repo
    namespace is carried by the ``repo`` column, not embedded in the ID.
    """
    clean = re.sub(r"::[A-Za-z_]\w*$", "", relative_path)
    clean = re.sub(r":[A-Za-z_]\w*$", "", clean)
    return clean


def _status_from_adr(status: str) -> DecisionStatus:
    s = status.strip().lower()
    if s in ("deprecated", "superseded"):
        return DecisionStatus.DEPRECATED
    return DecisionStatus.ACCEPTED


def _add_decision_dedup(graph: GraphStore, decision: Decision) -> bool:
    """Add decision if not duplicate. Merge sources on duplicate. Returns True if added."""
    if graph.has_node(decision.id):
        payload = graph.get_node_payload(decision.id)
        existing_source = payload.get("source", "")
        if decision.source not in existing_source:
            payload["source"] = f"{existing_source}, {decision.source}"
            graph.set_node_payload(decision.id, payload)
        return False
    graph.add_decision(decision)
    return True


def _add_constraint_dedup(graph: GraphStore, constraint: Constraint) -> bool:
    if graph.has_node(constraint.id):
        return False
    graph.add_constraint(constraint)
    return True


def _ingest_result(
    graph: GraphStore, result: ExtractionResult, source_path: str,
    repo_path: str | None = None,
) -> None:
    """Add extracted decisions, constraints, and entities to the graph."""
    for d in result.decisions:
        d.repo = resolve_repo_name(repo_path) if repo_path else ""
        _add_decision_dedup(graph, d)
        for entity_path in result.affected_entities:
            eid = _entity_id(repo_path, entity_path)
            graph.add_code_entity(
                CodeEntity(id=eid, type=EntityType.FILE, repo=resolve_repo_name(repo_path) if repo_path else "")
            )
            graph.link(d.id, eid, "affects")
    for c in result.constraints:
        c.repo = resolve_repo_name(repo_path) if repo_path else ""
        if _add_constraint_dedup(graph, c):
            _ensure_code_entities(graph, c, source_path, result, repo_path=repo_path)
    for edge in result.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)


def _ensure_code_entities(
    graph: GraphStore, constraint: Any, source_path: str, result: Any,
    repo_path: str | None = None,
) -> None:
    """Ensure CodeEntity nodes exist for bound entities, falling back to source path."""
    entities = list(constraint.bound_entities)
    if not entities:
        entities = find_file_paths(constraint.description)
    if not entities:
        entities = [source_path]
    for entity_path in entities:
        eid = _entity_id(repo_path, entity_path)
        graph.add_code_entity(
            CodeEntity(id=eid, type=EntityType.FILE, repo=resolve_repo_name(repo_path) if repo_path else "")
        )
        graph.link(constraint.id, eid, "binds_to")
    for entity_path in result.affected_entities:
        eid = _entity_id(repo_path, entity_path)
        graph.add_code_entity(
            CodeEntity(id=eid, type=EntityType.FILE, repo=resolve_repo_name(repo_path) if repo_path else "")
        )
        graph.link(constraint.id, eid, "binds_to")


def _backfill_edges(graph: GraphStore, repo_path: str | None = None) -> int:
    """Ensure all constraints have entity links. Returns number of constraints fixed."""
    count = 0
    for node_id, node_type, payload in graph.iter_nodes():
        if node_type != "Constraint":
            continue
        if graph.has_outgoing_edges(node_id):
            continue
        constraint = Constraint(**payload)
        _ensure_code_entities(
            graph, constraint,
            payload.get("source", "unknown"),
            ExtractionResult(),
            repo_path=resolve_repo_name(repo_path) if repo_path else "",
        )
        count += 1
    return count


def _backfill_decision_edges(
    graph: GraphStore, repo_path: str | None = None
) -> int:
    """Ensure all decisions have entity links. Returns number of decisions fixed."""
    count = 0
    for node_id, node_type, payload in graph.iter_nodes():
        if node_type != "Decision":
            continue
        if graph.has_outgoing_edges(node_id):
            continue
        text_to_scan = (
            payload.get("rationale", "")
            + " "
            + payload.get("source", "")
            + " "
            + payload.get("title", "")
        )
        paths = find_file_paths(text_to_scan)
        if not paths:
            paths = ["src/"]
        for entity_path in paths:
            eid = _entity_id(repo_path, entity_path)
            graph.add_code_entity(
                CodeEntity(id=eid, type=EntityType.FILE, repo=resolve_repo_name(repo_path) if repo_path else "")
            )
            graph.link(node_id, eid, "affects")
        count += 1
    return count


def _zero_usage() -> UsageStats:
    """Return a zero-token UsageStats for deterministic extraction calls."""
    return UsageStats(model="deterministic", status="success")


def _should_use_llm(cfg: MementicConfig) -> bool:
    """Check if LLM extraction should be attempted based on mode and key presence."""
    mode = cfg.llm.extraction_mode
    if mode == "deterministic":
        return False
    if mode == "llm":
        return True
    # auto: use LLM only if key is present
    return cfg.llm.get_api_key() is not None


def _extract_markdown(
    content: str, source_label: str, cfg: MementicConfig
) -> tuple[ExtractionResult, UsageStats]:
    """Dispatch markdown extraction by mode. Falls back to deterministic on LLM failure."""
    if not _should_use_llm(cfg):
        return extract_from_markdown(content, source_label), _zero_usage()
    try:
        return extract_from_freeform(content, source_label, cfg.llm)
    except Exception:
        if cfg.llm.extraction_mode == "llm":
            raise
        sys.stderr.write("LLM unavailable, falling back to deterministic.\n")
        return extract_from_markdown(content, source_label), _zero_usage()


def _extract_consequences(
    consequences_text: str,
    source_label: str,
    decision_id: str,
    cfg: MementicConfig,
) -> tuple[list[Constraint], UsageStats]:
    """Dispatch ADR consequences extraction by mode."""
    if not _should_use_llm(cfg):
        return (
            extract_constraints_from_consequences(
                consequences_text, source_label, decision_id
            ),
            _zero_usage(),
        )
    try:
        return extract_from_adr_consequences(consequences_text, cfg.llm)
    except Exception:
        if cfg.llm.extraction_mode == "llm":
            raise
        sys.stderr.write("LLM unavailable, falling back to deterministic.\n")
        return (
            extract_constraints_from_consequences(
                consequences_text, source_label, decision_id
            ),
            _zero_usage(),
        )


def _extract_session(
    session_log: str,
    git_diff: str,
    user_notes: str,
    cfg: MementicConfig,
    max_messages: int = 50,
    custom_keywords: list[str] | None = None,
) -> tuple[ExtractionResult, UsageStats]:
    """Dispatch session extraction by mode."""
    if not _should_use_llm(cfg):
        result = extract_from_session_deterministic(
            session_log, git_diff, user_notes, max_messages, custom_keywords
        )
        return result, _zero_usage()
    try:
        return extract_from_session(
            session_log,
            git_diff,
            user_notes,
            cfg.llm,
            max_messages=max_messages,
            custom_keywords=custom_keywords,
        )
    except Exception:
        if cfg.llm.extraction_mode == "llm":
            raise
        sys.stderr.write("LLM unavailable, falling back to deterministic.\n")
        result = extract_from_session_deterministic(
            session_log, git_diff, user_notes, max_messages, custom_keywords
        )
        return result, _zero_usage()


def _resolve_db_path(repo_path: str | None) -> str | None:
    """Resolve the SQLite db_path from the global config's project entry.

    Searches for a project whose repos contain ``repo_path``.  Falls back
    to the default project's ``db_path`` when no match is found.

    Args:
        repo_path: Absolute path to the repository root.

    Returns:
        The ``db_path`` string, or ``None`` if no project config is found.
    """
    try:
        global_cfg = load_global_config()
    except Exception:
        return None

    if repo_path:
        resolved = Path(repo_path).resolve()
        for proj_entry in global_cfg.projects.values():
            for repo_entry in proj_entry.repos.values():
                try:
                    if Path(repo_entry.path).resolve() == resolved:
                        return proj_entry.db_path
                except (OSError, ValueError):
                    continue

    if global_cfg.default_project:
        try:
            return global_cfg.projects[global_cfg.default_project].db_path
        except KeyError:
            pass

    return None


def _run_codebase_scan(
    graph: GraphStore, repo_path: str, cfg: MementicConfig, no_scan: bool
) -> list[UsageStats]:
    """Phase 1: scan codebase structure for architectural patterns (Spec 12a).

    Only runs when an LLM is available — there is no deterministic equivalent.
    """
    if no_scan or not _should_use_llm(cfg):
        return []

    summary = scan_codebase_structure(repo_path)
    if not summary.strip():
        return []

    result, usage = extract_from_codebase_summary(summary, cfg.llm)
    if result.decisions:
        sys.stderr.write(
            f"Codebase scan: detected {len(result.decisions)} "
            f"potential decision(s) from structural patterns.\n"
        )
    _ingest_result(graph, result, source_path="init", repo_path=repo_path)
    return [usage]


def _run_markdown_scan(
    graph: GraphStore, repo_path: str, cfg: MementicConfig
) -> list[UsageStats]:
    """Phase 0: scan repo-wide markdown files for decisions (Spec 15)."""
    usage_list: list[UsageStats] = []
    for md_relpath, md_content in collect_markdown_files(repo_path):
        result, usage = _extract_markdown(md_content, md_relpath, cfg)
        usage_list.append(usage)
        _ingest_result(graph, result, source_path=md_relpath, repo_path=repo_path)
    return usage_list


def _ingest_adr_source(
    graph: GraphStore, adr_dir_path: str, cfg: MementicConfig, repo_path: str | None
) -> list[UsageStats]:
    """Process a single ADR source directory.

    Creates ``Decision`` nodes from ADR files, extracts constraints from
    consequences sections, and links file-path ``CodeEntity`` nodes.
    """
    if not Path(adr_dir_path).is_dir():
        sys.stderr.write(f"Warning: Source '{adr_dir_path}' not found. Skipping.\n")
        return []

    usage_list: list[UsageStats] = []
    for adr in scan_adr_dir(adr_dir_path):
        decision_id = slugify(adr.title, max_length=DECISION_ID_MAX_LENGTH)
        decision = Decision(
            id=decision_id,
            title=adr.title,
            rationale=f"Context: {adr.context}\n\nDecision: {adr.decision}",
            rejected_alternatives=[],
            source="adr",
            status=_status_from_adr(adr.status),
            created_at=adr.timestamp,
            modified_at=adr.timestamp,
            repo=resolve_repo_name(repo_path) if repo_path else "",
        )
        _add_decision_dedup(graph, decision)

        if adr.consequences.strip():
            constraints, usage = _extract_consequences(
                adr.consequences, adr.path, decision_id, cfg
            )
            usage_list.append(usage)
            for c in constraints:
                c.repo = resolve_repo_name(repo_path) if repo_path else ""
                c.enforced_by = decision_id
                if _add_constraint_dedup(graph, c):
                    _ensure_code_entities(
                        graph, c, adr.path, ExtractionResult(), repo_path=repo_path,
                    )

        for path_ in find_file_paths(adr.decision + " " + adr.consequences):
            eid = _entity_id(repo_path, path_)
            graph.add_code_entity(
                CodeEntity(id=eid, type=EntityType.FILE, repo=resolve_repo_name(repo_path) if repo_path else "")
            )
            graph.link(decision.id, eid, "affects")

    return usage_list


def _ingest_instruction_source(
    graph: GraphStore, source_path: str, cfg: MementicConfig, repo_path: str | None
) -> list[UsageStats]:
    """Process a single instruction source directory.

    Used for opencode, claude, cursor, and windsurf instruction dirs.
    """
    if not Path(source_path).is_dir():
        sys.stderr.write(f"Warning: Source '{source_path}' not found. Skipping.\n")
        return []

    usage_list: list[UsageStats] = []
    for instr in scan_opencode_instructions(source_path):
        result, usage = _extract_markdown(instr.content, instr.path, cfg)
        usage_list.append(usage)
        _ingest_result(graph, result, instr.path, repo_path=repo_path)
    return usage_list


def run_init(
    repo_path: str | None = None,
    no_scan: bool = False,
    db_path: str | None = None,
    project: str | None = None,
    advance: Callable[[str], None] | None = None,
) -> tuple[GraphStore, list[UsageStats]]:
    """Initialize the knowledge graph from all configured sources.

    When ``repo_path`` is provided (or CWD is auto-detected as a codebase),
    a deterministic structural scan runs first to detect architectural
    patterns from the codebase itself. This is skipped when ``no_scan``
    is True, when no codebase indicators are found, or when LLM extraction
    is unavailable (the structural scan has no deterministic equivalent).

    When ``db_path`` is provided, the graph is persisted to SQLite instead
    of the legacy JSON file.  Entity IDs are namespaced with the repo path
    and ``repo`` fields are populated on all decisions, constraints, and
    entities.

    Args:
        repo_path: Optional path to the repository root for codebase scanning.
        no_scan: If True, skip the codebase structural scan.
        db_path: Optional path to a SQLite database for persistent storage.
        project: Optional project name to scope the loaded configuration.

    Returns:
        A tuple of (GraphStore, list[UsageStats]) — usage stats from all
        LLM extraction calls made during init.
    """
    cfg = load_config(repo_path=repo_path, project=project)

    # Resolve db_path from project config when not explicitly provided.
    # Avoids falling back to the deprecated .mementic/graph.json path.
    if db_path is None:
        db_path = _resolve_db_path(repo_path)
    if db_path:
        db_path = str(Path(db_path).expanduser())

    graph = GraphStore(db_path=db_path) if db_path else GraphStore()
    all_usage: list[UsageStats] = []

    # Phase 1: Codebase structural scan (Spec 12a) — cold-start killer.
    if repo_path:
        if advance:
            advance("Scanning codebase structure...")
        all_usage.extend(_run_codebase_scan(graph, repo_path, cfg, no_scan))

    # Phase 0: Repo-wide markdown scan (Spec 15) — deterministic-friendly.
    if repo_path:
        if advance:
            advance("Scanning markdown files...")
        all_usage.extend(_run_markdown_scan(graph, repo_path, cfg))

    # ADR sources
    for adr_source in cfg.sources.adr_dirs:
        if advance:
            advance("Ingesting ADR sources...")
        all_usage.extend(_ingest_adr_source(graph, adr_source.path, cfg, repo_path))

    # Instruction sources
    for oc_src in cfg.sources.opencode_instructions:
        if advance:
            advance("Ingesting OpenCode instructions...")
        all_usage.extend(_ingest_instruction_source(graph, oc_src.path, cfg, repo_path))
    for cl_src in cfg.sources.claude_instructions:
        if advance:
            advance("Ingesting Claude instructions...")
        all_usage.extend(_ingest_instruction_source(graph, cl_src.path, cfg, repo_path))
    for cu_src in cfg.sources.cursor_instructions:
        if advance:
            advance("Ingesting Cursor instructions...")
        all_usage.extend(_ingest_instruction_source(graph, cu_src.path, cfg, repo_path))
    for ws_src in cfg.sources.windsurf_instructions:
        if advance:
            advance("Ingesting Windsurf instructions...")
        all_usage.extend(_ingest_instruction_source(graph, ws_src.path, cfg, repo_path))

    # Backfill edges + persist
    has_sources = bool(
        cfg.sources.adr_dirs
        or cfg.sources.opencode_instructions
        or cfg.sources.claude_instructions
        or cfg.sources.cursor_instructions
        or cfg.sources.windsurf_instructions
    )
    if has_sources or (repo_path and not no_scan):
        if advance:
            advance("Backfilling entity edges...")
        _backfill_edges(graph, repo_path=repo_path)
        _backfill_decision_edges(graph, repo_path=repo_path)
        if db_path is None:
            # Legacy: save to graph.json (last resort when no SQLite config)
            Path(cfg.output.graph_path).parent.mkdir(parents=True, exist_ok=True)
            graph.save(cfg.output.graph_path)

    # Track repo in project_sources for multi-repo awareness
    if db_path and repo_path and graph._conn is not None:
        if advance:
            advance("Tracking repo...")
        graph._conn.execute(
            "INSERT OR REPLACE INTO project_sources (repo, ingested_at) "
            "VALUES (?, ?)",
            (resolve_repo_name(repo_path), datetime.now(timezone.utc).isoformat()),
        )
        graph._conn.commit()

    return graph, all_usage
