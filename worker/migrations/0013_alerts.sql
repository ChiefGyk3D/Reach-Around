-- SPDX-License-Identifier: Apache-2.0
-- Owner alert quotas and dedup (issue #252). Shared durable state, so the
-- per-minute limit, the daily budget and the dedup hold across Worker isolates.
--
--   alert_quota  one row per (site, channel, scope, window). `scope` is 'minute'
--                (window_start = the UTC minute in ms) or 'day' (the UTC day in
--                ms; channel 'all' counts alerts, not sends). Taken with one
--                conditional upsert, so concurrent callers cannot overshoot and a
--                refused call writes nothing.
--   alert_dedup  one row per (site, event_hash). event_hash is a salted SHA-256
--                prefix of the event kind and address, kept at most ten minutes
--                (expires_at, ms) and pruned on every write. No address is stored.
--
-- Both tables are bounded: expired rows are deleted on each write, and the Worker
-- refuses a new row at a hard cap (2000 quota rows, 500 dedup rows).
-- The CHECKs are small on purpose (CI's SQLite is older than a developer's).
--
-- Apply AFTER 0011_click_funnel.sql and 0012_click_country_null.sql (#451).
-- Down: DROP TABLE alert_quota; DROP TABLE alert_dedup;
-- RULE: apply with the Worker idle, as for 0007 and 0008.

CREATE TABLE alert_quota (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 64),
  channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 16),
  scope TEXT NOT NULL CHECK (scope IN ('minute', 'day')),
  window_start INTEGER NOT NULL CHECK (window_start >= 0),
  count INTEGER NOT NULL CHECK (count >= 0),
  PRIMARY KEY (site, channel, scope, window_start)
) WITHOUT ROWID, STRICT;

CREATE TABLE alert_dedup (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 64),
  event_hash TEXT NOT NULL CHECK (length(event_hash) = 32),
  expires_at INTEGER NOT NULL CHECK (expires_at >= 0),
  PRIMARY KEY (site, event_hash)
) WITHOUT ROWID, STRICT;
