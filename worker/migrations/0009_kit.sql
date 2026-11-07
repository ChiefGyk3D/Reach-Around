-- SPDX-License-Identifier: Apache-2.0
-- Media kit lead storage and kit events (Plan 3 Task 4, issue #250).
--
-- Numbering: Plan 3 expected 0002_kit.sql. Main already holds 0001 and
-- 0005 to 0008, so this takes the next free number, 0009 (cutover slice rule:
-- never reuse or insert below an applied number; gaps stay gaps).
--
-- Visitor data. A lead is an email address the visitor typed, nothing more.
-- There is deliberately NO column for an IP address, user agent, referrer,
-- URL or any device identifier. `country` is the two-letter country code the
-- edge already gave us and the CHECK refuses anything else, so an address or
-- agent string cannot be stored there by mistake. `kit_events.ts` is written
-- rounded down to the hour by the Worker.
--
-- Retention. Every v1 lead is unverified (verified_ts IS NULL) and is purged
-- 30 days after created_ts; a hard maximum from created_ts applies to every
-- lead. The purge lives in worker/src/kit/leads.ts. These tables are never
-- part of a backup or export.
--
-- email_links (the emailed-link flow) is not created: that route is cut from
-- the v1 slice. A later migration adds it if the flow returns.

CREATE TABLE leads (
  id TEXT PRIMARY KEY
    CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
  email TEXT NOT NULL UNIQUE
    CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
  created_ts INTEGER NOT NULL,
  last_seen_ts INTEGER NOT NULL,
  verified_ts INTEGER,
  source TEXT NOT NULL CHECK (source IN ('gate', 'email')),
  country TEXT CHECK (country IS NULL OR (length(country) = 2 AND country NOT GLOB '*[^A-Z]*')),
  last_view_alert_ts INTEGER,
  last_view_event_ts INTEGER,
  last_cc_alert_ts INTEGER,
  session_floor_ts INTEGER
);
CREATE INDEX idx_leads_created_ts ON leads (created_ts);
CREATE INDEX idx_leads_verified_ts ON leads (verified_ts);

CREATE TABLE kit_events (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  lead_id TEXT NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('unlock', 'view', 'verify'))
);
CREATE INDEX idx_kit_events_ts ON kit_events (ts);
CREATE INDEX idx_kit_events_lead ON kit_events (lead_id);

-- Route budgets and daily counters. `day` is a UTC day or a UTC hour string.
CREATE TABLE counters (
  key TEXT NOT NULL,
  day TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (key, day)
) WITHOUT ROWID;

-- Heartbeats such as last_purge_ts.
CREATE TABLE kit_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;
