-- Core tables for the snapshot store. Never edit this file once released.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE observations (
    platform TEXT NOT NULL,
    account TEXT NOT NULL,
    metric TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('snapshot', 'aggregate')),
    day TEXT NOT NULL,
    day_tz TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value REAL NOT NULL CHECK (value >= 0),
    source TEXT NOT NULL CHECK (source IN ('fetched', 'imported', 'typed')),
    access TEXT NOT NULL CHECK (access IN ('public', 'owner_authorized')),
    expires_at TEXT,
    coverage TEXT NOT NULL CHECK (coverage IN ('complete', 'partial')),
    coverage_note TEXT,
    fetched_at TEXT NOT NULL,
    CHECK (access = 'owner_authorized' OR expires_at IS NULL),
    PRIMARY KEY (platform, account, metric, kind, day, source, observed_at)
);
CREATE INDEX observations_expires_at ON observations (expires_at);
CREATE INDEX observations_series ON observations (platform, account, metric, day);
CREATE TABLE runs (
    platform TEXT NOT NULL,
    account TEXT NOT NULL,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
    reason TEXT,
    partial INTEGER NOT NULL DEFAULT 0 CHECK (partial IN (0, 1)),
    PRIMARY KEY (platform, account, ts)
);
CREATE TABLE kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE click_daily (
    day TEXT NOT NULL,
    day_tz TEXT NOT NULL,
    link_id TEXT NOT NULL,
    country TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (count >= 0),
    PRIMARY KEY (day, day_tz, link_id, country)
);
CREATE TABLE click_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_id INTEGER NOT NULL CHECK (last_id >= 0)
);
INSERT INTO click_cursor (id, last_id) VALUES (1, 0);
CREATE TABLE click_batches (
    lo_id INTEGER PRIMARY KEY,
    hi_id INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    bucket_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL
);
CREATE TABLE click_dead_letters (
    lo_id INTEGER NOT NULL,
    hi_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    resolved_at TEXT,
    resolution TEXT CHECK (resolution IN ('applied', 'skipped')),
    PRIMARY KEY (lo_id, hi_id)
);
