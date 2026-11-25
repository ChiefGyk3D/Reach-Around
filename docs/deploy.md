# Deploying to the test subdomain

> **Stale in parts (state STALE, issue #801). Written for the test subdomain and Free-first profiles. The 2026-11-19 decision to use the Paid plan as the baseline, and the 2026-11-24 storage and domain decisions (Durable Objects, Analytics Engine daily counters, two D1 databases, a separate frame domain), supersede those parts. Do not rewrite from this page alone. See the ADR index in `docs/wiki/adr/` once merged (PR #835).**

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

   Optional variables for page views on the test site (issues #260 and #261;
   the Worker side is under [Page views](#page-views-post-evpageid)). Plain
   **variables**, never secrets:

   | Variable | Value | What it does |
   |---|---|---|
   | `PAGE_VIEWS` | exactly `true` | **enables page views for this deploy.** Unset, empty and `false` are off and leave the deploy config byte-for-byte unchanged; any other text (`True`, `1`, `yes`) stops the run before anything is fetched. On, the build adds `analytics: {page_views: true}` to a copy of `examples/instance/site.yaml` (the committed file is not edited) |
   | `PAGE_IDS` | `["start-here","events"]` | **required when on.** The tab ids the Worker counts; a JSON array, each id lowercase letters, digits and `-`, 1 to 40 characters. The "Verify the page ids" step fails the deploy if the build has a tab that is not listed (its views would be dropped) and warns about a listed id the build lacks |
   | `VIEW_LIMITER_NAMESPACE_ID` | a number, not `1001` | **required when on.** Namespace id of the per-client `VIEW_LIMITER` rate limiter (5 views per client per 60 s). It must differ from `RATELIMIT_NAMESPACE_ID` and is not the `1002` placeholder in `wrangler.toml` |
   | `BEACON_MODE` | exactly `observe`, or unset, empty or `off` | **optional.** `observe` writes `BEACON_MODE = "observe"` and the `MINT_LIMITER` binding into the deploy config (see "Beacon token" below); unset, empty and `off` write nothing. Anything else, `enforce` included (not available yet), stops the run. The key `BEACON_HMAC_KEY` is a Worker secret the owner sets, never a repository variable |
   | `MINT_LIMITER_NAMESPACE_ID` | a number, not `1004` | **required when `BEACON_MODE` is `observe`.** Namespace id of the per-client `MINT_LIMITER` rate limiter (6 tokens per client per 60 s). It must differ from `RATELIMIT_NAMESPACE_ID` and `VIEW_LIMITER_NAMESPACE_ID` |
   | `VIEWS_PER_HOUR`, `BOT_VIEWS_PER_HOUR` | whole numbers, 0 to 100000 | optional hourly budgets of likely-human and bot views (defaults 120 and 30); written to the Worker and used by the budget check below |
   | `ACCOUNT_VIEWS_PER_HOUR`, `ACCOUNT_BOT_VIEWS_PER_HOUR` | whole numbers, 0 to 100000 | optional account-wide ceilings above the per-site budgets (defaults 480 and 120); written to the Worker and used by the budget check below |
   | `ROUTE_BUDGET_GATE`, `ROUTE_BUDGET_VIEW`, `MAX_LEADS`, `MAX_KIT_EVENTS`, `DAILY_ALERT_BUDGET`, `DAILY_SEND_BUDGET`, `D1_DAILY_WRITE_QUOTA`, `CLICK_DAILY_WRITE_CEILING` | digits only | optional kit limits, the D1 quota and the daily click write ceiling (see Kit limits below). With page views on they are written to the Worker **and** given to the budget check, so the check sees the same values the Worker loads. The exception is `D1_DAILY_WRITE_QUOTA` and `CLICK_DAILY_WRITE_CEILING`, which are validated, written and budget-checked whether or not page views are on (with page views off the step "Check the click write budget" runs the same Worker function on those two and the site list); the check applies the Worker's exact ranges and stops the deploy on a value the Worker would refuse |

   Page views also need `ZONE_NAME` (the `/e/*` route; the deploy stops
   without it) and the Worker secret `VIEW_HASH_SECRET`, which you set once
   with `npx wrangler secret put VIEW_HASH_SECRET` (never in a variable, a file
   or this workflow; without it the route records nothing). The hourly cron
   already in `wrangler.toml` runs the rollup and the purge, so the deploy adds
   no trigger. The page needs no CSP change: the beacon goes to the site's own
   origin and `connect-src 'self'` is already there. The page-view migrations (`0005` to `0008`)
   are applied by the existing "Apply D1 migrations" step; read the migration
   `0007` warning under Page views before the first deploy on a database that
   has ever rolled up a day.

   **Write budget check.** When `PAGE_VIEWS` is `true`, the step "Check the
   page-view write budget" bundles the Worker's own `assessDailyBudget`
   (`worker/src/kit/budgets.ts`) and prints the projected worst-case daily D1
   writes: the kit at its defaults, the page-view feature at the binding hourly ceiling
   for humans and for bots separately (the lower of the sites' budgets added up
   and the account ceiling; 24 x (human + bot) x 5.6 rows per view, plus 2,540
   for the rollup, purge and heartbeat), the click reserve, the click funnel tables (see Click funnel in the check below) and
   the margin, against the daily write quota (`D1_DAILY_WRITE_QUOTA`, default 1,600,000:
   the paid plan's 50 million included rows written a month over a 31 day month,
   rounded down; the Free plan's 100,000 a day no longer fits the click ceiling and
   is refused). Over 100 percent, or an
   unusable value, stops the deploy; above 80 percent is a warning. This is the
   same function and the same lines the Worker applies to itself when it loads
   its limits, and the page-view part now follows the configured budgets
   instead of a fixed 22,700. There is no sampling: a view over the hourly
   budget is dropped, so the budgets are the cap. The sites are counted by the Worker's own
   site loader from the same `SITE_HOST`, `PAGE_IDS` and `LINK_IDS` (or a
   `SITES` text); a site list that does not load stops the deploy, and the
   Worker itself reads a list it cannot count as the maximum of 8 sites.
   **Kill switch:** `VIEWS_KILL`, see below; `npx wrangler deploy --var
   VIEWS_KILL:1` turns the route into an empty 204 that touches neither D1 nor
   the limiter, and setting `PAGE_VIEWS` back to `false` stops the next build
   from loading the script at all.

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

The Worker also has a write-only page-view route (Plan 4 Tasks A3 and A4; the browser script `static/view.js` calls it when `analytics.page_views` is on). `deploy-test.yml` wires it for the test site from repository variables (see the page-view variables in the GitHub steps above). It needs migrations `0005` and `0006` applied to the same D1 database, and the same `/e/*` zone route as clicks.

| Variable | Kind | What it does |
|---|---|---|
| `PAGE_IDS` | var | JSON array of tab ids from the build manifest. Any other page id is ignored. Deploy the Worker with it before the site |
| `VIEW_HASH_SECRET` | **secret** | 32 or more random bytes, base64. Set with `npx wrangler secret put VIEW_HASH_SECRET`; never in `wrangler.toml`, a committed file, a log or a chat. **Required.** It keys the rate-limiter keys, the per-client cap and the visitor hash. Absent, too short or malformed (or a request with no parsable client address): the route fails closed and records nothing |
| `VIEWS_KILL` | var | `1` or `true` makes the route return its usual empty 204 with no database or limiter call. Read per request |
| `SCHEDULED_KILL` | var | maintenance switch: `1` or `true` (any case) makes every scheduled run (the hourly jobs and the Live poller) return at once with no D1 read or write. Any other value, or unset, leaves the jobs on. Read on every run, so `npx wrangler deploy --var SCHEDULED_KILL:1` takes effect for the next trigger and `--var SCHEDULED_KILL:0` turns it back on. Leave it unset in the committed `wrangler.toml`. It does not stop a run already in progress; see the migration step in [cutover-runbook.md](cutover-runbook.md) for how to confirm none is. While it is on, health sampling and its alerts and notifications are paused for the whole window, the live status goes stale, and retention, purges and rollups wait (they catch up on the next run after it is off). Reports show the window as a `-` outage gap, not as `kill_switch`. It is for D1 migrations only |
| `VIEW_LIMITER` | binding | Workers Rate Limiting binding, placeholder in `wrangler.toml` (5 per client per 60 s). Required: without it the route records nothing. It is keyed by an HMAC of the address under a stable key derived from the secret (purpose label `lim`, no day component, so there is no burst at midnight UTC), never the raw address, so no address is stored in the binding either. `VIEW_SUSPECT_LIMITER` (optional) works the same way with label `sus`. The click route's `CLICK_LIMITER` uses the same secret with label `clk`; if the secret is missing or unusable on the Worker, the click route refuses (the same empty 204, nothing recorded), as the page-view route does, so a Worker that counts clicks needs the secret set even when page views are off. Addresses are canonicalized first (`worker/src/address.ts`), so equivalent spellings share an allowance; an absent or malformed address uses one shared `unknown` bucket. Rotating the secret changes every limiter key, resets every address-based allowance and per-client cap, and can count a visitor twice that UTC day (the visitor hash changes mid-day): rotate at UTC midnight or accept the burst |
| `VIEWS_PER_HOUR`, `BOT_VIEWS_PER_HOUR` | var | global hourly write budgets, defaults 120 and 30 (Plan 4 open decision P-6) |
| `CLIENT_VIEWS_PER_HOUR` | var | per-address hourly cap, default 60, ceiling 1000, then clamped to half the human budget (humans) or a quarter of the bot budget (bots). One counter per address is shared by bot and human traffic. Keyed on a daily keyed hash of the address, never the address |
| `RAW_KEEP_DAYS` | var | days raw view rows are kept once rolled up, default 7 (open decision P-3), ceiling 90; a bad value falls back to 7. An unrolled day is kept up to 3 times this |
| `QUERY_BUDGET` | var | D1 queries one cron invocation may make: default 40 (Free allows 50), at most 900 (Paid allows 1000); a batch counts as one |
| `ROLLUP_MAX_ROWS_PER_DAY` | var | raw rows above which a day is rolled in the reduced mode; default 3000, ceiling 20000. One invocation takes at most 4 times this many input rows |
| `UTM_ALLOWLIST` | var | optional comma-separated lower-case `utm_source` labels; any other label is stored as `other` in the aggregates. Unset means no filter |
| `MIN_CELL` | var | cells under this many views in a day fold into `other`, default 3, 1 to 50 |
| `ROLLUP_GRACE_HOURS` | var | hours after UTC midnight before a day is rolled up, default 2, at most 12 |
| `TOP_SOURCES` | var | referrer and utm labels kept per day in the rollup, default 50; the rest fold into `other` |
| `GEO_LEVEL`, `BOTS_MODE`, `UV_ENABLED`, `UV_BITS` | var | `region` / `count` / on / `64` by default. `UV_BITS` has a minimum of 64 (open decision P-5): a lower value falls back to 64, and at most 128 is accepted |

Retention runs from the cron (`17 4 * * *` and `5 0 * * *`): `visitor_seen` rows before today's UTC date, raw view rows past `RAW_KEEP_DAYS`, hourly budget counters older than 48 hours and client-cap counters older than the previous hour. Counter keys are `bud:<hour>:<route>` and `cap:<hour>:<hash>` (the hash is 16 hex characters, 64 bits; counters from before the widening expire within the hour) with the hour zero-padded, so expiry is a range delete on the primary key. A run that hits its batch budget records `purge_backlog` in `view_meta` and the next run continues. The daily rollup that should run before the raw purge (Task A5) is not built yet, so raw rows are deleted by age alone for now.
Cron order (`17 4 * * *` and `5 0 * * *`): click purge, then the **daily rollup**, then the view purge. Each step is isolated, so one failing never stops the others, and none of them throws out of the handler.
Cron: one hourly trigger, `5 * * * *` (so the pseudonymous daily visitor hash is deleted within about an hour of its UTC day ending). Each run does, in this order, the click purge, the **rollup**, then the view purge. Each step is isolated, so one failing never stops the others, and none of them throws out of the handler. Free allows 5 cron triggers per account in total, so this uses one.
Cron: one hourly trigger, `5 * * * *` (so the pseudonymous daily visitor hash is deleted within about an hour of its UTC day ending). Each run does, in this order, the click purge, the **view purge**, the **lead purge** (media kit retention, see below) then the **rollup**. The purge comes before the rollup on purpose: a rollup that fails, runs out of CPU or is cut off by the platform can never block retention (the view purge only deletes days that are already rolled, plus rows past the hard ceiling). Each step is isolated, so one failing never stops the others, and none of them throws out of the handler. Free allows 5 cron triggers per account in total, so this uses one.

**Hard per-invocation query budget.** The Cloudflare limits this is built on (read 2026-10-24): the D1 limits page (`developers.cloudflare.com/d1/platform/limits/`, last updated Oct 24, 2026) says a Worker may make 1000 D1 queries per invocation on Paid and 50 on Free, and that a `db.batch()` counts as ONE query, not one per statement. The Workers limits page (`developers.cloudflare.com/workers/platform/limits/`, last updated Oct 12, 2026) gives 50 subrequests per invocation on Free (10,000 on Paid, D1 included) and 5 Cron Triggers per account on Free. `worker/src/d1budget.ts` wraps the database once, so every `run`, `all`, `first`, `raw`, `exec` and `batch` is counted in one place and throws `BudgetExceeded` instead of making the call when the meter is empty. `QUERY_BUDGET` sets it: default 40 (Free, 10 left as margin), bad values fall back to 40, ceiling 900 (Paid). The handler gives the view purge at most half; the click purge, the purge and the rollup all draw from the same meter, and every unit of work checks what is left before it starts.

Cost per invocation with the defaults: click purge 2 when idle (one delete that finds nothing plus one marker-reconciliation batch), up to 10 deletes plus one marker batch (11) while a backlog lasts, and its `batch`/`maxBatches` options can only lower those bounds, never raise them; rollup 1 (one batch that finds the days to roll and the sealed days with late rows) + 1 (lease) + 2 per day (one read batch, one write batch) + 1 (heartbeat batch that also releases the lease); view purge 1 (one batch holding the overdue check and every step's first delete) + up to a few more rounds while the budget lasts + 1 (closing batch). The rollup therefore rolls up to about 6 days per invocation with the defaults (the purge uses 2 to 3 of the 40); the worst day (every cap and fold exercised, 3,600 raw rows) is still exactly those 2 queries, because the write batch holds up to about 370 statements (a batch of 2,000 statements is fine per the D1 page). An invocation with a tight budget rolls fewer days and stops; the next hourly run continues from the same raw rows, and a test proves repeated runs at budget 12 end with the same aggregates as one unbounded run. A day is one transaction (the write batch), so there is no half-sealed day and no stage marker is needed.

**Rollup** (`worker/src/viewrollup.ts`). Every UTC day that has ended and whose grace period has passed (`ROLLUP_GRACE_HOURS`, default 2, ceiling 12, because a view that began before midnight can be written after it), and that still has raw rows in `view_events` and no row in `view_rollup_log`, is aggregated into three marginal tables: `view_daily` (page, source, country, bot class), `view_dim_daily` (one dimension at a time: browser, os, device, lang, vp, net, reason) and `view_geo_daily` (country, region, city). No address, user agent, referrer URL or browser version reaches them. `uniques` counts the raw rows whose `first` flag is exactly 1 (set from the daily hash at write time); a damaged or hostile value such as 1000000, -1, 2 or text counts as 0, so it cannot inflate uniques or fail a day. `bot_class` and `src_kind` are closed sets enforced by CHECK constraints. I did not add a `CHECK (first IN (0,1))` to the raw table: the aggregation is already safe against any value and tested with hostile rows, which a CHECK would make impossible to insert. A row whose timestamp is not an integer belongs to no day; the purge deletes such rows. The `view_rollup_log` row is the last statement of the day's write transaction and is the checkpoint, so running twice never double counts. A day with a log row is sealed. A day that fails (database error or a totals mismatch) is recorded and skipped: later days still roll, the run records `rollup_err` and `rollup_failed_day`, and the failed day is retried on the next run; it never blocks the purge of days that are already rolled. At most 14 days are rolled per run (the query budget and the input row budget usually stop it earlier). Ranking, top-N and every fold run in SQL (window functions in D1), so the Worker receives only the capped output rows. Sources beyond `TOP_SOURCES` (default 50) fold into `other`; per bot class, `browser` keeps 12 values, `lang` 10, `os` 10, `net` 10, `reason` 20, `device` and `vp` 6, the rest `other`; the core and geography tables are capped at 500 and 300 rows per day, and the dimension table has a real maximum of 324 rows (4 bot classes times the top values of all seven dimensions plus one `other` each; it follows from the per-dimension tops, a test reaches it exactly). The core cap is hard: overflow rows lose their page as well as their source and country, so they collapse to one `other` page row per bot class (readers must accept `other` as a page value). `GEO_LEVEL` is applied again at rollup, so a `country` setting never aggregates a region or city even if an older raw row has one; at `city` the rollup reads the city from `view_city_daily`, never from a raw row.

**Small cells.** `MIN_CELL` (default 3, 1 to 50) applies to every cell: a dimension value with fewer than `MIN_CELL` views in a day (per bot class) is folded into `other` for that dimension; a `view_daily` cell (page, source, country, bot class) under it folds its referrer or `utm` label into `other` first and then its country into `ZZ` (direct and internal visits only lose the country); geography folds a city into its region and a region into its country under `max(5, MIN_CELL)`. Totals always sum to the raw count. **This is label suppression, not k-anonymity.** One-view cells can still remain inside `other` and `ZZ`; the exact per-day and per-class totals in the aggregate tables and `view_rollup_log` are exact; and anyone with read access to the D1 database can read the raw rows for the whole raw retention window. So folding protects dashboards and exports, not people with database access. The tradeoff is privacy against detail: a rare browser, language, country or referrer cannot be singled out of a small day's published numbers, but a small site sees a large share of `other` and `ZZ`, and a long tail is never shown. `MIN_CELL=1` turns it off. Downstream readers must accept `other` as a value in every dimension, including the closed ones such as `reason`, `os` and `net`. The log table keeps overall per-day totals only (no per-class totals) for reconciliation; do not export more than a dashboard needs.

**Lease.** A run takes a lease in `view_meta` (`rollup_lease`, a random token and an expiry 10 minutes ahead) with one atomic upsert that only replaces an expired lease; a run that cannot take it does nothing and does not touch the heartbeat. The lease is renewed inside each day's read batch (no extra query). Every statement in a day's write batch is guarded by "the lease token is mine AND its expiry has not passed" (compared with a bound `now`, not SQL time) and by the day's state (no log row yet for a fresh day, or the log row still showing the `max_id` this run read for a re-roll), so an overlapping run, or a run whose lease expired even with no takeover, writes and deletes nothing; the checkpoint is only counted as sealed when it changed a row. The lease is released in the heartbeat batch (and is harmless if left behind: it expires). Idle hours take no lease.

**Migrations 0005 to 0007 go together.** The maintainer has ACCEPTED the risk that migration 0007 deletes rollup data, because only migration 0001 has ever run on a live database (his test database), so nothing it deletes was ever real history. Apply 0005, 0006 and 0007 together, on a database that has never run a page-view rollup. Only migration 0001 has ever been applied to a live D1 (the maintainer's test database); 0005 to 0007 have never shipped, so applying them loses no history. Migration 0007 deletes every row of `view_daily`, `view_dim_daily`, `view_geo_daily` and `view_rollup_log` (the old rollup left no record of which raw rows it counted, and guessing could purge an uncounted row). **A database that already ran an earlier rollup version must not use this migration: take an export first (`npx wrangler d1 export`), because days whose raw rows have expired would lose their aggregates.** SQL migrations cannot refuse to run, so this rule is the control.

**Migration 0008 (sites).** It adds a `site` column, rebuilds the aggregate tables and the rollup log with `site` first in the key (every existing row becomes site `links`), rounds raw timestamps to the hour, clears the raw network class and **empties `visitor_seen`**. Daily visitor codes now include the site, so the old site-less codes would count a returning visitor twice that day. Apply it right after 00:05 UTC (the run that closes the day) so the overcount lasts at most one hour of one day. It is forward only; export first (`npx wrangler d1 export`) if you want a way back. Set `SITES` (see `wrangler.toml`) or keep `SITE_HOST`, `PAGE_IDS` and `LINK_IDS`, which still describe the one site `links`. Each accepted view now writes about nine D1 rows (counters, the event, two indexes, the daily code); watch the free-plan write quota. `view_meta('config_err')` shows closed codes if the site config has a problem.

**Migration 0016 (destination kind).** It adds the closed
`destination_kind` column to raw clicks and the daily and monthly
source/country rollups. Existing rows become `link`. The build-derived
`DESTINATION_KINDS` mapping selects `link` or `social_icon` for new rows. Apply
this forward-only migration before deploying the Worker that writes the field.

**Late rows (full re-roll).** The log row records `max_id`, the highest raw row id the day's aggregates count, and `rerolls`. A raw row of a sealed day written afterwards has a larger id. The raw purge deletes a rolled day's rows only up to that `max_id`, so a late row is never deleted as if it had been counted. Folding is not additive (a 3-view named cell plus one late view, top-N order, caps), so a late row is never added as a delta: the next run **re-rolls the whole day** from all its raw rows and replaces the day's aggregate rows and log row in one transaction (`max_id` advances, `rerolls` goes up). The state is explicit: no log row means fresh; a log row with raw rows above its `max_id` means re-roll. A re-roll needs every originally counted raw row to still exist. If the purge already took some (the day is older than `RAW_KEEP_DAYS`), the day cannot be recomputed, so the late rows are **dropped**: `max_id` advances so they are not found again, the aggregates stay as they were, and `rollup_late_dropped` counts them. Counted re-rolled rows are `rollup_late_rows`.

**Re-roll window (migration `0018`).** A late row re-rolls its day only while the day is open: until its end plus `ROLLUP_GRACE_HOURS` plus 48 hours, and only while `view_rollup_log.reroll_closed` is 0. A dropped late batch (the purge already took counted rows) or a late row after the window sets `reroll_closed` to 1 together with the new `max_id`, so the day is never re-rolled again; later late rows are counted in `rollup_late_dropped`. The migration is a plain column add (existing days stay open).

**Retention order.** The view purge deletes a raw row past `RAW_KEEP_DAYS` only if its UTC day is in `view_rollup_log` and the row's id is at most that day's `max_id`. A day that has not rolled up, and a late row above a rolled day's `max_id`, is kept until the hard ceiling of the smaller of 3 times `RAW_KEEP_DAYS` and 90 days (21 days by default); after that it is dropped, `view_meta('rollup_overdue')` is set to the date and `view_meta('rollup_overdue_rows')` adds up how many rows no aggregate counted (it clears with the flag). Every bounded delete of an aggregate, hour or log table is keyed by the table's whole primary key, so a day of many sites goes a batch at a time. Click rows are deleted in batches of 500 (at most 10 per run); a run that stops with expired rows left records `click_purge_backlog` in `view_meta` and the next run continues. The flag stays for the rest of that UTC day and is cleared by the first later run that finds nothing left beyond the ceiling. `visitor_seen` rows (the pseudonymous daily hash) are deleted as soon as the UTC day ends, whatever the rollup state: the rollup never reads that table, because uniques come from the raw `first` flags. Aggregate and log rows are deleted after 90 days. Hourly counter keys are `bud:<hour>:<route>` and `cap:<hour>:<hash>` with the hour zero-padded, so expiry is a range delete on the primary key. A run that stops with delete work left (batch rounds or query budget used up) records `purge_backlog` in `view_meta` and the next run continues.

**Write budget (D1, page views; first sized to the Free plan's 100,000 rows written a day).** The accepted-view budgets are 120 human plus 30 bot views per hour, so at most 150 x 24 = 3,600 raw views a day. The rollup writes one row per aggregate row. Worst case per day: 500 (core) + 324 (dim) + 300 (geo) + 1 (log) = 1,125 rows, and the 90-day purge later deletes the same rows, so about 2,200 a day (well under the 7,200 Plan 4 P-6 allowed). The caps are hard: more distinct values fold into `other` or `ZZ`, they never add rows. A re-roll rewrites its day (up to 1,125 rows plus the same deleted) and happens only when late rows exist. The hourly cron adds an idle-hour cost of about 9 rows (heartbeat keys) and, on a day with work, 2 lease rows: roughly 250 a day. Together with the per-view writes in P-6 (about 20,160) the page-view feature stays near 22,700 rows a day (the quota it is checked against is now the paid plan's, see Kit limits below). A backfill is bounded by the query budget (about 6 days per default invocation, about 6,600 rows).

**Heartbeat.** After every run the Worker records, in `view_meta`, `rollup_last_ok` (epoch milliseconds, set only on success), `rollup_days`, `rollup_rows`, `rollup_late_rows` (late rows re-rolled), `rollup_late_dropped`, `rollup_reduced_days` (this run), `rollup_err` (`none`, `db` or `mismatch`; no free text), `rollup_failed_day:<site>` (the first day that failed for that site, cleared by the next clean run) and `rolled_through:<site>` (the last UTC day that is final for that site; one pair of keys per site, `links` for a single-site setup). To check it:

```sh
npx wrangler d1 execute reach-around-clicks --remote \
  --command "SELECT key, value FROM view_meta WHERE key IN ('rollup_last_ok','rollup_days','rollup_rows','rollup_late_rows','rollup_late_dropped','rollup_reduced_days','rollup_err','rollup_overdue') OR key LIKE 'rollup_failed_day:%' OR key LIKE 'rolled_through:%'"
```

Convert `rollup_last_ok` with `date -u -d @$((VALUE/1000))`. More than about 2 days old, or `rollup_err` other than `none`, or any `rollup_overdue` row, means the rollup is stuck: raw rows are being kept (up to 21 days) instead of purged, and no aggregate is being written. Check the Worker's scheduled logs, fix, and the next run catches up. To see which days are done: `SELECT day, raw_views, views, uniques FROM view_rollup_log ORDER BY day DESC LIMIT 10`.

**CPU on the Workers Free plan (cannot be measured in the tests).** What Cloudflare documents (read 2026-10-24): the Workers limits page (`developers.cloudflare.com/workers/platform/limits/`, last updated Oct 12, 2026) says "CPU time measures how long the CPU spends executing your Worker code. Waiting on network requests (such as fetch() calls, KV reads, or database queries) does not count toward CPU time." and lists CPU time per Cron Trigger as 10 ms on Free and, on Paid, 30 seconds for a trigger interval under 1 hour and 15 min for an interval of 1 hour or more (this Worker's hourly trigger counts as 1 hour). The D1 limits page (`developers.cloudflare.com/d1/platform/limits/`, last updated Oct 24, 2026) says "Operations on a D1 database, including query execution and result serialization, run within the Workers platform CPU and memory limits. Exceeding these limits, or hitting other platform limits, will generate errors." The two statements together do not tell us how much of a D1 call counts as CPU, so this document does not claim D1 work is free. vitest runs under miniflare and cannot measure Worker CPU, so everything below is an argument, not a measurement. What the design does to stay small: ranking, top-N, small-cell folding, the row caps, the UTM allowlist and the geography level run in D1 (SQL with window functions), not in JavaScript; the Worker receives at most 500 + 324 + 300 = 1,124 output rows per day (a few hundred on a normal day), sums them to check the totals and builds 10-row insert statements from them (about 110 statements for a full day). It never sorts, groups or merges raw-sized data. Result serialization still happens, which is why input size is bounded too: a day with more than `ROLLUP_MAX_ROWS_PER_DAY` raw rows (default 3000, ceiling 20000) is rolled in a reduced mode (referrer top-N 10, geography at country level, counted in `rollup_reduced_days`, for fresh days and re-rolls alike), and one invocation will not take another day once it has used `4 x ROLLUP_MAX_ROWS_PER_DAY` input rows. The purge runs before the rollup, so even if the rollup is cut off, retention has already happened. **After the first deploy, watch `npx wrangler tail` (or the Workers observability logs) for `exceededCpu` and error 1102.** If you see it, either move to the Workers Paid plan or lower the volume: a smaller `VIEWS_PER_HOUR` and `BOT_VIEWS_PER_HOUR`, a smaller `ROLLUP_MAX_ROWS_PER_DAY`, or a smaller `TOP_SOURCES`. A cut-off invocation does not reach its catch or heartbeat, so a stale `rollup_last_ok` is the symptom to look for.

**Identifying labels.** Aggregates keep labels, and a label can identify a person: a referrer host such as `alice-smith.example.com`, a `utm_source` such as `john-doe`, or a small town's city name. MIN_CELL hides rare ones in a small day, not all of them. Set `UTM_ALLOWLIST` (comma-separated lower-case labels, for example `bluesky,mail,newsletter`) so any other `utm_source` is stored in the aggregates as `other`; it is applied at rollup, so the raw rows (kept for `RAW_KEEP_DAYS`) still hold the original label. There is no referrer allowlist; referrer hosts are bounded by `TOP_SOURCES` and `MIN_CELL`. The hourly cap keys in `view_meta` (`cap:<hour>:<hash>`) are keyed hashes of a client address and are kept as they are (deleted after the previous hour). Say all of this in the privacy text.

**Geography is stored at most to the region in a raw row.** `GEO_LEVEL` defaults to `region` (owner decision 2026-11-24): the country and region code are written to the raw rows (kept for `RAW_KEEP_DAYS`, default 7, ceiling 90) and, folded for small counts, to the daily geography aggregate. A raw row never holds a city, at any level. Set `GEO_LEVEL=city` (or a site's `geo`) knowingly to also get cities: the Worker then adds each view to a daily city counter (`view_city_daily`, migration 0019; site, UTC day, country, region, city, bot class, views, uniques, and nothing else), the rollup moves cities with at least five views (or `MIN_CELL`) into `view_geo_daily` and folds the rest into their region, and the counter is purged with the raw rows. Set `GEO_LEVEL=country` to store the country only; in `country` mode no region or city is stored anywhere (a test dumps the whole database to prove it). The privacy page follows `analytics.geo_level` and says which applies. The city counter is one more D1 row per accepted view, so the budget model counts 6.6 rows per view (7.6 with BEACON_MODE on) when any site is at city, and 5.6 otherwise: the default 22,700 a day does not move.

**Atomic accept.** An accepted view is one D1 batch: the client counter, the global counter, the event row and the visitor row are written together only if both the global budget and the client cap have room, otherwise nothing is written; a failure rolls the whole batch back. After the budget is spent, extra clients create no rows.

**Timing.** Every outcome is the same empty 204 with the same headers, so responses are byte-uniform, but they are not timing-uniform: an early refusal (wrong method, unknown page, GPC) returns faster than an accepted view, which makes database calls. A patient observer can tell stages apart. This is accepted: nothing secret is revealed, the page ids and routes are public. The body read has a fixed 2 second deadline and then gives the same 204.

The route stores no address, user agent or referrer URL. After a deploy, one beacon from a browser should give a 204 and one `view_events` row with categories only; the same request with `Sec-GPC: 1` should leave no row.

## The invisible bot check (`analytics.turnstile_beacon`), off by default

Part of the beacon abuse work (#610, epic #246). This is the browser side only: a hidden helper page that runs Cloudflare Turnstile in invisible mode. Nothing in the Worker reads its result yet, so turning it on today changes no count; it only makes the page run the check, which is what the storage and accessibility checks below need.

Config (`docs/config.md`): `analytics.turnstile_beacon: true` and `analytics.turnstile_site_key: <public key>`. The key is public; the secret key is for the Worker and is never put in this repository. Off (the default), the build has no helper page, no script and no header change, byte for byte.

What the build adds when it is on:

| Piece | Where | Detail |
| --- | --- | --- |
| Helper page | `/e-frame/` | Static, not linked from any page, `noindex` as a meta tag and as `X-Robots-Tag`. Loads Turnstile's script and `static/beacon-frame.js`, both `defer`, no inline script. Renders the widget and posts the result to its parent with a same-origin message. Opened on its own it does nothing |
| Page script | `static/beacon.js` on every tab page that counts | After the load event and an idle moment, adds one hidden frame for `/e-frame/`. Never for a visitor with Global Privacy Control or Do Not Track (checked at start and again just before the frame is made). Waits 5 seconds, then gives up. Touches no link |
| Frame | added by the script | `sandbox="allow-scripts"` (no `allow-same-origin`, so the frame has an opaque origin), `aria-hidden="true"`, `tabindex="-1"`, `title`, 0 by 0, removed as soon as the check ends. Its address is `/e-frame/#<nonce>`: 16 random bytes from `crypto.getRandomValues`, made per frame, in the fragment so no server sees it |
| Policy for `/e-frame/` only | `_headers` | `script-src 'self' https://challenges.cloudflare.com`, `frame-src https://challenges.cloudflare.com`, `img-src https://challenges.cloudflare.com` (the site-wide `https:` images rule is replaced, not extended), `frame-ancestors 'self'`; everything else as on every page, with `connect-src 'self'`. The full string is pinned in `tests/site/test_beacon_frame.py` |
| Policy for every other page | `_headers` | Unchanged: `script-src 'self'`, `frame-ancestors 'none'` and trusted types. They can embed the helper because `frame-src` falls back to `default-src 'self'` |

The result, in the page's memory only: `window.reachAroundBeacon` is `{state: "verified", token}` or `{state: "unverified"}`, and the document gets a `reach-around:beacon` event with the same object. A blocked script, a slow check, a failed check or a timeout all end as `unverified`; the page and every link behave the same either way, so the check fails open. The token is a Turnstile response: it lasts 300 seconds, works once and has to be checked server side (with the hostname and the action `beacon`); the Worker side is a separate change.

**Third-party code runs inside the helper frame.** Cloudflare's Turnstile script is mutable vendor code, and it executes in `/e-frame/`. This repository cannot vouch for it. What contains it:

- The frame has no `allow-same-origin`, so it has an opaque origin: it cannot read or change the parent page, same-origin storage or cookies (including cookies without `HttpOnly`). It has no popups, forms or top navigation.
- The helper's own policy names only the vendor origin for script, frame and image, and `connect-src` is `'self'`, so there is no wide image or network channel out.
- The parent accepts a result only when the message origin is exactly `"null"` (what an opaque origin reports), the sender is exactly its own frame window, the message is a plain object with exactly the expected own keys, and it carries the nonce this page made for this frame. A frame cannot be addressed by origin, so the helper posts with target `"*"`, to `window.parent` only.

What it does not stop: the vendor script can read its own address, so it can read the nonce and post a "verified" message with a token string of its choosing. The nonce keeps stray frames and replayed messages out; it is not a defence against the vendor script. So `reachAroundBeacon.state === "verified"` is a hint for the browser side only. The Worker must verify the token with Cloudflare (siteverify, with the hostname and the action `beacon`) before anything trusts it; that is the separate Worker change. Until it lands, nothing counts differently because of this check.

Not confirmed from Cloudflare's documents, so measured instead: whether Turnstile keeps anything in the browser (the documents disagree), and **whether Turnstile produces a token inside an opaque-origin sandbox at all** (it may need cookies or storage, which an opaque origin does not give it). The measurement is in `docs/turnstile-storage-check.md`. If no token arrives the page ends as `unverified` after the 5 second wait, which is safe, but the check is then useless. The fallback is a dedicated origin for the helper (a separate Pages project or a subdomain that holds no site cookies and no site storage), with the parent checking that origin, the exact source window and the nonce instead of `"null"`. That needs infrastructure decisions by the owner (a project, a host name and a DNS record, and a policy `frame-src` entry on the ordinary pages for it); it is not built here. No widening of the sandbox (`allow-same-origin`) is the fallback: that puts vendor code back on the site's origin.

Before this is turned on for a live site, all of these must be done:

1. Run the storage check (`docs/turnstile-storage-check.md`) in a clean Chromium and Firefox profile and keep the output.
2. Run the accessibility checklist (`docs/turnstile-accessibility-check.md`).
3. The privacy page says what the check does and what it stores, matching step 1. The generated privacy page does not mention the check yet; that wording is written separately, after step 1.
4. Create the Turnstile widget as Invisible, limited to the site hostnames, and use its public key here.

## Beacon token (`POST /e/t`), observe mode only

A short-lived signed token that a page asks the Worker for once per visit and sends back with its click ping (`?t=`) and its view beacon (`?t=`). The Worker checks it from the URL and charges the event to the **verified lane** (a currently valid token for this site and this network) or the **unverified lane** (anything else). The token is stateless: nothing is stored, no cookie is set, and the page keeps it in memory only. A visitor with Global Privacy Control or Do Not Track never asks for one or attaches one (that is all this setting covers; whether a click itself is sent under those signals is the click opt-out change's, #609), and a page whose token request fails still counts and navigates exactly as before.

**Settings** (all optional; the default is off and changes nothing):

| Name | Kind | Meaning |
| --- | --- | --- |
| `BEACON_MODE` | variable | `off` (default; unset or empty is the same) or `observe`. **`enforce` is not available yet.** It is an unusable value: clicks and views are refused (the same empty 204) and the health flag `click_setup_invalid` is set, exactly as for any other unusable setting, until the issuance gate and the per-token limit have landed in later slices of #610. The code constant is `ENFORCE_AVAILABLE` in `worker/src/beaconmode.ts`. |
| `BEACON_HMAC_KEY` | **secret** | At least 32 bytes, its own key (never `TOKEN_HMAC_KEY`). Set it once with `npx wrangler secret put BEACON_HMAC_KEY`; it is never written to `wrangler.toml` and CI never reads or writes it. |
| `BEACON_HMAC_KEY_PREVIOUS` | **secret**, optional | The previous key during a rotation. It only checks; tokens signed by it keep working for their 15 minute life. |
| `MINT_LIMITER` | rate limiter binding | 6 tokens per client per 60 seconds (key: a keyed hash of the address, never the address). Without it, or without `VIEW_HASH_SECRET`, the mint issues nothing. |

The test deploy (`deploy-test.yml`) writes `BEACON_MODE = "observe"` and the `MINT_LIMITER` binding into its throwaway config only when the repository variable `BEACON_MODE` is exactly `observe` and `MINT_LIMITER_NAMESPACE_ID` is a number that differs from the click and view limiter namespaces; `off`, empty or unset writes nothing, and any other value (`enforce` included) fails the check. The budget check reads `BEACON_MODE` too. In the site config, `analytics.beacon_token: true` makes the pages ask for a token (`docs/config.md`); it is off by default.

**What it records.** In observe mode a click adds its charge to `cwl-v-<site>` or `cwl-u-<site>` as before (the lane counters already in the `counters` table), and an accepted view writes one more row, `pvl-v-<site>` or `pvl-u-<site>`, in the same atomic batch as the view itself, only when the view was stored. Nothing is refused and nothing is counted differently: observe records data only. The view budget model reserves that row while the mode is on (6.6 rows per accepted view instead of 5.6, so the page-view worst case at the default budgets is 26,300 a day instead of 22,700). No migration.

**What a token proves, and what it does not.** A token proves only that something asked the Worker for one in the last 15 minutes from the same network prefix (a /24 for IPv4, a /48 for IPv6) and for the same site. The site comes from the `Origin` or `Ping-From` header, which the sender chooses, and nothing yet limits who may ask for a token beyond the per-client limiter, or how often one token may be used inside its window. So the verified lane is **not proof of a real visitor**, and nothing becomes enforceable in this slice. The verified lane is still held below the whole share: it is charged against the general day budget (the share less the 20 percent first-click reserve) and the one-twelfth hour slice, the same limits as every other click.

**What a visitor sees, and what is sent.** With the token on, the view beacon waits up to 1.5 seconds for the token request and then goes without one, so a visitor who leaves the page inside that window sends no view beacon and is not counted. Observe mode does not change what the server counts for a request it receives, but it can change what is sent. A visitor whose network changes between the token request and a click (IPv6 to IPv4, Wi-Fi to cellular) fails the network check and is recorded as unverified; that is expected, not an error.

**Responses.** The mint answers `200` with `{"t": "<token>", "ttl": 900}` and `Cache-Control: no-store` only when it issues a token. Every other outcome, on every route, is the empty 204. The mint honors the same kill switches as the routes it serves: it issues nothing for a site whose click route and view route are both off (`CLICKS_KILL`, `CLICKS_KILL_SITES`, `VIEWS_KILL`, `VIEWS_KILL_SITES`), and a click-only (`edge`) site still gets a token because its clicks use one.

## Kit limits and the daily write budget

The media kit Worker is sized for the Cloudflare paid plan (approved 2026-11-19; it was sized for the Free plan before, and the Free plan's 100,000 rows written a day no longer fits the click ceiling below), and the ruling is: **when a limit is reached the kit goes unavailable; the limits cap the bill.** Every limit below fails closed. A request over a route budget, a lead or event over its cap, an alert or email over the daily budget, and any invalid setting all get a refusal, never a pass. No paid feature is switched on by any of these settings.

All values are plain decimal integers (digits only, no sign, space, exponent or unit). An unset or empty value takes the default. Any other value is rejected as a whole: an invalid kit limit or quota setting makes every kit reservation refuse until it is fixed, while an invalid `CLICK_DAILY_WRITE_CEILING` refuses clicks and fails the deploy check without taking the kit routes or the owner alerts down. Nothing is clamped or guessed, and a rejected value is never printed, only its variable name.

| Variable | Range | Default | Meaning |
|---|---|---|---|
| `ROUTE_BUDGET_GATE` | 0 to 100000 | 150 | Gate unlocks per UTC hour, charged only after the body parsed and Turnstile passed. Requests that only reach Turnstile are capped by the `GATE_CHECK_LIMITER` binding (20 per 60 seconds documented in `wrangler.toml`; no database write) |
| `ROUTE_BUDGET_VIEW` | 0 to 100000 | 300 | Gated-page views per UTC hour |
| `MAX_LEADS` | 1 to 50000 | 5000 | Stored leads; at the cap a new visitor is still unlocked but not stored (the answer is the same as for a known address), a known one is refreshed. See the gate routes in kit.md |
| `MAX_KIT_EVENTS` | 1 to 500000 | 50000 | Stored kit events; at the cap events are skipped |
| `DAILY_ALERT_BUDGET` | 0 to 1000 | 20 | Owner alerts per UTC day |
| `DAILY_SEND_BUDGET` | 0 to 1000 | 0 | Emails per UTC day (0: none; the send route is not in v1) |
| `D1_DAILY_WRITE_QUOTA` | 1000 to 999999999 | 1600000 | The paid D1 plan's 50 million included rows written a month, spread over a 31 day month and rounded down (the Free plan's daily figure was 100000, which the click ceiling no longer fits). Re-check it against Cloudflare's current limits before relying on it |
| `CLICK_DAILY_WRITE_CEILING` | 1000 to 10000000 | 300000 | Click write charge per UTC day, all sites together (the click write ceiling below). The Worker's click route, the budget model and the deploy check all read this one value, and they apply it whether or not page views are on. **Free plan:** set `D1_DAILY_WRITE_QUOTA` to `100000` and this to `7500` (82.5 percent of the quota since the gate claim rows are counted, so the check warns but passes; a ceiling of 5000 or less stays under 80 percent); the largest ceiling that fits 100000 at the defaults is 25001. An unusable value makes the click route refuse and fails the deploy check, by name; so does a ceiling too small to keep a first click for every link (the check says so) |

**Worst-case daily writes.** The D1 write allowance is one per account, shared by the kit, page views, clicks, the rollup and every purge, so the check adds them all. Settings are checked when a limit is used, not at deploy: if the combined worst case is over `D1_DAILY_WRITE_QUOTA`, or any kit limit or quota value is invalid, every kit reservation refuses (fail closed) until it is fixed; an invalid `CLICK_DAILY_WRITE_CEILING` is the one exception: it refuses clicks and fails the deploy check, and does not take the kit routes or the owner alerts down. The loader also reports a warning above 80 percent. The kit part is `24 x (gate x (5 + 1 claim) + view x 2) + alerts x 1 + sends x 7 + 10000`, where each number is the most rows one accepted operation writes (the row, its indexes and the counter) and 10000 is a reserve for the kit purge. To it are added the page-view feature with its rollup and purge (22700 at the default hourly budgets, from the page-view write budget above; the loader and the deploy check use the configured `VIEWS_PER_HOUR` and `BOT_VIEWS_PER_HOUR` instead, `worker/src/pvlimits.ts`), a click-route ceiling (300501 in all: the 300000 write charge, the 500 dropped-click counter writes and the one setup-flag write; enforced, see Click write ceiling below) and a margin (5000). A request refused over a budget writes nothing, except the bounded counters named there (the dropped-click counter, at most 500 writes a day in all, and the setup flag, one write a day). A media kit gate request that passes Turnstile writes its single-use claim row and takes its hourly gate unit in one atomic batch (the claim is inserted only if the unit was taken), so a request over the budget, or one that replays a claimed token, writes nothing that stays. The claim rows are one `counters` row each, written only when the hourly gate unit is taken, so they are bounded by the hourly gate budget for all networks together: at most `ROUTE_BUDGET_GATE` an hour and 24 x `ROUTE_BUDGET_GATE` a day (3,600 at the default). The model counts them (issue #650): `claim` is 1 write each, so the gate costs 6 rows per solved challenge, 5 for the budgeted operation and 1 for the claim. Per solved challenge, one claim row and one gate unit; per network the figure is at most 150 claim rows an hour (3,600 a day) at the default, because the 20 a minute allowance in front of Turnstile (1,200 an hour per coarse network) limits vendor calls, not claim writes, and the shared gate budget stops claim writes first. Not counted: the give-back update when two concurrent first uses of one token race (one extra write per lost race, never a claim row; the unit is returned at once) (see Counter growth in `docs/kit.md`).

| Part | Budget | Writes each | Worst case per day |
|---|---|---|---|
| gate | 150 per hour | 5 | 18000 |
| gate claim rows | 150 per hour | 1 | 3600 |
| view | 300 per hour | 2 | 14400 |
| alert | 20 per day | 1 | 20 |
| inquiry | 20 per day | 1 | 20 |
| send | 0 per day | 7 | 0 |
| kit purge reserve | | | 10000 |
| page views, rollup and purge | | | 22700 |
| clicks (enforced ceiling, all sites) | | | 300501 |
| click funnel (Free profile, one site, no link ids) | | | 758 |
| margin | | | 5000 |
| **Total** | | | **374999** |

That is 23.4 percent of the default quota of 1600000. Raising a budget raises this number; raise `D1_DAILY_WRITE_QUOTA` only on a plan that allows it. Counters live in the `counters` table, one row per route and hour or per day, and are purged after two days. A viral spike over a budget makes the gate unavailable until the next hour; that is the accepted trade.

**Click write ceiling (hardening, Refs #566 and #610).** Every accepted click writes several rows (the raw row and its index, the country counter, the capacity row, the hourly new-cell budget rows, and one counter for each of source, landing and hop the site lists), and the per-client limiter alone does not bound the total across clients. The click route therefore charges each click, before it writes anything, against a per-UTC-day ceiling on its write charge of 300000 in all (raised from 7500 on 2026-11-19, with the move to the paid plan), split equally over the configured sites so one site cannot spend another's share (`CLICK_ADMIT_CEILING` in `worker/src/clickceiling.ts` is the default; the Worker variable `CLICK_DAILY_WRITE_CEILING` sets it, digits only, 1000 to 10000000, and an unusable value refuses clicks), at its write charge: 9 for a site with no optional lists, up to 13 with source, landing and hop all listed (the charge counts the four admission rows below, and both hourly budget rows, `click_budget` and, when landing or hop is listed, `click_budget_lh`, whether or not that click makes a new cell). **The write charge is a deliberately conservative upper bound, not a count of the rows a click changes.** A click that makes new cells changes one row fewer than it is charged (the capacity row is charged but written only when a click folds). A repeat click on a link that already has its cells changes 6 to 9 rows while it is charged the same 9 to 13, because it does not write the first-click marker, the new-cell budget rows or the capacity row: the over-charge is at most 4 rows a click. A test pins that the charge is never below the rows changed. A site's share is spent through four limits, all from `clickceiling.ts`: **an hour slice** (no UTC hour may be charged more than one twelfth of the share, twice the average hour, so a burst cannot spend the whole day); **two lanes** (verified and unverified; the unverified lane may be charged at most 15 percent of the share a day; no token exists yet to prove a click came from the page, so every click is charged to the unverified lane today, both lanes are recorded, and that cap is **off by default**: it binds only when the Worker variable `BEACON_LANE_CAP_ENFORCED` is `1` or `true` (the deploy workflow does not write this variable; it is turned on only by editing the committed Worker config, and is meant for later token work), which belongs with tokens being enforced, since until then it would limit every real click; `0`, `false`, empty or unset leave it off, anything else refuses clicks); **a general day budget** (the share less a reserve of 20 percent); and **the first-click reserve** (that 20 percent is held back for the first click of each link each UTC day, which is judged against the whole share and ignores the hour slice and the lane cap, so a burst on one link is much less likely to stop another link from being counted at all. It is a best-effort shared pool, not a guarantee for each link and not an authentication boundary: the deploy check refuses a ceiling whose per-site reserve is smaller than the number of link ids times the largest click charge, which is what makes a first click for every listed link affordable). The charge is one atomic batch on the existing `counters` table, no migration: a conditional upsert on the day row (`cw-<site>`), then the hour row (`cwh-<site>-<HH>`), the lane row (`cwl-<lane>-<site>`) and the link's first-click marker (`cf-<site>_<link>`, written once per link per day; the underscore occurs in no site or link id, so two different pairs never share a marker, and the key stays within the column's 64 characters), each written only if the one before it changed a row, so concurrent clicks cannot overshoot. The four rows are in the charge. Past the ceiling the route still answers the same empty 204 and only the counting stops; the clicks dropped are counted in `cd-<site>` up to 500 a day in all, split the same way (`CLICK_DROP_COUNT_CAP`), after which a refused click writes nothing at all, so the figure reads "at least 500". The model reserves ceiling plus drop cap plus the setup flag, 300501, whatever the number of sites (`CLICK_ACCOUNT_DAILY_WRITES`), derived from the same constants the Worker enforces, and a test fails if they differ. **The trade, decided to protect the shared write allowance:** counting is no longer unconditional. With one site, the lane cap off (the default) and the default ceiling, counting stops once the general day budget is spent: about 18,460 clicks a day (240000 over 13 rows, all lists on) to 26,660 (9 rows), plus each link's first click of the day (it was about 750 to 1,250 at the old 7500); with two sites each site's figure is half of that. The hour slice stops a burst sooner: one hour holds about 1,920 to 2,770 clicks on one site (25000 over 13 or 9 rows). With the lane cap switched on and every click still unverified, the day stops at about 3,460 clicks (45000 over 13 rows) to 5,000 (9 rows) plus first clicks. Once a limit is spent the day counters undercount for the rest of that hour or UTC day; the raw `click_events` rows stop too. Redirects and the page are unaffected. **What the ceiling does not hide, and what it cannot stop.** A refused click and an admitted click have the same HTTP representation (an empty 204); their timing is not the same, because a refused click skips the raw insert and the counter batch, so a patient observer can tell them apart. Nothing here claims constant-time or indistinguishable behavior. The ceiling can also be exhausted by any valid-looking traffic, not only real visitors: the click beacon is a plain unauthenticated POST. The lanes and the hour slice limit how much of the day any one stretch of unproven traffic can use. A signed per-click token that would fill the verified lane is a later change; until then the unverified label means "not yet proven", nothing more. The owner notices through the analytics health alert `click_ceiling_reached` (`docs/alerts.md`), which reads the `cw-` and `cd-` counters, writes nothing, and reports that the ceiling was reached today and "dropped clicks today: at least N" (the dropped counter stops at its cap). Enable the `analytics_health` alert so a blind day is visible. If the real volume needs a higher ceiling, raise `CLICK_DAILY_WRITE_CEILING`; the deploy check and the Worker read the same value, so the combined figure rises by the same amount and must stay under the quota. The default ceiling is a bill cap on the paid plan (50 million included rows written a month, then a per-million charge), not a hard quota: even a fully spent 300000 a day is 9 million a month. **Free plan:** the default 300000 cannot fit 100,000 a day, and the deploy check refuses that pair; set `D1_DAILY_WRITE_QUOTA` to `100000` and `CLICK_DAILY_WRITE_CEILING` to `7500` (the old ceiling; 82.5 percent of the quota since the gate claim rows are counted, a warning, not a refusal).

**The hashing secret is required on every Worker that counts clicks.** The click limiter is keyed by `VIEW_HASH_SECRET` (32 or more random bytes, base64, set with `npx wrangler secret put VIEW_HASH_SECRET`), so a Worker that only counts clicks, with page views off, needs it too. Without it the route refuses every click: the same empty 204, nothing recorded. The same refusal happens for an unusable `CLICK_DAILY_WRITE_CEILING` or `BEACON_LANE_CAP_ENFORCED`. It is never silent: the route writes one content-free flag for the day (`cs-bad` in `counters`, no site, link or address, one write a day) and the hourly health check raises the analytics health condition `click_setup_invalid` ("clicks are being refused because the hashing key or a click setting is missing or unusable"), which clears when the setting is fixed and the next UTC day passes. The trade-off: a click with an unusable setup costs one D1 call (the setup-flag insert, a no-op after the first of the day) before the limiter, so those clicks are not free of database work. A ceiling set out of band (for example in the dashboard) that does not fit the quota is not re-checked at runtime: the deploy check is the gate, and if the combined figure is over the quota the kit routes refuse while clicks keep counting. Enable the `analytics_health` alert (`docs/alerts.md`) so it reaches you, and set the secret on the test and the production Worker before deploying this change.

**Click funnel in the check (issue #332).** The funnel tables of migration `0011` (`click_src_daily`, `click_country_daily`, `click_seal`, `click_capacity`, `click_budget` and the monthly tables) are part of the combined figure and of the deploy-time check, at the caps the Worker enforces. The caps live in one file, `worker/src/funnelcaps.ts`; `worker/src/clickfunnel.ts` and `worker/src/kit/budgets.ts` both read it, and `worker/test/kit/funnelbudget.test.ts` fails if they drift apart or if a flood writes more rows than the model allows. Per site the model counts, at the caps: the most cells the two day counters can hold (the general pools, the reserved cells of every link id times two destination kinds and two tables, four overflow rows, and never more than the hourly new-cell slices allow), times three writes per cell over its life (insert, new-cell budget upsert, retention delete); plus the seal, the capacity row, 24 hourly click rows, and the month roll-up (monthly caps plus overflow rows, written once and deleted once, all counted on one day so the check fails closed). The sites and their link ids come from the same site list the page-view part counts. Counting on a cell that already exists is click-route traffic and stays inside the click reserve above; that reserve is still an allowance, not a measured bound, so the funnel figure is a bound on cell creation and retention, not on click volume. The step prints it as `funnel N`. A cap raised high enough to push the total over the quota stops the deploy, like any other limit.

*Profiles (Free-first decision, owner, 2026-11-08).* D1 starts on the **Free plan with lower caps**; real row counts are measured for one week before any paid-plan decision. `free` is the default profile (source cells 40 and country cells 50 per site and day, 2 reserved cells per link id, monthly folds at 100 and 120, 12 new cells per site and UTC hour). `standard` keeps the caps the funnel was first written with (110, 160, 2, 300, 400, 150) and is the documented step up. The `free` numbers are provisional: chosen so the default deployment stays under 80 percent of the free write quota, not measured on real traffic (unverified). Moving off `free` is a code change to the `FUNNEL_CAPS` line in `funnelcaps.ts` and shows up in this check. The D1 daily write figure is the constant already used above (`D1_DAILY_WRITE_QUOTA`, default 1,600,000 a day on the paid plan since 2026-11-19; it was 100,000 on the Free plan, when the `free` profile was sized); re-check it against Cloudflare before relying on it. The profile is unchanged by the plan move: this change does not raise any funnel cap.

*Landing and hop tables (issue #566, migration `0017`).* `click_landing_daily`, `click_hop_daily` and their monthly twins are modeled in `funnelcaps.ts` (`FunnelCaps` and `funnelDailyWrites`), and only for a site that lists `landings` or `hops` in its config (a site without the list writes no cells, so the default figure above is unchanged). Free caps: 24 landing and 16 hop cells per site and day, 1 reserved cell per link id and kind in each, monthly folds 40 and 30, and an hour budget of 16 new cells in a slice of its own (`click_budget_lh`), apart from the source and country slice of 12. One site that lists both, per UTC day, in the worst case: 1038 writes with no link ids, 1278 with 5, 1998 with 20 (758, 938, 1424 without the lists). Headroom was small at the Free plan's 100,000 a day: two sites that both list both tables pushed the combined worst case over that quota (at the paid quota now used as the default, 1,600,000 a day, they fit); if the check fails, lower the caps (or the reserved cells) or list fewer tables per site. The measurement week should record rows read as well as written.

*Free-plan rollout (owner decision, 2026-11-19; superseded the same day by the paid plan, which is now the default quota above, so two sites may list both tables; the paragraph is kept for a deployment that sets `D1_DAILY_WRITE_QUOTA` to the Free plan's 100000, where the click ceiling no longer fits at all).* only ONE site (the links page) turns on `landings` and `hops` on the Free plan for now. Two sites that both list both tables model at about 100,400 writes a day (the independent review reproduced 100,472 at 1 link id each and 100,376 at 0), over the 100,000 quota, and the kit routes fail closed (`loadKitLimits` throws `KitLimitsError` when worst exceeds quota). Even two sites with no new tables already model at 99,888 (99.9 percent), because page views cost about 20,000 a day per site, so the new tables only tip a full budget. A second site turns the tables on only after a paid plan or trimmed page-view caps. No cap number changes; the caps above stay as they are.

*Open items (issue #332, criteria 2 and 3 stay open; nothing below is done):*

- [ ] Measure real row counts per funnel table (and rows written per day) on the Free plan for one full week of live traffic.
- [ ] Compare the measured rows with the `free` caps and the figure the check prints; adjust the caps or the model if they disagree.
- [ ] Owner decision on the paid plan after that review (stay on Free, or move to Paid and switch to `standard` or new caps).

**Analytics health and coverage (issues #401, #402).** Migration `0014_health.sql` adds `analytics_coverage` (one row per UTC day, 24 letters; apply it before or with the Worker, nothing breaks if it is missing: the step logs a failure and the cron goes on). Each hourly run (in degraded mode, when the table definition is wrong, it writes nothing and sends nothing) does, before the purges and the rollup, one read batch and one write batch (coverage plus a prune of rows older than 400 days) and, only when the `analytics_health` alert is on and a condition changed, a few more queries for the message; it is set aside at most 8 of the run's `QUERY_BUDGET` and skipped below 12, so keep `QUERY_BUDGET` at 12 or more. About 24 rows written a day. Details: [alerts.md](alerts.md#analytics-health-alerts). Failure modes: [failure-modes.md](failure-modes.md).

**Lead retention.** One source of truth: `privacy.lead_retention_days` in `site.yaml` (1 to 365, default 30). The build records it in the build manifest, `verify-manifest.py` checks it, "Write deploy config" writes it to the Worker as `LEAD_RETENTION_DAYS` (always, so the deployed value is never the Worker's silent default by accident), and the generated privacy page prints the same number. The repository variable `LEAD_RETENTION_DAYS` is optional and only compared: when it is set and differs from the config the deploy stops with an error naming both numbers (an invalid variable stops it too); unset, it changes nothing. The Worker variable `LEAD_RETENTION_DAYS` (1 to 365, default 30; digits only, an invalid value falls back to 30 and is logged as a fixed line, it is not part of the table above because it never closes the gate) sets how long a lead is kept. Each hourly run is ONE metered D1 query (a batch) that deletes at most 80 `kit_events` of expired leads, then at most 20 expired leads that have no events left (a lead goes only after its events, so the cascade never deletes events in bulk and a lead with many events is purged over several runs), and writes the heartbeat. Estimated writes per run: 20 x 4 + 80 x 3 + 1 = 321 (a lead row and its three indexes, an event row and its two indexes; an estimate, not measured on D1), so 24 runs are 7704 a day, within 80 percent of the 10000 kit purge reserve above; the rest is left for the counter purge. **Budget guarantee:** at `QUERY_BUDGET` 9 or more the lead purge runs first and always gets its one query, and the rest of the jobs share the remainder (the rollup gets the budget minus one). Below 9 the rollup wins (8 is the smallest budget in which one rollup day completes, `rollup_budget.test.ts`), the lead purge runs last on whatever is left with no guarantee, and logs `purge` / `over_budget` when it gets none; keep `QUERY_BUDGET` at 9 or more (default 40). It never runs on the 2-minute live cron. Details and the owner deletion command: [privacy.md](privacy.md).

**For the route code that uses these limits:** run the per-client throttle (`checkIpMinute`) before taking from a global route counter, so one client cannot spend the shared budget. A request refused by the throttle must not touch a counter.

### Business inquiry form (`POST /k/inquiry`), not deployed

The form needs, besides the gate's `TOKEN_HMAC_KEY` (so only an unlocked visitor can post): the alert channels with the event `inquiry_received` enabled (the inquiry is delivered as an owner alert and stored nowhere), and a Workers Rate Limiting binding named `INQUIRY_LIMITER` (3 per client per 60 seconds in `worker/wrangler.toml`, placeholders only). What the owner must add, exactly: (1) a `[[ratelimits]]` binding named `INQUIRY_LIMITER` with a namespace id of their own (copy the commented block in `worker/wrangler.toml`: `limit = 3`, `period = 60`); (2) the secret `TOKEN_HMAC_KEY`, the same one the gate signs with, and the secret `VIEW_HASH_SECRET` (at least 32 random bytes, base64, the one the click and page-view limiters already use): the per-client limit is keyed from it so the key is the same in every isolate and after every restart, and without a usable one the route refuses (503) rather than limit per isolate; (3) `inquiry_received` in `alerts.events` and at least one enabled channel. The route fails closed: if the binding is missing or malformed, or the key or `VIEW_HASH_SECRET` is missing or too short, it refuses (503) and sends nothing, and the test `refuses with 503, sends nothing and spends nothing` pins it. It never runs without a limit. It adds no variable of its own: the daily cap on inquiries is `DAILY_ALERT_BUDGET`. An accepted inquiry writes one `counters` row update (key `inquiry`) beside the alert's own quota rows, so the table above has an `inquiry` row (up to `DAILY_ALERT_BUDGET` writes a day, one each) and the checked total includes it. Result pages are `GET /k/inquiry/sent` and `GET /k/inquiry/error`. The route is not live until the gate route (#263) serves the gated page and sets the unlock cookie.

## Secrets and Doppler

Project secrets (this Cloudflare token, later the Mailgun and collector
tokens) live in the Doppler project `reach-around`, with configs `dev`, `stg`
and `prd`. The `dev` config holds the deploy credentials for the test deploy
(and nothing else the deploy does not need). For a local deploy, read the
rotating key value (`CLOUDFLARE_KEY_VALUE`) from the `dev` config:

```
doppler run --project reach-around --config dev --only-secrets CLOUDFLARE_KEY_VALUE -- <command>
```

The deploy workflow reads the rotating key value (`CLOUDFLARE_KEY_VALUE`) from
Doppler over OIDC and uses it directly as the Cloudflare API token. No
`CLOUDFLARE_API_TOKEN` secret is needed in the `dev` config. When Doppler is
not configured, it falls back to a GitHub `test` environment secret named
`CLOUDFLARE_API_TOKEN`. Never paste a token into chat, issues or commits.

**Where the token is available.** The workflow asks the Doppler action for
that one name only, in its `outputs` form: the value is masked and returned as
one step output, and nothing is written to the job environment (`GITHUB_ENV`).
Only the steps that map it into their own `env:` (the presence check and the
three wrangler steps) can read it; other steps and post-run hooks do not
inherit it. A test checks this mapping. Package installs still run before the
Doppler step, and a test fails if an install or an unlisted action is added
after it.

**The token is account-wide.** It covers Pages, D1, Workers Scripts and zone
routes for the whole account, not just this project. Rotation keeps two such
tokens valid at once; if you suspect a compromise, revoke both immediately in
Cloudflare, then issue a new one. Per-operation tokens (one each for D1, the
Worker and Pages) or a dedicated Cloudflare account for this project is the
real isolation. That is an owner decision, tracked in issue #206.

**Service-token fallback.** The `DOPPLER_TOKEN` secret is emergency-only. It is
a long-lived credential stored in GitHub; do not leave it set while OIDC works.

**Test deploy credentials (the `dev` config).** The test deploy reads the `dev` config, not `ci`. It needs only the
rotating key value (`CLOUDFLARE_KEY_VALUE`), and the workflow requests that
name alone. Runtime secrets such as the Mailgun key stay in `stg` and `prd`.
The optional `CLOUDFLARE_PURGE_TOKEN` is not in `dev` either: it stays a GitHub
`test` environment secret so it reaches only the purge step and never the
wrangler steps. To rotate the token automatically, add a Doppler Cloudflare
Tokens rotated secret in `dev` (it provides `CLOUDFLARE_KEY_NAME` and
`CLOUDFLARE_KEY_VALUE`). The deploy uses `CLOUDFLARE_KEY_VALUE` as the API
token directly, so do not add a `CLOUDFLARE_API_TOKEN` secret to `dev`. Create
the two tokens it rotates with identical permissions, as in the token table
above. The value must be at least 8 characters, which a real token always is.

**Identity.** Create a Doppler service account with Viewer on `reach-around`
config `dev` only, and an OIDC identity on it with issuer
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
days old triggers a warning in the test deployment's `counts` job
(`.github/scripts/test-counts.py`), in an instance repository's counts refresh
(`scripts/validate-numbers.py` prints a `::warning::` annotation naming the
platform and the age, never the count), in the build and in
`reach-around check`; the number is still shown until you update it. A fetched
number always wins over a hand-kept one while it is fresh (see
[the numbers file](config.md#the-numbers-file)). The same rule applies to a
`count` and `as_of` typed on a platform item in `site.yaml`.

**Keyless sources** (Mastodon-compatible servers, Bluesky, GitHub, Discord
invites, Matrix) need nothing.

**Keyed sources** read `YOUTUBE_API_KEY`, `TWITCH_CLIENT_ID` and
`TWITCH_CLIENT_SECRET`; a YouTube source that looks the channel up by handle
(`handle_env: YOUTUBE_USERNAME`) also reads `YOUTUBE_USERNAME`. The handle is
not a secret, but storing it as a secret of that name is the supported path for
the template, the same as the keys. Pick where they come from:

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

## Post-deploy smoke and the no-address dump check

Two read-only checks you run by hand against a deployed site (issue #272). Neither needs a credential and neither is run by the workflow yet.

`bash scripts/smoke.sh https://links.example.test` asks the site for its page and the Worker's write-only routes. It checks that the page answers 200 as HTML with a Content-Security-Policy and `nosniff`; that `POST /e/c/<unknown id>`, `POST /e/v/<unknown id>` and `GET /e/v/<unknown id>` each answer an empty 204 (an unknown id records nothing); that no response sets a cookie; and that no response echoes a request header back (the probe header carries `198.51.100.77`, a documentation address). Every check runs even after a failure. Exit 0 passes, 1 fails, 2 is bad usage. `--timeout SECONDS` (a whole number of at least 1, default 15) bounds each request; the URL must be the site root with a host and no credentials, query or fragment, and curl runs with URL globbing off. Point it at the test site, not at production.

`python3 -I scripts/check-dump.py dump.sql` parses a SQL dump of the D1 database (for example `npx wrangler d1 export reach-around-clicks --remote --output dump.sql`, run by you with your own login). The export must carry its schema (the default `wrangler d1 export` does); a dump with no `CREATE TABLE` fails unless you pass `--data-only`, for an export made with `--no-schema`. Rows are still scanned and unexpected columns still refused in that mode. It fails on any table or column outside the allowlist `scripts/dump-schema.json` (whatever it is called), on a view, trigger or table made by SELECT, on a blob, on an expression instead of a literal, and on any value that holds an address or user agent after percent-decoding and joining `||` pieces: dotted, `0x` hex or whole-number IPv4, IPv6 in text or as 32 unseparated hex digits, user-agent shapes, characters a closed-vocabulary column never holds, a value outside its column's list (browser, system, device and so on), or a `--forbid VALUE` you planted. It prints the line number, the kind and a schema name, never a value, and it fails on a dump with no `INSERT` row (`--allow-empty` overrides). A test keeps the allowlist equal to the Worker's migrations, so a new table or column needs a reviewed edit to that file. Numbers are checked per column: every numeric column in the allowlist names a kind with an inclusive range (counters stay under 2^24, timestamps are epoch milliseconds from 2020 to 2099), so a value outside it, including a whole-number address, fails; a number in a column with no rule is always an error, and negative or over-32-bit forms fail unless the kind allows them. Percent-encoding is decoded to a fixed point, and an 8-character hex token (digits only included) or a run of 9 or more digits is refused in every text column that does not declare it expects one (the hash and id columns, the hourly counter keys and counter values; a lead's email may hold a digits-only name below 2^24, but not a bare 8-character hex token, a dotted, octal or hex address, or a longer all-digit name such as a phone number, which is flagged for a human to review). Addresses are decided the way a resolver reads them, not by a pattern per spelling: every decoded layer and every token, including each colon-separated piece (`label:203.0.113.211`, `203.0.113.211:443`), goes through `inet_aton` and `inet_pton` rules plus a lenient decimal reading, which covers dotted, legacy octal (`0313.0.0161.0323`), hex components, the one-, two- and three-component shortenings, mapped and zoned IPv6. The schema comparison is two-way: a table or column the allowlist expects but the dump lacks fails too (Cloudflare's own bookkeeping tables may be absent), and a dump with no schema at all fails unless `--data-only`. Known limit: a short made-up token in a free-text place column (`city`, `region`) matches no check. The Worker test `worker/test/dump_no_raw_ip.test.ts` covers that side: it checks the real schema against the same allowlist and records every value bound to every statement the click route, the view route and the rollup run, then requires that planted request data (addresses, a user agent, a header value) appears in none of them in any form (case, hex, base64, percent-encoding, integer address, any 6-character fragment), and it reads binary bindings (ArrayBuffer, typed arrays, DataView) as bytes, text, hex and base64.

Wiring into `deploy-test.yml` is deliberately not done: the deploy job runs under `harden-runner` in block mode with a fixed allow-list that cannot name the site host, and it holds the Cloudflare token. A smoke step there needs the host added to the allow-list and the token blanked for that step (as the purge step does).

## Deploying the Worker from an instance repo

The Worker and D1 half of the test deploy is a reusable workflow,
`.github/workflows/deploy-worker.yml` (issue #596). An instance repository
calls it for its staging and production Worker, so the config writing, the
budget check, the migrations and the deploy exist once. `deploy-test.yml` still
carries the same steps inline for now: a same-repository `uses: ./...` call
fails zizmor 1.30.1 and `uses: $/...` fails actionlint 1.7.12 (both CI pins),
so a follow-up pull request, after this one is merged, switches `deploy-test.yml`
to call the workflow pinned by commit
(`uses: ChiefGyk3D/Reach-Around/.github/workflows/deploy-worker.yml@<sha> # <version>`),
the same way an instance repo calls it. Tests keep the two copies' deploy
config equal until then.

**The instance template does not call it yet.** An instance repository can call
this workflow, but the shipped template
(`examples/instance-repo/.github/workflows/deploy.yml`) still does the older
inline Worker deploy, with a `secrets.CLOUDFLARE_API_TOKEN` token. Moving the
template to the reusable workflow is deliberately deferred and tracked in issue
#694 ("Instance template: call the reusable Worker deploy workflow instead of
the inline Worker deploy"). Until #694 lands, a repository copied from the
current template does NOT get the OIDC-only reusable deploy; it has to add the
call below itself.

The reusable workflow does not publish the site; the caller does that with its own
Pages step. It reads no GitHub secret: the Cloudflare token comes from Doppler
over GitHub OIDC (the `CLOUDFLARE_KEY_VALUE` secret of one Doppler project and
config), as one masked output that only the wrangler steps read.

**What the caller must provide.** The call goes on a job with
`permissions: { contents: read, id-token: write }` (permissions are checked when
the workflow is parsed, so a job that is skipped at run time still needs them),
after a job that uploads the site manifest as an artifact (the build job of
`deploy-test.yml` and the instance template do). Pin the call by commit, with the
version in a comment, as every other action. The called job runs in the GitHub
environment you name, so that environment's reviewers and deployment-branch rule
gate it, and it refuses any ref other than `main`.

```yaml
  worker:
    needs: validate
    if: github.ref == 'refs/heads/main'
    permissions:
      contents: read
      id-token: write
    uses: ChiefGyk3D/Reach-Around/.github/workflows/deploy-worker.yml@<40-hex> # vX.Y.Z
    with:
      environment: staging
      engine_ref: <40-hex, the commit in engine.lock>
      worker_name: reach-around-clicks-staging
      # The artifact names and the manifest file of the instance template's
      # validate job (a test keeps these equal to its upload steps):
      site_artifact: site-out
      manifest_artifact: site-manifest
      manifest_file: manifest.json
      doppler_identity_id: ${{ vars.DOPPLER_IDENTITY_ID }}
      doppler_config: dev
      cloudflare_account_id: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
      d1_database_id: ${{ vars.D1_DATABASE_ID }}
      ratelimit_namespace_id: ${{ vars.RATELIMIT_NAMESPACE_ID }}
      site_host: ${{ vars.SITE_HOST }}
      zone_name: ${{ vars.ZONE_NAME }}
```

The values come from repository variables (a calling job cannot read an
environment's own variables, so set them at repository level, or pass literals).
None is a secret.

| Input | Required | What it is |
|---|---|---|
| `environment` | yes | The caller's GitHub environment, 2 to 31 characters of `[a-z0-9-]` starting with a letter. Its reviewers and branch rule gate the job. The workflow checks the shape only: it cannot prove the environment exists, and GitHub creates an unprotected one on first use. |
| `engine_repo`, `engine_ref` | `engine_ref` | `engine_repo` must be `ChiefGyk3D/Reach-Around` (any other value is refused; a fork keeps and calls its own copy of this workflow). `engine_ref` is a full 40-character commit (the one in `engine.lock`) or a release tag like `v1.2.3`. Before anything is checked out, a first job asks the GitHub compare API (base the ref, head `main`) and refuses unless the status is `identical` or `ahead`, so only a commit on the engine's `main` deploys. The called job then checks out the commit that call resolved, so a tag cannot move in between. |
| `worker_name` | yes | The Worker name, always passed as `--name`. `worker/wrangler.toml` names the test Worker `reach-around-clicks`, so a staging Worker must be named here, or the deploy would replace the test Worker. No default, on purpose. |
| `manifest_artifact`, `manifest_file` | no | The artifact (default `site-manifest`) and the one file in it (default `site-out.manifest.json`; the instance template uses `manifest.json`). |
| `site_artifact` | no | The built site (default `site-out`), read only to check page ids when `page_views` is `true`. |
| `doppler_identity_id`, `doppler_project`, `doppler_config` | identity, config | The Doppler OIDC identity (an id, not a secret), the project (default `reach-around`) and the config that holds `CLOUDFLARE_KEY_VALUE`. Use a config of the right tier: `dev` for the test and staging Workers, `prd` only for production, as its own approved step. |
| `cloudflare_account_id`, `d1_database_id`, `ratelimit_namespace_id`, `site_host` | yes | The account, the D1 database (lowercase UUID shape; the all-zero placeholder is refused), the click limiter namespace and the site host. Every namespace id is a plain number with no leading zero, so `01001` is refused as a shape and the placeholders `1001`, `1002` and `1004` are refused as numbers. |
| `zone_name`, `route_pattern` | no | `zone_name` must be the site host or a parent domain of it. With a zone, the Worker gets the `/e/*` route. `route_pattern` replaces the default `<site_host>/e/*`: its host must be exactly `site_host` (no wildcard host such as `*` or `*.example.org`, no other host of the zone), only the path may use `*`, and it needs `zone_name`. Both are checked again where the config is written. |
| `sites` | no | A `SITES` JSON object (`docs/analytics-design.md`, section 4), written as the `SITES` var. The Worker then ignores the one-site shorthand, so `site_host` must be one of the hosts named in it. |
| `link_ids`, `lead_retention_days` | no | Only compared with the manifest, which is what is deployed. A difference is a warning. |
| `live_twitch_handle`, `live_kick_handle`, `live_kv_id` | no | The Live poller (`docs/live.md`); a handle needs a real KV id. |
| `page_views`, `page_ids`, `view_limiter_namespace_id`, `views_per_hour`, `bot_views_per_hour`, `account_views_per_hour`, `account_bot_views_per_hour` | no | Page views, exactly as in "Page views" above. Only the text `true` turns them on. |
| `route_budget_gate`, `route_budget_view`, `max_leads`, `max_kit_events`, `daily_alert_budget`, `daily_send_budget`, `d1_daily_write_quota`, `click_daily_write_ceiling` | no | The kit limits and the D1 quota, digits only, as in "Kit limits and the daily write budget". The write-budget check runs with them and stops the deploy when the projection is over quota. |
| `beacon_mode`, `mint_limiter_namespace_id` | no | The beacon token, `off` or `observe` only. |
| `quiet`, `clicks_kill`, `views_kill`, `scheduled_kill`, `kit_kill` | no | Booleans, default false. See below. `quiet` sets the first three on and skips migrations; `kit_kill` is separate and quiet never sets it. |

**Order for a deploy that migrates** (`docs/cutover-runbook.md`, 2.3): call it
once with `quiet: true`. That deploys the Worker with `CLICKS_KILL`,
`VIEWS_KILL` and `SCHEDULED_KILL` all on, and does **not** run migrations. Then
verify the quiet state (the next scheduled trigger returns at once), take the
Time Travel bookmark and the backup, and call it again with `quiet: false`: it
applies `wrangler d1 migrations apply DB --remote`, then deploys the Worker with
the switches off. The Worker stays quiet through the migrations because the
first call's deploy is still live. A normal call does the migrations and the
deploy in that order, as `deploy-test.yml` always did. The three switches can
also be set one by one (`clicks_kill`, `views_kill`, `scheduled_kill`); only a
true value is written, as `"1"`, into the throwaway config.

**The kit is not part of quiet.** The cutover runbook's quiet state also
assumes `KIT_KILL` is engaged (2.2, 2.3), because the three switches cover the
click, view and scheduled paths only. This workflow does not turn it on for
you: pass `kit_kill: true` on every call while the kit must stay closed. It
matters on the normal call too. The deploy config is written fresh each time, so
a call without `kit_kill` (or without a kill switch) deploys the Worker with that
variable removed and the route open again. The Worker reads `KIT_KILL` as a plain
variable (`worker/src/kit/env.ts`: `1` or `true` engaged, anything unreadable
counts as engaged).

**What a quiet deploy does with the new code and the old schema.** A quiet
deploy puts new Worker code in front of a database that has not been migrated
(migration `0016` adds `destination_kind` to `click_events`; the runbook applies
it before the Worker that writes the field). I read the code, I did not run it:
the click route returns on `CLICKS_KILL` as its first statement
(`worker/src/click.ts`, `handleClick`, before any D1 call and before it reads the
body); the view route returns on `VIEWS_KILL` first (`view.ts`); every scheduled
trigger returns on `SCHEDULED_KILL` before any D1 or Live work (`index.ts`); the
kit routes return on `KIT_KILL` first (`kit/gate.ts`, `kit/inquiry.ts`). The one
D1 write before a kill check is `handleMint` (`beacon.ts`) when the beacon
configuration is invalid, and it touches only the old `counters` table. The Live
route reads no D1. So with the switches on, nothing reaches a `0016` table or
column. The migration step comes after this deploy in the runbook order, and
migrations are forward only.

**Rollback with the kill switches.** To stop counting without a rollback, call
it with `quiet: true` (or only the switch you need): the Worker deploys with the
route or the scheduled jobs off and no migration runs. To go back, call it again
with the switches false. Migrations only go forward, so a bad migration is not
undone by a redeploy: restore from the Time Travel bookmark and backup
(`docs/backup-restore.md`). Rolling back the site itself is the DNS step in
"Rolling back".

**Never move `engine.lock` during a 48 hour soak.** `engine_ref` is the pin
that decides which Worker is deployed. Changing it (or the image, or the
workflow pin) in the middle of the staging soak restarts the evidence the
cutover needs, so any engine bump waits until the soak report is written.

**What actually gates a deploy.** The workflow's own checks are shape checks and
a refusal of any ref but `main`; they are not the access control. Read it this
way:

- Callers must not run this from a `pull_request_target` (or any event that
  gives a pull request secrets or an environment). Call it from a push to `main`
  or a manual run on `main` only.
- In a called workflow `github.ref` is, as I read the GitHub documentation, the
  CALLER's ref, so the `main` check tests the caller's branch. UNVERIFIED: not
  confirmed by a run.
- The real controls are who can write to the caller's `main`, and the GitHub
  environment's protection: deployment branches limited to `main` and a required
  reviewer (not the author). An environment that does not exist yet is created
  unprotected on first use, so create and protect it before the first call.
- Concurrency: the called job takes the group `deploy-worker-<worker_name>`
  with `cancel-in-progress: false`, so two calls for one Worker from ONE
  repository do not run at the same time. GitHub scopes a concurrency group to
  the repository that runs it, so two repositories deploying one Worker name are
  NOT serialized; see "Single owner" below. It is not a queue: GitHub keeps at most one pending run per
  group and cancels an older pending one when a newer one arrives. Callers that
  need every call to run, in order, must serialize their own calls (for example
  one call per manual run, or a `needs` chain). A caller may add its own group
  (use a different name).

### Deploy ordering and rollback

The migrations, the Worker and Pages are three separate changes. A failure
between them must not leave an old Worker on a schema it cannot read, so
(issue #768):

1. **Expand, migrate, contract.** The deploy applies migrations FIRST, so for a
   moment (and after any failed deploy or rollback) the previous Worker runs on
   the new schema. Every new migration must therefore be additive: create
   table, create index, add column, insert.
   `python3 -I .github/scripts/check-migrations.py` runs in CI and refuses a
   new migration with `DROP`, `RENAME` or `DELETE FROM`. A migration that must
   remove something carries the line `-- migration-phase: contract` and ships
   in a release AFTER the Worker that stopped using the column or table
   (contract comes last). Migrations 0001 to 0017 predate the rule and are
   exempt.
2. **Previous version recorded.** Before anything changes, the workflow reads
   `wrangler deployments status --json` and keeps the version id that holds the
   traffic.
3. **Probe.** After the Worker deploy, a GET to `https://<site_host>/e/` must
   answer 204 (up to 6 tries, 10 seconds apart). The Worker only counts a POST,
   so the probe proves the Worker serves on its route, not that every feature
   works. It runs only with `zone_name` set and no `route_pattern` (otherwise
   the host to ask is not known here) and says so when skipped.
4. **Rollback.** A failed probe runs
   `wrangler rollback <recorded version> --name <worker> --message ...` and
   fails the job. The schema stays migrated, which is safe only because of
   rule 1. If no previous version could be read (a first deploy), the job says
   so and you roll back by hand: Worker > Deployments > Rollback.
5. **Test deploy, link ids.** `deploy-test.yml` deploys the UNION of the live
   Worker's link ids and the new ones before Pages, then the new ids only after
   Pages succeeded, so a failed Pages deploy never leaves the old site pinging
   ids the Worker dropped. If the live ids cannot be read it deploys the new
   ids only and warns.

Sources: VERIFIED 2026-11-24 from
<https://developers.cloudflare.com/workers/wrangler/commands/workers/>
(`wrangler rollback [VERSION_ID] --name --message`,
`deployments status|list --name --json`, `versions view <id> --json`,
`deploy --message`) and
<https://developers.cloudflare.com/workers/wrangler/commands/d1/> (a failing
migration is rolled back on its own; an applied one is not, and a Worker
rollback does not touch it). INFERRED: the field names inside the JSON that
wrangler prints (`version_id`, a `LINK_IDS` binding with `text`) are not
documented; the helper `.github/scripts/deploy-guard.py` searches for them and
an empty answer means "unknown", which the workflow reports.

### Single owner

One Worker name has one deploying repository. The concurrency group cannot
enforce that across repositories, so each deploy writes
`reach-around-owner=<repository>` into the deployment message and the next
deploy refuses when one of the last 10 deployments names a different
repository. This narrows the window; it is not a lock (two simultaneous first
deploys from two repositories both pass). To hand a Worker to another
repository, deploy once from the new owner by hand (or wait until the old
owner's messages leave the last 10 deployments), then deploy normally.

### Engine release manifest (instance repositories)

`engine.lock` pins the image digest, the repository and the commit, and each is
only shape-checked on its own, so an image from release A with a commit from
release B would pass. The instance template adds `engine.release.json` beside
the lock: the image, repository, commit, tag and D1 schema (highest migration
number) of ONE release. `scripts/read-lock.sh --release` checks that all of
them agree with the lock, the image tag and the `# vX.Y.Z` comment on
`ENGINE_REF`; the preview and deploy workflows and `scripts/deploy-local.sh`
run it. `deploy-local.sh` also checks that a remote of `ENGINE_DIR` is
`github.com/<ENGINE_REPO>`, that the tag in the manifest resolves to the pinned
commit in that checkout, that the checkout's migrations end at the manifest's
schema, and that tracked files are unmodified. The manifest is reviewed in the
same pull request as the lock and is NOT signed: it ties the pins to each
other, not to a publisher. Publishing it with each engine release, and signing
it, is left for the release workflow.

**Doppler identity pins** (one identity per tier, Viewer on one config only; a
`dev` identity must not read `prd`). On the identity's OIDC claims, pin
`sub` to the immutable form described in "Secrets and Doppler" (numeric owner and
repository ids), with the exact environment:

```
repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<exact environment>
```

and, where Doppler lets you add the claim, `job_workflow_ref` to
`ChiefGyk3D/Reach-Around/.github/workflows/deploy-worker.yml@<release ref or main>`
and `ref` to `refs/heads/main`. UNVERIFIED for a called workflow: which of these
claims the token carries when the job is in a reusable workflow (the subject
uses the environment either way; `job_workflow_ref` names the called file). Check
the claims of a real token before relying on any pin beyond `sub`.

**Masking.** The Doppler action masks the token before handing it over, and the
workflow passes it only through the `env:` of three steps. UNVERIFIED: that the
masking holds for every way a step could print it; a step that receives the
token can read it, which is why only wrangler steps get it.

**Egress.** The called workflow has two jobs, both in block mode with explicit
lists (the `validate` job needs `api.github.com` and `github.com` only). Both
lists are unmeasured; see "Egress control".

**Caveats, none of them tested by a run yet.**

- Repository-level variables only for the call. A calling job cannot read an
  environment's own variables, so the `vars.*` passed in `with:` must be set on
  the repository (or passed as literals). Check where each value lives first.
- The Pages job and the Worker call each hold the environment. After the
  follow-up that switches `deploy-test.yml` to the call, the `test`
  environment's reviewer approves twice, once per job. Today it approves once.
- Check the Doppler OIDC subject for the called job. The identity's claim
  mapping decides whether the called job (which runs under the environment named
  in `environment`) may fetch `CLOUDFLARE_KEY_VALUE`; confirm it matches before
  the first run.
- The optional `sites` check uses `jq`, which the GitHub-hosted Ubuntu runners
  have. A self-hosted runner needs it installed.

**Not run for you.** The workflow has not been run end to end by the change that
added it: the first run is on a staging environment, from `main`, with a
reviewer present. Its egress allow-list is unmeasured, like the one in
"Egress control".

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

To store a token by hand (when you do not use the rotating secret), store it
under the name the deploy reads, `CLOUDFLARE_KEY_VALUE`, and let Doppler read
the value from its prompt instead of putting it on the command line (a value
typed as an argument lands in your shell history and the process list):

```
doppler secrets set CLOUDFLARE_KEY_VALUE --project reach-around --config dev
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
doppler run --project reach-around --config dev --only-secrets CLOUDFLARE_KEY_VALUE -- \
  sh -c 'printf "header = \"Authorization: Bearer %s\"\n" "$CLOUDFLARE_KEY_VALUE" \
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
   `reach-around build`. It always supplies a separate `--gated-out` directory.
   When the build produces `kit.html`, `KIT_KV_ID` is required in the settings;
   the script binds that namespace as `KIT` and uploads the file to `kit/v1`.
   Pages receives only the public site output. No token exists in the process
   during the build.
2. **Derives the link ids from the build manifest** (`jq -c '.links | keys'`),
   so the Worker accepts exactly the links the page publishes and the two
   cannot drift.
   It also reads `lead_retention_days` from the same manifest (the site
   config's `privacy.lead_retention_days`, which the privacy page prints),
   refuses anything but a whole number from 1 to 365, and writes it into the
   generated Worker vars as `LEAD_RETENTION_DAYS`. An optional
   `LEAD_RETENTION_DAYS` line in the settings file is only compared: if it
   differs from the config the script stops and names both numbers. The
   manifest is written beside the output with `--manifest`, never inside it.
3. **Generates the wrangler config in the private directory** from
   `worker/wrangler.toml`, replacing the placeholder ids and making `main`
   and `migrations_dir` absolute because the file no longer sits in
   `worker/`.
4. **Checks the shape of every value** (UUID, number, hostname, project name,
   32-character account id, link-id array) before it lands in a file.
5. **Offers `--dry-run`**, which builds, writes the config and prints the
   plan, then stops before any wrangler command. It also asks you to type
   `deploy` to confirm unless you pass `--yes`.
6. **Gives the token only to the wrangler commands** (migrations, gated-kit KV
   upload when present, Worker deploy, Pages deploy), each wrapped in
   `doppler run --only-secrets`.
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

## Redirecting the old /socials path

Cutover item for issue #356 (epic #246). The link page replaces an older page at
`/socials` on the same host, so `/socials` and `/socials/` must redirect to `/`
and keep working for links people already shared. Reach Around only writes the
rule into the build output; it creates nothing in Cloudflare.

1. In the site config add the switch ([`redirects`](config.md#redirects)):

   ```yaml
   redirects:
     socials:
       status: 302
   ```

2. Build. `out/_redirects` now holds two lines, `/socials / 302` and
   `/socials/ / 302`. Without the key there is no `_redirects` file.
3. While testing on the staging host keep `302`. Check by hand with
   `curl -sI "https://YOUR-HOST/socials?x=1"` and `.../socials/`: expect the
   status you set, a `Location` that ends at `/`, and your query string still
   on it. Check one other path (a tab, `/kit/`) still answers 200.
4. For production change `status` to `301` (or delete the line; 301 is the
   default), rebuild and deploy with the cutover.
5. Rollback: remove the `redirects` key and redeploy, or restore the DNS record
   as in the parity doc's rollback plan. A `301` can stay cached in browsers,
   which is why testing uses `302`.

**Verified against the docs.** Read 2026-11-08 at
<https://developers.cloudflare.com/pages/configuration/redirects/>: one rule per
line as `[source] [destination] [code?]`; `#` starts a comment; 301, 302, 303,
307 and 308 are redirects; 302 is the default; the file is plain `_redirects`
at the root of the build output; up to 2,000 static rules; the first matching
rule wins; redirects are always followed whether or not an asset matches.

**Not verified.** The docs do not say whether the visitor's own query string is
carried to a destination that has none, and say nothing about loops. These must
be confirmed on the first staging deploy:

- Pages applies both lines, with and without the trailing slash.
- The visitor's query string is carried to the destination. The file has no
  query part; whether the host forwards it is host behavior.
- A redirect to `/` does not loop or get rewritten by any rule in the zone.
  The config refuses a target of `/socials` or `/socials/`.

If the host does not behave this way, the owner may prefer a dashboard-level
Single Redirect instead. That is a Cloudflare write and an owner decision, and
this repository does not make it. The tests check the `_redirects` text and
that nothing else in the build changes; they do not call a live host.

### Setting the cutover date

On the day the DNS switch is verified end to end (the site answers on the public
hostname through the new path), add the UTC day to the instance config and
redeploy:

```yaml
cutover_date: 2026-11-08
```

Use the UTC day, not the local one, which can differ near midnight. Then run
`reach-around check --config site.yaml --production`; it warns until the date is
set. The media kit page and PDF then print "Live since <date> (UTC)". See
[`cutover_date`](config.md#cutover_date).

## 4. Rolling back

- **Site.** Pages project > Deployments > pick the previous deployment >
  Rollback to this deployment. Or re-run the workflow from an earlier good
  commit on `main`.
- **Worker.** Worker > Deployments > Rollback, or `wrangler rollback
  <version-id> --name <worker> --message <text>` with the token. The deploy
  workflow does this itself after a failed probe (see "Deploy ordering and
  rollback"); it restores code only, never the D1 schema. Pause clicks entirely by deleting the Worker; links keep working
  because the page uses `ping` plus a beacon, not a redirect.
- **D1 migrations.** Migrations are forward only. Ship a new migration to
  undo a change; as a backstop, `wrangler d1 export reach-around-clicks
  --remote` before schema changes. The full backup and restore steps, and the routine that
  uses them, are in [backup-restore.md](backup-restore.md) and
  [operations.md](operations.md).
- **DNS.** Remove the custom domain from the Pages project (or delete the
  `CNAME`). The test hostname stops resolving; nothing else is affected.
- **Token.** If the token leaks or you are unsure, roll or delete it in
  Cloudflare, then update the secret.
- **Stop all deploys.** Disable the workflow in the Actions tab, or remove
  the `test` environment secret.
