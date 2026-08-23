"""Tests for the one-shot v4 -> v5 migration script (Spec 29 Phase 3)."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migrate_v4_to_v5 import migrate  # noqa: E402


V4_DDL = """
CREATE TABLE decisions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    rationale TEXT DEFAULT '',
    source TEXT DEFAULT '',
    repo TEXT DEFAULT '',
    affected_entities TEXT DEFAULT '[]',
    rejected_alternatives TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    modified_at TEXT NOT NULL
);

CREATE TABLE constraints (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    enforced_by TEXT DEFAULT '',
    source TEXT DEFAULT '',
    repo TEXT DEFAULT '',
    bound_entities TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE entities (
    id TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'file',
    repo TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (repo, id)
);

CREATE TABLE edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, kind)
);

CREATE TABLE project_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL UNIQUE,
    label TEXT,
    ingested_at TEXT NOT NULL
);

CREATE TABLE extraction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    action        TEXT NOT NULL,
    details       TEXT DEFAULT '{}',
    source        TEXT DEFAULT '',
    timestamp     TEXT NOT NULL
);
"""


def _build_v4_fixture(db_path: Path) -> None:
    """Create a miniature v4 database exercising every migration rule."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(V4_DDL)

    conn.executemany(
        "INSERT INTO decisions (id, title, status, rationale, source, repo, "
        "affected_entities, rejected_alternatives, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # Live decision with a JSON path.
            ("d-live", "Use SQLite", "accepted", "Simple persistence",
             "cli", "mementic", '["src/db.py"]', "[]",
             "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
            # Deprecated decision whose reason lives in a rationale suffix.
            ("d-dead", "Use Monolith", "deprecated",
             "Single deployable keeps ops simple [DEPRECATED: moved to services]",
             "cli", "mementic", "[]", "[]",
             "2024-02-01T00:00:00Z", "2024-03-01T00:00:00Z"),
            # Rejected decision: must tombstone, never resurface as live.
            ("d-rejected", "Adopt NoSql", "rejected",
             "Sounded fast on a whiteboard",
             "cli", "mementic", "[]", "[]",
             "2024-02-15T00:00:00Z", "2024-02-15T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO constraints (id, description, enforced_by, source, repo, "
        "bound_entities, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("c-live", "All I/O must be async", "d-live",
             "cli", "mementic", '["src/io.py"]',
             "active", "2024-01-01T00:00:00Z"),
            # Zombie: stuck at legacy 'proposed' status -> must be dropped.
            ("c-zombie", "Never merged proposal", "d-live",
             "capture", "mementic", "[]",
             "proposed", "2024-01-05T00:00:00Z"),
            ("c-dead", "Old rule [DEPRECATED: replaced by new rule]", "",
             "cli", "mementic", "[]",
             "deprecated", "2024-02-01T00:00:00Z"),
        ],
    )
    # Edges: affects/bind_to materialize into entry_paths; enforces is dropped;
    # the affects target duplicates the JSON path -> deduplicated.
    conn.executemany(
        "INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, ?)",
        [
            ("d-live", "src/db.py", "affects"),          # dup of JSON path
            ("d-live", "src/extra.py", "affects"),        # new path
            ("c-live", "src/io.py", "binds_to"),          # matches JSON path
            ("d-live", "c-live", "enforces"),             # non-path edge
        ],
    )
    conn.execute(
        "INSERT INTO entities (id, type, repo, created_at) "
        "VALUES ('src/db.py', 'file', 'mementic', '2024-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO audit_log (entity_type, entity_id, action, details, "
        "source, timestamp) VALUES ('decision', 'd-live', 'add', '{}', '', "
        "'2024-01-01T00:00:00Z')"
    )
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()


@pytest.fixture
def v4_db(tmp_path: Path) -> Path:
    """A populated v4 database."""
    db = tmp_path / "graph.db"
    _build_v4_fixture(db)
    return db


class TestMigrateV4ToV5:
    def test_roundtrip_moves_all_data(self, v4_db: Path) -> None:
        assert migrate(v4_db) == 0

        conn = sqlite3.connect(str(v4_db))
        conn.row_factory = sqlite3.Row

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5

        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"decisions", "constraints", "entry_paths", "deprecations",
                "audit_log"} <= tables
        assert not {"entities", "edges", "project_sources",
                    "extraction_log"} & tables

        # Decisions copied verbatim minus dead columns.
        decisions = {
            r["id"]: dict(r) for r in conn.execute("SELECT * FROM decisions")
        }
        assert set(decisions) == {"d-live", "d-dead", "d-rejected"}
        assert decisions["d-live"]["rationale"] == "Simple persistence"
        assert set(decisions["d-live"]) == {
            "id", "title", "rationale", "created_at",
        }

        # The zombie proposed constraint was dropped.
        constraint_ids = {
            r["id"] for r in conn.execute("SELECT id FROM constraints")
        }
        assert constraint_ids == {"c-live", "c-dead"}

        # entry_paths materialized and deduplicated (JSON + edges).
        paths = {
            (r["entry_type"], r["entry_id"], r["path"])
            for r in conn.execute("SELECT * FROM entry_paths")
        }
        assert paths == {
            ("decision", "d-live", "src/db.py"),
            ("decision", "d-live", "src/extra.py"),
            ("constraint", "c-live", "src/io.py"),
        }

        # Deprecations backfilled with parsed reasons, replaced_by NULL.
        deps = {
            (r["entry_type"], r["entry_id"]): dict(r)
            for r in conn.execute("SELECT * FROM deprecations")
        }
        assert set(deps) == {
            ("decision", "d-dead"),
            ("constraint", "c-dead"),
            ("decision", "d-rejected"),
        }
        assert deps[("decision", "d-dead")]["reason"] == "moved to services"
        assert deps[("constraint", "c-dead")]["reason"] == (
            "replaced by new rule"
        )
        assert deps[("decision", "d-rejected")]["reason"] == (
            "rejected (pre-v5 status)"
        )
        assert all(d["replaced_by"] is None for d in deps.values())

        # Suffix stripped out of migrated prose.
        assert "[DEPRECATED" not in decisions["d-dead"]["rationale"]
        dead_desc = conn.execute(
            "SELECT description FROM constraints WHERE id = 'c-dead'"
        ).fetchone()[0]
        assert dead_desc == "Old rule"

        # Standalone enforced_by stored as NULL (FK-safe).
        enforced = conn.execute(
            "SELECT enforced_by FROM constraints WHERE id = 'c-dead'"
        ).fetchone()[0]
        assert enforced is None

        # audit_log carried forward untouched.
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit_log"
        ).fetchone()[0]
        assert audit_count == 1
        conn.close()

    def test_backup_file_created(self, v4_db: Path) -> None:
        before = v4_db.read_bytes()
        assert migrate(v4_db) == 0
        backup = Path(str(v4_db) + ".bak")
        assert backup.exists()
        assert backup.read_bytes() == before

    def test_migrated_store_serves_live_sets(self, v4_db: Path) -> None:
        from mementic.store import MementicStore

        assert migrate(v4_db) == 0
        store = MementicStore(db_path=v4_db)
        live_d = {d.id for d in store.get_live_decisions()}
        live_c = {c.id for c in store.get_live_constraints()}
        assert live_d == {"d-live"}
        assert live_c == {"c-live"}
        # Paths survive into model objects.
        d_live = next(d for d in store.get_all_decisions() if d.id == "d-live")
        assert sorted(d_live.paths) == ["src/db.py", "src/extra.py"]
        store.close()

    def test_rerun_refuses_non_v4_database(self, v4_db: Path) -> None:
        assert migrate(v4_db) == 0
        # Now at v5: refusing to re-run is the loud failure mode.
        assert migrate(v4_db) == 1

    def test_missing_database_fails(self, tmp_path: Path) -> None:
        assert migrate(tmp_path / "nope.db") == 1

    def test_unexpected_version_fails(self, tmp_path: Path) -> None:
        db = tmp_path / "v3.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (x)")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()
        assert migrate(db) == 1

    def test_dangling_enforced_by_fails_before_mutation(
        self, tmp_path: Path,
    ) -> None:
        db = tmp_path / "bad.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(V4_DDL)
        conn.execute(
            "INSERT INTO decisions (id, title, status, rationale, source, "
            "repo, affected_entities, rejected_alternatives, created_at, "
            "modified_at) VALUES ('d1', 'T', 'accepted', 'r', '', '', '[]', "
            "'[]', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO constraints (id, description, enforced_by, source, "
            "repo, bound_entities, status, created_at) "
            "VALUES ('c1', 'desc', 'missing-d', '', '', '[]', 'active', "
            "'2024-01-01T00:00:00Z')"
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        assert migrate(db) == 1
        # Database untouched: still v4 with original rows.
        conn = sqlite3.connect(str(db))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM decisions"
        ).fetchone()[0] == 1
        conn.close()

    def test_bare_deprecated_marker_yields_empty_reason(
        self, tmp_path: Path,
    ) -> None:
        db = tmp_path / "bare.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(V4_DDL)
        conn.execute(
            "INSERT INTO decisions (id, title, status, rationale, source, "
            "repo, affected_entities, rejected_alternatives, created_at, "
            "modified_at) VALUES ('d1', 'T', 'deprecated', 'Old idea "
            "[DEPRECATED]', '', '', '[]', '[]', "
            "'2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')"
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        assert migrate(db) == 0
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        dep = conn.execute("SELECT * FROM deprecations").fetchone()
        assert dep["reason"] == ""
        row = conn.execute(
            "SELECT rationale FROM decisions WHERE id = 'd1'"
        ).fetchone()
        assert row["rationale"] == "Old idea"
        conn.close()


class TestMigrationAtomicity:
    def test_failure_mid_rebuild_leaves_pristine_v4(
        self, v4_db: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A crash between the drops and the commit destroys nothing.

        The whole rebuild runs in one transaction: simulating an exception
        mid-copy must roll back every DROP/CREATE/INSERT and leave a
        readable v4 database behind.
        """
        import migrate_v4_to_v5 as mig

        def boom(raw: str) -> str:
            raise RuntimeError("simulated crash mid-copy")

        monkeypatch.setattr(mig, "_normalize_path", boom)
        assert mig.migrate(v4_db) == 1

        conn = sqlite3.connect(str(v4_db))
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # Rolled-back drops: legacy v4 tables all still exist...
        assert {"entities", "edges", "project_sources"} <= tables
        assert "entry_paths" not in tables and "deprecations" not in tables
        # ...with every original row intact.
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM constraints"
        ).fetchone()[0] == 3
        conn.close()

    def test_refuses_to_overwrite_existing_backup(
        self, v4_db: Path, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An existing .bak file aborts the migration before any mutation."""
        backup = Path(str(v4_db) + ".bak")
        backup.write_bytes(b"previous backup contents")

        assert migrate(v4_db) == 1
        assert "backup already exists" in capsys.readouterr().err

        # Database untouched: still v4 with its rows.
        conn = sqlite3.connect(str(v4_db))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 3
        conn.close()
        # The pre-existing backup was not overwritten.
        assert backup.read_bytes() == b"previous backup contents"


class TestMalformedJsonTolerance:
    def test_malformed_json_rows_are_skipped_and_counted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Undecodable / non-list / non-string entity payloads don't abort.

        The migration completes; malformed rows are counted in the report
        and any salvageable string paths still materialize.
        """
        db = tmp_path / "badjson.db"
        ts = "2024-01-01T00:00:00Z"
        conn = sqlite3.connect(str(db))
        conn.executescript(V4_DDL)
        conn.executemany(
            "INSERT INTO decisions (id, title, status, rationale, source, "
            "repo, affected_entities, rejected_alternatives, created_at, "
            "modified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                # JSON object where a list belongs.
                ("d-bad-dict", "T", "accepted", "r", "", "",
                 '{"not": "a list"}', "[]", ts, ts),
                # Undecodable garbage.
                ("d-garbage", "T", "accepted", "r", "", "",
                 "garbage{", "[]", ts, ts),
                # List containing a non-string item next to a valid path.
                ("d-mixed-list", "T", "accepted", "r", "", "",
                 '[42, "src/ok.py"]', "[]", ts, ts),
                ("d-good", "T", "accepted", "r", "", "",
                 '["src/real.py"]', "[]", ts, ts),
                ("d-empty", "T", "accepted", "r", "", "", "[]", "[]", ts, ts),
            ],
        )
        conn.execute(
            "INSERT INTO constraints (id, description, enforced_by, source, "
            "repo, bound_entities, status, created_at) "
            "VALUES ('c-garbage', 'desc', '', '', '', 'garbage{', 'active', ?)",
            (ts,),
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        assert migrate(db) == 0
        report = capsys.readouterr().out
        assert re.search(r"malformed JSON rows:\s+4", report)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        paths = {
            (r["entry_type"], r["entry_id"], r["path"])
            for r in conn.execute("SELECT * FROM entry_paths")
        }
        assert paths == {
            ("decision", "d-good", "src/real.py"),
            ("decision", "d-mixed-list", "src/ok.py"),
        }
        conn.close()
