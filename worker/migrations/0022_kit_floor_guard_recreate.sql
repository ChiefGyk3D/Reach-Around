-- migration-phase: contract
-- Recreates the two guard triggers of 0021 with their exact intended bodies. 0021 used CREATE TRIGGER IF NOT
-- EXISTS, which keeps a trigger of the same name that has a different body; this migration drops both names
-- and creates them again, so the final text is always the one below. Migration 0021 is already applied on
-- existing databases and is left unchanged (D1 tracks applied migrations by name).
-- The drop is immediately followed by the recreate in this same file, so the previous Worker never runs against
-- a schema without the guard: the annotation above records that decision for the migrations check.
-- Apply this file as one unit (wrangler d1 migrations apply does, and rolls it back whole on a failure). The
-- guard triggers are created BEFORE the repair, so no writer can store a negative floor once the repair has
-- run. A negative floor already stored is replaced with the migration time, which revokes every unstored
-- unlock issued before this migration (fail closed). Applying the statements by hand one at a time is not
-- supported: run the whole file in one command.
DROP TRIGGER IF EXISTS kit_meta_floor_insert;
DROP TRIGGER IF EXISTS kit_meta_floor_update;

CREATE TRIGGER kit_meta_floor_insert BEFORE INSERT ON kit_meta
WHEN NEW.key = 'unstored_floor_ts' AND NEW.value < 0
BEGIN
  SELECT RAISE(ABORT, 'unstored_floor_ts must be >= 0');
END;

CREATE TRIGGER kit_meta_floor_update BEFORE UPDATE ON kit_meta
WHEN NEW.key = 'unstored_floor_ts' AND NEW.value < 0
BEGIN
  SELECT RAISE(ABORT, 'unstored_floor_ts must be >= 0');
END;

UPDATE kit_meta SET value = CAST(strftime('%s', 'now') AS INTEGER) * 1000
WHERE key = 'unstored_floor_ts' AND value < 0;
