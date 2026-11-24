<!-- SPDX-License-Identifier: Apache-2.0 -->
# Storage architecture: events, rollups, published JSON, snapshots

> **Status: design draft, no code.** Written for issue #753 (epic #365) on 2026-11-24.
> It covers items 2 to 5 of that issue. Item 1 (hot counters) and the hourly budget and
> limiter state are a separate design and are out of scope here; this page only names the
> interfaces it needs from them (section 7).
>
> **Marking.** Every claim about Cloudflare is marked **VERIFIED** (the vendor page was read,
> URL and read date `2026-11-24` given, the page's own "last updated" date shown when the page
> has one) or **INFERRED** (reasoned, not read or not tested). Nothing was tried against a live
> account. Every INFERRED item is collected in section 9. Prices change; the owner re-checks the
> pricing pages before approving any billable step.
>
> **Placeholders.** `example.com`, `links.example.com`. No real host, account, database or
> namespace id appears here.

## 0. Summary and recommendation

Nothing is in production, so the storage shape can change now. The recommendation:

1. **Raw events leave D1.** Views, clicks and kit events are written as Analytics Engine
   data points. D1 keeps only rollups, written by the hourly job from an Analytics Engine
   query. The raw tables `view_events`, `click_events` and `kit_events` are dropped.
2. **Views are written as separate marginal data points** (a core point, one point per
   dimension, one geography point), so no joint row of city, browser, system and language
   exists in a store we cannot delete from (Analytics Engine keeps data three months and
   stamps its own exact time).
3. **Public stats and the public kit JSON are pre-rendered by the hourly job into a
   separate KV namespace** that holds nothing gated, and read through one new GET route.
4. **Collector snapshots move to two D1 databases**, one public and one owner-authorized, so
   the wall is a Worker binding and not a `WHERE` clause. Collectors stay in Python and write
   through an ingest route. R2 holds large raw imports; D1 holds their manifest.
5. **One schema convention** (STRICT, integer counts named `n`, WITHOUT ROWID on composite
   keys, one key shape, one rebuild pattern) applied by **resetting the Worker migrations to
   one baseline** while nothing is live, enforced by a migration lint in CI.

What needs the Workers Paid plan, exactly (section 8): the hourly job's CPU time (Free allows
10 ms per cron run, **VERIFIED**), and D1 writes at the configured click ceiling (already
refused on Free by `docs/deploy.md`). Analytics Engine, KV and R2 each have a Free allowance
that would cover this design's volumes; billing and any account change are the owner's step.

## 1. What exists today

| Piece | State (from `worker/src`, `worker/migrations`, `src/reach_around/store`) |
|---|---|
| Views | One atomic D1 batch per accepted view: budget counters, one `view_events` row (two indexes), a `visitor_seen` row for a first human view. About 5.6 rows written per view (`docs/deploy.md`) |
| Clicks | `click_events` row plus counter upserts into `click_src_daily`, `click_country_daily`, `click_landing_daily`, `click_hop_daily` |
| Kit events | `kit_events` row per unlock, view, verify, with `lead_id` (foreign key, cascades on lead delete), capped by `MAX_KIT_EVENTS` |
| Rollup | Hourly cron: lead purge, health, click purge, view purge, `rollupClosedDays` (raw rows to `view_daily`, `view_dim_daily`, `view_geo_daily`, `view_hour_daily`), click funnel seal and monthly roll. Sealing uses raw row ids (`max_id`) |
| Read path | None on the Worker. The owner runs fixed `SELECT`s, makes `export.json`, and `reach-around report` reads it (`docs/report.md`). The one stored-data route is `GET /e/live` |
| Kit KV | The `KIT` binding holds the gated page at `kit/v1`, served only with the unlock cookie |
| Snapshot store | Local SQLite (`src/reach_around/store/migrations/0001_core.sql`): `observations`, `runs`, `kv`, plus `click_daily`, `click_cursor`, `click_batches`, `click_dead_letters` (a pull of raw clicks from D1) |
| Migrations | Worker: 13 files, rebuild by copy and rename in 0008 and 0016. Store: checksummed runner, one transaction per file, `VACUUM INTO` backup |

Schema inconsistencies found while reading (the input to item 5):

- Only `0009`, `0013`, `0014` are `STRICT`. `0005` to `0008`, `0010`, `0011`, `0015` to `0017`
  are not, so text can land in a count column.
- `view_meta` stores counters as `TEXT` and the SQL casts them back (`CAST(value AS INTEGER)`).
- Count column names differ: `views`, `clicks`, `count`, `folded`, `value`, `uniques`.
  Lower bounds differ: `views >= 1`, `views >= 0`, `count >= 0`.
- Key shapes differ: `(site, day, day_tz, ...)` in the view tables, `(site, day, ...)` in
  the funnel, `(key, day)` in `counters`, `(day, day_tz, ...)` with no site in the store's
  `click_daily`. `day_tz` is always `'UTC'` in the Worker. The `site` CHECK has three
  variants (first-character rule or not; length 20 or 64).
- Composite keys on rowid tables: every store table (`observations` has a seven-column key),
  which keeps a second copy of the key in an implicit index.
- Rebuild migrations (0008, 0016) copy and rename with no row-count check and rely on a
  comment saying the file is atomic. The D1 migrations page does not say so (section 5.5).

## 2. Cloudflare facts used (read 2026-11-24)

| Fact | Value | Source, page date |
|---|---|---|
| Analytics Engine, write limits | 20 blobs, 20 doubles, 1 index per point; blobs total at most 16 KB per point; index at most 96 bytes; at most 250 points per invocation | VERIFIED, developers.cloudflare.com/analytics/analytics-engine/limits/ (2026-04-23) |
| Analytics Engine, retention | Stored for three months | VERIFIED, same page |
| Analytics Engine, pricing | Free: 100,000 points per day, 10,000 read queries per day. Paid: 10 million points per month, then $0.25 per million; 1 million queries per month, then $1.00 per million. The page says usage is not billed yet and billing starts "in the coming months" | VERIFIED, .../analytics-engine/pricing/ (2026-04-23) |
| Analytics Engine, write call | `writeDataPoint()` needs no `await`, returns at once, writes in the background; no confirmation to the Worker; the runtime adds a `timestamp`; the single index is the sampling key; high volume may be sampled and queries use `_sample_interval` | VERIFIED, .../get-started/ (2026-04-23) and .../sql-api/ (2026-10-02) |
| Analytics Engine, read | REST `POST /accounts/<id>/analytics_engine/sql` with an API token holding Account Analytics Read. The page does not mention a Worker binding for queries | VERIFIED, .../sql-api/ (2026-10-02) |
| KV limits | Free: 100,000 reads per day, 1,000 writes per day, 1 GB. Paid: no read or write cap. 1 write per second per key on both. Value at most 25 MiB, key at most 512 bytes | VERIFIED, developers.cloudflare.com/kv/platform/limits/ (2026-10-30) |
| KV pricing | Paid: 10 million reads per month then $0.50 per million; 1 million writes per month then $5.00 per million; 1 GB then $0.50 per GB-month | VERIFIED, .../kv/platform/pricing/ (2026-11-24) |
| KV consistency | Eventually consistent; other locations may take 60 seconds or more; default `cacheTtl` 60 s | VERIFIED, .../kv/concepts/how-kv-works/ (2026-04-21) |
| D1 limits | Free: 500 MB per database, 10 databases, 5 GB per account, 50 queries per invocation. Paid: 10 GB per database, 1,000 queries per invocation. Both: row or value at most 2 MB, statement at most 100 KB, 100 bound parameters. A `batch()` counts as one query toward the invocation limit | VERIFIED, .../d1/platform/limits/ (2026-11-20) |
| D1 pricing | Free: 5 million rows read and 100,000 rows written per day. Paid: 25 billion read and 50 million written per month included, then $0.001 per million read and $1.00 per million written; storage 5 GB then $0.75 per GB-month. An indexed write also writes one index row | VERIFIED, .../d1/platform/pricing/ (2026-10-30) |
| D1 Time Travel | Always on; 7 days retention on Free, 30 days on Paid | VERIFIED, .../d1/reference/time-travel/ (2026-11-20) |
| D1 migrations | Applied files are recorded in `d1_migrations`; the page says nothing about atomicity or rollback on failure, and advises `PRAGMA defer_foreign_keys = true` for key violations | VERIFIED (absence), .../d1/reference/migrations/ (2026-06-08) |
| D1 export | Blocks other requests while it runs; no virtual tables; page says nothing about `STRICT` | VERIFIED, .../d1/best-practices/import-export-data/ (2026-11-20) |
| Workers limits | Free: 10 ms CPU per request and per cron run, 5 cron triggers, 50 subrequests, 100,000 requests per day. Paid: 30 s default CPU for crons under one hour apart, up to 15 min for hourly or longer, 1,000 cron triggers, 10,000 subrequests | VERIFIED, .../workers/platform/limits/ (2026-11-19) |
| R2 free tier | 10 GB-month, 1 million Class A, 10 million Class B operations per month; egress free; Standard: $0.015 per GB-month, Class A $4.50 per million, Class B $0.36 per million. The page does not say whether a payment method is needed | VERIFIED, .../r2/pricing/ (2026-10-30) |
| R2 limits and lifecycle | Object up to 5 TiB; 1 write per second per key; lifecycle rules delete objects after N days (removal within about 24 hours of expiry), up to 1,000 rules per bucket | VERIFIED, .../r2/platform/limits/ (2026-10-30), .../r2/buckets/object-lifecycles/ (2026-10-26) |

## 3. Item 2: raw events to Analytics Engine, D1 keeps rollups only

### 3.1 Current state

Section 1. Per accepted view the Worker writes about 5.6 D1 rows, of which the raw event row
is roughly three (table plus two indexes) and its later purge delete is more (INFERRED: the
D1 pricing page names the index row; the repository figure 5.6 is `docs/deploy.md`'s). Raw
rows are kept 7 days by default, ceiling 90; timestamps are rounded to the hour; no row ever
holds an address, user agent or referrer URL.

### 3.2 Target

Datasets (one `[[analytics_engine_datasets]]` binding each; datasets are created by the first
write, **VERIFIED**):

| Dataset | One point per | Blobs (fixed order, versioned in a TypeScript constant and a test) | Doubles | Index |
|---|---|---|---|---|
| `ra_view_core` | accepted view | `page`, `src_kind`, `src`, `country`, `bot_class` | `first` (0 or 1) | `site` |
| `ra_view_dim` | accepted view and dimension (browser, os, device, lang, vp, reason) | `dim`, `value`, `bot_class` | none | `site` |
| `ra_view_geo` | accepted view | `country`, `region`, `city`, `bot_class` | none | `site` |
| `ra_click` | accepted click | `link_id`, `destination_kind`, `src_or_country_label`, `landing`, `hop` | none | `site` |
| `ra_kit` | kit event | `kind` | none | `site` |

Rules:

- **No joint row.** The existing marginal split (`view_daily`, `view_dim_daily`,
  `view_geo_daily`) exists so a day's browsers, systems, languages and cities can never be
  recombined into a visitor profile. Analytics Engine data cannot be deleted early and
  carries an exact runtime timestamp the Worker cannot round (INFERRED: the page lists
  `timestamp` as automatic and documents no override). A single wide point would hold city,
  browser, system and language together, exactly, for three months. One point per marginal
  keeps the existing guarantee. The residual risk is that points written in the same request
  carry near-identical timestamps (section 3.8).
- **No `lead_id`, no address, no hash.** Kit events carry only `kind` and `site`. The daily
  visitor hash never enters Analytics Engine: `first` is computed at write time against the
  daily dedupe store (section 7) and only the bit is written. `visitor_seen` therefore
  keeps its end-of-UTC-day deletion.
- **Index is `site`**, so a quiet site's points are not sampled away by a busy one
  (INFERRED from "the index is the sampling key", **VERIFIED**).
- Writes are not awaited and use at most 8 points per view (1 core, 6 dimension, 1 geo; `net`
  stays out while it is always `unknown`), far under 250 per invocation.
- All values pass the same closed-vocabulary normalizers the D1 insert uses today; the schema
  guard test in `worker/test/views/schema.test.ts` is extended to the blob lists.

D1 after the change keeps: `view_daily`, `view_dim_daily`, `view_geo_daily`,
`view_hour_daily`, `view_rollup_log`, the click funnel tables, `kit_daily` (new, below),
`analytics_coverage`, `leads` and the item 1 tables. It drops `view_events`, `click_events`,
`kit_events`.

### 3.3 Data flow

```text
visit -> Worker (checks, normalize, closed values)
           |-- writeDataPoint x N  (Analytics Engine, background, no ack)
           |-- dedupe check for `first` (section 7)
hourly cron -> for each site, each closed UTC day not yet sealed:
           SQL query per dataset over the AE REST API (GROUP BY the key columns,
           SUM(_sample_interval) for counts, SUM(first) for uniques)
           -> fold small cells (MIN_CELL, TOP_SOURCES, ROLLUP_MAX_ROWS_PER_DAY, unchanged logic)
           -> one D1 batch: replace (site, day) rows in the rollup tables, upsert view_rollup_log
```

Replace, not add: the job writes `views = excluded.views` for a `(site, day)` it recomputes
from scratch. That makes a re-roll idempotent and removes the `max_id` mechanism. A day is
re-rolled on every run until `ROLLUP_GRACE_HOURS` plus a seal window (default 48 hours, an
open decision) has passed, then sealed. A point arriving after the seal is not counted; the
reconciliation in 3.6 shows it.

### 3.4 Cloudflare limits and costs

- Points per month at the repository's configured ceilings (`docs/deploy.md`): views at 600 per
  hour (480 human plus 120 bot) is at most 14,400 views a day, times 8 points, about 3.5
  million a month; clicks at `CLICK_DAILY_WRITE_CEILING` 300,000 a day is about 9 million a
  month. Together about 12.5 million at the ceilings, so about 2.5 million over the Paid
  allowance, roughly $0.63 a month at $0.25 per million (INFERRED arithmetic on VERIFIED
  prices). Real traffic is far below the ceilings.
- Queries: about 4 per site-day per run times a few open days times 24 runs, a few thousand a
  month, far under 1 million (Paid) and about 10,000 a day (Free).
- Free fit: at real traffic the points fit the Free 100,000 a day. At the ceilings they do
  not (clicks alone are 300,000). **Paid is needed only for the configured ceilings and for
  CPU** (section 8).
- The page says Analytics Engine is not billed yet. Treat the cost lines as a forecast.
- D1 writes saved: the raw row and its index rows, plus the purge deletes, per event. The
  remaining D1 writes per view belong to item 1.
- A read token is required: API token, Account Analytics Read, stored as a Worker secret
  `AE_READ_TOKEN`. It reads every dataset of the account (INFERRED: the permission pages show
  no narrower scope). Owner step; rotated a/b pair in the secret manager, dev first.

### 3.5 Migration plan with rollback

1. Add the bindings and the writer behind `EVENTS_SINK = "d1"` (default), `"both"`, `"ae"`.
   Today's behavior is `d1`.
2. `both` on the test deployment: every event goes to D1 raw and to Analytics Engine. The
   rollup still reads D1. A shadow job also rolls from Analytics Engine into scratch tables
   `*_shadow` and a comparison reports per day and table the difference.
3. When the shadow matches (section 3.7 thresholds) for 7 closed days, switch the rollup to
   Analytics Engine (`ROLLUP_SOURCE = "ae"`), keep `both`.
4. After 7 more days, set `EVENTS_SINK = "ae"` and ship the migration that drops the raw tables
   and their indexes.

Rollback: steps 1 to 3 flip a variable (`npx wrangler deploy --var ...`, the existing pattern
for kill switches) and lose nothing. After step 4 the raw tables are gone, so rollback is
forward: re-create them from the baseline file and set `EVENTS_SINK = "both"`; events written
while on `ae` exist only in Analytics Engine and are rolled from there (rolled days are
unaffected). Take a D1 export and a Time Travel bookmark before step 4 (D1 export blocks
queries while it runs; do it in a quiet hour with the maintenance switch, **VERIFIED** page
text).

### 3.6 Privacy impact

| Rule | Before | After |
|---|---|---|
| No raw IP, user agent, referrer URL, ASN | Not stored | Unchanged. Points carry closed values only |
| Daily hashed visitor, deleted at end of UTC day | `visitor_seen` | Unchanged; the hash never leaves it |
| Raw retention | 7 days default, ceiling 90 | **Fixed three months** (VERIFIED) for event points; `RAW_KEEP_DAYS` stops applying to events. Within the existing 90-day hard ceiling, but longer than the 7-day default |
| Raw timestamp | Rounded to the hour | **Exact**, set by the runtime (INFERRED) |
| Joint rows (city with browser with language) | Existing for up to 7 days | **None**, by the split in 3.2 |
| `lead_id` beside a kit event | Present, cascade delete | **Gone**; kit points carry no lead. Lead deletion has no event rows to cascade, and `leadretention.ts` loses a query |
| Deletion on request | Not possible by design; short raw window | Same statement, longer window; privacy text changes |
| Backups | Time Travel keeps raw rows 7 or 30 days | Raw rows are not in D1, so Time Travel no longer holds them (an improvement) |

The privacy page, `docs/privacy.md` and the threat model entries T-12 and T-17 change in the
code PR (see the `analytics-design.md` rule that the privacy text moves with the code).

### 3.7 Test strategy

- Writer unit tests with a fake dataset binding: exact blob order and count, no field outside
  the closed lists, at most 8 points per view, a sentinel test that no address, user agent,
  referrer URL or hash value reaches any point (extends `dump_no_raw_ip.test.ts`).
- Blob-order contract test: the TypeScript constant and the rollup SQL's column list are
  generated from one table, and a test fails when they drift.
- Rollup tests with a fake AE query client returning fixed rows, including `_sample_interval`
  greater than 1, empty days, a late point after the seal, and a site sealed alone (the
  per-site seal tests of `analytics-design.md` section 3.6 carry over).
- Parity fixtures: the same synthetic events through the D1 path and the AE path give equal
  rollups. Thresholds for the shadow period: counts equal for humans, within 2 percent for
  bots (best-effort writes, INFERRED), zero unexplained days.
- Reconciliation: the hourly accepted-view counter (item 1) against the points counted by the
  rollup, stored in `view_rollup_log.raw_views` and shown as coverage; a gap beyond a
  threshold sets a health flag in `analytics_coverage` terms.
- Migration test: apply the drop migration to a seeded database, assert the raw tables are
  gone and the rollups are byte-identical.

### 3.8 Risks

| Risk | Mitigation |
|---|---|
| Points are best-effort and unacknowledged (INFERRED from "no confirmation"); a lost point is an undercount | Reconciliation against the accepted-view counter; report says "may undercount"; the old atomic guarantee ("no counter without an event") is relaxed and the doc says so |
| Sampling at high volume | `_sample_interval` in every query; `view_rollup_log.sampled` flag; threshold is undocumented on the pages read (INFERRED), measure in the shadow period |
| Timestamp correlation across the points of one view | Residual. An attacker needs the Account Analytics Read token. Option C in question 2 removes it |
| REST query token is account-wide read | Secret in the secret manager, rotated pair, never in argv or logs, fetch only from the cron |
| Analytics Engine outage or API change stops the rollup | Rollup fails closed, retries next hour, health alert; D1 raw stays available in `both` mode |
| 3-month retention cannot be shortened | Question 2; do not write a field you would not keep three months |
| 15 min CPU is only on Paid | Section 8 |

## 4. Item 3: pre-rendered public stats and kit JSON in KV

### 4.1 Current state

The Worker never returns stored data except `GET /e/live`, and `analytics-design.md` lists "a
public stats endpoint" as out of scope. The only KV in use is `LIVE_KV` (live state) and
`KIT` (the gated page at `kit/v1`, behind the unlock cookie). The public site gets its numbers
at build time from the numbers file and the kit build reads `public_projection`
(`docs/kit.md`, "The wall between the two").

### 4.2 Target

A new KV namespace `PUB`, separate from `KIT` and `LIVE_KV`, holding only values safe for
anyone:

| Key | Content |
|---|---|
| `v1/stats/<site>.json` | Likely-human totals over fixed windows (7 and 30 days): views, clicks, kit views; top countries and top source labels, floored; `as_of`, `coverage` status, `sampled` flag. No page-level or per-link rows unless the owner adds them |
| `v1/kit/public.json` | The kit's public projection (title, about, metrics, `unified_total`, analytics) as rendered by the build, copied here by the instance deploy |
| `v1/snapshots/public.json` | Public-access collector numbers (section 5) |

- The hourly job renders `stats` from D1 rollups after the rollup step, applying the same
  floors (`MIN_CELL`, the report floor in `funnel/floor.py`). A key is written only when its
  content hash changed, so writes stay near zero on a quiet hour.
- One route, `GET /e/stats/<site>` (and `/e/kit.json`), reads `PUB` only. Fixed method, fixed
  path allowlist (the site id must be a configured site), `Cache-Control: public, max-age=300`,
  `X-Content-Type-Options: nosniff`, no CORS header by default, every other path keeps the
  usual empty 204. Reuse the `live.ts` pattern: kill switch first, no KV call for an
  unknown site.
- The gated kit stays in `KIT` and behind the cookie. The wall is the binding: the route's
  handler type does not include `KIT`, and a test asserts that the public handler's env
  object has no gated binding and that no key of `PUB` appears in the gated store.
- The gated kit JSON (if any) stays in `KIT` under its own keys.

### 4.3 Data flow

```text
hourly cron: D1 rollups -> render (floors, likely-human only) -> hash compare -> PUB.put
visitor/site JS -> GET /e/stats/<site> -> edge cache -> Worker -> PUB.get (cacheTtl) -> JSON
instance deploy -> PUB.put v1/kit/public.json (build artifact, owner-approved deploy)
```

### 4.4 Limits and costs

- Writes: 8 sites at most, 3 keys each, at most 24 hourly writes each, 576 a day at the
  absolute worst, under the Free 1,000 writes a day (VERIFIED limit). One write per second per
  key is never reached by an hourly job.
- Reads: public traffic times `Cache-Control` hit rate. KV Paid reads are $0.50 per million
  after 10 million. Free allows 100,000 a day, and Free Workers also cap total requests at
  100,000 a day (VERIFIED), so a public route on Free shares that cap with the whole site's
  `/e/*` traffic. Values are a few KB, far under 25 MiB.
- Freshness: KV can lag 60 seconds or more across locations (VERIFIED), plus the hourly
  refresh. The JSON carries `as_of`; consumers show "as of".
- Free vs Paid: fits Free by volume; the route's CPU and the cap sharing make Paid the safe
  choice (section 8).

### 4.5 Migration plan with rollback

1. Create `PUB` (owner step, billable on Paid only above free amounts, shown first), add the
   binding and the renderer, deploy with the route off (`STATS_KILL = "1"`, same rules as the
   other kill switches).
2. Render on the test deployment, compare against `reach-around report` for the same window
   (the golden test, 4.7).
3. Turn the route on for the test host; link the site build to it only after the privacy text
   lists it.

Rollback: set `STATS_KILL`; the route returns the empty 204. Delete the namespace if the
feature is dropped. No D1 data depends on it.

### 4.6 Privacy impact

First time the Worker serves aggregate data publicly. The content is the floored, likely-human
rollups the report already prints to the owner, restricted to totals and top lists. The
privacy page and the threat model gain an entry (a new public read route: enumeration of
sites is limited by the configured list; cache poisoning is not possible, the route is
read-only). Nothing gated is reachable by construction (4.2).

### 4.7 Test strategy

- Golden vectors shared between Python and TypeScript for the floor and fold logic (the same
  JSON fixtures drive `tests/funnel/test_floor.py` and a Worker test). This is the main risk:
  two implementations of one suppression rule must not diverge.
- Route tests: unknown site, wrong method, oversize URL, kill switch, missing binding (all the
  empty 204), headers on the success body, no cookie handling.
- Wall test: a gated sentinel string written to `KIT` never appears in any `PUB` body or in
  the route's output; `PUB` keys are a closed list.
- Staleness test: `as_of` older than a limit is served with a degraded flag.

### 4.8 Risks

| Risk | Mitigation |
|---|---|
| Floor logic duplicated in two languages | Shared golden vectors in CI; the Worker renders only totals and top lists, not the whole report |
| Public exposure of low counts on a small site | Floors and the minimum window; the owner picks the field list (question 4) |
| Stale or missing JSON looks like zero | `as_of` and `coverage` fields; a gap is never rendered as 0 (the report's existing rule) |
| Route becomes a request sink on Free | Edge cache, kill switch, Paid plan |
| KV propagation delay | Documented; `as_of` |

## 5. Item 4: collector snapshots in D1, large raw imports in R2

### 5.1 Current state

`observations` (seven-column composite key, `value REAL`, `access` of `public` or
`owner_authorized`, `expires_at` only for owner rows), `runs`, `kv`, and the click pull tables,
in a local SQLite file written by the Python collectors (`counts/providers.py`, OAuth token
sinks). The wall today: a CHECK that only owner rows expire, a purge of expired owner rows
(`purge_expired`), and the kit's `public_projection` plus a leak search at build time. A Worker
cron cannot read the local file.

### 5.2 Target

- **Collectors stay in Python** (eleven providers with token sinks and egress guards; porting
  them is out of proportion). They write snapshots to Cloudflare through an **ingest route**,
  not a D1 REST token: a D1 write token is account-wide (INFERRED, `docs/research/d1-read-token.md`
  section 2), the route validates every row with the same refusals as `SqlStore.write`, and its
  credential can do nothing else.
- **Two D1 databases.** `ra_snapshots_public` holds only `access = 'public'` observations,
  `runs` and `kv`. `ra_snapshots_owner` holds `owner_authorized` observations. A Worker binds
  only what it needs: the stats renderer and the public route bind the public database
  alone, so the wall is a binding. The ingest Worker binds both and picks the database from the
  row's `access`. No row exists in both.
- **R2 bucket `ra-imports`** (private, no public domain) holds large raw files: platform
  exports, the Beacons history file, insight CSVs. Key `imports/<platform>/<sha256>`. D1 holds
  the manifest row, never the file.
- **Hourly job renders `v1/snapshots/public.json`** into `PUB` from the public database
  (section 4).

Schema sketch (STRICT, section 6 conventions):

```sql
CREATE TABLE observations (
  platform TEXT NOT NULL, account TEXT NOT NULL, metric TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('snapshot','aggregate')),
  day TEXT NOT NULL, observed_at TEXT NOT NULL,
  value REAL NOT NULL CHECK (value >= 0),
  source TEXT NOT NULL CHECK (source IN ('fetched','imported','typed')),
  coverage TEXT NOT NULL CHECK (coverage IN ('complete','partial')),
  coverage_note TEXT, fetched_at TEXT NOT NULL,
  expires_on TEXT,                -- owner database only; NOT NULL there, absent in public
  PRIMARY KEY (platform, account, metric, kind, day, source, observed_at)
) WITHOUT ROWID, STRICT;

CREATE TABLE import_files (
  sha256 TEXT NOT NULL PRIMARY KEY CHECK (length(sha256) = 64),
  platform TEXT NOT NULL, bytes INTEGER NOT NULL CHECK (bytes >= 0),
  rows_imported INTEGER NOT NULL CHECK (rows_imported >= 0),
  imported_at TEXT NOT NULL, expires_on TEXT, access TEXT NOT NULL
) WITHOUT ROWID, STRICT;
```

`observations.value` stays `REAL` on purpose: it holds rates and durations as well as counts.
Only count columns become integers (item 5). The public table has no `expires_on` column and
no `access` column; the owner table has `expires_on NOT NULL`, so the old cross-check
(`access = 'owner_authorized' OR expires_at IS NULL`) becomes structure.

### 5.3 Data flow

```text
Python collector -> Store protocol (D1IngestStore) -> POST /i/snapshots (Access service token)
   -> validate batch (same refusals as SqlStore.write) -> D1 batch into public or owner DB
hourly cron -> read public DB -> render snapshots JSON -> PUB
owner purge (cron) -> DELETE owner rows past expires_on; R2 lifecycle deletes the raw file
large import -> Python uploads to R2 (presigned or ingest route) -> manifest row -> parsed rows
```

The local SQLite store stays as the offline and test implementation of the same `Store`
protocol; `open_store` gains a `kind: d1_ingest`. `click_daily`, `click_cursor`,
`click_batches`, `click_dead_letters` are dropped: raw clicks no longer exist (item 2), and
per-link click totals come from the funnel rollups.

### 5.4 Limits and costs

- Size: a year of observations is tens of thousands of rows (INFERRED estimate: dozens of
  platform-account-metric series, one row a day), megabytes, against 500 MB per database on
  Free (VERIFIED). Rows are far under 2 MB (VERIFIED limit), which is why raw files cannot live
  in D1.
- Writes: a few hundred rows a day, negligible beside the click ceiling.
- Databases: two more fits the Free 10 (VERIFIED).
- R2: within the free tier at this scale (10 GB-month, 1 million Class A; VERIFIED). Enabling R2
  may need a payment method on file; the page does not say (INFERRED), so the owner checks the
  dashboard first. Lifecycle rules delete a raw file after the platform's retention window
  (`docs/research/platform-terms-retention.md`), within about 24 hours of expiry (VERIFIED).
- Ingest route: a Worker request per batch; Access is a Cloudflare One feature whose Free
  allowance and setup were not read for this page (INFERRED; `d1-read-token.md` read only the
  service-token page).

### 5.5 Migration plan with rollback

1. Create the two databases and the bucket (owner step; D1 and R2 creates are shown first).
2. Apply the snapshot baseline (section 6) to each database.
3. Ship the ingest route and `D1IngestStore` with the collectors still writing local SQLite.
4. Dual write: collectors write local first, then the ingest route; a mismatch check compares
   the two stores per series and day.
5. Make D1 the source for the rendered public JSON; keep the local store as a cache for one
   release.
6. Drop the click pull tables with a store migration.

Rollback: the local store is untouched until step 6, so rollback is to stop the ingest call
and the JSON render. Owner rows deleted by `expires_on` are not recoverable from Time Travel
past its window (7 or 30 days, VERIFIED); expiry-sensitive data may sit in Time Travel up to 30
days after deletion (INFERRED from "always on"). That is a retention fact the platform-terms
note must state (question 5).

### 5.6 Privacy impact

- The public/owner wall changes from a column check to separate databases and bindings. A new
  failure mode is a mis-routed write; the ingest validator routes by `access` and refuses a row
  whose access and database disagree, and a test covers every combination.
- Owner-authorized rows now exist in a cloud account, not only on the owner's machine. They
  were already collected from platform APIs under their terms; the move needs the owner's
  review of `platform-terms-retention.md` per platform (question 5). The kit build and its leak
  search still run on the Python side.
- The D1 REST read token is account-wide (INFERRED), so a leaked read token reads both
  databases. The Access-protected read route recommended by `d1-read-token.md` applies here
  too. Moving the owner database to a second account is the only documented way to make the
  token unable to reach it (INFERRED) and is not recommended.
- Time Travel keeps deleted owner rows for the retention window (above).

### 5.7 Test strategy

- Parity tests: the same `Store` contract tests (`tests/store/`) run against SQLite and against
  an in-memory D1 stand-in (Miniflare via the existing vitest pool) through the ingest route.
- Wall tests: every `access` and database combination; the public Worker env has no owner
  binding; a sentinel owner value never appears in the public JSON.
- Validator tests: every refusal in `SqlStore.write` has a route twin (negative value,
  non-finite, bad slug, kind mismatch).
- Import test: a large synthetic file lands in a fake R2, its manifest row matches the hash, a
  second upload of the same hash is a no-op.
- Expiry test: owner rows and the R2 object follow the same date (fake clock).

### 5.8 Risks

| Risk | Mitigation |
|---|---|
| Collectors and the ingest route drift | One validation spec as JSON, loaded by Python and TypeScript tests |
| Access service token is a new credential | Rotated pair in the secret manager, dev first, per-purpose identity; route only accepts the one schema |
| Owner data in the cloud outlives its terms | `expires_on` plus R2 lifecycle plus the Time Travel note; per-platform owner sign-off |
| Two databases double the migration paths | One migration lint and one baseline pattern (section 6) |
| R2 needs billing set up | Question 7 |

## 6. Item 5: schema cleanup and one migration pattern

### 6.1 Conventions (the lint enforces these)

1. Every table is `STRICT`.
2. Every count is `INTEGER NOT NULL CHECK (n >= 0)` and is named for what it counts. One rule
   for the lower bound: a stored row exists only when its count is at least 1, except tables
   that legitimately hold zero (`view_hour_daily` with views or clicks; state tables). The
   lint reads the rule from a comment tag, so exceptions are explicit.
3. Counter state moves out of `view_meta`'s text values into an integer table
   (`counter(key, day, n)`), which `counters` already is. `view_meta` keeps only text state
   (`rolled_through:<site>`), renamed `state`.
4. One key shape for rollup tables: `(site, period, ...dimensions)` where `period` is a UTC
   day `YYYY-MM-DD` or a UTC month `YYYY-MM` in a column named `day` or `month`. `day_tz` is
   dropped (always `UTC`; the Python report keeps its display zone).
5. `site` is one definition: `length(site) BETWEEN 1 AND 20 AND site NOT GLOB '*[^a-z0-9-]*'`;
   the leading character rule stays in `sites.ts`. CHECKs stay small (the repository notes a CI
   SQLite parser stack limit).
6. Composite or text primary keys use `WITHOUT ROWID`. Tables with an integer surrogate key
   (`leads.id` is text, so it becomes `WITHOUT ROWID` with its `email` unique index kept) use
   the rowid.
7. Names: tables `<thing>_<grain>` (`view_daily`, `click_src_monthly`); no new column called
   `count`, `value` or `key` unless it is a literal key or value.

### 6.2 One rebuild pattern

SQLite cannot add to a primary key, so a key change is a rebuild. The pattern, in one file per
rebuild, is the only one allowed:

```sql
-- rebuild: <table>   reason: <why>   rows expected: same
PRAGMA defer_foreign_keys = true;
CREATE TABLE <table>_new ( ... target shape ... ) WITHOUT ROWID, STRICT;
INSERT INTO <table>_new (<explicit columns>) SELECT <explicit columns, with casts> FROM <table>;
-- guard: aborts the file when counts differ
CREATE TABLE _rebuild_guard (ok INTEGER NOT NULL CHECK (ok = 1));
INSERT INTO _rebuild_guard SELECT (SELECT COUNT(*) FROM <table>) = (SELECT COUNT(*) FROM <table>_new);
DROP TABLE _rebuild_guard;
DROP TABLE <table>;
ALTER TABLE <table>_new RENAME TO <table>;
-- recreate indexes here, then end
```

D1's migrations page does not say whether a failed file rolls back (VERIFIED absence), so the
pattern is built to fail safe in either case: a failure before `DROP TABLE <table>` leaves the
original intact and the next run stops at `CREATE TABLE <table>_new` until the stray table is
dropped, a documented recovery line. The existing comment "D1 applies a migration file as one
batch" is repository text, not a vendor statement; the lint and the recovery line replace
reliance on it. Always run with the Worker idle (the switches in `docs/cutover-runbook.md`).

The Python store migration runner keeps its checksums, lock and `VACUUM INTO` backup; its SQL
files follow the same conventions and the same rebuild comment header.

### 6.3 Migration lint (CI)

A script (Python, run by the repository's existing CI job; no new workflow) parses every file
in `worker/migrations` and `src/reach_around/store/migrations` and fails on: a `CREATE TABLE`
without `STRICT`; a composite key without `WITHOUT ROWID`; a count column not `INTEGER`; a
rebuild with no guard; a column name matching the privacy guard's forbidden list (the same
list as `worker/test/views/schema.test.ts` and `funnel/export.py`); a gap or reuse in file
numbers. A negative fixture per rule proves each bites. It also applies the whole set to an
empty database and compares `sqlite_master` to a committed schema snapshot, so a drift is a
reviewed diff.

### 6.4 Current state, target, plan

Current state: section 1. Target: the conventions above. Two ways to get there:

**A. Reset the baseline (recommended).** Nothing is live, and only migration 0001 has ever run
on the test database (`worker/migrations/0007` header). Replace `0001` to `0017` with one
`0001_baseline.sql` in the target shape; do the same for the store's `0001_core.sql` (its header
says never edit once released; no release has shipped it, which the owner confirms, question 1).
The test database is dropped and recreated. Cost: no migration chain to review twice, no
rebuild debt, one lint-clean file. Risk: the "never edit" rule is broken once, on purpose and
on the owner's word; the test database loses its test data.

**B. Forward migrations.** Add `0018` onward that rebuild every non-conforming table with the 6.2
pattern. Keeps history, costs about twelve rebuilds and a long reviewed chain for a database
nobody depends on.

Rollback for A: the old files stay in git history; a test database export is taken first and
the recreate is repeatable. Rollback for B: restore from the export (the pre-step bookmark).

The Python report's export contract (`funnel/export.py` `Spec` entries) is the consumer that
must change with the schema: `day_tz` becomes optional and ignored, `click_events` and
`kit_events` rows are replaced by rollup tables (`click_daily` per link, `kit_daily`), and an
adapter keeps old exports loadable for one release. This is why the export contract is part
of the slice list, not an afterthought.

### 6.5 Privacy impact and risks

No new stored field. The lint's forbidden-name list is the same one the guards already use. The
risk is a baseline that silently differs from the chain it replaces: the schema-snapshot
comparison and the parity fixtures (3.7) cover it, and the baseline is built by applying the old
chain to a scratch database and diffing the two `sqlite_master` dumps before the old files are
deleted.

### 6.6 Test strategy

Lint negative fixtures; snapshot test; the rebuild pattern run on a seeded scratch database
(row counts and a checksum before and after); a forced failure after the copy to prove the
recovery line; the whole suite of existing Worker tests against the new baseline
(`apply-migrations.ts`).

## 7. Interfaces needed from item 1 (not designed here)

This design only assumes these, and the item 1 design supplies them:

- A daily dedupe check that answers "first sighting of this address hash today for this site"
  and forgets at the end of the UTC day (today `visitor_seen`).
- An accepted-view and accepted-click count per site and hour, used for reconciliation (3.7).
- The budget and limiter state, wherever it lives, so the event write is skipped when a view is
  not accepted.

If item 1 changes where the click funnel cells are incremented, the AE `ra_click` dataset and
the rollup are unaffected: the funnel tables stay the rollup of record and the rollup can fill
them from Analytics Engine when the counters move.

## 8. Free versus Paid, and what billing the owner decides

| Piece | Fits Free? | Needs Paid for | Billable step |
|---|---|---|---|
| Hourly job reading Analytics Engine and writing D1 | No (INFERRED) | 10 ms CPU per cron run on Free is too small for HTTP fetches, parsing and several D1 batches; Paid allows up to 15 min (VERIFIED) | Plan change, owner |
| D1 writes at the configured ceilings | No | 100,000 rows a day on Free; `docs/deploy.md` already refuses Free at the click ceiling | None beyond the plan |
| Analytics Engine | At real traffic yes | Configured ceilings (about 12.5 million points a month, 10 million included) | None until billing starts (VERIFIED); then about $0.63 a month at the ceilings (INFERRED) |
| KV `PUB` | Yes by volume | Safety margin against the Free 100,000 requests a day shared by the whole Worker | None below the allowances |
| Two D1 snapshot databases | Yes | Nothing | None below the allowances |
| R2 bucket | Yes by volume | Nothing; payment method need is unknown (INFERRED) | Owner checks the dashboard |
| Access service token for the ingest route | Unverified | Unverified | Owner checks the Cloudflare One plan |

The cutover runbook already says the Workers paid plan is approved (#610); this page assumes
that (INFERRED from `docs/cutover-runbook.md`). The owner sets a billing alert before the shadow
period. Every create (datasets are created by first write, namespaces, databases, bucket) is
shown to the owner first, as the issue requires.

## 9. INFERRED items to close

1. The runtime timestamp of an Analytics Engine point cannot be set or rounded by the writer.
2. Analytics Engine writes are best-effort and the sampling threshold; measure in the shadow
   period.
3. Account Analytics Read, D1 Read and the Access service token have no per-dataset or
   per-database scope.
4. D1 migration files are atomic; this design does not rely on it.
5. The R2 payment-method requirement, and the Cloudflare One plan for Access.
6. Time Travel holding deleted rows for its window after a purge.
7. Analytics Engine query latency after a write (affects `ROLLUP_GRACE_HOURS`).
8. The volume estimates (series count for snapshots, real traffic).

## 10. Sliced build order

Every slice is its own issue and card under epic #365, its own PR, merged only on `CI green`.
Review notes name the domain pack. Security and privacy slices get the adversarial review on
the owner's cyber-program tier (three rounds at most, fixtures proved by revert).

| # | Slice | Test notes | Review notes |
|---|---|---|---|
| S1 | Conventions page, migration lint, schema snapshot, rebuild pattern doc (item 5, no schema change yet) | Negative fixture per rule; lint runs on today's chain and lists the debt | Ordinary review |
| S2 | New baseline for Worker and store (option A) or rebuild chain (option B); export contract adapter | Chain-versus-baseline `sqlite_master` diff; all existing Worker and `tests/funnel` suites; seeded rebuild checksums | Review plus a reviewer who reverts each lint rule |
| S3 | Analytics Engine writer behind `EVENTS_SINK=d1\|both\|ae`; blob-order contract; sentinel tests | Section 3.7 writer tests | Adversarial review: no joint row, no hash or lead id in a point, closed values only |
| S4 | Rollup from Analytics Engine (shadow, then `ROLLUP_SOURCE=ae`); reconciliation and `sampled` flag | Fake query client; parity fixtures; per-site seal tests | Adversarial review of the token handling and the replace-not-add idempotence |
| S5 | Drop raw tables, retention simplification, `kit_daily`, lead purge loses its event delete; privacy text and threat model entries | Migration test on a seeded database; privacy-text coverage test | Adversarial review of the privacy delta |
| S6 | `PUB` namespace, renderer, `GET /e/stats/<site>`, kill switch; golden floor vectors | Section 4.7 | Adversarial review of the wall and the new public route |
| S7 | Snapshot databases, baseline, ingest route, `D1IngestStore`, dual write | Section 5.7 | Adversarial review of the ingest validator and the access routing |
| S8 | R2 imports: bucket binding, manifest, lifecycle, upload path | Fake R2; expiry fake clock | Ordinary plus the owner's per-platform terms sign-off |
| S9 | Public snapshots JSON into `PUB`; drop the store's click pull tables | Wall sentinel test; store migration test | Review |

S1 and S2 come first because every later table is written in the final shape once. S3 to S5 are
one chain (flip, shadow, cut over) with the rollback points in 3.5. S6 can run in parallel with
S3 to S5 after S2. S7 to S9 are independent of S3 to S6 after S2. Owner-only steps (account
resources, tokens, plan, billing alert) sit at the start of S3, S6, S7 and S8 and are not done
by any agent.

## 11. Owner-only questions (answered 2026-11-24)

All ten questions below are answered. The answers are in the next section; the questions and
recommendations stay for the record.

1. **Reset the migration baseline now (option A) or add forward rebuilds (option B)?**
   Recommended: A. It breaks the "never edit a released migration" rule once, which is safe only
   if no release has shipped these files and the test database may be recreated. Confirm both.
2. **How should raw events be kept given Analytics Engine's fixed three months and exact
   timestamps?**
   Recommended: B, split marginal points, accept 90 days, update the privacy text.
   A: one wide point per view (simplest, joint rows for 90 days, not recommended).
   C: keep only coarse fields in raw points (page, source, country, class) and aggregate city
   and fine dimensions only through counters (removes timestamp correlation; makes the geography
   rollup depend on item 1).
3. **Billing.** Confirm the Workers Paid plan is the baseline for this design, and that you set
   a billing alert before the shadow period. Recommended: yes, alert at a low monthly figure.
4. **Which fields are public in `v1/stats/<site>.json`?**
   Recommended: likely-human totals for 7 and 30 days, top 5 countries and top 5 source labels,
   all floored, plus `as_of` and coverage. No page, link or city rows.
5. **Collector snapshots: one database or two, and which platforms may leave your machine?**
   Recommended: two databases (the wall is a binding), and move owner-authorized rows only for
   platforms whose terms in `platform-terms-retention.md` you approve, one at a time.
6. **Ingest credential: Access service token on a route (recommended) or a D1 write token?**
   Recommended: the route. A D1 write token covers every database in the account (INFERRED).
7. **R2.** Approve enabling R2 (check whether a payment method is required first), and the
   default retention for raw imports. Recommended: private bucket, lifecycle equal to each
   platform's retention window, 90 days where the terms are silent.
8. **Analytics Engine read token.** You create a token with Account Analytics Read only, as a
   rotated a/b pair in the secret manager's dev environment, and set it as the Worker secret.
   Recommended: yes, nothing else on that token.
9. **Seal window** for re-rolling a closed day from Analytics Engine. Recommended: 48 hours after
   `ROLLUP_GRACE_HOURS`.
10. **Leads database.** Separate issue (not in this design): move `leads` to its own database so
    no read credential for analytics reaches it. Recommended: file it.

## 12. Owner decisions (2026-11-24)

These answer the ten questions in section 11, in the same order.

1. Reset both migration baselines (Worker and store) now.
2. Raw events: split marginal points in Analytics Engine, 90-day retention. The privacy text and
   threat model are updated in the code PR to state 90 days and exact timestamps.
3. Workers Paid is the baseline, Free is best-effort, and a billing alert is set before the
   shadow period. Billing is the owner's step and is shown first.
4. Public stats JSON in KV: likely-human totals for 7 and 30 days, top 5 countries, top 5
   sources, all floored, plus `as_of` and coverage. No page, link or city rows.
5. Two D1 databases (public and owner_authorized). Collectors write through an ingest route
   behind Access using a scoped Access service token (a rotated a/b pair in the secret manager's
   dev environment). Which platforms' owner-authorized data move to the cloud is decided one
   platform at a time later.
6. R2 private bucket with lifecycle expiry equal to each platform's window (90 days where terms
   are silent); D1 keeps a manifest row. Check read-only first whether R2 needs a payment method.
7. The owner creates the Analytics Engine read token (Account Analytics Read only) as a rotated
   a/b pair in dev when that slice is reached.
8. Seal window: 48 hours after `ROLLUP_GRACE_HOURS`.
9. Leads move to their own D1 database (separate issue).
