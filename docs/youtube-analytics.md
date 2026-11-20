<!-- SPDX-License-Identifier: Apache-2.0 -->
# YouTube Analytics (private, owner only)

Refs #391, epic #364. Optional collector, off by default. Set `analytics: true`
on the `youtube` source in `numbers.sources` (see [config](config.md#numbers)).
It runs in `reach-around counts refresh`, only when `numbers.history_file` is
set and only when the subscriber read of the same source succeeded in the run.

**Removal is not done by this collector.** Records past the 30-day rule are
removed by the history tombstone tool (issue #586, draft PR #607 and its
stacked implementation). Until that tool is merged, enabling `analytics: true`
means the owner must run the removal by hand. Every `counts refresh` prints a
notice with the number of analytics records past 30 days (a count, never a
value) so the owner is told each run until they are removed.

## Privacy: never public

The figures are the channel owner's authorized data. The YouTube developer
policy (III.E.3.b, see [platform terms](research/platform-terms-retention.md))
bars showing them to anyone but the authorizing user, and the owner decided on
2026-11-19 that they stay private. They go only to the history file in the
private instance repository, as `source: api` records with
`api_provider: youtube_analytics`. The tag is set by the collector for every one
of these figures, whatever the source's `platform` slug is called. Nothing
infers it from the slug. The site build never reads the history file. Three
tests guard it. A differential test builds the public site from one config
twice, with a history file full of these figures and without one, and requires
the whole output trees to be byte-identical (so a renderer printing a derived
form such as `987.7k` is caught too). A test fails if the build opens the
history file at all. An import-graph test (static, defense in depth) fails if
site, kit, editor, config, preview or editing code can reach the collector.

`reach-around report` refuses these records (`_refused` in `funnel/social.py`,
display policy, #424), so the owner cannot see them there yet. They can be read
from the history file itself.

**Records are identified only by the provider tag** `api_provider:
youtube_analytics`. One rule is applied everywhere: the report refuses it, and
deletion, revocation and the retention notice select it. There is no guessing
from metric names, so an untagged record named `views_1d` is ordinary data. If a
hand-written or pre-existing history file already holds analytics-shaped rows
without the tag, tag them by hand before enabling the collector.

**A correction keeps the tag of the record it fixes.** The history validator
refuses a correction whose `api_provider` differs from its original, and
`counts history-add --correction` copies the original's tag automatically, so a
corrected value is still found by deletion, revocation and the notice. A
correction goes with its original in the report: refused when the original is,
and ignored (never an error) when it has no original.

**One analytics source per config.** `analytics: true` is allowed on at most one
`youtube` source (the config is refused at load otherwise). One `counts refresh`
appends at most 200 history records, and a run of this collector can add up to 62.
If the combined batch of a run (subscribers, other sources and these figures)
would still be over 200, only the analytics figures of that run are left out and
reported as `too_large`; subscribers and other sources are always stored.

## What is stored

| Metric | Meaning |
| --- | --- |
| `views_1d`, `watch_minutes_1d` | one record per UTC day for the last 7 processed days; the record's time is that day |
| `demo_a18_24_f` and so on | share of viewers by age group and gender over 28 UTC days, in hundredths of a percent (4025 is 40.25 percent) |
| `demo_other` | the sum of known cells under 2.00 percent. Only the documented age labels (13-17, 18-24, 25-34, 35-44, 45-54, 55-64, 65 and over; ASSUMED list) and the three gender labels are accepted; any other label is a bad row, not folded |
| `geo_us_views` and so on | views by country over 28 UTC days: the 25 largest countries with at least 100 views |
| `geo_other_views` | the sum of every other country read (under 100 views, outside the 25 largest, or the unknown territory code) |

Geography reads up to 3 pages of 100 countries (`maxResults`, `startIndex`; the
largest page size the API accepts is not known and the 1-based start index is
ASSUMED), so at most 300 countries per run. The collector sorts them itself
rather than trusting the answer's order. Output is bounded before any record is
built: at most 14 daily figures, 22 demographic figures and 26 geography figures
(62 in a run), far under the 200 records one history append takes. A report that
returns more than its limit fails alone with `too_large`; the others are kept.

The folding floors (2.00 percent, 100 views) are this project's choice
(ASSUMED: YouTube does not publish its own thresholds). They reduce noise. They
do not stop differencing: the demographic floor is on a share, not on a viewer
count; the daily series has no floor; and because consecutive stored 28-day
windows overlap, the change in an "other" total from one day to the next can
recover a folded cell. That is acceptable only because the data is private and
never published.

Data trails by 48 to 72 hours, so the last day asked for is 3 days before today
(UTC). The history store keeps one record per metric and UTC day (`skip_days`),
so a later revision of a past day's views is never stored: the first value read
for a day stays. An empty demographic or geography answer stores nothing and is
not an error. An empty daily answer is the failure word `no_data`. A row with a
bad day or bad numbers is skipped and counted (the word `bad_row`) while the
other rows are kept; a wrong header, a duplicate, or an oversized answer fails
that whole report. A failure is never stored as a zero; the day stays a gap.

Demographic shares are checked as a whole: the recognized cells, kept and folded
together, must not add up to more than 100 percent. A total above 100.25 percent
is refused as `bad_aggregate` and that report stores nothing. The 0.25 percent
slack is for rounding of each cell to a hundredth of a percent (ASSUMED; the
vendor's rounding is not documented on a page read). Likewise a geography
"other" total over the history maximum is refused, not clamped.

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

The rule is the one in [platform terms](research/platform-terms-retention.md)
(YouTube Developer Policies, read 2026-11-19): refresh or delete API data after
30 calendar days (III.E.4, III.E.4.c); delete within 7 days of a trigger
(III.E.4.g); delete within 30 days of user revocation (III.D.2). The owner
decision of 2026-11-19 applies the 30-day refresh-or-delete rule to stored
authorized data. UNVERIFIED beyond that note: any finer reading of what counts as
a trigger, and the exact wording of the clauses. A human must check them against
the vendor page.

`reach_around.counts.youtube_analytics.deletion_candidates(records, today,
revoked=False)` selects the records that are due: older than 30 days, or all of
them when `revoked`. A correction that carries an analytics value counts like
the record it fixes. It only selects, and `counts refresh` uses it to print the
notice above. The redaction is the tombstone record of #586, not built here. Git
history keeps old values, so a true deletion needs the owner-approved rewrite
described in the platform terms note.

## Quota

Logical report queries: three reports per run (one daily series, one
demographics, one geography). The geography report takes 1 to 3 queries (pages),
so a run makes 3 to 5 report queries, plus one token exchange.

HTTP attempts are more than that. Each report GET may be retried by the fetch
layer on a 429 or a 5xx, up to three attempts, so a run can make 9 to 15 report
attempts in a bad case. The token exchange is a POST and is never retried. There
is no retry loop beyond the fetch layer's bound. The daily quota figure was not
found on the vendor pages read, and whether a retried attempt counts again is not
known.
