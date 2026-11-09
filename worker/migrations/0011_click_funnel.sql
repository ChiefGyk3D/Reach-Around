-- SPDX-License-Identifier: Apache-2.0
-- Funnel rollups, aggregate only (issues #331 and #409).
--
-- Two marginal counters, one pair each, never a three-way cell:
--
--   click_src_daily      site x day x link x source label    (clicks)
--   click_country_daily  site x day x link x country         (clicks)
--
-- The click route increments them when a click arrives, so country by link
-- covers EVERY click, whether or not it carries a source (a raw click row holds
-- a source or a country, never both, so the pair cannot be read back from raw
-- rows). A counter row holds a count and three closed labels. It holds no
-- timestamp finer than a day, no identifier and no address. The source counter
-- is kept only for a site that lists `sources`; a click without a label counts
-- as 'direct'. In the country counter 'ZZ' means unknown or folded.
--
-- Row bounds (set in the Worker, worker/src/clickfunnel.ts): a general pool of cells per
-- (site, day), a few reserved cells per destination, and an hourly slice of a new-cell budget
-- (click_budget). Anything past them goes into ONE overflow row per (site, day) ('overflow-all',
-- with 'other' for a source or 'ZZ' for a country) and the day is marked in click_capacity. A day is SEALED by the hourly job after it closes (quiet days too, the proof a day is closed): the Worker never
-- increments a sealed day (click_seal), so a closed day cannot change.
--
-- Retention: per-day cells and seals are removed once they are 90 days old, but only once
-- their month is in the monthly tables (a hard ceiling drops them anyway). The
-- monthly tables hold the sum of a closed month, with a fold cap, and are kept
-- for 25 months. The report applies its floor after summing.
--
-- CHECK clauses are small on purpose (CI's SQLite has a parser stack limit).
-- Nothing here touches an existing table. RULE: apply with the Worker idle, as for 0008.

CREATE TABLE IF NOT EXISTS click_src_daily (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  src TEXT NOT NULL CHECK (length(src) BETWEEN 1 AND 30 AND src NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day, link_id, src)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_country_daily (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  country TEXT NOT NULL CHECK (length(country) = 2 AND country NOT GLOB '*[^A-Z]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day, link_id, country)
) WITHOUT ROWID;

-- A sealed (site, day): the Worker adds nothing to it any more.
CREATE TABLE IF NOT EXISTS click_seal (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  sealed_at INTEGER NOT NULL,
  PRIMARY KEY (site, day)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_src_monthly (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  src TEXT NOT NULL CHECK (length(src) BETWEEN 1 AND 30 AND src NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, month, link_id, src)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_country_monthly (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  country TEXT NOT NULL CHECK (length(country) = 2 AND country NOT GLOB '*[^A-Z]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, month, link_id, country)
) WITHOUT ROWID;

-- The new-cell budget of a (site, day, UTC hour): clicks counted so far in that hour. Each
-- hour has its own slice, so flooding in one hour cannot stop new cells for the rest of the day.
CREATE TABLE IF NOT EXISTS click_budget (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  hour INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day, hour)
) WITHOUT ROWID;

-- A capacity counter: counting capacity ran out on this (site, day) (an hourly slice of the budget
-- or the cell pools). first_hour is the UTC hour it began and folded the clicks that went into the
-- overflow row (an upper bound). The report carries only a coarse status (none, under or over 5 percent of
-- the period's clicks) and a warning; exact figures are for the operator only.
CREATE TABLE IF NOT EXISTS click_capacity (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  marked_at INTEGER NOT NULL,
  first_hour INTEGER NOT NULL CHECK (first_hour BETWEEN 0 AND 23),
  folded INTEGER NOT NULL CHECK (folded >= 1),
  PRIMARY KEY (site, day)
) WITHOUT ROWID;

-- The first day the funnel knew a site (its first cell, or the first run after it was configured),
-- so the hourly job can seal quiet days and log empty months from then on, with no click needed.
CREATE TABLE IF NOT EXISTS click_funnel_state (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  from_day TEXT NOT NULL CHECK (length(from_day) = 10),
  PRIMARY KEY (site)
) WITHOUT ROWID;

-- A month that was rolled up (even when it had no cells to add).
CREATE TABLE IF NOT EXISTS click_month_log (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  rolled_at INTEGER NOT NULL,
  exhausted_days INTEGER NOT NULL CHECK (exhausted_days >= 0),
  folded_clicks INTEGER NOT NULL CHECK (folded_clicks >= 0),
  PRIMARY KEY (site, month)
) WITHOUT ROWID;
