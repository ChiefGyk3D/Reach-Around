<!-- SPDX-License-Identifier: Apache-2.0 -->
# Fediverse collector: public post counts for Mastodon, Bluesky and Pixelfed

> **Status: design draft, no code.** Written for issue #743 (epic #244) on 2026-11-26. It
> closes acceptance criterion 6 of #630: the `kit.fediverse` block takes typed counts and has no
> collector. No collector code lands until the owner approves this page. Nothing here was run
> against a live account; no live call was made while writing it.
>
> **Marking.** A platform fact is **VERIFIED** when the vendor page or file was read (URL and read
> date `2026-11-26` given, the page's own "last updated" date when it shows one) and **INFERRED**
> when it is reasoned, carried over from a repo note, or read only through a mirror or a
> summarizing fetch. Every INFERRED item is collected in section 14. Platform limits change; the
> first slice re-reads them.
>
> **Placeholders.** `social.example`, `bsky.example`, `pix.example`, `@example`. No real account,
> instance or handle appears here.

## 0. Summary and recommendation

1. **Build one small collector with one provider per platform**, reusing the code that already
   exists: the hardened `fetch_json` and pinned `HttpsTransport` (`src/reach_around/net/`), the
   `counts refresh` runner and its status file, and the append-only history store.
2. **Bluesky and Mastodon first, and tokenless.** Both expose per-post boosts or reposts,
   favorites or likes, and replies on public endpoints that need no login. **Pixelfed is last and
   optional**: the route file read for this design puts the account and statuses endpoints behind
   authentication, so it needs a token, and its field names are INFERRED (section 3).
3. **Aggregate counts only.** The collector keeps, per platform per run: followers, the post
   count, and one `{boosts, favorites, replies}` row of three integers per public post in the
   window. A row has no post id, no URL, no text, no author, no reply text and no timestamp
   (section 4). That is exactly the shape `FediverseEntry` already validates.
4. **An incomplete read is reported, never written as zero.** A platform that cannot be read in
   full leaves its previous values alone, with their age, and the run records a fixed reason word
   (section 8).
5. **Tier 2, not a cutover blocker.** Bluesky and Mastodon are Tier 2 in
   `docs/research/beacons-parity.md` (item A5, issue #396). Nothing here blocks `v0.3.0`.
6. **Four slices, about 28 oversight hours** (section 12), the first of which is a read-only probe
   that settles the INFERRED items before any code is trusted.

## 1. What exists today

| Piece | State on `origin/main` (read 2026-11-26) |
|---|---|
| Kit block | `kit.fediverse`, max three entries, one per platform (`src/reach_around/config/kit.py`, `src/reach_around/config/fediverse.py`). An entry holds `profile_url`, `as_of`, `followers`, `posts`, `interactions` (a list of at most 200 rows of `boosts`, `favorites`, `replies`, each 0 to 10^9) and `show_mean`. The build works out per-post medians (median first, mean only with `show_mean`); a value that was not typed prints "not provided", never zero (`docs/kit.md`, "The fediverse block"). |
| Why it is hand-kept | The module docstring: "Nothing is fetched: a collector for public post data has its own design issue and lands separately." That is this page. |
| Numbers sources | `numbers.sources` already has `bluesky` (fixed host `public.api.bsky.app`, followers only) and `mastodon` (`host` and `acct` from config, followers only, via `GET /api/v1/accounts/lookup`). Both return one integer (`src/reach_around/counts/providers.py`). They fetch nothing per post. |
| HTTP helper | `fetch_json` (`src/reach_around/net/fetch.py`): https only, port 443 only, same-host redirects only (at most 3), 10 s per attempt, 256 KiB cap, JSON content types only, identity encoding only, at most 3 attempts with `Retry-After` honored up to 60 s, GET-only retries, fixed error words, `User-Agent: reach-around-counts/<version>`. It returns a JSON **object** only; a top-level array raises `bad_json`. |
| Egress guard | `validate_hostname` refuses IP literals in any notation, `localhost`, local suffixes, userinfo, ports, paths and control characters; `HttpsTransport` resolves the name itself, requires every answer to be public, connects to the checked address and keeps the name for Host and TLS (`src/reach_around/net/egress.py`). |
| History store | One append-only JSON-lines file, hash-chained, `platform`, `metric`, integer `value`, `as_of_utc`, `source` (`api`, `hand_kept`, `imported`), `run_id` (`src/reach_around/counts/history.py`, `docs/social-history.md`). A failed source writes nothing; a gap stays a gap. |
| Import guard | `src/reach_around/config/import_guard.py` refuses person-level column names and values (handles, emails, IP addresses, channel ids) and demographic cells under the floor of 5. |
| Refresh workflow | The instance template's `refresh-counts.yml` runs daily at 07:17 UTC, one job holding keys, a second job that proposes the result as a pull request. Its egress mode is audit (`docs/deploy.md`, "Egress control"): the Mastodon and Matrix hosts come from config and cannot be listed. |

## 2. Scope

In scope: public posts of one account per platform, boosts, favorites and replies per post for the
30 days before the run, the profile's follower count and post count, for Mastodon-API servers
(Mastodon and, as INFERRED-compatible software, Pleroma, Akkoma, GoToSocial), Bluesky and Pixelfed.

Out of scope: who boosted, favorited or replied (`favourited_by`, `reblogged_by`, `getLikes`,
`getRepostedBy` are never called); post text, media, links, hashtags and mentions; follower lists;
other people's posts; impressions, reach and demographics (no platform exposes them, and the card
already says so); history backfill (counts cannot be backfilled, so history starts at the first
run); anything private (a token never widens the read: only `public` and `unlisted` posts count,
checked client side whatever the server returns).

**The 30-day window.** The window ends at the run instant `T` (UTC) and starts at `T` minus 30 x 24
hours. `as_of` is the UTC date of `T`. A post is in the window when its server-assigned creation
time `t` satisfies `start <= t <= T + skew` (section 9). For Bluesky that time is `indexedAt` (the
server's clock), never the author-supplied `createdAt`, which an author can backdate. Posts the
account **boosted or reposted** are not the account's posts and are excluded. Replies to other
accounts are excluded (see section 3 on how each platform filters them); a thread the account
continues under its own posts is included. Counts are read as they stand at `T`: a post under 24
hours old is still collecting interactions, so the window is a snapshot, not a settled figure
(open question 5).

## 3. Platform facts

All pages read 2026-11-26 unless a row says otherwise.

| | Mastodon | Bluesky | Pixelfed |
|---|---|---|---|
| Boosts per post | `reblogs_count` | `repostCount` | shares: INFERRED `reblogs_count` |
| Favorites per post | `favourites_count` | `likeCount` | likes: INFERRED `favourites_count` |
| Replies per post | `replies_count` (since 2.5.0) | `replyCount` | comments: INFERRED `replies_count` |
| Quotes | `quotes_count` (4.5.0), not collected | `quoteCount`, not collected | not collected |
| Followers, post count | `followers_count`, `statuses_count` | `followersCount`, `postsCount` | INFERRED Mastodon-style fields |
| Auth for posts | none for public posts | none | **token required** (route file) |
| Auth for followers | docs list a user token (`read:accounts`) for `lookup`; existing provider calls it without one | none | route file shows `accounts/lookup` public, `accounts/{id}` behind auth |
| Published limit | 300 calls per 5 minutes per account and per IP | PDS: 3,000 per 5 minutes per IP; AppView: "generous", no number | none read |
| 429 and `Retry-After` | page names neither | page: 429 "usually"; no header names or backoff given | none read |

**Mastodon.**
- `GET /api/v1/accounts/:id/statuses`: "Public access covers public statuses only"; unauthenticated
  use allowed since 2.7.0; `limit` default 20, maximum 40; `max_id`, `since_id`, `min_id`;
  `exclude_replies` ("filters out statuses replying to a different account"), `exclude_reblogs`
  (2.7.0), `only_media`, `pinned` ("pinned statuses get no special ordering"). A 401 is returned
  only when the instance is in whitelist mode; 404 for an unknown account; 410 for a suspended
  account only between 2.4.0 and 3.3.0. The page documents no `Link` header for this method:
  paginate with `max_id` set to the smallest `id` of the last page. VERIFIED
  (https://docs.joinmastodon.org/methods/accounts/, read 2026-11-26, page "Last updated November
  6, 2026").
- `GET /api/v1/accounts/lookup?acct=`: "User token with `read:accounts`" is the documented auth;
  404 `{"error": "Record not found"}` when unknown (same page). The existing numbers provider calls
  it with no token on servers in the default configuration, so the documented requirement and
  the observed behavior differ. The collector treats a 401 or 403 as `needs_token` and offers an
  optional numeric `account_id` in config that skips the lookup entirely (INFERRED to work for the
  statuses endpoint; the slice 1 probe settles it).
- Status fields: `replies_count` "How many replies this status has received" (2.5.0),
  `reblogs_count` "How many boosts this status has received", `favourites_count` "How many
  favourites this status has received", `edited_at` (3.5.0, last edit time), `created_at`,
  `reblog` (nullable), `visibility` (`public`, `unlisted`, `private`, `direct`), `in_reply_to_id`.
  VERIFIED (https://docs.joinmastodon.org/entities/Status/, read 2026-11-26).
- Account fields: `followers_count` "The reported followers of this profile", `statuses_count`
  "How many statuses are attached to this account", `locked`, `bot`, `moved`, `suspended`. VERIFIED
  (https://docs.joinmastodon.org/entities/Account/, read 2026-11-26).
- Rate limits: "All endpoints and methods can be called 300 times within 5 minutes", per account
  and per IP; `X-RateLimit-Limit`, `-Remaining`, `-Reset` headers; "An API method can be subject to
  multiple overlapping rate limits." The page does not describe a 429 response and does not mention
  `Retry-After`. VERIFIED (https://docs.joinmastodon.org/api/rate-limits/, read 2026-11-26). An
  administrator can tune this; the config page lists no variable for it (docs/research/mastodon.md,
  2026-10-23, reviewed SOUND).
- Server switches that close the public read: `DISALLOW_UNAUTHENTICATED_API_ACCESS` "blocks" the
  public calls the web app makes; `LIMITED_FEDERATION_MODE` "turns off public pages and some REST
  APIs"; authorized fetch ("secure mode") restricts ActivityPub representations, not the REST API.
  VERIFIED (https://docs.joinmastodon.org/admin/config/, read 2026-11-26). Result for the
  collector: a 401 from the owner's own instance means `needs_token`, and the owner chooses
  between a read token (open question 4) and hand-kept numbers.
- Terms: Mastodon has no central terms of service; each instance sets its own rules. The API pages
  read state no limit on storing public data, which is an absence on those pages, not a grant.
  The instance's own rules were not read (no instance is named yet). INFERRED, and tracked with
  the Tier 2 terms audit in #585.

**Bluesky.**
- `GET https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed`: "Get a view of an actor's
  'author feed' (post and reposts by the author). Does not require auth." VERIFIED through a
  mirror of the official reference (https://bsky-docs-246.onrender.com/docs/api/app-bsky-feed-get-author-feed,
  read 2026-11-26; the official host redirects to `bsky.network` and returned no text to the
  fetcher). The parameter set `actor`, `limit` (1 to 100, default 50), `cursor`, `filter`
  (`posts_no_replies`, `posts_with_replies`, `posts_with_media`, `posts_and_author_threads`, ...) and
  `includePins`, and the per-post fields `likeCount`, `repostCount`, `replyCount`, `quoteCount`,
  `indexedAt`, with `reason` marking a repost or a pin, are INFERRED from the protocol's lexicon
  (the lexicon file could not be fetched this session). The repo note of 2026-10-23 confirmed
  `getPosts` returns `likeCount`, `repostCount`, `replyCount`, `quoteCount` on a live call
  (docs/research/bluesky.md, review section).
- `GET .../app.bsky.actor.getProfile?actor=`: `followersCount`, `followsCount`, `postsCount`; this
  is what the existing numbers provider calls. Confirmed live 2026-10-23 (same repo note).
- Hosts: `api.bsky.app` and `public.api.bsky.app` "don't support authentication"; the public host
  is cached and recommended for public web use. Limits: "These API services have generous
  rate-limits. Please contact us if you encounter rate-limiting." The numbers on that page
  (3,000 requests per 5 minutes per IP) are given for the PDS, not stated for the AppView.
  Requests over a limit "usually receive an HTTP 429"; rate limit headers follow an IETF draft,
  and the page names none and gives no backoff advice. VERIFIED through the mirror
  (https://bsky-docs-246.onrender.com/docs/advanced-guides/rate-limits, read 2026-11-26).
  Because the cache sits in front of public reads, a count can lag the live value by an unpublished
  interval (INFERRED).
- Terms: the Terms of Service (https://bsky.social/about/support/tos, "Last Updated: 14 August,
  2025", read 2026-11-26) say nothing about automated access, scraping, API use or rate limits;
  they say third-party "Developer Applications" are governed by their own terms and that Bluesky
  "does not control how others may use your Content". No separate developer policy was found
  (INFERRED absent). Tracked in #585.

**Pixelfed.**
- The official API documentation page was not found (`docs.pixelfed.org/.../api/` returned 404,
  2026-11-26). The route file `routes/api.php` on the `dev` branch was read through a
  summarizing fetch (https://github.com/pixelfed/pixelfed/blob/dev/routes/api.php, 2026-11-26):
  `accounts/lookup` is outside the auth group and has a `throttle:account-lookup` limiter;
  `accounts/{id}/statuses`, `accounts/{id}` and `statuses/{id}` sit behind `auth:sanctum,api`.
  So **Pixelfed needs a token for the per-post read** (INFERRED from a summary of the file; a
  reviewer re-reads the file in slice 1). Field names and the instance's throttle values are not
  documented anywhere read: INFERRED to follow the Mastodon entity. The Mastodon note already
  warns "Do not assume Pixelfed parity" (docs/research/mastodon.md). Terms are per instance.

## 4. Data model

The collector produces one **snapshot** per platform per run and writes two things from it.

**4.1 The snapshot (private instance repo, next to `numbers.json`).** A `fediverse.json` file, one
entry per platform, keys exactly those of `FediverseEntry` plus a status block:

| Field | Type | Note |
|---|---|---|
| `platform` | `mastodon`, `bluesky`, `pixelfed` | closed list |
| `profile_url` | https URL | built from config, not from the response |
| `as_of` | date | UTC date of `T` |
| `followers`, `posts` | integer 0 to `MAX_COUNT` or absent | absent means "not provided" |
| `interactions` | list of at most 200 `{boosts, favorites, replies}` | no ids, no times, no text; sorted by value so the order carries no timeline |
| `complete` | boolean | true only when every page of the window was read |
| `reason` | fixed word or absent | section 8 |

A row of three integers cannot name a person: it holds no author, no reply text, no post id, no
URL and no time. The import guard's denylist (`username`, `displayname`, `handle`, `email`,
`userid`, `authorid`, `commenter`, ...) and its value patterns (an `@handle`, an email, an IP
address) are run over every key and string of the snapshot before it is written, as defense in
depth; a hit fails the platform with `guard` and writes nothing. `profile_url` is the one string
value; it is the owner's own public profile and is already public in `kit.fediverse`.

The kit loader fills `kit.fediverse` from this file the way the numbers file overrides a typed
value: a fresh `complete` snapshot wins over typed values for the same platform; an incomplete or
stale one never replaces a typed entry, and the card shows the age. Medians are still computed by
the build from `interactions`, so the collector never computes a statistic the build then has to
trust.

**4.2 History store (aggregates only).** One `api` row per metric per run, with the platform slug and
existing metric-name rules (lowercase, digits, underscores): `followers` (already written by the
numbers sources, not duplicated), `posts_30d` (rows in the window), and the window **sums**
`boosts_30d`, `favorites_30d`, `replies_30d`. Medians are not stored: they are derivable from the
snapshot and an old snapshot is not kept. Rows are written only when `complete` is true, in one
batch under the store's lock, with the run id of the refresh. A snapshot that is incomplete writes
nothing. History is therefore a dated series of whole numbers, with gaps where a run failed.

**4.3 What is never stored.** Post text, post ids, post URLs, reply text, reply authors, boosters,
likers, avatars, hashtags, mentions, display names, the response body, response headers other than
the three the collector reads (`Retry-After`, `Date`, and the Mastodon rate-limit headers), and
the raw JSON of a page. The statuses JSON is parsed in memory, reduced to a tuple
`(created_at, boosts, favorites, replies)` per accepted post plus one pagination cursor (the last
id), and dropped before the next page is requested. Because no per-post identity is kept, a post
deleted or edited by its author simply is not in the next run's rows: nothing has to be
propagated or removed from storage.

**4.4 Tests of the privacy rule.** A hermetic test feeds a recorded page whose posts carry
`@handle` text, an email, an IP address and a display name, and asserts the snapshot, the history
rows and every log line contain none of them. A second test adds a person-level key to the
snapshot and asserts the import guard refuses the write. Both must fail without the guard.

## 5. Endpoints and the call budget

**Mastodon-compatible (Mastodon, and Pixelfed when a token is configured).**

1. `GET /api/v1/accounts/lookup?acct=<local name>` for `id`, `followers_count`, `statuses_count`;
   skipped when `account_id` is configured (followers are then not read; open question 3).
2. `GET /api/v1/accounts/<id>/statuses?limit=20&exclude_replies=true&exclude_reblogs=true`, then
   repeated with `max_id=<smallest id of the previous page>` until a post older than the window
   start, an empty page or the page cap. Ids are checked to be digit strings before they enter a
   URL. The next URL is built from the configured host and a numeric cursor; the collector never
   follows a URL, `Link` header or `next` value from a response.

Replies are filtered a second time client side (`in_reply_to_account_id` must be absent or the
account itself) and boosts a second time (`reblog` must be null), because compatible software can
ignore the query flags (INFERRED).

**Bluesky.**

1. `GET /xrpc/app.bsky.actor.getProfile?actor=<handle or DID>` on `public.api.bsky.app`.
2. `GET /xrpc/app.bsky.feed.getAuthorFeed?actor=...&limit=50&filter=posts_no_replies` with `cursor`
   until a non-pinned post is older than the window start, no cursor remains, or the page cap.
   Entries with a `reason` of repost are skipped; a pinned entry is kept when in the window but
   never ends the walk, since pins come first out of order.

**Budget per run (one platform).** At most 1 profile call and 12 page calls, so at most 13 calls;
12 pages of 20 cover 240 posts, above the 200-post kit cap. A window with 200 posts costs 10 to 11
calls. Bluesky pages of 50 need at most 5. Across all three platforms a run makes at most 39
calls, once a day (the existing 07:17 UTC refresh), so at most 39 calls per day, and at most 13 to any one
host, against a published Mastodon limit of 300 per 5 minutes. A self-imposed
pause of at least 1 second (plus up to 1 second of jitter) sits between two calls to the same
host.

**Limits the collector enforces itself:**

| Limit | Value | Reason |
|---|---|---|
| Timeout per attempt | 10 s (the existing default), 90 s for the whole platform | a stuck host cannot hold the job |
| Attempts per call | 3, GET only, existing backoff | existing `fetch_json` behavior |
| `Retry-After` | honored up to 60 s; longer means stop that platform with `rate_limited` | existing behavior; the collector adds no second retry layer |
| Response size cap | 1 MiB per page (a 20-post Mastodon page with rich HTML is far below it; the shared 256 KiB default is too tight for 40-post pages and is raised per call, not globally) | fixed memory, `too_large` on breach |
| Page cap | 12 per platform | bounded work |
| Rate-limit headers | if `X-RateLimit-Remaining` is under 5, stop the platform with `rate_limited` and keep the pages read as incomplete | Mastodon only; headers are read when present, never required |
| Rows | at most 200 kept; more in the window means `window_truncated` (section 8) | matches `MAX_POSTS` |

The shared `fetch_json` returns an object and discards headers, so slice 1 adds a sibling
`fetch_json_array` that returns the parsed array (Mastodon) and a small header view (`date`,
`x-ratelimit-remaining`), built on the same transport and error words; `fetch_json` itself does not
change.

## 6. Authentication and tokens

| Platform | Read | Token |
|---|---|---|
| Bluesky | profile, author feed | none; the public host does not support authentication (VERIFIED, rate-limits page) |
| Mastodon | statuses | none for public posts (VERIFIED); only an instance with `DISALLOW_UNAUTHENTICATED_API_ACCESS` or limited federation needs one |
| Mastodon | `lookup` | documented as a user token with `read:accounts`; works without in the default configuration (INFERRED, observed by the existing provider); `account_id` in config avoids it |
| Pixelfed | statuses, account | token required (INFERRED from the route file) |

Where a token is needed (Pixelfed, or an owner's locked-down instance): the narrowest scopes, read
only, `read:statuses` and `read:accounts`; a Mastodon-API token "will not expire automatically"
and dies only on revocation (docs/research/mastodon.md, reviewed SOUND, 2026-10-23); it is handed
to the engine through an environment variable named in config, from the secret manager, exactly
like the keyed sources in `docs/deploy.md`; it never appears in the config file, argv, a log line,
an error text or the snapshot. It is sent only in an `Authorization` header to the configured host;
because redirects are same-host only, it cannot be carried to another host. A token that returns 401
or 403 makes the platform `needs_token` and the run says so by name. The token is a user token and
therefore carries the user's authority; slice 4 documents creating it with the read scopes only,
and the owner rotates it on the schedule the token-health doc sets.

## 7. Terms and rate limits, in one place

| Platform | What the vendor says (VERIFIED unless marked) | What the collector does |
|---|---|---|
| Mastodon | 300 calls per 5 minutes per account and per IP; no central terms; instance rules apply (instance rules INFERRED, unread) | at most 13 calls a day per instance; stops on a low `X-RateLimit-Remaining`; the owner reads the instance rules before enabling it |
| Bluesky | AppView "generous", no number; 3,000 per 5 minutes per IP on a PDS; terms of 14 August 2025 silent on automated reads | at most 6 calls a day on the public cached host; no login; stops on 429 |
| Pixelfed | nothing read for limits; terms per instance (INFERRED) | optional, token, at most 13 calls a day |

Reads only, public data only, one account per platform (the owner's own), once a day, an honest
`User-Agent` of the form `reach-around-counts/<version>`, no scraping of HTML pages. If a platform
or instance asks for different behavior, the collector for it is switched off in config and the
typed block remains. A terms read for the owner's chosen instance is a precondition of slice 3
(open question 2).

## 8. Failure modes and what is reported

The rule is the existing one (`src/reach_around/counts/refresh.py`): a failed source keeps its
previous values, the status file records a class and a time, and nothing is written as zero. The
collector adds that a platform is `complete` or not, as a whole.

| Situation | Reason word | Result |
|---|---|---|
| Instance down, DNS or TLS failure, timeout | `egress_*`, `timeout`, `server_error` | previous snapshot kept with its age; run continues with the other platforms |
| 401 or 403 | `needs_token` | as above; the status file names the platform |
| 404 on the account | `account_not_found` | as above; the card keeps the typed entry |
| Account moved (`moved` present) or suspended (410) | `account_moved`, `account_suspended` | no write; the collector never follows a move |
| 429, or `Retry-After` over 60 s, or headers say the budget is nearly spent | `rate_limited` | as above |
| A page after the first fails or is too large | `partial_pages` | no interactions, no history write; followers may still be written from the profile call, which is complete on its own |
| Page cap reached before the window start | `page_cap` | as `partial_pages` |
| More than 200 posts in the window | `window_truncated` | no interactions and no medians (200 newest rows would describe a shorter window than the card states); question 6 asks whether to raise `MAX_POSTS` |
| Server `Date` differs from our clock by more than 10 minutes | `clock_skew` | no write; a post time more than 10 minutes in the future is dropped and counted |
| Window read in full and there are zero posts | none (complete) | `posts` 0, empty `interactions`; the card prints medians as "not provided", since a median of nothing does not exist |
| Account hides counts or the server reports a count it cannot compute (a negative or missing field on compatible software) | `counts_unavailable` | no write for that field; "not provided", never 0 |
| Wrong content type, bad JSON, wrong shape | `content_type`, `bad_json`, `bad_shape` | as `partial_pages` |
| Guard hit on a person-level key or value | `guard` | nothing written, platform named |

**Deleted and edited posts.** A deleted post is absent from the next read and so leaves the window;
nothing is stored that would keep it. An edit changes the post's text and `edited_at` but not its
`created_at`, so the post stays in the window with its current counts (Mastodon `created_at` and
`edited_at` fields, VERIFIED above). A post deleted during a run, between two pages, can shift the
page boundary and drop or repeat one post; duplicate ids within a run are removed, and a count of
dropped duplicates is logged as a number.

**Software differences.** The collector accepts a status only when `replies_count`,
`reblogs_count`, `favourites_count` and `created_at` are present, integers (no booleans, floats or
strings) and in range, and treats anything else as `bad_shape` for the platform. Pleroma, Akkoma and
GoToSocial implement much of the Mastodon API; whether each honors `exclude_replies`,
`exclude_reblogs` and unauthenticated reads, and whether `replies_count` is present, is INFERRED
and is settled by recorded fixtures per software, not by guessing (section 11). Unsupported
software is reported as `bad_shape`, not partially read.

**Counts are as reported by the server.** A server can hide or filter counts, and compatible
software can report any number (the card already says "Counts are as reported by the server").
Remote posts boosted from another instance are never read through the account's home instance's
copy of someone else's status; only the account's own statuses are counted.

## 9. Security

The instance host is attacker-influenced only through the owner's own config, but the response is
untrusted data from a host the owner does not control.

| Risk | Control |
|---|---|
| SSRF through the host | The host comes from config only, is validated at config load (the existing `MastodonSource` validator calls `validate_hostname`) and again at request time: https only, port 443 only, no IP literal in any notation, no `localhost`, no local suffix, no userinfo, no path. The host is never taken from a response. |
| SSRF through a followed URL | Never follow `Link`, `next`, `url`, `uri` or `moved` values from a body or header. The next page URL is built from the configured host and a numeric cursor. |
| Redirects | Same host over https only, at most 3 (existing). A redirect to another host, to http or to an IP literal is `redirect`. |
| DNS rebinding and private answers | `HttpsTransport` resolves once, requires every answer to be public, connects to the checked address and keeps the name for Host and TLS. The check runs on every connection, including each page and each redirect hop. |
| Resource exhaustion | 1 MiB per page, 12 pages, 10 s per attempt, 90 s per platform, identity encoding only (no decompression bomb), integers range-checked (the array path refuses integers over 18 digits, `NaN`, `Infinity` and overflowing floats; the shared object path `fetch_json` is unchanged), JSON nesting handled by the existing `RecursionError` catch, at most 200 rows. |
| Hostile content in the response | Never rendered, executed or written. Only four integers and a timestamp per post leave the parser; strings other than the timestamp are never read into the snapshot. Instance text that looks like instructions is data. |
| Forged counts or times | Counts are reported by the server and labeled so. The window uses the server's timestamp (`created_at`, `indexedAt`), future times beyond the skew tolerance are dropped, and ids must be digit strings. |
| Secret in logs | Error text is a fixed reason word (existing `FetchError` contract); no URL query, header value, body or token is logged. The token is held only for the Pixelfed or locked-instance case, read from the environment, passed in a header and never in a URL. |
| Token carried to another host | Redirects are same-host only; the token is attached only when the request host equals the configured host. |
| Output tampering | Writes go through the history store's lock, hash chain and atomic append; the snapshot file is written to a temporary name and renamed. |

**Egress allow-list entries.** Bluesky adds nothing: `public.api.bsky.app:443` is already in the
`counts` job's fixed list (`docs/deploy.md`). Mastodon and Pixelfed add one entry each,
`<instance host>:443`, taken from the same config the engine reads. The `counts` job is in audit
mode today for this reason (config-named hosts cannot be listed in a template). Slice 4 adds a
small script step that derives `allowed-endpoints` from the config's hosts, so the job could be
moved to block mode without a hand-kept list; moving it is a separate owner decision, since the
wheels artifact host still varies per run.

**CI of this repository.** `ci.yml` runs in block mode with a fixed allow-list that contains no
fediverse host and gets none. Every test is hermetic: the transport is injected, so no test opens a
socket (the `Transport` callable already exists for this).

## 10. Reporting

The status file records, per platform, only the reason word and the time; the refresh summary
prints one line per platform: `fediverse mastodon: complete 37 posts`, or `fediverse bluesky:
incomplete partial_pages, previous values from 2026-11-24`. No handle, no URL query and no count
beyond the post count appears. The kit and the owner report show the as-of date beside every
figure, as for the other collectors; a snapshot older than 30 days is warned as stale like any
number.

## 11. Testing strategy

All tests are hermetic and use recorded fixtures; no test makes a live call.

- **Fixtures.** Hand-built, minimal JSON in `tests/fixtures/fediverse/`, one directory per
  software (`mastodon`, `bluesky`, later `pixelfed`, `akkoma`, `gotosocial`): a profile, one to
  three status pages, an empty page, a 401, a 404, a 429 with `Retry-After`, a page with a boost
  and a reply to another account, an edited post, a pinned old post (Bluesky), a future-dated post,
  duplicate ids, a booleans-for-counts page, an oversized page. Fixtures use `social.example`
  and synthetic ids and contain no real account; they are trimmed, never raw captures, so no real
  person's text enters the repository. A privacy test greps every fixture for `@`, email and IP
  patterns.
- **Transport.** A fake `Transport` keyed on `(host, path-and-query)` serves the fixtures and
  records every request: the tests assert the exact endpoint, the cursor, the page count and that
  no request goes to a host other than the configured one (a fixture whose `Link` header points
  elsewhere proves the cursor is built, not followed).
- **Window logic.** Table tests over the boundary: a post exactly at start, one second before, one
  at `T`, one beyond the skew; a Bluesky pin that is older than the window and out of order.
- **Failure matrix.** One test per row of section 8, each asserting the previous values are kept,
  the reason word is the fixed one, no zero is written, no history row is appended, and no log line
  holds a fixture string, a URL query or a token sentinel.
- **Security tests.** IP literals in every notation, `localhost`, a private resolution, a redirect
  off host and to http, a resolver that changes between calls (the pinned transport test pattern
  that exists for `egress.py`), and an oversized body.
- **Privacy.** Section 4.4.
- **Revert proof.** Each new test is shown to fail on a copy of `origin/main` without the slice.
- **Live probe.** The only live call is slice 1's read-only probe, run by the owner or the lead
  from a developer machine against the owner's chosen accounts, kept out of CI and out of the
  repository. Its output is checked into fixtures only after trimming.

## 12. Tiers and build order

Tier definitions come from `docs/research/beacons-parity.md`: Tier 1 is the five cutover-critical
platforms (Twitch, YouTube, TikTok, Threads, Instagram) and blocks `v0.3.0`; **Tier 2 is bonus**:
"Bluesky and Mastodon first, then Kick, Discord and GitHub. None blocks cutover." Everything on
this page is Tier 2; none of it blocks cutover. Pixelfed is not named in the parity list at all, so
it is "Tier 2 or later, optional".

Sizes use the plan's scale (S 2 h, M 5 h, L 12 h of oversight, including an expected 1.5 fix
rounds).

| Slice | What | Tier | Effort |
|---|---|---|---|
| 1. Probe and shared plumbing | One read-only live probe per chosen platform to settle every INFERRED item in section 14 (a reviewer re-reads the Pixelfed route file and the Bluesky lexicon). `fetch_json_array` with a header view, the 1 MiB per-call cap, the page and time budget, the window helper, the guard pass over the snapshot. No provider yet. | 2 | M, 5 h |
| 2. Bluesky provider | `getProfile` plus `getAuthorFeed`, snapshot file, history aggregates, status reasons, fixtures and the failure matrix. Bluesky first because it is tokenless, one fixed host, no egress change, and its pagination is a documented cursor. | 2 | M, 5 h |
| 3. Mastodon provider | Lookup (or `account_id`), statuses with `max_id`, client-side reply and boost filters, `needs_token`, instance host validation, per-software fixtures (Mastodon first; Akkoma, GoToSocial and Pleroma only when the owner runs or follows one). Precondition: the owner's instance terms read. | 2 | L, 12 h |
| 4. Kit wiring, workflow and docs | Kit loader fills `kit.fediverse` from the snapshot with the freshness rule; the instance template's refresh script and the `allowed-endpoints` derivation; `docs/kit.md`, `docs/config.md`, `docs/deploy.md`, the schema, the changelog; the research notes updated with this page's verified facts. | 2 | M, 5 h |
| 5. Pixelfed (optional) | Token path and fixtures, only if the owner wants it and the probe confirms the read works. | 2 or later | M, 5 h |

**Slice 1 as built (#921).** The shared plumbing only; no provider and no live host. Where
it lives: `fetch_json_array` and its `FetchedArray` result in `src/reach_around/net/fetch.py`
(an array answer, a hard 1 MiB cap, a read-only view of `Link`, `Retry-After`, `Date` and the
rate-limit headers; the `Link` header is exposed but never followed); the 30 day window in
`src/reach_around/counts/fediverse_window.py` (reads `created_at` or `indexedAt` only, refuses
the author-supplied `createdAt`, naive times are invalid); the three-integer row and the
`NotProvided` marker in `src/reach_around/counts/fediverse_rows.py`; the hand-run probe
`scripts/fediverse-probe.py`, which prints status codes, header names and counts and never a
body, and runs in no workflow. The guard pass over the snapshot and the page and time budget
belong with the first provider (slice 2), which is the first caller. Nothing is added to
any egress allow-list in this slice.

Slices 1 to 4 are about 27 h; with slice 5, about 32 h. Slices 2 and 3 can run in parallel after
slice 1. Nothing requires a Cloudflare or schema migration change: the history store accepts the
new metric names today, and `FediverseEntry` already carries the fields.

## 13. Open questions for the owner

1. **Accounts and instance.** Which handle on which Mastodon-API instance, and which Bluesky
   handle or DID, should the collector read? (Config values only; they are never written into this
   repository.) Is the Mastodon account on an instance you run, or a public one?
2. **Terms.** May the collector call that instance daily? Please read its rules page, or name the
   operator to ask. This is the one terms read the design cannot do for you.
3. **Followers from the same collector, or only post data?** The numbers sources already collect
   followers for Bluesky and Mastodon; the recommendation is to keep them, not duplicate the
   follower call in this collector, and write only `posts`, the interactions and the window sums.
4. **Token handling.** If your instance closes the public read, or you want Pixelfed: a read-only
   user token in the secret manager under a name you pick, rotated with the token-health schedule.
   Or hand-kept numbers for that platform, which is also acceptable.
5. **Settling period.** Include posts under 24 hours old (matches what a sponsor sees when
   re-checking the profile; recommended), or leave them out until they have settled?
6. **Cap.** Raise `MAX_POSTS` above 200 so a prolific account is not `window_truncated`, or keep
   200 and accept "not provided" for an account that posts more than 6 times a day?
7. **Pixelfed.** Is it wanted at all? It is the only platform here that needs a token and the one
   with the least documentation.
8. **Replies.** Count only the account's top-level posts and its own thread continuations
   (recommended), or also its replies to other people?

## 14. INFERRED items

- Mastodon `lookup` works without a token on default configurations (existing provider behavior;
  docs say a token is needed). Settled by the slice 1 probe.
- Bluesky `getAuthorFeed` parameter names, limits and filter values and the `reason` shape (lexicon
  not fetched); the AppView limit is unpublished; the public host's cache lag.
- Pixelfed token requirement (summary of the route file, `dev` branch), its field names and its
  throttle values.
- Pleroma, Akkoma and GoToSocial behavior for every endpoint and filter.
- Each instance's own terms; Bluesky has no separate developer policy.
- That `account_id` alone is enough for the Mastodon statuses endpoint without a prior lookup.
- That `unlisted` posts are returned to unauthenticated reads (they are counted if returned).
- Bluesky pages read through a mirror because the official host redirected to `bsky.network` and
  returned no text to the fetcher on 2026-11-26.

## 15. Sources

All read 2026-11-26.

1. https://docs.joinmastodon.org/methods/accounts/ (Last updated November 6, 2026)
2. https://docs.joinmastodon.org/entities/Status/
3. https://docs.joinmastodon.org/entities/Account/
4. https://docs.joinmastodon.org/api/rate-limits/
5. https://docs.joinmastodon.org/admin/config/
6. https://bsky-docs-246.onrender.com/docs/advanced-guides/rate-limits (mirror)
7. https://bsky-docs-246.onrender.com/docs/api/app-bsky-feed-get-author-feed (mirror)
8. https://bsky.social/about/support/tos (Last Updated: 14 August, 2025)
9. https://github.com/pixelfed/pixelfed/blob/dev/routes/api.php (through a summarizing fetch)
10. Repo notes read: `docs/research/mastodon.md`, `docs/research/bluesky.md` (2026-10-23),
    `docs/research/platform-terms-retention.md`, `docs/research/beacons-parity.md`,
    `docs/deploy.md`, `docs/social-history.md`.
