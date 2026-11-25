<!-- SPDX-License-Identifier: Apache-2.0 -->
# Storage architecture

Reach Around is pre-release software. This page says where each kind of data
lives and why. Only one database is built today. Everything else here is a
design that has been decided but not built, and it is labeled **PLANNED**. A
claim that is not in the project's public design document is marked
**INFERRED**.

The storage design is a draft (issue #753). Nothing in it is deployed, so it
can still change. Where this page and the code disagree, the code is right
today.

Contents: [Summary](#summary) · [Built today](#what-is-built-today) ·
[Planned](#what-is-planned) · [Loss window](#the-loss-window) ·
[Why](#why-each-choice) · [Cost](#cost) · [Diagram](#diagram) ·
[Sources](#sources)

## Summary

| Store | Holds | Status |
|---|---|---|
| D1 (one database) | Raw click and view rows with hour-rounded times, kit events and leads, daily aggregates, budget counters, health rows | Built |
| KV `KIT` | The gated media kit page | Built, optional |
| KV `LIVE_KV` | The live status | Built, optional |
| Durable Object counter ledger | Hot counters, buffered before they reach D1 | **PLANNED** |
| D1 daily and monthly aggregates | Rollups of the raw rows | Daily rollups built; the planned changes are a new kit daily table and one schema convention |
| KV `PUB` | Pre-rendered public stats JSON and public kit JSON | **PLANNED** |
| Two D1 snapshot databases | Collector numbers, one public and one owner-authorized | **PLANNED** |
| D1 for leads, on its own | Media kit leads, away from analytics | **PLANNED** (separate issue) |
| R2 bucket | Large raw imports, with an expiry date | **PLANNED**, optional |
| Analytics Engine | Daily floored counters for a three-month trend | **PLANNED**, optional, off by default |

## What is built today

**One D1 database** holds almost everything the Worker stores:

- Raw rows for accepted page views and clicks. Times are rounded to the hour.
  No row holds an address, a user agent or a referrer URL.
- Kit events (unlock, view, verify) with the lead they belong to. Deleting the
  lead deletes its kit events.
- Daily aggregates, built by the hourly job from the raw rows once a day is
  closed. These are what the owner's report reads.
- Counters that enforce the budgets, and health and coverage rows.

Each accepted view is written in one atomic D1 batch, so a counter never exists
without its event row. The design document puts this at about 5.6 rows written
per view.

Raw rows are purged on a schedule: 7 days by default, held up to three times
that long if they are not yet rolled up, with a hard maximum of 90 days.

**KV** holds two optional things: the gated kit page and the live status. The
Worker never serves stored analytics data to the public today.

There is no read path from the Worker for stats. The owner runs fixed queries,
makes an export, and the local report reads it.

## What is planned

Everything in this section is **PLANNED**. None of it exists on main.

### Durable Object counter ledger

A Durable Object would buffer the hot counters (accepted views and clicks per
site and hour, and the budget state) so the Worker does not write a D1 row for
every counter bump. The public design document names the interfaces it needs
from this piece: a daily "first sighting today" check that forgets at the end
of the UTC day, an accepted-view and accepted-click count per site and hour,
and the budget state. Its internals are not in the public design, so how it
flushes, and how often, is **INFERRED** and may change.

### Hour-rounded raw events

Raw events stay in D1. This is a reversal: an earlier draft moved them to
Analytics Engine. They stay in D1 because Analytics Engine keeps exact
timestamps for a fixed three months with no early delete. That would break the
7-day promise and make erasure impossible for three months.

### Daily and monthly aggregates

The hourly job keeps rolling closed days into daily tables. A new small kit
daily table is planned: a count per site, day and kind, with no lead id and no
time finer than a day, filled before the raw kit rows are purged. The public
stats need 7 and 30 day kit totals that outlast the raw window. Small counts are
folded together so one lead cannot be picked out. The design also sets one schema
convention for every table and a reset of the migration history to one baseline
while nothing is live.

### KV pre-rendered stats

A new KV namespace, `PUB`, would hold public JSON: likely-human totals for 7
and 30 days, the top countries and sources, all floored, plus an "as of" time and
a coverage flag. The hourly job renders it from the D1 rollups. One read-only
route would serve it. The gated kit stays in its own namespace behind the
cookie, and the public route has no binding to it, so the wall is a binding
and not a filter.

### Two D1 snapshot databases

Collector snapshots (numbers pulled from platform APIs) would move to two D1
databases: one for public numbers and one for owner-authorized numbers. A
Worker binds only what it needs, so the public route cannot reach owner data.
Collectors stay in Python and write through an ingest route behind Cloudflare
Access, using a service token that can do nothing else. A write token for D1
itself would reach every database in the account (**INFERRED**).

### Leads in their own database

Leads would move to a separate D1 database so no credential used for analytics
can read them. This is its own issue and is not part of the storage design.

### R2 imports with expiry

A private R2 bucket would hold large raw files, such as platform exports,
because a D1 row is limited to 2 MB. D1 would keep only a manifest row for each
file. A lifecycle rule deletes each file when its platform's retention window
ends, and 90 days where the platform's terms are silent. R2 is optional, and
it is not confirmed whether enabling it needs a payment method (**INFERRED**).

### Analytics Engine, optional

If enabled, the hourly job writes a few daily, floored, closed-list counters
for a three-month trend. It is never written per event, never holds a visitor,
lead or kit metric, and it is off by default. Nothing in the product requires
it.

## The loss window

The hot counters would sit in a Durable Object for a short time before they are
written to D1. If that buffer is lost between flushes, the counts in the lost
window are gone for good. **INFERRED**: the public design does not state the
flush interval or the failure behavior, so treat the size of the window as
unknown until the code lands.

What the design does say limits the damage:

- The raw event row is written by D1 itself, in the same atomic batch as the
  budget counters today. The planned reconciliation compares the hourly
  accepted-view count against the raw rows the rollup counted. A gap sets a
  health flag and shows in the coverage report instead of reading as zero.
- A missing number is shown as missing, never as 0.
- Analytics Engine writes are best effort, with no confirmation. They are
  optional and are not an input to any rollup of record.

In plain words: a crash could lose a small slice of recent counts, and the
project is built to show that as a gap in coverage and not hide it.

## Why each choice

- **D1 for raw rows.** The 7-day purge and the lead delete cascade need a store
  that can delete early. Analytics Engine cannot.
- **A Durable Object for hot counters.** It cuts D1 writes per view. D1 write
  volume is the main cost lever.
- **Hour-rounded times.** An exact time can be matched to a lead's created
  time. An hour cannot.
- **Daily and monthly aggregates.** Long-lived history is kept as totals, not
  as events, so privacy rules do not depend on raw rows living longer.
- **KV for public stats.** A pre-rendered file is cheap to read, and the public
  route cannot touch D1 or the gated kit.
- **Two snapshot databases.** The wall between public and owner data is a
  Worker binding, not a `WHERE` clause that one mistake can drop.
- **Leads on their own.** A leaked analytics credential should not reach contact
  details.
- **R2 with expiry.** Large files do not fit in D1, and a lifecycle date makes
  deleting them automatic.
- **Analytics Engine as optional.** It is useful for a trend that outlasts the
  raw purge, but its fixed retention means only safe aggregates can go in.

## Cost

Storage choices drive the bill mostly through D1 rows written and Worker CPU.
The Workers Paid plan is the baseline, and the hourly job needs more CPU time
than the Free plan allows (**INFERRED** from the vendor limits in the design
document). Prices, free allowances and a worksheet are on
[Costs](Costs.md). Prices change, so check the sources there.

## Diagram

[Diagram 2: Data flow](diagrams/02-data-flow.md) draws the same stores with the
retention, rounding and floor points marked. See also the
[Architecture overview](Architecture.md).

## Sources

- The storage design draft, `docs/design/storage-architecture.md`, on the
  branch of draft PR #755 (issue #753). Not yet on main.
- `worker/wrangler.toml` on main: one D1 binding, optional KV, no R2 binding.
- [Costs](Costs.md).
