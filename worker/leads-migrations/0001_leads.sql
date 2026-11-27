-- SPDX-License-Identifier: Apache-2.0
-- The media kit leads database (issue #772, storage design decision 9).
--
-- This folder is the migration set of the SECOND D1 database, bound to the Worker as
-- LEADS_DB. It is applied only to that database (docs/deploy.md, "The leads database"):
--
--   npx wrangler d1 migrations apply LEADS_DB --remote --config <your wrangler config>
--
-- It is never applied to the analytics database (binding DB), whose migrations live in
-- worker/migrations. The tables below are a copy of the lead tables of 0009_kit.sql, with
-- identical checks, so the Worker code that stores a lead did not change shape, only
-- the database it talks to. The old copies in the analytics database are left in place
-- (a contract migration there needs its own later release, .github/scripts/check-migrations.py)
-- and the Worker no longer reads or writes them. The migration baseline reset means there
-- is no data to carry over.
--
-- Visitor data. A lead is an email address the visitor typed, nothing more. There is
-- deliberately NO column for an IP address, user agent, referrer, URL or any device
-- identifier. `country` is the two-letter country code the edge already gave us. Every
-- table is STRICT and `kit_events.ts` must be a whole hour, enforced here and not only by
-- the Worker. The email CHECK is a structural backstop; the strict grammar is the Worker's
-- normalizer, which worker/src/kit/leads.ts mirrors.
--
-- kit_events lives here, not in the analytics database, because it carries lead_id and is
-- deleted with its lead (ON DELETE CASCADE). kit_meta here holds the lead purge heartbeat
-- (last_purge_ts) only; the kit-wide unstored session floor stays in the analytics
-- database, which holds no lead data.

CREATE TABLE leads (
  id TEXT NOT NULL PRIMARY KEY
    CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
  email TEXT NOT NULL UNIQUE
    CHECK (
      length(email) BETWEEN 6 AND 254
      AND email = lower(email)
      AND email NOT GLOB '*[^a-z0-9@._+-]*'
      AND length(email) - length(replace(email, '@', '')) = 1
      AND instr(email, '@') BETWEEN 2 AND 65
      AND substr(email, instr(email, '@') + 1) GLOB '*.*'
      AND substr(email, instr(email, '@') + 1) NOT GLOB '*[^a-z0-9.-]*'
      AND replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(substr(email, instr(email, '@') + 1), 'a', 'x'), 'b', 'x'), 'c', 'x'), 'd', 'x'), 'e', 'x'), 'f', 'x'), 'g', 'x'), 'h', 'x'), 'i', 'x'), 'j', 'x'), 'k', 'x'), 'l', 'x'), 'm', 'x'), 'n', 'x'), 'o', 'x'), 'p', 'x'), 'q', 'x'), 'r', 'x'), 's', 'x'), 't', 'x'), 'u', 'x'), 'v', 'x'), 'w', 'x'), 'x', 'x'), 'y', 'x'), 'z', 'x'), '0', 'x'), '1', 'x'), '2', 'x'), '3', 'x'), '4', 'x'), '5', 'x'), '6', 'x'), '7', 'x'), '8', 'x'), '9', 'x'), '-', 'x'), 'xxxxxxxx', 'y') NOT GLOB '*yyyyyyyy*'
      AND email NOT GLOB '*..*'
      AND email NOT GLOB '.*' AND email NOT GLOB '*.@*'
      AND email NOT GLOB '*@.*' AND email NOT GLOB '*.'
      AND email NOT GLOB '*@-*' AND email NOT GLOB '*-.*' AND email NOT GLOB '*.-*' AND email NOT GLOB '*-'
      AND (email GLOB '*[a-z][a-z]' OR email GLOB '*.xn--*')
    ),
  created_ts INTEGER NOT NULL CHECK (created_ts >= 0),
  last_seen_ts INTEGER NOT NULL CHECK (last_seen_ts >= 0),
  verified_ts INTEGER CHECK (verified_ts IS NULL OR verified_ts >= 0),
  source TEXT NOT NULL CHECK (source IN ('gate', 'email')),
  country TEXT CHECK (country IS NULL OR (length(country) = 2 AND country NOT GLOB '*[^A-Z]*')),
  last_view_alert_ts INTEGER CHECK (last_view_alert_ts IS NULL OR last_view_alert_ts >= 0),
  last_view_event_ts INTEGER CHECK (last_view_event_ts IS NULL OR last_view_event_ts >= 0),
  last_cc_alert_ts INTEGER CHECK (last_cc_alert_ts IS NULL OR last_cc_alert_ts >= 0),
  session_floor_ts INTEGER CHECK (session_floor_ts IS NULL OR session_floor_ts >= 0)
) STRICT;
CREATE INDEX idx_leads_created_ts ON leads (created_ts);
CREATE INDEX idx_leads_verified_ts ON leads (verified_ts);

CREATE TABLE kit_events (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL CHECK (ts >= 0 AND ts % 3600000 = 0),
  lead_id TEXT NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('unlock', 'view', 'verify'))
) STRICT;
CREATE INDEX idx_kit_events_ts ON kit_events (ts);
CREATE INDEX idx_kit_events_lead ON kit_events (lead_id);

CREATE TABLE kit_meta (
  key TEXT NOT NULL PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 64 AND key NOT GLOB '*[^a-z0-9_]*'),
  value INTEGER NOT NULL
) WITHOUT ROWID, STRICT;
