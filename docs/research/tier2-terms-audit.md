<!-- SPDX-License-Identifier: Apache-2.0 -->
# Tier 2 platform terms audit: Bluesky, Mastodon, Discord, GitHub

> **Status: draft for the maintainer, issue #585 (epic #364).** Documentation, not legal advice. Read from public vendor pages on 2026-11-26. It closes the Tier 2 rows that [platform-terms-retention.md](platform-terms-retention.md) left open (written for #382). No collector code is proposed here and nothing was run against a live account.
>
> **Marking.** VERIFIED means the quoted or paraphrased text was on the page named, on the date given. INFERRED means reasoned, or read only through a summarizing fetch, or carried from a repo note. UNREAD means the page could not be read; the verdict then says so and the owner or counsel reads it.

## Scope and the storage rule

The project stores aggregate counts only (see [beacons-parity.md](beacons-parity.md) and [../privacy.md](../privacy.md)): a follower count, a post count, per-post totals as bare integers, a star count. No post text, handle, follower list, member id or message content. Every verdict below is judged against that rule. A platform that is fine for a count may not be fine for content, and this page does not cover content.

The Bluesky and Mastodon collector design is [Fediverse collector: public post counts for Mastodon, Bluesky and Pixelfed](../design/fediverse-collector.md) (PR 914, branch `docs/743-fediverse-collector-design`; not on main when this page was written). Its facts about endpoints, field names and call budgets are not repeated here. This page adds the terms view.

## Verdict table

| Platform | Use | Verdict | Deciding clause or fact | Source, date read |
|---|---|---|---|---|
| Bluesky | Public follower, post and per-post counts, read without login | Allowed with conditions | The Terms of Service say nothing about automated access or rate limits, and say "Bluesky does not control how others may use your Content" (3.3). No separate developer terms were found. Public APIs return HTTP 429 over a limit. | [Terms of Service](https://bsky.social/about/support/tos), 2026-11-26 (page date not captured), INFERRED through a summarizing fetch for the section numbers |
| Bluesky | Storing post text, handles or follower lists | Unclear: owner or counsel to read | Deletion "may not always be possible" across the network (3.6), so stored copies of others' content carry a deletion duty the page does not define | same |
| Mastodon (project, mastodon.social) | Public counts from one named instance | Allowed with conditions | The instance terms "do not apply to anyone who is not a registered user" (effective 2026-08-31); no clause on automated reads or scraping was found in the text | [mastodon.social terms](https://mastodon.social/terms-of-service) read through `/api/v1/instance/terms_of_service`, 2026-11-26 |
| Mastodon (any other instance) | Public counts | Unclear: owner or counsel to read | Each instance sets its own terms and rate limits; none other than mastodon.social was read | INFERRED, [docs/research/mastodon.md](mastodon.md) |
| Discord | Approximate member and online counts through a bot | Unclear: owner or counsel to read | The Developer Policy and Developer Terms pages returned HTTP 403 to every automated read on 2026-11-26 (a bot challenge). A search result shows the 2020 policy limited retention to what the application needs; the current text was not read. | UNREAD: [Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy) (search snippet shows "Effective date: July 8, 2024"), [Developer Terms](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service) |
| Discord | Member ids, names, message content | Unclear: owner or counsel to read, and out of scope under the storage rule | Not read; the project does not store these | same |
| GitHub | Follower, star and repository counts through the REST API | Allowed | Section H rules for the API are about abuse, token sharing and spam; none restricts storing a count | [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service), effective April 27, 2026, read 2026-11-26 |
| GitHub | Follower or stargazer lists | Allowed with conditions | "Misuse of personal information is prohibited" and data may be used only for the purpose the user authorized (Acceptable Use Policies, sections 8 and 7) | [Acceptable Use Policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies), 2026-11-26, INFERRED reading |
| GitHub | Traffic (views, clones) for the owner's own repositories | Allowed with conditions | Needs write access; the endpoints return the last 14 days, so the job must keep its own daily history | [Traffic API](https://docs.github.com/en/rest/metrics/traffic), 2026-11-26 |

## Bluesky

- **Automated reads.** The Terms of Service (https://bsky.social/about/support/tos, 2026-11-26) do not address scraping, bots or API access. The only related line is "Bluesky does not control how others may use your Content" (3.3). The network is open (AT Protocol), so a read of public records without login is the documented model. A separate developer policy was not found. UNVERIFIED whether one exists.
- **Rate limits.** From the Bluesky developer docs (https://docs.bsky.app/docs/advanced-guides/rate-limits, read 2026-11-26 through the docs source on GitHub because the site page rendered empty): overall API requests to a hosted account service are "rate limited by IP", 3000 per 5 minutes; content writes are 5,000 points per hour and 35,000 per day, which a read-only collector does not touch. The page says the limits "are likely to evolve", other hosts "are likely to have different rate-limits", and many services return rate limit headers and HTTP 429. The limits for the public read host are not on that page: UNREAD.
- **Retention and deletion.** The Terms of Service say Bluesky may "store and display your posts" to other developer applications (3.5.1), and that "complete deletion across the network may not always be possible" (3.6). Practical result: a stored count has no deletion duty; stored text would. This matches the project rule.
- **Attribution and display.** None stated on the page. A public number with a plain label is within the open model. INFERRED.
- **Constraint on the collector.** Respect HTTP 429 and the rate limit headers, send a descriptive User-Agent, and keep the call count per run small. The fediverse design gives the budget.

## Mastodon

- **There is no central terms page for the API.** The project (joinmastodon.org) publishes a server covenant for the server picker, which governs which servers are listed, not API use (https://joinmastodon.org/covenant, 2026-11-26). Terms belong to each instance.
- **mastodon.social terms** (effective 2026-08-31, operator Mastodon GmbH, English text read on 2026-11-26): they apply "to registered users", and "do not apply to anyone who is not a registered user of the Service". The licence clause lists making content available through "one or more APIs for application access". No clause on automated reads, scraping or rate limits was found in the text read. Deleted content "may remain in our backups", and "Deleting Content from the Service does not guarantee that it will be deleted from other servers", which is the federation caveat.
- **mastodon.social privacy policy** (record dated 2022-10-07 by the API, read 2026-11-26): public and unlisted posts are "available publicly"; server logs with request IP addresses are kept "no more than 90 days"; the latest login IP "up to 12 months". Nothing there limits reading public data.
- **Rate limits.** Default limits from the Mastodon documentation (https://docs.joinmastodon.org/api/rate-limits/, read 2026-11-26): "All endpoints and methods can be called 300 times within 5 minutes" per account and per IP. The page calls them defaults; whether an instance may change them is not stated there. A collector reads the response headers rather than assuming 300.
- **Constraint on the collector.** One instance per config entry, the owner's own account, public endpoints, counts only. Re-read that instance's terms and its `/api/v1/instance` limits before enabling it. The owner's instance is not mastodon.social by default, so this audit does not clear it.

## Discord

- **Pages not readable.** The Developer Policy and Developer Terms of Service redirect from `docs.discord.com` to `support-dev.discord.com` and returned HTTP 403 with a bot challenge to every automated read on 2026-11-26 (the same as the 2026-10-23 note in [discord.md](discord.md)). Nothing about retention, deletion, display or attribution is VERIFIED. A search result shows the 2020 policy barred retaining data "longer than necessary" for the application, and the page title shows an effective date of July 8, 2024; the current wording needs a human read in a browser.
- **Rate limits.** From the API docs (https://docs.discord.com/developers/topics/rate-limits, read 2026-11-26): "All bots can make up to 50 requests per second to our API", plus per-route limits, with HTTP 429 and a `Retry-After` header. A daily count read is far below this.
- **Constraint on the collector.** The project already reads approximate counts only, never member ids or messages ([discord.md](discord.md)). That is the conservative shape under any reading of the policy. The owner or counsel reads the two pages before the collector is enabled on a server the owner does not run.

## GitHub

- **Automated reads.** Section H of the Terms of Service (effective April 27, 2026, https://docs.github.com/en/site-policy/github-terms/github-terms-of-service, read 2026-11-26): "Abuse or excessively frequent requests to GitHub via the API may result in the temporary or permanent suspension of your Account's access to the API", and "You may not share API tokens to exceed GitHub's rate limitations." The Acceptable Use Policies define scraping as extraction "via an automated process, such as a bot or webcrawler"; API reads are the sanctioned path.
- **Rate limits.** "The primary rate limit for unauthenticated requests is 60 requests per hour"; authenticated requests have "a personal rate limit of 5,000 requests per hour" (https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api, 2026-11-26). Use a token; a daily job needs a handful of calls.
- **Retention.** The Terms set no storage limit for API data. The traffic endpoints return the last 14 days and require write access to the repository (https://docs.github.com/en/rest/metrics/traffic, 2026-11-26), so history must be stored by the project.
- **Personal data.** Section H bars using the API "for spamming purposes, including for the purposes of selling GitHub users' personal information". The Acceptable Use Policies (section 8) require collected personal information to follow the Privacy Statement and be used only for the purpose the user authorized. A count of followers is not personal information; a list of them is. The project stores counts.
- **Attribution.** No attribution rule applies to showing the owner's own counts. The GitHub Privacy Statement says nothing about followers or stargazers (read 2026-11-26).
- **Section D.9 (access reciprocity)** concerns automated access to train a commercial AI system and does not apply to a counts collector.

## What this changes in the repository

Nothing in code. The follow-ups are the owner's:

1. Read the Discord Developer Policy and Developer Terms in a browser and record the retention and display clauses here.
2. Name the Mastodon instance the collector will read, then re-read that instance's terms and limits.
3. Optionally read Bluesky's current developer terms if the project later stores anything beyond counts.

## Not covered

Instance-specific Mastodon terms other than mastodon.social, Pixelfed, Bluesky third-party relays, Discord policies after the 2024 revision, and any content (text, media, identities). Legal advice: this page is not any.
