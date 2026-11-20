<!-- SPDX-License-Identifier: Apache-2.0 -->
# Media kit v2: design

Status: **proposal for the maintainer's approval. Nothing here is built.** Written 2026-11-20 for issue #608, under epic #245. It changes no code and no config. Part of the owner's 2026-11-19 priority list (1: the media kit, 2: the sponsor page and quote, 3: outreach and follow-up, 4: pricing). This document covers priority 1 only; the quote and the rate calculator are named where the kit has to hand them data, and are designed elsewhere.

How to read this: **VERIFIED** means the claim was read on the named page on the date given, or is a fact about this repository at `origin/main` `f853cad`. **INFERRED** means reasoning or a secondary source, with the reason. Section 14 holds the decisions the maintainer must make, each with a recommended answer first.

## 1. Intent and why

The kit today (see [kit.md](../../kit.md)) is a good private-first design: a public page, a gated page, two PDFs, a hard wall between them, and a build that refuses to leak. What it does not do is *sell a creator who lives on many kinds of platform*.

A creator who streams on Twitch and Kick, publishes video and short clips, posts on Mastodon and the wider fediverse and on Bluesky, and may run a podcast or a newsletter has numbers in several different units from several different kinds of source. Traditional kits and link-in-bio tools handle this badly. Per the market research for #608 (private; summarized here, not copied):

- Their kits connect only the large closed networks and lead with a summed follower count. A person who follows in five places is counted five times. INFERRED from Linktree's own help article (https://linktr.ee/help/en/articles/16937450-how-to-set-up-and-share-your-media-kit, "Updated October 28, 2026", read 2026-11-19; VERIFIED that it connects Instagram, TikTok or YouTube and shows a combined follower count).
- No product found covers Twitch and Kick equally, or Mastodon, Bluesky, PeerTube, Lemmy or Pixelfed at all. INFERRED: absence in the pages read, not proof.
- Beacons now labels data sources (verified, estimated, entered) and shows "unavailable" rather than zero (https://beacons.ai/i/whats-new, read 2026-11-19; VERIFIED). So a source badge alone is no longer a differentiator. What can differentiate is the badge plus its method and date, a public re-check link where one exists, platforms nobody else covers, and honest overlap.
- Its rate calculator is a black box (https://help.beacons.ai/en/articles/4704705, read 2026-11-19; VERIFIED that no formula or data source is published). The owner said calculators like that felt wrong. The kit must not turn a pile of numbers into one invented figure.

The kit exists to sell the creator to vendors, partners and sponsors **with honest numbers**. Honest here is concrete: every number says where it came from, how, and when, and numbers in different units are never blended.

## 2. Goals

1. A kit that shows each platform in its own native vocabulary (live viewers for a stream, boosts for a post, watch time for a video) instead of forcing one template.
2. A trust label on every number: how it was obtained, by what method, as of which UTC day, and a re-check link when anyone can re-check it.
3. Twitch and Kick shown side by side, same fields, same order, same weight (partner-parity rule).
4. A fediverse-first metric set: the numbers the open platforms really expose, with the missing ones stated as missing.
5. A cross-platform audience summary that states its overlap assumption and gives a range, never one deduplicated number.
6. Every section degrades gracefully when a platform has no data, and never invents or zero-fills.
7. Numbers refresh daily with no owner action where an API exists, and a short flow to enter the rest by hand.
8. The existing public/gated wall, leak search and PDF guarantees keep working unchanged in kind.

## 3. Non-goals

- The sponsor page, the quote builder and the rate calculator. The kit only supplies them with labeled numbers (section 9).
- Outreach and follow-up tooling, and pricing of the project itself.
- New collectors. This design names which collectors the kit would use if they exist (sections 6 and 13) but does not build or specify their auth.
- Audience demographics for platforms that offer none. The kit says "not available"; it does not estimate demographics from third-party tools.
- A hosted service. The kit stays a static site plus the existing Worker for the gate.
- Replacing the owner PDF or the gate. Both stay; they gain the new sections.
- Scraping any platform against its terms.

## 4. Anti-goals

These are failures even if the build passes.

- **Never fake comparability.** No sum, average or ranking across different units (a follow, a concurrent viewer, a download, an email, a view). No chart that puts them on one axis.
- **Never a number without a source and a date.** A value with no `as_of` and no trust label does not render. There is no "unknown source" badge.
- **Never zero for missing.** Missing is `not available`, with the reason when known.
- **Never present typed or estimated numbers as platform-verified.** The FTC's 2024 rule on fake indicators (bars buying or selling followers or views from bots or hijacked accounts for commercial use) is the legal backdrop; the safe product behavior is plain labeling. Source: https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers, read 2026-11-19 (VERIFIED for the rule; the product implication is INFERRED). This is a design rule, not legal advice.
- **Never one deduplicated "unique people" figure** without a poll behind it, and then only as a labeled estimate beside the range.
- **Never a ranking of one live platform above another** in the layout, ordering or wording (partner parity).
- **Never a vendor tier word** ("nano", "micro") as a headline. Tier scales are context for the quote, not the kit.

## 5. Constraints

- **Privacy and public-repo hygiene.** The engine repo holds no real handles, numbers, partner names or hostnames; examples use `example.com` and made-up creators. Owner callsign, grid square and employer never appear. Kit content lives in the private instance repo.
- **Twitch and Kick equal.** Same measures, same field order, same visual weight, same wording pattern, same degrade rules. A difference in *source* (Twitch exposes a follower count to an app token; Kick's documented channel response has no follower field, per [research/kick.md](../../research/kick.md), VERIFIED 2026-10-23) shows up only in each number's trust label, never in layout or prominence. Neither is demoted for needing hand entry.
- **No hard-coded plan or host.** No Cloudflare plan limit, hostname or instance is baked into the engine. Platform and host names come from config. Limits quoted here are configuration defaults, not code constants tied to a vendor tier.
- **Static plus Worker.** The public kit and public PDF remain static files. The gated page remains a file served by the Worker after the gate. No new runtime service, no new third-party request from any kit page, no new script on the gated page.
- **Owner-private versus public numbers.** The display policy table in `reach_around/config/kit.py` stays the single rule. YouTube Analytics figures collected with private OAuth scopes never enter the kit; a YouTube figure appears only if the owner types it and sets `owner_supplied: true` (kit.md). A platform's terms limit what may be shown publicly; the display policy, not the template, decides.
- **UTC and dates.** Every date is a UTC day, labeled UTC, as today.
- **Accessibility and theme.** Dark mode default; no new light fills; contrast and target sizes as the existing kit; no color-only meaning (every chart prints its labels and values).
- **American English, no em dashes.**

## 6. The unified data model

### 6.1 One row type: a measure

Everything on the kit is a **measure**: one value, one unit, one source, one date. This generalizes the existing `PlatformMetric`, `PlatformAnalytics.stats`, `EngagementRow` and `RecentPost` figures, which today carry their provenance in different shapes (`source: manual|counts`, `synced_with`, `counts` text, `owner_supplied`).

| Field | Meaning | Rule |
| --- | --- | --- |
| `platform` | the platform slug (the existing `PLATFORM_SLUGS`, plus `peertube`, `lemmy`, `podcast`, `newsletter`, `blog` as new slugs) | required |
| `account` | the public handle or address the number is about | optional, public text |
| `key` | what is measured, from a code registry (6.3) or a custom key | required |
| `value` | a whole number, or for `percent` an integer in tenths | required, never negative; absent means `not available` |
| `unit` | `follows`, `viewers`, `hours`, `views`, `interactions`, `downloads`, `emails`, `clicks`, `percent` | fixed per registry key; custom keys choose one |
| `period` | `{start, end}` UTC days, or none for a point-in-time count | required for averages, rates and totals over time |
| `as_of` | the UTC day it was read or computed | required |
| `trust` | `api`, `hand_kept`, `estimate` | required (6.2) |
| `method` | one line: what endpoint or procedure produced it | derived for `api`; required for `estimate`; optional for `hand_kept` |
| `verify_url` | an `https` link anyone can open to re-check | optional; only where a public page shows the number |
| `visibility` | `public` or `gated` | defaults from the registry and the display policy; never wider than the policy allows |

The measure model keeps the existing field limits and the YouTube Analytics refusal. A measure never carries free-form HTML. Whole numbers only, as in the history store ([social-history.md](../../social-history.md)).

### 6.2 Trust labels

Three labels, matching the owner's wording. The label is data, not decoration: the build derives it from where the value came from and refuses a mismatch.

| Label | Printed as | Set when | Public re-check |
| --- | --- | --- | --- |
| `api` | "API verified, read 2026-11-19" | the value came from a collector in `numbers.sources`, so the history store holds `source: api` | `verify_url` when the platform has a public profile page that shows the count |
| `hand_kept` | "Hand-kept, 2026-11-15" | a person typed it, or imported it from a platform export; the history store holds `source: hand_kept` or `imported` | usually none |
| `estimate` | "Estimate: <method>, 2026-11-15" | derived or modeled (a poll-based overlap, a projected view count) | none |

Notes:

- "API verified" means *the platform's own API returned it to our collector on that day*. It does not mean audited by a third party, and the page footnote says so. A sponsor can re-check only where a public page exists, which is why `verify_url` is separate.
- `imported` (a CSV or export file) prints as hand-kept with the method line naming the export ("Imported from the platform's analytics export"). A fourth public label would add noise; the method line carries the difference. This is a deliberate collapse of the research report's four-value trust field.
- A computed figure (average views per item, engagement rate) takes the *weakest* trust of its inputs, prints its formula, and its inputs keep their own labels. This extends the existing rule that the formula is printed under each measure.
- Stale marks stay as today: older than the platform's stale limit (30 days default) prints "(out of date)" and the build warns.

### 6.3 The measure registry (in code)

A registry of known keys, each with its unit, definition text, formula if computed, the platforms it applies to, whether the platform exposes it, and the default visibility. Config picks keys; it does not invent definitions for known ones. Starting set:

**All platforms**
- `followers` (unit `follows`; `subscribers` for YouTube and newsletters; the existing rule that labels follow what the platform reports stays)
- `posts_per_week` (items per week over a period; `views`-free, a cadence)

**Live (Twitch, Kick, and YouTube Live)** with identical fields and order:
`followers`, `avg_ccv`, `peak_ccv`, `hours_watched`, `unique_chatters`, `followers_gained`, `hours_streamed_per_week`.
Definitions follow Kick's own analytics help (https://help.kick.com/en/articles/15201110-understanding-your-streamer-analytics, dated 2026-08-20, read 2026-11-19; VERIFIED for Kick's wording). Twitch's help page did not render for the research run, so for Twitch the same definition is used and labeled INFERRED until read. Every live measure prints its period.

**Fediverse and open platforms (Mastodon, Pixelfed, Lemmy, PeerTube, Bluesky)**
- `followers`
- `posts_per_week`
- `median_boosts_30d`, `median_favorites_30d`, `median_replies_30d` (per post; Bluesky: reposts, likes, replies). Median, not mean: one viral post should not define the creator. The mean is shown only beside the median, and only if the owner enables it.
- `interactions_per_post_30d` = interactions / posts, with the formula printed
- `peertube_watch_time_30d`, `peertube_avg_watch_time`, `peertube_unique_viewers_30d` (PeerTube only; the platform documents these per video, including remote ActivityPub and embed sessions, though one source-code reading disagreed on remote sessions, so the page says "as reported by the instance"; https://docs.joinpeertube.org/use/video-stats, read 2026-11-19; VERIFIED for the docs, INFERRED for the federated-completeness caveat)
- `lemmy_subscribers`, `lemmy_active_users_month` (Lemmy community fields; INFERRED for post fields)
- Instance size from NodeInfo is shown as **context** ("the instance has N active users in 30 days"), never as the creator's reach. NodeInfo is self-reported and unauditable (INFERRED).

What these platforms do **not** expose, stated on the page as such and not estimated: impressions, reach, demographics on Mastodon and Bluesky; any insights on Pixelfed. VERIFIED in the repo's own research notes ([mastodon.md](../../research/mastodon.md), [bluesky.md](../../research/bluesky.md), both reviewed SOUND on 2026-10-23, plus the Mastodon docs they cite). The registry stores these absences as `not_provided_by_platform` reasons so the page can say "Mastodon does not provide impressions" instead of leaving a gap. That is a feature: a sponsor who knows the platform sees an honest kit, and one who does not learns why.

**Video and short form (YouTube, TikTok, Instagram, Threads)**
`followers`, `avg_views_per_item` (existing formula), `engagement_rate` (existing formula, interactions / views x 100), `posts_per_week`. YouTube follows the existing owner-supplied-only rule.

**Audio and text (podcast, newsletter, blog)**: `downloads_30d` (IAB definition; unit `downloads`), `newsletter_delivered` and `newsletter_clicks` (clicks, not opens: opens are unreliable since Apple Mail Privacy Protection, https://mailchimp.com/en-gb/help/apple-privacy-faq/, read 2026-11-19, VERIFIED), `blog_views_30d` (from the existing blog collector). These are optional and later (section 13).

**Custom measures.** A creator may define a measure with a name, a unit from the fixed list, a one-line definition and a source. A custom measure is always `hand_kept` or `estimate`, never `api`. Example: weekly active members of a community chat.

### 6.4 Where the values come from

Three feeds already exist and stay:

1. `numbers.json` from `counts refresh` (API collectors for follower counts: Bluesky, Mastodon and other Mastodon-API servers, Twitch, YouTube, Instagram, Threads, TikTok, GitHub, Discord, Matrix). VERIFIED against `docs/config.md#numbers` at `f853cad`.
2. The append-only history store (`numbers.history_file`), which already records `source: api | hand_kept | imported`, an `era` for definition breaks and corrections. The kit's `trust` is a direct read of this, so the label cannot drift from the record.
3. Typed config values (today's `metrics`, `engagement`, `analytics`).

v2 adds one more kit-side input and no new store: per-platform **measure rows** in the config for what has no collector, validated like today's rows. When both a collector value and a typed value exist for the same key and day, the build prefers the newer `as_of`, then the `api` value, and prints one build note. A typed value never silently shadows a fresher API value (this extends the existing "stale loses to fresh" rule in `docs/config.md`).

Live measures (`avg_ccv`, `peak_ccv`, `hours_watched`) need either a sampler that polls while live or the creator's own dashboard export. Twitch's Get Streams returns only current viewers and has no history, so a sampler is a persistent job (VERIFIED in [research/twitch.md](../../research/twitch.md), 2026-10-23, review verdict FIX with corrections applied). Until a sampler exists these are `hand_kept`, entered from the platform's own dashboard. That is the same for Twitch and Kick, which keeps parity honest.

### 6.5 The cross-platform audience summary

One block, always the same shape, always stating its assumption:

```
Audience across platforms (as of 2026-11-15, UTC)
  6 platforms, 21,300 follows.
  Largest single audience: 9,100 (platform name).
  People who follow in more than one place are counted more than once.
  Unique people are somewhere between 9,100 and 21,300.
  Not included: viewers, downloads, emails (different units, shown on their own).
```

Rules:

- Only measures with unit `follows` and a flag `in_summary` (default true for `followers`, false for community `members` pending decision Q7) enter it.
- **Lower bound** = the largest single `follows` measure (a set cannot be smaller than its largest member). **Upper bound** = the sum (assuming no overlap). Both are plain arithmetic; the sources are the set bounds, not a vendor method. The existing unified total becomes the upper bound and is relabeled; its existing `in_total: false` flag is honored as `in_summary: false`.
- The block prints the **oldest** `as_of` among its inputs, the number of inputs, and any stale one, like today's total.
- It carries a plain caveat that follower counts include inactive and automated accounts, because platforms do not remove them from the counts the collectors read (INFERRED; no platform page was read for this, so the sentence is generic and does not name a share).
- **Optional estimate.** If the owner has run a one-question poll ("where else do you follow me?") the kit may print one extra line: "Estimate from a poll of N people on <date>: roughly X unique people", where X = sum of follows / (1 + average number of other platforms per respondent). It is `estimate`, the poll size and date are printed, and it is shown *beside* the range, never replacing it. The research report calls this an invented method (private report, section 8.3) and the kit says "estimate" in its label for that reason. The independence formula (Sainsbury, https://support.mediaocean.com/tview/help/t/mixing_media.htm, read 2026-11-19, VERIFIED) is **not** used: it needs a defined population and overstates unique reach when audiences correlate (INFERRED).
- Fediverse note: a follower count on Mastodon or Pixelfed is a count of accounts on any server that follow, and ActivityPub collections "MAY be filtered" by servers (https://www.w3.org/TR/activitypub/, read 2026-11-19, VERIFIED), so the page labels such counts "as reported by the server".

## 7. What the kit shows, and how each part degrades

### 7.1 Public page (static, anyone)

1. Header (photo, title, tags, description, contact line), unchanged.
2. **Audience summary** (6.5), including the "before and after Reach Around" era text when the cutover day applies.
3. **Platform sections**, one per configured platform, each a small card with: the platform name and handle, a headline number, the measures it has with trust labels and dates, a "not available on this platform" line for registry-known absences, and a re-check link where `verify_url` exists. Sections group by kind: **Live** (Twitch and Kick together), **Video and short form**, **Fediverse and open** (Mastodon, Pixelfed, Lemmy, PeerTube, Bluesky), **Audio and text**. Group order is fixed by kind in code; order inside the live group follows config order and is not alphabetical-by-popularity (Q3).
4. Most recent posts, as today (counts only in the PDF).
5. The gate form (if gated content exists), unchanged.

Public means: counts that the platform itself publishes on a public page, plus the owner's chosen computed figures from them. Engagement tables, demographics and deal data stay gated, as today. A platform whose terms limit public display stays gated by display policy (YouTube Analytics is the existing case; Twitch's developer terms on storing and re-showing data are unresolved, see risk R2).

### 7.2 Gated page and owner PDF (after the gate)

Everything above, plus the gated sections: engagement and average views per period with formulas, demographics where a platform provides them, partners, case studies, packages, rates, testimonials, disclosure, inquiry. New in v2: each gated section shows the trust label on every figure too, and a short "How to read these numbers" panel that explains the three labels once.

### 7.3 Public PDF

The public page rendered to a dated PDF as today ("Valid as of" line, gate box dropped), with platform sections laid out one per block so a page break never splits a section. Re-check links print as plain URLs, since a PDF needs them readable on paper. The PDF stays offline-sealed under the existing stager rules; no new tag is allowed through the allowlist.

### 7.4 Degrade rules (one table, applied by the renderer, not by templates)

| Situation | What the kit shows |
| --- | --- |
| Platform configured, no data at all | a section card with the name, the handle and "No numbers yet", not hidden, not zero. Option to hide empty cards (Q8) |
| Follower count only (the common case) | the headline number with its label and date; other measures are omitted, not zeroed. Registry-known platform absences print "not provided by <platform>" |
| API ok, then collector fails for days | last good value with its `as_of` and "(out of date)" past the stale limit; never replaced by zero (existing rule in `counts refresh`) |
| Definition break (platform changed what a number means) | an `era` note beside the value, from the history store, as today |
| Hand-kept number past its stale limit | "(out of date)" mark plus the build warning; the production check still fails, as today |
| Computed figure with a missing input | `not available` plus the missing input named; never filled |
| Only one of Twitch and Kick has live data | both stay in the same live block with the same fields; the empty side says "No live numbers yet" in the same style, so one platform never looks absent or demoted |
| Platform with no public profile page | no re-check link; the label says why ("typed from the platform dashboard") |
| Mixed units in one group | shown in separate rows with their units; no combined figure |
| No measures anywhere | the existing minimal kit: header, contact and gate |

A measure that fails validation fails the build with the field path, never the value, as today.

## 8. How numbers refresh

**Daily rebuild.** The instance repo already ships a scheduled workflow pattern (a daily cron running `counts refresh` then the build; [customizing.md](../../customizing.md#a-scheduled-refresh-in-your-instance-repository), VERIFIED at `f853cad`). v2 keeps that: refresh, append to the history store, build the site, publish static output and the gated page. The kit build reads the history store to set `trust` and `as_of` for each `api` measure. A failed source never overwrites and never becomes zero (existing behavior).

**Hand-kept entry flow.** Today `counts history add` appends one hand-kept value from the command line, and typed config rows exist. v2 proposes one flow for all hand-kept measures:

1. `reach-around kit measure add --platform ... --key ... --value ... --as-of ... [--period ...] [--note ...]` writes to the history store as `hand_kept` (extending the existing `counts history add` with `period` and registry keys). The config no longer needs a typed `value` for these; it only declares which measures a platform section shows.
2. `reach-around counts stale` (existing) lists hand-kept measures past their stale limit and is the reminder; the weekly or daily scheduled run prints it, and the alert system can notify.
3. The local editor (a separate design) may add a form over the same command later; the CLI is the one source of truth.
4. Importing a platform export (CSV) is `imported`, one parser per platform, each its own small slice.

**What does not refresh by itself.** Live metrics without a sampler, demographics, podcast and newsletter figures, and any platform without a collector are hand-kept and carry that label. The kit never hides that.

**Timing.** All as-of values are UTC days. A daily run means a number is at most about one day old on an API platform; the page prints the day, not a promise of freshness.

## 9. Hand-off to the sponsor page and the quote

The kit publishes a **measures file** (`kit-measures.json`, gated, never published, owner-only, same wall as `kit.html`) holding the resolved measures with their trust labels. The sponsor page and the quote builder read it so a quoted number is the same number the kit shows, with the same label and date. The rate calculator takes its inputs (average CCV, expected views, downloads) from labeled measures and prints each input's trust beside it. The kit does not compute a price. This keeps the pricing work out of this slice and out of the kit's honesty contract.

## 10. What changes in config

All additive for the first slice; no existing key is removed until the deprecation step.

- New `kit.sections` list (or `kit.platforms`; name settled at build): one entry per platform with `platform`, optional `account`, `show` (a list of registry keys, or `default` for the group's default set), optional `headline` key, and optional typed `measures` rows for what has no collector. Order inside a group follows list order.
- New optional `kit.audience_summary` block: `include` (default all `follows`), `poll` (optional: `respondents`, `as_of`, `avg_other_platforms`, all hand-kept) and `show_estimate` (default false).
- New optional `kit.custom_measures` list (name, unit, definition, source, trust).
- `kit.metrics`, `kit.analytics` stats and `kit.engagement` keep working in v2.0. The loader compiles them into measures internally so one renderer serves both. They are marked deprecated once the sections cover their cases, and removed only in a later major step with a migration note.
- `numbers.sources` gains `peertube` and `lemmy` kinds only when those collectors are built (slice 6 or later).
- New registry slugs `peertube`, `lemmy`, `podcast`, `newsletter`, `blog` added to the platform list when each collector or entry path lands.
- A JSON Schema update (`docs/site.schema.json`) and the config reference page, written with each slice.

Nothing in config holds a credential, hostname or real handle in the public repo's examples.

## 11. Approaches, trade-offs and recommendation

**A. Render-layer only.** Leave config and the three input shapes as they are. Add platform-section rendering, trust labels and the audience range on top of today's `metrics`, `analytics`, `engagement` and `recent_posts`.
- For: smallest change, no migration, fastest to show the owner something. The wall and the PDF tests barely move.
- Against: provenance stays in four different shapes, so the trust label has to be inferred from field names; fediverse and live measures have no home; `engagement` rows stay platform-wide rather than per-measure. It lands the look and not the model.

**B. Unified measure model with compatibility shim (recommended).** Introduce the measure row (6.1), the registry (6.3) and `kit.sections` (10); compile old fields into measures at load; render everything from measures. Roll out in slices (13) behind the same display policy.
- For: one honest model; trust label read from the history store, so it cannot disagree with the record; fediverse and live sets become data in a registry rather than template code; the sponsor page and quote read one file (section 9); old configs keep working.
- Against: the largest design surface; the compile shim and the leak and PDF tests need care; a registry adds a maintenance duty when a platform changes a definition (mitigated by the existing `era` mechanism).

**C. History store as the source of truth for everything.** Make the kit a view over the history store, with config holding only presentation.
- For: elegant; one append-only record with a hash chain.
- Against: the store is whole numbers per platform, metric and day with no unit, period or method fields, and it lives in the private instance repo. Extending it reshapes a file the owner already depends on, and typed one-off figures (a case study total, a poll result) are not a time series. Too large a bet for this slice.

**Recommendation: B**, delivered as thin slices, with A's first two slices doing the visible work early. B's first slice is nearly A plus a trust label, so the owner sees the new look before the registry exists. C is reconsidered only if the history store later gains unit and period fields for its own reasons.

## 12. Text sketch of the public kit page

```
+----------------------------------------------------------------+
| [wide photo]                                                   |
| Example Creator media kit                                      |
| Gaming - Hardware - Open source            (content tags)      |
| A short description in the creator's words.                    |
| Contact: partners at example.com                               |
+----------------------------------------------------------------+
| AUDIENCE ACROSS PLATFORMS            as of 2026-11-15 (UTC)    |
| 6 platforms, 21,300 follows.  Largest single: 9,100 (Platform) |
| Unique people are somewhere between 9,100 and 21,300.          |
| People on several platforms are counted more than once.        |
| Not added: viewers, downloads, emails (their own units).       |
+----------------------------------------------------------------+
| LIVE                                                           |
| +----------------------------+ +----------------------------+  |
| | Twitch                     | | Kick                       |  |
| | 4,200 followers            | | 3,100 followers            |  |
| | API verified, 2026-11-15   | | Hand-kept, 2026-11-14      |  |
| | Avg viewers   84  (30 d)   | | Avg viewers   71  (30 d)   |  |
| | Peak viewers  310 (30 d)   | | Peak viewers  260 (30 d)   |  |
| | Hours watched 5,100 (30 d) | | Hours watched 4,300 (30 d) |  |
| | Chatters      410  (30 d)  | | Chatters      360  (30 d)  |  |
| | each: label + date + how   | | each: label + date + how   |  |
| | Re-check: public profile   | | Re-check: public profile   |  |
| +----------------------------+ +----------------------------+  |
+----------------------------------------------------------------+
| VIDEO AND SHORT FORM                                           |
| YouTube 6,500 subscribers (API, 11-15)   TikTok 3,800 (hand..) |
| Avg views per video  (formula, period, inputs labeled)         |
+----------------------------------------------------------------+
| FEDIVERSE AND OPEN                                             |
| Mastodon    1,900 followers   API, 2026-11-15  [re-check]      |
|   median boosts per post 6, favorites 14, replies 3 (30 d)     |
|   Not provided by the platform: impressions, demographics      |
| Bluesky     2,100 followers   API, 2026-11-15  [re-check]      |
|   median reposts 4, likes 21, replies 5 (30 d)                 |
| PeerTube    340 subscribers   Hand-kept, 2026-11-10            |
|   Watch time 820 hours (30 d), as reported by the instance     |
| Instance context: server has 4,000 monthly active users        |
|   (self-reported; not this creator's reach)                    |
+----------------------------------------------------------------+
| AUDIO AND TEXT   (only when configured)                        |
| Newsletter 2,300 delivered, 190 clicks (30 d)  Hand-kept       |
+----------------------------------------------------------------+
| HOW TO READ THESE NUMBERS                                      |
| API verified: the platform's API returned it to our collector. |
| Hand-kept: typed from a dashboard or export on the date shown. |
| Estimate: derived; the method is printed with it.              |
+----------------------------------------------------------------+
| MOST RECENT POSTS (grouped by platform, as today)              |
+----------------------------------------------------------------+
| Detail for partners: [email] [Request access]  (gate form)     |
+----------------------------------------------------------------+
```

All numbers above are made up. The sketch is a layout, not a visual design; the look is settled with the existing theme tokens and checked against the owner's reference images before any slice merges (the owner's standing visual-review rule). On a phone the live cards stack with the same field order. The page fits a short-page budget by collapsing "How to read these numbers" into a native disclosure.

## 13. Rough slice order

Each slice is one small PR with its own issue and card under epic #245. Order is by value to the owner's first priority, and no slice needs the next.

1. **Trust label on existing numbers.** Add `trust`, method text and the legend to what the kit already shows (`metrics`, `analytics`, `engagement`), reading the history store where present. Public page, gated page and both PDFs. No config break.
2. **Audience summary as a range.** Replace the unified total with the lower and upper bound block (6.5), with the unit rule and the stale and era notes. Poll estimate line stays behind `show_estimate`.
3. **Measure model and registry, compile shim.** The model, the registry for the existing keys, `kit.sections` with default sets, old fields compiled into measures. Renderer reads measures only.
4. **Live block: Twitch and Kick side by side.** The seven live measures from hand-kept entry; the parity tests (identical field order and markup for both, a test that swaps them and compares the output shape).
5. **Fediverse-first block.** Mastodon, Bluesky, Pixelfed measures and the "not provided" lines; re-check links; the median interaction measures from a collector that reads public post data (needs its own collector design; hand-kept until then).
6. **Hand-kept entry flow.** `kit measure add`, stale reminders wired to the alert system, one importer (platform export) as the pattern.
7. **Measures file and sponsor hand-off** (section 9), consumed by the sponsor page work.
8. **PeerTube, Lemmy, podcast, newsletter and blog measures.** One platform per PR, each with its collector or import path.
9. **Deprecate the old keys** with a migration note and a `check` warning.

Sampler for live measures (a collector, persistent job) is a separate epic item; slice 4 does not wait for it.

## 14. Risks and open questions

### 14.1 Risks

- **R1. A label is only as honest as its inputs.** A hand-kept number can be wrong or flattering. The kit labels it; it cannot verify it. Mitigation: the legend, the stale marks, and re-check links wherever a public page exists. Not solvable by software.
- **R2. Platform terms on storing and re-showing data.** Twitch's developer terms were not read by any run (the page returned navigation text; forum posts describe a 24-hour cache rule that is unconfirmed; see [research/twitch.md](../../research/twitch.md)). Kick's developer terms were also not found. INFERRED risk. Until a human reads both, Twitch and Kick API-derived values stay behind the existing display policy and the kit uses hand-kept entry for anything beyond a public follower count. This applies equally to both platforms.
- **R3. Definition drift.** A platform changes what a number means. The history store's `era` records and the registry's definition text are the defenses; a registry change needs a changelog line.
- **R4. Fediverse counts are server-reported.** A server can hide or filter counts, and a Mastodon-API-compatible server can report anything. The page says "as reported by the server" and links to the public profile; a sponsor can look for themselves.
- **R5. Sampler cost and consent.** Live measures need a persistent sampler or a dashboard export. Until then the live block is hand-kept, which the kit shows plainly.
- **R6. Registry maintenance.** A growing registry is code the owner must review when platforms change. Keep it small and grow it only with a collector or entry path.
- **R7. Leak surface grows.** Every new field is a possible leak between the public and gated pages. Mitigation: new fields enter the display policy table first, the existing leak search and the PDF text check cover them, and the slice's tests add a gated value per new field.
- **R8. No market rate for fediverse reach.** No source found prices a Mastodon or Bluesky audience (private research, section 8.2; INFERRED from absence in what was read). The kit sells audience fit and re-checkable engagement, not size, and says nothing about price.
- **R9. Beacons keeps adding trust features.** Parity on badges is not the goal. The differentiators are method and re-check links, uncovered platforms, honest overlap and no fees; if those slip, the kit is only another kit.

### 14.2 Decisions for the owner

Answer yes or no. The recommended answer is first in each.

1. **Adopt approach B (unified measure model with a compatibility shim) over A or C?** Recommended: **yes**. Slices 1 and 2 ship the visible change first.
2. **Collapse imported data into the "hand-kept" label, with the export named in the method line, rather than show a fourth label?** Recommended: **yes**.
3. **Order the live block Twitch then Kick as set by config order, with no sorting by size, and print both in one shared block with identical fields?** Recommended: **yes**. (If no: say whether the order should alternate by build or be alphabetical.)
4. **Show the audience summary as a range (largest single audience to sum of follows), with the overlap sentence, and never one deduplicated number?** Recommended: **yes**.
5. **Allow the optional poll-based estimate line, shown beside the range and labeled "Estimate"?** Recommended: **yes, off by default**.
6. **Make fediverse follower counts and per-post medians public, each with a public re-check link, rather than gated?** Recommended: **yes**. They are public by nature and re-checkable.
7. **Keep community `members` (Discord, Matrix) out of the "follows" range by default and show them on their own line?** Recommended: **yes**. A chat member is not a follower of the same kind.
8. **Show an empty platform as a "No numbers yet" card, rather than hide it?** Recommended: **yes**. A configured but empty platform is a gap the owner should see; add a per-platform `hide_if_empty` switch for pages that should omit it.
9. **Keep `engagement` rows, demographics and deal data gated, as today, and publish only counts a platform itself shows publicly?** Recommended: **yes**.
10. **Use median (not mean) for per-post interaction measures, with the mean optional beside it?** Recommended: **yes**.
11. **Read the Twitch and Kick developer terms (a human read) before any collector output beyond a public follower count is shown on the public kit?** Recommended: **yes**. This is a file-and-block item, not a design change, and it applies to both platforms equally.
12. **Publish the gated measures file (section 9) for the sponsor page and quote to read, owner-only and never published?** Recommended: **yes**.
13. **Deprecate `kit.metrics`, `kit.analytics` stats and `kit.engagement` after slice 9, with a `check` warning and a migration note, rather than keep both forever?** Recommended: **yes**.
14. **Accept the slice order in section 13, with live-metric sampling tracked as its own item rather than a prerequisite?** Recommended: **yes**.

## 15. Not verified, and what would close it

- Twitch's own definitions of average and peak concurrent viewers and unique viewers: the help page did not render during research. Close by reading it with a browser and recording the date.
- Twitch and Kick developer terms on storing and republishing data (R2).
- Whether Kick or Twitch dashboards offer an export suitable for import; unknown. Close by checking the creator dashboards.
- PeerTube federated-view completeness; check current PeerTube source before claiming it. The page wording ("as reported by the instance") avoids the claim.
- Lemmy post-level fields and Pixelfed follower and post count fields (INFERRED in the research).
- Any published sponsor valuation of fediverse audiences (none found; R8).
- Per-platform brand-disclosure rules (Twitch, Kick, TikTok, Meta): not found or not verified; the kit makes no compliance claim.

## 16. Sources

Public pages cited above, each read 2026-11-19 unless noted:

- https://linktr.ee/help/en/articles/16937450-how-to-set-up-and-share-your-media-kit (Updated October 28, 2026)
- https://beacons.ai/i/whats-new
- https://help.beacons.ai/en/articles/4704705
- https://help.kick.com/en/articles/15201110-understanding-your-streamer-analytics (2026-08-20)
- https://docs.joinpeertube.org/use/video-stats
- https://www.w3.org/TR/activitypub/
- https://support.mediaocean.com/tview/help/t/mixing_media.htm
- https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers
- https://mailchimp.com/en-gb/help/apple-privacy-faq/

Repository documents (state at `origin/main` `f853cad`): [kit.md](../../kit.md), [config.md](../../config.md), [social-history.md](../../social-history.md), [analytics-design.md](../../analytics-design.md), [research/twitch.md](../../research/twitch.md), [research/kick.md](../../research/kick.md), [research/mastodon.md](../../research/mastodon.md), [research/bluesky.md](../../research/bluesky.md), [customizing.md](../../customizing.md), [2026-10-23-reach-around-design.md](2026-10-23-reach-around-design.md).
