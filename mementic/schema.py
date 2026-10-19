"""SQLite schema definition and canonical timestamp helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 5
DEFAULT_AUDIT_LIMIT = 20

SCHEMA_DDL = """
CREATE TABLE decisions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    rationale  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE constraints (
    id          TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    enforced_by TEXT DEFAULT '' REFERENCES decisions(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE entry_paths (
    entry_type TEXT NOT NULL CHECK (entry_type IN ('decision','constraint')),
    entry_id   TEXT NOT NULL,
    path       TEXT NOT NULL,
    PRIMARY KEY (entry_type, entry_id, path)
);
CREATE INDEX idx_entry_paths_path ON entry_paths(path);

CREATE TABLE deprecations (
    entry_type  TEXT NOT NULL CHECK (entry_type IN ('decision','constraint')),
    entry_id    TEXT NOT NULL,
    replaced_by TEXT,               -- NULL = retired without successor
    reason      TEXT DEFAULT '',
    at          TEXT NOT NULL,
    PRIMARY KEY (entry_type, entry_id, at)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    action        TEXT NOT NULL,
    details       TEXT DEFAULT '{}',
    source        TEXT DEFAULT '',
    timestamp     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_entity
    ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_time
    ON audit_log(timestamp);
"""

SET_USER_VERSION_SQL = f"PRAGMA user_version = {SCHEMA_VERSION}"

LIVE_WHERE = (
    "NOT EXISTS ("
    "  SELECT 1 FROM deprecations dp"
    "  WHERE dp.entry_type = :etype"
    "    AND dp.entry_id = {alias}.id"
    "    AND dp.at > {alias}.created_at)"
)


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string.

    The single canonical clock for every stored timestamp, so timestamp
    comparisons stay lexicographically safe.
    """
    return datetime.now(timezone.utc).isoformat()


def successor_timestamp(tombstone_at: str) -> str:
    """Return ``tombstone_at`` advanced by one microsecond.

    A successor incarnation's ``created_at`` must compare greater than the
    tombstone stamping its predecessor even when two consecutive wall-clock
    reads return identical strings. Deriving the successor stamp from the
    tombstone makes that ordering structural instead of probabilistic.
    """
    moment = datetime.fromisoformat(tombstone_at)
    return (moment + timedelta(microseconds=1)).isoformat()
