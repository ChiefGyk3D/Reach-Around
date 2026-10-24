-- SPDX-License-Identifier: Apache-2.0
-- Page view daily aggregates (Plan 4 Task A3). Three separate marginal
-- tables, one kind of fact each, so a day's browsers, systems, languages and
-- cities can never be recombined into a per-visitor profile.
--
-- No column exists for an address, user agent, referrer URL, coordinates or
-- ASN. The rollup job that fills these tables is a later task (A5).

-- Source and page by country. src_kind as in view_events.
CREATE TABLE view_daily (
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  page TEXT NOT NULL,
  src_kind TEXT NOT NULL CHECK (src_kind IN ('d', 'i', 'r', 'u', 'o')),
  src TEXT NOT NULL,
  country TEXT NOT NULL,
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (day, day_tz, page, src_kind, src, country, bot_class)
) WITHOUT ROWID;

-- One dimension at a time: (dim, value) is a single pair per row.
CREATE TABLE view_dim_daily (
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  dim TEXT NOT NULL CHECK (dim IN ('browser', 'os', 'device', 'lang', 'vp', 'net', 'reason')),
  value TEXT NOT NULL,
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (day, day_tz, dim, value, bot_class)
) WITHOUT ROWID;

-- Geography only; never joined to browser, system or language.
CREATE TABLE view_geo_daily (
  day TEXT NOT NULL,
  day_tz TEXT NOT NULL,
  country TEXT NOT NULL,
  region TEXT NOT NULL,
  city TEXT NOT NULL,
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (day, day_tz, country, region, city, bot_class)
) WITHOUT ROWID;

-- Per-day totals so the three tables can be reconciled against each other.
CREATE TABLE view_rollup_log (
  day TEXT PRIMARY KEY,
  day_tz TEXT NOT NULL,
  raw_views INTEGER NOT NULL,
  views INTEGER NOT NULL,
  uniques INTEGER NOT NULL,
  rows_core INTEGER NOT NULL,
  rows_dim INTEGER NOT NULL,
  rows_geo INTEGER NOT NULL,
  rolled_at INTEGER NOT NULL
) WITHOUT ROWID;
