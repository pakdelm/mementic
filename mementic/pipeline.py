from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mementic.config import LLMConfig
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
    repo_name = resolve_repo_name(repo_path) if repo_path else ""
    for d in result.decisions:
        d.repo = repo_name
        _add_decision_dedup(graph, d)
        for entity_path in result.affected_entities:
            eid = _entity_id(repo_path, entity_path)
            graph.add_code_entity(
                CodeEntity(id=eid, type=EntityType.FILE, repo=repo_name)
            )
            graph.link(d.id, eid, "affects")
    for c in result.constraints:
        c.repo = repo_name
        if _add_constraint_dedup(graph, c):
            _ensure_code_entities(graph, c, source_path, result, repo_path=repo_path)
    for edge in result.edges_to_create:
        graph.link(edge.source, edge.target, edge.relation)


def _ensure_code_entities(
    graph: GraphStore, constraint: Any, source_path: str, result: Any,
    repo_path: str | None = None,
) -> None:
    """Ensure CodeEntity nodes exist for bound entities, falling back to source path."""
    repo_name = resolve_repo_name(repo_path) if repo_path else ""
    entities = list(constraint.bound_entities)
    if not entities:
        entities = find_file_paths(constraint.description)
    if not entities:
        entities = [source_path]
    for entity_path in entities:
        eid = _entity_id(repo_path, entity_path)
        graph.add_code_entity(
            CodeEntity(id=eid, type=EntityType.FILE, repo=repo_name)
        )
        graph.link(constraint.id, eid, "binds_to")
    for entity_path in result.affected_entities:
        eid = _entity_id(repo_path, entity_path)
        graph.add_code_entity(
            CodeEntity(id=eid, type=EntityType.FILE, repo=repo_name)
        )
        graph.link(constraint.id, eid, "binds_to")


def _backfill_edges(graph: GraphStore, repo_path: str | None = None) -> int:
    """Ensure all constraints have entity links. Returns number of constraints fixed."""
    repo_name = resolve_repo_name(repo_path) if repo_path else ""
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
            repo_path=repo_name,
        )
        count += 1
    return count


def _backfill_decision_edges(
    graph: GraphStore, repo_path: str | None = None
) -> int:
    """Ensure all decisions have entity links. Returns number of decisions fixed."""
    repo_name = resolve_repo_name(repo_path) if repo_path else ""
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
                CodeEntity(id=eid, type=EntityType.FILE, repo=repo_name)
            )
            graph.link(node_id, eid, "affects")
        count += 1
    return count


def _should_use_llm(
    llm_cfg: LLMConfig, force_deterministic: bool = False
) -> bool:
    """Check if LLM extraction should be attempted.

    Returns True when an API key is available AND deterministic mode has not
    been forced (e.g. by ``run_init`` when ``deep=False``).

    Args:
        llm_cfg: The LLM configuration.
        force_deterministic: If True, forces deterministic extraction regardless
            of API key availability.

    Returns:
        True if LLM extraction should be used.
    """
    if force_deterministic:
        return False
    return llm_cfg.get_api_key() is not None


def _extract_markdown(
    content: str, source_label: str, llm_cfg: LLMConfig,
    force_deterministic: bool = False,
) -> ExtractionResult:
    """Dispatch markdown extraction by mode. Falls back to deterministic on LLM failure."""
    if not _should_use_llm(llm_cfg, force_deterministic):
        return extract_from_markdown(content, source_label)
    try:
        return extract_from_freeform(content, source_label, llm_cfg)
    except Exception:
        sys.stderr.write("LLM unavailable, falling back to deterministic.\n")
        sys.stderr.flush()
        return extract_from_markdown(content, source_label)


def _extract_consequences(
    consequences_text: str,
    source_label: str,
    decision_id: str,
    llm_cfg: LLMConfig,
    force_deterministic: bool = False,
) -> list[Constraint]:
    """Dispatch ADR consequences extraction by mode."""
    if not _should_use_llm(llm_cfg, force_deterministic):
        return extract_constraints_from_consequences(
            consequences_text, source_label, decision_id
        )
    try:
        return extract_from_adr_consequences(consequences_text, llm_cfg)
    except Exception:
        sys.stderr.write("LLM unavailable, falling back to deterministic.\n")
        return extract_constraints_from_consequences(
            consequences_text, source_label, decision_id
        )


def _extract_session(
    session_log: str,
    git_diff: str,
    user_notes: str,
    llm_cfg: LLMConfig,
    max_messages: int = 50,
    custom_keywords: list[str] | None = None,
) -> ExtractionResult:
    """Dispatch session extraction by mode."""
    if not _should_use_llm(llm_cfg):
        return extract_from_session_deterministic(
            session_log, git_diff, user_notes, max_messages, custom_keywords
        )
    try:
        return extract_from_session(
            session_log,
            git_diff,
            user_notes,
            llm_cfg,
            max_messages=max_messages,
            custom_keywords=custom_keywords,
        )
    except Exception:
        sys.stderr.write("LLM unavailable, falling back to deterministic.\n")
        return extract_from_session_deterministic(
            session_log, git_diff, user_notes, max_messages, custom_keywords
        )


def _run_codebase_scan(
    graph: GraphStore, repo_path: str, llm_cfg: LLMConfig, no_scan: bool,
    force_deterministic: bool = False,
    advance: Callable[[str, int], None] | None = None,
) -> None:
    """Phase 1: scan codebase structure for architectural patterns (Spec 12a).

    Only runs when an LLM is available — there is no deterministic equivalent.
    """
    if no_scan or not _should_use_llm(llm_cfg, force_deterministic):
        return

    if advance:
        advance("Scanning codebase structure...", 1)

    summary = scan_codebase_structure(repo_path)
    if not summary.strip():
        return

    sys.stderr.write("Codebase structural scan complete. Analyzing with LLM...\n")
    sys.stderr.flush()

    if advance:
        advance("Analyzing structure with LLM...", 1)

    t0 = time.monotonic()
    result = extract_from_codebase_summary(summary, llm_cfg)
    elapsed = time.monotonic() - t0

    if result.decisions:
        sys.stderr.write(
            f"LLM analysis completed in {elapsed:.1f}s — "
            f"{len(result.decisions)} potential decision(s) detected.\n"
        )
        sys.stderr.flush()
    _ingest_result(graph, result, source_path="init", repo_path=repo_path)


# Characters per batch — ~10K tokens at 4 chars/token, well below max_tokens=16384.
_MD_BATCH_CHARS = 40_000


def _build_md_batches(
    md_files: list[tuple[str, str]], max_chars: int
) -> list[list[tuple[str, str]]]:
    """Split markdown files into batches that fit within ``max_chars``.

    Each batch contains complete files — a file is never split across batches.
    """
    batches: list[list[tuple[str, str]]] = []
    current_batch: list[tuple[str, str]] = []
    current_chars = 0

    for relpath, content in md_files:
        file_chars = len(relpath) + len(content) + 20  # padding for "### File: ..."
        if current_batch and current_chars + file_chars > max_chars:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0
        current_batch.append((relpath, content))
        current_chars += file_chars

    if current_batch:
        batches.append(current_batch)
    return batches


def _run_markdown_scan(
    graph: GraphStore, repo_path: str, llm_cfg: LLMConfig,
    force_deterministic: bool = False,
    advance: Callable[[str, int], None] | None = None,
) -> None:
    """Phase 0: scan repo-wide markdown files for decisions (Spec 15).

    In LLM mode, files are batched into token-budget groups — one API call
    per batch instead of one per file.  Deterministic mode processes per-file
    since there is no API cost.
    """
    md_files = collect_markdown_files(repo_path)
    if not md_files:
        return

    if not _should_use_llm(llm_cfg, force_deterministic):
        total = len(md_files)
        for idx, (md_relpath, md_content) in enumerate(md_files, 1):
            result = _extract_markdown(
                md_content, md_relpath, llm_cfg, force_deterministic,
            )
            _ingest_result(graph, result, source_path=md_relpath, repo_path=repo_path)
            if advance:
                advance(f"Markdown file {idx}/{total}", 1)
        return

    # LLM mode: batch files to reduce API calls
    batches = _build_md_batches(md_files, _MD_BATCH_CHARS)
    sys.stderr.write(
        f"Markdown scan: processing {len(md_files)} file(s) "
        f"in {len(batches)} batch(es)...\n"
    )
    sys.stderr.flush()
    t0 = time.monotonic()
    for batch_idx, batch in enumerate(batches, 1):
        if advance:
            advance(f"Markdown batch {batch_idx}/{len(batches)}", 1)
        batch_content = "\n\n".join(
            f"### File: {relpath}\n{content}" for relpath, content in batch
        )
        result = extract_from_freeform(
            batch_content, f"markdown-scan-batch-{batch_idx}", llm_cfg
        )
        _ingest_result(
            graph, result, source_path="markdown-scan", repo_path=repo_path
        )

    elapsed = time.monotonic() - t0
    sys.stderr.write(
        f"Markdown scan: processed {len(md_files)} file(s) in {elapsed:.1f}s\n"
    )
    sys.stderr.flush()


def _ingest_adr_source(
    graph: GraphStore, adr_dir_path: str, llm_cfg: LLMConfig,
    force_deterministic: bool = False, repo_path: str | None = None,
) -> None:
    """Process a single ADR source directory.

    Creates ``Decision`` nodes from ADR files, extracts constraints from
    consequences sections, and links file-path ``CodeEntity`` nodes.
    """
    if not Path(adr_dir_path).is_dir():
        sys.stderr.write(f"Warning: Source '{adr_dir_path}' not found. Skipping.\n")
        sys.stderr.flush()
        return

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
            constraints = _extract_consequences(
                adr.consequences, adr.path, decision_id,
                llm_cfg, force_deterministic,
            )
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


def _ingest_instruction_source(
    graph: GraphStore, source_path: str, llm_cfg: LLMConfig,
    force_deterministic: bool = False, repo_path: str | None = None,
) -> None:
    """Process a single instruction source directory.

    Used for opencode, claude, cursor, and windsurf instruction dirs.
    """
    if not Path(source_path).is_dir():
        sys.stderr.write(f"Warning: Source '{source_path}' not found. Skipping.\n")
        sys.stderr.flush()
        return

    for instr in scan_opencode_instructions(source_path):
        result = _extract_markdown(
            instr.content, instr.path, llm_cfg, force_deterministic,
        )
        _ingest_result(graph, result, instr.path, repo_path=repo_path)


def run_init(
    repo_path: str | None = None,
    no_scan: bool = False,
    db_path: str | None = None,
    llm_cfg: LLMConfig | None = None,
    advance: Callable[[str, int], None] | None = None,
    deep: bool = False,
) -> GraphStore:
    """Initialize the knowledge graph from all configured sources.

    By default, extraction runs deterministically (regex-based, near-instant).
    Pass ``deep=True`` to enable full LLM-powered extraction for maximum
    quality seed decisions (requires an API key).

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
        llm_cfg: Optional LLM configuration for LLM-powered extraction phases.
            If not provided, LLM phases are skipped.
        deep: If True, use LLM extraction for all phases (requires API key).
            Default is deterministic (fast, no API calls).

    Returns:
        The initialized GraphStore.
    """
    force_deterministic = not deep

    if db_path:
        db_path = str(Path(db_path).expanduser())

    graph = GraphStore(db_path=db_path) if db_path else GraphStore()

    # Batch all ingestion into a single SQLite transaction — without this,
    # every add_decision / add_constraint / add_code_entity / link call
    # triggers a full DELETE + re-INSERT of the entire graph to SQLite,
    # making init O(n^2) in the number of extracted items (~13 min for
    # 33 files on this project).
    with graph.batch():
        # Phase 1: Codebase structural scan (Spec 12a) — cold-start killer.
        if repo_path and llm_cfg:
            _run_codebase_scan(
                graph, repo_path, llm_cfg, no_scan,
                force_deterministic=force_deterministic, advance=advance,
            )

        # Phase 0: Repo-wide markdown scan (Spec 15) — deterministic-friendly.
        if repo_path and llm_cfg:
            _run_markdown_scan(
                graph, repo_path, llm_cfg,
                force_deterministic=force_deterministic, advance=advance,
            )

        # ADR sources (when llm_cfg is available)
        if llm_cfg:
            adr_source: Any
            for adr_source in ():  # sources configured externally via Spec 22
                if advance:
                    advance("Ingesting ADR sources...", 1)
                _ingest_adr_source(
                    graph, adr_source.path, llm_cfg,
                    force_deterministic=force_deterministic, repo_path=repo_path,
                )

            # Instruction sources
            oc_src: Any
            for oc_src in ():  # sources configured externally via Spec 22
                if advance:
                    advance("Ingesting OpenCode instructions...", 1)
                _ingest_instruction_source(
                    graph, oc_src.path, llm_cfg,
                    force_deterministic=force_deterministic, repo_path=repo_path,
                )
            cl_src: Any
            for cl_src in ():  # sources configured externally via Spec 22
                if advance:
                    advance("Ingesting Claude instructions...", 1)
                _ingest_instruction_source(
                    graph, cl_src.path, llm_cfg,
                    force_deterministic=force_deterministic, repo_path=repo_path,
                )
            cu_src: Any
            for cu_src in ():  # sources configured externally via Spec 22
                if advance:
                    advance("Ingesting Cursor instructions...", 1)
                _ingest_instruction_source(
                    graph, cu_src.path, llm_cfg,
                    force_deterministic=force_deterministic, repo_path=repo_path,
                )
            ws_src: Any
            for ws_src in ():  # sources configured externally via Spec 22
                if advance:
                    advance("Ingesting Windsurf instructions...", 1)
                _ingest_instruction_source(
                    graph, ws_src.path, llm_cfg,
                    force_deterministic=force_deterministic, repo_path=repo_path,
                )

        # Backfill edges + persist
        has_sources = (
            repo_path is not None and repo_path and not no_scan
        )
        if has_sources:
            if advance:
                advance("Backfilling entity edges...", 1)
            _backfill_edges(graph, repo_path=repo_path)
            _backfill_decision_edges(graph, repo_path=repo_path)

    # Track repo in project_sources for multi-repo awareness (outside batch
    # since it writes directly to _conn, not through GraphStore mutation methods).
    if db_path and repo_path and graph._conn is not None:
        if advance:
            advance("Tracking repo...", 1)
        graph._conn.execute(
            "INSERT OR REPLACE INTO project_sources (repo, ingested_at) "
            "VALUES (?, ?)",
            (resolve_repo_name(repo_path), datetime.now(timezone.utc).isoformat()),
        )
        graph._conn.commit()

    return graph
