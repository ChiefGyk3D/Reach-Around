# Kick: API access and metrics (issue #12)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #12. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23. Sources are listed at the bottom with the same date.

## Summary

Kick now has an official public API with OAuth 2.1 (PKCE), documented at docs.kick.com. It covers livestream status, live viewer count, subscriber counts, and webhooks for follows, subscriptions, and stream status. The documented Channels response has no follower count field. Follower totals appear only in the undocumented endpoints that kick.com itself uses, which Kick's Terms of Service do not authorize. Kick publishes no history or analytics endpoint, so Reach Around has to build its own history by polling daily.

## Metrics

| Metric | How to get it | Scope or permission |
|---|---|---|
| Live status | `GET /public/v1/channels` returns `stream.is_live` and `start_time` | `channel:read` (user token) or app token |
| Live viewer count | `stream.viewer_count` on Channels, or `viewer_count` on `/public/v1/users/livestreams` | Same; instantaneous value only |
| Subscribers | Channels: `active_subscribers_count`, `active_gifted_subscribers_count`, `canceled_subscribers_count` | `channel:read` (UNVERIFIED which field needs the user token) |
| Sub events | Webhooks `channel.subscription.new`, `.renewal`, `.gifts` | `events:subscribe`; public HTTPS endpoint |
| Follower total | NOT in the documented Channels schema | n/a |
| Follow events | Webhook `channel.followed`, usable as a running counter | `events:subscribe` |
| Stream start/end | Webhook `livestream.status.updated` | `events:subscribe` |
| Demographics, revenue, VOD stats, history | Not documented | n/a |

## User setup

1. Create a Kick account and enable two-factor authentication (required for developer tools).
2. Account Settings, Developer tab: create an app. This yields a client ID and client secret and asks for a redirect URL.
3. Keep the secret in the user's own secret store. Reach Around never ships a shared key.
4. For public-only data (live status, viewers) use the client credentials grant: `POST https://id.kick.com/oauth/token`, no browser step.
5. For user-scoped data, run the authorization code flow with PKCE once (`https://id.kick.com/oauth/authorize`) and store the refresh token.
6. For follow and subscription events, enable webhooks in the Developer tab and give a public HTTPS URL, then subscribe through the Event Subscriptions endpoint. A self-hosted daily job usually has no public endpoint, so webhooks are optional.

## Risks and ToS limits

- No app review or approval step is described in the app setup page. Separate "App Verification" exists (badge, chat subscription cap 1,000 to 10,000) by emailing the developer address; not needed here.
- Rate limits: no page found states numeric limits. UNVERIFIED. A once-a-day job per user is very unlikely to hit them.
- Developer Terms and Conditions are referenced by the setup page but I did not read them. Rules on storing and showing API data publicly are therefore UNVERIFIED. The maintainer should read them before the first release.
- Kick's general Terms of Service (section 1.4) prohibit scraping, automated access outside "means provided or authorized," and circumventing access controls. Using the undocumented internal endpoints for follower counts falls under that. Third-party scrapers describe those endpoints as unstable and breaking without notice.
- Webhooks: apps that fail to process an event for more than a day are auto-unsubscribed (changelog 2025-12-12). Disabling webhooks drops all subscriptions (2025-12-19).
- The API changes: Livestreams V1 and Categories V1 are deprecated, and token introspect moved. Pin to v2 where present and watch the changelog.

## Token lifetime and unattended access

The OAuth page gives no numeric lifetime; responses carry `expires_in`. Refresh uses `grant_type=refresh_token`, and each refresh returns a NEW refresh token that must be stored (UNVERIFIED whether the old one is invalidated; assume yes). The app token from client credentials has no refresh token, but the job simply requests a new one each run. So a daily job on public data needs only the client ID and secret. A job that needs a user token must persist the rotating refresh token durably and handle revocation.

## Fallbacks if the API is closed or lacks a field

- Follower total: user enters it manually or by CSV, or the job counts `channel.followed` events from a baseline (drifts on unfollows). Social Blade does not list Kick (see its note).
- Undocumented kick.com JSON endpoints return followers, but I do not recommend them: ToS conflict and fragility. If the maintainer wants it, make it opt-in, off by default, with a clear warning.
- Paid scraper services exist (Apify and similar); they carry the same ToS question and add cost.

## Verdict

Official API for live status, viewer count, and subscriber counts (daily snapshots, own history). Follower total: import or manual entry, with an opt-in unofficial path flagged as a ToS risk. Webhooks: optional, only for users who can expose a public URL.

## Sources (all fetched 2026-10-23)

- https://docs.kick.com/llms.txt (page index)
- https://docs.kick.com/getting-started/kick-apps-setup.md
- https://docs.kick.com/getting-started/generating-tokens-oauth2-flow.md
- https://docs.kick.com/getting-started/scopes.md
- https://docs.kick.com/apis/channels.md
- https://docs.kick.com/apis/users.md
- https://docs.kick.com/apis/livestreams.md
- https://docs.kick.com/apis/faqs.md
- https://docs.kick.com/events/introduction.md and /events/event-types
- https://docs.kick.com/changelog.md
- https://kick.com/terms-of-service
- Secondary (unofficial, low trust): Apify Kick scraper listings and the docs.rs `kick-api` crate, via web search.

## Open questions for the maintainer

1. Read the Kick Developer Terms and Conditions: may users store and publicly display API data?
2. Is an opt-in unofficial follower path acceptable, or is manual and CSV entry enough?
3. Does the daily job get a public HTTPS URL so webhooks are possible, or is polling only?
4. Is a user token (subscriber counts) required, or are public live stats enough for v1?
