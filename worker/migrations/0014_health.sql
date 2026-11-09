-- SPDX-License-Identifier: Apache-2.0
--
-- Collection coverage (issues #401, #402). One row per UTC day: 24 letters, one
-- per UTC hour, written by the hourly health step for the hour that just ended.
--   o  the Worker was collecting normally
--   k  the page-view kill switch (VIEWS_KILL) was on when sampled
--   q  the account-wide hourly view budget was reached (views were dropped)
--   -  no sample for that hour (the Worker did not run: an outage)
-- It holds no visitor data and no counts. `reach-around report` and the Grafana
-- export read it to show a missing day as a gap with a reason, never as a zero.
-- Rows older than 400 days are deleted by the same step.
-- Apply with the Worker idle or not: the table is new and nothing reads it yet.
-- Down: DROP TABLE analytics_coverage.
CREATE TABLE IF NOT EXISTS analytics_coverage (
  day TEXT NOT NULL PRIMARY KEY CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  hours TEXT NOT NULL CHECK (length(hours) = 24 AND hours NOT GLOB '*[^okq-]*')
) WITHOUT ROWID, STRICT;
