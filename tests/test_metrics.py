"""Tests for mementic.metrics (UsageStore)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mementic.metrics import UsageStore
from mementic.models import MetricsEntry


def _make_entry(**overrides: object) -> MetricsEntry:
    """Return a MetricsEntry with sensible defaults, overridden by kwargs."""
    entry = MetricsEntry(
        timestamp="2024-01-01T00:00:00Z",
        operation="capture",
        source_label="test",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        model="deepseek-chat",
        status="success",
    )
    if overrides:
        entry = entry.model_copy(update=overrides)  # type: ignore[arg-type]
    return entry


# ---------------------------------------------------------------------------
# TestAppendAndRead
# ---------------------------------------------------------------------------


class TestAppendAndRead:
    """Tests for UsageStore.append() and UsageStore.read_all()."""

    def test_append_creates_file_and_dirs(self, tmp_path: Path) -> None:
        """append writes JSONL, file exists, parent directory is created."""
        store = UsageStore(path=str(tmp_path / "sub" / "metrics.jsonl"))
        store.append(_make_entry())

        assert store._path.exists()
        assert store._path.parent.exists()

    def test_read_all_returns_entries(self, tmp_path: Path) -> None:
        """read_all returns all appended entries."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(timestamp="2024-01-01T00:00:00Z"))
        store.append(_make_entry(timestamp="2024-01-02T00:00:00Z"))
        store.append(_make_entry(timestamp="2024-01-03T00:00:00Z"))

        entries = store.read_all()
        assert len(entries) == 3
        assert entries[0].timestamp == "2024-01-01T00:00:00Z"
        assert entries[2].timestamp == "2024-01-03T00:00:00Z"

    def test_read_all_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """read_all returns [] when the file does not exist."""
        store = UsageStore(path=str(tmp_path / "nonexistent" / "metrics.jsonl"))
        entries = store.read_all()
        assert entries == []

    def test_read_all_skips_blank_lines(self, tmp_path: Path) -> None:
        """read_all ignores blank lines in the JSONL file."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(timestamp="2024-01-01T00:00:00Z"))

        # Manually insert a blank line into the file
        with open(store._path, "a", encoding="utf-8") as f:
            f.write("\n")
        store.append(_make_entry(timestamp="2024-01-02T00:00:00Z"))

        entries = store.read_all()
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# TestSummary
# ---------------------------------------------------------------------------


class TestSummary:
    """Tests for UsageStore.summary()."""

    def test_summary_empty_returns_defaults(self, tmp_path: Path) -> None:
        """summary() on an empty store returns a dict with zero/default values."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        result = store.summary()

        assert result["total_calls"] == 0
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["total_tokens"] == 0
        assert result["by_operation"] == {}
        assert result["model"] == ""
        assert result["status"] == {}
        assert result["benchmark_tasks"] == 0
        assert result["avg_baseline_tokens"] == 0.0
        assert result["avg_context_pack_tokens"] == 0.0
        assert result["avg_savings_ratio"] == 0.0
        assert result["tasks_to_breakeven"] is None

    def test_summary_aggregates_tokens(self, tmp_path: Path) -> None:
        """summary() correctly aggregates token counts across entries."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(input_tokens=100, output_tokens=50, total_tokens=150))
        store.append(_make_entry(input_tokens=200, output_tokens=80, total_tokens=280))

        result = store.summary()

        assert result["total_calls"] == 2
        assert result["total_input_tokens"] == 300
        assert result["total_output_tokens"] == 130
        assert result["total_tokens"] == 430

    def test_summary_by_operation(self, tmp_path: Path) -> None:
        """summary() breaks down by operation correctly."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="capture", total_tokens=100))
        store.append(_make_entry(operation="capture", total_tokens=200))
        store.append(_make_entry(operation="init_adr", total_tokens=50))

        result = store.summary()

        by_op = result["by_operation"]
        assert by_op["capture"]["calls"] == 2
        assert by_op["capture"]["total_tokens"] == 300
        assert by_op["init_adr"]["calls"] == 1
        assert by_op["init_adr"]["total_tokens"] == 50

    def test_summary_status_counts(self, tmp_path: Path) -> None:
        """summary() counts statuses correctly."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(status="success"))
        store.append(_make_entry(status="success"))
        store.append(_make_entry(status="error"))

        result = store.summary()

        assert result["status"] == {"success": 2, "error": 1}

    def test_summary_model_counter(self, tmp_path: Path) -> None:
        """summary() returns the most common model."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(model="deepseek-chat"))
        store.append(_make_entry(model="deepseek-chat"))
        store.append(_make_entry(model="gpt-4"))

        result = store.summary()

        assert result["model"] == "deepseek-chat"

    def test_summary_model_counter_single_entry(self, tmp_path: Path) -> None:
        """summary() model is correct with a single entry."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(model="gpt-4"))

        result = store.summary()

        assert result["model"] == "gpt-4"

    def test_summary_benchmark_calculations(self, tmp_path: Path) -> None:
        """summary() computes benchmark averages and tasks_to_breakeven."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=10000,
                context_pack_tokens=2000,
                savings_ratio=0.8,
                total_tokens=500,
            )
        )
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=8000,
                context_pack_tokens=1500,
                savings_ratio=0.7,
                total_tokens=400,
            )
        )

        result = store.summary()

        assert result["benchmark_tasks"] == 2
        assert result["avg_baseline_tokens"] == 9000.0  # (10000+8000)/2
        assert result["avg_context_pack_tokens"] == 1750.0  # (2000+1500)/2
        assert result["avg_savings_ratio"] == 0.75  # (0.8+0.7)/2

        avg_savings = 9000.0 - 1750.0  # 7250.0
        expected_breakeven = 900.0 / avg_savings  # total_tokens / avg_savings
        assert result["tasks_to_breakeven"] == pytest.approx(expected_breakeven)

    def test_summary_breakeven_none_when_no_savings(self, tmp_path: Path) -> None:
        """tasks_to_breakeven is None when avg_savings <= 0."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=1000,
                context_pack_tokens=2000,  # more context than baseline → negative savings
                savings_ratio=-0.5,
                total_tokens=100,
            )
        )

        result = store.summary()

        assert result["benchmark_tasks"] == 1
        # avg_baseline - avg_context_pack = 1000 - 2000 = -1000 <= 0 → no breakeven
        assert result["tasks_to_breakeven"] is None

    def test_summary_breakeven_zero_savings(self, tmp_path: Path) -> None:
        """tasks_to_breakeven is None when avg_savings exactly equals 0."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=1000,
                context_pack_tokens=1000,  # equal → savings = 0
                savings_ratio=0.0,
                total_tokens=100,
            )
        )

        result = store.summary()

        assert result["benchmark_tasks"] == 1
        assert result["tasks_to_breakeven"] is None

    def test_summary_benchmark_handles_none_fields(self, tmp_path: Path) -> None:
        """Benchmark entries with None baseline/context_pack/savings_ratio don't crash."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=None,
                context_pack_tokens=None,
                savings_ratio=None,
                total_tokens=100,
            )
        )
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=5000,
                context_pack_tokens=1000,
                savings_ratio=0.4,
                total_tokens=200,
            )
        )

        result = store.summary()

        assert result["benchmark_tasks"] == 2
        # None is treated as 0: (0 + 5000) / 2 = 2500.0
        assert result["avg_baseline_tokens"] == 2500.0
        assert result["avg_context_pack_tokens"] == 500.0
        # (0.0 + 0.4) / 2 = 0.2
        assert result["avg_savings_ratio"] == 0.2

    def test_summary_mixed_operations_with_benchmark(self, tmp_path: Path) -> None:
        """summary() correctly separates benchmark and non-benchmark entries."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(
            _make_entry(
                operation="capture", total_tokens=100, model="gpt-4", status="success"
            )
        )
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=5000,
                context_pack_tokens=1000,
                savings_ratio=0.5,
                total_tokens=200,
            )
        )

        result = store.summary()

        assert result["total_calls"] == 2
        assert result["total_tokens"] == 300
        assert result["by_operation"]["capture"]["calls"] == 1
        assert result["by_operation"]["enforce_benchmark"]["calls"] == 1
        assert result["benchmark_tasks"] == 1

    def test_summary_model_empty_string_entries(self, tmp_path: Path) -> None:
        """summary() handles entries with empty model string gracefully."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(model=""))
        store.append(_make_entry(model=""))

        result = store.summary()

        # Empty strings are falsy, so model_counter stays empty → returns ""
        assert result["model"] == ""


# ---------------------------------------------------------------------------
# TestReset
# ---------------------------------------------------------------------------


class TestReset:
    """Tests for UsageStore.reset()."""

    def test_reset_deletes_file(self, tmp_path: Path) -> None:
        """reset() removes the metrics file when it exists."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry())
        assert store._path.exists()

        store.reset()

        assert not store._path.exists()

    def test_reset_does_nothing_when_no_file(self, tmp_path: Path) -> None:
        """reset() does not raise when the file is already absent."""
        store = UsageStore(path=str(tmp_path / "nonexistent" / "metrics.jsonl"))
        # Should not raise
        store.reset()

    def test_reset_clears_data(self, tmp_path: Path) -> None:
        """After reset, read_all returns [] and summary returns defaults."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry())
        store.append(_make_entry())

        store.reset()

        assert store.read_all() == []
        result = store.summary()
        assert result["total_calls"] == 0

    def test_reset_survives_os_error(self, tmp_path: Path) -> None:
        """reset() does not raise when unlink fails with OSError."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry())

        # Simulate a permission / filesystem error on unlink
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            # Should not raise
            store.reset()


# ---------------------------------------------------------------------------
# TestRoundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    """Integration-style tests verifying JSONL serialisation fidelity."""

    def test_roundtrip_preserves_all_fields(self, tmp_path: Path) -> None:
        """All MetricsEntry fields survive append → read_all."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        entry = _make_entry(
            timestamp="2024-06-15T12:30:00Z",
            operation="mcp_context_pack",
            source_label="my-source",
            input_tokens=42,
            output_tokens=7,
            total_tokens=49,
            model="claude-3-opus",
            status="success",
            baseline_tokens=None,
            context_pack_tokens=None,
            savings_ratio=None,
        )
        store.append(entry)
        [restored] = store.read_all()

        assert restored.timestamp == entry.timestamp
        assert restored.operation == entry.operation
        assert restored.source_label == entry.source_label
        assert restored.input_tokens == entry.input_tokens
        assert restored.output_tokens == entry.output_tokens
        assert restored.total_tokens == entry.total_tokens
        assert restored.model == entry.model
        assert restored.status == entry.status
        assert restored.baseline_tokens == entry.baseline_tokens
        assert restored.context_pack_tokens == entry.context_pack_tokens
        assert restored.savings_ratio == entry.savings_ratio

    def test_jsonl_format_is_valid(self, tmp_path: Path) -> None:
        """The file contains valid JSON lines, one per entry."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(timestamp="2024-01-01T00:00:00Z"))
        store.append(_make_entry(timestamp="2024-01-02T00:00:00Z"))

        raw = Path(store._path).read_text(encoding="utf-8")
        # Filter out blank lines (works cross-platform regardless of \n or \r\n)
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data
            assert isinstance(data["timestamp"], str)


# ---------------------------------------------------------------------------
# TestDefaultPath
# ---------------------------------------------------------------------------


class TestDefaultPath:
    """Tests for UsageStore default path behaviour."""

    def test_repr_includes_path(self) -> None:
        """UsageStore's default (no explicit path) uses .mementic/metrics.jsonl."""
        store = UsageStore()
        assert store._path.name == "metrics.jsonl"
        # The relative path ends with .mementic / metrics.jsonl (os-agnostic)
        assert store._path.parts[-2] == ".mementic"
        assert store._path.parts[-1] == "metrics.jsonl"

    def test_custom_path_is_stored(self) -> None:
        """A custom path string is stored as a Path object."""
        store = UsageStore(path="custom/path/data.jsonl")
        assert isinstance(store._path, Path)
        # Path str() uses OS-native separators — compare with Path equality
        assert store._path == Path("custom/path/data.jsonl")
