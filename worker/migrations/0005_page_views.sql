-- SPDX-License-Identifier: Apache-2.0
-- Page views (Plan 4 Task A3). Raw, short-lived tables for the write-only
-- POST /e/v/:pageId route.
--
-- There is deliberately no column for an address, user agent, referrer URL,
-- coordinates or ASN: what is not stored cannot leak. Only small derived
-- categories (and a daily keyed hash of the address in its own table) exist.
-- If a later change needs another column, it is a new migration and it must
-- pass the schema guard in worker/test/views/schema.test.ts.
--
-- Numbering: 0002 to 0004 are claimed by other plans (click autoincrement,
-- kit, owner nonces). Gaps are harmless; migrations apply in filename order.

-- One row per accepted view. Source kinds: d direct, i internal (another tab
-- of this site), r referrer host, u utm label, o other (rollup only).
-- Bot classes: h human-likely, v verified-crawler (claims only), a automated,
-- s suspicious. `reason` is a value from the closed list in botclass.ts.
CREATE TABLE view_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  page TEXT NOT NULL,
  src_kind TEXT NOT NULL CHECK (src_kind IN ('d', 'i', 'r', 'u', 'o')),
  src TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  region TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  browser TEXT NOT NULL DEFAULT 'other',
  bmajor INTEGER NOT NULL DEFAULT 0,
  os TEXT NOT NULL DEFAULT 'other',
  device TEXT NOT NULL DEFAULT 'other',
  lang TEXT NOT NULL DEFAULT '',
  vp TEXT NOT NULL DEFAULT '',
  net TEXT NOT NULL DEFAULT 'unknown',
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  reason TEXT NOT NULL,
  first INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_view_events_ts ON view_events (ts);

-- The daily keyed hash of the connection address, humans only, current UTC
-- day. No other column and no key shared with view_events, so a hash cannot
-- be joined to a row. The daily job deletes every row before today.
CREATE TABLE visitor_seen (
  day TEXT NOT NULL,
  h TEXT NOT NULL,
  PRIMARY KEY (day, h)
) WITHOUT ROWID;

-- Small key/value state: rolled_through, rollup_last_ok, net_status, hourly
-- write-budget counters, and similar. Never request data.
CREATE TABLE view_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;
