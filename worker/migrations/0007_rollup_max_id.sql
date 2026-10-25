-- SPDX-License-Identifier: Apache-2.0
-- Page view rollup: the state that makes late rows and re-rolls safe.
--
--   max_id   the highest raw row id a day's aggregates count. The raw purge
--            deletes a rolled day's rows only up to this id, and a raw row of
--            a sealed day with a larger id is a LATE row, which triggers a
--            full re-roll of that day.
--   rerolls  how many times the day was re-rolled.
--
-- Old state is INVALIDATED, not inferred. Whatever the rollup wrote before this
-- migration has no record of which raw rows it counted, and guessing (for
-- example from the current MAX(id)) could mark a never-counted late row as
-- counted and let the purge delete it. So every aggregate and every log row is
-- removed; every day that still has raw rows is rolled again from scratch by
-- the new code, and days whose raw rows are already gone are simply not
-- available (their aggregates are dropped). Fresh databases have nothing to
-- delete.
DELETE FROM view_daily;
DELETE FROM view_dim_daily;
DELETE FROM view_geo_daily;
DELETE FROM view_rollup_log;
DELETE FROM view_meta WHERE key IN ('rolled_through', 'rollup_failed_day');
ALTER TABLE view_rollup_log ADD COLUMN max_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE view_rollup_log ADD COLUMN rerolls INTEGER NOT NULL DEFAULT 0;
