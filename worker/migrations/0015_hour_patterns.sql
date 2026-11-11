-- Hour-of-day and UTC-weekday owner-report aggregate (#410).
-- One row per site, UTC day, UTC hour and closed bot class. It carries counts
-- only, so it cannot pair time with source, country, device or a person.

CREATE TABLE IF NOT EXISTS view_hour_daily (
  site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'),
  day TEXT NOT NULL CHECK (length(day) = 10),
  hour INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
  bot_class TEXT NOT NULL CHECK (bot_class IN ('h', 'v', 'a', 's')),
  views INTEGER NOT NULL CHECK (views >= 0),
  clicks INTEGER NOT NULL CHECK (clicks >= 0 AND (views > 0 OR clicks > 0)),
  PRIMARY KEY (site, day, hour, bot_class)
) WITHOUT ROWID;
