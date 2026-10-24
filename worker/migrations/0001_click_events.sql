-- SPDX-License-Identifier: Apache-2.0
-- One row per accepted click. There is deliberately no column for an IP
-- address, user agent or referrer: what is not stored cannot leak.
CREATE TABLE click_events (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  link_id TEXT NOT NULL,
  country TEXT
);

CREATE INDEX idx_click_events_ts ON click_events (ts);
