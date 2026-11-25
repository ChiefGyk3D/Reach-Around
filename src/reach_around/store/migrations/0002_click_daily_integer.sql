-- click_daily.count must stay an exact integer inside a safe bound. SQLite cannot
-- add a CHECK in place, so the table is rebuilt; a row outside the bound stops the
-- migration (the pre-migration backup is taken first).
CREATE TABLE click_daily_new (
    day TEXT NOT NULL,
    day_tz TEXT NOT NULL,
    link_id TEXT NOT NULL,
    country TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (
        typeof(count) = 'integer' AND count >= 0 AND count <= 9007199254740991
    ),
    PRIMARY KEY (day, day_tz, link_id, country)
);
INSERT INTO click_daily_new (day, day_tz, link_id, country, count)
    SELECT day, day_tz, link_id, country, count FROM click_daily;
DROP TABLE click_daily;
ALTER TABLE click_daily_new RENAME TO click_daily;
