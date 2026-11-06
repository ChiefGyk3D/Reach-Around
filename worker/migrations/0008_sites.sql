-- SPDX-License-Identifier: Apache-2.0
-- Multi-site analytics (docs/analytics-design.md sections 3.3 to 3.6, issue #101).
--
-- One Worker and one D1 database now serve several sites. Every row says which
-- site it belongs to, and a day is sealed per (site, day). The site id is a
-- short slug set by the Worker from the matched host, never read from the
-- client.
--
--   1. view_events and click_events gain `site` (a plain column add), existing
--      rows become 'links', the site the database served so far.
--   2. view_daily, view_dim_daily, view_geo_daily and view_rollup_log are
--      rebuilt with `site` as the FIRST primary-key column (SQLite cannot add a
--      column to a key), every existing row copied with site = 'links'.
--   3. visitor_seen is emptied (cutover). Its codes were derived without a site;
--      after this change a returning visitor gets a new site-scoped code, so the
--      old codes would count that visitor twice that day. Deploy right after
--      00:05 UTC to keep the overcount to at most one hour of one day.
--   4. Raw timestamps are rounded down to the hour (decision D-A1), existing
--      rows included, so no minute precision outlives this migration.
--   5. The raw `net` column is cleared to 'unknown' (decision D-A7): the network
--      class was always 'unknown' so far (no range data is loaded), nothing is
--      lost, and from now on the purge clears it from every counted raw row.
--   6. The state keys in view_meta that were global become per site.
--
-- There is deliberately no column for an address, user agent, referrer URL,
-- coordinates, postal code or ASN. `site` passes the schema guard in
-- worker/test/views/schema.test.ts.
--
-- RULE (the control for this migration, as for 0007): apply it with the Worker
-- idle, or accept that a view written between two statements keeps the old
-- shape for one run. D1 applies a migration file as one batch, so the table
-- rebuilds are atomic.

ALTER TABLE view_events ADD COLUMN site TEXT NOT NULL DEFAULT 'links'
  CHECK (length(site) BETWEEN 1 AND 20 AND site GLOB '[a-z0-9]*' AND site NOT GLOB '*[^a-z0-9-]*');
CREATE INDEX idx_view_events_site_ts ON view_events (site, ts);
UPDATE view_events SET ts = ts - (ts % 3600000), net = 'unknown';

ALTER TABLE click_events ADD COLUMN site TEXT NOT NULL DEFAULT 'links'
  CHECK (length(site) BETWEEN 1 AND 20 AND site GLOB '[a-z0-9]*' AND site NOT GLOB '*[^a-z0-9-]*');

CREATE TABLE view_daily_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site GLOB '[a-z0-9]*' AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  page TEXT NOT NULL,
  src_kind TEXT NOT NULL CHECK (src_kind IN ('d', 'i', 'r', 'u', 'o')),
  src TEXT NOT NULL,
  country TEXT NOT NULL,
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (site, day, day_tz, page, src_kind, src, country, bot_class)
) WITHOUT ROWID;
INSERT INTO view_daily_new (site, day, day_tz, page, src_kind, src, country, bot_class, views, uniques)
  SELECT 'links', day, day_tz, page, src_kind, src, country, bot_class, views, uniques FROM view_daily;
DROP TABLE view_daily;
ALTER TABLE view_daily_new RENAME TO view_daily;

CREATE TABLE view_dim_daily_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site GLOB '[a-z0-9]*' AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  dim TEXT NOT NULL CHECK (dim IN ('browser', 'os', 'device', 'lang', 'vp', 'net', 'reason')),
  value TEXT NOT NULL,
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (site, day, day_tz, dim, value, bot_class)
) WITHOUT ROWID;
INSERT INTO view_dim_daily_new (site, day, day_tz, dim, value, bot_class, views, uniques)
  SELECT 'links', day, day_tz, dim, value, bot_class, views, uniques FROM view_dim_daily;
DROP TABLE view_dim_daily;
ALTER TABLE view_dim_daily_new RENAME TO view_dim_daily;

CREATE TABLE view_geo_daily_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site GLOB '[a-z0-9]*' AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  country TEXT NOT NULL,
  region TEXT NOT NULL,
  city TEXT NOT NULL,
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (site, day, day_tz, country, region, city, bot_class)
) WITHOUT ROWID;
INSERT INTO view_geo_daily_new (site, day, day_tz, country, region, city, bot_class, views, uniques)
  SELECT 'links', day, day_tz, country, region, city, bot_class, views, uniques FROM view_geo_daily;
DROP TABLE view_geo_daily;
ALTER TABLE view_geo_daily_new RENAME TO view_geo_daily;

CREATE TABLE view_rollup_log_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site GLOB '[a-z0-9]*' AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  raw_views INTEGER NOT NULL,
  views INTEGER NOT NULL,
  uniques INTEGER NOT NULL,
  rows_core INTEGER NOT NULL,
  rows_dim INTEGER NOT NULL,
  rows_geo INTEGER NOT NULL,
  rolled_at INTEGER NOT NULL,
  max_id INTEGER NOT NULL DEFAULT 0,
  rerolls INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (site, day)
) WITHOUT ROWID;
INSERT INTO view_rollup_log_new (site, day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at, max_id, rerolls)
  SELECT 'links', day, day_tz, raw_views, views, uniques, rows_core, rows_dim, rows_geo, rolled_at, max_id, rerolls FROM view_rollup_log;
DROP TABLE view_rollup_log;
ALTER TABLE view_rollup_log_new RENAME TO view_rollup_log;

DELETE FROM visitor_seen;

UPDATE view_meta SET key = 'rolled_through:links' WHERE key = 'rolled_through';
UPDATE view_meta SET key = 'rollup_failed_day:links' WHERE key = 'rollup_failed_day';
