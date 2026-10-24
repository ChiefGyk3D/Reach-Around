# Social Blade: API access and metrics (issue #13)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #13. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23. Important limit: socialblade.com returned HTTP 403 to every direct fetch (business-api, developers, docs, terms). Official-page claims below come from search-result excerpts of those official pages or from a third-party mirror, and are marked. Nothing was confirmed from a full read of the official documentation.

> **Verified from primary sources on 2026-10-23** (socialblade.com read in a real browser: the Developers page, API documentation, Terms and Developer Terms of Service). This block supersedes any conflicting figure below, including the monthly cost estimate.
>
> - **Pricing:** 100, 500 and 1,000 credits cost $0.50 per credit (100 credits = $50.00). 5,000 and 7,500 credits cost $0.25 per credit. The page says rates go as low as $0.10 (medium use) and $0.06 (heavy use). Premium members get bonus credits every month.
> - **How a lookup is charged:** pricing is per profile per platform. "Once you look up a profile (and spend a credit) it is good for the next 30 days for free. Looking it up each day to get new data will not incur any more costs during this period. On day 31 it will cost another credit." So daily polling of a profile costs about 1 credit per 30 days at the default history setting.
> - **Real monthly cost:** about 4 credits per 30 days for YouTube, TikTok, Twitch and Instagram, which is roughly $2.00 per month at $0.50 per credit (5 credits, $2.50, if Facebook is added). The earlier estimate of about $75 per month was wrong because it assumed a credit per call.
> - **History depth, per lookup:** `default` 1 credit (30 days), `extended` 2 credits (1 year), `archive` 3 credits (3 years), `vault` 5 credits (10 years, not offered for YouTube). A one-time deep backfill of four platforms is on the order of 18 credits (about $9).
> - **Coverage:** YouTube, TikTok, Twitch, Facebook, Instagram. Not Kick, X, Threads or Bluesky. Public statistics only.
> - **Auth:** `clientid` and `token` as headers (preferred) or query parameters. Keys must be kept private and never put in git or client-side code. Calls against Social Blade's own accounts cost 0 credits and need no key, which is useful for tests.
> - **Terms that shape the design (Developer Terms of Service 3.1, 3.3, 3.4):** data retrieved through the API is "for Your use only"; you may not resell or license it, use it "as part of any other products or services", or offer a service similar to Social Blade. "Data may be shared publicly (in a blog post for example) but credit must be given to Social Blade." The token and client ID may not be shared with any third party. Scraping the website is prohibited.
> - **Design consequences:** (1) each user supplies their own Social Blade key, and Reach Around never ships, hosts or shares Social Blade data between users; (2) a creator showing their own counts on their own public media kit is allowed with a visible credit and link, so the kit needs a "Data from Social Blade" credit wherever it is used; (3) whether a general-purpose open-source tool counts as "another product or service" is a gray reading of 3.1 and should be confirmed with Social Blade before the collector ships to other people. Not legal advice.
> - **Still unverified:** the Instagram endpoint page was not read in full, so the Instagram history tiers and fields are assumed to match TikTok and Twitch.

## Summary

Social Blade sells a paid "Business API" (prepaid credits, client ID plus token headers) for YouTube, Facebook, Instagram, TikTok, and Twitch. It does NOT list Kick. It returns public counts and daily history for any public account, not only the user's own. It can fill Instagram and TikTok public follower counts, which the official platform APIs often gate. It does not give private analytics: no demographics, revenue, or watch time.

## Metrics

| Metric | How to get it | Permission |
|---|---|---|
| Followers or subscribers, total views, uploads/media, likes, rank (sbrank) | `GET https://matrix.sbapis.com/b/{platform}/statistics?query=name` | Headers `clientid` and `token`; credits |
| Daily history | `history` parameter: default 30 days, extended 1 year, archive 3 years, vault 10 years | More credits per tier |
| YouTube by channel ID or @handle | `/youtube/channel/stats` | Same |
| Top lists | `/{platform}/top`, `/top_lists` | Page 0 free, later pages cost; limited output without credentials |
| Demographics, revenue, live stats | Not offered | n/a |

Sources for the table: context7 mirror of the official docs (fetched 2026-10-23); the exact endpoint list is UNVERIFIED against socialblade.com directly. The mirror notes the TikTok section is internally inconsistent (`query` vs `username`).

## User setup

1. User creates a Social Blade account and opens the Developer Console (official page, per search excerpt).
2. User buys a credit pack (below) and receives a client ID and token. I did not sign up, so approval time is UNVERIFIED (appears to be self-serve).
3. User stores both values in their own secret store; the job sends them as `clientid` and `token` headers.
4. The statistics response includes remaining credits and session expiry in an `info` block, so the job can warn when credits run low.

## Pricing and cost

- Pack prices (official page excerpt via search, 2026-10-23): 100, 500, and 1,000 credits at $0.50 each; 5,000 and 7,500 at $0.25 each; tiers go down to $0.06. A competitor page (1Lookup, claims verified August 2026) says 18 tiers, smallest pack 100 credits for $50, purchases final, unused credits expire after six months of inactivity. Review correction: the pricing is corroborated only indirectly (search excerpts of https://socialblade.com/developers; the official pages returned 403 to the reviewer too, reviewer fetch 2026-10-23), so it is not VERIFIED from the official page. The maintainer's figure reads as $50 for 100 credits, i.e. $0.50 per credit, not $0.50 per pack. "Prepaid, one credit per profile lookup" is from a third-party reviewer only.
- Free monthly credits (review correction; New fact): paid Social Blade personal plans include free monthly API credits (Silver 5, Gold 20, Platinum 50) per a search excerpt of the official site (https://socialblade.com/products/subscription/personal, reviewer fetch 2026-10-23; not read directly). This changes the cost model for users who already subscribe: some of the assumptions below may be covered by the plan allowance. Corroborated only indirectly; UNVERIFIED.
- Credits per call: the mirror says YouTube history costs 1 credit (30 days), 2 (1 year), 3 (3 years). The competitor says 1 per profile lookup, up to 5 for ten-year history, and that after paying once a profile can be re-looked up daily for 30 days free. The free relookup is UNVERIFIED (single competitor source; the reviewer could not verify it either). The six-month credit expiry also comes only from the competitor page.
- Assumption A (conservative): 1 credit per call, no relookup discount, default 30-day history, 5 platforms (YouTube, TikTok, Twitch, Instagram, Facebook), one account each.
  - Daily: 5 x 30 = 150 credits/month x $0.50 = $75.00.
  - Weekly: 5 x 4.33 = about 22 credits/month x $0.50 = about $10.80.
- Assumption B (30-day free relookup holds): about 5 credits/month = about $2.50 daily or weekly.
- Only Instagram plus TikTok (gap filling): A daily = 60 credits = $30; A weekly = about 9 credits = about $4.30. B = $1.00.
- Cheaper trick under A: the default call returns 30 days of history, so polling once a month and storing the series gives daily data for 5 credits ($2.50) a month. Worth testing.
- A $50 minimum purchase means the first outlay is $50 regardless.

## Risks and ToS limits

- Terms on redistribution, caching, and public display: UNVERIFIED (Developer Terms of Service not readable; refunds and credit expiry are said to be governed by it). A third-party Go library README says the API was once private and licensed by written permission; likely outdated.
- Reach Around is open source with user-supplied keys, so the code never redistributes Social Blade data itself, but a public dashboard would. The maintainer should read the Developer Terms for display and storage rules.
- Rate limits: not stated anywhere I could read. UNVERIFIED.
- Data is third-party tracked, so freshness depends on Social Blade's crawl. The `allow-stale` flag may return cached data.

## Token lifetime and unattended access

Static client ID and token headers; no OAuth refresh. Lifetime or rotation: UNVERIFIED. The `info` block mentions a session expiry, so the job should read it. Credits are prepaid, so the job fails closed when they run out, with no surprise bill.

## Fallbacks

Official platform APIs for the user's own accounts, CSV import of platform exports, manual entry, or public-page counts. Social Blade is the fallback for Instagram and TikTok, not the primary source.

## Verdict

Official paid API, optional (pricing corroborated only indirectly; platform list and per-call credits from the context7 mirror, not socialblade.com). Use it for Instagram and TikTok public counts and history gaps, weekly or monthly polling. Not feasible for Kick (not listed). Gate on reading the Developer Terms.

## Sources (fetched 2026-10-23)

- https://socialblade.com/business-api, /developers, /api, /developers/docs, /info/terms (all HTTP 403; only search excerpts seen)
- https://context7.com/websites/socialblade_developers (mirror of the developer docs)
- https://www.1lookup.io/compare/social-blade (competitor, low trust)
- https://outlierkit.com/resources/social-blade-pricing/ (third-party, via search excerpt)

## Open questions for the maintainer

1. Read the Developer Terms (display, storage, redistribution), since a browser can reach them and my fetches could not.
2. Confirm credits per call and the 30-day free relookup by checking the Developer Console after a $50 test purchase, or by emailing support.
3. Is a $50 minimum acceptable as an optional per-user cost?
4. Should Kick get a different gap filler, since Social Blade does not cover it?

## Review

Verdict: FIX. Reviewer fetch date for every source: 2026-10-23. Verdict is from the independent review (REVIEW.md, Reach Around #1).
Changed: pricing now marked as corroborated only indirectly (not verified from the official page; the "VERIFIED" wording is removed); added the free monthly credits on paid personal plans (Silver 5, Gold 20, Platinum 50); noted credit expiry and "prepaid, one credit per lookup" are third-party only. Confirmed by the review (context7 mirror): platforms YouTube, Facebook, Instagram, TikTok, Twitch (Kick not listed); headers `clientid` and `token`; YouTube history credits 1/2/3; top lists page 0 free. UNVERIFIED: terms on storage and redistribution, rate limits, credit expiry, the free 30-day relookup, token lifetime, credits per call for non-YouTube platforms, TikTok `query` vs `username`.
