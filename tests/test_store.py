"""Tests for the relational memory store (Spec 29 schema v5)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from mementic.store import SCHEMA_VERSION, MementicStore, successor_timestamp


@pytest.fixture
def store() -> Generator[MementicStore, None, None]:
    """Fresh in-memory store per test."""
    s = MementicStore(db_path=":memory:")
    yield s
    s.close()


def _add_decision(
    store: MementicStore,
    entry_id: str = "d1",
    rationale: str = "Better performance",
    created_at: str = "2024-01-01T00:00:00Z",
    paths: list[str] | None = None,
) -> None:
    """Seed one decision with sane defaults."""
    store.add_decision(
        entry_id=entry_id,
        title=f"Title {entry_id}",
        rationale=rationale,
        created_at=created_at,
        paths=paths or [],
    )


def _add_constraint(
    store: MementicStore,
    entry_id: str = "c1",
    description: str = "All I/O must be async",
    enforced_by: str = "",
    created_at: str = "2024-01-01T00:00:00Z",
    paths: list[str] | None = None,
) -> None:
    """Seed one constraint with sane defaults."""
    store.add_constraint(
        entry_id=entry_id,
        description=description,
        enforced_by=enforced_by,
        created_at=created_at,
        paths=paths or [],
    )


# ── Schema ──────────────────────────────────────────────────────────


def _raw_conn(store: MementicStore) -> sqlite3.Connection:
    """Return the store's connection for schema-level assertions."""
    conn = store._conn
    assert conn is not None
    return conn


class TestSchema:
    def test_fresh_database_gets_schema_v5(self, tmp_path: Path) -> None:
        db = tmp_path / "graph.db"
        store = MementicStore(db_path=db)
        version = _raw_conn(store).execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION == 5
        tables = {
            r["name"]
            for r in _raw_conn(store).execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"decisions", "constraints", "entry_paths", "deprecations",
                "audit_log"}.issubset(tables)
        # Legacy v4 tables are gone.
        assert "entities" not in tables and "edges" not in tables

    def test_no_status_column_anywhere(self, store: MementicStore) -> None:
        for table in ("decisions", "constraints"):
            cols = {
                r["name"]
                for r in _raw_conn(store).execute(f"PRAGMA table_info({table})")
            }
            assert "status" not in cols

    def test_foreign_keys_pragma_enabled(self, store: MementicStore) -> None:
        fk = _raw_conn(store).execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_opening_v4_database_raises_with_migration_hint(
        self, tmp_path: Path,
    ) -> None:
        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()
        with pytest.raises(RuntimeError, match="migrate_v4_to_v5"):
            MementicStore(db_path=db)


# ── Basic CRUD ──────────────────────────────────────────────────────


class TestEntryCrud:
    def test_add_and_get_roundtrip(self, store: MementicStore) -> None:
        _add_decision(store, "d1", paths=["src/a.py", "src/b.py"])
        _add_constraint(store, "c1", enforced_by="d1", paths=["src/a.py"])
        d = store.get_decision("d1")
        assert d is not None
        assert d.title == "Title d1"
        assert d.paths == ["src/a.py", "src/b.py"]
        c = store.get_constraint("c1")
        assert c is not None
        assert c.enforced_by == "d1"
        assert c.paths == ["src/a.py"]

    def test_get_unknown_entry_returns_none(self, store: MementicStore) -> None:
        assert store.get_decision("nope") is None
        assert store.get_constraint("nope") is None

    def test_get_entry_type(self, store: MementicStore) -> None:
        _add_decision(store, "d1")
        _add_constraint(store, "c1")
        assert store.get_entry_type("d1") == "decision"
        assert store.get_entry_type("c1") == "constraint"
        assert store.get_entry_type("zz") is None

    def test_re_add_overwrites_and_reports_modify(
        self, store: MementicStore,
    ) -> None:
        first_action = store.add_decision(
            entry_id="d1", title="T", rationale="r",
            created_at="2024-01-01T00:00:00Z",
        )
        assert first_action == "add"
        action = store.add_decision(
            entry_id="d1", title="T2", rationale="r2",
            created_at="2024-01-02T00:00:00Z",
        )
        assert action == "modify"
        d = store.get_decision("d1")
        assert d is not None and d.rationale == "r2"

    def test_replace_paths_on_re_add(self, store: MementicStore) -> None:
        _add_decision(store, "d1", paths=["src/old.py"])
        store.add_decision(
            entry_id="d1", title="T", rationale="r",
            created_at="2024-01-02T00:00:00Z", paths=["src/new.py"],
        )
        d = store.get_decision("d1")
        assert d is not None
        assert d.paths == ["src/new.py"]

    def test_duplicate_paths_deduplicated_in_order(
        self, store: MementicStore,
    ) -> None:
        store.add_decision(
            entry_id="d1", title="T", rationale="r",
            created_at="2024-01-01T00:00:00Z",
            paths=["src/a.py", "src/b.py", "src/a.py"],
        )
        d = store.get_decision("d1")
        assert d is not None
        assert d.paths == ["src/a.py", "src/b.py"]

    def test_cross_type_collision_raises_value_error(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "shared-id")
        with pytest.raises(ValueError, match="already exists as a decision"):
            _add_constraint(store, "shared-id")
        _add_constraint(store, "other")
        with pytest.raises(ValueError, match="already exists as a constraint"):
            _add_decision(store, "other")


# ── FK integrity ────────────────────────────────────────────────────


class TestForeignKeyEnforcement:
    def test_dangling_enforced_by_raises_integrity_error(
        self, store: MementicStore,
    ) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            _add_constraint(store, "c1", enforced_by="missing-decision")

    def test_valid_enforced_by_accepted(self, store: MementicStore) -> None:
        _add_decision(store, "d1")
        _add_constraint(store, "c1", enforced_by="d1")  # no raise
        c = store.get_constraint("c1")
        assert c is not None and c.enforced_by == "d1"

    def test_standalone_constraint_stored_as_null_enforced_by(
        self, store: MementicStore,
    ) -> None:
        _add_constraint(store, "solo", enforced_by="")
        row = _raw_conn(store).execute(
            "SELECT enforced_by FROM constraints WHERE id = 'solo'"
        ).fetchone()
        assert row["enforced_by"] is None  # NULL storage...
        c = store.get_constraint("solo")
        assert c is not None and c.enforced_by == ""  # ..."" at read time


# ── UPSERT keeps FK children alive ──────────────────────────────────


class TestUpsertPreservesChildren:
    def test_re_add_decision_with_dependent_constraint_succeeds(
        self, store: MementicStore,
    ) -> None:
        """Re-adding a decision that constraints point at must not die.

        INSERT OR REPLACE deletes+reinserts the row, tripping the
        enforced_by foreign key; the UPSERT updates in place instead.
        """
        _add_decision(store, "d1", rationale="v1")
        _add_constraint(store, "c1", enforced_by="d1", paths=["src/a.py"])
        action = store.add_decision(
            entry_id="d1", title="Title d1", rationale="v2",
            created_at="2024-01-02T00:00:00Z",
        )
        assert action == "modify"
        d = store.get_decision("d1")
        assert d is not None and d.rationale == "v2"
        # The dependent constraint survived and still resolves.
        c = store.get_constraint("c1")
        assert c is not None and c.enforced_by == "d1"
        assert [x.id for x in store.get_live_constraints()] == ["c1"]

    def test_overwrite_constraint_with_paths_succeeds(
        self, store: MementicStore,
    ) -> None:
        """Overwriting a constraint while paths exist must not raise."""
        _add_decision(store, "d1")
        _add_constraint(store, "c1", description="v1", paths=["src/a.py"])
        action = store.add_constraint(
            entry_id="c1", description="v2", enforced_by="d1",
            created_at="2024-01-02T00:00:00Z", paths=["src/b.py"],
        )
        assert action == "modify"
        c = store.get_constraint("c1")
        assert c is not None
        assert c.description == "v2"
        assert c.paths == ["src/b.py"]


# ── Derived liveness ────────────────────────────────────────────────


class TestDerivedLiveness:
    def test_deprecated_entry_excluded_from_live_queries(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "d1")
        _add_constraint(store, "c1")
        result = store.deprecate("decision", "d1", reason="obsolete")
        assert result["status"] == "deprecated"
        assert [d.id for d in store.get_live_decisions()] == []
        assert [d.id for d in store.get_all_decisions()] == ["d1"]
        assert [c.id for c in store.get_live_constraints()] == ["c1"]
        assert store.is_deprecated("decision", "d1") is True
        assert store.is_deprecated("constraint", "c1") is False

    def test_deprecate_twice_reports_already_deprecated(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "d1")
        store.deprecate("decision", "d1")
        result = store.deprecate("decision", "d1")
        assert result["status"] == "already_deprecated"

    def test_deprecate_missing_entry_reports_not_found(
        self, store: MementicStore,
    ) -> None:
        result = store.deprecate("decision", "ghost")
        assert result["status"] == "not_found"

    def test_readded_incarnation_derives_live_again(
        self, store: MementicStore,
    ) -> None:
        """Liveness correlates tombstone time against created_at.

        A deprecations row only kills incarnations it postdates; re-adding
        a slug creates a newer incarnation that derives as live. Re-adds
        are stamped at call time (as the CLI does), so they postdate the
        tombstone.
        """
        from mementic.format import timestamp

        _add_decision(store, "d1", rationale="old truth",
                      created_at="2024-01-01T00:00:00Z")
        store.deprecate("decision", "d1", reason="superseded")
        assert store.get_live_decisions() == []
        # Re-add with a current timestamp: new incarnation, tombstone
        # predates it.
        _add_decision(store, "d1", rationale="new truth",
                      created_at=timestamp())
        live = store.get_live_decisions()
        assert [d.id for d in live] == ["d1"]
        assert live[0].rationale == "new truth"
        assert store.is_deprecated("decision", "d1") is False

    def test_superseded_by_column_reports_successor_or_retired(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "old-d", created_at="2024-01-01T00:00:00Z")
        _add_decision(store, "new-d", created_at="2024-02-01T00:00:00Z")
        _add_constraint(store, "dead-c", created_at="2024-01-01T00:00:00Z")
        store.deprecate("decision", "old-d", replaced_by="new-d")
        store.deprecate("constraint", "dead-c")

        pairs = {d.id: sb for d, sb in store.get_decisions_with_liveness()}
        assert pairs["old-d"] == "new-d"
        assert pairs["new-d"] is None

        cpairs = {c.id: sb for c, sb in store.get_constraints_with_liveness()}
        assert cpairs["dead-c"] == "retired"


# ── Succession timestamps (structural ordering) ─────────────────────


class TestSuccessionTimestamps:
    def test_record_succession_returns_tombstone_stamp(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "d1")
        stamp = store.record_succession("decision", "d1", replaced_by="d1")
        records = store.get_deprecations_for_entry("decision", "d1")
        assert records[0]["at"] == stamp

    def test_deprecate_result_carries_tombstone_stamp(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "d1")
        result = store.deprecate("decision", "d1", reason="done")
        assert result["status"] == "deprecated"
        records = store.get_deprecations_for_entry("decision", "d1")
        assert result["at"] == records[0]["at"]

    def test_successor_timestamp_strictly_after_any_stamp(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "d1")
        for _ in range(5):
            stamp = store.deprecate("decision", "d1", replaced_by="d1")["at"]
            created_at = successor_timestamp(stamp)
            # THE invariant: the fresh incarnation always postdates its
            # tombstone, regardless of wall-clock granularity.
            assert created_at > stamp
            store.add_decision(
                entry_id="d1", title="T", rationale="again",
                created_at=created_at,
            )
            live = store.get_live_decisions()
            assert [d.id for d in live] == ["d1"]

    def test_identical_clock_reads_still_derive_live(
        self, store: MementicStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The Windows-clock-collision regression: two wall-clock reads
        returning the SAME string must not strand a dead incarnation."""
        import mementic.store as store_mod

        frozen = "2026-01-01T00:00:00+00:00"
        monkeypatch.setattr(store_mod, "utc_now", lambda: frozen)

        _add_decision(store, "d1", rationale="v1",
                      created_at="2024-01-01T00:00:00Z")
        result = store.deprecate("decision", "d1", reason="x", replaced_by="d1")
        assert result["at"] == frozen  # tombstone came from the frozen clock
        # A naive re-read of the clock would return the identical string;
        # deriving created_at from the tombstone instead makes liveness
        # structural.
        created_at = successor_timestamp(result["at"])
        store.add_decision(
            entry_id="d1", title="T", rationale="v2", created_at=created_at,
        )
        live = store.get_live_decisions()
        assert [d.id for d in live] == ["d1"]
        d = store.get_decision("d1")
        assert d is not None and d.created_at > result["at"]

    def test_rapid_redeprecate_does_not_collide_on_pk(
        self, store: MementicStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deprecate -> re-add -> deprecate again within one clock tick.

        The deprecations PK includes ``at``; two lifecycle events for the
        same entry with an identical coarse-clock stamp used to raise an
        unhandled IntegrityError. The insert now bumps the stamp and
        retries, so both tombstones coexist.
        """
        import mementic.store as store_mod

        frozen = "2026-06-01T12:00:00+00:00"
        monkeypatch.setattr(store_mod, "utc_now", lambda: frozen)

        _add_decision(store, "d1", rationale="v1",
                      created_at="2024-01-01T00:00:00Z")
        first = store.deprecate("decision", "d1", reason="first death")
        created_at = successor_timestamp(first["at"])
        store.add_decision(
            entry_id="d1", title="T", rationale="v2", created_at=created_at,
        )
        second = store.deprecate("decision", "d1", reason="second death")
        assert second["status"] == "deprecated"
        assert second["at"] > first["at"]
        records = store.get_deprecations_for_entry("decision", "d1")
        assert len(records) == 2


# ── Path queries (exact default / prefix opt-in) ────────────────────


class TestPathQueries:
    @pytest.fixture(autouse=True)
    def _seeded(self, store: MementicStore) -> None:
        _add_decision(
            store, "d-io", paths=["src/services/io.py"],
            created_at="2024-01-01T00:00:00Z",
        )
        _add_decision(
            store, "d-models", paths=["src/models/base.py", "src/models/types.py"],
            created_at="2024-02-01T00:00:00Z",
        )
        _add_constraint(store, "c-io", paths=["src/services/io.py"])
        _add_constraint(store, "c-models", paths=["src/models/base.py"])

    def test_exact_match_default(self, store: MementicStore) -> None:
        decisions = store.get_decisions_for_path("src/services/io.py")
        assert [(d.id, overlap) for d, overlap in decisions] == [("d-io", 1)]
        assert [c.id for c in store.get_constraints_for_path(
            "src/services/io.py")] == ["c-io"]

    def test_prefix_is_opt_in(self, store: MementicStore) -> None:
        # Exact match does not match directory prefixes.
        assert store.get_decisions_for_path("src/services/") == []
        # Prefix mode does.
        ids = {d.id for d, _ in store.get_decisions_for_path(
            "src/services/", prefix=True)}
        assert ids == {"d-io"}
        assert [c.id for c in store.get_constraints_for_path(
            "src/models/", prefix=True)] == ["c-models"]

    def test_overlap_counts_matching_paths(self, store: MementicStore) -> None:
        results = store.get_decisions_for_path("src/models/", prefix=True)
        by_id = {d.id: overlap for d, overlap in results}
        assert by_id["d-models"] == 2

    def test_no_substring_or_suffix_matches(self, store: MementicStore) -> None:
        assert store.get_decisions_for_path("io.py") == []
        assert store.get_decisions_for_path(
            "src/models/base.py.method", prefix=True) == []

    def test_prefix_does_not_substring_match_mid_tree(
        self, store: MementicStore,
    ) -> None:
        """Prefix matching anchors at the start of the stored path."""
        _add_decision(
            store, "d-docs", paths=["docs/src/services/io.py"],
            created_at="2024-03-01T00:00:00Z",
        )
        # "src/services/" must NOT match the nested docs/ copy...
        ids = {
            d.id for d, _ in store.get_decisions_for_path(
                "src/services/", prefix=True)
        }
        assert "d-docs" not in ids
        assert ids == {"d-io"}
        cids = {
            c.id for c in store.get_constraints_for_path(
                "src/services/", prefix=True)
        }
        assert cids == {"c-io"}
        # ...while the exact path still matches exactly and via prefix.
        exact = store.get_decisions_for_path("docs/src/services/io.py")
        assert [d.id for d, _ in exact] == ["d-docs"]
        pref = store.get_decisions_for_path("docs/src/", prefix=True)
        assert [d.id for d, _ in pref] == ["d-docs"]

    def test_path_exists_filters_dead_paths_and_scores_overlap(
        self, store: MementicStore,
    ) -> None:
        """The optional predicate drops dead paths; overlap counts survivors."""
        _add_decision(
            store, "d-mixed", paths=["live.py", "dead.py", "other.py"],
            created_at="2024-01-01T00:00:00Z",
        )
        _add_constraint(store, "c-dead-path", paths=["gone.py"])

        def only_live(path: str) -> bool:
            return path != "dead.py" and path != "gone.py"

        decisions = store.get_decisions_for_path(
            "", prefix=True, path_exists=only_live)
        by_id = {d.id: overlap for d, overlap in decisions}
        # Only surviving matched paths count toward overlap
        # (dead.py filtered out, live.py + other.py survive).
        assert by_id["d-mixed"] == 2
        # Entries whose every path is dead are dropped entirely.
        assert "c-dead-path" not in {
            c.id for c in store.get_constraints_for_path(
                "", prefix=True, path_exists=only_live)
        }
        # Without the predicate, nothing is filtered (default unchanged):
        # all seeded entries plus the new ones match the empty prefix.
        unfiltered = store.get_decisions_for_path("", prefix=True)
        assert {d.id for d, _ in unfiltered} == {
            "d-io", "d-models", "d-mixed",
        }
        constraints_unfiltered = store.get_constraints_for_path(
            "", prefix=True)
        assert {c.id for c in constraints_unfiltered} == {
            "c-io", "c-models", "c-dead-path",
        }

    def test_dead_entries_never_match_paths(self, store: MementicStore) -> None:
        store.deprecate("decision", "d-io")
        assert store.get_decisions_for_path("src/services/io.py") == []

    def test_get_all_paths_distinct_insertion_order(
        self, store: MementicStore,
    ) -> None:
        assert store.get_all_paths() == [
            "src/services/io.py",
            "src/models/base.py",
            "src/models/types.py",
        ]


# ── History / lifecycle queries ─────────────────────────────────────


class TestHistoryQueries:
    def test_single_record_lookup(self, store: MementicStore) -> None:
        _add_decision(store, "d1")
        store.deprecate("decision", "d1", reason="moved to services")
        records = store.get_deprecations_for_entry("decision", "d1")
        assert len(records) == 1
        assert records[0]["reason"] == "moved to services"
        assert records[0]["replaced_by"] is None
        assert records[0]["at"]

    def test_transitive_chain_follows_replaced_by(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "a", created_at="2024-01-01T00:00:00Z")
        _add_decision(store, "b", created_at="2024-02-01T00:00:00Z")
        _add_decision(store, "c", created_at="2024-03-01T00:00:00Z")
        store.deprecate("decision", "a", reason="v1", replaced_by="b")
        store.deprecate("decision", "b", reason="v2", replaced_by="c")

        chain = store.get_replacement_chain("decision", "a")
        trail = [(r["entry_id"], r["replaced_by"]) for r in chain]
        assert ("a", "b") in trail
        assert ("b", "c") in trail
        # Chronological order across the chain.
        ats = [r["at"] for r in chain]
        assert ats == sorted(ats)

    def test_self_replacement_from_auto_resolve_stays_in_chain(
        self, store: MementicStore,
    ) -> None:
        """Same-slug replace writes a tombstone whose successor is itself."""
        _add_decision(store, "x", rationale="v1",
                      created_at="2024-01-01T00:00:00Z")
        store.deprecate("decision", "x", reason="superseded", replaced_by="x")
        chain = store.get_replacement_chain("decision", "x")
        assert len(chain) == 1
        assert chain[0]["replaced_by"] == "x"

    def test_record_succession_adds_pointer_before_readd(
        self, store: MementicStore,
    ) -> None:
        """Replace-path succession rows precede the new incarnation."""
        from mementic.format import timestamp

        _add_decision(store, "d1", rationale="v1",
                      created_at="2024-01-01T00:00:00Z")
        store.deprecate("decision", "d1", reason="manual kill")
        store.record_succession(
            "decision", "d1", replaced_by="d1",
            reason="superseded by re-added entry",
        )
        # New incarnation stamped after the succession row derives live.
        _add_decision(store, "d1", rationale="v2", created_at=timestamp())
        assert [d.id for d in store.get_live_decisions()] == ["d1"]
        records = store.get_deprecations_for_entry("decision", "d1")
        reasons = [r["reason"] for r in records]
        assert reasons == ["manual kill", "superseded by re-added entry"]

    def test_full_table_newest_first(self, store: MementicStore) -> None:
        _add_decision(store, "d1", created_at="2024-01-01T00:00:00Z")
        _add_constraint(store, "c1", created_at="2024-01-01T00:00:00Z")
        store.deprecate("decision", "d1")
        store.deprecate("constraint", "c1")
        rows = store.get_all_deprecations()
        assert [r["entry_id"] for r in rows] == ["c1", "d1"]

    def test_find_deprecation_ignores_prior_incarnations(
        self, store: MementicStore,
    ) -> None:
        from mementic.format import timestamp

        _add_decision(store, "d1", created_at="2024-01-01T00:00:00Z")
        store.deprecate("decision", "d1", reason="old death")
        # Re-add at call time (as the CLI does): the prior tombstone now
        # belongs to a replaced incarnation.
        _add_decision(store, "d1", created_at=timestamp())
        assert store.find_deprecation("decision", "d1") is None
        assert store.is_deprecated("decision", "d1") is False


# ── Validation & connection lifecycle ───────────────────────────────


class TestValidationAndLifecycle:
    @pytest.mark.parametrize("bad_type", ["entity", "", "DECISION", "rule"])
    def test_deprecate_rejects_unknown_entry_type(
        self, store: MementicStore, bad_type: str,
    ) -> None:
        with pytest.raises(ValueError, match="entry_type must be"):
            store.deprecate(bad_type, "whatever")

    def test_record_succession_rejects_unknown_entry_type(
        self, store: MementicStore,
    ) -> None:
        with pytest.raises(ValueError, match="entry_type must be"):
            store.record_succession("entity", "x", replaced_by="y")

    def test_methods_raise_runtime_error_after_close(
        self, tmp_path: Path,
    ) -> None:
        """Closed stores raise RuntimeError instead of asserting."""
        s = MementicStore(db_path=tmp_path / "closed.db")
        s.close()
        with pytest.raises(RuntimeError, match="store connection is closed"):
            s.get_live_decisions()
        # close() stays safe to call twice.
        s.close()


# ── Stats & audit ───────────────────────────────────────────────────


class TestStatsAndAudit:
    def test_stats_keys_and_counts(self, store: MementicStore) -> None:
        assert store.stats() == {
            "decisions": 0, "constraints": 0, "paths": 0, "deprecated": 0,
        }
        _add_decision(store, "d1", paths=["src/a.py"])
        _add_decision(store, "d2")
        _add_constraint(store, "c1")
        store.deprecate("decision", "d2")
        stats = store.stats()
        assert stats == {
            "decisions": 2, "constraints": 1, "paths": 1, "deprecated": 1,
        }

    def test_audit_entries_written_for_add_and_deprecate(
        self, store: MementicStore,
    ) -> None:
        _add_decision(store, "d1")
        store.deprecate("decision", "d1", reason="done", replaced_by=None)
        entries = store.get_audit_entries(limit=10)
        actions = [e["action"] for e in entries]
        assert actions[0] == "deprecate"  # newest first
        assert "add" in actions
        details = json.loads(entries[0]["details"])
        assert details["reason"] == "done"

    def test_file_backed_store_roundtrip(self, tmp_path: Path) -> None:
        db = tmp_path / "graph.db"
        s1 = MementicStore(db_path=db)
        _add_decision(s1, "persisted")
        s1.close()
        s2 = MementicStore(db_path=db)
        assert [d.id for d in s2.get_live_decisions()] == ["persisted"]
        s2.close()
