# StreamElements: API access and metrics (issue #20)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #20. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

> **WARNING (review verdict UNRELIABLE):** the API claims in this note are largely UNVERIFIED because the StreamElements developer docs did not render for the author or for the independent reviewer. Endpoints, scopes, rate limits, tip history and OAuth availability all come from secondary sources. A human must read dev.streamelements.com in a browser before any design depends on this note.

Fetched 2026-10-23. Caveat: the official reference (dev.streamelements.com) is a JavaScript-rendered Stoplight site that returned no content to my fetcher, so most specifics below come from secondary sources and are marked.

## Summary

StreamElements has an official REST API (api.streamelements.com, kappa v2/v3) and a realtime socket gateway. A creator can authenticate with a per-channel JWT copied from the dashboard, or with an OAuth2 access token. Tips, activities, loyalty and channel data are readable for the creator's own channel. Tip history is the revenue view. The JWT reportedly expires about every two weeks, which is the main problem for an unattended daily job. Official rate limits and OAuth approval terms are UNVERIFIED.

## Metrics

| Metric | How to get it | Scope or permission |
|---|---|---|
| Tips (amount, currency, time, donor) | `tips` data, incremental on `createdAt` (PostHog source doc; exact endpoint path UNVERIFIED) | JWT, or OAuth `tips:read` |
| Activity feed (follows, subs, cheers, tips, raids) | `activities`, incremental on `createdAt` | JWT, or `activities:read` |
| Session stats (latest follower, tip, totals) | Sessions API listed by an API directory; paths and fields UNVERIFIED | JWT (scope UNVERIFIED) |
| Loyalty points, leaderboards, store items and redemptions | Leaderboard, store item and redemption tables | `loyalty:read`, `store:read` |
| Channel details | `channel` | `channel:read` |
| Realtime feed (live events) | Astro WebSocket gateway, per directory listing; topics UNVERIFIED | JWT or OAuth (UNVERIFIED) |
| Follower/sub history, demographics, video stats | Not offered by StreamElements; get from Twitch/YouTube/Kick | n/a |

## User setup

1. Sign in to StreamElements, open the account page from the avatar menu.
2. Under Channels, turn on Show secrets and copy the JWT for the channel.
3. Put it in the self-hosted env or secret store (never in the repo).
4. Optional and preferred: apply for an OAuth2 app and request `channel:read tips:read activities:read loyalty:read store:read`. Application process: UNVERIFIED.

Sources for steps: PostHog docs (2026-10-23), community post (2026-10-23, via search summary).

## Risks and ToS

- UNVERIFIED: I could not read the StreamElements Terms of Service or any API terms. Do not assume storing or publicly showing tip data is permitted until the maintainer reads them.
- Tip data contains donor names and messages, which are personal data of third parties. Store aggregates (daily totals) by default; avoid publishing donor names or messages.
- A JWT is a full-power secret for the channel (per community material it can act on the channel). Treat as a credential; prefer OAuth read scopes for least privilege.
- Streamer.bot documents an OAuth consent flow for tips and merch events, which shows a third-party OAuth app exists in practice (docs.streamer.bot, 2026-10-23, via search summary).

## Token lifetime and unattended access

- A community post reports the JWT resets about every two weeks; staff suggested OAuth2 instead. A later poster claimed OAuth was no longer supported. Conflicting, user-reported, UNVERIFIED (support.streamelements.com community post, 2026-10-23). The reviewer found the same thread and nothing official (reviewer fetch 2026-10-23); the OAuth2 reference page returned only a title.
- PostHog's troubleshooting says 401 means "invalid or expired", consistent with expiry.
- A daily job with a pasted JWT will break roughly fortnightly unless OAuth refresh works. Refresh-token lifetime: UNVERIFIED. Design: alert on 401 and tell the user to repaste; document it.

## Rate limits, cost, freshness

- Rate limits: UNVERIFIED. An API directory rates StreamElements limit signaling as absent. Keep the job to a few calls per day with backoff.
- Cost: no price found; the API appears free for account holders (UNVERIFIED).
- Freshness: tips and activities are timestamped and effectively realtime. History depth is UNVERIFIED; start capturing daily from day one rather than relying on backfill.

## Fallbacks

- Dashboard CSV/export of tips: existence UNVERIFIED.
- Manual entry of monthly tip total (low effort, exact if user is honest).
- Twitch/YouTube/Kick native tips (bits, Super Chat) are separate and read via their own APIs.
- No public endpoint exposes a creator's tip totals; Social Blade does not cover them.

## Verdict

Review correction: the earlier verdict "Official API, feasible" is NOT supported. An official API appears to exist, but nearly every specific is secondary or UNVERIFIED (endpoints, scopes, rate limits, tip history, OAuth availability). Do not design on it until someone reads dev.streamelements.com in a browser. Manual entry remains the fallback.

## Sources (all fetched 2026-10-23)

- https://posthog.com/docs/cdp/sources/streamelements (tables, JWT steps, OAuth scopes)
- https://support.streamelements.com/hc/en-us/community/posts/19564098624786-API-to-retrieve-JWT-Token (JWT reset; page itself returned 403, content only via search summary)
- https://apitracker.io/a/streamelements and https://providers.apievangelist.com/providers/streamelements/ (auth types, sessions API, WebSocket; secondary)
- https://dev.streamelements.com/docs/api-docs/cd02cda5171ea-o-auth2 (title only, no content readable)
- https://docs.streamer.bot/guide/integrations/streamelements (OAuth example; secondary)

## Open questions for the maintainer

1. Can you read the dev.streamelements.com pages in a browser and confirm rate limits, the tips and sessions endpoint paths, and tip history depth?
2. Is OAuth2 app registration still open, and who approves it? If not, is a fortnightly JWT repaste acceptable?
3. Do you want donor names stored at all, or aggregates only?
4. Does the ToS allow showing tip totals publicly?

## Review

Verdict: UNRELIABLE. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
Changed: added an opening warning that the API claims are largely unverified and need a human read of the developer docs; replaced the "Official API, feasible" verdict with a statement that it is unsupported; recorded that the reviewer saw only the same community thread. Nothing in this note was confirmed from official documentation.
