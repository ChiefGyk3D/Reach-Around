-- SPDX-License-Identifier: Apache-2.0
-- Closed destination kind for owner-report Links and Social icons tabs (#438).
-- Existing rows are regular links. The destination id remains the vocabulary;
-- this field only selects its report tab and does not add a new analytics cut.
ALTER TABLE click_events ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'link'
  CHECK (destination_kind IN ('link', 'social_icon'));

-- A destination may move between the link and social-icon sections in a later
-- build. Kind is therefore part of a counter cell's identity, including the
-- kind-specific overflow cells. SQLite cannot add a column to a primary key,
-- so rebuild the four aggregate tables and preserve existing rows as links.
CREATE TABLE click_src_daily_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  src TEXT NOT NULL CHECK (length(src) BETWEEN 1 AND 30 AND src NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  destination_kind TEXT NOT NULL DEFAULT 'link' CHECK (destination_kind IN ('link', 'social_icon')),
  PRIMARY KEY (site, day, link_id, destination_kind, src)
) WITHOUT ROWID;
INSERT INTO click_src_daily_new SELECT site, day, link_id, src, clicks, 'link' FROM click_src_daily;
DROP TABLE click_src_daily;
ALTER TABLE click_src_daily_new RENAME TO click_src_daily;

CREATE TABLE click_country_daily_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  country TEXT NOT NULL CHECK (length(country) = 2 AND country NOT GLOB '*[^A-Z]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  destination_kind TEXT NOT NULL DEFAULT 'link' CHECK (destination_kind IN ('link', 'social_icon')),
  PRIMARY KEY (site, day, link_id, destination_kind, country)
) WITHOUT ROWID;
INSERT INTO click_country_daily_new SELECT site, day, link_id, country, clicks, 'link' FROM click_country_daily;
DROP TABLE click_country_daily;
ALTER TABLE click_country_daily_new RENAME TO click_country_daily;

CREATE TABLE click_src_monthly_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  src TEXT NOT NULL CHECK (length(src) BETWEEN 1 AND 30 AND src NOT GLOB '*[^a-z0-9-]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  destination_kind TEXT NOT NULL DEFAULT 'link' CHECK (destination_kind IN ('link', 'social_icon')),
  PRIMARY KEY (site, month, link_id, destination_kind, src)
) WITHOUT ROWID;
INSERT INTO click_src_monthly_new SELECT site, month, link_id, src, clicks, 'link' FROM click_src_monthly;
DROP TABLE click_src_monthly;
ALTER TABLE click_src_monthly_new RENAME TO click_src_monthly;

CREATE TABLE click_country_monthly_new (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  month TEXT NOT NULL CHECK (length(month) = 7),
  link_id TEXT NOT NULL CHECK (length(link_id) BETWEEN 1 AND 40),
  country TEXT NOT NULL CHECK (length(country) = 2 AND country NOT GLOB '*[^A-Z]*'),
  clicks INTEGER NOT NULL CHECK (clicks >= 1),
  destination_kind TEXT NOT NULL DEFAULT 'link' CHECK (destination_kind IN ('link', 'social_icon')),
  PRIMARY KEY (site, month, link_id, destination_kind, country)
) WITHOUT ROWID;
INSERT INTO click_country_monthly_new SELECT site, month, link_id, country, clicks, 'link' FROM click_country_monthly;
DROP TABLE click_country_monthly;
ALTER TABLE click_country_monthly_new RENAME TO click_country_monthly;
