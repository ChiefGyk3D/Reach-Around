# X (Twitter) API access and metrics (issue #9)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #9. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

All sources fetched 2026-10-23.

## Summary

X's official docs now describe a single pay-per-usage model: prepaid credits, no subscriptions, no named tiers. The pricing page I fetched does not describe a free API tier (it lists promotional credits instead). Third-party trackers say the Free tier closed in early 2026; the dates conflict, so treat that as UNVERIFIED but consistent with the official page. Your own follower count is available at `GET /2/users/me?user.fields=public_metrics`. A daily call is a tiny cost: the docs list "Owned Reads (your own data)" at $0.001, or "Users" reads at $0.010 per resource. Which rate applies to `/users/me` is UNVERIFIED, but either is well under a dollar a month for one call per day.

## Metrics

| Metric | How | Scope |
|---|---|---|
| Follower count | `GET /2/users/me`, `user.fields=public_metrics` -> `followers_count` | OAuth 2.0 user token: `users.read`, `tweet.read` |
| Following, listed, post count | same `public_metrics` | same |
| Likes received, media count | `public_metrics` optional fields `like_count`, `media_count` | same |
| Per-post metrics (impressions, likes, reposts, replies) | `GET /2/users/:id/tweets` with `tweet.fields` (e.g. `public_metrics`); non-public/organic impression fields: UNVERIFIED | `tweet.read` |
| Audience demographics | Not in the API; only in X Analytics UI | n/a |
| Live stats, monetization | Not found | n/a |
| History | None from API; snapshot daily yourself | n/a |

## Pricing and limits (pay-per-usage)

- Reads per resource returned: Posts $0.005, Users $0.010, Following/Followers $0.010, Owned Reads $0.001.
- Writes per request: post create $0.015 (with URL $0.200).
- Cap: 3 million post reads per billing cycle; optional spending limit blocks requests when reached; a resource charged once is not charged again within 24 hours UTC (soft guarantee).
- Promotional: $20 credit after saving a card and first purchase (a card is required).
- Rate limits are per 15 minutes: `/2/users/me` 75 per user; users lookup 900 per user or 300 per app; user timeline 900 per user. Limits per plan are in the Developer Console (not visible without signing in).
- Enterprise is the upgrade path above the cap.

## User setup

1. Create a developer account at console.x.com, declare the use case (binding per policy), create an app.
2. Add credits (card required); set a spend cap.
3. Enable OAuth 2.0 with PKCE, redirect URI, scopes `users.read tweet.read offline.access`.
4. Run the authorization once in a browser; store the access and refresh tokens.
5. Approval timeline: UNVERIFIED; the pages I fetched describe no manual review for pay-per-usage.

## Risks and ToS limits

- Developer Policy: stored X content must be kept current and deleted or changed within 24 hours of a request or when removed on X; displayed content must match the API's latest version.
- You may not use the API to compute X-wide usage totals for benchmarking or commercial purposes; per-account own metrics are not that.
- Redistribution to third parties is limited to Post/User IDs; hydrated objects go to individuals only through non-automated means (up to 500 per person per day). A public dashboard of aggregated own counts is not clearly covered; the safe reading is own-account numbers on the creator's own page. I am not a lawyer.
- Keys are personal; do not share or ship keys, which fits the bring-your-own-key design. Use cases changing substantially need X approval.
- Cost risk: pricing has changed repeatedly; pin nothing in code and expose a spend cap.

## Tokens and unattended access

Access token: 2 hours by default (PKCE flow). Refresh token is issued only with `offline.access` and lets the job mint new access tokens without a person ("until you revoke access"). Refresh token lifetime and rotation: not stated on the page (UNVERIFIED), so persist whatever the refresh response returns each run.

## Fallbacks

- Public profile count scraping: brittle, against X terms; UNVERIFIED.
- Manual entry or Analytics CSV export (includes impressions and audience data): free, needs a human.
- Social Blade or similar: estimated counts, third-party terms.
- Third-party X data providers: paid, outside X ToS comfort; not evaluated.

## Verdict

Official API, pay-per-usage: viable for a daily follower count at roughly cents per month, but not free: a payment card and prepaid credit are required. Offer manual/CSV import as the zero-cost fallback.

## Sources

- https://docs.x.com/x-api/getting-started/pricing (2026-10-23)
- https://docs.x.com/x-api/getting-started/about-x-api (2026-10-23)
- https://docs.x.com/x-api/fundamentals/rate-limits (2026-10-23)
- https://docs.x.com/x-api/users/get-my-user (2026-10-23)
- https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code (2026-10-23)
- https://docs.x.com/developer-terms/agreement-and-policy (2026-10-23; summary by fetch tool)
- Secondary, unverified: pricing trackers surfaced by web search on 2026-10-23 (e.g. blotato.com/blog/twitter-api-pricing, agentdeals.dev/vendor/x-twitter) on Free tier closure and the 2M vs 3M cap.

## Open questions for the maintainer

1. Will users accept a card plus prepaid credits? Provide a spend-cap default.
2. Which rate does `/users/me` bill at ($0.001 owned vs $0.010)? Check in the Developer Console.
3. May a public dashboard show the creator's own counts under the redistribution rules, or private-only?
4. Does the Free tier still exist for existing accounts? Verify in the console.
5. Should per-post metrics be in scope, given $0.005 per post read?
