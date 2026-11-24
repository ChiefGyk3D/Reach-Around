-- SPDX-License-Identifier: Apache-2.0
-- City counters for page views (issue #778).
--
-- A raw page-view row never carries a city (its city column stays, always empty). When a
-- site's geography level is `city`, the Worker adds one to this daily counter in the same
-- batch that stores the view, and nothing else: no page, source, browser, time of day or
-- identifier. The daily rollup reads it, applies the small-count floor, and writes the result
-- to the daily geography table; a city under the floor folds into its region there. The counter itself is
-- purged with the raw rows (viewretention.ts).
--
-- Nothing here touches an existing table. RULE: apply with the Worker idle, as for 0008.

CREATE TABLE IF NOT EXISTS view_city_daily (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20),
  day TEXT NOT NULL CHECK (length(day) = 10),
  country TEXT NOT NULL,
  region TEXT NOT NULL,
  city TEXT NOT NULL CHECK (length(city) BETWEEN 1 AND 60),
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 1),
  uniques INTEGER NOT NULL CHECK (uniques >= 0),
  PRIMARY KEY (site, day, country, region, city, bot_class)
) WITHOUT ROWID;
