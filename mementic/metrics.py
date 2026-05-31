"""Operational metrics storage backed by append-only JSONL."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from mementic.models import MetricsEntry


class UsageStore:
    """Manages append-only JSONL storage for mementic operational metrics.

    The underlying file lives at `.mementic/metrics.jsonl` by default.
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

    def summary(self) -> dict:
        """Return an aggregated summary of all stored metrics.

        Returns:
            A dict with keys:

            - total_calls: Total number of entries.
            - total_input_tokens: Sum of input tokens across all entries.
            - total_output_tokens: Sum of output tokens across all entries.
            - total_tokens: Sum of total tokens across all entries.
            - by_operation: Dict mapping operation name to a dict with
              ``calls`` (int) and ``total_tokens`` (int).
            - model: Most common model identifier (empty string if none).
            - status: Dict mapping status label to count (e.g. ``success``).
            - benchmark_tasks: Count of ``enforce_benchmark`` entries.
            - avg_baseline_tokens: Average baseline tokens for benchmark
              entries (0.0 if none).
            - avg_context_pack_tokens: Average context-pack tokens for
              benchmark entries (0.0 if none).
            - avg_savings_ratio: Average savings ratio for benchmark entries
              (0.0 if none).
            - tasks_to_breakeven: ``total_tokens / avg_savings`` when
              benchmark data is available and ``avg_savings > 0``,
              otherwise ``None``. ``avg_savings`` is computed as
              ``avg_baseline_tokens - avg_context_pack_tokens``.
        """
        entries = self.read_all()
        if not entries:
            return {
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "by_operation": {},
                "model": "",
                "status": {},
                "benchmark_tasks": 0,
                "avg_baseline_tokens": 0.0,
                "avg_context_pack_tokens": 0.0,
                "avg_savings_ratio": 0.0,
                "tasks_to_breakeven": None,
            }

        total_calls = len(entries)
        total_input = sum(e.input_tokens for e in entries)
        total_output = sum(e.output_tokens for e in entries)
        total_tokens = sum(e.total_tokens for e in entries)

        by_operation: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "total_tokens": 0}
        )
        model_counter: Counter[str] = Counter()
        status_counter: Counter[str] = Counter()

        benchmark_entries: list[MetricsEntry] = []
        avg_baseline = 0.0
        avg_context_pack = 0.0
        avg_savings_ratio = 0.0

        for e in entries:
            by_operation[e.operation]["calls"] += 1  # type: ignore[index]
            by_operation[e.operation]["total_tokens"] += e.total_tokens  # type: ignore[index]

            if e.model:
                model_counter[e.model] += 1
            status_counter[e.status] += 1

            if e.operation == "enforce_benchmark":
                benchmark_entries.append(e)

        model = model_counter.most_common(1)[0][0] if model_counter else ""

        benchmark_tasks = len(benchmark_entries)
        tasks_to_breakeven: float | None = None

        if benchmark_tasks > 0:
            avg_baseline = (
                sum(be.baseline_tokens or 0 for be in benchmark_entries)
                / benchmark_tasks
            )
            avg_context_pack = (
                sum(be.context_pack_tokens or 0 for be in benchmark_entries)
                / benchmark_tasks
            )
            avg_savings_ratio = (
                sum(be.savings_ratio or 0.0 for be in benchmark_entries)
                / benchmark_tasks
            )

            avg_savings = avg_baseline - avg_context_pack
            if avg_savings > 0:
                tasks_to_breakeven = total_tokens / avg_savings

        return {
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_tokens,
            "by_operation": dict(by_operation),
            "model": model,
            "status": dict(status_counter),
            "benchmark_tasks": benchmark_tasks,
            "avg_baseline_tokens": avg_baseline,
            "avg_context_pack_tokens": avg_context_pack,
            "avg_savings_ratio": avg_savings_ratio,
            "tasks_to_breakeven": tasks_to_breakeven,
        }

    def reset(self) -> None:
        """Delete the metrics file if it exists.

        Does nothing when the file is already absent.
        """
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
