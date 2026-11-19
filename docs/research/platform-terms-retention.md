<!-- SPDX-License-Identifier: Apache-2.0 -->
# Platform terms: retention, deletion and display

Refs #382 (epic #364, social platform analytics). Draft for the maintainer. This is a reading of vendor pages, not legal advice. Rows above the Bluesky row were read on 2026-11-19 from the vendor page named in the Source column. The Bluesky, Mastodon, Discord and GitHub rows carry only what the repo research notes record (read 2026-10-23, drafts not yet independently reviewed), each cell tagged with its source path, date and status. VERIFIED means the quoted or paraphrased rule is on that page. INFERRED means the rule is carried over from a related page. NOT FOUND means the text could not be located.

## Table

| Platform | Data class | Allowed retention | Re-verification or deletion duty | Public-display rule | Source | Date read | Status |
|---|---|---|---|---|---|---|---|
| YouTube | API data (III.E.4) | Refresh or delete after 30 calendar days | Refresh or delete API data after 30 calendar days (III.E.4); the same 30-day rule for authorized data (III.E.4.c); delete within 7 days of a trigger (III.E.4.g); delete within 30 days of user revocation (III.D.2) | Public subscriber count fetched with an API key is API data too: the same 30-day maximum applies, authorized or not (III.E.4.c) | [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies) | 2026-11-19 | VERIFIED |
| YouTube | Authorized data (user OAuth) | 30 days (III.E.4.c) | As above | III.E.3.b: "must not display or allow access to Authorized Data to anyone other than the authorizing user" | same | 2026-11-19 | VERIFIED |
| Twitch | Twitch Content and Program Materials | Do not store copies unless (a) prior written authorization from Twitch, (b) you control the rights, or (c) cache for only 24 hours without further sharing with third parties (Schedule 1 C) | Delete all end-user data on termination, revocation, scope reduction or request; update cached results and delete content Twitch reports deleted or expired | "Re-syndication and re-distribution of Program Materials or data as available from a Twitch API is prohibited." Chat logs only as long as necessary, never to build public databases | [Twitch Developer Agreement](https://legal.twitch.com/en/legal/developer-agreement/) (last modified 12/04/2024) | 2026-11-19 | VERIFIED |
| Meta (Facebook, Instagram) | Platform Data | No fixed day count. Delete as soon as reasonably possible when no longer necessary, when you stop operating, when Meta requests, or when the user requests deletion (3.d) | Update or delete promptly after a request from Meta or the user (3.d) | 3.a.iv bans selling, licensing or purchasing Platform Data; 3.c restricts sharing. Instagram Platform policy 6.2: do not use the Instagram Platform to simply display User Content, or to import or back up content without permission | [Meta Platform Terms](https://developers.facebook.com/terms/) (updated February 3, 2026); [Meta developer policies](https://developers.facebook.com/devpolicy/) | 2026-11-19 | VERIFIED |
| Threads | Platform Data | Same as Meta | Same as Meta | Same as Meta. No Threads-specific data rules exist | same | 2026-11-19 | INFERRED |
| TikTok | TikTok Information (Display API) | "Not kept for longer than necessary"; no numeric period for the Display API (the 15-day rule belongs to the Research API) | Delete on termination | Use solely to develop, maintain and support your Applications; may only be made available to End Users "on a personal, non-exclusive, non-sublicensable, non-transferrable basis" | [TikTok Developer Terms of Service](https://www.tiktok.com/legal/page/global/tik-tok-developer-terms-of-service/en) (modified Dec 26, 2025); [Developer Data Sharing Agreement](https://www.tiktok.com/legal/page/global/tiktok-data-sharing-agreement/en) (effective January 22, 2026, names Display API) | 2026-11-19 | VERIFIED |
| Kick | Developer data | NOT FOUND | NOT FOUND | NOT FOUND | docs.kick.com App Setup mentions Developer Terms and Conditions without a link; the kick.com footer lists only terms-of-service, privacy-policy and dmca-policy. The terms are likely behind the logged-in developer settings | 2026-11-19 | NOT FOUND |
| Bluesky | Public counts and post URIs | Not recorded. Note recommends counts and URIs only, and re-checking deleted posts (INFERRED, docs/research/bluesky.md, 2026-10-23) | Not recorded. Note says deletions may not propagate for other people's post text (INFERRED, docs/research/bluesky.md, 2026-10-23) | Not recorded. Note says public counts in a repo-hosted dashboard are consistent with the open model, opt in per account (INFERRED, docs/research/bluesky.md, 2026-10-23). Terms of Service page does not address API use or third-party storage (VERIFIED as read in the note, UNVERIFIED whether a developer policy exists). Developer terms: not yet researched; tracked in #585 (Tier 2, follows cutover) | docs/research/bluesky.md | 2026-10-23 (note date) | INFERRED |
| Mastodon | Public counts and statuses | Not recorded. Note: no central ToS; each instance sets its own rules; docs read state no storage limits (VERIFIED absence on pages read, UNVERIFIED for any instance; docs/research/mastodon.md, 2026-10-23) | not recorded. Per-instance terms: not yet researched; tracked in #585 (Tier 2, follows cutover) | not recorded. Per-instance terms: not yet researched; tracked in #585 (Tier 2, follows cutover) | docs/research/mastodon.md | 2026-10-23 (note date) | INFERRED |
| Discord | Server counts (approximate) | not recorded. Developer Policy and Developer Terms returned HTTP 403, so storage limits are UNVERIFIED (docs/research/discord.md, 2026-10-23). not yet researched; tracked in #585 (Tier 2, follows cutover) | not recorded. not yet researched; tracked in #585 (Tier 2, follows cutover) | not recorded. Note advises against storing member IDs, names or message content (INFERRED, docs/research/discord.md, 2026-10-23). not yet researched; tracked in #585 (Tier 2, follows cutover) | docs/research/discord.md | 2026-10-23 (note date) | INFERRED |
| GitHub | Followers, stars, traffic | Traffic API keeps 14 days, so the job must store its own history (VERIFIED in note, docs/research/github.md, 2026-10-23). Terms of Service Section H sets no specific storage or display limit (VERIFIED in note, same source) | not recorded. Note: storing follower or stargazer lists is personal data, store counts only (INFERRED). Other terms: not yet researched; tracked in #585 (Tier 2, follows cutover) | not recorded. Note: showing the creator's own counts publicly is low risk (INFERRED, docs/research/github.md, 2026-10-23) | docs/research/github.md | 2026-10-23 (note date) | INFERRED |

Notes:

* A "90 days" figure for Meta appears in a third-party summary. It is not in the Meta terms and is not used here.
* Owner decision 2026-11-08: store Twitch data only from user-authorized calls.

## What this means for the history store

Design note only. No code changes are proposed in this document.

Today (see [social-history.md](../social-history.md)) the history file is one append-only JSONL file with unbounded retention, in the private instance repository. Records carry `source` of `api`, `hand_kept` or `imported`, but no per-platform expiry. Git history keeps every committed line forever, and the `prev` hash chain makes deleting an old line a chain break. Both properties conflict with a 30-day or 24-hour rule, and with delete-on-request.

Proposals:

1. **Access class per source.** Add a design-level label to each record or to each platform in config: `owner_private` (never leaves the private instance; authorized data), `aggregate_publishable` (floored, labeled aggregates), and `hand_kept` (typed by the owner, outside the platform data rules because it is the owner's own reading of a dashboard). Public builds already do not read the history file; the access class would make that rule explicit and checkable.
2. **Per-source expiry.** A `retain_days` per platform: YouTube authorized 30, Twitch API 1 (or 0 when not authorized), Meta and TikTok unset with a documented purpose and a deletion path. Records past expiry are redacted by a tombstone mechanism, not by deleting lines.
3. **Tombstone or rewrite instead of append-only delete.** The hash chain and git history are the hard part. Options: a redaction record that blanks the value but keeps the line and its hash (the git history still holds the old value, so it is not a deletion), or a rewrite of the file plus a history rewrite of the private repository (a destructive step that needs its own approval and a restore plan). Neither is built; the owner picks.
4. **Hand-kept numbers.** A number the owner reads and types is already `hand_kept`. It carries no platform API data and avoids the Twitch 24-hour and YouTube 30-day rules. This is the low-risk path for a long Twitch follower history.
5. **Deletion on request or revocation.** Uses the same path as expiry (item 3), not a removal of rows: for the platform, the revoked or expired `api` records are replaced in effect by a redaction (tombstone) record that blanks the value and keeps the line and its hash, so `counts history-verify` still passes. Steps: (a) identify the platform's `api` records to redact and note the trigger and its date (YouTube: 30 days from revocation, 7 days from a trigger; Twitch: on revocation; Meta: on user request); (b) append the tombstone records; (c) run `counts history-verify`; (d) record the date and what was redacted. Because the private repository's git history still holds the old values, a redaction alone does not delete them. Where a deadline requires true deletion, the only route is the other path: an owner-approved rewrite of the file, re-chaining every later `prev`, plus a history rewrite of the private repository, run as its own destructive operation with a backup and a restore plan. The owner chooses per case; neither mechanism is built, and [social-history.md](../social-history.md) today documents only `correction` records, which keep the original line.
6. **Display.** Never feed `api` rows from YouTube authorized calls, Twitch or Meta into a public page or a sponsor deck. Public output uses hand-kept or owner-published aggregates only.

## Owner decisions

Recommended option first in each.

1. **YouTube authorized analytics history.**
   a. (Recommended) Keep authorized analytics history private to the owner only, with no public or sponsor display of authorized data (III.E.3.b), and a 30-day expiry. This matches the blocker on #397.
   b. Store only manually exported numbers as `hand_kept`.
   c. Do not store YouTube authorized data at all.
2. **Twitch.** The 24-hour cache rule blocks a long history of Twitch API data unless one of its exceptions applies.
   a. (Recommended) Ask Twitch for written authorization, and keep the follower number by hand in the meantime.
   b. Treat the data as user-authorized (own channel) with deletion on revocation. This is a reading of the terms that Twitch has not confirmed.
   c. Hand-keep every Twitch number permanently.
3. **Meta (Facebook, Instagram, Threads).**
   a. (Recommended) Keep analytics with a documented purpose and a deletion path on user request.
   b. Hand-keep the numbers only.
   c. Drop Meta API history.
4. **Kick and TikTok.**
   a. (Recommended) The owner supplies the Kick developer terms text so the row can be filled in. For TikTok, use the same documented-purpose-and-deletion approach (it only requires deletion on termination and "not longer than necessary").
   b. Hand-keep both until the Kick terms are read.
   c. Skip API collection for both.
