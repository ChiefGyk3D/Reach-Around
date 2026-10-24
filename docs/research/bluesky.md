# Bluesky (AT Protocol) research note

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #14. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Issue: ChiefGyk3D/Reach-Around#14. All sources fetched 2026-10-23.

## Summary

Bluesky has an open, documented API with no app review and no fee. Public read endpoints on the public AppView work with no login, so follower, post, like, repost and reply counts can be read for any account. Authentication is only needed for private data (which Reach Around does not need). Bluesky offers no creator analytics: no impressions, reach, demographics or monetization. Everything is raw public counts, so history must be built by snapshotting daily.

## Metrics

| Metric | How to get it | Auth needed |
|---|---|---|
| Followers, follows, post count | `app.bsky.actor.getProfile` on the public AppView (`followersCount`, `followsCount`, `postsCount`) | None. Auth-free access to public endpoints is confirmed by the docs per search result [1]; exact field names UNVERIFIED against live lexicon (fetch failed; from prior knowledge) |
| Per-post likes, reposts, replies, quotes | `app.bsky.feed.getPosts` (batch of URIs) or `app.bsky.feed.getAuthorFeed`; count fields `likeCount`, `repostCount`, `replyCount`, `quoteCount` | None. Field names and batch size UNVERIFIED |
| Who liked / reposted | `app.bsky.feed.getLikes`, `getRepostedBy` | None (UNVERIFIED) |
| Real-time events | Jetstream WebSocket, filter by `collections` (limit 100) and `dids` (limit 10,000) [3] | None |
| Follower history | Not offered. Snapshot daily | n/a |
| Impressions, demographics, monetization | No such API found | UNVERIFIED (absence not proven; no official page lists any) |

## User setup

1. For public counts: enter a handle or DID. No key is needed.
2. Only if an authenticated feature is wanted later: create an app password in Bluesky settings, or register an OAuth client (see token notes).
3. Schedule the daily job; store one row per account and per post per day.

## Rate limits and cost

- Free. Docs describe AppView limits only as "generous", with no numbers, and ask developers to contact Bluesky on 429s [2].
- PDS limits: 3,000 requests per 5 minutes per IP for all endpoints; createSession 30 per 5 minutes and 300 per day per account [2]. Write budget 5,000 points per hour, 35,000 per day [2]. Irrelevant for read-only use except createSession.
- Docs warn limits "are likely to evolve" [2]. A daily job of tens to hundreds of requests is far below any published figure.

## Risks and ToS

- Bluesky's Terms of Service say users keep ownership of content and grant Bluesky a license; the page does not address API use, scraping or third-party storage [4]. UNVERIFIED whether a separate developer policy exists.
- Storing public counts is low risk. Storing post text of other people is higher risk: deletions may not propagate. Recommend storing counts and URIs only, and re-checking deleted posts.
- Account is public by design; showing counts publicly in a repo-hosted dashboard is consistent with the platform's open model, but the user should still opt in per account.

## Token lifetime and unattended access

- Not needed for public counts.
- If auth is added: OAuth access tokens should live under 30 minutes. Confidential clients may have unlimited total sessions, with refresh tokens limited to 180 days and single-use (each refresh returns a new one, so concurrent refreshes need locking). Public clients are limited to 2 weeks for session and refresh token [5]. A self-hosted daily job would be a public client, so it would need re-login every 2 weeks unless it hosts a confidential client with a key and metadata URL.
- App passwords still appear to work, but Bluesky reportedly urged new projects to use OAuth. Only third-party summaries found; UNVERIFIED against official docs [6].

## Fallbacks

- Public counts via an alternative AppView, or a profile page scrape (fragile, not recommended).
- Jetstream (hosts `jetstream.us-west.bsky.network`, `jetstream.us-east.bsky.network`) [3] for live like/repost events. Replay is only a "bounded lookback window", not defined [3]. Not needed for a daily poll.
- Manual entry or CSV for anything unavailable. No analytics export exists, so accuracy for reach is zero.

## Verdict

Public endpoint (official, unauthenticated AppView API). Daily snapshots give followers, posts and engagement counts. No audience or reach data is possible.

## Sources

1. https://docs.bsky.app/docs/api/app-bsky-actor-get-profile (via search snippet, 2026-10-23; page redirected and could not be read directly)
2. https://bsky-docs-246.onrender.com/docs/advanced-guides/rate-limits (mirror of official rate-limits page, 2026-10-23; official host redirect returned empty)
3. https://bsky.network/docs/jetstream/ and https://github.com/bluesky-social/jetstream (2026-10-23)
4. https://bsky.social/about/support/tos (2026-10-23)
5. https://atproto.com/specs/oauth (2026-10-23)
6. Third-party summaries via search, 2026-10-23 (UNVERIFIED)

## Open questions for the maintainer

- Accept public-endpoint-only (no login) for v1?
- Store only counts and post URIs, not text?
- Is an optional OAuth login worth the 2-week public-client expiry?
- Confirm exact endpoint field names with one live unauthenticated test call (this research did not make calls).

## Review

Verdict: SOUND. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
No changes made to this note. Confirmed by the review with a live unauthenticated call (2026-10-23): `getProfile` returns `followersCount`, `followsCount`, `postsCount`; `getPosts` returns `likeCount`, `repostCount`, `replyCount`, `quoteCount` and also `bookmarkCount` (not listed in the note). The note's UNVERIFIED field-name caveat is therefore now verified and can be removed. OAuth: access tokens under 30 minutes; public clients' session and refresh tokens 2 weeks; confidential refresh tokens 180 days; refresh tokens single-use. Unverified: rate-limit mirror, Jetstream limits, ToS.
