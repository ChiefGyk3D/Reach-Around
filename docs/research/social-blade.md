# Social Blade: API access and metrics (issue #13)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #13. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23. Important limit: socialblade.com returned HTTP 403 to every direct fetch (business-api, developers, docs, terms). Official-page claims below come from search-result excerpts of those official pages or from a third-party mirror, and are marked. Nothing was confirmed from a full read of the official documentation.

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

- Pack prices (official page excerpt via search, 2026-10-23): 100, 500, and 1,000 credits at $0.50 each; 5,000 and 7,500 at $0.25 each; tiers go down to $0.06. A competitor page (1Lookup, claims verified August 2026) says 18 tiers, smallest pack 100 credits for $50, purchases final, unused credits expire after six months of inactivity. So the maintainer's figure of "100 credits at $0.50 per credit" is VERIFIED as $50 for 100 credits, i.e. $0.50 per credit, not $0.50 per pack.
- Credits per call: the mirror says YouTube history costs 1 credit (30 days), 2 (1 year), 3 (3 years). The competitor says 1 per profile lookup, up to 5 for ten-year history, and that after paying once a profile can be re-looked up daily for 30 days free. The free relookup is UNVERIFIED (single competitor source).
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

Official paid API, optional. Use it for Instagram and TikTok public counts and history gaps, weekly or monthly polling. Not feasible for Kick (not listed). Gate on reading the Developer Terms.

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
