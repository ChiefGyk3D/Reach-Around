-- SPDX-License-Identifier: Apache-2.0
-- Source-attributed clicks (issue #329, aggregate only).
--
--   1. click_events gains a nullable `src`: the closed-set source label the page
--      sent (a label on the site's `sources` list, or the fixed 'other'). It is
--      not an identifier: no cookie, no stored id, no join to a view or a person.
--      The CHECK is small on purpose (CI's SQLite has a parser stack limit).
--      A row holds a source or a country, never both (the Worker writes only one),
--      so no source-by-country pair is stored (#408). The CHECK enforces it.
--   2. Raw click timestamps are rounded down to the hour, as 0008 did for
--      views (issue #408), existing rows included.
--
-- Down note: SQLite 3.35+ can `ALTER TABLE click_events DROP COLUMN src`. The
-- hour rounding cannot be undone, and does not need to be.
--
-- RULE: apply with the Worker idle, as for 0007 and 0008.

ALTER TABLE click_events ADD COLUMN src TEXT
  CHECK (src IS NULL OR (length(src) BETWEEN 1 AND 30 AND src NOT GLOB '*[^a-z0-9-]*' AND country IS NULL));
UPDATE click_events SET ts = ts - (ts % 3600000);
