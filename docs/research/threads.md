# Threads API access and metrics (issue #7)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #7. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from Meta for Developers (official docs). Items not seen in the docs are marked UNVERIFIED.

## Summary

Threads has an official API with insights. A creator can read their own follower count, follower demographics (100-follower minimum), views, likes, replies, reposts, quotes and link clicks, plus per-post insights. Auth is OAuth through a Meta app created with the Threads use case. Long-lived tokens last 60 days and can be refreshed. With each user bringing their own app and being a Threads Tester on it, App Review should not be needed; review is required for users without a role on the app, and the app must be published.

## Metrics

| Metric | How | Permission |
|---|---|---|
| Followers (current) | `GET /<THREADS_USER_ID>/threads_insights?metric=followers_count` (total value; no since/until) | `threads_basic` + `threads_manage_insights` |
| Follower demographics | same endpoint, `metric=follower_demographics`, one `breakdown` of country, city, age or gender; profile needs 100+ followers | same |
| Profile views | `views` (time series) | same |
| Likes, replies, reposts, quotes | user-level totals via `threads_insights` | same |
| Link clicks | `clicks` (clicks on URLs you shared) | same |
| Per-post | `GET /<MEDIA_ID>/insights`: views, likes, replies, reposts, quotes, shares (views and shares marked "in development") | same |
| Follower history | not exposed as a series; store `followers_count` daily ourselves | |
| Live stats, monetization | not in docs: UNVERIFIED, assume none | |

User insights accept `since`/`until` Unix timestamps, earliest accepted 1712991600 (2024-04-13); the docs warn they are not guaranteed before 2024-06-01. Default range is yesterday through today. Retention beyond that is not stated.

## User setup steps

1. Create a Meta developer app using the Threads use case (it has separate Threads app ID and secret; use those).
2. Add the creator's Threads profile as a Threads Tester and accept the invite in the Threads app.
3. Run the authorization window to get a code (valid 1 hour), exchange for a short-lived token (1 hour), then exchange for a long-lived token.
4. Request `threads_basic` and `threads_manage_insights` only (least privilege).
5. Store token, Threads user ID and app secret in the tool's config or secret store.

## Risks and ToS

- Meta Platform Terms (section 3, fetched 2026-10-23): no selling or licensing data, no profiling without consent, process only for purposes in the developer docs, delete on user request or when no longer needed. Tokens and app secrets are not to be shared (section 11).
- Public display of the creator's own counts is not specifically addressed: UNVERIFIED. Demographics are the most sensitive; keep private by default.
- Rate limit: calls per rolling 24 hours = 4800 x impressions (minimum 10 impressions), plus CPU time 720,000 x impressions and total time 2,880,000 x impressions. A daily job is far below this. The 250 posts per 24 hours cap applies to publishing only, which we do not use.
- Cost: no fee mentioned in docs: UNVERIFIED.
- Some insights fields are labeled "in development" and may change.

## Token lifetime and unattended access

Long-lived token: 60 days. Refresh with `GET /refresh_access_token` before expiry. For public profiles a refresh extends the grant by 90 days per the get-started page; if the profile is private the grant cannot be extended and the user must re-authorize. The exact refresh preconditions (such as minimum token age) were not on the page: UNVERIFIED, though Instagram's equivalent requires 24 hours. The job should refresh weekly, persist the new token, and alert before expiry.

## Fallbacks

- Public follower count on the profile page: scraping may breach terms and is fragile; UNVERIFIED, not researched.
- Manual entry or Threads' in-app data export: UNVERIFIED format.
- Third-party trackers such as Social Blade: coverage and accuracy UNVERIFIED.
- Because Threads is tied to an Instagram profile, the follower number may also be reachable manually from the app.

## Verdict

Official API. Per-user own app, Threads Tester role, no App Review. Manual entry as the fallback.

## Sources (all fetched 2026-10-23)

- https://developers.facebook.com/docs/threads
- https://developers.facebook.com/docs/threads/get-started
- https://developers.facebook.com/docs/threads/insights
- https://developers.facebook.com/docs/threads/overview
- https://developers.facebook.com/docs/graph-api/overview/rate-limiting
- https://developers.facebook.com/terms/dfc_platform_terms/

## Open questions for the maintainer

1. Is requiring each user to create a Meta app and add themselves as a Tester acceptable setup friction?
2. Can the Instagram and Threads setups share one Meta app (docs say Threads has its own app ID, so probably separate)?
3. Demographics: private only or optional public?
4. Token storage and expiry alerting, same as Instagram?
5. Do we want a manual-entry fallback in version one?

## Review

Verdict: SOUND. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
No changes made to this note. Confirmed by the review: short-lived 1 h tokens, long-lived 60 days, refresh via GET /refresh_access_token, grant extension 90 days for public profiles (private profiles must re-grant), Tester invites must be accepted, non-role users need App Review and a published app. The 90 days is the permission grant, not the token; the job should track both. Unverified: refresh precondition (24 h minimum age) for Threads, metric names, the 1712991600 timestamp floor, rate formulas.
