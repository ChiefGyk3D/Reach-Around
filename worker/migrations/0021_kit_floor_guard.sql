-- The unstored-session floor (kit_meta 'unstored_floor_ts', milliseconds) is a revocation time. kit_meta
-- is STRICT INTEGER but has no sign check, and the key is shared with other heartbeats, so a negative
-- floor is refused here for that one key. leads.session_floor_ts already has CHECK (>= 0) in a STRICT table.
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
