"""Tests for mementic.metrics (UsageStore) and mementic.server benchmark logging."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mementic.config import MementicConfig
from mementic.metrics import UsageStore, snapshot_raw_sources
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
        assert result["extraction_init_calls"] == 0
        assert result["extraction_init_tokens"] == 0
        assert result["extraction_capture_calls"] == 0
        assert result["extraction_capture_tokens"] == 0
        assert result["extraction_total_tokens"] == 0
        assert result["context_pack_queries"] == 0
        assert result["context_pack_total_tokens"] == 0
        assert result["context_pack_avg_tokens"] == 0.0
        assert result["context_pack_avg_constraints"] == 0.0
        assert result["context_pack_avg_decisions"] == 0.0
        assert result["raw_source_tokens"] == 0
        assert result["size_ratio"] is None
        assert "Need benchmark data" in result["breakeven_note"]

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

    def test_summary_model_empty_string_entries(self, tmp_path: Path) -> None:
        """summary() handles entries with empty model string gracefully."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(model=""))
        store.append(_make_entry(model=""))

        result = store.summary()

        # Empty strings are falsy, so model_counter stays empty - returns ""
        assert result["model"] == ""

    # ── Honest metrics: extraction breakdown ─────────────────────────

    def test_summary_extraction_breakdown(self, tmp_path: Path) -> None:
        """summary() correctly breaks down init vs capture costs."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="init_adr", total_tokens=100))
        store.append(_make_entry(operation="init_freeform", total_tokens=200))
        store.append(_make_entry(operation="init_adr", total_tokens=50))
        store.append(_make_entry(operation="capture", total_tokens=300))
        store.append(_make_entry(operation="capture", total_tokens=400))

        result = store.summary()

        assert result["extraction_init_calls"] == 3
        assert result["extraction_init_tokens"] == 350  # 100 + 200 + 50
        assert result["extraction_capture_calls"] == 2
        assert result["extraction_capture_tokens"] == 700  # 300 + 400
        assert result["extraction_total_tokens"] == 1050  # 350 + 700

    def test_summary_no_negative_savings(self, tmp_path: Path) -> None:
        """summary() does not contain avg_savings_ratio or negative savings display."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(
            _make_entry(
                operation="enforce_benchmark",
                baseline_tokens=1000,
                context_pack_tokens=2000,
                savings_ratio=-0.5,
                total_tokens=100,
            )
        )

        result = store.summary()

        # The dishonest fields must not exist
        assert "avg_savings_ratio" not in result
        assert "tasks_to_breakeven" not in result
        assert "benchmark_tasks" not in result
        assert "avg_baseline_tokens" not in result
        assert "avg_context_pack_tokens" not in result

        # Honest fields exist
        assert "Need benchmark data" in result["breakeven_note"]

    def test_summary_breakeven_requires_data(self, tmp_path: Path) -> None:
        """When no context packs are logged, breakeven_note indicates need for data."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="capture", total_tokens=100))
        store.append(_make_entry(operation="init_adr", total_tokens=50))

        result = store.summary()

        assert result["context_pack_queries"] == 0
        assert result["context_pack_total_tokens"] == 0
        assert result["context_pack_avg_tokens"] == 0.0
        assert result["size_ratio"] is None
        assert "Need benchmark data" in result["breakeven_note"]

    def test_summary_context_pack_aggregation(self, tmp_path: Path) -> None:
        """summary() correctly aggregates context pack query metrics."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="mcp_context_pack", total_tokens=200))
        store.append(_make_entry(operation="mcp_context_pack", total_tokens=300))
        store.append(_make_entry(operation="mcp_context_pack", total_tokens=100))

        result = store.summary()

        assert result["context_pack_queries"] == 3
        assert result["context_pack_total_tokens"] == 600
        assert result["context_pack_avg_tokens"] == 200.0  # 600 / 3

    def test_summary_mixed_operations(self, tmp_path: Path) -> None:
        """summary() handles mix of extraction, context pack, and other entries."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="capture", total_tokens=100))
        store.append(_make_entry(operation="init_adr", total_tokens=200))
        store.append(_make_entry(operation="mcp_context_pack", total_tokens=50))
        store.append(_make_entry(operation="enforce_benchmark", total_tokens=30))

        result = store.summary()

        assert result["total_calls"] == 4
        assert result["total_tokens"] == 380
        assert result["extraction_init_calls"] == 1
        assert result["extraction_capture_calls"] == 1
        assert result["context_pack_queries"] == 1
        assert result["by_operation"]["capture"]["calls"] == 1
        assert result["by_operation"]["mcp_context_pack"]["calls"] == 1


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
        """All MetricsEntry fields survive append - read_all."""
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
        # Path str() uses OS-native separators - compare with Path equality
        assert store._path == Path("custom/path/data.jsonl")


# ---------------------------------------------------------------------------
# snapshot_raw_sources tests
# ---------------------------------------------------------------------------


class TestSnapshotRawSources:
    """Tests for snapshot_raw_sources()."""

    def test_snapshot_raw_sources(self, tmp_path: Path) -> None:
        """snapshot_raw_sources counts tokens in configured source directories."""
        adr_dir = tmp_path / "adrs"
        adr_dir.mkdir()
        (adr_dir / "adr-001.md").write_text("hello world hello", encoding="utf-8")
        (adr_dir / "adr-002.md").write_text("foo bar baz", encoding="utf-8")

        oc_dir = tmp_path / "opencode"
        oc_dir.mkdir()
        (oc_dir / "style.md").write_text("some instructions here", encoding="utf-8")

        from mementic.config import AdrDirSource, OpenCodeSource, Sources

        cfg = MementicConfig(
            sources=Sources(
                adr_dirs=[AdrDirSource(path=str(adr_dir))],
                opencode_instructions=[OpenCodeSource(path=str(oc_dir))],
            )
        )

        total = snapshot_raw_sources(cfg)
        # 21 chars in 3 files, token estimate = 21 // 4 = 5
        # hello world hello = 17 chars, foo bar baz = 11, some instructions here = 23
        # total = 51 chars, 51 // 4 = 12
        assert total > 0
        assert isinstance(total, int)

    def test_snapshot_empty_dirs(self, tmp_path: Path) -> None:
        """snapshot_raw_sources returns 0 for empty/nonexistent directories."""
        from mementic.config import AdrDirSource, Sources

        nonexistent = str(tmp_path / "does_not_exist")
        cfg = MementicConfig(
            sources=Sources(
                adr_dirs=[AdrDirSource(path=nonexistent)],
            )
        )

        total = snapshot_raw_sources(cfg)
        assert total == 0

    def test_snapshot_no_sources_configured(self) -> None:
        """snapshot_raw_sources returns 0 when no sources are configured."""
        cfg = MementicConfig()
        total = snapshot_raw_sources(cfg)
        assert total == 0


# ---------------------------------------------------------------------------
# Benchmark logging test (server.py)
# ---------------------------------------------------------------------------


class TestBenchmarkLogging:
    """Tests for honest benchmark logging in server.py get_context_pack."""

    def test_benchmark_logs_honest_fields(self, tmp_path: Path) -> None:
        """Benchmark MetricsEntry has baseline_tokens and context_pack_tokens set correctly."""
        temp_store = UsageStore(path=str(tmp_path / "metrics.jsonl"))

        with (
            patch("mementic.server._load_graph") as mock_load,
            patch("mementic.server.format_context_pack", return_value="pack text"),
            patch("mementic.server.count_tokens", return_value=100),
            patch("mementic.server._count_raw_source_tokens", return_value=5000),
            patch("mementic.server._metrics_store", temp_store),
        ):
            cfg = MementicConfig()
            mock_load.return_value = (MagicMock(), cfg)

            from mementic.server import get_context_pack

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(get_context_pack("test task", ["file.py"], benchmark=True))
            finally:
                loop.close()

        entries = temp_store.read_all()
        bench_entries = [e for e in entries if e.operation == "enforce_benchmark"]
        assert len(bench_entries) == 1
        be = bench_entries[0]
        assert be.baseline_tokens == 5000
        assert be.context_pack_tokens == 100
        assert be.savings_ratio == 0.0  # deprecated
        assert be.total_tokens == 100  # actual LLM cost
