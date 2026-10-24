# Mastodon (and compatible software) research note

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #15. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Issue: ChiefGyk3D/Reach-Around#15. All sources fetched 2026-10-23.

## Summary

Mastodon has an official, documented REST API. Any user can register an app on their own instance with no review, and most read data for public accounts needs no token at all. Follower, post, favourite, boost and reply counts are public fields. There are no creator analytics (no impressions or demographics); instance-level stats exist. Behavior varies by instance version, instance configuration and compatible software, so Reach Around should treat the instance as untrusted and degrade gracefully.

## Metrics

| Metric | How to get it | Auth / scope |
|---|---|---|
| Followers, following, post count | `GET /api/v1/accounts/:id` (`followers_count`, `following_count`, `statuses_count`, `last_status_at`) [1][2] | Public. User token optional |
| Resolve handle to id | `GET /api/v1/accounts/lookup?acct=` | Doc lists user token with `read:accounts` [2] |
| Posts | `GET /api/v1/accounts/:id/statuses` (default 20, max 40) | Public for public posts since 2.7.0 [2] |
| Replies, boosts, favourites per post | Status fields `replies_count` (2.5.0), `reblogs_count`, `favourites_count`, `quotes_count` (4.5.0) [3] | Public for public posts. Authorized-fetch mode can block (401) [5] |
| Who favourited / boosted | `GET /api/v1/statuses/:id/favourited_by`, `reblogged_by` (default 40, max 80) | Public for public posts [5] |
| Follower list | `GET /api/v1/accounts/:id/followers` (default 40, max 80); since 4.0.0 no token needed [2] | Public |
| Instance size and activity | `GET /api/v2/instance` (public; includes `usage.users.active_month`), `GET /api/v1/instance/activity` (3 months weekly: statuses, logins, registrations) [6] | Public unless limited federation mode |
| Demographics, impressions, monetization | Not offered by the API | UNVERIFIED (absence only) |

## User setup

1. Enter instance domain and handle. Public counts need nothing else.
2. For authenticated access: `POST /api/v1/apps` with `client_name`, `redirect_uris`, `scopes` (no auth, no approval step documented) [7]. Use out-of-band URI `urn:ietf:wg:oauth:2.0:oob` to show the code [7].
3. Authorize through the authorization-code flow, then store the token as a secret. Request the narrowest scope, such as `read:accounts` or `profile` (4.3.0+) [8].

## Rate limits and cost

Free. Defaults: 300 calls per 5 minutes per account and per IP across all endpoints, with `X-RateLimit-*` headers [4]. Instance admins can tune this; the docs list no config variables for it [9]. UNVERIFIED for non-Mastodon software.

## Risks and ToS

- No central ToS: each instance sets its own rules. Mastodon docs in scope state no limits on storing public data (not addressed on pages read). UNVERIFIED for any given instance.
- Authorized fetch and limited federation mode return 401 or restrict public API; `DISALLOW_UNAUTHENTICATED_API_ACCESS` blocks logged-out reads [9]. A user whose own instance does this needs a token.
- Counts for remote posts viewed from another instance may be stale or partial. The docs give no note either way; UNVERIFIED. Query the account's home instance.

## Token lifetime and unattended access

"Tokens will not expire automatically"; they die only on user or app revocation [10]. No refresh tokens are documented [10]. This is ideal for a daily unattended job: authorize once. User tokens always require a person to approve (client-credentials app tokens only reach two endpoints) [10]. Pixelfed reportedly supports refresh tokens and may differ in token lifetime (secondary sources, UNVERIFIED) [11].

## Compatible software

Pixelfed implements a subset of the Mastodon API; official docs were not found (404). Per forum and aggregator posts, it supports Link-header pagination and OAuth app registration [11]. Exact fields (for example `followers_count`) are UNVERIFIED; check Pixelfed `routes/api.php`. Other forks (Glitch, Hometown, Akkoma, GoToSocial, Misskey) were not researched; treat as UNVERIFIED. Use `GET /api/v2/instance` or `/api/v1/instance` to detect version (`api_versions` since 4.3.0).

## Fallbacks

Public profile counts via the ActivityPub actor document (followers collection `totalItems`) is UNVERIFIED here. Otherwise manual entry or CSV. History cannot be backfilled for counts: snapshot daily. Per-account analytics are absent, so accuracy for reach is zero.

## Verdict

Official API with public endpoints. Public counts need no key; optional user token for locked or restricted instances. Do not assume Pixelfed parity.

## Sources

1. https://docs.joinmastodon.org/entities/Account/ (2026-10-23)
2. https://docs.joinmastodon.org/methods/accounts/ (2026-10-23)
3. https://docs.joinmastodon.org/entities/Status/ (2026-10-23)
4. https://docs.joinmastodon.org/api/rate-limits/ (2026-10-23)
5. https://docs.joinmastodon.org/methods/statuses/ (2026-10-23)
6. https://docs.joinmastodon.org/methods/instance/ (2026-10-23)
7. https://docs.joinmastodon.org/methods/apps/ (2026-10-23)
8. https://docs.joinmastodon.org/api/oauth-scopes/ (2026-10-23)
9. https://docs.joinmastodon.org/admin/config/ (2026-10-23)
10. https://docs.joinmastodon.org/api/oauth-tokens/ (2026-10-23)
11. Web search of Pixelfed forum posts and aggregators, 2026-10-23 (secondary, UNVERIFIED)

## Open questions for the maintainer

- Which software must v1 support beyond Mastodon (Pixelfed, GoToSocial, Akkoma)?
- Public-only reads by default, with a token as opt-in?
- Is a per-instance compatibility probe (version check, graceful 401/404) acceptable?
- Check other instances' ToS, or leave that to each user?
