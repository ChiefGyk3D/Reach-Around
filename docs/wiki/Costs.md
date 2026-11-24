<!-- SPDX-License-Identifier: Apache-2.0 -->
# What Reach Around costs to run

Reach Around is pre-release software. Nothing on this page describes a hosted
service: you run your own copy on your own Cloudflare account, so the bills
below are yours. This page explains which Cloudflare services the project uses,
what they cost, how to estimate your own bill, and how to put a ceiling on it.

Prices were read from Cloudflare's own pages on 2026-11-24. They change. Every
number below carries its source, so check the source before you rely on it.
All amounts are US dollars. Every estimate here is an estimate.

Contents: [Services](#what-each-service-does-here) ·
[Plan limits](#the-limits-that-matter) · [Budgets](#the-projects-own-budgets) ·
[Worksheet](#the-worksheet) · [Examples](#three-worked-examples) ·
[Not included](#what-is-not-included) · [Controls](#cost-controls) ·
[Caveats](#caveats) · [Prices and sources](#published-prices-and-sources)

## What each service does here

Status words: **in the repo** means the code exists in the engine, though no
public deployment is claimed. **Optional** means the feature is off unless you
turn it on. **Planned** means a decision or design exists but the code does not,
so nothing is priced for it in the calculator.

| Service | Used for | Status |
|---|---|---|
| Workers | The small Worker that counts clicks and page views, serves the media kit gate, and runs the hourly purge and rollup on a cron trigger | In the repo |
| D1 | The database behind the Worker: raw click and view rows (short retention), daily aggregates, kit leads, counters that enforce the budgets | In the repo |
| Turnstile | The bot check on the media kit gate. Free | In the repo |
| Pages or static hosting | Serves the built static site. Static asset requests are free. Cloudflare also offers Workers static assets; the deploy guide covers the choice | In the repo |
| KV | The Live status feature (is the channel live) | Optional, code only, not deployed |
| Durable Objects | Buffering counters before they reach D1 | Planned |
| R2 | File storage, if the project ends up needing it | Planned, not confirmed |
| Analytics Engine | Daily floored counters | Planned, optional |

## The limits that matter

The plan is chosen per Cloudflare account. The project treats the **Paid** plan
as its baseline (the Workers Paid minimum is $5 a month for the account). The
Free plan is possible for a small site only with the lower click ceiling the
deploy guide documents for it.

| Limit | Free | Paid | Source |
|---|---|---|---|
| Workers requests | 100,000 a day, a hard stop | 10 million a month included, then $0.30 per million | Workers pricing |
| Workers CPU time per request | 10 ms | 30 seconds default, up to 5 minutes | Workers limits |
| Workers CPU time per cron run | 10 ms | 30 seconds default for an interval under one hour | Workers limits |
| Workers CPU, billed | not billed | 30 million CPU ms a month included, then $0.02 per million ms | Workers pricing |
| Cron triggers per account | 5 | 1,000 | Workers limits |
| D1 rows read | 5 million a day | 25 billion a month included, then $0.001 per million | D1 pricing |
| D1 rows written | 100,000 a day | 50 million a month included, then $1.00 per million | D1 pricing |
| D1 storage | 5 GB total | 5 GB included, then $0.75 per GB-month | D1 pricing |
| D1 queries per Worker run | 50 | 1,000 | D1 limits |
| D1 Time Travel retention | 7 days | 30 days | D1 limits |

On Free, the daily limits stop the service until the next day. The cron run
limit of 10 ms CPU is the one most likely to hurt a busy Free deployment, and
the project cannot measure it in tests, so the deploy guide tells you to watch
the logs after the first deploy.

## The project's own budgets

The Worker does not let usage run away on its own. Every limit below fails
closed: past it, the Worker stops counting and answers with an empty response.
You lose some analytics for the rest of the hour or day, and you do not get a
surprise bill. The defaults are in `worker/wrangler.toml` and explained in
`docs/deploy.md`.

| Setting | Default | What it bounds |
|---|---|---|
| `QUERY_BUDGET` | 40 (range 5 to 900) | D1 queries in one cron run. Free allows 50 and Paid 1,000; the defaults leave margin |
| `CLICK_DAILY_WRITE_CEILING` | 300,000 (range 1,000 to 10,000,000) | D1 rows the click route may be charged per UTC day, all sites together. A click is charged 9 to 13 rows. The Free pair is 7,500 |
| `VIEWS_PER_HOUR`, `BOT_VIEWS_PER_HOUR` | 120 and 30 per site | Accepted page views an hour. A view over the budget is dropped, not sampled |
| `ACCOUNT_VIEWS_PER_HOUR`, `ACCOUNT_BOT_VIEWS_PER_HOUR` | 480 and 120 | The same budgets across all your sites together |
| `RAW_KEEP_DAYS` | 7 (ceiling 90) | How long raw view rows are kept |
| `ROUTE_BUDGET_GATE`, `ROUTE_BUDGET_VIEW` | 150 and 300 per hour | Media kit gate requests and gated page views |
| `MAX_LEADS` | 5,000 | Stored kit leads |
| `LEAD_RETENTION_DAYS` | 30 (1 to 365) | How long a lead is kept |
| `D1_DAILY_WRITE_QUOTA` | 1,600,000 | The daily D1 write total the combined worst case must fit under. It is the Paid plan's 50 million a month over a 31 day month, rounded down |

**The D1 write model.** The engine adds up the worst-case D1 rows written in a
day from the kit, page views, clicks, the click funnel tables and a margin, and
refuses to run a configuration that goes over the quota. At the defaults that
total is 374,999 rows a day, or 23.4 percent of the default quota. A test in
the repo pins the table in `docs/deploy.md` to the Worker's arithmetic. The
per-operation write counts the calculator uses come from the same model: 5.6
rows per accepted page view, 9 rows per click (the lowest charge, for a site
with no optional lists), 6 rows per kit gate submission (5 plus one claim row),
and 2,540 rows per site per day for the rollup, purge and heartbeat.

One consequence: at the defaults the daily quota times 31 days is just under
the 50 million rows a month that Paid includes, so a deployment left at the
defaults cannot be billed for D1 writes. Raising a budget raises the worst
case. If you raise the budgets you also raise the quota variable, and that is
the moment you accept a bill that may be larger.

## The worksheet

Inputs, per month unless noted:

| Input | Meaning |
|---|---|
| V | Page views a month, all sites |
| S | Number of sites the Worker serves |
| C | Link clicks a month |
| L | Media kit leads (gate submissions) a month |
| R | Raw retention in days (`RAW_KEEP_DAYS`, default 7) |
| T | Lead retention in days (default 30) |
| H, A | Hourly human view budget per site, and the account ceiling |

Steps, using a 30 day month:

1. Workers requests = V + C + 2 x L + 720. The 720 is the hourly cron, 24 runs a day. Static pages are not counted because static asset requests are free. The 2 per lead is the gate request and the gated page view (INFERRED).
2. Workers CPU ms = requests x 3 + 720 x 500. The 3 ms a request and 500 ms a cron run are assumptions (INFERRED, not measured). Measure your own with the Worker logs and replace them.
3. D1 rows written = V x 5.6 + C x 9 + L x 6 + 2,540 x 30 x S.
4. D1 rows read = requests x 10 (INFERRED). Reads are never close to the included amount in the examples.
5. D1 storage in bytes = (V + C) / 30 x R x 200 + L / 30 x T x 600 + 1,125 x S x 90 x 100. The 600 bytes a lead is the repo's constant. The 200 bytes a raw row is INFERRED. The last term is the worst case for 90 days of daily aggregates, at an INFERRED 100 bytes a row.
6. Cost = $5 (Workers Paid minimum) + (requests over 10 million) x $0.30 per million + (CPU ms over 30 million) x $0.02 per million + (rows written over 50 million) x $1.00 per million + (rows read over 25 billion) x $0.001 per million + (storage over 5 GB) x $0.75 per GB.
7. Check the budgets. Average views an hour is V / 720. It should be under the smaller of S x H and A. If it is not, the Worker will drop views at busy hours and your numbers will under-count.
8. Check the Free plan. A deployment fits Free only if rows written a day are at most 100,000, click rows written a day are at most 7,500, and requests a day are at most 100,000. Even then, the Free 10 ms CPU limit per cron run is unmeasured.

A plain-table version is in [Costs-Worksheet](Costs-Worksheet.md). An optional
calculator does the same arithmetic offline:

```
python3 -I scripts/cost_estimate.py --example medium
python3 -I scripts/cost_estimate.py --views 50000 --sites 2 --clicks 6000 --leads 100
```

It uses only the Python standard library and opens no network connection. The
issue for a fuller calculator is tracked as #808.

## Three worked examples

The examples are invented, round numbers, not measurements of any real site.
Clicks are 15 percent of views, a placeholder you should replace with your own
measured rate.

### A small creator

Inputs: V = 20,000, S = 1, C = 3,000, L = 50, R = 7, T = 30, H = 120, A = 480.

1. Requests = 20,000 + 3,000 + 50 x 2 + 720 = 23,820. Included: 10,000,000. Over: 0.
2. CPU ms = 23,820 x 3 + 720 x 500 = 71,460 + 360,000 = 431,460. Included: 30,000,000. Over: 0.
3. Rows written = 20,000 x 5.6 + 3,000 x 9 + 50 x 6 + 2,540 x 30 x 1 = 112,000 + 27,000 + 300 + 76,200 = 215,500. A day is 215,500 / 30 = 7,183. Included: 50,000,000. Over: 0.
4. Rows read = 23,820 x 10 = 238,200. Over: 0.
5. Storage about 0.011 GB (raw 1.1 MB, leads 0.03 MB, aggregates 10.1 MB). Over: 0.
6. Cost = $5.00 + $0 + $0 + $0 + $0 + $0 = **$5.00 a month**.
7. Average views an hour is 27.8, under the budget of 120. Fine.
8. Plan: this could fit the Free plan, because 7,183 rows written a day is under 100,000 and click rows written a day is 900, under 7,500. Free also has the unmeasured 10 ms CPU limit on the cron run. Paid at $5 is the supported baseline.

### A medium creator

Inputs: V = 300,000, S = 3, C = 45,000, L = 600, R = 7, T = 30, H = 400, A = 1,200. At the default budgets (120 an hour a site, 480 for the account) the average of 417 views an hour would exceed the 360 an hour that three sites allow, so the budgets are raised here.

1. Requests = 300,000 + 45,000 + 600 x 2 + 720 = 346,920. Over: 0.
2. CPU ms = 346,920 x 3 + 360,000 = 1,040,760 + 360,000 = 1,400,760. Over: 0.
3. Rows written = 300,000 x 5.6 + 45,000 x 9 + 600 x 6 + 2,540 x 30 x 3 = 1,680,000 + 405,000 + 3,600 + 228,600 = 2,317,200. A day is 77,240. Over the included 50,000,000: 0.
4. Rows read = 3,469,200. Over: 0.
5. Storage about 0.047 GB. Over: 0.
6. Cost = **$5.00 a month**.
7. Average views an hour is 416.7, under the budget of min(3 x 400, 1,200) = 1,200. Fine.
8. Plan: **Paid**. Click rows written a day are 45,000 x 9 / 30 = 13,500, over the Free pair's 7,500.

### A busy creator

Inputs: V = 10,000,000, S = 5, C = 1,500,000, L = 20,000, R = 7, T = 30, H = 4,000, A = 20,000.

1. Requests = 10,000,000 + 1,500,000 + 20,000 x 2 + 720 = 11,540,720. Over 10,000,000 by 1,540,720, which is 1.54072 million x $0.30 = $0.46.
2. CPU ms = 11,540,720 x 3 + 360,000 = 34,622,160 + 360,000 = 34,982,160. Over 30,000,000 by 4,982,160, which is 4.98216 million x $0.02 = $0.10.
3. Rows written = 10,000,000 x 5.6 + 1,500,000 x 9 + 20,000 x 6 + 2,540 x 30 x 5 = 56,000,000 + 13,500,000 + 120,000 + 381,000 = 70,001,000. Over 50,000,000 by 20,001,000, which is 20.001 million x $1.00 = $20.00.
4. Rows read = 115,407,200. Over: 0.
5. Storage about 0.599 GB. Over: 0.
6. Cost = $5.00 + $0.46 + $0.10 + $20.00 = **$25.56 a month**.
7. Average views an hour is 13,889, under the budget of min(5 x 4,000, 20,000) = 20,000. Fine.
8. Plan: **Paid**, with two settings that must be raised from the defaults. Rows written a day are 2,333,366, over the default `D1_DAILY_WRITE_QUOTA` of 1,600,000. Click rows charged a day are 1,500,000 x 9 / 30 = 450,000, over the default `CLICK_DAILY_WRITE_CEILING` of 300,000. Raising them is a choice to accept this bill. The engine's deploy check totals the worst case for the settings you give it, and you should run it after changing them. This page did not run it for the busy example, so whether those exact settings pass is unverified.

| | Small | Medium | Busy |
|---|---|---|---|
| Workers floor | $5.00 | $5.00 | $5.00 |
| Workers requests over | $0.00 | $0.00 | $0.46 |
| Workers CPU over | $0.00 | $0.00 | $0.10 |
| D1 rows written over | $0.00 | $0.00 | $20.00 |
| D1 rows read and storage over | $0.00 | $0.00 | $0.00 |
| **Total a month** | **$5.00** | **$5.00** | **$25.56** |

The test `tests/test_cost_estimate.py` runs the script against these three
examples and checks the same figures.

## What is not included

The numbers above cover Cloudflare usage only. You also pay for, or provide:

- **Your own domain name.** The registration and renewal fee depends on the registrar and the name.
- **An email service.** The media kit alerts go to your own email or chat channel. The engine sends no marketing mail to leads, and a mail service for alerts is your own account and your own cost.
- **Optional alert services**, such as a push notification service or a chat bot, if you wire the alerts to one.
- **Optional dashboards**, such as a Grafana instance, if you want the example dashboards.
- **Secret storage and CI.** A secret manager and GitHub Actions minutes are separate accounts. Public repositories get free Actions minutes under GitHub's own terms; check them.
- **Your time.** The estimate does not price setup or upkeep.
- **Taxes and currency conversion.**

## Cost controls

Written for the person who owns the Cloudflare account.

1. **Start with the budgets.** The Worker's own budgets (the table above) are the first line. At the defaults a deployment cannot spend more D1 writes than the Paid plan includes. Leave them alone until you have measured real traffic.
2. **Set a Workers spending limit.** Cloudflare lets you set a spending limit for Workers and for Durable Objects in the dashboard under Billing, Spend limits, with email alerts at 80 and 100 percent of the limit. When the limit takes effect, new Worker invocations are blocked, which stops the click and view counters. The limit counts requests and CPU time only. It does not count the $5 subscription or D1. It is not an exact cap: usage reporting takes time and recorded spend can pass the limit. Source: Cloudflare spend management pages, read 2026-11-24.
3. **Set a budget alert for everything else.** Budget alerts email you when account-wide usage-based spend crosses a dollar amount, and they do not stop anything. Cloudflare reports usage once a day, so an alert arrives the day after the threshold is crossed. Because D1 is not covered by the spending limit, a budget alert is your notice for D1 overage.
4. **Know the kill switches.** The Worker has switches you can flip without a redeploy of the site: `CLICKS_KILL`, `VIEWS_KILL`, `SCHEDULED_KILL`, and per-site lists. Setting one returns the usual empty response and skips the database. If a bill alert fires, turning the counters off stops D1 writes from them at once. `docs/deploy.md` and `docs/failure-modes.md` list them.
5. **Fail closed is the design.** When a budget is hit, the engine accepts an outage of the counter over a larger bill. A viral hour can make the gate unavailable until the next hour. That is the accepted trade.
6. **Check the bill early.** Open the billable usage page in the dashboard after the first week and compare it with this page. If your rows written a day are far from the estimate, replace the INFERRED numbers with yours.
7. **Cloudflare's Free plan has no bill but hard stops.** If you stay on Free, you pay nothing and the service stops at the daily limits instead.

## Caveats

- Prices change. Re-read the sources before relying on a number. The Cloudflare page for Durable Objects says SQLite storage billing would start in January 2026, which is a stale sentence on a page last modified in November 2026, so treat that line as unconfirmed.
- Workers Analytics Engine: Cloudflare's page says "Currently, you will not be billed for your use of Workers Analytics Engine." That page was last modified on 2026-04-23, older than the others, so check it.
- The project is pre-release. Its write counts and budgets can change between releases, and the calculator will change with them.
- Planned services (Durable Objects, R2, Analytics Engine) are not in the calculator. Their prices are listed below so you can see the order of magnitude, but how much the project will use is not decided.
- The inputs marked INFERRED are assumptions. The 15 percent click rate, 3 ms of CPU a request, 500 ms of CPU a cron run, 10 rows read a request, 200 bytes a raw row and 100 bytes an aggregate row are not measurements.
- A month is taken as 30 days. Cloudflare bills by calendar month.
- Whether the hourly cron invocation is counted as a request for billing is not stated on the pages read. The estimate counts it, which can only raise the figure.
- Estimates are estimates. Your invoice is the record.

## Published prices and sources

Each page was read on 2026-11-24. The date in parentheses is the page's own
last-modified date as reported by the page. All are VERIFIED reads.

| Service | Free | Paid | Source |
|---|---|---|---|
| Workers | 100,000 requests a day; 10 ms CPU per run | $5 a month minimum per account; 10 million requests included, then $0.30 per million; 30 million CPU ms included, then $0.02 per million | developers.cloudflare.com/workers/platform/pricing/ (2026-11-18) |
| Workers limits | 10 ms CPU per request and per cron run; 5 cron triggers; 50 subrequests | CPU up to 5 minutes, 30 seconds default; 1,000 cron triggers; 10,000 subrequests | developers.cloudflare.com/workers/platform/limits/ (2026-11-19) |
| D1 | 5 million rows read a day; 100,000 rows written a day; 5 GB | 25 billion rows read a month, then $0.001 per million; 50 million rows written, then $1.00 per million; 5 GB, then $0.75 per GB-month | developers.cloudflare.com/d1/platform/pricing/ (2026-10-30) |
| D1 limits | 50 queries per Worker run; 500 MB database; 7 days Time Travel | 1,000 queries; 10 GB database; 30 days Time Travel | developers.cloudflare.com/d1/platform/limits/ (2026-11-20) |
| Durable Objects (planned) | 100,000 requests a day; 13,000 GB-s a day; SQLite 5 million rows read and 100,000 written a day; 5 GB | 1 million requests, then $0.15 per million; 400,000 GB-s, then $12.50 per million GB-s; SQLite rows as for D1; 5 GB-month, then $0.20 per GB-month | developers.cloudflare.com/durable-objects/platform/pricing/ (2026-11-13) |
| KV (optional) | 100,000 reads, 1,000 writes, deletes and lists a day; 1 GB | 10 million reads, then $0.50 per million; 1 million writes, deletes and lists, then $5.00 per million; 1 GB, then $0.50 per GB-month | developers.cloudflare.com/kv/platform/pricing/ (2026-11-24) |
| R2 (planned, unconfirmed) | none listed separately | $0.015 per GB-month, 10 GB-month free; Class A $4.50 per million, 1 million free; Class B $0.36 per million, 10 million free; egress free | developers.cloudflare.com/r2/pricing/ (2026-10-30) |
| Analytics Engine (planned, optional) | 100,000 data points and 10,000 queries a day | 10 million data points, then $0.25 per million; 1 million queries, then $1.00 per million; page says not currently billed | developers.cloudflare.com/analytics/analytics-engine/pricing/ (2026-04-23) |
| Pages static hosting | static asset requests free and unlimited | the same; Functions bill as Workers requests | developers.cloudflare.com/pages/functions/pricing/ (2026-09-08) |
| Turnstile | up to 20 widgets, unlimited verifications, 10 hostnames a widget, 7 days of analytics | Enterprise only for more; contact sales | developers.cloudflare.com/turnstile/plans/ (2026-08-14) |
| Spending limits and budget alerts | | Workers and Durable Objects limits and email alerts; account-wide budget alerts | developers.cloudflare.com/billing/manage/spend-management/ and /billing/manage/budget-alerts/ (read 2026-11-24) |

Not read: Cloudflare Access pricing, which a planned ingest step may use. No
number is given for it.
