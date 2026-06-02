"""Operational metrics storage backed by append-only JSONL."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from mementic.config import MementicConfig
from mementic.format import count_tokens
from mementic.models import MetricsEntry


def _empty_summary(cfg: MementicConfig | None = None) -> dict:
    """Return an empty summary dict with honest zero/default values."""
    raw_src = snapshot_raw_sources(cfg) if cfg is not None else 0
    return {
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "by_operation": {},
        "model": "",
        "status": {},
        "extraction_init_calls": 0,
        "extraction_init_tokens": 0,
        "extraction_capture_calls": 0,
        "extraction_capture_tokens": 0,
        "extraction_total_tokens": 0,
        "context_pack_queries": 0,
        "context_pack_total_tokens": 0,
        "context_pack_avg_tokens": 0.0,
        "context_pack_avg_constraints": 0.0,
        "context_pack_avg_decisions": 0.0,
        "raw_source_tokens": raw_src,
        "size_ratio": None,
        "breakeven_note": "Need benchmark data. Run `mementic context --benchmark`.",
    }


def snapshot_raw_sources(cfg: MementicConfig) -> int:
    """Count tokens in all configured source directories (ADRs + instructions).

    Reads every ``*.md`` file in each configured source directory and sums
    their token counts. Cache-free (called when metrics is displayed).
    Returns 0 if no sources configured or directories missing.

    Args:
        cfg: The MementicConfig instance.

    Returns:
        Total token count across all ``*.md`` source files.
    """
    total = 0
    for adr in cfg.sources.adr_dirs:
        dir_path = Path(adr.path)
        if dir_path.is_dir():
            for f in dir_path.rglob("*.md"):
                if f.is_file():
                    total += count_tokens(f.read_text(encoding="utf-8"))
    for oc_src in cfg.sources.opencode_instructions:
        dir_path = Path(oc_src.path)
        if dir_path.is_dir():
            for f in dir_path.rglob("*.md"):
                if f.is_file():
                    total += count_tokens(f.read_text(encoding="utf-8"))
    for claude_src in cfg.sources.claude_instructions:
        dir_path = Path(claude_src.path)
        if dir_path.is_dir():
            for f in dir_path.rglob("*.md"):
                if f.is_file():
                    total += count_tokens(f.read_text(encoding="utf-8"))
    return total


class UsageStore:
    """Manages append-only JSONL storage for mementic operational metrics.

    The underlying file lives at ``.mementic/metrics.jsonl`` by default.
    """

    def __init__(self, path: str = ".mementic/metrics.jsonl") -> None:
        """Initialize with path to the JSONL file.

        Args:
            path: Filesystem path to the metrics JSONL file.
        """
        self._path = Path(path)

    def append(self, entry: MetricsEntry) -> None:
        """Append a single MetricsEntry as a JSON line.

        Creates the parent directory if it does not exist.

        Args:
            entry: The MetricsEntry instance to persist.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + os.linesep)

    def read_all(self) -> list[MetricsEntry]:
        """Read all entries from the JSONL file.

        Returns:
            List of MetricsEntry instances. Returns an empty list when the file
            does not exist or is unreadable.
        """
        if not self._path.exists():
            return []
        entries: list[MetricsEntry] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                entries.append(MetricsEntry.model_validate(json.loads(stripped)))
        return entries

    def summary(self, cfg: MementicConfig | None = None) -> dict:
        """Return an aggregated summary of all stored metrics.

        Args:
            cfg: Optional MementicConfig for counting raw source tokens.

        Returns:
            A dict with keys for honest cost accounting:
            total_calls, total_input_tokens, total_output_tokens,
            total_tokens, by_operation, model, status,
            extraction_init_calls, extraction_init_tokens,
            extraction_capture_calls, extraction_capture_tokens,
            extraction_total_tokens, context_pack_queries,
            context_pack_total_tokens, context_pack_avg_tokens,
            context_pack_avg_constraints, context_pack_avg_decisions,
            raw_source_tokens, size_ratio, breakeven_note.
        """
        entries = self.read_all()
        if not entries:
            return _empty_summary(cfg)

        total_calls = len(entries)
        total_input = sum(e.input_tokens for e in entries)
        total_output = sum(e.output_tokens for e in entries)
        total_tokens = sum(e.total_tokens for e in entries)

        by_operation: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "total_tokens": 0}
        )
        model_counter: Counter[str] = Counter()
        status_counter: Counter[str] = Counter()

        extraction_init_calls = 0
        extraction_init_tokens = 0
        extraction_capture_calls = 0
        extraction_capture_tokens = 0

        context_pack_queries = 0
        context_pack_total_tokens = 0

        for e in entries:
            by_operation[e.operation]["calls"] += 1  # type: ignore[index]
            by_operation[e.operation]["total_tokens"] += e.total_tokens  # type: ignore[index]

            if e.model:
                model_counter[e.model] += 1
            status_counter[e.status] += 1

            if e.operation in ("init_adr", "init_freeform"):
                extraction_init_calls += 1
                extraction_init_tokens += e.total_tokens
            elif e.operation == "capture":
                extraction_capture_calls += 1
                extraction_capture_tokens += e.total_tokens

            if e.operation == "mcp_context_pack":
                context_pack_queries += 1
                context_pack_total_tokens += e.total_tokens

        model = model_counter.most_common(1)[0][0] if model_counter else ""

        extraction_total_tokens = extraction_init_tokens + extraction_capture_tokens

        context_pack_avg_tokens = (
            context_pack_total_tokens / context_pack_queries if context_pack_queries > 0 else 0.0
        )

        raw_source_tokens = snapshot_raw_sources(cfg) if cfg is not None else 0

        if context_pack_queries > 0 and raw_source_tokens > 0:
            size_ratio: float | None = context_pack_avg_tokens / raw_source_tokens
            if size_ratio < 1.0:
                savings_pct = (1.0 - size_ratio) * 100
                breakeven_note = (
                    f"Context pack is {savings_pct:.0f}% smaller than raw sources "
                    f"({context_pack_avg_tokens:.0f} vs {raw_source_tokens} tokens). "
                    f"Saving tokens per query."
                )
            else:
                breakeven_note = (
                    f"Context pack is {size_ratio:.1f}x larger than raw sources "
                    f"({context_pack_avg_tokens:.0f} vs {raw_source_tokens} tokens). "
                    f"Not yet saving."
                )
        else:
            size_ratio = None
            breakeven_note = "Need benchmark data. Run `mementic context --benchmark`."

        return {
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_tokens,
            "by_operation": dict(by_operation),
            "model": model,
            "status": dict(status_counter),
            "extraction_init_calls": extraction_init_calls,
            "extraction_init_tokens": extraction_init_tokens,
            "extraction_capture_calls": extraction_capture_calls,
            "extraction_capture_tokens": extraction_capture_tokens,
            "extraction_total_tokens": extraction_total_tokens,
            "context_pack_queries": context_pack_queries,
            "context_pack_total_tokens": context_pack_total_tokens,
            "context_pack_avg_tokens": context_pack_avg_tokens,
            "context_pack_avg_constraints": 0.0,
            "context_pack_avg_decisions": 0.0,
            "raw_source_tokens": raw_source_tokens,
            "size_ratio": size_ratio,
            "breakeven_note": breakeven_note,
        }

    def reset(self) -> None:
        """Delete the metrics file if it exists.

        Does nothing when the file is already absent.
        """
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
