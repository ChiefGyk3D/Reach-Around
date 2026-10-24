# Patreon: API v2 access and metrics (issue #21)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #21. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from the official docs at https://docs.patreon.com/ unless marked.

## Summary

Patreon has an official OAuth2 API (v2, base `https://www.patreon.com/api/oauth2/v2`). A creator registers their own client at the Patreon developer portal and gets a Creator Access Token for their own campaign, which suits a bring-your-own-keys tool. Patron count is a direct campaign field. Dollar income is not a v2 campaign field: `pledge_sum` appears only in the deprecated v1 sample. Monthly recurring revenue must be computed by summing members' `currently_entitled_amount_cents`. Webhooks exist. v1 endpoints stop on October 7, 2026, so build on v2 only.

## Metrics

| Metric | How to get it | Scope |
|---|---|---|
| Patron count | Campaign `patron_count` | `campaigns` |
| Active members and status | Campaign members list; `patron_status` (active_patron, declined_patron, former_patron) | `campaigns.members` |
| Current monthly pledge per member | `currently_entitled_amount_cents`; sum for MRR (derived, not a native total) | `campaigns.members` |
| Lifetime support per member | `campaign_lifetime_support_cents` (`lifetime_support_cents` is deprecated) | `campaigns.members` |
| Tiers | Tier resources included on members/campaign | `campaigns` |
| Posts | Posts resource | `campaigns.posts` |
| Lives | Lives resource | `campaigns.lives` |
| Real-time changes | Webhooks: `members:create`, `members:update`, `members:delete`, `members:pledge:create/update/delete`, `posts:publish/update/delete` | `w:campaigns.webhook` |
| Campaign earnings total / payouts | Not found in v2 docs (UNVERIFIED that none exists) | n/a |
| Follower/free-member counts, demographics | Not documented | n/a |

Email and address fields need extra sensitive scopes (`campaigns.members[email]`, `campaigns.members.address`); Reach Around should not request them.

## User setup

1. Have a Patreon creator account.
2. Register a client at https://www.patreon.com/portal/registration/register-clients (sets name, redirect URI, optional `tos_url` and `privacy_policy_url`).
3. Copy the Creator's Access Token (and refresh token) from the client page into the self-hosted secrets.
4. Reach Around reads its own campaign only. Request scopes `identity campaigns campaigns.members`.
5. Optional: register a webhook at the portal for live updates.

The portal page itself needs a sign-in, so steps 2-3 rely on the docs and third-party guides (rapidevelopers.com guide, search summary).

## Risks and ToS

- Docs: "You may only fetch your own list of pledges or public pledges." Multi-creator apps must contact Patreon via the developers forum. Per-user keys fits this; a hosted multi-tenant service would not.
- Since 2026-05-26, member `full_name`, `image_url`, `url`, `vanity` can be null when a member hides identity. Respect it; store counts and cents, not names.
- Patreon's Terms (https://patreon.com/legal) cover the APIs; I did not read the separate API/developer policy text, so rules on storing and publicly showing data are UNVERIFIED. Showing aggregate patron counts and MRR is lower risk than member lists.
- Bad-request abuse protection: more than 2,000 4xx responses in 10 minutes triggers a 30-minute block. Bad tokens retried in a loop would trip this. Back off on 401/429.
- No sandbox; testing needs real dummy accounts.

## Token lifetime and unattended access

- Tokens are valid for `expires_in` seconds; the docs give no number. A third-party guide says up to about one month (UNVERIFIED).
- Refresh uses `https://www.patreon.com/api/oauth2/token` (third-party; confirm). Refreshing does not reset rate limits.
- Whether the Creator Access Token auto-refreshes or must be re-copied: UNVERIFIED. Design the job to refresh and persist the new token, and alert on 401.

## Rate limits, cost, freshness

- Client: 100 requests per 2 seconds. Access token: 100 requests per minute. Far above a daily job's needs.
- Cost: no fee found for API use.
- Freshness: live data; webhooks push changes. History: only current state plus lifetime support; there is no documented time series, so store daily snapshots of count and MRR starting at setup.

## Fallbacks

- Creator dashboard CSV export of members/earnings (manual; existence of the export not verified here).
- Manual entry of monthly income.
- Public page patron count if the creator shows it (scraping is fragile and likely against terms; not recommended).

## Verdict

Official API. Patron counts: direct. Income: derived by summing entitled amounts (approximate; excludes fees, taxes, pending or declined charges). Real earnings need manual or CSV import.

## Sources (fetched 2026-10-23)

- https://docs.patreon.com/ (scopes, fields, webhooks, rate limits, v1 retirement, data policy)
- https://patreon.com/legal (terms scope, via search)
- https://rollout.com/integration-guides/patreon/api-essentials and https://rapidevelopers.com/api-automations/how-to-automate-patreon-membership-analytics-using-the-api (secondary, token endpoint and setup)

## Open questions for the maintainer

1. Is a derived MRR (sum of entitled cents) acceptable, or do you need true net earnings via CSV?
2. Will you read the Patreon developer policy to confirm public display of aggregates?
3. Should the tool refuse to store member names entirely?
4. Can you check in the portal whether the Creator Access Token expires and refreshes?
