-- The unstored-session floor (kit_meta 'unstored_floor_ts', milliseconds) is a revocation time. kit_meta
-- is STRICT INTEGER but has no sign check, and the key is shared with other heartbeats, so a negative
-- floor is refused here for that one key. leads.session_floor_ts already has CHECK (>= 0) in a STRICT table.
-- A negative floor already stored is repaired first, before the triggers exist: it is replaced with the
-- migration time, so every unstored unlock issued before this migration is revoked (fail closed) instead of
-- every later unstored session answering a storage error until someone edits the row by hand.
UPDATE kit_meta SET value = CAST(strftime('%s', 'now') AS INTEGER) * 1000
WHERE key = 'unstored_floor_ts' AND value < 0;

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
