# Instagram API access and metrics (issue #6)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #6. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from Meta for Developers (official docs). Items not seen in the docs are marked UNVERIFIED.

## Summary

Instagram has an official API for professional (business or creator) accounts only. Personal accounts are not supported. Since the Instagram Login product, a linked Facebook Page is not required. A user can read their own follower count, post and reel insights, account-level reach and views, and audience demographics, with a 100-follower floor on some metrics. Tokens last 60 days and can be refreshed, so a daily job keeps access indefinitely if it refreshes before expiry. For a tool where each user brings their own app and keys, Standard Access (owner-only) appears sufficient, which avoids App Review. That conclusion is inferred from the access-level text, not stated for this exact use case.

## Metrics

| Metric | How | Permission (Instagram Login) |
|---|---|---|
| Followers (current) | `GET /<IG_ID>?fields=followers_count` | `instagram_business_basic` |
| Media count, follows count | same node, `media_count`, `follows_count` | `instagram_business_basic` |
| Account reach, views, likes, comments, shares, saves, total_interactions, accounts_engaged, profile_links_taps | `GET /<IG_ID>/insights` with `metric`, `period=day`, `metric_type` | `instagram_business_manage_insights` |
| Follows and unfollows | insights, `follows_and_unfollows`, breakdown `follow_type` | `instagram_business_manage_insights` |
| Follower demographics | insights, `follower_demographics`, `timeframe` required, breakdown age, city, country, gender, top 45 | `instagram_business_manage_insights` |
| Engaged-audience demographics | insights, `engaged_audience_demographics` (needs 100 engagements in timeframe) | same |
| Follower count over time | UNVERIFIED as a time series. Docs mention `follower_count` as unavailable under 100 followers, but the metric table does not list it. Safest is to store `followers_count` daily ourselves. | |
| Per-post and reel insights | `GET /<MEDIA_ID>/insights` | same |
| Live stats | Live video media readable only while broadcasting | |
| Monetization | not seen in docs: UNVERIFIED, assume unavailable | |

The `impressions` metric is deprecated; use `views`. Missing data returns an empty set, not 0. Data can lag up to 48 hours. User metrics are stored up to 90 days, so history beyond that must be accumulated by our own daily snapshots.

## User setup steps

1. Convert the Instagram account to a professional (creator or business) account.
2. Create a Meta developer app of type Business and add the Instagram product (Instagram API with Instagram Login).
3. Add the Instagram account to the app (as an app role or tester) so Standard Access applies.
4. Generate a token in App Dashboard, Instagram, API setup. Dashboard tokens are long-lived (60 days). Business Login tokens start short-lived (1 hour) and are exchanged server-side via `graph.instagram.com/access_token` with `grant_type=ig_exchange_token`.
5. Put the token, the IG user ID and the app secret in the tool's config or secret store.

## Risks and ToS

- Meta Platform Terms (section 3): no selling or licensing Platform Data, no profiling without consent, process only for the purposes in the developer docs, and delete data when a user asks or stops using the product. Users must be able to request deletion (section 4 privacy policy duty applies to app operators; a self-hosted per-user tool is arguably the user's own app, UNVERIFIED legal reading).
- Tokens and app secrets must not be shared (section 11). The tool must never publish them.
- Showing a user's own aggregate stats on their own public page is not clearly addressed. Treat demographics as the most sensitive; default them to private. Needs maintainer decision.
- Moving to Advanced Access later (to serve other people's accounts) requires App Review and Business Verification; the review duration was not stated in the docs: UNVERIFIED.
- Rate limit: calls per 24 hours = 4800 x impressions of the account (Business Use Case limits). A daily job uses a handful of calls; fine. Cost: no price in docs; UNVERIFIED but no fee is mentioned.
- Doc inconsistencies were noted (`breakdown` vs `breakdowns`, deprecated timeframe values). Pin an API version and test.

## Token lifetime and unattended access

Long-lived token: 60 days. Refresh with `GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token`; it must be at least 24 hours old, unexpired, and the user must have granted `instagram_business_basic`. Each refresh adds 60 days. A token that lapses cannot be refreshed and requires a manual re-login. The daily job should refresh weekly, persist the new token, and alert when expiry is under 14 days or a call fails with an auth error.

## Fallbacks

- Public follower count by scraping the profile page: violates Instagram ToS and breaks often; not recommended (UNVERIFIED, not researched further).
- Instagram's in-app export of account data or insights (manual download): UNVERIFIED format.
- Manual entry of follower count; third-party aggregators such as Social Blade: not researched, reliability and ToS UNVERIFIED.
- Business Discovery (reading another account's public counts) needs a Facebook Login setup and is subject to platform rate limits; not researched in depth.

## Verdict

Official API. Per-user own app, Instagram Login, Standard Access, professional account, no Facebook Page. Manual entry is the fallback for personal accounts.

## Sources (all fetched 2026-10-23)

- https://developers.facebook.com/docs/instagram-platform/overview
- https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
- https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started
- https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- https://developers.facebook.com/documentation/instagram-platform/insights.md
- https://developers.facebook.com/documentation/instagram-platform/api-reference/instagram-user/insights.md
- https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user.md
- https://developers.facebook.com/docs/graph-api/overview/rate-limiting
- https://developers.facebook.com/terms/dfc_platform_terms/

## Open questions for the maintainer

1. Is it acceptable to require a professional account and to leave personal accounts to manual entry?
2. Should demographics ever be shown publicly, or stored but private only?
3. Do we support only Standard Access (user brings own app), or ever a shared hosted app needing App Review?
4. Where should the token live (env var, Doppler) and who gets the expiry alert?
5. Is a no-API scraping fallback allowed at all, given ToS?
