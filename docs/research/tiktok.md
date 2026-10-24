# TikTok API access and metrics (issue #8)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #8. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

All sources fetched 2026-10-23 unless noted.

## Summary

TikTok has an official, free path for a creator's own data: Login Kit (OAuth) plus the Display API. With the `user.info.stats` scope, `/v2/user/info/` returns follower, following, likes and video counts. `/v2/video/list/` (scope `video.list`) returns the creator's public videos, and the Video Object carries view, like, comment and share counts. Audience demographics are not exposed by any official API I could find. The Research API is limited to non-profit researchers. App review is required before production use, and TikTok does not publish a review timeline. Review correction: a Sandbox mode (up to 10 target accounts you own, no review) exists and may serve the single-creator case; see User setup. The open-source clause wording (III.3(e)) is unconfirmed and needs a human legal read. Post-alert detection has no webhook; polling `video/list` is the only official route.

## Metrics

| Metric | How | Scope / permission |
|---|---|---|
| Follower count | `GET /v2/user/info/` field `follower_count` | `user.info.stats` |
| Following, likes total, video count | same endpoint | `user.info.stats` |
| Per-video views, likes, comments, shares | `POST /v2/video/list/` (max 20 per page, public videos only, newest first) or `/v2/video/query/` by ID | `video.list` |
| New-video detection | Poll `video/list`, compare `id`/`create_time` | `video.list` |
| Live stats | UNVERIFIED: none found in Display API | n/a |
| Audience demographics | No official API found; only in the TikTok Analytics app/export | n/a |
| Monetization | None found | n/a |
| History | No historical endpoint; the tool must snapshot daily itself. Video list reaches back through all public videos via cursor (UNVERIFIED: review found display-api-overview says "recently uploaded" videos, so full back-catalog coverage is not confirmed) | n/a |

## User setup

1. Register a developer account and create an app (TikTok recommends an organization owner, not an individual).
2. Add Login Kit and Display API products; add scopes `user.info.basic`, `user.info.stats`, `video.list`.
3. Verify Terms of Service, Privacy Policy and web URLs (required for apps created after 2024-09-09).
4. Submit for review with a per-scope explanation and a demo video of the full flow (up to five videos, 50 MB each).
5. Run the OAuth consent once, in a browser, as the creator; store the refresh token.
6. Sandbox mode (review correction, https://developers.tiktok.com/doc/add-a-sandbox, reviewer fetched 2026-10-23): up to 5 sandboxes and 10 target accounts; target accounts must be accounts you own and you must provide their login credentials; usable without app review; Login Kit works with target users. Content Posting and Data Portability are excluded. UNVERIFIED: whether indefinite use is allowed. For a single creator this is a possible workaround for the review step.

## Risks and ToS limits

- Every self-hoster must register and get their own app approved. A review that asks for a demo video of an end-user product may be a poor fit for a private personal job; outcome UNVERIFIED.
- Developer Terms: TikTok Information is usable "solely for the purpose of developing, maintaining, and supporting your Applications" (II.1); no profiling or building databases on individuals (III.3(h)); no combining the services with open-source software in a way that subjects them to open-source license terms (III.3(e)). Verified in a browser on 2026-10-23 (the primary page, not a summary): III.3(e) says you will not "use or combine the TikTok Developer Services with software offered under an open source license in such a way that would cause the TikTok Developer Services to be subject to any obligations under any such open source license." Plain reading: it bars combinations that would pull TikTok's services under an open-source license's obligations (a copyleft problem). It does not bar open-source tools that call the API. Apache-2.0 is permissive and a collector that only calls the API does not put TikTok's services under that license, so this clause does not appear to block Reach Around. This is a plain reading, not legal advice, and III.3(a) (no redistributing or copying the TikTok Developer Services) should be kept in mind: the project must not bundle TikTok's SDK or documentation text. Also per the reviewer: III.3(b) bars allowing third parties to access the TikTok Developer Site, III.3(c) bars "commercial or unauthorized" use without consent, and II.1 limits distribution of Sample Code/SDK to object code inside your Application. Nothing found addresses per-user self-registered, self-hosted apps either way (https://www.tiktok.com/legal/page/global/tik-tok-developer-terms-of-service/en, reviewer fetch 2026-10-23). Public display is allowed only to end users on a personal, non-transferable basis, so a public dashboard of the creator's own data is a gray area. I am not a lawyer.
- Retention periods are not specified; data must be deleted on termination (VI).
- Rate limit: 600 requests per minute per endpoint (sliding window), ample for one daily call. No fee.
- Cover image URLs expire after 6 hours; do not store them as permanent links. (UNVERIFIED by the review: the 6-hour figure was not confirmed.)
- The maintainer reported unreliable TikTok API behavior a year ago. I found no independent reliability reporting; UNVERIFIED either way (the review did not verify this anecdote).

## Tokens and unattended access

Access token: 24 hours. Refresh token: 365 days, and the refresh response may return a new refresh token that must replace the old one. A daily job can keep access indefinitely if it persists the rotated refresh token every run; if the token is lost or a year passes without it being exercised, the user must redo consent. The store must be writable by the job.

## Fallbacks

- Public profile page count scraping: against TikTok terms and brittle; UNVERIFIED, not recommended.
- Manual entry or CSV import from TikTok Analytics export: reliable, includes demographics, needs the human.
- Third-party scrapers/Social Blade: estimated, third-party terms.
- Research API: only non-profit academic and research applicants in eligible regions; not for a personal tool.

## Verdict

Official API (Display API, followers and per-video stats) if the app passes review; manual/CSV import for demographics. Demographics: not feasible via API. Post-alert: no push event for new videos by the creator (webhooks cover only Video Kit uploads and authorization removal); polling `video/list` works only for creators who authorized the app, so it cannot watch arbitrary accounts.

## Sources

- https://developers.tiktok.com/doc/display-api-overview (2026-10-23)
- https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info (2026-10-23)
- https://developers.tiktok.com/doc/tiktok-api-v2-video-list (2026-10-23)
- https://developers.tiktok.com/doc/tiktok-api-v2-video-object (2026-10-23)
- https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit (2026-10-23)
- https://developers.tiktok.com/doc/oauth-user-access-token-management (2026-10-23)
- https://developers.tiktok.com/doc/getting-started-create-an-app (2026-10-23)
- https://developers.tiktok.com/doc/webhooks-events (2026-10-23)
- https://developers.tiktok.com/doc/content-sharing-guidelines (2026-10-23; unaudited Content Posting limits. Review correction: this page is about Content Posting, not the Display API, so it is not evidence of any Display API cap)
- https://developers.tiktok.com/doc/add-a-sandbox (reviewer fetch 2026-10-23; Sandbox rules)
- https://developers.tiktok.com/doc/about-research-api/ (2026-10-23)
- https://www.tiktok.com/legal/page/global/tik-tok-developer-terms-of-service/en (2026-10-23; summary by fetch tool)
- Secondary, unverified: https://kinit.sk/when-research-apis-close-the-door-when-you-finally-get-it-it-is-not-what-you-have-asked-for/ (2026-10-23)

## Open questions for the maintainer

1. Is a personal, not-for-public-use app likely to pass TikTok review? Worth one trial submission by you.
2. The open-source clause (III.3(e)) was read in full on 2026-10-23 and does not appear to block an Apache-2.0 collector that only calls the API. It is a plain reading, not legal advice; confirm before shipping a TikTok collector to other people.
3. Is a public dashboard of the creator's own numbers acceptable, or private-only?
4. Does `follower_count` require re-review beyond `user.info.basic`? Scope is listed separately; approval behavior UNVERIFIED.
5. Does the Display API apply an unaudited cap to reads? Not found in anything the review read; stays open, not a finding.
6. Is Sandbox (10 owned target accounts, no review) acceptable as the single-creator path, and is indefinite use allowed? UNVERIFIED.

## Review

Verdict: FIX. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
Changed: added Sandbox rules; III.3(e) wording later verified from the primary page (see Developer Terms bullet); corrected the unaudited-cap source note; marked "all public videos via cursor" and the 6-hour cover expiry UNVERIFIED. Confirmed by the review: webhook list (no new-video event), token lifetimes (24 h / 365 d, rotating), `user.info.stats` fields, 600/min rate limit, review requirement. Still not verified: cover URL expiry, `video.list` coverage, the reliability anecdote.
