<!-- SPDX-License-Identifier: Apache-2.0 -->
# YouTube Analytics (private, owner only)

Refs #391, epic #364. Optional collector, off by default. Set `analytics: true`
on the `youtube` source in `numbers.sources` (see [config](config.md#numbers)).
It runs in `reach-around counts refresh`, only when `numbers.history_file` is
set and only when the subscriber read of the same source succeeded in the run.

## Privacy: never public

The figures are the channel owner's authorized data. The YouTube developer
policy (III.E.3.b, see [platform terms](research/platform-terms-retention.md))
bars showing them to anyone but the authorizing user, and the owner decided on
2026-11-19 that they stay private. They go only to the history file in the
private instance repository, as `source: api` records with
`api_provider: youtube_analytics`. The site build never reads that file. Two
tests guard it: an import-graph test fails if site, kit, editor, config, preview
or editing code can reach the collector, and a leak test builds a public site
beside a history file holding these figures and fails if any value or figure
name appears in the output. The owner report refuses these records too.

## What is stored

| Metric | Meaning |
| --- | --- |
| `views_1d`, `watch_minutes_1d` | one record per UTC day for the last 7 processed days; the record's time is that day |
| `demo_a18_24_f` and so on | share of viewers by age group and gender over 28 UTC days, in hundredths of a percent (4025 is 40.25 percent) |
| `demo_other` | cells under 2.00 percent and unknown labels, folded together |
| `geo_us_views` and so on | views by country over 28 UTC days, top 25 countries |
| `geo_other_views` | countries under 100 views, and unknown territory, folded together |

The folding floors are this project's choice (ASSUMED: YouTube does not publish
its own thresholds). Data trails by 48 to 72 hours, so the last day asked for is
3 days before today (UTC). An empty demographic or geography answer stores
nothing and is not an error. An empty daily answer is the failure word
`no_data`. A failure is never stored as a zero; the day stays a gap.

## Credentials (names only)

Read from the environment at run time (supplied by the secret manager):

* `YOUTUBE_OAUTH_CLIENT_ID`
* `YOUTUBE_OAUTH_CLIENT_SECRET`
* `YOUTUBE_OAUTH_REFRESH_TOKEN`

The names are this project's choice (ASSUMED). The collector exchanges the
refresh token for a short access token on every run and keeps it in memory only.
It does not store a rotated token: Google's refresh grant normally returns no
new refresh token (ASSUMED, standard OAuth 2.0; not read on a vendor page for
this work). Errors carry fixed words only.

## Owner steps (one time)

1. In a Google Cloud project, enable the YouTube Analytics API and create an
   OAuth client.
2. Authorize once, offline access, with the Analytics read-only scope
   `https://www.googleapis.com/auth/yt-analytics.readonly`.
   UNRESOLVED: the vendor page for the report method also lists
   `https://www.googleapis.com/auth/youtube.readonly`. If the first run answers
   `scope_missing`, add that scope and authorize again. Ask for the least that
   works.
3. Move the consent screen out of Testing status. A Testing-status external app
   gets refresh tokens that last 7 days. A refresh token also dies when revoked,
   unused for six months, or past 100 per account per client. A rejected refresh
   shows as `refresh_failed`: authorize again.
4. Put the three values in the secret manager (dev first) and expose them to the
   refresh job.

## Retention and deletion

Policy (tool-extracted summary of the developer policy, read 2026-11-19, not yet
human-reviewed): authorized data may be kept while authorization is re-confirmed
about every 30 days (a daily run that still authenticates does that); deletion is
due within 7 days of a user request or in-client revocation, and within 30 days
of revocation through Google's security page. The owner decision of 2026-11-19
applies a 30-day refresh-or-delete rule to stored authorized data.

`reach_around.counts.youtube_analytics.deletion_candidates(records, today,
revoked=False)` selects the records that are due: older than 30 days, or all of
them when `revoked`. It only selects. The redaction is the tombstone record of
#586, not built yet. Git history keeps old values, so a true deletion needs the
owner-approved rewrite described in the platform terms note.

## Quota

One request per report, three reports per run (one daily series, one
demographics, one geography), no retry loop beyond the fetch layer's bound. The
daily quota figure was not found on the vendor pages read.
