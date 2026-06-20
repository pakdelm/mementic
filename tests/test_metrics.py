"""Tests for mementic.metrics (UsageStore)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

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
        assert result["extraction_init_calls"] == 0
        assert result["extraction_init_tokens"] == 0
        assert result["extraction_capture_calls"] == 0
        assert result["extraction_capture_tokens"] == 0
        assert result["extraction_total_tokens"] == 0
        assert result["context_pack_queries"] == 0
        assert result["context_pack_total_tokens"] == 0
        assert result["context_pack_avg_tokens"] == 0.0

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

    def test_summary_honest_fields_only(self, tmp_path: Path) -> None:
        """summary() does not contain deprecated or dishonest fields."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="get_task_context", total_tokens=100))

        result = store.summary()

        # The dishonest/deprecated fields must not exist
        assert "avg_savings_ratio" not in result
        assert "tasks_to_breakeven" not in result
        assert "benchmark_tasks" not in result
        assert "avg_baseline_tokens" not in result
        assert "avg_context_pack_tokens" not in result
        assert "breakeven_note" not in result
        assert "raw_source_tokens" not in result
        assert "size_ratio" not in result

    def test_summary_context_pack_aggregation(self, tmp_path: Path) -> None:
        """summary() correctly aggregates context pack query metrics."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="get_task_context", total_tokens=200))
        store.append(_make_entry(operation="get_task_context", total_tokens=300))
        store.append(_make_entry(operation="get_task_context", total_tokens=100))

        result = store.summary()

        assert result["context_pack_queries"] == 3
        assert result["context_pack_total_tokens"] == 600
        assert result["context_pack_avg_tokens"] == 200.0  # 600 / 3

    def test_summary_mixed_operations(self, tmp_path: Path) -> None:
        """summary() handles mix of extraction, context pack, and other entries."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="capture", total_tokens=100))
        store.append(_make_entry(operation="init_adr", total_tokens=200))
        store.append(_make_entry(operation="get_task_context", total_tokens=50))

        result = store.summary()

        assert result["total_calls"] == 3
        assert result["total_tokens"] == 350
        assert result["extraction_init_calls"] == 1
        assert result["extraction_capture_calls"] == 1
        assert result["context_pack_queries"] == 1
        assert result["by_operation"]["capture"]["calls"] == 1
        assert result["by_operation"]["get_task_context"]["calls"] == 1

    def test_summary_no_negative_savings_field(self, tmp_path: Path) -> None:
        """summary() never includes a negative savings ratio or similar fake field."""
        store = UsageStore(path=str(tmp_path / "metrics.jsonl"))
        store.append(_make_entry(operation="init_adr", total_tokens=100))
        store.append(_make_entry(operation="get_task_context", total_tokens=10))

        result = store.summary()

        # No dishonest savings field
        assert "avg_savings_ratio" not in result
        assert "savings_ratio" not in result


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
            operation="get_task_context",
            source_label="my-source",
            input_tokens=42,
            output_tokens=7,
            total_tokens=49,
            model="claude-3-opus",
            status="success",
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


# ── Spec 12b: SQLite metrics ───────────────────────────────────────

_EXTRACTION_LOG_DDL = """\
CREATE TABLE IF NOT EXISTS extraction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    source_label TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    model TEXT DEFAULT '',
    status TEXT DEFAULT 'success',
    timestamp TEXT NOT NULL
);
"""


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with the extraction_log table."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_EXTRACTION_LOG_DDL)
    conn.commit()
    return conn


class TestSQLiteMetrics:
    """Tests for UsageStore with SQLite connection."""

    def test_append_to_db(self) -> None:
        """Creates UsageStore(conn=conn), appends and reads back a MetricsEntry."""
        conn = _make_db()
        store = UsageStore(conn=conn)

        entry = _make_entry(
            operation="capture",
            source_label="test-src",
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            model="deepseek-chat",
            status="success",
            timestamp="2024-06-01T00:00:00Z",
        )
        store.append(entry)

        rows = store.read_all()
        assert len(rows) == 1
        assert rows[0].operation == "capture"
        assert rows[0].source_label == "test-src"
        assert rows[0].input_tokens == 10
        assert rows[0].output_tokens == 20
        assert rows[0].total_tokens == 30
        assert rows[0].model == "deepseek-chat"
        assert rows[0].status == "success"
        assert rows[0].timestamp == "2024-06-01T00:00:00Z"

    def test_read_all_from_db(self) -> None:
        """Appends 3 entries, reads all, verifies count and fields."""
        conn = _make_db()
        store = UsageStore(conn=conn)

        for i in range(3):
            store.append(_make_entry(
                timestamp=f"2024-06-0{i+1}T00:00:00Z",
                total_tokens=(i + 1) * 100,
            ))

        rows = store.read_all()
        assert len(rows) == 3
        assert rows[0].timestamp == "2024-06-01T00:00:00Z"
        assert rows[2].timestamp == "2024-06-03T00:00:00Z"
        assert rows[0].total_tokens == 100
        assert rows[2].total_tokens == 300

    def test_summary_from_db(self) -> None:
        """Appends 3 entries with different operations, calls summary(), verifies total_calls=3."""
        conn = _make_db()
        store = UsageStore(conn=conn)

        store.append(_make_entry(operation="capture", total_tokens=100))
        store.append(_make_entry(operation="init_adr", total_tokens=200))
        store.append(_make_entry(operation="init_freeform", total_tokens=300))

        result = store.summary()
        assert result["total_calls"] == 3
        assert result["total_tokens"] == 600
        assert result["by_operation"]["capture"]["calls"] == 1
        assert result["by_operation"]["init_adr"]["calls"] == 1

    def test_reset_db(self) -> None:
        """Appends 2 entries, resets, reads all → empty list."""
        conn = _make_db()
        store = UsageStore(conn=conn)

        store.append(_make_entry())
        store.append(_make_entry())
        assert len(store.read_all()) == 2

        store.reset()
        assert store.read_all() == []

    def test_jsonl_fallback(self, tmp_path: Path) -> None:
        """UsageStore without conn still works (append/read via JSONL)."""
        jsonl_path = tmp_path / "metrics.jsonl"
        store = UsageStore(path=str(jsonl_path))

        store.append(_make_entry(operation="capture", total_tokens=99))
        store.append(_make_entry(operation="init_adr", total_tokens=101))

        rows = store.read_all()
        assert len(rows) == 2
        assert rows[0].operation == "capture"
        assert rows[1].operation == "init_adr"
        assert rows[0].total_tokens == 99
        assert rows[1].total_tokens == 101



