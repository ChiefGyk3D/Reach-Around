-- D1 cross-statement changes() check (#759). Dev database only. See d1-changes-check.md.
-- wrangler d1 execute runs a --file as one batch, the same path as db.batch().
DROP TABLE IF EXISTS zz_chg_src;
DROP TABLE IF EXISTS zz_chg_log;
CREATE TABLE zz_chg_src (id INTEGER PRIMARY KEY, v INTEGER);
CREATE TABLE zz_chg_log (k TEXT PRIMARY KEY, n INTEGER);
INSERT INTO zz_chg_src (id, v) VALUES (1, 1), (2, 1), (3, 1), (4, 2);
DELETE FROM zz_chg_src WHERE v = 1;
INSERT INTO zz_chg_log (k, n) SELECT 'deleted', changes() WHERE changes() > 0;
INSERT INTO zz_chg_log (k, n) SELECT 'flag', 1 WHERE changes() > 0;
DELETE FROM zz_chg_src WHERE v = 99;
INSERT INTO zz_chg_log (k, n) SELECT 'none', 1 WHERE changes() > 0;
SELECT
  (SELECT n FROM zz_chg_log WHERE k = 'deleted') = 3 AS ok_count_follows_delete,
  (SELECT n FROM zz_chg_log WHERE k = 'flag') = 1 AS ok_chained_write_sets_changes,
  NOT EXISTS (SELECT 1 FROM zz_chg_log WHERE k = 'none') AS ok_zero_delete_skips;
DROP TABLE zz_chg_src;
DROP TABLE zz_chg_log;
