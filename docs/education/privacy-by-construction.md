<!-- SPDX-License-Identifier: Apache-2.0 -->
# Privacy by construction

A privacy page that someone writes by hand drifts. A feature gets added, the
page is not updated, and the site quietly does more than it says. Reach Around
avoids that by generating the page from the same config that turns features on.

## What is generated

`collected_items(config)` in `reach_around.site.privacy` returns one
`Disclosure` (what, why, how long, sensitive) for each thing an enabled feature
collects:

- **Click tracking** (on by default): which link was clicked and the visitor's
  country only. No IP address is stored. Kept for `privacy.retention_days`
  (default 90).
- **Kit gate** (off by default): the email address a visitor enters, kept for
  `privacy.lead_retention_days` (default 365), and the cookie that remembers
  the kit is unlocked.

Turn a feature off and its item disappears. With everything off, the page says
the site collects nothing.

## What is not collected

No IP addresses, no names, no fingerprints, no third-party trackers, and no
JavaScript on the public site. Nothing the site collects is sensitive, and each
item on the page says so.

## Cookies and the notice

`sets_cookies(config)` is true only when an enabled feature stores a cookie
(today, the kit gate). Only then does every page show a notice with Preferences,
Reject and Accept controls. They are a plain form that opens the privacy page, so
they work without JavaScript. The cookie logic itself arrives in a later phase;
this phase sets no cookie.

## Your own wording

`privacy.intro` replaces the neutral opening text. It is plain text, escaped like
every other config value, so markup in it is shown, not run. It does not replace
the generated list of what is collected.

## How the tests keep this honest

- Click tracking on: the item says "country" and "no IP address".
- Gate on: the email item, the retention days and the cookie item appear, and
  the banner is rendered. Gate off: no banner and no cookie code in any page.
- `test_disabling_click_tracking_removes_the_click_item` proves the list follows
  the config, and the retention tests prove the numbers come from it.
- `test_disclosure_never_marks_collected_items_sensitive` and
  `test_privacy_page_has_no_scripts_and_escapes_the_intro` cover the plain
  statement and the escaping.
- A golden file pins the rendered privacy page, so any wording change is a
  visible diff in review.
