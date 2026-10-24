# Twitch research note (Reach Around issue #11)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #11. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from dev.twitch.tv docs unless noted. The API reference page is about 517k characters; the fetch tool could not surface the Get Streams, Get Broadcaster Subscriptions, Get Clips and Get Videos sections, so those details are UNVERIFIED and must be rechecked.

> **Verified from the primary reference on 2026-10-23** (dev.twitch.tv/docs/api/reference, read in a real browser). This block supersedes any conflicting statement below.
>
> - **Get Streams** (`GET /helix/streams`, app or user access token): returns only streams that are broadcasting right now. Fields include `viewer_count`, `started_at`, `title`, `type`, `language`, `is_mature`, `thumbnail_url`. It has no history, so a collector that wants viewer-count curves must sample it while the creator is live.
> - **Get Videos** (`GET /helix/videos`, app or user access token): returns videos including `type=archive` (on-demand VODs of past streams) with `view_count`, `duration`, `created_at`, `published_at`, `stream_id`, `title`, `language`, `viewable`, `muted_segments`. So per-stream history does exist through the API for as long as VODs are kept. The earlier claim that per-stream history is dashboard-only was unsupported and is withdrawn. How long Twitch keeps VODs depends on account type and is not stated on this page (UNVERIFIED).
> - **Get Broadcaster Subscriptions** (`GET /helix/subscriptions`): needs a user access token with the `channel:read:subscriptions` scope, and the token's user must be the broadcaster.
> - **Get Channel Followers** (`GET /helix/channels/followers`): the total follower count comes back even without the follower scope. The follower list needs `moderator:read:followers` and a token for the broadcaster or a moderator.
> - **Design consequence:** the daily job can read follower total, subscriber total and recent VOD stats with no sampling. Concurrent-viewer curves and peak viewers during a live stream need a sampler (poll every minute or two while live, or EventSub stream.online and stream.offline to start and stop it). The sampler is a persistent job and belongs in the homelab collector.
> - **Still unverified:** the Twitch Developer Agreement text (including any limit on how long data may be stored). The earlier 24-hour storage rule came from forum posts only and should not be relied on.

## Summary

Twitch has an official Helix REST API plus EventSub. Registration is self-service with no review step. Owner-level data (followers list, subscribers) needs a user token with specific scopes. No per-stream analytics endpoint was found in what was read. Review correction: the earlier claim that per-stream analytics are "dashboard-only" is UNVERIFIED, because Get Videos (type archive), Get Clips and Get Streams were never read (VOD archives may expose created_at, duration and view_count while retained). VOD metadata may serve as history, unchecked. Until checked, plan to build history by sampling.

## Metrics

| Metric | How | Scope / permission |
|---|---|---|
| Follower total | `GET /helix/channels/followers` | `moderator:read:followers` user token (broadcaster or moderator); without that only the total is returned (source 1) |
| Subscribers | Get Broadcaster Subscriptions | scope `channel:read:subscriptions` (source 3); endpoint details UNVERIFIED |
| Live status, viewer count | Get Streams (default page 20, max 100, source 2) | UNVERIFIED which token types; sample it on a schedule |
| Past streams | Get Videos (archives) | UNVERIFIED; retention of VODs and fields not confirmed |
| Clips | Get Clips | UNVERIFIED |
| Bits leaderboard | `GET /helix/bits/leaderboard` | `bits:read` |
| Game analytics | Get Game Analytics: CSV link, last 365 days default; needs 5+ hours broadcast | `analytics:read:games` |
| Extension analytics | `GET /helix/analytics/extensions` (CSV from 2018-01-31) | `analytics:read:extensions`; only relevant if you own an extension |
| Audience demographics | Not found in the API reference | not available (UNVERIFIED absence) |
| Monetization (revenue) | Not found | dashboard only (UNVERIFIED absence) |

## User setup

1. Enable 2FA and verify email on the Twitch account (required for registration, source 4).
2. Register an app in the developer console: unique name, OAuth redirect URL, category. Copy client ID and secret.
3. One-time Authorization Code flow requesting only needed scopes (`moderator:read:followers`, `channel:read:subscriptions`).
4. Save the refresh token as a secret; the job refreshes on 401 (Twitch recommends reacting to 401, not a timer).

Public data such as stream status and clips can use an app access token (client credentials) with no user consent. UNVERIFIED for each endpoint (the review could not confirm the Get Streams token type either).

## Rate limits and cost

Free. Token-bucket points per app per minute; default cost 1 per call; HTTP 429 when empty; headers `Ratelimit-Limit`, `Ratelimit-Remaining`, `Ratelimit-Reset`. User-token and app-token buckets are separate (source 2). The bucket size number was not on the fetched page (UNVERIFIED). A daily job is far below any limit.

## EventSub

`stream.online` and `stream.offline` are available. Webhook transport uses an app token and needs an HTTPS callback on port 443, which a home-hosted daily job may not have. WebSocket transport requires a user access token and a long-lived connection (max 3 connections, 300 subscriptions each, `max_total_cost` 10 per user; source 5; these limits were not verified by the review). `stream.online` costs 1 unless the broadcaster authorized your app, then 0. `channel.subscribe` needs `channel:read:subscriptions`; `channel.follow` v2 needs `moderator:read:followers`. A once-a-day batch job does not fit EventSub; polling Get Streams every few minutes is simpler, or an optional always-on listener captures exact peak/duration. Conduit transport: UNVERIFIED.

## Freshness and history

Followers, streams: current values only as far as read. History must be accumulated by the tool unless Get Videos archive metadata proves sufficient (UNVERIFIED). Game analytics reaches back 365 days (above).

## Risks and ToS limits

- Developer Services Agreement: the fetched page returned only navigation text. Forum posts (secondary, undated or old, source 7) quote a rule limiting storage of Twitch content to a 24-hour cache unless you hold authorization, and a Twitch staff reply that obtaining user permission via OAuth makes longer storage compliant, "general guidance" only. Resyndication of data is said to be against the agreement without a specific deal. Treat as UNVERIFIED and unresolved. The reviewer also got only navigation text from the agreement page (reviewer fetch 2026-10-23) and the 24-hour cache claim is forum-only; nothing fetched confirms or refutes it.
- Users must be able to delete their data; honor disconnects.
- Public display of owner-authorized data (follower and sub counts) is a gray area; keep sub and revenue data private by default.
- Registration collects no review, but Twitch can revoke apps.

## Token lifetime and unattended access

Access tokens expire (`expires_in`); app tokens cannot be refreshed, just re-requested. A confidential client's refresh tokens do not expire; public client refresh tokens expire after 30 days (source 6). A self-hosted job holds the client secret, so it is a confidential client and can run unattended. Refresh tokens are invalidated if the user changes their password or disconnects the app; then re-consent is needed. Max 50 valid access tokens per refresh token.

## Fallbacks

Public counts through app token (no consent). Scraping the Twitch site or Social Blade, TwitchTracker, Streams Charts: UNVERIFIED and likely against their terms. Dashboard CSV export: UNVERIFIED whether available. Manual entry for peak and average viewers.

## Verdict

Official API. Followers and subs via user token; live metrics via self-built sampling. Stream analytics history: no per-stream analytics endpoint found; VOD metadata (Get Videos archive) may serve as history, UNVERIFIED and unchecked. The earlier "dashboard only, not API" claim is removed as unsupported. Manual entry remains the fallback for peak and average viewers if no API route is confirmed.

## Sources (fetched 2026-10-23)

1. https://dev.twitch.tv/docs/api/reference/
2. https://dev.twitch.tv/docs/api/guide/
3. https://dev.twitch.tv/docs/authentication/scopes/
4. https://dev.twitch.tv/docs/authentication/register-app/
5. https://dev.twitch.tv/docs/eventsub/manage-subscriptions/ and /eventsub-subscription-types/
6. https://dev.twitch.tv/docs/authentication/refresh-tokens/
7. Secondary: https://discuss.dev.twitch.com/t/storage-of-data-from-twitch-api/63884 and related forum threads (via search); https://legal.twitch.com/en/legal/developer-agreement/ (body not retrieved)

## Open questions for the maintainer

1. Accept the 24-hour-cache ambiguity, or ask Twitch developer support for written confirmation that OAuth-consented self-hosted storage of history is fine?
2. Do you want an optional always-on EventSub WebSocket listener, or polling only?
3. Public site: show follower counts only, keep subs private?
4. Should the research be redone with direct reading of the reference sections that did not load?

## Review

Verdict: FIX. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
Changed: removed the "dashboard only, not API" claim from the summary and verdict and marked it UNVERIFIED (Get Videos, Get Clips, Get Streams never read; VOD metadata may serve as history); noted the storage-rule page and 24-hour cache claim are still unresolved; marked EventSub limits and Get Streams token type as unconfirmed. Confirmed by the review: `moderator:read:followers` behavior, `channel:read:subscriptions`, `bits:read`, analytics scopes, refresh-token lifetimes, Get Streams paging, separate app and user buckets. Not verified: subscriber response shape (total/points), Get Streams token type, EventSub transport limits.
