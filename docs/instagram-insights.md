<!-- SPDX-License-Identifier: Apache-2.0 -->
# Instagram insights (private, owner only)

Refs #393, epic #246. Live-API verification by the owner is #552. Optional
collector, off by default. Set `analytics: true` and `user_id` (the numeric
Instagram professional account id) on the `instagram` source in
`numbers.sources` (see [config](config.md#numbers)). It runs in
`reach-around counts refresh`, only when `numbers.history_file` is set and only
when the follower read of the same source succeeded in the run. It follows the
[YouTube Analytics](youtube-analytics.md) collector's pattern.

**Nothing in this page has been checked against a live Meta API.** Fixtures are
written by hand from [docs/research/instagram.md](research/instagram.md), whose
sources were read on 2026-10-23. Whatever that note does not confirm is marked
UNVERIFIED below and in the code, and is tied to #552.

## Privacy: never public

The figures are Meta Platform Data from the owner's account. The owner decided
on 2026-11-19 that authorized API rows stay private ([platform
terms](research/platform-terms-retention.md)). They go only to the history file in
the private instance repository, as `source: api` records with `api_provider:
instagram_insights`. The collector sets the tag for every figure, whatever the
source's `platform` slug is called; nothing infers it from the slug. The site
build never reads the history file. The same three guards as YouTube cover it: a
differential test (the public site is byte-identical with and without a history
file full of these figures), a test that the build never opens the history file,
and the import-graph test (site, kit, editor, config, preview and editing code
cannot reach `reach_around.counts.instagram_insights`).

`reach-around report` refuses these records by the tag (`_refused` in
`funnel/social.py`, via `PRIVATE_PROVIDERS` in `counts/history.py`). One rule is
applied everywhere: the report refuses the tag, and redaction, revocation, the
retention notice and the append-limit fit select it. An untagged record named
`views_7d` is ordinary data. A correction keeps the tag of the record it fixes
(the history validator enforces it).

**One analytics source per provider.** `analytics: true` is allowed on at most one
`instagram` source (the config is refused at load otherwise); one YouTube source
may opt in as well. A run of this collector adds at most 43 records (YouTube: 62).
If the combined batch of a run would pass the 200 record append limit, every
private analytics figure of the run is left out and reported as `too_large`;
follower counts and other sources are always stored.

## What is stored

All values are whole numbers. Windows are UTC days; the last day asked for is 3
days before today, because the note says data can lag up to 48 hours.

| Metric | Meaning |
| --- | --- |
| `reach_7d`, `views_7d`, `interactions_7d`, `engaged_7d`, `link_taps_7d` | the vendor's `reach`, `views`, `total_interactions`, `accounts_engaged` and `profile_links_taps` over the 7 UTC days ending 3 days before the run. Stored exactly. `reach` is a unique count over the window as the vendor defines it, so it must never be added across days or windows (UNVERIFIED) |
| `fdemo_age_18_24` and so on | followers in that age group: 13-17, 18-24, 25-34, 35-44, 45-54, 55-64, 65+ |
| `fdemo_gender_f`, `_m`, `_u` | followers by gender (F, M, U) |
| `fdemo_country_us` and so on | followers by country: the 25 largest countries at or above the floor |
| `fdemo_age_other`, `fdemo_gender_other`, `fdemo_country_other` | the sum of every cell under the floor, plus (country only) every country outside the 25 largest and the `ZZ` unknown code |

**Floor.** A follower cell under 5 is summed into the report's `other` figure, as
[report.md](report.md#the-privacy-floor) and the display work (#397) expect.
Unknown labels (an age group or gender not in the list) are bad rows, counted and
skipped; they are not folded, so an odd answer cannot grow the output. The floor
reduces noise. It does not stop differencing: consecutive stored windows overlap,
so the change in an `other` total from one day to the next can reveal a folded
cell, and an `other` made of one cell under 5 is that cell. That is acceptable only
because the data is private and never published. Activity totals are stored
exactly; the display work applies its own floor.

**Not collected.** "Profile visits" is not in the research note's metric table, so
nothing asks for it. Follower count over time is the follower read's own
`followers` record, not this collector's. City demographics are not collected.

**Limits.** Each of the four reports (activity, age, gender, country) has its own
figure limit, a refused answer fails alone, and the run limit is the sum
(`MAX_RUN_FIGURES`, 43). An answer with more than 100 result rows, or more than 20
entries, is `too_large` and that report stores nothing. A failed or empty report is
never stored as zero: empty activity is the failure word `no_data` (the note: missing
data returns an empty set, not 0); an empty demographic answer stores nothing and is
not an error. Words: `no_data`, `bad_report`, `bad_row` (a bad value or label was
skipped), `bad_aggregate`, `too_large`, `token_rejected` (401), `scope_missing`
(403), `http_400` (also what the note's follower minimum could look like), `rate_limited`,
`server_error`, `redirect`, `fetch_failed`. A failure exits `counts refresh` with 3
and never changes the follower count.

The history store keeps one record per metric and UTC day, so a second run the same
day keeps the first value.

## Credentials (names only)

`INSTAGRAM_ACCESS_TOKEN`, the same fixed variable the follower read uses. The
collector never refreshes or stores a token (the follower read owns that). The
vendor pages show the token only as the `access_token` query value (see
`meta_user.py`), so the token is in the request URL; nothing the fetch layer raises
may travel upward, errors carry fixed words only, no redirect is followed, and
every call is a read-only GET. The token never goes to argv, logs, the history file
or an error message; tests use sentinels and assert it is absent from output.

## HTTP behavior

The collector reuses the follower read's session: https to
`graph.instagram.com` only, no redirect followed (a redirect answer is the word
`redirect` and the target is never contacted), a 64 KiB response cap, a 10 second
timeout, and the fetch layer's bounded retry of 429 and 5xx for GET only (at most 3
attempts a report, so 4 to 12 requests a run). There is no pagination: the insights
route is not paged in the note, and the row cap above bounds an answer. Nothing is
a mutation, so nothing can be replayed after an ambiguous outcome.

## Owner-needed items (#552)

1. **A professional account** (business or creator). Personal accounts have no API
   (note). Confirm the account type.
2. **A Meta developer app** with the Instagram product and the account added so
   Standard Access applies (note, inferred then confirmed by the reviewer for
   accounts you own).
3. **Token scopes**: `instagram_business_basic` (follower count) and
   `instagram_business_manage_insights` (insights, per the note). If the first run
   answers `scope_missing`, the token lacks the second; issue a new one.
4. **Follower minimums.** The note records that follower demographics need 100
   followers (and engaged-audience data needs 100 engagements). Below that the
   answer may be empty or an `http_400`. The thresholds for the activity metrics
   are not in the note. UNVERIFIED.
5. Run one collection against the live API and compare with the fixtures (#552).

## UNVERIFIED list (all tied to #552)

* That `metric_type=total_value` with `since` and `until` Unix seconds is accepted
  for the five activity metrics, and the answer shape `data[].total_value.value`.
  (The note says `period=day` and `metric_type` are parameters; it does not give
  the value for this use.)
* That `reach` is a unique count over a window, not additive.
* The `follower_demographics` request (`period=lifetime`, `timeframe=last_30_days`,
  one `breakdown` per call) and the answer shape (`total_value.breakdowns[]` with
  `dimension_keys` and `results[]` of `dimension_values` and `value`). The note
  says `timeframe` is required and that some timeframe values are deprecated, so
  the value used is a guess.
* The label spellings: ages `13-17` ... `65+`, genders `F`/`M`/`U`, upper-case ISO
  country codes. An unrecognized label is a bad row, never folded.
* The route `/v27.0/<IG_ID>/insights` for an Instagram-Login token and a numeric
  id (the follower read uses `/me`; `me` is not assumed to work for insights).
  The version `v27.0` comes from `meta_user.py`.
* Whether a business account, the permission or a follower minimum is required for
  each metric (above).
* The 48 hour lag: the note lists it as unverified, and the 3 day end is this
  project's margin.
* The rate limit formula (4800 x impressions, note: unverified) and the cost of a
  retried attempt. A run makes 4 to 12 requests.
* Meta's current terms on retention and on private analysis of insights data (the
  terms were read 2026-11-19 and name no day count).

## Retention and deletion

[Platform terms](research/platform-terms-retention.md) (Meta Platform Terms, read
2026-11-19): no fixed day count; delete when no longer necessary, when Meta
requests, or when the user requests deletion (3.d). The 30-day refresh-or-delete
rule is the owner's YouTube decision applied here as a conservative default
(ASSUMED; the owner may change `RETAIN_DAYS` in `instagram_insights.py`).

`reach_around.counts.instagram_insights.deletion_candidates(records, today,
revoked=False)` selects the records that are due: older than 30 days, or all of
them when `revoked`. It only selects; `counts refresh` uses it to print, every run,
`instagram: retention notice: N insights record(s) are past 30 days` (a count,
never a value).

To delete, use the history tombstone tool (#586):

```
reach-around counts history-redact --file <history> --provider instagram_insights \
  --reason user_request --trigger-date YYYY-MM-DD            # dry run (counts only)
reach-around counts history-redact --file <history> --provider instagram_insights \
  --reason user_request --trigger-date YYYY-MM-DD --before YYYY-MM-DD --confirm
reach-around counts history-verify --file <history>
```

Use `--reason platform_revocation` or `user_request` on a revoked token or a
deletion request. A redaction blanks the values but git history keeps the old ones;
a true deletion needs the owner-approved rewrite described in the platform terms
note. Revoking the Meta app or token means every record is due: redact them all.

## Quota

Each run makes four report queries (activity, age, gender, country) and, in a bad
case, up to three attempts each. The note gives the limit as 4800 x impressions of
the account per 24 hours (UNVERIFIED); this collector uses a handful of calls.
