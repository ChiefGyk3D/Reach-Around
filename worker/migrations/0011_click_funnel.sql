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
-- Per-site row caps (set in the Worker, worker/src/clickfunnel.ts): once a
-- (site, day) holds the cap, or the site-wide daily budget (click_budget) is used
-- up, a new cell goes into ONE overflow row per (site, day) ('overflow-all', with
-- 'other' for a source or 'ZZ' for a country), so rows per (site, day) are at
-- most the cap plus one. A day is SEALED by the hourly job after it closes: the Worker never
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

-- The site-wide write budget of a (site, day): clicks counted so far. Past the Worker's
-- budget no new cell is made (counting goes on in existing cells and the overflow row).
CREATE TABLE IF NOT EXISTS click_budget (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day)
) WITHOUT ROWID;

-- A month that was rolled up (even when it had no cells to add).
CREATE TABLE IF NOT EXISTS click_month_log (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  rolled_at INTEGER NOT NULL,
  PRIMARY KEY (site, month)
) WITHOUT ROWID;
