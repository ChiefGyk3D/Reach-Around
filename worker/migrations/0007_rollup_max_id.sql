-- SPDX-License-Identifier: Apache-2.0
-- Page view rollup: the highest raw row id that a day's aggregate already
-- counts. The raw purge deletes a rolled day's rows only up to this id, and a
-- later run folds rows above it (a view that began before UTC midnight and
-- was written after the day sealed) into the same day exactly once.
ALTER TABLE view_rollup_log ADD COLUMN max_id INTEGER NOT NULL DEFAULT 0;

-- Safe on its own: a log row that already existed counted every raw row it had
-- at the time, so its max_id is the highest id now stored for that day (0 when
-- no raw row is left). Without this, `id <= max_id` in the raw purge would keep
-- all of such a day's raw rows until the hard ceiling. The day expression is
-- the one the rollup and the purge use.
UPDATE view_rollup_log SET max_id = COALESCE(
  (SELECT MAX(id) FROM view_events
    WHERE strftime('%Y-%m-%d', ts / 1000, 'unixepoch') = view_rollup_log.day),
  0);
