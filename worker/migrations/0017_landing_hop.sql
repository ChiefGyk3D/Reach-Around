-- SPDX-License-Identifier: Apache-2.0
-- Landing and hop funnel counters, aggregate only (issue #566, design: docs/analytics-funnel-tables-design.md).
--
-- Two more marginal counters beside click_src_daily and click_country_daily, each with ONE closed label:
--
--   click_landing_daily  site x day x link x kind x landing   (the page of our site the click was made on)
--   click_hop_daily      site x day x link x kind x hop       (the surface of ours the visitor came from, one step)
--
-- Both labels come from per-site lists in the Worker config (`landings`, `hops`); a value not on the
-- list is 'other', a missing or malformed one is 'none'. The tables are three separate marginals of the
-- same clicks and are never joined: no table here holds two label columns, no timestamp finer than a UTC
-- day, no identifier and no address. The monthly twins hold the sum of a closed month with a fold cap.
-- The same seal (click_seal) closes all four day counters.
--
-- click_budget_lh is a SEPARATE hourly slice of the new-cell budget for these two tables, so a flood of
-- source or country labels cannot use up their budget, and the reverse. click_landing_state holds the
-- first day these tables had cells for a site: a period before it is "not collected", not zero.
--
-- CHECK clauses are small on purpose (CI's SQLite has a parser stack limit). Nothing here touches an
-- existing table. RULE: apply with the Worker idle, as for 0008.

CREATE TABLE IF NOT EXISTS click_landing_daily (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  destination_kind TEXT NOT NULL CHECK (destination_kind IN ('link', 'social_icon')),
  landing TEXT NOT NULL CHECK (length(landing) BETWEEN 1 AND 30 AND landing NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day, link_id, destination_kind, landing)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_landing_monthly (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  destination_kind TEXT NOT NULL CHECK (destination_kind IN ('link', 'social_icon')),
  landing TEXT NOT NULL CHECK (length(landing) BETWEEN 1 AND 30 AND landing NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, month, link_id, destination_kind, landing)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_hop_daily (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  destination_kind TEXT NOT NULL CHECK (destination_kind IN ('link', 'social_icon')),
  hop TEXT NOT NULL CHECK (length(hop) BETWEEN 1 AND 30 AND hop NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day, link_id, destination_kind, hop)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_hop_monthly (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  destination_kind TEXT NOT NULL CHECK (destination_kind IN ('link', 'social_icon')),
  hop TEXT NOT NULL CHECK (length(hop) BETWEEN 1 AND 30 AND hop NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, month, link_id, destination_kind, hop)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_budget_lh (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  hour INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  PRIMARY KEY (site, day, hour)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS click_landing_state (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  from_day TEXT NOT NULL CHECK (length(from_day) = 10),
  PRIMARY KEY (site)
) WITHOUT ROWID;
