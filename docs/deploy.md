# Deploying to the test subdomain

This is the one-time setup the maintainer does in Cloudflare and GitHub, and
how the manual workflow `.github/workflows/deploy-test.yml` uses it. The
workflow deploys the click-counting Worker and the static link page to a
**test** hostname. The production hostname is a separate cutover.

Every name below is a placeholder. Replace `example.invalid` style values with
your own; never commit real account ids, database ids or tokens.

| Placeholder | Meaning |
|---|---|
| `links-test.example.invalid` | the test hostname you choose |
| `reach-around-test` | the Pages project name |
| `reach-around-clicks` | the D1 database name (matches `worker/wrangler.toml`) |

## 1. Cloudflare steps

1. **Pages project.** Dashboard > Workers & Pages > Create > Pages > Direct
   Upload. Name it `reach-around-test` and create it with any placeholder
   file. Direct Upload means Cloudflare never builds from your repository; the
   workflow uploads the already-built site. Note the project name. Check that
   you really got a Pages project and not a Worker (see "Pages or Worker?"
   below), and that its production branch is `main` (see "Production
   branch").
2. **D1 database.** Dashboard > Storage & Databases > D1 > Create database,
   named `reach-around-clicks`. Copy the database id (a UUID).
3. **Rate limiting namespace.** The Worker's `CLICK_LIMITER` binding needs a
   namespace id: any positive integer you pick that is unique within your
   account (for example `1001`). It is not a secret and nothing is created in
   the dashboard.
4. **Account id.** Dashboard > Workers & Pages overview, right-hand column.
   It identifies the account; it is not a secret, but keep it out of the
   repository.
5. **Test hostname.** Pick `links-test.example.invalid` (use a hostname in a
   zone you control, for example `links-test.<your-domain>`). Attach it to the
   Pages project: Pages project > Custom domains > Set up a custom domain.
   When the zone is on Cloudflare this creates the DNS record for you. If you
   add it by hand, create a proxied `CNAME` from the test hostname to
   `reach-around-test.pages.dev`.
6. **API token.** My Profile > API Tokens > Create Token. Use a **user**
   token, not an account-owned one (see "Token type" below for why). Start
   from the "Edit Cloudflare Workers" template or Create Custom Token. Grant
   only:

   | Scope | Permission | Why |
   |---|---|---|
   | Account | Cloudflare Pages: Edit | `wrangler pages deploy` |
   | Account | D1: Edit | `wrangler d1 migrations apply` |
   | Account | Workers Scripts: Edit | `wrangler deploy` |
   | User | User Details: Read | wrangler identifies the token owner |

   Under Account Resources, include only your one account. Add a client IP
   filter and a short expiry if you can. Do not use the Global API Key. If a
   wrangler step reports a missing permission, add that one permission and
   nothing broader.

   Only if you set `ZONE_NAME` (section 2), also grant these two, limited to
   the one zone under Zone Resources:

   | Scope | Permission | Why |
   |---|---|---|
   | Zone | Workers Routes: Edit | `wrangler deploy` creates the `/e/*` route |
   | Zone | Zone: Read | wrangler looks up the zone by name |

   Without `ZONE_NAME` neither is needed. If you would rather not grant the
   zone permissions at all, add the route by hand instead (see "Adding the
   route by hand").

## 2. GitHub steps

1. Repository > Settings > Environments > New environment named `test`.
   - Add yourself under **Required reviewers** so every deploy needs a click.
     Keep at least one reviewer; an environment with none releases its
     secrets without a click.
   - Under **Deployment branches**, choose "Selected branches" and allow only
     `main`. The workflow also refuses any other ref on its own.
   - **Prevent self-review.** With a sole maintainer, turning this on would
     lock you out: you could never approve your own deploy. Leave it off
     while you are the only reviewer, and turn it on once a second reviewer
     exists (then a person other than the one who started the run must
     approve it).
2. In the `test` environment add this **secret**:
   - `CLOUDFLARE_API_TOKEN`: the token from step 1.6.
3. In the same environment add these **variables** (not secret, but still
   keep real values out of commits):

   | Variable | Value | What it does |
   |---|---|---|
   | `CLOUDFLARE_ACCOUNT_ID` | account id | selects the account for wrangler |
   | `PAGES_PROJECT` | `reach-around-test` | Pages project to publish to |
   | `D1_DATABASE_ID` | the D1 UUID | written into a throwaway deploy config |
   | `RATELIMIT_NAMESPACE_ID` | `1001` | rate limiter namespace |
   | `SITE_HOST` | `links-test.example.invalid` | the only origin the Worker accepts clicks from |
   | `LINK_IDS` | optional: `["example-one","example-two"]` | not needed. The deploy gives the Worker the link ids of the build itself (read from `site-out.manifest.json`). If you set it, a difference from the build is a warning only and the build wins |

   Optional variable, set it to count clicks:

   | Variable | Value | What it does |
   |---|---|---|
   | `CLICKS_KILL` | `1` or `true` | optional kill switch: the click route returns its usual empty 204 and touches neither D1 nor the rate limiter. Any other value, or unset, leaves it on. Read on every request, so `npx wrangler deploy --var CLICKS_KILL:1` takes effect when the deploy lands and `--var CLICKS_KILL:0` turns it back on. Leave it unset in the committed `wrangler.toml`. The link page keeps working with the Worker dark, because links carry their real `href` |
   | `ZONE_NAME` | `example.invalid` | the Cloudflare zone that holds `SITE_HOST`; adds a `[[routes]]` entry for `SITE_HOST/e/*` to the throwaway deploy config |

   **Without `ZONE_NAME` the page works but clicks are not counted.** The
   click pings go to `/e/c/<id>` on the site's own host, and only a zone route
   sends that path to the Worker. When set, `ZONE_NAME` must be a plain
   hostname (`a-z`, `0-9`, `.`, `-`); when unset, the deploy config is
   unchanged.

   Optional variables for the Live tab poller (issue #314; full setup in
   [live.md](live.md#deploying-the-live-worker)). Plain **variables**, never
   secrets:

   | Variable | Value | What it does |
   |---|---|---|
   | `LIVE_TWITCH_HANDLE`, `LIVE_KICK_HANDLE` | your channel handles | **setting either one enables the Live Worker** for this deploy: the throwaway config gets the handle(s), the `LIVE_KV` binding and the second cron `*/2 * * * *` |
   | `LIVE_KV_ID` | 32 lowercase hex characters, the KV namespace id | **required once Live is enabled**. Not a secret. The deploy fails closed before anything is fetched if it is unset, empty, all zeros or malformed; the placeholder id in `wrangler.toml` can never ship |

   With neither handle set the deploy config is unchanged, even if
   `LIVE_KV_ID` is set.

   The workflow validates each value's shape before using it and fails with a
   clear message if a required one is missing.

## Live status (`GET /e/live`), not deployed

Code only for now (issue #297, slice 4). Nothing below is set up; each step needs the owner.

The Worker's rule is that every request gets the same empty 204 and no route returns stored data. `GET /e/live` is the one deliberate exception: it returns booleans and timestamps only, `{"v":1,"as_of":..,"ttl":30,"kick":{"live":..,"since":..,"code":..},"twitch":{"live":..,"since":..,"code":..}}`, where `live` is `true`, `false` or `null` (unknown), `since` is Unix seconds or `null`, and `code` is one fixed string from a closed list (or `null`) saying why a platform is unknown; it is for operators reading the JSON, the page ignores it (see [live.md](live.md#failure-codes)). It takes no parameters (the query string is ignored), sends `Cache-Control: public, max-age=30`, `X-Content-Type-Options: nosniff` and no CORS header, and keeps an edge copy so KV reads stay small. Any other method on it, and every other path, is the usual empty 204.

| Name | Kind | What it does |
|---|---|---|
| `LIVE_KV` | binding | KV namespace holding the state and the cached app tokens. The commented block in `wrangler.toml` is a placeholder; the deploy workflow writes the real binding from the repository variable `LIVE_KV_ID` (above). Unbound: the route serves the all-unknown body |
| `LIVE_KILL` | var | Strict: `1`/`true`/`on`/`yes` or `0`/`false`/`off`/`no` (any case, no spaces). Unset or empty is off. Anything else fails closed: the route serves the all-unknown body and the poller does nothing. Read per request |
| `LIVE_TWITCH_HANDLE`, `LIVE_KICK_HANDLE` | var | Channel handles, checked by the config model's patterns (Twitch `[A-Za-z0-9_]{1,25}`, Kick `[A-Za-z0-9_-]{1,40}`). A bad or missing handle means that platform is never polled and shows as unknown. Never taken from a request |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `KICK_CLIENT_ID`, `KICK_CLIENT_SECRET` | **secret** | App (client-credentials) credentials, set with `npx wrangler secret put <NAME>`; never in a file, log or chat |
| second cron `*/2 * * * *` | trigger | Runs ONLY the poller. The hourly trigger still runs only the purge and rollup. Free allows 5 cron triggers per account |

The poller writes KV only when a platform's state changes, plus a heartbeat every 10 minutes, and skips the heartbeat past about 800 state writes in a UTC day (the data then goes stale and the page degrades: an outage, not a bill). The Worker serves a platform as unknown when `as_of` is older than 30 minutes (three heartbeats; three 2-minute polls would flap between heartbeats). The request builders for both platforms are small functions in `worker/src/live.ts`, each marked "verify against the current Twitch (Kick) docs before deploy": the token and stream endpoints were partly inferred, not confirmed. The Kick channel response carries a stream key; only `is_live` and `start_time` are read and nothing else is kept, logged or returned.

## Page views (`POST /e/v/:pageId`)

The Worker also has a write-only page-view route (Plan 4 Tasks A3 and A4; the browser script that calls it is a later task). It needs migrations `0005` and `0006` applied to the same D1 database, and the same `/e/*` zone route as clicks.

| Variable | Kind | What it does |
|---|---|---|
| `PAGE_IDS` | var | JSON array of tab ids from the build manifest. Any other page id is ignored. Deploy the Worker with it before the site |
| `VIEW_HASH_SECRET` | **secret** | 32 or more random bytes, base64. Set with `npx wrangler secret put VIEW_HASH_SECRET`; never in `wrangler.toml`, a committed file, a log or a chat. **Required.** It keys the rate-limiter keys, the per-client cap and the visitor hash. Absent, too short or malformed (or a request with no parsable client address): the route fails closed and records nothing |
| `VIEWS_KILL` | var | `1` or `true` makes the route return its usual empty 204 with no database or limiter call. Read per request |
| `VIEW_LIMITER` | binding | Workers Rate Limiting binding, placeholder in `wrangler.toml` (5 per client per 60 s). Required: without it the route records nothing. It is keyed by an HMAC of the address under a stable key derived from the secret (purpose label `lim`, no day component, so there is no burst at midnight UTC), never the raw address, so no address is stored in the binding either. `VIEW_SUSPECT_LIMITER` (optional) works the same way with label `sus`. The click route's `CLICK_LIMITER` uses the same secret with label `clk`; if the secret is not set on the Worker it falls back to a random per-isolate key (still never the raw address, but the allowance is per isolate). Addresses are canonicalized first (`worker/src/address.ts`), so equivalent spellings share an allowance; an absent or malformed address uses one shared `unknown` bucket. Rotating the secret changes every limiter key, resets every address-based allowance and per-client cap, and can count a visitor twice that UTC day (the visitor hash changes mid-day): rotate at UTC midnight or accept the burst |
| `VIEWS_PER_HOUR`, `BOT_VIEWS_PER_HOUR` | var | global hourly write budgets, defaults 120 and 30 (Plan 4 open decision P-6) |
| `CLIENT_VIEWS_PER_HOUR` | var | per-address hourly cap, default 60, ceiling 1000, then clamped to half the human budget (humans) or a quarter of the bot budget (bots). One counter per address is shared by bot and human traffic. Keyed on a daily keyed hash of the address, never the address |
| `RAW_KEEP_DAYS` | var | days raw view rows are kept once rolled up, default 7 (open decision P-3), ceiling 90; a bad value falls back to 7. An unrolled day is kept up to 3 times this |
| `QUERY_BUDGET` | var | D1 queries one cron invocation may make: default 40 (Free allows 50), at most 900 (Paid allows 1000); a batch counts as one |
| `ROLLUP_MAX_ROWS_PER_DAY` | var | raw rows above which a day is rolled in the reduced mode; default 3000, ceiling 20000. One invocation takes at most 4 times this many input rows |
| `UTM_ALLOWLIST` | var | optional comma-separated lower-case `utm_source` labels; any other label is stored as `other` in the aggregates. Unset means no filter |
| `MIN_CELL` | var | cells under this many views in a day fold into `other`, default 3, 1 to 50 |
| `ROLLUP_GRACE_HOURS` | var | hours after UTC midnight before a day is rolled up, default 2, at most 12 |
| `TOP_SOURCES` | var | referrer and utm labels kept per day in the rollup, default 50; the rest fold into `other` |
| `GEO_LEVEL`, `BOTS_MODE`, `UV_ENABLED`, `UV_BITS` | var | `city` / `count` / on / `64` by default. `UV_BITS` has a minimum of 64 (open decision P-5): a lower value falls back to 64, and at most 128 is accepted |

Retention runs from the cron (`17 4 * * *` and `5 0 * * *`): `visitor_seen` rows before today's UTC date, raw view rows past `RAW_KEEP_DAYS`, hourly budget counters older than 48 hours and client-cap counters older than the previous hour. Counter keys are `bud:<hour>:<route>` and `cap:<hour>:<hash>` (the hash is 16 hex characters, 64 bits; counters from before the widening expire within the hour) with the hour zero-padded, so expiry is a range delete on the primary key. A run that hits its batch budget records `purge_backlog` in `view_meta` and the next run continues. The daily rollup that should run before the raw purge (Task A5) is not built yet, so raw rows are deleted by age alone for now.
Cron order (`17 4 * * *` and `5 0 * * *`): click purge, then the **daily rollup**, then the view purge. Each step is isolated, so one failing never stops the others, and none of them throws out of the handler.
Cron: one hourly trigger, `5 * * * *` (so the pseudonymous daily visitor hash is deleted within about an hour of its UTC day ending). Each run does, in this order, the click purge, the **rollup**, then the view purge. Each step is isolated, so one failing never stops the others, and none of them throws out of the handler. Free allows 5 cron triggers per account in total, so this uses one.
Cron: one hourly trigger, `5 * * * *` (so the pseudonymous daily visitor hash is deleted within about an hour of its UTC day ending). Each run does, in this order, the click purge, the **view purge**, the **lead purge** (media kit retention, see below) then the **rollup**. The purge comes before the rollup on purpose: a rollup that fails, runs out of CPU or is cut off by the platform can never block retention (the view purge only deletes days that are already rolled, plus rows past the hard ceiling). Each step is isolated, so one failing never stops the others, and none of them throws out of the handler. Free allows 5 cron triggers per account in total, so this uses one.

**Hard per-invocation query budget.** The Cloudflare limits this is built on (read 2026-10-24): the D1 limits page (`developers.cloudflare.com/d1/platform/limits/`, last updated Oct 24, 2026) says a Worker may make 1000 D1 queries per invocation on Paid and 50 on Free, and that a `db.batch()` counts as ONE query, not one per statement. The Workers limits page (`developers.cloudflare.com/workers/platform/limits/`, last updated Oct 12, 2026) gives 50 subrequests per invocation on Free (10,000 on Paid, D1 included) and 5 Cron Triggers per account on Free. `worker/src/d1budget.ts` wraps the database once, so every `run`, `all`, `first`, `raw`, `exec` and `batch` is counted in one place and throws `BudgetExceeded` instead of making the call when the meter is empty. `QUERY_BUDGET` sets it: default 40 (Free, 10 left as margin), bad values fall back to 40, ceiling 900 (Paid). The handler gives the view purge at most half; the click purge, the purge and the rollup all draw from the same meter, and every unit of work checks what is left before it starts.

Cost per invocation with the defaults: click purge 1; rollup 1 (one batch that finds the days to roll and the sealed days with late rows) + 1 (lease) + 2 per day (one read batch, one write batch) + 1 (heartbeat batch that also releases the lease); view purge 1 (one batch holding the overdue check and every step's first delete) + up to a few more rounds while the budget lasts + 1 (closing batch). The rollup therefore rolls up to about 6 days per invocation with the defaults (the purge uses 2 to 3 of the 40); the worst day (every cap and fold exercised, 3,600 raw rows) is still exactly those 2 queries, because the write batch holds up to about 370 statements (a batch of 2,000 statements is fine per the D1 page). An invocation with a tight budget rolls fewer days and stops; the next hourly run continues from the same raw rows, and a test proves repeated runs at budget 12 end with the same aggregates as one unbounded run. A day is one transaction (the write batch), so there is no half-sealed day and no stage marker is needed.

**Rollup** (`worker/src/viewrollup.ts`). Every UTC day that has ended and whose grace period has passed (`ROLLUP_GRACE_HOURS`, default 2, ceiling 12, because a view that began before midnight can be written after it), and that still has raw rows in `view_events` and no row in `view_rollup_log`, is aggregated into three marginal tables: `view_daily` (page, source, country, bot class), `view_dim_daily` (one dimension at a time: browser, os, device, lang, vp, net, reason) and `view_geo_daily` (country, region, city). No address, user agent, referrer URL or browser version reaches them. `uniques` counts the raw rows whose `first` flag is exactly 1 (set from the daily hash at write time); a damaged or hostile value such as 1000000, -1, 2 or text counts as 0, so it cannot inflate uniques or fail a day. `bot_class` and `src_kind` are closed sets enforced by CHECK constraints. I did not add a `CHECK (first IN (0,1))` to the raw table: the aggregation is already safe against any value and tested with hostile rows, which a CHECK would make impossible to insert. A row whose timestamp is not an integer belongs to no day; the purge deletes such rows. The `view_rollup_log` row is the last statement of the day's write transaction and is the checkpoint, so running twice never double counts. A day with a log row is sealed. A day that fails (database error or a totals mismatch) is recorded and skipped: later days still roll, the run records `rollup_err` and `rollup_failed_day`, and the failed day is retried on the next run; it never blocks the purge of days that are already rolled. At most 14 days are rolled per run (the query budget and the input row budget usually stop it earlier). Ranking, top-N and every fold run in SQL (window functions in D1), so the Worker receives only the capped output rows. Sources beyond `TOP_SOURCES` (default 50) fold into `other`; per bot class, `browser` keeps 12 values, `lang` 10, `os` 10, `net` 10, `reason` 20, `device` and `vp` 6, the rest `other`; the core and geography tables are capped at 500 and 300 rows per day, and the dimension table has a real maximum of 324 rows (4 bot classes times the top values of all seven dimensions plus one `other` each; it follows from the per-dimension tops, a test reaches it exactly). The core cap is hard: overflow rows lose their page as well as their source and country, so they collapse to one `other` page row per bot class (readers must accept `other` as a page value). `GEO_LEVEL` is applied again at rollup, so a `country` setting never aggregates a region or city even if an older raw row has one.

**Small cells.** `MIN_CELL` (default 3, 1 to 50) applies to every cell: a dimension value with fewer than `MIN_CELL` views in a day (per bot class) is folded into `other` for that dimension; a `view_daily` cell (page, source, country, bot class) under it folds its referrer or `utm` label into `other` first and then its country into `ZZ` (direct and internal visits only lose the country); geography folds a city into its region and a region into its country under `max(5, MIN_CELL)`. Totals always sum to the raw count. **This is label suppression, not k-anonymity.** One-view cells can still remain inside `other` and `ZZ`; the exact per-day and per-class totals in the aggregate tables and `view_rollup_log` are exact; and anyone with read access to the D1 database can read the raw rows for the whole raw retention window. So folding protects dashboards and exports, not people with database access. The tradeoff is privacy against detail: a rare browser, language, country or referrer cannot be singled out of a small day's published numbers, but a small site sees a large share of `other` and `ZZ`, and a long tail is never shown. `MIN_CELL=1` turns it off. Downstream readers must accept `other` as a value in every dimension, including the closed ones such as `reason`, `os` and `net`. The log table keeps overall per-day totals only (no per-class totals) for reconciliation; do not export more than a dashboard needs.

**Lease.** A run takes a lease in `view_meta` (`rollup_lease`, a random token and an expiry 10 minutes ahead) with one atomic upsert that only replaces an expired lease; a run that cannot take it does nothing and does not touch the heartbeat. The lease is renewed inside each day's read batch (no extra query). Every statement in a day's write batch is guarded by "the lease token is mine AND its expiry has not passed" (compared with a bound `now`, not SQL time) and by the day's state (no log row yet for a fresh day, or the log row still showing the `max_id` this run read for a re-roll), so an overlapping run, or a run whose lease expired even with no takeover, writes and deletes nothing; the checkpoint is only counted as sealed when it changed a row. The lease is released in the heartbeat batch (and is harmless if left behind: it expires). Idle hours take no lease.

**Migrations 0005 to 0007 go together.** The maintainer has ACCEPTED the risk that migration 0007 deletes rollup data, because only migration 0001 has ever run on a live database (his test database), so nothing it deletes was ever real history. Apply 0005, 0006 and 0007 together, on a database that has never run a page-view rollup. Only migration 0001 has ever been applied to a live D1 (the maintainer's test database); 0005 to 0007 have never shipped, so applying them loses no history. Migration 0007 deletes every row of `view_daily`, `view_dim_daily`, `view_geo_daily` and `view_rollup_log` (the old rollup left no record of which raw rows it counted, and guessing could purge an uncounted row). **A database that already ran an earlier rollup version must not use this migration: take an export first (`npx wrangler d1 export`), because days whose raw rows have expired would lose their aggregates.** SQL migrations cannot refuse to run, so this rule is the control.

**Migration 0008 (sites).** It adds a `site` column, rebuilds the aggregate tables and the rollup log with `site` first in the key (every existing row becomes site `links`), rounds raw timestamps to the hour, clears the raw network class and **empties `visitor_seen`**. Daily visitor codes now include the site, so the old site-less codes would count a returning visitor twice that day. Apply it right after 00:05 UTC (the run that closes the day) so the overcount lasts at most one hour of one day. It is forward only; export first (`npx wrangler d1 export`) if you want a way back. Set `SITES` (see `wrangler.toml`) or keep `SITE_HOST`, `PAGE_IDS` and `LINK_IDS`, which still describe the one site `links`. Each accepted view now writes about nine D1 rows (counters, the event, two indexes, the daily code); watch the free-plan write quota. `view_meta('config_err')` shows closed codes if the site config has a problem.

**Late rows (full re-roll).** The log row records `max_id`, the highest raw row id the day's aggregates count, and `rerolls`. A raw row of a sealed day written afterwards has a larger id. The raw purge deletes a rolled day's rows only up to that `max_id`, so a late row is never deleted as if it had been counted. Folding is not additive (a 3-view named cell plus one late view, top-N order, caps), so a late row is never added as a delta: the next run **re-rolls the whole day** from all its raw rows and replaces the day's aggregate rows and log row in one transaction (`max_id` advances, `rerolls` goes up). The state is explicit: no log row means fresh; a log row with raw rows above its `max_id` means re-roll. A re-roll needs every originally counted raw row to still exist. If the purge already took some (the day is older than `RAW_KEEP_DAYS`), the day cannot be recomputed, so the late rows are **dropped**: `max_id` advances so they are not found again, the aggregates stay as they were, and `rollup_late_dropped` counts them. Counted re-rolled rows are `rollup_late_rows`.

**Retention order.** The view purge deletes a raw row past `RAW_KEEP_DAYS` only if its UTC day is in `view_rollup_log` and the row's id is at most that day's `max_id`. A day that has not rolled up is kept until the hard ceiling of the smaller of 3 times `RAW_KEEP_DAYS` and 90 days (21 days by default); after that it is dropped and `view_meta('rollup_overdue')` is set to the date. The flag stays for the rest of that UTC day and is cleared by the first later run that finds nothing left beyond the ceiling. `visitor_seen` rows (the pseudonymous daily hash) are deleted as soon as the UTC day ends, whatever the rollup state: the rollup never reads that table, because uniques come from the raw `first` flags. Aggregate and log rows are deleted after 90 days. Hourly counter keys are `bud:<hour>:<route>` and `cap:<hour>:<hash>` with the hour zero-padded, so expiry is a range delete on the primary key. A run that stops with delete work left (batch rounds or query budget used up) records `purge_backlog` in `view_meta` and the next run continues.

**Write budget (D1 free plan, 100,000 rows written per day).** The accepted-view budgets are 120 human plus 30 bot views per hour, so at most 150 x 24 = 3,600 raw views a day. The rollup writes one row per aggregate row. Worst case per day: 500 (core) + 324 (dim) + 300 (geo) + 1 (log) = 1,125 rows, and the 90-day purge later deletes the same rows, so about 2,200 a day (well under the 7,200 Plan 4 P-6 allowed). The caps are hard: more distinct values fold into `other` or `ZZ`, they never add rows. A re-roll rewrites its day (up to 1,125 rows plus the same deleted) and happens only when late rows exist. The hourly cron adds an idle-hour cost of about 9 rows (heartbeat keys) and, on a day with work, 2 lease rows: roughly 250 a day. Together with the per-view writes in P-6 (about 20,160) the page-view feature stays near 22,700 of the 100,000. A backfill is bounded by the query budget (about 6 days per default invocation, about 6,600 rows).

**Heartbeat.** After every run the Worker records, in `view_meta`, `rollup_last_ok` (epoch milliseconds, set only on success), `rollup_days`, `rollup_rows`, `rollup_late_rows` (late rows re-rolled), `rollup_late_dropped`, `rollup_reduced_days` (this run), `rollup_err` (`none`, `db` or `mismatch`; no free text), `rollup_failed_day:<site>` (the first day that failed for that site, cleared by the next clean run) and `rolled_through:<site>` (the last UTC day that is final for that site; one pair of keys per site, `links` for a single-site setup). To check it:

```sh
npx wrangler d1 execute reach-around-clicks --remote \
  --command "SELECT key, value FROM view_meta WHERE key IN ('rollup_last_ok','rollup_days','rollup_rows','rollup_late_rows','rollup_late_dropped','rollup_reduced_days','rollup_err','rollup_overdue') OR key LIKE 'rollup_failed_day:%' OR key LIKE 'rolled_through:%'"
```

Convert `rollup_last_ok` with `date -u -d @$((VALUE/1000))`. More than about 2 days old, or `rollup_err` other than `none`, or any `rollup_overdue` row, means the rollup is stuck: raw rows are being kept (up to 21 days) instead of purged, and no aggregate is being written. Check the Worker's scheduled logs, fix, and the next run catches up. To see which days are done: `SELECT day, raw_views, views, uniques FROM view_rollup_log ORDER BY day DESC LIMIT 10`.

**CPU on the Workers Free plan (cannot be measured in the tests).** What Cloudflare documents (read 2026-10-24): the Workers limits page (`developers.cloudflare.com/workers/platform/limits/`, last updated Oct 12, 2026) says "CPU time measures how long the CPU spends executing your Worker code. Waiting on network requests (such as fetch() calls, KV reads, or database queries) does not count toward CPU time." and lists CPU time per Cron Trigger as 10 ms on Free and, on Paid, 30 seconds for a trigger interval under 1 hour and 15 min for an interval of 1 hour or more (this Worker's hourly trigger counts as 1 hour). The D1 limits page (`developers.cloudflare.com/d1/platform/limits/`, last updated Oct 24, 2026) says "Operations on a D1 database, including query execution and result serialization, run within the Workers platform CPU and memory limits. Exceeding these limits, or hitting other platform limits, will generate errors." The two statements together do not tell us how much of a D1 call counts as CPU, so this document does not claim D1 work is free. vitest runs under miniflare and cannot measure Worker CPU, so everything below is an argument, not a measurement. What the design does to stay small: ranking, top-N, small-cell folding, the row caps, the UTM allowlist and the geography level run in D1 (SQL with window functions), not in JavaScript; the Worker receives at most 500 + 324 + 300 = 1,124 output rows per day (a few hundred on a normal day), sums them to check the totals and builds 10-row insert statements from them (about 110 statements for a full day). It never sorts, groups or merges raw-sized data. Result serialization still happens, which is why input size is bounded too: a day with more than `ROLLUP_MAX_ROWS_PER_DAY` raw rows (default 3000, ceiling 20000) is rolled in a reduced mode (referrer top-N 10, geography at country level, counted in `rollup_reduced_days`, for fresh days and re-rolls alike), and one invocation will not take another day once it has used `4 x ROLLUP_MAX_ROWS_PER_DAY` input rows. The purge runs before the rollup, so even if the rollup is cut off, retention has already happened. **After the first deploy, watch `npx wrangler tail` (or the Workers observability logs) for `exceededCpu` and error 1102.** If you see it, either move to the Workers Paid plan or lower the volume: a smaller `VIEWS_PER_HOUR` and `BOT_VIEWS_PER_HOUR`, a smaller `ROLLUP_MAX_ROWS_PER_DAY`, or a smaller `TOP_SOURCES`. A cut-off invocation does not reach its catch or heartbeat, so a stale `rollup_last_ok` is the symptom to look for.

**Identifying labels.** Aggregates keep labels, and a label can identify a person: a referrer host such as `alice-smith.example.com`, a `utm_source` such as `john-doe`, or a small town's city name. MIN_CELL hides rare ones in a small day, not all of them. Set `UTM_ALLOWLIST` (comma-separated lower-case labels, for example `bluesky,mail,newsletter`) so any other `utm_source` is stored in the aggregates as `other`; it is applied at rollup, so the raw rows (kept for `RAW_KEEP_DAYS`) still hold the original label. There is no referrer allowlist; referrer hosts are bounded by `TOP_SOURCES` and `MIN_CELL`. The hourly cap keys in `view_meta` (`cap:<hour>:<hash>`) are keyed hashes of a client address and are kept as they are (deleted after the previous hour). Say all of this in the privacy text.

**Geography is stored.** By the maintainer's decision `GEO_LEVEL` defaults to `city`: country, region code and city name are written to the raw rows (kept for `RAW_KEEP_DAYS`, default 7, ceiling 90) and, folded for small counts, to the daily geography aggregate. Set `GEO_LEVEL=region` to drop the city, or `GEO_LEVEL=country` to store the country only; in `country` mode no region or city is stored anywhere (a test dumps the whole database to prove it). Say this plainly in the privacy text.

**Atomic accept.** An accepted view is one D1 batch: the client counter, the global counter, the event row and the visitor row are written together only if both the global budget and the client cap have room, otherwise nothing is written; a failure rolls the whole batch back. After the budget is spent, extra clients create no rows.

**Timing.** Every outcome is the same empty 204 with the same headers, so responses are byte-uniform, but they are not timing-uniform: an early refusal (wrong method, unknown page, GPC) returns faster than an accepted view, which makes database calls. A patient observer can tell stages apart. This is accepted: nothing secret is revealed, the page ids and routes are public. The body read has a fixed 2 second deadline and then gives the same 204.

The route stores no address, user agent or referrer URL. After a deploy, one beacon from a browser should give a 204 and one `view_events` row with categories only; the same request with `Sec-GPC: 1` should leave no row.

## Kit limits and the free-plan budget

The media kit Worker is sized for the Cloudflare free plan, and the ruling is: **when a limit is reached the kit goes unavailable; it never costs money.** Every limit below fails closed. A request over a route budget, a lead or event over its cap, an alert or email over the daily budget, and any invalid setting all get a refusal, never a pass. No paid feature is switched on by any of these settings.

All values are plain decimal integers (digits only, no sign, space, exponent or unit). An unset or empty value takes the default. Any other value is rejected as a whole: every limit then refuses until it is fixed. Nothing is clamped or guessed, and a rejected value is never printed, only its variable name.

| Variable | Range | Default | Meaning |
|---|---|---|---|
| `ROUTE_BUDGET_GATE` | 0 to 100000 | 150 | Gate requests per UTC hour |
| `ROUTE_BUDGET_VIEW` | 0 to 100000 | 300 | Gated-page views per UTC hour |
| `MAX_LEADS` | 1 to 50000 | 5000 | Stored leads; at the cap a new address is refused, a known one still works |
| `MAX_KIT_EVENTS` | 1 to 500000 | 50000 | Stored kit events; at the cap events are skipped |
| `DAILY_ALERT_BUDGET` | 0 to 1000 | 20 | Owner alerts per UTC day |
| `DAILY_SEND_BUDGET` | 0 to 1000 | 0 | Emails per UTC day (0: none; the send route is not in v1) |
| `D1_DAILY_WRITE_QUOTA` | 1000 to 999999999 | 100000 | The free D1 plan's daily rows-written figure. Re-check it against Cloudflare's current limits before relying on it |

**Worst-case daily writes.** The D1 write allowance is one per account, shared by the kit, page views, clicks, the rollup and every purge, so the check adds them all. Settings are checked when a limit is used, not at deploy: if the combined worst case is over `D1_DAILY_WRITE_QUOTA`, or any value is invalid, every reservation refuses (fail closed) until it is fixed. The loader also reports a warning above 80 percent. The kit part is `24 x (gate x 5 + view x 2) + alerts x 1 + sends x 7 + 10000`, where each number is the most rows one accepted operation writes (the row, its indexes and the counter) and 10000 is a reserve for the kit purge. To it are added the page-view feature with its rollup and purge (22700, from the page-view write budget above), a click reserve (8000; the click route has only a per-client limiter and no global budget, so this is an allowance, not a measured bound) and a margin (5000). A refused request writes nothing, so a flood over a budget costs reads only.

| Part | Budget | Writes each | Worst case per day |
|---|---|---|---|
| gate | 150 per hour | 5 | 18000 |
| view | 300 per hour | 2 | 14400 |
| alert | 20 per day | 1 | 20 |
| send | 0 per day | 7 | 0 |
| kit purge reserve | | | 10000 |
| page views, rollup and purge | | | 22700 |
| clicks (reserve) | | | 8000 |
| margin | | | 5000 |
| **Total** | | | **78120** |

That is 78.1 percent of the default quota of 100000. Raising a budget raises this number; raise `D1_DAILY_WRITE_QUOTA` only on a plan that allows it. Counters live in the `counters` table, one row per route and hour or per day, and are purged after two days. A viral spike over a budget makes the gate unavailable until the next hour; that is the accepted trade.

**Lead retention.** `LEAD_RETENTION_DAYS` (1 to 365, default 30; digits only, an invalid value falls back to 30 and is logged as a fixed line, it is not part of the table above because it never closes the gate) sets how long a lead is kept. Each hourly run is ONE metered D1 query (a batch) that deletes at most 80 `kit_events` of expired leads, then at most 20 expired leads that have no events left (a lead goes only after its events, so the cascade never deletes events in bulk and a lead with many events is purged over several runs), and writes the heartbeat. Estimated writes per run: 20 x 4 + 80 x 3 + 1 = 321 (a lead row and its three indexes, an event row and its two indexes; an estimate, not measured on D1), so 24 runs are 7704 a day, within 80 percent of the 10000 kit purge reserve above; the rest is left for the counter purge. **Budget guarantee:** at `QUERY_BUDGET` 9 or more the lead purge runs first and always gets its one query, and the rest of the jobs share the remainder (the rollup gets the budget minus one). Below 9 the rollup wins (8 is the smallest budget in which one rollup day completes, `rollup_budget.test.ts`), the lead purge runs last on whatever is left with no guarantee, and logs `purge` / `over_budget` when it gets none; keep `QUERY_BUDGET` at 9 or more (default 40). It never runs on the 2-minute live cron. Details and the owner deletion command: [privacy.md](privacy.md).

**For the route code that uses these limits:** run the per-client throttle (`checkIpMinute`) before taking from a global route counter, so one client cannot spend the shared budget. A request refused by the throttle must not touch a counter.

## Secrets and Doppler

Project secrets (this Cloudflare token, later the Mailgun and collector
tokens) live in the Doppler project `reach-around`, with configs `dev`, `stg`
and `prd`, plus a `ci` config for the deploy credentials only. For a local
deploy, read the token from the `ci` config:

```
doppler run --project reach-around --config ci --only-secrets CLOUDFLARE_API_TOKEN -- <command>
```

The deploy workflow reads the token from Doppler over OIDC, and falls back to
a GitHub `test` environment secret named `CLOUDFLARE_API_TOKEN` when Doppler is
not configured. Never paste a token into chat, issues or commits.

**Where the token is available.** The Doppler action exports every secret in
the config it reads into the job environment (`GITHUB_ENV`). From the Doppler
step on, the Cloudflare token is therefore inherited by every later step and
by every action's post-run hook, whether or not that step lists it in `env:`.
It is not limited to the wrangler steps. Narrowing it to the steps that need it
waits on git-your-ship-together#207. Until then the workflow keeps every
package install before the Doppler step, and a test fails if an install or an
unlisted action is added after it. That test checks order only; the YAML cannot
prove the inherited-environment boundary.

**The token is account-wide.** It covers Pages, D1, Workers Scripts and zone
routes for the whole account, not just this project. Rotation keeps two such
tokens valid at once; if you suspect a compromise, revoke both immediately in
Cloudflare, then issue a new one. Per-operation tokens (one each for D1, the
Worker and Pages) or a dedicated Cloudflare account for this project is the
real isolation. That is an owner decision, tracked in issue #206.

**Service-token fallback.** The `DOPPLER_TOKEN` secret is emergency-only. It is
a long-lived credential stored in GitHub; do not leave it set while OIDC works.

**CI config.** The Doppler action exports every secret in the config it reads
into the rest of the job, so CI reads a dedicated config named `ci` that holds
only the deploy token (`CLOUDFLARE_API_TOKEN`). Runtime secrets such as the
Mailgun key stay in `stg` and `prd`. The optional `CLOUDFLARE_PURGE_TOKEN` is
not in `ci` either: it stays a GitHub `test` environment secret so it reaches
only the purge step and never the wrangler steps. To rotate the token automatically, add a Doppler Cloudflare
Tokens rotated secret in `ci` (it injects `CLOUDFLARE_KEY_NAME` and
`CLOUDFLARE_KEY_VALUE`) and set `CLOUDFLARE_API_TOKEN` to
`${CLOUDFLARE_KEY_VALUE}`. Create the two tokens it rotates with identical
permissions, as in the token table above.

**Identity.** Create a Doppler service account with Viewer on `reach-around`
config `ci` only, and an OIDC identity on it with issuer
`https://token.actions.githubusercontent.com`, audience
`https://github.com/<owner>`, and one subject. The job runs in the `test`
environment, so the subject ends in `environment:test`, not a branch ref.
Register only the subject form this repository actually emits, not both: an
extra form is an extra way in. Check which form is in use:

```
gh api repos/<owner>/<repo>/actions/oidc/customization/sub
```

This repository uses the immutable form (it includes the numeric owner and
repository ids, so a renamed or recreated repository cannot reuse it):

```
repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:test
```

If the command shows the default form instead, register
`repo:<owner>/<repo>:environment:test` alone.

Put the identity UUID in the repository variable `DOPPLER_IDENTITY_ID`. A
Doppler service token in the secret `DOPPLER_TOKEN` also works, but it is a
long-lived credential in GitHub; use it only in an emergency and prefer OIDC.

## Daily follower counts (test deployment)

`deploy-test.yml` also runs once a day (06:23 UTC) and by hand. Its `counts`
job runs first, refreshes the numbers, and hands one `numbers.json` to the
build, which reads it with `--numbers`. Every deployment, manual or scheduled,
therefore carries fresh numbers. The job can only read the repository; the keys
reach one step only (`Refresh the counts`), after every install step.

**As shipped, this refreshes nothing.** `examples/instance/site.yaml` has no
`numbers:` block and no platform items, and there is no
`examples/instance/numbers.json`. Until the owner adds them, the job writes an
empty numbers file and the page shows no follower statistic. The owner must add:
the `numbers:` block naming each source with its real public handle, the
platform items that display the numbers, and a hand-kept
`examples/instance/numbers.json` for platforms with no source. This repository
does not invent those handles.
What the job reads from `examples/instance/site.yaml`: the `numbers:` block.
With no block it only passes through the hand-kept file, if there is one.
Hand-kept platforms (TikTok, Instagram, X, Threads, LinkedIn, Kick: the engine
has no source for them) stay in that file with their `as_of` date; the run
prints a warning for every entry older than 30 days.

To update a hand-kept number, read it from the platform, then edit
`examples/instance/numbers.json` by hand: change that platform's `count` (a
whole number) and set `as_of` to the time you read it, written like
`2026-11-07T12:00:00Z` (a real date, not in the future). Commit it; the next
deployment picks it up. The 30 day rule: an entry whose `as_of` is more than 30
days old triggers a warning in the `counts` job, in the build and in
`reach-around check`; the number is still shown until you update it. A fetched
number always wins over a hand-kept one while it is fresh (see
[the numbers file](config.md#the-numbers-file)). The same rule applies to a
`count` and `as_of` typed on a platform item in `site.yaml`.

**Keyless sources** (Mastodon-compatible servers, Bluesky, GitHub, Discord
invites, Matrix) need nothing.

**Keyed sources** read `YOUTUBE_API_KEY`, `TWITCH_CLIENT_ID` and
`TWITCH_CLIENT_SECRET`. Pick where they come from:

1. **GitHub Actions secrets (default).** Add the secrets of those names under
   Settings > Secrets and variables > Actions. Nothing else to set. A name you
   do not set is empty and its source is skipped.
2. **Doppler over GitHub OIDC (opt in).** Set three repository **variables**:
   `DOPPLER_COUNTS_IDENTITY_ID` (the UUID of a Service Account Identity made
   for this job alone; not the `DOPPLER_IDENTITY_ID` the deploy job uses),
   `DOPPLER_COUNTS_PROJECT` and `DOPPLER_COUNTS_CONFIG`. There is no default
   project or config: use a dedicated project that holds social-media
   credentials only, not the shared `ci` project. The Doppler step runs only
   when all three are set; otherwise the GitHub secrets above are used. No
   long-lived token is stored. `COUNTS_SECRET_NAMES` (optional variable,
   comma-separated) limits which names are fetched; the default is the three
   above, and a name missing from the Doppler config fails the run. The values
   come back as one masked step output read by the refresh step; nothing is
   written to the job environment. A value in Doppler wins over a GitHub secret
   of the same name.

**Trust policy for the Doppler identity.** Create a separate Service Account
Identity for this job, never reuse the one the `ci` project uses. The `counts`
job runs in its own GitHub environment, `counts-secrets`. Create it under
Settings > Environments, restrict its deployment branches to `main`, and
optionally add a required reviewer (a scheduled run then waits for approval).
Set the identity's subject to the environment form,
`repo:OWNER/REPO:environment:counts-secrets` (in the immutable form described
above if your repository uses it). **That subject does not name a workflow.**
Any workflow in this repository that requests `id-token: write` and targets the
`counts-secrets` environment on an allowed branch gets the same subject and can
assume the identity.

Doppler's Service Account Identities page (docs.doppler.com, read 2026-11-07)
says an identity validates every claim rule you configure, that custom claims
can be added for a provider, and that each claim can list several accepted
values. GitHub's token also carries a `job_workflow_ref` claim (the workflow
file and ref). Adding it as a custom claim, with the value
`OWNER/REPO/.github/workflows/deploy-test.yml@refs/heads/main`, would narrow the
identity to this workflow file. Verified: Doppler supports custom claim rules.
Inferred, not tested here: that `job_workflow_ref` is accepted and matches. Try
it and confirm a run still authenticates before relying on it.

Even with that claim, the real limit is write access to `main`: whoever can
change the workflow on `main` can change what the `counts` job does. What limits
that: the `counts-secrets` environment restricted to `main`; a required reviewer
set by the owner on that environment; CODEOWNERS covering `.github/workflows/`
with branch protection requiring code-owner review, so a new workflow that can
target the environment needs review; and the identity's read access limited to
the dedicated social-media project and config. Those are owner settings, not
something this repository enforces. The job that can request the
token (`counts`) installs wheels only (`--no-index --only-binary`); the job that
builds from source (`wheels`) has no `id-token` permission and no keys. The one
step that holds the keys is dependency code that runs with them, so the Doppler
config must hold only the count-source keys. The keys cannot go to another job:
GitHub drops job outputs that contain masked values, so the fetch and the
refresh stay in one job.

**Engine wheel check.** The `wheels` job publishes the SHA-256 of the engine
wheel as a job output (not a secret). The `counts` job recomputes it from the
downloaded artifact and stops on a mismatch, then installs that one file with
`--no-index --no-deps`. Its dependencies are installed with `--require-hashes`.
This catches a swapped artifact; it does not make the same-run build trusted by
itself.

**Residual risk.** Anyone with write access to `main` already controls the
workflow, so they can change what runs in the `counts` job. What limits the
damage: branch protection on `main`, the `counts-secrets` environment
(branch-restricted, optional reviewer), a Doppler identity that can read only
the social-media project, and keys that are read-only API keys for public
counts. The `counts` job's harden-runner stays in audit mode because its hosts
cannot all be listed: the artifact blob host changes per run and the Mastodon
and Matrix hosts come from the config (see Egress control).

**Skipping the counts.** A manual run has a `skip_counts` input (default off).
Turn it on to redeploy with the `numbers.json` committed beside the config when
a count source or the Doppler step is down. The wheels and counts jobs do not
run, and the build does not wait for them. A scheduled run never skips.

**When the counts job fails.** A failed `counts` job does not stop the build or
the deployment (issue #317). The build downloads nothing from that job and
uses the `numbers.json` committed beside the config, the same path as
`skip_counts`, so the page keeps the last committed numbers. A failed `wheels`
job, a run on any ref other than `main`, and a cancelled run still do not
build. When a scheduled run's `counts` or `wheels` job fails, the `notify` job
opens one issue titled "Counts refresh failed", or adds a comment to the open
one with the run link, the date and the wheels and counts results; it never
opens a duplicate. It waits for those two jobs only, not for the build or the
deploy: the deploy job can sit pending on the `test` environment's reviewer, and
the alert must not wait for that approval. If listing the open issues fails, the
step fails (red) instead of opening a possible duplicate. A failed `wheels` job also skips the build and the deploy (the site
stays as last deployed), which is why it raises the same alert. The job can only write issues and
the text holds only values GitHub provides. A manual run raises no issue: the
person who started it sees the red job. Close the issue once the counts work
again; the next failure opens a new one.

A 404 from the Doppler step in the counts job means the counts identity cannot
see the project and config named in `DOPPLER_COUNTS_PROJECT` and
`DOPPLER_COUNTS_CONFIG`: the identity needs access to that project and that
config. Fix the access in Doppler, or unset the three variables to fall back to
GitHub Actions secrets. Until then the site builds with the committed numbers
and the alert issue stays open.

**When the counts stop.** GitHub disables scheduled workflows after 60 days
without repository activity, and nothing alerts you. The run also warns when a
hand-kept entry's `as_of` is over 30 days old, but only while the job runs.
If the numbers look frozen, check the Actions tab for a disabled schedule and
re-enable it.

A scheduled run waits for the `test` environment's reviewer on the `deploy` job
if one is required (section 2); remove the required reviewer there if the test
site should update unattended. A source that fails keeps its old number and
shows as a warning; the deployment continues.

## Egress control

The jobs start with `step-security/harden-runner`, followed by an egress
canary that says whether a failure is a broken runner network or a wrong list.

- **`deploy` is in block mode** with an explicit allow-list of `host:443`
  entries (no wildcards): GitHub, the npm registry, `api.doppler.com` and
  `api.cloudflare.com`. This is the job that holds the Cloudflare token.
- **`counts` is in audit mode** for two reasons that cannot be listed: the
  wheels artifact comes from a blob host whose name varies per run, and the
  Mastodon and Matrix sources use hosts named in the config. It needs no PyPI
  host, because it installs from the artifact. The fixed hosts it uses are
  GitHub, the Doppler API, and per source kind `public.api.bsky.app`,
  `discord.com`, `www.googleapis.com`, `id.twitch.tv` and `api.twitch.tv`. The
  script also hands the refresh only a short list of environment variables, so
  the runner's tokens never reach the engine.
- **`build` is in audit mode.** Artifact upload goes to a blob host whose name
  varies per run and cannot be listed without a wildcard, so a block-mode list
  would stop every build at upload. The build job holds no secrets and installs
  from hashed lockfiles. Audit records every destination instead of stopping it.

**The deploy allow-list is unmeasured.** It comes from what each step is known
to reach, not from a recorded run. An unmeasured allow-list fails safe: a
blocked host is named in the log; add it to the job's `allowed-endpoints` and
re-run.

**Measuring the build hosts.** After a build run, open the run's harden-runner
report (the step summary or the StepSecurity link in the job log) and read the
outbound destinations. If the artifact host turns out to be a stable set of
names, add them as `allowed-endpoints` with no wildcards, change
`egress-policy` to `block`, and re-run. If the names still vary per run, leave
the job in audit.

To store the token, let Doppler read the value from its prompt instead of
putting it on the command line (a value typed as an argument lands in your
shell history and the process list):

```
doppler secrets set CLOUDFLARE_API_TOKEN --project reach-around --config ci
```

Do the same for `dev` and `prd` as needed. If the Doppler CLI nags about a
newer version and a non-interactive run fails because of it, add
`--no-check-version` to the command (for example
`doppler --no-check-version run ...`). `--only-secrets` hands the child
command only the secrets you name, so an unrelated secret in the config never
reaches wrangler.

## Pages or Worker?

Workers & Pages > Create can produce either a **Pages** project or a
**Worker**, and some dashboard flows (a Git import, or a "Create application"
path that never offered Direct Upload) quietly make a Worker. The site must
live in a Pages project, because that is what `wrangler pages deploy` writes
to and what a custom domain has to point at.

How to tell, from the dashboard:

- The URL path contains `/pages/` for a Pages project and `/workers/` for a
  Worker.
- A Worker's overview page shows a **Worker URL** (`...workers.dev`). A Pages
  project shows deployments, a production branch and Custom domains.

Why it matters: the custom domain must be attached to the Pages project. A
domain attached to a placeholder Worker keeps serving that Worker, whatever
you upload to Pages, and `wrangler pages deploy` can even report success.

To fix it:

1. On the Worker, open Settings > Domains & Routes and remove the custom
   domain.
2. Delete the placeholder Worker. This is the Worker created by mistake, not
   the `reach-around-clicks` click counter that `wrangler deploy` creates
   later.
3. Create the Pages project with Direct Upload (section 1, step 1).
4. On the Pages project, Custom domains > Set up a custom domain, and add the
   hostname there.

## Production branch

`wrangler pages deploy ... --branch main` only counts as a production deploy
when the Pages project's production branch is also `main`. Otherwise
Cloudflare files every deploy as a **preview** (served on a branch alias such
as `main.reach-around-test.pages.dev`), and the custom domain keeps showing
the last production deployment, or nothing.

Check and change it: Pages project > Settings > Builds & deployments (the
label can differ between dashboard versions) > Production branch. It must read
`main`. Then re-run the deploy. In the project's Deployments list the new
deployment should be tagged Production.

## Token type

The Workers Routes permission (needed only when you set `ZONE_NAME`) is a
zone permission. In the current dashboard an **account-owned** token (Manage
Account > Account API Tokens) cannot carry zone permissions, so use a
**user** token: My Profile > API Tokens > Create Token.

1. Start from the "Edit Cloudflare Workers" template. It includes Workers
   Scripts and Workers Routes.
2. Add Account > Cloudflare Pages: Edit and Account > D1: Edit.
3. Under Account Resources include only your one account, and under Zone
   Resources only the one zone.
4. Set a TTL (an end date) so a forgotten token expires on its own. Add a
   client IP filter if your address is stable.

Check the token without printing it. These commands read it from Doppler (or
from an environment variable you already exported) and pass it to `curl`
through standard input, so it appears neither on screen nor in the process
list:

```
doppler run --project reach-around --config ci --only-secrets CLOUDFLARE_API_TOKEN -- \
  sh -c 'printf "header = \"Authorization: Bearer %s\"\n" "$CLOUDFLARE_API_TOKEN" \
    | curl -sS -K - https://api.cloudflare.com/client/v4/user/tokens/verify \
    | jq "{success, status: .result.status, expires_on: .result.expires_on}"'
```

`"status": "active"` means the token is valid. An account-owned token uses a
different endpoint, `https://api.cloudflare.com/client/v4/accounts/<account
id>/tokens/verify`. Outside Doppler, drop the first line and run the `sh -c`
part with the token already in `CLOUDFLARE_API_TOKEN` in your environment
(set it with `read -rs CLOUDFLARE_API_TOKEN && export CLOUDFLARE_API_TOKEN`
so it is not echoed). The verify call proves the token is alive, not which
permissions it has; a missing permission shows up as an error on the matching
wrangler step.

## A private deploy script

The workflow above suits a test hostname. For a real instance, many people
prefer to deploy from their own machine with a script kept outside the
repository, because the real ids, hostname and site config stay private.
[`docs/examples/deploy-private.sh`](examples/deploy-private.sh) is a
sanitized, generic version (placeholders only). It does this:

1. **Builds outside the repository**, into a private directory, with
   `reach-around build`. No token exists in the process during the build.
2. **Derives the link ids from the build manifest** (`jq -c '.links | keys'`),
   so the Worker accepts exactly the links the page publishes and the two
   cannot drift.
3. **Generates the wrangler config in the private directory** from
   `worker/wrangler.toml`, replacing the placeholder ids and making `main`
   and `migrations_dir` absolute because the file no longer sits in
   `worker/`.
4. **Checks the shape of every value** (UUID, number, hostname, project name,
   32-character account id, link-id array) before it lands in a file.
5. **Offers `--dry-run`**, which builds, writes the config and prints the
   plan, then stops before any wrangler command. It also asks you to type
   `deploy` to confirm unless you pass `--yes`.
6. **Gives the token only to the three wrangler commands** (migrations,
   Worker deploy, Pages deploy), each wrapped in `doppler run --only-secrets`.
   The script unsets any token inherited from your shell.

Try it with `--dry-run` first:

```
cp docs/examples/deploy-private.sh ~/private/deploy.sh
chmod 700 ~/private/deploy.sh
~/private/deploy.sh --dry-run ~/private/deploy.settings
```

How it differs from the CI workflow:

| | `deploy-test.yml` | private script |
|---|---|---|
| Where it runs | GitHub runner | your machine |
| Values | environment variables in GitHub | a private settings file |
| Token | `test` environment secret | Doppler, injected per command |
| Approval | environment reviewer | typed confirmation |
| Link ids | read from the build manifest (a `LINK_IDS` variable is optional, a comparison only) | read from the build manifest |
| Config file | `wrangler.deploy.toml` in `worker/` on the runner | in the private directory |
| Provenance | Actions run log | none beyond your shell history |

## Adding the route by hand

Clicks are counted only if `/e/*` on the site host reaches the Worker. If you
do not want the token to carry Workers Routes and Zone permissions, leave
`ZONE_NAME` unset and create the route yourself: dashboard > Workers & Pages
> the `reach-around-clicks` Worker > Settings > Domains & Routes > Add >
Route. Enter the route `links-test.example.invalid/e/*` and pick the zone.

The deploy leaves a hand-made route alone: without `ZONE_NAME` the generated
config has no `[[routes]]` entry, so `wrangler deploy` does not manage routes
and does not remove yours. (If you later set `ZONE_NAME`, wrangler will try to
create the same route; remove the hand-made one first if it complains.)

## Caching and stale files

The rule: **a page you change shows at once, and a file is cached for a year
only because its name changes whenever its content does.**

| Class | Files | `Cache-Control` |
|---|---|---|
| Hashed | everything under `static/` (CSS, JS, icons, fonts) and every image or font file your config names, for example `static/site.3fa9c1d2ab4e5f607182.css`, `media/mascot.73e9847dc81a2b3c4d5e.svg` | `public, max-age=31536000, immutable` |
| Stable | `index.html` and every page, `404.html`, `robots.txt`, and a root-level `apple-touch-icon.png` (a file a browser asks for by name; the build also keeps a root-level `favicon.ico` unrenamed, though the config cannot name one today) | `public, max-age=0, must-revalidate` |

The hash is the first 20 hex digits (80 bits) of the SHA-256 of the file's final bytes.
CSS is hashed after its `url()` references are rewritten to the hashed names,
so changing one icon renames the stylesheet that uses it, and the page that
links the stylesheet changes with it. Nothing else is renamed. Because HTML is
revalidated on every request, a returning visitor gets the new page, which
names the new files, which are not in any cache: a miss, by construction. The
old files stay at their old names for any page still open.

**How Cloudflare Pages applies `_headers`.** Read from the Pages docs
(Configuration > Headers): every rule whose pattern matches a URL applies, a
header set by two matching rules is joined with a comma, `! Header` detaches a
header an earlier rule set, a splat (`*`) matches greedily, and a file may hold
at most 100 rules. A catch-all revalidate rule plus a narrower immutable rule
would therefore send both values for a hashed file. So the build uses no
catch-all: the immutable patterns (`/static/*`, and a `/<folder>/*` for each
folder of your images when every file in it is hashed, otherwise one exact rule
per file) and the revalidate patterns (each page at its folder and `index.html`
URL, `404.html`, `robots.txt`, stable files) are listed so that no URL matches
both, and no rule relies on a detach. A URL in neither list, such as an unknown
path, gets the host default, which is also `max-age=0, must-revalidate`. A test
checks the no-overlap property on every built example. The build stops with a
clear message if a site would need more than 100 rules.

**Not verified by the tests.** They check the `_headers` text, not a live
Pages response. After the first deploy, check one of each class:

```
curl -sI https://YOUR-HOST/ | grep -i cache-control
curl -sI https://YOUR-HOST/static/<the hashed css name from the page> | grep -i cache-control
```

If the zone's **Browser Cache TTL** setting is a fixed value, Cloudflare can
raise `max-age` on files it caches by extension (CSS, JS, images, `robots.txt`).
That is how a stale four-hour window can appear on a file. Hashed files do not
care, since their name changes; for the stable files set Browser Cache TTL to
**Respect Existing Headers**.

### If a file is stale anyway

- A **hashed** file cannot be stale: a different name is a different URL. If a
  page looks wrong, hard-reload once; the page links the right names.
- A **stable** file (a page, `robots.txt`, `404.html`) is revalidated, so it is
  at most one request behind. If the zone setting above holds an old copy, purge
  that URL (dashboard: Caching > Configuration > Purge Cache > Custom Purge).
- A file you **removed** from the build (the first deploy published a
  `/manifest.json` that later builds no longer have) was seen still served from
  the edge for hours on the live test site (issue #155). Purge it by URL, or use
  the optional step below.

### The optional purge step

This is a backstop for paths that already exist at the edge. Nothing depends on
it. The deploy workflow runs it only when both of these are set in the `test`
environment, and skips it with a notice otherwise:

| Name | Kind | Value |
|---|---|---|
| `CLOUDFLARE_PURGE_TOKEN` | secret | an API token with exactly one permission: **Zone > Cache Purge > Purge**, limited to **one zone** (Zone Resources: Include > Specific zone > your zone). Not the deploy token, and it can do nothing else. |
| `ZONE_ID` | variable | the zone id (dashboard > the zone's Overview, right-hand column). Not a secret. |

Create the token under My Profile > API Tokens > Create Token > Custom token.
Keep it in Doppler or as a GitHub secret, never in git or a chat:

```
doppler secrets set CLOUDFLARE_PURGE_TOKEN --project reach-around --config stg
```

For the GitHub workflow, add it as an environment secret on `test`
(Settings > Environments > test > Add secret) and `ZONE_ID` as an environment
variable. The token is in the last step's environment only, and the script gives
it to `curl` on standard input as a header file, so it is never on a command
line or in a log.

How it works:

1. The `build` job writes `site-out.assets.json` beside the site (never in it): every
   published path with its SHA-256, size and class (`hashed` or `stable`). It is
   uploaded as the artifact `site-assets-inventory`, kept 90 days.
2. The same job downloads that artifact from the **latest successful** earlier
   run (`gh run list` and `gh run download`, in a step that holds the built-in
   read-only token and runs nothing else) and then, in a separate step with no
   token, runs `reach-around deploy-purge-list --previous OLD --current NEW`.
   The inventory is read as untrusted input (strict JSON, size cap, plain paths). The list has
   the paths that were removed, the stable files whose bytes changed, and always
   `/`, every page, `/404.html` and `/robots.txt`. A changed hashed file needs
   no purge, since its new name is a new URL. With no earlier deployment (the
   first run), or an unreadable inventory, the list is empty and the step
   reports a notice.
3. After the Pages publish, `.github/scripts/purge-cache.sh` posts the list to
   `POST https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/purge_cache` as
   `{"files": [...]}`, 30 URLs per request (the script's own limit; the current
   Cloudflare docs allow 100 for a single-file purge), https only, with the token
   in the `Authorization` header. It checks `"success": true` in each response.
   The work is bounded because the list came from an earlier run: at most 300
   URLs per deploy (the list puts the entry points first, so they are kept), a
   cap you can raise with `PURGE_MAX` up to a hard ceiling of 1000, and a total
   time budget of 240 seconds (`PURGE_BUDGET_SECONDS`, ceiling 600). Paths past
   the cap are counted in a warning, never sent, and so is any path over 2000
   characters. The JSON body goes to `curl` on standard input and the token in a
   private 0600 header file under `RUNNER_TEMP` that is removed on exit, so
   neither is on a command line.
4. A failure is a warning annotation and a line in the job summary. It never
   fails the deploy.

To run the comparison by hand: build twice and run
`reach-around deploy-purge-list --previous old.assets.json --current new.assets.json`.
The inventory comes from `build`; `--assets-inventory PATH` moves it (it is
refused inside `--out`, like `--manifest`).

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| A file looks old after a deploy | A removed path still cached at the edge, or the zone's Browser Cache TTL holds a stable file | See "Caching and stale files": purge the URL, set Browser Cache TTL to Respect Existing Headers, or set up the optional purge token. |
| The custom domain serves old or different content | The domain is attached to a Worker, not the Pages project; or the deploy went to a preview because the production branch is not `main` | See "Pages or Worker?" and "Production branch". Check the newest deployment is tagged Production. |
| Route rejected: "No access to the specified resource" | The token lacks Workers Routes: Edit or Zone: Read on that zone, or it is an account-owned token, which cannot carry zone permissions | Use a user token with the zone permissions (see "Token type"), or leave `ZONE_NAME` unset and add the route by hand. |
| `d1 migrations apply` says there is nothing to apply | Every migration is already recorded in the database | Normal on a repeat deploy. To check, run `wrangler d1 migrations list DB --remote --config wrangler.deploy.toml`. |
| Pages upload says files are already uploaded | Pages stores files by content hash and skips the ones it has | Normal and harmless. A deployment is still created. If the site looks stale, check the production branch. |
| `doppler` fails on an update prompt in a script | The CLI asks about a newer version and there is no terminal to answer | Add `--no-check-version`, or update the CLI. |
| A GitHub runner stalls (queued, or stuck on one step) | A hosted-runner hiccup. The deploy concurrency group does not cancel in-progress runs, so a stuck run also blocks the next | Cancel it (Actions > the run > Cancel workflow, or `gh run cancel <run id>`), then `gh run rerun <run id>` or start Run workflow again. |

## 3. Run it

Actions > Deploy to test > Run workflow, branch `main`. The workflow has two
jobs:

1. **`build`** has read-only repository access, no secrets and no
   environment. It installs the package, runs
   `reach-around build --config examples/instance/site.yaml --out site-out`,
   and uploads `site-out` as an artifact. The build also writes the link
   manifest to `site-out.manifest.json`, beside the site folder and never
   inside it, so Pages cannot publish it. It goes up as a second artifact,
   `site-manifest`. The assets inventory (`site-out.assets.json`) is written
   the same way and uploaded as `site-assets-inventory`; the job also compares
   it with the last deployment's to build the optional purge list.
2. **`deploy`** waits for `build`, then waits for a reviewer to approve the
   `test` environment. It downloads both artifacts (a script, `.github/scripts/verify-manifest.py`, derives the Worker's `LINK_IDS`
   from `site-out.manifest.json`, refuses any link id outside
   `[a-z0-9-]{1,40}` (without echoing it), an empty list, or more ids than the
   Worker's cap of 200, and only warns when the optional `LINK_IDS` variable differs), installs the Worker
   dependencies with `npm ci --ignore-scripts`, applies D1 migrations,
   deploys the Worker, publishes the site with `wrangler pages deploy`, and,
   when `CLOUDFLARE_PURGE_TOKEN` and `ZONE_ID` are set, purges the changed paths
   (see "Caching and stale files").

Why two jobs: `pip install` and `npm ci` run code from third-party
dependencies. Neither runs while the Cloudflare token exists: the build job has
no secrets, and the deploy job installs before it fetches the token. The build
installs Python dependencies with `pip install --require-hashes` from
`requirements-build.txt` and `requirements.txt`, then the project itself with
`--no-deps`, as the Containerfile does. After the Doppler step the token is in
the job environment, including later steps and post-run hooks (see "Where the
token is available"); it is not in workflow-level or job-level `env` and is
never echoed. The `test` environment is where you add a required reviewer
(section 2), so a person approves each deploy before the secret is released to
the `deploy` job.

The committed `worker/wrangler.toml` keeps its fake ids; the workflow writes a
temporary `wrangler.deploy.toml` on the runner only.

Note: the build command arrives in plan Task 6 (and `pyproject.toml` in Task
1). Until both are on `main`, the build step fails by design.

## 4. Rolling back

- **Site.** Pages project > Deployments > pick the previous deployment >
  Rollback to this deployment. Or re-run the workflow from an earlier good
  commit on `main`.
- **Worker.** Worker > Deployments > Rollback, or `wrangler rollback` with the
  token. Pause clicks entirely by deleting the Worker; links keep working
  because the page uses `ping` plus a beacon, not a redirect.
- **D1 migrations.** Migrations are forward only. Ship a new migration to
  undo a change; as a backstop, `wrangler d1 export reach-around-clicks
  --remote` before schema changes.
- **DNS.** Remove the custom domain from the Pages project (or delete the
  `CNAME`). The test hostname stops resolving; nothing else is affected.
- **Token.** If the token leaks or you are unsure, roll or delete it in
  Cloudflare, then update the secret.
- **Stop all deploys.** Disable the workflow in the Actions tab, or remove
  the `test` environment secret.
