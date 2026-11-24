<!-- SPDX-License-Identifier: Apache-2.0 -->
# Cost worksheet

A plain table version of the method on [Costs](Costs.md). Fill the input
column, work down the table, and add the dollar column. Prices were read on
2026-11-24 and can change. All amounts are US dollars. An estimate is an
estimate. Rows marked INFERRED rest on assumptions, not measurements.

A month is 30 days. An optional script does the same arithmetic:
`python3 -I scripts/cost_estimate.py --example small`.

## Inputs

| Name | Your value | Small | Medium | Busy |
|---|---|---|---|---|
| V, page views a month | | 20,000 | 300,000 | 10,000,000 |
| S, sites | | 1 | 3 | 5 |
| C, link clicks a month | | 3,000 | 45,000 | 1,500,000 |
| L, kit leads a month | | 50 | 600 | 20,000 |
| R, raw retention in days | | 7 | 7 | 7 |
| T, lead retention in days | | 30 | 30 | 30 |
| H, hourly human view budget per site | | 120 | 400 | 4,000 |
| A, hourly human view budget for the account | | 480 | 1,200 | 20,000 |

## Calculation

| Line | Formula | Your result | Small | Medium | Busy |
|---|---|---|---|---|---|
| Workers requests | V + C + 2 x L + 720 | | 23,820 | 346,920 | 11,540,720 |
| Workers CPU ms (INFERRED) | requests x 3 + 720 x 500 | | 431,460 | 1,400,760 | 34,982,160 |
| D1 rows written | V x 5.6 + C x 9 + L x 6 + 2,540 x 30 x S | | 215,500 | 2,317,200 | 70,001,000 |
| D1 rows written a day | rows written / 30 | | 7,183 | 77,240 | 2,333,366 |
| D1 rows read (INFERRED) | requests x 10 | | 238,200 | 3,469,200 | 115,407,200 |
| D1 storage GB (INFERRED) | see Costs, step 5 | | 0.011 | 0.047 | 0.599 |

## Dollars

| Line | Included | Price over included | Small | Medium | Busy |
|---|---|---|---|---|---|
| Workers Paid minimum | | $5 a month | $5.00 | $5.00 | $5.00 |
| Workers requests | 10 million | $0.30 per million | $0.00 | $0.00 | $0.46 |
| Workers CPU ms | 30 million | $0.02 per million | $0.00 | $0.00 | $0.10 |
| D1 rows written | 50 million | $1.00 per million | $0.00 | $0.00 | $20.00 |
| D1 rows read | 25 billion | $0.001 per million | $0.00 | $0.00 | $0.00 |
| D1 storage | 5 GB | $0.75 per GB-month | $0.00 | $0.00 | $0.00 |
| Turnstile | | free | $0.00 | $0.00 | $0.00 |
| Static hosting | | free | $0.00 | $0.00 | $0.00 |
| **Total a month** | | | **$5.00** | **$5.00** | **$25.56** |

## Checks

| Check | Pass when | Small | Medium | Busy |
|---|---|---|---|---|
| Hourly view budget | V / 720 is at most the smaller of S x H and A | 27.8 of 120 | 416.7 of 1,200 | 13,889 of 20,000 |
| Fits the Free plan | rows written a day at most 100,000; click rows written a day (C x 9 / 30) at most 7,500; requests a day at most 100,000 | Yes | No | No |
| Under default D1 daily quota | rows written a day at most 1,600,000 | Yes | Yes | No, raise `D1_DAILY_WRITE_QUOTA` |
| Under default click ceiling | click rows charged a day at most 300,000 | Yes | Yes | No, raise `CLICK_DAILY_WRITE_CEILING` |

## Not in the worksheet

Your own domain name, an email service for alerts, optional alert and
dashboard services, a secret manager, taxes, and the planned services
(Durable Objects, R2, Analytics Engine) whose use is not decided.
