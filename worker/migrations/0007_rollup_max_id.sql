-- SPDX-License-Identifier: Apache-2.0
-- Page view rollup: the highest raw row id that a day's aggregate already
-- counts. The raw purge deletes a rolled day's rows only up to this id, and a
-- later run folds rows above it (a view that began before UTC midnight and
-- was written after the day sealed) into the same day exactly once.
ALTER TABLE view_rollup_log ADD COLUMN max_id INTEGER NOT NULL DEFAULT 0;
