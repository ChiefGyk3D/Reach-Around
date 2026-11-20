<!-- SPDX-License-Identifier: Apache-2.0 -->
# Twitch channel analytics (private, owner only): what the notes verify

Refs #392 and #551, epic #246. Display terms: #625. **Status: no collector is built
by this change.** This page is the step 1 record (what the repository's own notes
verify) and the list of open items. The decision that blocks the collector is
below. Nothing here has been checked against a live Twitch API, and nothing in
this page calls Twitch.

Sources: [research/twitch.md](research/twitch.md) (read 2026-10-23, review verdict
FIX, corrections applied), [twitch-source.md](twitch-source.md) (read
2026-11-09), [research/platform-terms-retention.md](research/platform-terms-retention.md)
(read 2026-11-19), [research/platform-metrics-matrix.md](research/platform-metrics-matrix.md)
and the media kit v2 spec section 6.4.

## Why the collector is not written yet

The owner decided on 2026-11-19 ([platform-terms-retention.md](research/platform-terms-retention.md),
Owner decisions item 2, which supersedes the 2026-11-08 decision): "History is
hand-kept by the owner. No Twitch API data is stored beyond the 24-hour cache."
The Twitch Developer Agreement row (VERIFIED 2026-11-19, last modified
2024-12-04) says copies of Twitch content are not stored unless there is written
authorization from Twitch, the developer controls the rights, or the data is
cached for 24 hours without sharing. Kick has the same rule and the same decision
("Kick and Twitch are treated equally").

A collector in the pattern of [YouTube Analytics](youtube-analytics.md) and
[Instagram insights](instagram-insights.md) writes API values to the history
file, which is unbounded retention in a private git repository. That is storage
beyond 24 hours. The YouTube and Meta collectors have an owner decision that
allows it (30 days, or the documented-purpose rule); Twitch has the opposite one.
The owner kept the 2026-11-19 decision (comment on #392 and #551), so the
collector is not built and the live measures stay hand-kept; see
[research/twitch-sampling-design.md](research/twitch-sampling-design.md).
Hand-kept entry from the creator's own dashboard is not affected by the rule.

## Step 1: what the notes say the API provides, and what it does not

Marks: V = stated as verified in the note on the date given; U = the note says
UNVERIFIED, or does not state it.

### Provided by an official API for the owner's own channel

| Measure | Endpoint | Token and scope | Mark |
| --- | --- | --- | --- |
| Follower total | `GET /helix/channels/followers`, `total` | twitch-source.md: user token with `moderator:read:followers`. twitch.md: the total also comes back without that scope. The two notes disagree; the shipped follower source uses the user token | V (twitch-source.md, 2026-11-09); the disagreement is open |
| Subscriber total | `GET /helix/subscriptions` | user token with `channel:read:subscriptions`, the token's user must be the broadcaster | endpoint and scope V; the response shape (total, points) U |
| Stream snapshot | `GET /helix/streams`: `viewer_count`, `started_at`, `title`, `type`, `language`, `is_mature`, `thumbnail_url` | app or user token; only streams broadcasting now; no history | V (twitch.md, 2026-10-23) |
| Past streams | `GET /helix/videos` with `type=archive`: `view_count`, `duration`, `created_at`, `published_at`, `stream_id`, `title`, `language`, `viewable`, `muted_segments` | app or user token | fields V; query parameters, sort order, paging, date and duration formats, and VOD retention are not in the notes (U) |
| Bits leaderboard | `GET /helix/bits/leaderboard` | `bits:read` | scope V, nothing else read |
| Game analytics | CSV link, last 365 days, needs 5+ hours of broadcast | `analytics:read:games` | scope V, shape U |
| Extension analytics | `GET /helix/analytics/extensions` | `analytics:read:extensions` | only if the owner owns an extension |
| Clips | Get Clips | not stated | U |

### Not provided by any API in the notes

| Measure | What the notes say |
| --- | --- |
| Average concurrent viewers over a period | No endpoint. Get Streams has no history, so a persistent sampler (poll while live) or the creator's own dashboard export is needed |
| Peak concurrent viewers over a period | Same |
| Hours watched | Same. A VOD `view_count` is lifetime views of one recording, not hours watched, and must not be presented as it |
| Unique viewers | Not found in the API reference (absence UNVERIFIED) |
| Audience demographics | Not found in the API reference (absence UNVERIFIED) |
| Revenue | Not found; dashboard only (UNVERIFIED absence) |

Issue #551 says Twitch "exposes ... average stream views, average concurrent
viewers and 30-day watch hours". The notes do not confirm any of those three as an
API measure. twitch.md withdrew the earlier "dashboard only" claim as unsupported
and says only that they are not found as endpoints; this page follows the notes.

## UNVERIFIED list (tied to the owner's live check)

* The Twitch Developer Agreement was read by a person on 2026-11-19 (a row in the
  terms note); what #625 still needs is under Owner-needed items.
* Get Videos query parameters (`user_id`, `type`, `first`, paging), ordering,
  the `created_at` and `duration` formats, and how long VODs are kept.
* The Get Subscriptions response shape.
* Whether a 429 carries `Retry-After`, and the token bucket size (not recorded).
* That an app token and a user token give the same Get Videos rows for the owner.
* Twitch's own definitions of average concurrent, peak concurrent and unique
  viewers (the help page did not render).

## Owner-needed items

1. Answered: stored Twitch API data stays not allowed (decision kept); the open
   choice is whether to investigate a dashboard-export import.
2. Decide whether any future API-derived Twitch or Kick data may be shown on the
   public kit (#625). The terms readings are recorded in
   [platform-terms-retention.md](research/platform-terms-retention.md) (Twitch read
   from the vendor page on 2026-11-19; Kick from text the owner pasted that day,
   not checked against the live page), with the storage and display rules. What
   #625 still needs is its own acceptance: one note per platform under research/,
   same shape and dated within a day of each other, each giving the terms URL, the
   date, the reader and a verdict on caching, storage and public display, and the
   display policy table citing both by path.
3. Authorize the app once if a user token with new scopes is wanted
   (`channel:read:subscriptions`), and keep the secrets in the secret manager.
4. Run one live read of Get Videos and compare with a fixture before any
   collector is merged.

## What a collector would look like if the owner reverses the decision

For review only; none of it is written. Pattern: the merged YouTube and Instagram
collectors.

* A new closed provider tag `twitch_analytics`, added to `API_PROVIDERS` and
  `PRIVATE_PROVIDERS` in `counts/history.py`, so the report refusal
  (`funnel/social.py`), `history-redact --provider`, the retention notice and the
  `fit_batch` limit all select it by the tag.
* `analytics: true` on a `twitch` source, at most one per config, a strict bool.
* Credentials only from the variables the follower source reads
  (`TWITCH_CLIENT_ID`, `TWITCH_USER_ACCESS_TOKEN`); in a header, never in argv,
  logs or errors; bounded HTTP; no redirect followed; GET only.
* Two figures only, because only these are in the notes: `vods_30d` (archives
  created in 30 UTC days) and `vod_views_30d` (sum of their lifetime
  `view_count`). A full page with a next cursor and the oldest row still inside
  the window is refused as `window_incomplete`, never stored short.
* A retention notice with a count, and a `RETAIN_DAYS` that the decision sets
  (1 under the 24-hour rule, which a history file cannot meet).
* Subscribers wait for a verified response shape.
