# Page-view analytics: collector design for the link page and a second site

> **Status: design, not code.** Written for issue #101 on 2026-11-05. It builds on
> what is already on `main` (the page-view route, the daily rollup and the
> retention job, all in `worker/src`) and on the maintainer rulings in #101
> (2026-10-24 and 2026-11-04). Nothing here changes code. The code PR that
> follows must change the privacy text and the threat model in the same PR (see
> [Privacy page](#9-privacy-page-requirements) and
> [Threats added](#14-threats-added-to-t-07-and-t-17)).
>
> **Marking.** Anything that depends on Cloudflare behavior that was not read in
> the vendor documentation while writing this page is marked **UNVERIFIED**.
> The repository's own research (`docs/research/ip-network-datasets.md`, fetched
> 2026-10-24) is cited as "repo research" and was not re-read for this page.
> Every UNVERIFIED item is collected in [section 15](#15-unverified-items-to-close-before-building).

## 1. Goal and scope

The collector answers four questions for each site, and nothing else:

1. Where do visitors come from (referrer host or an allowlisted UTM source)?
2. What do they view (page)?
3. Where do they go (which link or action they click)?
4. Where are they (country, region, city)?

It must tell likely-human traffic from automated traffic and show both. It
serves two sites that share one Worker and one D1 database:

| Site id | What | Page kind | Collection |
|---|---|---|---|
| `links` | The Reach Around link page (this repository) | A small fixed set of tab ids from the build manifest | Same-origin beacon (shipped: `view.js` posting to `/e/v/:pageId`) |
| `blog` | The blog, ChiefGyk3D/no-php-no-cry (blog-side issue no-php-no-cry#84) | Many pages, one per post, from a Hugo build | Edge count with no client script (preferred), beacon as the fallback |

Out of scope: any per-person profile, any cross-site visitor identity, any
third-party call with visitor data, ad or conversion tracking, session replay,
heatmaps, and a public stats endpoint. The Worker never returns stored data.

Hard rules carried over unchanged from #101 and Plan 4
(`docs/superpowers/plans/2026-10-24-plan-4-analytics-funnel.md`):

- No raw IP address is stored or logged, ever. The raw address and raw
  `User-Agent` are read transiently in memory to derive closed categories and
  are then dropped.
- No cookie, `localStorage`, `sessionStorage`, IndexedDB or Cache API entry on
  the visitor's browser. No fingerprinting inputs.
- VPN, proxy, Tor and hosting detection uses range data held locally; visitor
  addresses are never sent to a third party.
- City is on (country, region, city); never coordinates or a postal code.
  Bots are counted separately, not dropped. No site may set `bots: drop`; the shipped `BOTS_MODE=drop` stays an operator emergency valve for budget pressure, and while it is on the dashboard says automated views are not being recorded. Unique visitors use a keyed hash
  that rotates daily.

## 2. What exists today

On `main` (from `worker/src/view.ts`, `viewrollup.ts`, `viewretention.ts`,
`worker/migrations/0005` to `0007`, `src/reach_around/site/static/view.js`,
`src/reach_around/site/privacy.py`):

| Piece | State |
|---|---|
| `POST /e/v/:pageId`: write-only, one uniform empty 204 for every outcome | Shipped |
| Raw table `view_events`, daily-hash table `visitor_seen`, state table `view_meta` | Shipped |
| Daily rollup into three marginal tables (`view_daily`, `view_dim_daily`, `view_geo_daily`) plus a log | Shipped |
| Retention job (raw rows 7 days by default, ceiling 90; aggregates 90 days) | Shipped |
| Bot classifier with closed reason codes (`botclass.ts`) | Shipped |
| Network class column `net` | Column shipped; value is `unknown` for now (no range data loaded) |
| `Sec-GPC` and Do Not Track honored in the Worker and in `view.js` | Shipped |
| Single site: one `SITE_HOST`, one `PAGE_IDS` list | Shipped; this is what multi-site replaces |
| Funnel read model and dashboard panels | Planned (Plan 4 A7 to A9) |
| Privacy page text for page views | Shipped for the link page; does not yet mention the network class or a second site |

So the new work in this design is: the site dimension, the blog collection
path, the network-class data, the funnel and dashboard, and the text that goes
with them.

## 3. Data model

### 3.1 Fields collected per view

| Field | Stored form | Source | Notes |
|---|---|---|---|
| `site` | Slug, `^[a-z0-9][a-z0-9-]{0,19}$` | Derived by the Worker from the matched host (edge) or the route plus matched Origin (beacon). Never free text from the client | **New.** Must be in the primary key of every aggregate table |
| `page` | Link page: tab id from the allowlist. Blog: normalized path (3.2) | Manifest (links); request URL path (blog edge) | Query string and fragment are never stored |
| Referrer host | Host name only, lower case, at most 80 characters | `document.referrer` (beacon) or `Referer` header (edge), cut to the host at once | Never the path or query. `src_kind` `r`. Internal navigation is `i`, none is `d` |
| UTM source | Allowlisted `utm_source`, `^[a-z0-9-]{1,30}$`; anything not on `UTM_ALLOWLIST` is stored as `other` in aggregates | Query string of the viewed URL | `src_kind` `u`. No other `utm_*` key is read |
| Time bucket | See 3.3 | Server clock | |
| Country, region, city | Two-letter country, region code (at most 3 characters), city name (at most 60 characters) | Cloudflare request metadata (`cf.country`, `cf.regionCode`, `cf.city`), `GEO_LEVEL` per site | Never coordinates, postal code, metro code, organization or ASN number. `GEO_LEVEL=region` drops city; `country` drops both |
| Bot class and reason | `h` human-likely, `v` verified crawler (claims only), `a` automated, `s` suspicious; plus a reason code from the closed list in `botclass.ts` | Derived from the user agent, language, rate signal and network class, then dropped | The dashboard calls `h` "likely human", never "human" |
| Network flag | The existing `NetworkClass` values in `botclass.ts`: `residential`, `mobile`, `vpn-proxy`, `hosting-datacenter`, `tor`, `unknown` (the classifier's `datacenter` signal tests `hosting-datacenter`, so the stored name is not renamed) ; crawler vendor is carried by the reason code, not a new column) | Local range data plus the ASN, read transiently (Plan 4 "Network-type dataset") | **Value is `unknown` today.** This is the "VPN/proxy/hosting flag" of #101. `residential` means only "no list matched" and is shown as "likely residential" |
| Device facts | Browser family and major-version bucket, OS family, device class, primary language, window-size letter (`s` `m` `l` `x`) | User agent and `Accept-Language` (transient), beacon `w` letter | Already shipped. The blog edge mode has no window letter (empty) |
| Unique-visitor code | See 3.4 | Keyed hash of the address | Stored only in `visitor_seen`, deleted at the end of the UTC day |

**Never stored, logged, thrown or echoed:** the raw IP address, the raw
`User-Agent`, `Accept-Language`, the full referrer URL, the viewed URL's query
string, the ASN number, any TLS or HTTP fingerprint, any header value from the
request.

### 3.2 Page identity per site

- `links`: the tab id allowlist from the build manifest (`PAGE_IDS`). An id not
  on the list records nothing.
- `blog` in edge mode: the path of a request that **the origin answered with a
  successful HTML page** (decision in [5.2](#52-edge-count-preferred-for-the-blog)),
  normalized: percent-decoded once, lower-cased, query and fragment removed,
  trailing slash normalized, characters outside `[a-z0-9/_.-]` rejected (the view
  is dropped, not rewritten), at most 96 characters, at most 6 segments.
- `blog` in beacon mode: the same normalization, plus membership in a path
  allowlist emitted by the blog build (a file of valid paths pushed to KV).
- Cardinality bound for any site with open-ended pages: at most `MAX_PAGES_PER_SITE`
  distinct pages are tracked per day (proposed default 400). The rest fold into
  the page `other` in the rollup, the same way small cells fold today
  (`MIN_CELL`). This bounds storage even when the allowlist is generous.

### 3.3 Time bucket

- **Shipped:** the Worker rollup (`viewrollup.ts`) buckets by UTC day and
  writes `day_tz = 'UTC'`. The `America/New_York` default belongs to the Python
  site config, not to `worker/src`. This design keeps UTC days everywhere
  (aggregates, funnel, dashboard). A per-site time zone would be a new feature
  with its own daylight-saving work (23- and 25-hour days) and is not specified
  here.
- **Shipped:** raw rows store `ts` rounded down to the minute
  (`Math.floor(now / MINUTE_MS) * MINUTE_MS` in `view.ts`). **Proposal (open
  decision D-A1):** round to the hour instead. The dashboard needs a day, and an
  hour is enough for a time-of-day view; minute precision still lets a row with
  a rare city and a known minute be matched by a person who remembers visiting
  a small blog. Rounding does not affect the rollup, which orders by row id
  (`max_id`), not time.

### 3.4 Unique visitors without storing an IP

- Code = HMAC-SHA-256 of the canonical address (`address.ts`: IPv6 cut to /64,
  spelling variants merged), under a key derived from a server secret, the UTC
  date **and the site id**, truncated to `UV_BITS` bits (default and minimum 64,
  at most 128).
- Including the site id in the key derivation means the same visitor has an
  unrelated code on `links` and on `blog`. There is deliberately no cross-site
  unique count; a visitor of both sites is counted once on each. This is a
  privacy choice that costs one capability nobody asked for.
- The key changes at UTC midnight, so a code cannot link two days. The code
  lives only in `visitor_seen` (no column in common with `view_events`) and is
  deleted at the end of its day whatever the rollup state.
- A uniqueness count is "distinct codes seen that day for that site", set by a
  `first` flag written with the event.
- The secret (`VIEW_HASH_SECRET`) is a Worker secret, never in a committed file.
  Without it the route records nothing (fail closed, as shipped).
- Bots are not added to `visitor_seen` (humans only, as shipped).
- **Cutover day.** Rows already in `visitor_seen` hold site-less codes. After
  the change a returning visitor gets a new `links` code, so both of that
  day's events would set `first = 1`. The migration therefore deletes every
  `visitor_seen` row, and the deploy happens right after 00:05 UTC (the hourly
  run that closes the day), so the overcount window is at most one hour of one
  day. That day's `uniques` may be high by the visitors seen in that window,
  and the rollup log notes the cutover day. The alternative, keeping the old
  derivation until the next UTC day, needs two code paths and is rejected.

### 3.5 Tables

SQLite cannot add a column to an existing primary key, so this is a table
rebuild, not an added column. One migration (proposed `0008_sites.sql`; the
number is not fixed here), run as one atomic batch:

1. `ALTER TABLE view_events ADD COLUMN site TEXT NOT NULL DEFAULT 'links'`
   (a plain column, so a plain add is fine) and an index on `(site, ts)`.
2. For `view_daily`, `view_dim_daily`, `view_geo_daily` and
   `view_rollup_log`: create a new table with `site` as the first primary-key
   column, copy every row with `site = 'links'`, drop the old table, rename
   the new one. (`view_rollup_log` is keyed by `day` alone today, so it must
   be rebuilt too, with `(site, day)`.)
3. `DELETE FROM visitor_seen` (cutover, 3.4). `visitor_seen` keeps its
   `(day, h)` key because codes are site-scoped by construction.
4. Click rows: rebuild `click_events` with `site` (default `links`), see 6.1.
5. Existing rollup state is rewritten for the new keys (`rolled_through`
   becomes per site, below).

### 3.6 Site-aware rollup, late rows, retention and the pull

The shipped job keeps day-only state: one `view_rollup_log` row per `day`,
a global `rolled_through` in `view_meta`, `NOT IN (SELECT day FROM
view_rollup_log)` in the retention probe, and a join on `l.day` when purging raw
rows (`viewretention.ts`, `viewrollup.ts`). With two sites a day sealed for
`links` must not seal it for `blog`. Invariants the code PR must implement and
test:

- A day is sealed per `(site, day)`. The rollup loops over configured sites;
  each has its own checkpoint, `max_id`, re-roll counter and lease state
  (`view_meta` keys gain the site: `rolled_through:<site>`, `rollup_failed_day:<site>`
  and so on).
- Every predicate over `view_rollup_log` and every raw-row join includes
  `site`: an event of site S on day D is purgeable only if `(S, D)` is sealed
  and `id <= max_id` of that row. The "unrolled day" ceiling is evaluated per
  site.
- A late row for `(S, D)` re-rolls only `(S, D)`.
- A site removed from the config keeps its existing rows until they age out;
  retention iterates sites present in the data, not only the config.
- The local pull (Plan 4 A7) reads per-site checkpoints and never treats a
  day as final for a site whose checkpoint has not passed it.
- Test: seal day D for site A only, then run retention and assert site B's raw
  rows of day D survive; and the reverse.

The schema guard test (`worker/test/views/schema.test.ts`) stays the gate: a
new column is allowed only if it is on the guard's list, and the guard
rejects a column whose name suggests an address, user agent, referrer URL,
coordinate, postal code or ASN. The code PR adds `site` to the list and adds a
negative case showing the guard still rejects an `ip` column.

## 4. Multi-site configuration

A single non-secret Worker variable, `SITES`, replaces `SITE_HOST` and
`PAGE_IDS` (both stay accepted as a one-site shorthand for `links`, so existing
deployments keep working). Synthetic example:

```jsonc
{
  "links": {
    "hosts": ["links.example.test"],
    "mode": "beacon",
    "pages": ["home", "support"],
    "link_ids": {"example-one": "home", "example-two": "support"},
    "geo": "city"
  },
  "blog": {
    "hosts": ["blog.example.test"],
    "mode": "edge",
    "pages": "html-200",
    "link_ids": {"support-cta": "*"},
    "geo": "city",
    "utm_allowlist": ["bluesky", "mastodon", "mail"],
    "min_cell": 5
  }
}
```

Rules the loader must enforce (the code PR tests each, and a bad value disables
that one site rather than the whole collector):

- Site id: the slug pattern in 3.1; at most 8 sites (a bound, not a goal).
- `hosts`: lower-case host names only, no scheme, port, path or wildcard, at
  most 4 per site. A host may belong to one site only; a duplicate disables
  both sites and records `view_meta('config_err')`.
- `mode`: `beacon` or `edge`. `pages`: an id list (beacon) or the literal
  `html-200` (edge: any path the origin served as a successful HTML page).
- Per-site overrides: `geo`, `utm_allowlist`,
  `min_cell`, and the raw keep days, each clamped to the global ceilings (raw
  keep at most 90 days, as shipped).
- Per-site kill switch: `VIEWS_KILL` stays global; add `VIEWS_KILL_SITES`, a
  comma list of site ids, read per request like the existing switch.
- Per-site budgets: the hourly human and bot budgets and the per-client cap
  are counted per site (counter keys gain the site id), so a flood on one site
  cannot spend another site's allowance. A small shared account-wide ceiling
  stays above them (the account quota is one resource; see T-07).

The privacy page for each site is generated from that site's config where the
site is built by Reach Around (`links`). The blog is built by Hugo; its privacy
text is its own file and is covered in section 9.

## 5. Collection path options

### 5.1 Write-only beacon (shipped for `links`)

A small script sends one form-encoded `navigator.sendBeacon` per page load to a
route on the page's own origin (`/e/v/:pageId`). Body is at most 256 bytes and
carries only the referrer host `r`, the UTM label `u` and the window letter
`w`. Nothing is stored in the browser. Without `sendBeacon` it does nothing,
and Global Privacy Control or Do Not Track makes it send nothing.

For the blog, a beacon has to reach the collector, which is on a different host
than the blog. Two shapes:

- **Collector route on the blog's own zone** (a Worker route like
  `blog.example.test/e/*`): same-origin, no CORS question, matches `links`.
  Needs the blog's zone to be in the same Cloudflare account (UNVERIFIED
  whether the blog's Pages project allows a Worker route on its custom domain;
  Cloudflare documents routes per zone, which was not read for this page).
- **Cross-origin beacon to a collector host:** the beacon is a simple request
  (a form-encoded or plain-text body is CORS-safelisted, so no preflight;
  UNVERIFIED, from the Fetch standard, not re-read). The response is never
  read, so no `Access-Control-Allow-Origin` header is needed and none should
  be sent. The Worker still checks `Origin` against the site's `hosts`.

Weaknesses that stay: the script is client JavaScript (the blog's rule is
"works with no JavaScript"; this is only an enhancement), it misses visitors who
block it or send GPC, and it is blind to crawlers that do not run script (the
dashboard says so). The `Origin` check is a noise filter only: the sender
controls the header (threat A1).

### 5.2 Edge count (preferred for the blog)

The count happens where the page is served, with no script on the page and no
beacon at all. A Worker in front of the blog (or middleware in the blog's
Pages project) sees every document request with its real `Referer` header, its
real query string (so UTM source is available) and Cloudflare's geography for
that request, then calls the collector in the background.

Design:

1. Count only a request that the origin answered with status 200 and a
   `text/html` content type, `GET` only. This single rule is the main defense
   against path flooding (random paths 404 and are never counted) and keeps
   images, scripts, feeds and other assets out of the numbers.
2. Reduce at the edge, before any call: path normalized (3.2), referrer cut to
   host, UTM read and checked against the allowlist, query discarded.
3. Derive at the edge. The edge Worker holds the raw address, user agent,
   language and ASN only as locals of the shared derivation function (the same
   function the beacon route uses, imported, not copied), reduces them to the
   closed categories (bot class and reason, network class, browser, OS, device,
   language, hashed daily code, geography) and passes **only those closed
   values** to the collector write. When both run in one Worker this is a
   function call and no raw value crosses a boundary. If the edge Worker and the
   collector are separate Workers, the same rule holds across the binding: the
   call carries closed categories only, never the address, user agent, language
   or ASN. Moving raw values across a binding is not adopted; doing so would
   need its own design (authenticated, non-logging envelope, tests) first.
   UNVERIFIED: whether `request.cf` is available to the edge Worker in the
   blog's setup (section 15).
4. Do the count after the response is on its way (`ctx.waitUntil`), so a
   collector outage or slowness never delays a page. Errors are swallowed and
   counted in `view_meta` as a closed class.
5. Do not add a Worker round trip to cached pages unnecessarily: UNVERIFIED
   whether a Pages Functions middleware or a Worker route runs for requests
   that would be served from the cache or as plain static assets, and what
   that costs in request quota. This decides between "Worker route on the
   blog zone" and "middleware in the Pages project" and must be settled by a
   spike before building (section 15, item 3).

Strengths: no client script, so it works for visitors with scripts blocked or
GPC on; the referrer and UTM are the real ones; the site id comes from the
matched host, not from anything the client claims, so outside callers cannot
pollute the blog's numbers through the collector. Weaknesses: it counts
prefetches and link previews and every bot that fetches HTML (the classifier
sees them; the dashboard separates them); it cannot see window size; and it
spends a Worker invocation per page view, which is account quota (T-07).

GPC and DNT: the edge path must honor `Sec-GPC: 1` and `DNT: 1` request headers
by recording nothing, the same as the beacon route does today.

### 5.3 Recommendation

- `links`: keep the shipped beacon. It is already built and tested.
- `blog`: edge count, with the beacon as an opt-in fallback if the spike in
  section 15 shows the edge path costs too much quota or cannot be placed in
  front of the Pages project.
- One collector, one database, two ways in. Both ways go through the same
  derivation function so the privacy contract (raw address and user agent are
  locals of one function) holds in one place and is tested once.

## 6. Funnel definition (source, page, click)

Per site, per UTC day:

- **Source** is the `src_kind`/`src` pair of a view: direct, internal, referrer
  host, or allowlisted UTM label.
- **Page** is the `page` of a view.
- **Click** is a recorded click on a link or action id (the shipped click ping,
  `POST /e/c/:linkId`) whose id maps to that page through the page's link list.
  On the blog a "click" is an outbound or call-to-action link click, counted by
  the same ping mechanism (the `ping` attribute on the anchor needs no script;
  browser support is UNVERIFIED and in some browsers it is off by default, so
  the funnel on the blog is a floor, not a count).
- **Funnel row:** `(site, day, page, views, uniques, clicks, clicks_per_100_views)`.
  `clicks_per_100_views` is empty when views are zero, and carries a note when
  it exceeds 100. It is not a conversion rate and the dashboard says so.
- **Source to click is not attributed.** A click row has no source, so the
  report shows source to page and page to click side by side, and does not
  claim "visitors from X clicked Y". Attribution (storing the source class on
  the click row) was Plan 4 open decision P-1 and stays out of scope here;
  adding it later would be a new column and a privacy-text change.
- Default funnel counts likely-human views only (class `h`); the other classes
  are shown in a separate traffic-quality panel.
- Ids that map to no page (header icons, copy and QR actions) are reported
  under "header and support actions" and never added to a page.

### 6.1 Clicks per site

The shipped click route has one `SITE_HOST` and one global `LINK_IDS`, so it
cannot tell sites apart. For the funnel join the click route must resolve the
site the same way the view route does:

- Each site in `SITES` gets its own `links` list (link and action ids). The
  click route resolves the site from the matched `Origin`/`Ping-From` host
  against `hosts`, then checks the id against **that site's** list. An id listed
  for another site records nothing, so identical ids on two hosts are
  separate rows, never merged by accident.
- The site is never read from the client body or path. As with views, host
  matching is a noise filter (threat A1); the per-site rate limit and budget
  bound forged clicks.
- Blog clicks (outbound or call-to-action links) use `ping` anchors to the
  same route on the blog's host or collector host (section 15, item 7).
- `click_events` gets `site` in the migration (3.5). The funnel joins clicks to
  views on `(site, day, page)` through each site's link-to-page map.

## 7. Retention

| Data | Default | Ceiling | Notes |
|---|---|---|---|
| Raw view rows (`view_events`) | **7 days** (shipped, `RAW_KEEP_DAYS`) | 90 days | Only rolled-up days are purged early; an unrolled day is kept to min(3 x keep, 90 days) |
| Daily visitor codes (`visitor_seen`) | Deleted at the end of the UTC day | n/a | Independent of the rollup |
| Daily aggregates | **90 days** (shipped) | 90 days | No site may exceed 90 days |
| Click rows | 90 days (shipped) | 90 days | |
| Hour counters in `view_meta` | 48 hours (budget), previous hour (cap) | n/a | |

**Deviation to confirm (D-A2).** The request for this design said "90 days
default". The shipped behavior is 90 days for aggregates and 7 days for raw
rows, because raw rows carry city and are the most identifying data. This page
keeps the shipped split and treats "90 days" as the aggregate window and the
hard ceiling for everything. If the maintainer wants raw rows at 90 days too,
the raw window is the thing that changes the privacy page and the small-
audience risk (B4) most.

Per-site raw keep may be set lower than the global value, never higher. Local
copies (the dashboard store, exports, Grafana sinks) follow the same ceiling
(threat B2). Deletion of a person's data on request is not possible by design,
because nothing identifies a person; the privacy text says so and points to
the short raw window instead (threat B3).

## 8. Dashboard views

The dashboard stays the static, no-JavaScript page built locally from pulled
aggregates (Plan 4 A7 and A8). Read-only; the Worker serves no data. A site
selector (one page per site, or a site column) and these panels:

1. **Funnel:** sources, pages with clicks per 100 views, links.
2. **Sources:** table by source kind and label, share per bot class.
3. **Pages:** views and uniques per page, top N, with the `other` fold.
4. **Geography:** country, then region and city per `GEO_LEVEL`; folded cells
   labeled "other" in their parent.
5. **Traffic quality:** daily stacked bars of the four classes, with the notes
   "human-likely is an estimate" and "crawlers that never run script are
   invisible to the beacon" (the edge mode does see them).
6. **Network type:** labeled "likely" in the heading and in every cell
   (residential, mobile, hosting, VPN or proxy, Tor, unknown). Shows the
   proportion of views whose class is `unknown` so a missing dataset is visible.
7. **Visitors:** browsers (family only), OS, language, device class,
   window size. The browser major version is **not** shown: the rollup
   deliberately excludes it (`view_dim_daily` has no such dimension and `bmajor`
   exists only in raw rows), so after the raw window there is no source for it.
   Adding it would need a separately approved aggregate dimension with its own
   retention and privacy treatment.
8. **Collector health:** last rollup success, rollup lag, days rolled in the
   reduced mode, late rows, config errors, network-data age. A stale heartbeat
   is shown in red (Plan 4 A8; the alert for a stale heartbeat is still planned).

Small-cell rule for the blog: cells under the site's `min_cell` (default 5 for
the blog, 3 for `links`) fold into `other` on every panel, and the dashboard
says this is label suppression, not k-anonymity.

## 9. Privacy page requirements

The privacy text for `links` is generated from the config
(`src/reach_around/site/privacy.py`), so it cannot say less than the site does.
The code PR must change the text, the generator and the tests together; a code
PR that changes what is collected without changing this text must fail review.

Requirements for the rewritten disclosure (plain words, American English):

1. Say what is recorded per view: the page, the referring site's name only, an
   optional campaign label, and the window width letter (links only).
2. Say that from the request the server works out and keeps only: browser and
   operating system, device kind, language, **country, region and city**, a
   likely-person-or-automated mark, and **a network type** (for example "looks
   like a hosting provider, VPN or proxy"). This last item is missing from the
   text today.
3. Say that the network type is worked out on our own server from lists we hold,
   and that no address is sent to anyone else.
4. Say that a daily code made from the address is kept for at most the rest of
   the day to count each visitor once, cannot be turned back into the address,
   and differs between our sites, so the sites cannot be linked.
5. Say plainly: **the IP address is never stored**, no cookie or other data is
   put in the browser, and the raw browser identification is not stored.
6. Say the retention in numbers taken from the config, not hard-coded: raw
   records (7 days by default), the daily code (the rest of the day), anonymous
   totals (up to 90 days).
7. Say that a browser sending Global Privacy Control or Do Not Track is not
   counted, in both collection modes.
8. Say that totals for small groups are merged into "other".
9. State that there is no per-person deletion because nothing identifies a
   person, and say how long raw records live.
10. Name the sites the collector serves, and that the count for each site is
    kept separately.
11. The blog's privacy page (no-php-no-cry#84) must carry the same facts for the
    blog, including that counting happens when the page is served (edge mode)
    and so also applies to visitors whose browser blocks scripts. The blog's
    page is changed in the blog repository in the same release.
12. A test must fail when a collected field exists in the code but not in the
    text: for each stored column or class, the generated text contains its
    plain-words phrase (the existing items-versus-features test is the model).

## 10. Abuse limits

All anonymous, so all of T-07 applies. Shipped limits are kept; the new ones
are marked.

| Limit | Value | State |
|---|---|---|
| Uniform response | One empty 204 for every outcome, same headers | Shipped |
| Check order | Kill switch, method, URL/path/id caps, site and page allowlist, GPC and DNT, origin, `Content-Length` cap, burst limiter, bounded body read, derive, budgets, insert; ignored requests spend no budget | Shipped (site lookup is **new**) |
| Beacon body | At most 256 bytes, `Content-Length` required, no `Transfer-Encoding`, read counted and aborted past the cap, 2 second deadline | Shipped |
| URL/path/header caps | URL 2048, path 128, id 40, origin header 256 | Shipped; the blog normalized path cap of 96 is **new** |
| Per-client burst | 5 views per client per 60 seconds, keyed by a purpose-separated keyed hash | Shipped; UNVERIFIED how the Rate Limiting binding behaves across locations (repo research did not cover it) |
| Hourly budgets | 120 human and 30 bot views per hour, plus a per-address cap (default 60, ceiling 1000) | Shipped globally; **per site** is new |
| Cardinality | At most `MAX_PAGES_PER_SITE` pages per day, rest folded; allowlisted UTM only; referrer host length 80 | Shipped for UTM and host; page cap is **new** |
| Edge-mode amplification | Count only 200 HTML `GET`; skip when the site budget is spent without calling the collector's database; sample (count one in N) under pressure, flagged in `view_meta` | **New** |
| Storage | Aggregation bounds rows to sites x days x pages x sources x countries after rollup; raw rows bounded by the budgets x raw keep | Shipped design; per-site makes the bound `sites x` larger, which is why the site count is capped at 8 |
| Per-site kill switch | `VIEWS_KILL_SITES` | **New** |
| Query budget | At most 40 D1 queries per cron run | Shipped |

When a budget is spent the route still returns the same 204 and records
nothing; it does not tell the sender.

## 11. Failure modes

| Failure | Behavior | Visible as |
|---|---|---|
| `VIEW_HASH_SECRET` missing or invalid | Nothing recorded (fail closed), same 204 | Collector health panel: zero views, `view_meta` closed class |
| D1 write fails | Atomic batch rolls back, no counter consumed without an event; same 204 | `view_meta` error class |
| D1 unavailable or slow in edge mode | Page is served normally; the count is dropped | Gap in counts; error class |
| Rollup fails or is cut off | Purge runs first, so retention is never blocked; a stuck day is skipped and later days roll; raw rows of an unrolled day are kept at most min(3 x keep, 90 days) then dropped with `rollup_overdue` set | Heartbeat stale; `rollup_failed_day` |
| Late row after a day is rolled | Full re-roll of the day, or dropped and counted if raw rows are already purged | `rollup_late_rows`, `rollup_late_dropped` |
| Network range data missing, stale or corrupt | Class `unknown`, never blocks a request | Dashboard shows the unknown share; `net_status` |
| Site config invalid or duplicate host | That site records nothing; other sites continue | `config_err` |
| Site added without a privacy text change | Build/CI fails the privacy-coverage test (section 9, item 12) | CI red |
| Account request quota exhausted (free plan) | Pages and links may stop with the analytics; analytics must not be the reason, so the edge path sheds first | Quota alert (account step, `docs/deploy.md`) |
| Collector deployed with a typo in a site's hosts | Beacons are ignored (origin mismatch); edge mode is unaffected because the site is chosen by the matched host | Zero views for that site |
| Cron missed | Next hourly run catches up within its query budget | Rollup lag on the health panel |
| Clock or time-zone change (daylight saving) | Days are UTC, so there is no daylight-saving boundary | Rollup log row counts |

## 12. Operations notes

- The collector runs on the existing Worker and D1 database; a second site adds
  routes or a service binding and one config variable, not a new service.
- Cron stays at one hourly trigger (the Free plan allows five per account;
  repo research, fetched 2026-10-24, not re-read). The weekly network-data
  refresh from Plan 4 needs a second trigger.
- Free-plan CPU limits (10 ms per HTTP request and per cron invocation, from
  repo research) bound the work done per call. This is why the edge path derives
  closed categories only, does one atomic D1 batch, and why range data is a
  binary search over a typed array.
- D1 backups and point-in-time restore may hold raw rows for longer than the
  raw window. The retention period of that feature is UNVERIFIED and the
  privacy text must not claim deletion that a backup undoes (threat B2).

## 13. Verification plan (design level)

Each item names the test the code PR must add. A guard test collects loosely,
asserts strictly (it fails if it collected nothing) and has a negative case.

| Claim | Test |
|---|---|
| No raw IP, user agent or referrer URL reaches any table or log, for both collection modes | Sentinel test: send sentinel strings through the beacon and the edge path, scan every table and every captured console call (extends `no_sentinel_reaches_any_table`) |
| Schema guard rejects an address-like column | Negative case: add an `ip` column in a scratch migration and see the guard fail |
| Site is chosen by host (edge) and cannot be set by the client | Send a body or header naming another site; assert the row's site is the matched one |
| Same address has different daily codes per site and per day | Compute codes for two sites and two dates; assert all four differ |
| Per-site budgets are independent | Spend site A's budget; assert site B still records |
| Random paths are not counted | Edge test with 404 and non-HTML responses; assert zero rows |
| Page cardinality is bounded | Send more than `MAX_PAGES_PER_SITE` distinct valid paths; assert the fold |
| GPC and DNT record nothing on both paths | Header tests, both modes |
| Privacy text covers every stored field | Generated-text test (section 9, item 12) with a negative case that adds a field and fails |
| Retention ceilings hold per site | Retention test with two sites and a per-site lower keep |
| Edge path leaks no raw value across any boundary | Sentinel test on the edge path: the arguments of the collector write contain only closed values; a negative case passes a raw address and fails |
| Rollup, late rows and retention are per site | Seal-one-site tests (3.6) |
| Cutover | Migration test: `visitor_seen` is empty after migration; old rows copied with `site = 'links'`; composite keys present |
| Collector never calls a third party in a request path | Test that fails if `fetch` is called on the request path (carried from Plan 4) |

## 14. Threats added to T-07 and T-17

The register in `docs/security/threat-model.md` uses numbers up to T-36 (T-22 to
T-24 are the editor's). These are written as additions to the existing entries
so no number is claimed here; the code PR renumbers them into the register if the
maintainer prefers separate entries. Rank each with the register's scale at
that time. "Prereq" is what the attacker needs.

### Additions to T-07 (anonymous Worker cost exhaustion and forgery)

| Id | Threat | Prereq | Mitigation | Verification |
|---|---|---|---|---|
| A1 | **Cross-site forgery:** a sender posts beacons naming a site (route or spoofed `Origin`/`Ping-From`) to inflate or poison the other site's numbers | An HTTP client and the public route | Origin check is a noise filter only (F3, F15); per-site and per-client budgets bound the damage; blog edge mode takes the site from the matched host so the blog cannot be polluted this way; dashboard labels counts as estimates | Test: forged Origin cannot exceed the per-client cap; edge row's site equals the matched host |
| A2 | **Path cardinality flood:** random valid-looking paths grow D1 and the aggregates | Any client; trivially cheap | Count only origin-confirmed 200 HTML pages (edge), path allowlist (beacon), `MAX_PAGES_PER_SITE` fold, normalization that drops rather than rewrites | Tests in section 13 (random paths, cap) |
| A3 | **Edge amplification:** every blog request (including crawls and floods of real pages) becomes a collector call, spending Worker and D1 quota that the gate and kit also need | A flood of GETs to real pages | Background `waitUntil` count, site budget check before the database, sampling under pressure, per-site kill switch, account quota alerts | Load test with the budget spent: zero D1 calls; kill switch makes zero I/O |
| A4 | **Cross-site starvation:** one site's flood spends the shared budget so the other site records nothing | A flood on one site | Counters keyed by site, plus a small account-wide ceiling above them | Test: spend A, B still records |
| A5 | **Public or loose cross-origin access to the collector:** a wildcard CORS header, extra routes, or a service-binding entry reachable from the internet lets anyone submit views with forged metadata (country, user agent, address) | Knowing the collector host | Send no `Access-Control-Allow-*` headers at all; the edge-to-collector call carries closed categories only and, if it is a separate Worker, goes through a binding with no public route; collector entry ignores caller-supplied geography on the public route; every request path returns the uniform 204 | Test: no CORS header on any response; public route ignores any smuggled metadata; UNVERIFIED how the binding is exposed |
| A6 | **Config abuse or drift:** a duplicate host, a wildcard host or a site added without a privacy change sends one site's traffic into another or collects without disclosure | A bad config change (insider or mistake) | Strict loader that disables the site on any violation; privacy-coverage test in CI; the config is reviewed like code | Loader tests with bad configs; the privacy test with a negative case |
| A7 | **Cross-site linkage via the daily code:** one code across sites would let the operator, or anyone with D1 read access, follow a visitor between the link page and the blog | Read access to `visitor_seen` | Site id inside the key derivation (3.4); no cross-site unique count | Test: codes differ per site |

### Additions to T-17 (data copies, retention ceiling, deletion)

T-17 is written for lead data; the same reasoning applies to analytics data.

| Id | Threat | Mitigation | Verification |
|---|---|---|---|
| B1 | **Second site outlives its purpose:** raw rows or aggregates of the blog are kept past the maximum, because retention was written for one site | Retention loops over all sites; per-site keep is clamped to the global ceiling; no site exceeds 90 days | Retention test with two sites and a lower per-site keep; negative case with a keep above the ceiling is clamped |
| B2 | **Copies outside the Worker:** the pulled local store, exports, Grafana sinks, D1 backups and point-in-time restore hold city-level raw rows past the raw window | Pull aggregates only, not raw rows; copies follow the 90 day ceiling; list every copy in the deletion registry the T-17 work defines; the privacy text does not claim deletion that a backup undoes; backup retention is UNVERIFIED and must be read from the vendor docs | Test: the pull reads no raw table; registry lists every copy |
| B3 | **Deletion on request is impossible:** nothing identifies a person, so a request to delete "my views" cannot be met | Say so in the privacy text; keep the raw window short; offer the opt-out signals (GPC, DNT) | Privacy text test |
| B4 | **Small-audience re-identification:** on a small blog, a rare city plus a referrer host plus a known minute can single out one person, and a daily code plus a rare city can too | Hour-rounded timestamps (D-A1), `min_cell` suppression, city in raw rows and in the geography aggregate (the aggregates never join it to device facts: separate marginal tables, as shipped; raw rows do keep them together for the raw window), short raw window; accepted residual: one-view cells remain inside `other` and DB readers can read raw rows during the window | Rollup test that no aggregate table joins geography to device facts; suppression test; residual stated in the register |
| B5 | **The network flag becomes a profile:** a VPN or hosting flag stored with city and referrer marks a person as privacy-conscious or as a researcher | In the aggregates the class lives only in `view_dim_daily` (dimension `net`), never joined to geography or source. In raw `view_events` the class sits in the same row as city and source for the raw window; that is a **stated residual**: anyone with D1 read access can combine them until the row ages out, which is why the raw window is short and why the pull reads aggregates only. Option if the maintainer wants it closed: drop `net` from raw rows after the bot classification has used it (D-A7) | Aggregate-level schema test: `net` appears in no aggregate table that has `city` or source columns. No such claim is made for `view_events` |

## 15. UNVERIFIED items to close before building

None of these were read in the Cloudflare vendor documentation for this page.
Close each with a dated read of the docs page, recorded in this file or in
`docs/research/`, before the code that depends on it.

1. Whether `request.cf` city and region are available on the maintainer's plan
   and for a Pages Functions request (Plan 4 task A0 was meant to check; its
   outcome is not recorded in this repository).
2. Whether a Worker route can be attached to the blog's custom domain when the
   blog is a Pages project, and whether a Pages Functions middleware sees the
   original `Referer`, query string and client address.
3. Whether the middleware or Worker route runs for requests served from cache
   or as static assets, and how those invocations count against the request
   quota. This decides edge count versus beacon for the blog.
4. How a service binding between Workers passes request metadata and whether the
   callee is publicly reachable.
5. Rate Limiting binding semantics across locations (per location, period
   options).
6. D1 backup and point-in-time restore retention.
7. Browser behavior of the `<a ping>` attribute (the funnel's click side on the
   blog), and whether the Fetch standard's CORS-safelisted types apply to the
   beacon as described in 5.1.
8. Free-plan limits quoted from repo research (cron CPU 10 ms, five cron
   triggers per account): re-read the limits page before relying on them.

## 16. Open decisions for the maintainer

| Id | Decision | Recommendation |
|---|---|---|
| D-A1 | Round raw timestamps to the hour? | Yes |
| D-A2 | Raw retention default: keep 7 days, or 90 as the request said? | Keep 7 for raw, 90 for aggregates |
| D-A3 | Blog collection: edge count first, beacon fallback? | Yes, after the spike in section 15 items 2 and 3 |
| D-A4 | Cross-site unique count: never (recommended) or add a separate site-less code? | Never |
| D-A5 | Source on click rows (funnel attribution, old P-1)? | Not now |
| D-A7 | Drop `net` from raw rows once classified (closes the raw-row residual in B5)? | Maintainer to decide; it costs the raw `net` column's use in later reclassification |
| D-A6 | Separate register numbers for A1 to A7 and B1 to B5, or fold into T-07 and T-17? | Fold in, with the ids kept as sub-entries |

## 17. Build order for the follow-up code PRs

1. Config loader for `SITES`, the site column migration and per-site counters,
   with the privacy text and the T-07/T-17 register entries in the same PR.
2. Spike: close section 15 items 1 to 4 and record the results.
3. Edge-count Worker for the blog, with the sentinel and cardinality tests.
4. Network-type dataset (Plan 4 A16 and A18) and the privacy text for it.
5. Funnel read model, dashboard panels and the health panel.
6. Blog-side privacy page and template change in no-php-no-cry#84, released
   together with step 3.

Cutover of the link page and the blog waits until both projects are done
(maintainer ruling, 2026-11-04).
