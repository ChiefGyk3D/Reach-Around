# Discord research note (issue #16)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #16. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23. Discord docs moved from discord.com/developers/docs to docs.discord.com/developers (301 redirect); URLs below are the new ones.

## Summary

Discord has an official REST and Gateway API. A daily job can read a server's approximate member count and approximate online count with one bot-token call, no privileged intent needed. Joins and leaves have no history endpoint: either snapshot the count daily and compute deltas, or hold a Gateway connection with the privileged GUILD_MEMBERS intent, which a once-a-day job does not do. Without a bot, a public invite code or the server widget gives current counts only.

## Metrics

| Metric | How | Scope or permission |
|---|---|---|
| Total members (approximate) | `GET /guilds/{id}?with_counts=true` returns `approximate_member_count` | Bot in the guild; docs list no extra permission |
| Online members (approximate) | Same call, `approximate_presence_count` | Same; no GUILD_PRESENCES intent needed for this REST field |
| Counts without a bot | `GET /invites/{code}?with_counts=true` returns both approximate counts | Needs a permanent public invite code. Whether it requires auth: UNVERIFIED (docs silent) |
| Counts via widget | `GET /guilds/{id}/widget.json`: `presence_count`, up to 100 members | Server owner enables the widget (needs MANAGE_GUILD). No member total; online count only |
| Discoverable preview | `GET /guilds/{id}/preview` | Guild must be discoverable if caller is not a member |
| Net growth per day | Store daily snapshots, subtract | None beyond the above; history starts on day one of collection |
| Joins and leaves (exact) | Gateway events `GUILD_MEMBER_ADD` / `GUILD_MEMBER_REMOVE` | Privileged GUILD_MEMBERS intent, persistent connection |
| Invite uses | Guild invites list with `uses` | MANAGE_GUILD. Not fetched this session: UNVERIFIED |
| Server Insights (official analytics) | Dashboard UI | No API found in the docs read; treat as not available |

## Setup steps for a user

1. Create an application in the Discord Developer Portal, add a Bot, copy the token (the maintainer's tool never needs more than this).
2. Invite the bot with the `bot` scope and no permissions (permission integer 0). Member counts need none.
3. Leave all three Privileged Gateway Intents off for the counts-only design.
4. Put the token and guild ID in the tool's secret store or env.
5. No-bot alternative: enable the widget (Server Settings, Widget) or supply a permanent invite code.

Never request ADMINISTRATOR. Only if exact joins and leaves are wanted: enable GUILD_MEMBERS in the portal and run a long-lived listener (a separate, optional component).

## Rate limits and cost

Source: rate-limits page. Global 50 requests per second per bot; per-route buckets via `X-RateLimit-*` headers; 429 includes `retry_after`; do not hard-code limits. An IP with over 10,000 invalid (401, 403, 429) requests in 10 minutes is temporarily blocked. One call a day is far below all of it. The API is free; no pricing found. Approval time: none for counts.

## Privileged intents

GUILD_PRESENCES, GUILD_MEMBERS and MESSAGE_CONTENT are privileged. Apps in 100+ guilds need verification and approval; under 100 the user toggles them in the portal. Passing an unapproved intent closes the connection with code 4014 (gateway docs).

## Risks and ToS

The Developer Policy and Developer Terms pages returned HTTP 403 to my fetch, so storage and display limits are UNVERIFIED. Search results only confirm the Developer Terms (effective 2024-07-08) and Developer Policy govern API use. Aggregate counts of the user's own server carry little risk; avoid storing member IDs, names or message content. The user's own bot token is the user's own agreement with Discord; the open-source tool should not ship a shared bot. Widget `id`, `discriminator` and `avatar` are anonymized by Discord.

## Token lifetime and unattended access

Bot tokens do not expire on a schedule in the docs read (no expiry statement found; UNVERIFIED beyond that). They are revoked if the user resets it or if the token leaks and Discord invalidates it. A daily cron job works with the static token.

## Fallbacks

Public invite counts or widget (current only, approximate, online count may differ from true presence); manual entry; CSV from Server Insights export if the user downloads it (UNVERIFIED that an export exists). Accuracy cost: counts are labelled approximate, and history is limited to what the tool has stored.

## Verdict

Official API (bot token, counts-only, no privileged intents) for totals and online; public endpoint (invite or widget) as a no-bot fallback. Exact joins and leaves: derive net change from daily snapshots; exact events are possible only with a persistent GUILD_MEMBERS listener (optional, not recommended in v1).

## Sources (fetched 2026-10-23)

- https://docs.discord.com/developers/resources/guild
- https://docs.discord.com/developers/resources/invite
- https://docs.discord.com/developers/topics/gateway
- https://docs.discord.com/developers/topics/rate-limits
- https://support-dev.discord.com/hc/articles/8562894815383 (403, not read; existence via search)

## Open questions for the maintainer

1. Is net daily change (snapshot deltas) acceptable instead of exact joins and leaves?
2. Should an optional Gateway listener component be in scope?
3. Someone with a bot should confirm the Developer Policy storage and display clauses and whether `GET /guilds/{id}` needs any permission in practice.
4. Does `GET /invites/{code}` need auth today? Confirm with a live test by the maintainer.

## Review

Verdict: SOUND. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
No changes made to this note. Confirmed by the review: Get Guild with `with_counts=true` returns `approximate_member_count` and `approximate_presence_count`; the page lists no permission or auth requirement. Wording: the presence count is non-offline members, not strictly "online". Unverified: invite endpoint auth, rate limits, intent approval thresholds, Developer Policy (403).
