# Bot classifier (Worker)

Pure function `classifyBot(signals)` in `worker/src/botclass.ts`, with token
data in `worker/src/botdata.ts`. Issue: Reach-Around#119. Bots are counted in a
separate bucket, never dropped by this code, and no paid Cloudflare field is
needed.

## Classes

| Class | Meaning |
|---|---|
| `human-likely` | nothing suspicious found |
| `verified-crawler` | the user agent **claims to be** a known search engine or link-preview fetcher. Nothing is verified: no address-range or reverse-DNS check. |
| `automated` | declares itself a bot, runs headless, or sends no user agent |
| `suspicious` | looks like a browser but weak signals add up to the threshold |

Every result is "likely", not proof. A user agent is whatever the sender says.

## Reasons (closed list)

`ua-declared-bot`, `ua-headless`, `ua-missing`, `ua-too-long`, `no-lang`,
`no-sec-fetch`, `datacenter`, `tor`, `no-js`, `click-without-view`, `rate`,
`paid-score`, `verified-crawler-ua`, `none`. Exactly one is returned.

## Order

First match wins:

1. headless -> `automated` / `ua-headless`
2. declared automated token -> `automated` / `ua-declared-bot`
3. declared crawler token -> `verified-crawler` / `verified-crawler-ua`
4. missing or empty user agent -> `automated` / `ua-missing`
5. weak signals: add the weights; at or above the threshold of 2 the class is
   `suspicious`, and the reason is the heaviest present signal (ties go to the
   earlier entry in `WEAK_ORDER`)
6. otherwise `human-likely` / `none`

| Weak signal | Weight |
|---|---|
| `paid-score` (only when `usePaidFields` is true and score below 30), `rate`, `datacenter`, `ua-too-long` | 2 |
| `no-sec-fetch`, `no-lang`, `tor`, `click-without-view`, `no-js` | 1 |

So one light signal alone never makes a visitor suspicious (script blockers,
privacy tools and JS-off visitors are legitimate), two do, and a datacenter
address, a rate flag or an oversized user agent does by itself. A VPN or proxy
address is never a signal. `no-js` is last in `WEAK_ORDER`: it adds weight but
never leads the reported reason.

A missing or malformed field counts as unknown and adds nothing. The function
never throws.

## Paid fields

`paidBotScore` is ignored unless `usePaidFields` is `true`. Nothing in this
module reads Cloudflare request metadata; the caller decides what to pass.

## User agent data

`matchUaToken(ua)` returns `verified-crawler`, `automated`, `headless` or
`null`. It reads at most 512 characters, lowercases, and uses substring checks
only (no regular expression), so hostile input cannot cause backtracking.
Crawler tokens cite each operator's documentation in `botdata.ts`. Headless is
checked first, then named crawlers, then generic automation words.

## Plan 4 differences to resolve

The plan (A19) gives one weak signal the class `suspicious`; this module
requires a score of 2, which is a decision recorded here for the maintainer.
The plan's `crawler_spoof`, `se_verified`, `tls_old` and `hdr_combo` reasons
need range data and are not part of this module.
