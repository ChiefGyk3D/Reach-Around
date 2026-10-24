# LinkedIn API access and metrics (issue #19)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #19. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from learn.microsoft.com and linkedin.com unless noted.

## Summary

LinkedIn now documents member-level analytics APIs: `memberFollowersCount` (permission `r_member_profileAnalytics`) and `memberCreatorPostAnalytics` (permission `r_member_postAnalytics`) [1][2]. But the open self-serve permissions are only sign-in profile, email and `w_member_social` (posting) [3]. Analytics sit inside the Community Management API, a "Vetted Product" with a Development tier and a Standard tier requiring a screencast review [4]. Whether an individual with no company can be approved is UNVERIFIED (the app-review page returned 404). LinkedIn's native Excel export is the realistic path for a self-hosted individual.

## Metrics

| Metric | How to get it | Permission |
|---|---|---|
| Follower count (lifetime) | `GET /rest/memberFollowersCount?q=me` [1] | `r_member_profileAnalytics` (vetted) |
| Follower count per day | `q=dateRange` on the same endpoint [1] | same |
| Impressions, members reached, reactions, comments, reshares | `GET /rest/memberCreatorPostAnalytics?q=me` or `q=entity`, `queryType=...` [2] | `r_member_postAnalytics` (vetted) |
| Saves, sends, link clicks, followers gained, profile views from content | same endpoint, added in version 202604 and later [2] | same |
| Daily series | `aggregation=DAILY`; not supported for MEMBERS_REACHED, LINK_CLICKS, FOLLOWER_GAINED_FROM_CONTENT, PROFILE_VIEW_FROM_CONTENT [2] | same |
| Video watch time, views, viewers | Member video analytics [4] | UNVERIFIED permission name |
| Demographics, monetization | No API documented in pages read | UNVERIFIED |
| Everything above, manually | Analytics export to .XLSX, 7 to 365 days, desktop [5] | Logged-in member |

Docs warn that reshare, reaction and comment counts are "not consistent with UI at the moment," and counts are "best-effort accurate" [2].

## Approval and cost

Open permissions need no approval; most others "require explicit approval from LinkedIn" [3]. Community Management API: Development tier (limited volume), then Standard tier needing a screencast of each use case [4]. Rate limits: Development tier 500 per app and 100 per member (period not stated on the page) [4]. Review timeline: UNVERIFIED. Cost: no price listed on pages read. One FAQ says Community Management access should be requested only on new apps that have no other API products [4]. Approval is aimed at partners building products for clients; a one-person open-source tool where each user registers their own app is an unusual fit and could be rejected. UNVERIFIED either way.

## User setup steps (if the user gets approved)

1. Create an app at the LinkedIn Developer Portal and request Community Management API.
2. Complete Development tier review; upgrade to Standard if needed.
3. Run the 3-legged OAuth flow once, granting the two analytics permissions.
4. Store the refresh token in the user's own secrets.

## Token lifetime and unattended access

Access tokens last 60 days; programmatic refresh tokens last 365 days from the initial grant and do not extend when used; after that the member must re-authorize in a browser. Programmatic refresh is documented for approved Marketing Developer Platform partners, and LinkedIn may revoke tokens at any time [6]. So a daily job needs a human re-consent at least yearly. Note: the 202511 API version sunsets 2026-11-16, so pin a current version [1][2].

## Risks and terms

LinkedIn API Terms [7]: no caching or storing Content unless expressly permitted; profile data refreshed only while the member is actively using the app, not on an automated schedule; content shown only inside the app; may not sell, rent or make Content available to third parties; no commingling with other data; no scraping, and scraped data is "Non-Official Content" you may not store or show even if obtained indirectly. Read literally, a scheduled daily pull that stores history and publishes a public dashboard conflicts with several of these. Whether analytics counts count as "Content" or "Profile Data" is UNVERIFIED; this needs the maintainer's call, not a lawyer-free guess. Scraping is off the table.

## Fallbacks

1. Manual export: Analytics page, choose range up to 365 days, export .XLSX; follower and audience analytics export the same way [5][8]. Reach Around ingests the file. Accuracy equals LinkedIn's own numbers; impressions are an estimate [8]. Cost: user does it about monthly.
2. Manual entry of follower count and post metrics in a form.
3. Public counts: UNVERIFIED; public profile pages show follower counts but fetching them is scraping, prohibited by [7].
4. Social Blade style trackers: UNVERIFIED for LinkedIn and likely scraping.

## Verdict

Official API exists but is gated (vetted, Community Management); treat as not feasible for general users, possible for a user who is approved. Ship v1 as import (XLSX upload) plus manual entry. Add an optional API connector later behind a clear "requires LinkedIn approval" note.

## Sources (fetched 2026-10-23)

1. https://learn.microsoft.com/en-us/linkedin/marketing/community-management/members/follower-statistics (updated 2026-04-30)
2. https://learn.microsoft.com/en-us/linkedin/marketing/community-management/members/post-statistics (updated 2026-05-15)
3. https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access (updated 2026-06-03)
4. https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview (updated 2026-05-15)
5. https://www.linkedin.com/help/linkedin/answer/a701208
6. https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens (updated 2025-10-08)
7. https://www.linkedin.com/legal/l/api-terms-of-use (summarized by the fetch tool, not read verbatim)
8. Web search summary 2026-10-23, secondary: https://www.linkedin.com/help/learning/answer/a704175 and third-party guides

## Open questions for the maintainer

1. Is a non-company individual allowed into the Community Management program? Only a test application answers it; do you want to apply as a probe?
2. Do the API Terms (no scheduled refresh, no storing) rule out the API path for an open-source tool, or only for public display?
3. Is XLSX import plus manual entry acceptable as the v1 LinkedIn story?
4. Should the dashboard show LinkedIn numbers publicly, or only privately?
