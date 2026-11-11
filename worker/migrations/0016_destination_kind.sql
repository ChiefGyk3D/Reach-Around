-- SPDX-License-Identifier: Apache-2.0
-- Closed destination kind for owner-report Links and Social icons tabs (#438).
-- Existing rows are regular links. The destination id remains the vocabulary;
-- this field only selects its report tab and does not add a new analytics cut.
ALTER TABLE click_events ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'link'
  CHECK (destination_kind IN ('link', 'social_icon'));
ALTER TABLE click_src_daily ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'link'
  CHECK (destination_kind IN ('link', 'social_icon'));
ALTER TABLE click_country_daily ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'link'
  CHECK (destination_kind IN ('link', 'social_icon'));
ALTER TABLE click_src_monthly ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'link'
  CHECK (destination_kind IN ('link', 'social_icon'));
ALTER TABLE click_country_monthly ADD COLUMN destination_kind TEXT NOT NULL DEFAULT 'link'
  CHECK (destination_kind IN ('link', 'social_icon'));
