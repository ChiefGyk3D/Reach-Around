# YouTube research note (Reach Around issue #10)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #10. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from official Google developer docs unless noted.

## Summary

YouTube has an official, free API pair. The Data API v3 gives public-style channel and video counts. The YouTube Analytics API gives owner-only reports: views, watch time, subscribers gained/lost, and demographics (age group, gender, country). Auth is OAuth 2.0 with a per-user Google Cloud project. Daily quota is generous for a once-a-day job. The hard parts are OAuth app status (Testing vs. verified) and YouTube's 30-day data refresh rule.

## Metrics

| Metric | How | Scope / permission |
|---|---|---|
| Channel subscribers, total views, video count | Data API `channels.list` (`part=statistics`, `mine=true`), 1 unit | OAuth with `youtube.readonly` (`mine` needs authorization). Public lookups by id/handle: API key only. UNVERIFIED: exact field list and rounding (the fetched page did not document fields; rounding is a known YouTube policy, see source 7) |
| Per-video views/likes/comments | `videos.list` (1 unit) | API key for public videos |
| Views, watch time (`estimatedMinutesWatched`), `averageViewDuration` | Analytics API `reports.query`, `ids=channel==MINE` | `yt-analytics.readonly` |
| Subscribers gained/lost | Analytics metrics `subscribersGained`, `subscribersLost` | `yt-analytics.readonly` |
| Age and gender | Analytics "Viewer demographics": dimensions `ageGroup`, `gender`, metric `viewerPercentage` | `yt-analytics.readonly` |
| Geography | Analytics dimension `country` (also province for US, city, DMA) | `yt-analytics.readonly` |
| Revenue, ads | Analytics monetary metrics | `yt-analytics-monetary.readonly`; channel must be in the Partner Program or the call returns 403 |
| Live stream stats | UNVERIFIED (not researched in the docs fetched) | n/a |

## User setup

1. Create a Google Cloud project; enable YouTube Data API v3 and YouTube Analytics API.
2. Configure the OAuth consent screen and create an OAuth client (desktop or web).
3. Run a one-time consent flow with `access_type=offline` (and `prompt=consent` if no refresh token came back) for the scopes above.
4. Store the refresh token as a secret in the self-hosted job; the daily job exchanges it for a short access token (about an hour; the doc example shows 3920 seconds).

## Quota and cost

Default is 10,000 units/day for most endpoints; `search.list` has its own 100-call/day bucket. List calls cost 1 unit. Resets at midnight Pacific. A daily job needs a few dozen units. No fee documented (source 1). Pagination costs per page.

## Freshness and history

Analytics data lags 48 to 72 hours, and the newest days may be missing. For current counts use `videos.list` (source 5). How far back analytics history goes: UNVERIFIED (page does not say). Totals can differ between reports because low-volume data is withheld below undisclosed thresholds.

## Risks and ToS limits

- Verification: Google says sensitive scopes require OAuth app verification for public apps; the help page did not state turnaround (UNVERIFIED) and did not list personal-use exemptions in the visible text. Whether the YouTube scopes are classed sensitive is not stated on the auth page (UNVERIFIED; check the scopes list).
- Developer Policies (source 4): authorized analytics data may be kept while the user consents, but you must verify authorization every 30 days. Non-authorized statistics (for example a subscriber count read without owner auth) may be kept 30 days at most. Deletion within 7 days of a user request. Only the authorizing user (or agents they approve) may view Authorized Data. Do not derive new metrics or replace YouTube's figures; label non-YouTube figures. Extended storage and derived metrics need audited-developer permission (from 2026-06-01 per the page).
- Implication: a public Reach Around dashboard that displays a creator's own Analytics data is a gray area. The creator is the authorizing user and self-hosts, but publishing demographics to the public web is not clearly permitted. Treat demographics as private; publish only public counts. UNVERIFIED legal reading; maintainer should decide.
- Long-term history stored by the tool beyond 30 days of unauthorized data conflicts with the policy for public-count-only mode.

## Token lifetime and unattended access

Refresh tokens last until revoked, but expire after 6 months unused, on 100+ tokens per account per client (oldest dies), admin policy, or password change for Gmail scopes. Critically, an external consent screen left in "Testing" status gives refresh tokens that expire after 7 days (source 3). So each user must publish the app to "In production" (an unverified-app warning will appear for the user's own account) or accept weekly re-consent. Handle `invalid_grant` by alerting the user.

## Fallbacks

Public counts via Data API with only an API key (no OAuth, 1 unit): subscribers (rounded), views, video count; 30-day retention rule applies. Studio CSV export import for demographics and history. Manual entry. Third-party sites such as Social Blade: UNVERIFIED, not researched.

## Verdict

Official API. Tier 1 public counts by API key; tier 2 OAuth Analytics for the owner's own demographics and watch time. CSV import as fallback.

## Sources (all fetched 2026-10-23)

1. https://developers.google.com/youtube/v3/determine_quota_cost
2. https://developers.google.com/youtube/analytics/channel_reports
3. https://developers.google.com/identity/protocols/oauth2 (expiration section)
4. https://developers.google.com/youtube/terms/developer-policies
5. https://developers.google.com/youtube/analytics/data_model (updated 2026-10-15)
6. https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps and https://developers.google.com/youtube/reporting/guides/authorization
7. https://developers.google.com/youtube/v3/docs/channels/list ; https://support.google.com/cloud/answer/13463073

## Open questions for the maintainer

1. Does Reach Around publish creator analytics publicly, or only to the creator? Demographics should likely stay private.
2. Will you tell users to move their consent screen to production, or accept 7-day Testing tokens?
3. Is public-count-only mode (30-day storage cap) acceptable, given history would have to be refreshed or dropped?
4. Should we ask YouTube API compliance staff for a written reading on public display of owner-authorized data?
