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
-- edge already gave us and the CHECK refuses anything else. Every table is
-- STRICT (text cannot land in an integer column) and `kit_events.ts` must be a
-- whole hour, enforced here and not only by the Worker. The email CHECK is a
-- structural backstop (one @, a dotted domain, a letter ending); the strict
-- grammar is the Worker's normalizer, which leads.ts mirrors.
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
    CHECK (
      length(email) BETWEEN 6 AND 254
      AND email = lower(email)
      AND email NOT GLOB '*[^a-z0-9@._+-]*'
      AND length(email) - length(replace(email, '@', '')) = 1
      AND instr(email, '@') BETWEEN 2 AND 65
      AND substr(email, instr(email, '@') + 1) GLOB '*.*'
      AND substr(email, instr(email, '@') + 1) NOT GLOB '*[^a-z0-9.-]*'
      -- No label over 63 characters. D1 caps a GLOB pattern at 50 bytes, so map every
      -- label character to x, fold each run of eight x into one y, and refuse eight y.
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

-- Route budgets and daily counters. `key` is a short lowercase slug. `day` is a
-- UTC day (YYYY-MM-DD) or a UTC hour (YYYY-MM-DDTHH).
CREATE TABLE counters (
  key TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 64 AND key NOT GLOB '*[^a-z0-9_-]*'),
  -- D1 caps a GLOB pattern at 50 bytes, so the date and the hour are checked apart.
  day TEXT NOT NULL CHECK (
    length(day) IN (10, 13)
    AND day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
    AND (length(day) = 10 OR substr(day, 11) GLOB 'T[0-9][0-9]')
    -- A real calendar day: month 01-12, day 01-31, short months, and February at
    -- most 29 (leap years are not checked), hour 00-23.
    AND substr(day, 6, 2) BETWEEN '01' AND '12'
    AND substr(day, 9, 2) BETWEEN '01' AND '31'
    AND NOT (substr(day, 6, 2) = '02' AND substr(day, 9, 2) > '29')
    AND NOT (substr(day, 6, 2) IN ('04', '06', '09', '11') AND substr(day, 9, 2) = '31')
    AND (length(day) = 10 OR substr(day, 12, 2) <= '23')
  ),
  count INTEGER NOT NULL CHECK (count >= 0),
  PRIMARY KEY (key, day)
) WITHOUT ROWID, STRICT;

-- Heartbeats such as last_purge_ts: a short lowercase key and an integer.
CREATE TABLE kit_meta (
  key TEXT PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 64 AND key NOT GLOB '*[^a-z0-9_]*'),
  value INTEGER NOT NULL
) WITHOUT ROWID, STRICT;
