# Platform metrics matrix (issue #380)

> **Status: draft.** Compiled on 2026-11-19 from facts already recorded in two places: the vendor-doc research posted on issue #380 (read from vendor docs on 2026-11-08, no API called) and the per-platform notes in this directory (read 2026-10-23, reviewed under epic #1). Nothing here comes from a new fetch. Each cell carries VERIFIED (a vendor page was read on the date shown in the cited source) or INFERRED (the sources support it only indirectly, or a note marked it UNVERIFIED). "Not recorded" means none of the sources says.

Note: `matrix.md` in this directory is the Matrix chat protocol note, not this document.

Date read: 2026-11-08 for facts that come from the #380 comment (marked "380"); 2026-10-23 for facts that come from the platform note (marked by filename). Source URLs are listed per platform in each section and in the linked notes.

Tier 1 platforms: Twitch, YouTube, Instagram, Threads, TikTok. Kick is included as requested. Tier 2 rows are at the end and only cover what the notes record.

## Summary grid

| Platform | Followers | Views | Watch time | Demographics |
|---|---|---|---|---|
| Twitch | Yes, user token (VERIFIED, 380) | Live viewers and VOD views (VERIFIED, twitch.md) | Not available | Not available (INFERRED: absence not proven) |
| YouTube | Yes, subscribers (API key or OAuth) | Yes (Analytics, OAuth) | Yes, the only platform with it (VERIFIED, 380) | Yes (Analytics, OAuth) |
| Instagram | Yes, `followers_count` | Yes, `views` replaces `impressions` | Not available | Yes, 100+ followers |
| Threads | Yes, `followers_count` | Yes, `views` | Not available | Yes, 100+ followers |
| TikTok | Yes, `follower_count` (`user.info.stats`) | Per-video views via `video.list` | Not available | Not available in the Display API (VERIFIED, 380) |
| Kick | No follower field (VERIFIED, 380 and kick.md) | Live viewer count only | Not available | Not available |

Summary facts from the #380 comment: watch time exists only on YouTube; demographics exist only on Instagram, Threads and YouTube; everything else is "not available".

## Twitch

| Item | Detail | Status |
|---|---|---|
| Followers | `GET /helix/channels/followers`. The endpoint "Requires a user access token that includes the moderator:read:followers scope"; app tokens are not listed (380). This supersedes the 2026-10-23 note, which said the total returns without the scope. | VERIFIED (380, 2026-11-08) |
| Views | Get Streams (`viewer_count`, live only, no history; app or user token) and Get Videos (`view_count` on archives, app or user token). VOD retention depends on account type and is not stated. | VERIFIED (twitch.md, 2026-10-23); VOD retention INFERRED |
| Watch time | Not available. | VERIFIED (380 summary) |
| Demographics | Not found in the API reference. | INFERRED (twitch.md marks the absence UNVERIFIED) |
| Auth | OAuth. User token for followers (`moderator:read:followers`) and subscribers (`channel:read:subscriptions`); app token (client credentials) for public stream and video data. | VERIFIED (380, twitch.md) |
| Quota and rate limit | Token bucket per app per minute, default cost 1 per call, HTTP 429 when empty, `Ratelimit-*` headers; user and app buckets are separate. Bucket size not recorded. | VERIFIED (twitch.md); size not recorded |
| Token lifetime | Access tokens expire (`expires_in`). A confidential client's refresh tokens do not expire; public client refresh tokens expire after 30 days. Refresh tokens die on password change or app disconnect. | VERIFIED (twitch.md) |
| App review | None; registration is self-service. Needs 2FA and a verified email on the account. | VERIFIED (twitch.md) |
| Cost | Free. | VERIFIED (twitch.md) |
| Terms | Developer Services Agreement body not retrieved; the 24-hour storage rule seen on forums is unconfirmed. Stays open for #382. | Not recorded (380) |
| Sources | https://dev.twitch.tv/docs/api/reference/ ; https://dev.twitch.tv/docs/authentication/scopes/ ; https://dev.twitch.tv/docs/authentication/refresh-tokens/ ; https://dev.twitch.tv/docs/authentication/register-app/ | |

Design consequence from twitch.md: peak and concurrent viewers need a sampler while live; follower total, subscriber total and recent VOD stats need no sampling. Note: [twitch.md](twitch.md).

## YouTube

| Item | Detail | Status |
|---|---|---|
| Followers (subscribers) | Data API `channels.list` `part=statistics`. Public lookup by API key (rounded count); `mine=true` needs OAuth `youtube.readonly`. | VERIFIED (youtube.md); field list and rounding INFERRED |
| Views | Data API `videos.list` and `channels.list` totals (API key); Analytics API `reports.query` views (OAuth `yt-analytics.readonly`). | VERIFIED (youtube.md) |
| Watch time | Analytics `estimatedMinutesWatched`, `averageViewDuration`. The only platform with watch time. | VERIFIED (380, youtube.md) |
| Demographics | Analytics "Viewer demographics": `ageGroup`, `gender`, `viewerPercentage`; country in specific reports. Always needs authorization. | VERIFIED (youtube.md, 380) |
| Auth | API key for public counts. OAuth 2.0 with `yt-analytics.readonly` for Analytics; `yt-analytics-monetary.readonly` for revenue (Partner Program only). | VERIFIED (youtube.md) |
| Quota and rate limit | Default 10,000 units per day; list calls cost 1 unit; `search.list` has its own 100-call per day bucket; resets at midnight Pacific. A daily job needs a few dozen units. | VERIFIED (youtube.md) |
| Token lifetime | Access token about one hour. Refresh token: expires after 7 days while the consent screen is in Testing; otherwise lasts until revoked, 6 months idle, 100+ tokens per account per client, or admin policy. | VERIFIED (youtube.md) |
| App review | Google says sensitive scopes need OAuth app verification for public apps; turnaround and whether these scopes are sensitive are not stated. A personal consent screen can be set to In production, with an unverified-app warning. | INFERRED (youtube.md marks UNVERIFIED) |
| Cost | No fee documented. | VERIFIED (youtube.md) |
| Policy blocker | Developer Policies III.E.3.b: Authorized Data must not be displayed to anyone but the authorizing user. Analytics (views, watch time, demographics) always needs authorization, so public or sponsor-facing display (#397) is a blocker; a private report is fine. A public subscriber count read with an API key may be stored 30 days at most unless authorized. | VERIFIED (380, 2026-11-08) |
| Data lag | Analytics lags 48 to 72 hours. | VERIFIED (youtube.md) |
| Sources | https://developers.google.com/youtube/terms/developer-policies ; https://developers.google.com/youtube/v3/determine_quota_cost ; https://developers.google.com/youtube/analytics/channel_reports ; https://developers.google.com/identity/protocols/oauth2 | |

Note: [youtube.md](youtube.md).

## Instagram

| Item | Detail | Status |
|---|---|---|
| Followers | `GET /<IG_ID>?fields=followers_count`, permission `instagram_business_basic`. Professional (business or creator) accounts only. | VERIFIED (instagram.md) |
| Views | Insights `views`; `impressions` was removed and `views` replaces it (380). | VERIFIED (380, 2026-11-08) |
| Watch time | Not available. | VERIFIED (380 summary) |
| Demographics | Insights `follower_demographics` (age, city, country, gender); needs 100+ followers; permission `instagram_business_manage_insights`. | VERIFIED (380); breakdown detail from instagram.md |
| Auth | Instagram Login (no Facebook Page needed). Token from the App Dashboard, plus the two permissions above. | VERIFIED (instagram.md) |
| Quota and rate limit | Calls per 24 hours = 4800 x impressions of the account. A daily job uses a handful of calls. | INFERRED (the review could not confirm the formula) |
| Token lifetime | 60 days. Refresh via `refresh_access_token` needs a token at least 24 hours old, unexpired, with `instagram_business_basic`. A lapsed token needs a manual re-login. | VERIFIED (380, instagram.md) |
| App review | Not needed for accounts the app owner owns or manages (Standard Access). Advanced Access (other people's accounts) needs App Review and Business Verification; duration not stated. | VERIFIED (instagram.md review note) for Standard Access; duration not recorded |
| Cost | No fee mentioned in the docs. | INFERRED |
| History | User metrics stored up to 90 days (not confirmed); daily snapshots needed. | INFERRED |
| Sources | https://developers.facebook.com/documentation/instagram-platform/insights.md ; https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login ; https://developers.facebook.com/docs/graph-api/overview/rate-limiting | |

Note: [instagram.md](instagram.md).

## Threads

| Item | Detail | Status |
|---|---|---|
| Followers | `threads_insights` metric `followers_count` (total value, no since/until). | VERIFIED (380, threads.md) |
| Views | `views` (profile views, time series) and per-post `views`. Insights exist since 2024-04-13 only; not guaranteed before 2024-06-01. | VERIFIED (380, threads.md) |
| Watch time | Not available. | VERIFIED (380 summary) |
| Demographics | `follower_demographics` with one breakdown (country, city, age or gender); needs 100+ followers. | VERIFIED (380, threads.md) |
| Auth | Meta app with the Threads use case; permissions `threads_basic` and `threads_manage_insights`. Creator added as a Threads Tester. | VERIFIED (threads.md) |
| Quota and rate limit | Calls per rolling 24 hours = 4800 x impressions (minimum 10), plus CPU and total time budgets. | VERIFIED (threads.md); formulas unconfirmed by the review |
| Token lifetime | Short-lived 1 hour, long-lived 60 days, refreshed with `GET /refresh_access_token`. The permission grant extends 90 days for public profiles. Refresh precondition (such as a minimum token age) not confirmed. | VERIFIED (threads.md); precondition INFERRED |
| App review | Testers need no App Review (380). Review is needed for users without a role on the app, and the app must be published. | VERIFIED (380, threads.md) |
| Cost | No fee mentioned in the docs. | INFERRED |
| Sources | https://developers.facebook.com/docs/threads/insights ; https://developers.facebook.com/docs/threads/get-started ; https://developers.facebook.com/docs/graph-api/overview/rate-limiting | |

Note: [threads.md](threads.md).

## TikTok

| Item | Detail | Status |
|---|---|---|
| Followers | `GET /v2/user/info/` field `follower_count`; needs scope `user.info.stats`. | VERIFIED (380, tiktok.md) |
| Views | Per-video `view_count` via `POST /v2/video/list/` (scope `video.list`, 20 per page, public videos). | VERIFIED (tiktok.md); full back-catalog coverage INFERRED |
| Watch time | Not available. | VERIFIED (380 summary) |
| Demographics | No demographics in the Display API; only in the TikTok Analytics app or export. | VERIFIED (380) |
| Auth | Login Kit OAuth plus Display API. Scopes `user.info.basic`, `user.info.stats`, `video.list`. | VERIFIED (tiktok.md) |
| Quota and rate limit | 600 requests per minute per endpoint (sliding window). | VERIFIED (tiktok.md) |
| Token lifetime | Access token 24 hours; refresh token 365 days, rotating (store the new one each run). | VERIFIED (tiktok.md) |
| App review | Required for production. Needs a demo video of the full flow (up to five videos, 50 MB each); no timeline stated. Sandbox allows 10 owned accounts without review; indefinite use not confirmed. | VERIFIED (380, tiktok.md); indefinite Sandbox use INFERRED |
| Cost | No fee. | VERIFIED (tiktok.md) |
| Terms | Developer Terms II.1, III.3(a) and III.3(e) read on 2026-10-23: the plain reading is that an Apache-2.0 collector that only calls the API is not blocked; not legal advice. Public display is allowed only to end users on a personal, non-transferable basis, so a public dashboard is a gray area. | VERIFIED text; INFERRED reading |
| Sources | https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info ; https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit ; https://developers.tiktok.com/doc/oauth-user-access-token-management ; https://developers.tiktok.com/doc/add-a-sandbox | |

Note: [tiktok.md](tiktok.md).

## Kick

| Item | Detail | Status |
|---|---|---|
| Followers | No follower field on Channels; the word "follower" does not appear on the Channels page. Needs manual or CSV entry, a webhook counter (`channel.followed`, drifts on unfollows), or a source outside Kick. | VERIFIED (380, kick.md) |
| Views | Live `viewer_count` only (Channels `stream` object or `/public/v1/users/livestreams`); instantaneous. No VOD stats documented. | VERIFIED (kick.md) |
| Watch time | Not available. | VERIFIED (380 summary) |
| Demographics | Not documented. | VERIFIED (kick.md) |
| Subscribers | `active_subscribers_count` now includes gifted subs since 2026-08-11, a definition break. Other fields: `active_gifted_subscribers_count`, `canceled_subscribers_count`. | VERIFIED (380, kick.md) |
| Auth | OAuth 2.1 with PKCE. Client credentials for public data; authorization code flow for user scope (`channel:read`). Needs 2FA on the account. | VERIFIED (kick.md) |
| Quota and rate limit | No rate limit page found. | Not recorded (380, kick.md) |
| Token lifetime | No numeric lifetime in the docs; responses carry `expires_in`. Each refresh returns a new refresh token. | VERIFIED that the docs are silent; invalidation of the old token INFERRED |
| App review | None described for app setup. A separate "App Verification" (badge, chat subscription cap) exists and is not needed. | VERIFIED (kick.md) |
| Cost | No fee recorded. | Not recorded |
| Terms | Developer Terms link not found (380). Stays open for #382. | Not recorded |
| Secret warning | The Channels response carries `stream.key`, the channel stream key. Drop it before anything is stored or logged. | VERIFIED (380, kick.md) |
| Sources | https://docs.kick.com/apis/channels.md ; https://docs.kick.com/getting-started/kick-apps-setup.md ; https://docs.kick.com/getting-started/generating-tokens-oauth2-flow.md ; https://docs.kick.com/changelog.md | |

Note: [kick.md](kick.md).

## Tier 2 (only what the existing notes record)

| Platform | Followers | Views, watch time, demographics | Auth and limits | Note |
|---|---|---|---|---|
| Bluesky | `app.bsky.actor.getProfile` `followersCount`, public AppView, no login (field names INFERRED, from prior knowledge) | No impressions, watch time or demographics; per-post like, repost and reply counts only | No auth for public counts. Limits "generous", no numbers. Free. Public OAuth client sessions last 2 weeks if auth is ever added | [bluesky.md](bluesky.md) |
| Mastodon | `GET /api/v1/accounts/:id` `followers_count`, public | No impressions, watch time or demographics (absence INFERRED) | No token for public data. Default 300 calls per 5 minutes per account and IP; instances can change it. Free. Token lifetime not recorded here | [mastodon.md](mastodon.md) |
| Discord | `approximate_member_count` via `GET /guilds/{id}?with_counts=true` (bot token, no privileged intent) | No analytics API found; join and leave history needs a persistent Gateway connection | Bot token, permission integer 0. Rate limit numbers not recorded here | [discord.md](discord.md) |
| GitHub | `followers` on `GET /users/{username}`, no scope | Traffic views and clones need Administration read and keep 14 days only; the stargazer list is restricted to admins and collaborators since July 2026, counts unaffected (380) | Fine-grained PAT or GitHub App. Rate limit not recorded here | [github.md](github.md) |

X, LinkedIn, Patreon and Social Blade have their own notes in this directory; they are outside the scope of this matrix.

## Cross-cutting limits

- History is thin everywhere. Instagram keeps user metrics about 90 days (INFERRED), GitHub traffic 14 days, Threads insights start 2024-04-13, and Twitch Get Streams has none. The collector stores its own daily snapshots.
- Tokens that expire need refresh jobs and expiry alerts: Instagram and Threads 60 days, TikTok access 24 hours and refresh 365 days, YouTube refresh 7 days in Testing, Bluesky public clients 2 weeks.
- YouTube Authorized Data must stay in a private view (III.E.3.b). This blocks #397 as a public or sponsor-facing display of Analytics figures.
- Terms still unread: Twitch Developer Services Agreement, Kick Developer Terms, Discord Developer Policy (tracked in #382).

## Owner action plan

One plan, so the owner can grant everything in one pass. Every step comes from the notes above; the project performs none of them. Secrets go to the secret manager (Doppler dev environment first), never into the repo or logs.

| # | Step | Access it needs | Source |
|---|---|---|---|
| 1 | Twitch: enable 2FA and verify email; register an app in the developer console (name, redirect URL, category); run the authorization code flow once for `moderator:read:followers` and `channel:read:subscriptions` (a user token is needed for followers per 380); store client ID, secret and refresh token | Twitch account of the broadcaster | twitch.md, 380 |
| 2 | YouTube: create a Google Cloud project; enable YouTube Data API v3 and YouTube Analytics API; configure the OAuth consent screen and move it to In production (otherwise refresh tokens die after 7 days); create an OAuth client; run consent once with `access_type=offline` for `youtube.readonly` and `yt-analytics.readonly`; create an API key for public counts. Keep Analytics output private | Google account that owns the channel | youtube.md, 380 |
| 3 | Instagram: confirm the account is professional; create a Meta Business app with Instagram Login; add the account as a role or tester; generate a token (60 days) with `instagram_business_basic` and `instagram_business_manage_insights`; store token, IG user ID and app secret | Instagram account and Meta developer account | instagram.md |
| 4 | Threads: create a Meta app with the Threads use case (separate Threads app ID and secret); add the profile as a Threads Tester and accept the invite in the Threads app; get a long-lived token with `threads_basic` and `threads_manage_insights`; no App Review for Testers | Threads account and Meta developer account | threads.md, 380 |
| 5 | TikTok: register a developer account; create an app with Login Kit and Display API (`user.info.basic`, `user.info.stats`, `video.list`); verify the terms, privacy and web URLs; either submit for review with a demo video (no stated timeline) or use Sandbox with up to 10 owned accounts; run consent once and persist the rotating refresh token. Demographics stay manual or CSV | TikTok account and developer account | tiktok.md, 380 |
| 6 | Kick: enable 2FA; create an app in the Account Settings developer tab (client ID, secret, redirect URL); use client credentials for live status, and authorization code with PKCE once for `channel:read`; plan manual or CSV entry for followers; read the Developer Terms once the link is found | Kick account | kick.md, 380 |
| 7 | Tier 2, only if wanted: Discord bot token with permission integer 0 and guild ID; GitHub fine-grained PAT (Metadata and Administration read, with an expiry); Bluesky and Mastodon public counts need nothing | Discord server admin; GitHub account | discord.md, github.md, bluesky.md, mastodon.md |

Decisions still open for the owner, from the notes: whether Instagram and Threads demographics are ever shown publicly (default private); whether to submit TikTok for review or use Sandbox; whether an always-on listener (Twitch EventSub, Discord Gateway) is wanted; and the unread terms listed above.
