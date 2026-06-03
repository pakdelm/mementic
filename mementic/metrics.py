"""Operational metrics storage backed by append-only JSONL and SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from mementic.models import MetricsEntry


def _empty_summary() -> dict:
    """Return an empty summary dict with honest zero/default values."""
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
    }


class UsageStore:
    """Manages append-only storage for mementic operational metrics.

    When ``conn`` is provided, writes to the SQLite ``extraction_log`` table.
    Otherwise falls back to JSONL at ``.mementic/metrics.jsonl``.
    """

    def __init__(
        self,
        path: str = ".mementic/metrics.jsonl",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Initialize with path to the JSONL file and optional SQLite connection.

        Args:
            path: Filesystem path to the metrics JSONL file (used when conn is None).
            conn: Optional SQLite connection. When provided, the extraction_log table
                is used instead of the JSONL file. The table must already exist
                (created by GraphStore's schema migration).
        """
        self._path = Path(path)
        self._conn = conn

    def append(self, entry: MetricsEntry) -> None:
        """Append a single MetricsEntry.

        When ``conn`` is set, writes to the ``extraction_log`` SQLite table.
        Otherwise appends a JSON line to the JSONL file, creating the parent
        directory if it does not exist.

        Args:
            entry: The MetricsEntry instance to persist.
        """
        if self._conn:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO extraction_log
                       (operation, source_label, input_tokens, output_tokens,
                        total_tokens, model, status, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.operation,
                        entry.source_label,
                        entry.input_tokens,
                        entry.output_tokens,
                        entry.total_tokens,
                        entry.model,
                        entry.status,
                        entry.timestamp,
                    ),
                )
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + os.linesep)

    def read_all(self) -> list[MetricsEntry]:
        """Read all entries.

        When ``conn`` is set, reads from the ``extraction_log`` SQLite table.
        Otherwise reads from the JSONL file.

        Returns:
            List of MetricsEntry instances. Returns an empty list when the
            file does not exist or the table is empty.
        """
        if self._conn:
            rows = self._conn.execute(
                "SELECT operation, source_label, input_tokens, output_tokens, "
                "total_tokens, model, status, timestamp FROM extraction_log "
                "ORDER BY id"
            ).fetchall()
            return [
                MetricsEntry(
                    operation=row[0],
                    source_label=row[1],
                    input_tokens=row[2],
                    output_tokens=row[3],
                    total_tokens=row[4],
                    model=row[5],
                    status=row[6],
                    timestamp=row[7],
                )
                for row in rows
            ]

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

    def summary(self) -> dict:
        """Return an aggregated summary of all stored metrics.

        Returns:
            A dict with honest cost accounting:
            total_calls, total_input_tokens, total_output_tokens,
            total_tokens, by_operation, model, status,
            extraction_init_calls, extraction_init_tokens,
            extraction_capture_calls, extraction_capture_tokens,
            extraction_total_tokens, context_pack_queries,
            context_pack_total_tokens, context_pack_avg_tokens,
            context_pack_avg_constraints, context_pack_avg_decisions.
        """
        entries = self.read_all()
        if not entries:
            return _empty_summary()

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
        }

    def reset(self) -> None:
        """Delete all stored metrics.

        When ``conn`` is set, deletes all rows from ``extraction_log``.
        Otherwise deletes the JSONL file.
        """
        if self._conn:
            with self._conn:
                self._conn.execute("DELETE FROM extraction_log")
            return

        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
