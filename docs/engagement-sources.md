<!-- SPDX-License-Identifier: Apache-2.0 -->
# Views and engagement: TikTok video stats and Threads insights

Two optional collectors, off by default. Set `insights: true` on a `tiktok` or
`threads` source in `numbers.sources` to turn one on. They run in
`reach-around counts refresh`, only when `numbers.history_file` is set, only for
a source whose follower read succeeded in the same run, and the figures go to
the [history store](social-history.md) as `source: api` records with a `note`
that states the window and formula. Nothing else stores them.

Nothing here called a live API. Fixtures in `tests/test_counts_insights.py` are
SYNTHETIC: written by hand from the response shapes the vendor pages describe.

Marks: VERIFIED = read on the vendor page on 2026-11-09 (URL given).
UNVERIFIED = not stated on a page that could be read, or not tested live.

## Display policy

These metrics are OWNER REPORT ONLY. No kit, link page or public file may show
them yet: the display work (#397) with its floors, and the field marker work
(#424), are not built. The site build never reads them, and a test fails if site, kit, editor, config or preview code imports the
history or insights modules (the figures live only in the history file). When #397 lands,
these metrics enter the policy table as owner-report-only first. Both vendors
treat the figures as the account owner's own data; Meta Platform Terms and the
TikTok Developer Terms ask for deletion when the app stops or on request (see
the follower guides). If an app or token is revoked, delete these records too.

Every final figure must be a whole number from 0 to the history maximum
(10^12), else `bad_aggregate` and nothing is stored. Each figure is an aggregate over a stated window, stored as a whole number
(never a zero for a failure; a missing or wrong-typed field leaves that metric
out). Do not add figures across platforms: the definitions differ.

## TikTok (issue 395)

Config: `{platform: tiktok, kind: tiktok, insights: true}`. Needs the scope
`video.list` in addition to `user.info.stats`, and the same fixed variable
`TIKTOK_ACCESS_TOKEN`. The insights call never refreshes a token (a rotation
needs the sink and belongs to the follower read); an expired token ends in
`token_expired` and the follower run's health line tells the owner what to do.

| Fact | Mark | Source |
| --- | --- | --- |
| `POST https://open.tiktokapis.com/v2/video/list/`, scope `video.list`, public videos, newest first; JSON body `max_count` (default 10, at most 20) and `cursor` (UTC milliseconds, older than it); answer `data.videos`, `data.cursor`, `data.has_more`, `error` | VERIFIED | https://developers.tiktok.com/doc/tiktok-api-v2-video-list |
| Video fields `create_time` (UTC seconds), `view_count` (int64), `like_count`, `comment_count`, `share_count` (int32) | VERIFIED | https://developers.tiktok.com/doc/tiktok-api-v2-video-object |
| The page documents the `fields` query parameter only by example; the list used here (`id,create_time,view_count,like_count,comment_count,share_count`) is accepted as written | UNVERIFIED |  |
| No account-level view total exists; the number is a sum of per-video counts, each a lifetime count | INFERRED |  |
| `video.list` returns real data in a Sandbox, or is granted to a reviewed app together with `user.info.stats` | UNVERIFIED |  |
| Rate limit 600 requests a minute; a run makes at most 5 calls | VERIFIED (research note 2026-11-08) | https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit |

Metrics (history, platform `tiktok`):

- `videos_28d`: count of public videos whose `create_time` is within 28 days
  before the run.
- `video_views_28d`: sum of `view_count` over those videos. It is lifetime views
  of videos posted in the window, not views that happened in the window.
- `video_engagement_28d`: sum of `like_count + comment_count + share_count` over
  the same videos.

Paging: 20 videos a page (a page with more is refused), at most 5 pages (100
videos). `has_more` must be a real boolean: `true` continues, `false` ends, any
other type or an absent value is `missing_field`. If the 5th page still
holds videos inside the window, nothing is stored and the failure word is
`window_incomplete`. A cursor that does not go back is `missing_field`.

## Threads (issue 394)

Config: `{platform: threads, kind: threads, user_id: "...", insights: true}`.
Needs `threads_manage_insights` (the follower read already needs it) and the
fixed variable `THREADS_ACCESS_TOKEN`. The call never refreshes a token.

| Fact | Mark | Source |
| --- | --- | --- |
| `GET https://graph.threads.com/v1.0/<id>/threads_insights` with `metric` (comma list) and optional `since` and `until` (Unix time or date; default yesterday to today; earliest 1712991600) | VERIFIED | https://developers.facebook.com/docs/threads/insights |
| `views` is a time series (`period: day`, `values` of `value` and `end_time`); `likes`, `replies`, `reposts`, `quotes` are total values (`total_value.value`); all accept `since` and `until`; `replies` counts only top-level replies | VERIFIED | same |
| One example on that page shows `views` as a `total_value`; the collector accepts either shape for `views` | VERIFIED (the contradiction), which shape is live UNVERIFIED | same |
| Whether `until` is inclusive, the data delay, and that a day boundary is UTC midnight (the page's example shows `end_time` at 08:00 UTC) | UNVERIFIED |  |
| `clicks` is keyed by third-party link URLs | VERIFIED | same; not collected |
| Demographics need 100 followers | VERIFIED | same; not collected here |

Metrics (history, platform `threads`):

- `views_7d`: sum of the daily `views` (series shape) for `since` = 00:00 UTC
  seven days before the run day to `until` = 00:00 UTC of the run day. The answer
  is checked against that request: `period` must be `day`, every `end_time` must
  parse and fall from `since` to one day after `until`, none may repeat, and at
  most 8 points are accepted; otherwise `window_mismatch` and nothing is stored.
  The day boundaries are the API's own; see the UNVERIFIED row.
- `views_vendor_window`: `views` when the answer is the `total_value` shape. It
  carries no time information, so its window is vendor-defined and it is stored
  under its own name, never as `views_7d`.
- `engagement_7d`: `likes + replies + reposts + quotes` for the same
  request (each is a `total_value`, so the window is vendor-defined). Left out when
  any of the four is missing.

## Failure words

`bad_credential_name`, `bad_source_setting`, `missing_credential`,
`invalid_credential`, `token_expired`, `token_rejected`, `scope_missing`,
`api_error`, `missing_field`, `window_incomplete`, `window_mismatch`, `bad_aggregate`, `redirect`, `too_large`,
`bad_json`, `content_type`, `rate_limited`, `server_error`, `bad_host`,
`http_<status>`, `fetch_failed`. A failure prints `<platform>: insights error
(<word>)` to stderr, keeps the follower number and its history record, and makes
the run exit 3. No token, URL or response text appears in any message.

## Safety checks

Fixed hosts (`open.tiktokapis.com`, `graph.threads.com`) over https, no
redirect followed, 10 second timeout, 64 KiB body cap, JSON only. The credential
variable names are fixed in code and checked again at use. One wrapper makes
every call and turns any unexpected exception into `fetch_failed` with no cause
kept. The TikTok token travels in the `Authorization` header; the Threads token
is in the query string as in the vendor examples (header use is unverified, see
[the follower guide](instagram-threads-sources.md)). The TikTok POST is sent
once, never replayed.
