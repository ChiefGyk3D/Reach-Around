# Landing and hop funnel tables: design

Status: design only, no code. Issue: ChiefGyk3D/Reach-Around#566 (deferred from
#331 by the owner decision of 2026-11-11). Consumer: #334 (the report's funnel
section). Related: #332 (Free-plan budget, merged as #578), #329 (source label).

Conventions: a fact read from code or docs carries its file path. A guess is
marked INFERRED. Numbers are estimates until the one-week Free-plan measurement
in `docs/deploy.md` is done. Nothing here changes behavior.

## 1. Decisions already made (not reopened here)

Owner decisions of 2026-11-08 (issue #331 body, repeated in #334):

- Funnel attribution uses a **closed list of sources** in a short parameter
  named `s`, plus a **hop parameter**. Nothing is stored client-side and there
  are no cookies.
- D1 starts on the **Free plan with lower caps**; real row counts are measured
  for a week before any paid-plan decision (`docs/deploy.md`,
  `worker/src/funnelcaps.ts`).
- The owner report is marginal pairs only, each with the floor, never a
  three-way cell (`docs/analytics-design.md` section 6.2, the "Funnel counters"
  decision).

#331 asks for three aggregate tables and "no triple join stored". Two exist
(`click_src_daily` for source to destination, plus `click_country_daily`); this
design adds the landing and hop tables.

## 2. What exists today (read from the repo)

| Fact | Where |
|---|---|
| `s` is read by the click route only for a site that lists `sources`; exactly one value, judged raw against `^[A-Za-z0-9-]{1,30}$`, lowercased, then kept as itself if on the list, else the fixed `other`; missing, repeated or malformed is no source | `worker/src/click.ts` (`clickSource`) |
| `click.js` forwards exactly one label as `?s=` on the click ping; reads nothing else, stores nothing | `src/reach_around/site/static/click.js` |
| Counters: `click_src_daily` and `click_country_daily`, key `(site, day, link_id, destination_kind, label)`, value `clicks`; monthly twins; `click_seal`, `click_budget`, `click_capacity`, `click_funnel_state`, `click_month_log` | `worker/migrations/0011_click_funnel.sql`, `0016_destination_kind.sql` |
| Free profile caps: 40 source cells, 50 country cells, 2 reserved per link id, monthly folds 100 and 120, 12 new cells per site and UTC hour | `worker/src/funnelcaps.ts` |
| Fold: past the caps a new label goes to one overflow row per (site, day, destination kind), link `overflow-all`, label `other` (or `ZZ`); counting never stops | `worker/src/clickfunnel.ts` |
| The deploy-time check models funnel writes at those caps; its extension point names this issue: add the caps to `FunnelCaps` and the writes to `funnelDailyWrites`, nothing else changes | `worker/src/funnelcaps.ts`, `docs/deploy.md` ("Extension point") |
| Report floor of 5, remainder rule, joint suppression, fixed periods only for the funnel counters, a release ledger | `docs/report.md` ("Funnel counters: fixed periods, months and the floor") |
| The report says landing and hop attribution "are not collected and are not inferred" | `docs/report.md`, `docs/analytics-design.md` section 6 |
| "A hop carries only a closed source label" | `docs/audience-analysis-privacy.md` |
| Per-site `sources`: closed list, at most 40 labels, `^[a-z0-9-]{1,30}$`, `other` and `direct` reserved | `docs/analytics-design.md` section 4, `worker/src/sites.ts` |

## 3. Definitions

Three closed labels can travel with a click. Each is one value from a list the
site owner writes in config. Each is parsed with the same code path as `s`
today (`clickSource` in `worker/src/click.ts`, lines 147 to 154): the value is
read through `URL.searchParams`, so percent escapes are decoded first, and the
decoded text is then judged against `^[A-Za-z0-9-]{1,30}$`, lowercased and
looked up in the closed list. The code comment says "judged raw", but the
check runs on the decoded value. See Owner decision 8.

- **Source** (`s`): where the visitor came from (a bio link, a platform). Exists.
- **Landing** (`l`): the page of OUR site the visitor was on when they clicked.
  INFERRED reading of #331. Without any storage the Worker cannot know the first
  page of a multi-page visit, so "landing" is defined as the page the click was
  made on. The build knows the page id when it renders the page, so `click.js`
  sends it as a build-time constant (for example from a `data-page` attribute),
  never from a stored value. See Owner decision 1 for the alternative.
- **Hop** (`h`): the closed label of the OUR surface the visitor came from
  one step earlier, carried in the URL of an internal cross-site link (links
  page to blog, blog to support). One step only, never a chain: each internal
  link overwrites `h`, so a path of three sites cannot be rebuilt from one URL.

A hop is therefore "arrived from our own surface X", a source is "arrived from
outside". They are separate parameters so one never hides the other.

## 4. Closed vocabularies (proposed lists)

Lists live in per-site config next to `sources`, never in code (INFERRED
extension of `docs/analytics-design.md` section 4). Same grammar as a source:
`^[a-z0-9-]{1,30}$`, at most 12 labels each (sources allow 40; these lists are
small by construction), `other` and `none` reserved. A site that does not list
`landings` writes no landing cells; a site that does not list `hops` writes no
hop cells (the same rule `sources` has).

Proposed lists. The site ids and page names are INFERRED from the project docs
and must be checked against the real configs before the build PR.

| Site | `landings` (page of the click) | `hops` (previous surface) |
|---|---|---|
| links page | `home`, `links`, `social`, `support`, `kit`, `live` | `blog`, `support`, `kit`, `live` |
| blog | `home`, `post`, `about`, `support` | `links`, `support`, `kit` |
| support | `support`, `thanks` | `links`, `blog`, `kit` |

Rules:

- A blog post is always `post`, never its slug. Slugs would make the list
  unbounded and tie a row to one article.
- `none` is the label for a missing value. `other` is the label for a valid
  value that is not on the site's list. Its text is never stored.
- A hop label is a surface id (a site), not a person, handle or campaign. It
  has the same grammar as a source label, so the sentence in
  `docs/audience-analysis-privacy.md` ("a hop carries only a closed source
  label") stays true in spirit; Owner decision 6 covers the wording.
- Adding a label is a config change that needs no migration; removing one folds
  its old rows nowhere (they stay as written until retention removes them).

## 5. What one hop row means

Two new day tables and two monthly twins, same shape as the existing counters.
Proposed migration `0017` (INFERRED number: `0016` is the newest in
`worker/migrations`). Every column below except `clicks` is a closed label.

```text
click_landing_daily ( site, day, link_id, destination_kind, landing, clicks )
click_hop_daily     ( site, day, link_id, destination_kind, hop,     clicks )
click_landing_monthly / click_hop_monthly  ( site, month, ... same ... )
PRIMARY KEY (site, day, link_id, destination_kind, landing|hop) WITHOUT ROWID
CHECK: label length 1 to 30 and no character outside a-z 0-9 -, clicks >= 1
```

The CHECK clauses stay small, as 0011 notes, because CI's SQLite has a parser
stack limit (`worker/migrations/0011_click_funnel.sql`).

One row means: on this site and UTC day, this many clicks on this destination
were made by visitors whose click carried this one label. Nothing else.

Worked example. Site `support`, day `2026-11-20`. Twelve people click the
destination `donate` (kind `link`). Seven of them reached the support page from
the links page (the internal link carried `h=links`), three from the blog
(`h=blog`), and two opened the page directly (no `h`). All twelve clicked on the
page the build calls `support`.

| Table | Rows written (site, day, link, kind, label, clicks) |
|---|---|
| `click_hop_daily` | `support, 2026-11-20, donate, link, links, 7`; `... blog, 3`; `... none, 2` |
| `click_landing_daily` | `support, 2026-11-20, donate, link, support, 12` |
| `click_src_daily` (exists) | its own breakdown of the same 12 by `s` |

Each click adds exactly one to each table that is on for the site. The tables
are three separate marginals of the same clicks. They are never joined: there is
no row that says "from `links`, landed on `support`, clicked `donate`", and no
row ties a click to a view, a person or a time finer than a UTC day. A hop of 2
here cannot be combined with a source count to say how many `bio` visitors came
via the blog, because that pair is not stored.

Views are not given hop or landing tables in this design (INFERRED: the owner
decision and #331 are about click funnels, and a view table would grow the
Free-plan budget; a later issue can extend it).

## 6. Caps per site

The same machinery as the existing counters: a general pool of cells per (site,
day), reserved cells per destination, an hourly slice of a new-cell budget,
one overflow row per (site, day, destination kind), counting never stops.

Proposed Free-profile numbers (provisional, INFERRED, not measured):

| Cap | Landing | Hop | Existing source | Existing country |
|---|---|---|---|---|
| General cells per (site, day) | 24 | 16 | 40 | 50 |
| Reserved cells per link id and kind | 1 | 1 | 2 (+1 `direct`) | 2 |
| Fold cap for a closed month | 40 | 30 | 100 | 120 |
| Overflow rows per (site, day) | 2 (one per kind) | 2 | 2 | 2 |

The new-cell hour budget: the Free profile today is 12 per (site, UTC hour)
shared by two tables (`worker/src/funnelcaps.ts`). Four tables can make up to
four new cells per click, so the proposed shared figure is 16. Owner decision 5
asks whether landing and hop get their own slice so a flood of fake source
labels cannot use up the budget for them.

Why the numbers are small: the vocabularies are closed, so the most distinct
cells a (site, day) can ever need is (labels) x (destinations), and a real site
has a handful of both. The caps exist for the rest: bugs, a large future link
list, and a hostile sender (section 12).

`standard` profile numbers for these tables are not proposed here. The
`standard` profile is documented as the step up only after the measurement
(`worker/src/funnelcaps.ts`); inventing them now would be a guess with no
traffic behind it.

## 7. Small-cell fold rule

Two layers, both existing rules reused, none new.

1. **Worker fold (storage).** A label that has no row yet and cannot get a cell
   (pool full, reserved cells used, hour slice spent) is counted in the
   `overflow-all` row with label `other`. The cell itself is never created, so
   an attacker cannot make the table hold a rare label, and the day is recorded
   in `click_capacity` (once per click that folded on any axis, extended to the
   two new axes).
2. **Report floor (release).** Per destination, cells under the floor of 5 merge
   into one remainder built so it bounds nothing; a destination that cannot be
   made safe prints nothing; totals are sums of printed cells
   (`docs/report.md`).

New in this design: joint suppression extends across the landing and hop
sections for the same destination, as it does for source and country today. The
tables are marginals of the same clicks, so their destination totals agree. A
destination that is withheld in one section is withheld in all (INFERRED, the
conservative reading of the existing rule: it costs completeness, never safety).

## 8. Seals

No new seal table. A seal is per (site, day) and says "the Worker adds nothing
to this day any more" (`click_seal`, `worker/migrations/0011_click_funnel.sql`).
The two new increment statements carry the same `WHERE NOT EXISTS (... click_seal
...)` guard as the existing ones (`worker/src/clickfunnel.ts`), so one seal
closes all four tables. A day can never be sealed for two tables and open for
two others.

One addition is needed: a first-day marker. A day sealed before the migration
holds no landing or hop rows and that means "not collected", not zero. Proposed
`click_landing_state (site, from_day)` (INFERRED name), written when the tables
first exist for a site. The report refuses to release a landing or hop section
for a period that starts before `from_day` and prints "not collected for this
period". This mirrors the existing `click_funnel_state` and the report's
"not released" wording.

## 9. Retention

Identical to the existing counters (`docs/analytics-design.md` section 7):

| Data | Kept | Notes |
|---|---|---|
| Landing and hop day cells | 90 whole days, once their month is rolled; hard ceiling 125 days | Same job step as `click_src_daily` |
| Landing and hop monthly rows | 25 months | Sums of a closed month with the fold caps above |
| Seal, budget, capacity | As today | Shared tables, no change |
| Local archive (`analytics-archive`) | 25 closed months | See Owner decision 4 and section 11 |

The month roll-up writes all four monthly tables in the same batch as the
existing two, so `click_month_log` still means "this month is complete for every
table that existed on `from_day`". The archive needs a rule that a month file
is written once (`docs/analytics-design.md` threat B6): landing and hop must
not be added to a month file that already exists, because a changed month is
refused. They appear only in months closed after `from_day`.

**Archive format and cutover (verified against the code).** The v1 record has
exactly the keys `version, site, month, floor, capacity_ok, sources, countries`
(`_KEYS` in `archive.py`, line 58), the reader accepts only
`ARCHIVE_VERSION == 1` (line 310) and a different payload for an existing month
is refused. Adding fields to v1 is therefore not possible. The rule:

- **v2** is a new format version (`ARCHIVE_VERSION` becomes 2 for new writes).
  A v2 record has the v1 keys plus `landings` and `hops`, and every row in
  every list carries `destination_kind` (see section 11, prerequisite).
- **Cutover.** v2 starts at the first month that closes after `from_day`
  (section 8). That month name is recorded as `archive_v2_from_month`
  (INFERRED name) beside `from_day`. Month files before it stay v1, are never
  rewritten and are read unchanged. Months from it on are written as v2 only.
- **Reader.** It accepts v1 and v2. For a v1 file the key set must equal the v1
  set exactly and `landings` and `hops` are "not collected", never zero. For a
  v2 file the key set must equal the v2 set exactly. A v1 file at or after the
  cutover month, or a v2 file before it, is refused as a schema mismatch.
- **Write-once holds.** No existing file is touched; the same data again is a
  no-op and different data is refused, in both versions. v1 rows have no kind
  (the existing `_ROW_KEYS` are `link_id, label, clicks`); they are read as
  before and are not reinterpreted.
- Tests: a v1 month file is read unchanged after the change; a v2 file in a
  pre-cutover month is refused (negative); a v1 file after the cutover is
  refused; a second write of a different v2 payload is refused.

## 10. Free-plan row-count budget (estimates)

Method: extend the model already in `worker/src/funnelcaps.ts`
(`funnelRowsPerSiteDay`, `funnelDailyWrites`). Each cell costs three writes over
its life (insert, the new-cell budget upsert, the retention delete). Per site add
2 (seal and capacity), 24 (hourly click rows) and the month roll-up (monthly
caps plus overflow rows, written once and deleted once). The model counts worst
case, so each figure is an upper bound on cell creation, not on click volume;
counts on an existing cell stay inside the click reserve (8000).

I extended the same arithmetic with the proposed caps (landing 24 and hop 16
general, reserved 1, monthly 40 and 30, hour budget 16, eight overflow rows
because there are four tables and two kinds). One site, writes per UTC day:

| Link ids on the site | Today (`free`) | With landing and hop (proposed) | Difference |
|---|---|---|---|
| 0 | 758 | 1038 | +280 |
| 5 | 938 | 1278 | +340 |
| 20 | 1424 | 1998 | +574 |

Total against the 100000 writes a day constant in `docs/deploy.md`
(`D1_DAILY_WRITE_QUOTA`; that file says to re-check it against Cloudflare
before relying on it, so this is unverified):

| Case | Combined worst case | Share of 100000 |
|---|---|---|
| Today, one site, no link ids | 78898 | 78.9 percent |
| Proposed, one site, no link ids | 79178 | 79.2 percent |
| Proposed, one site, 5 link ids | 79418 | 79.4 percent |
| Proposed, one site, 20 link ids | 80138 | 80.1 percent |

Read this carefully: the headroom is small. The loader warns above 80 percent,
and the 20-link case crosses it, so the build PR must either keep reserved cells
for these tables at 0 (a link keeps only the `none` cell through the general
pool) or lower the general caps. This is the single most important number to
revisit after the week of measurement, and it is why Owner decision 3 exists.
Other sites add their own rows to the check; nothing here sets a site count.

Free-plan rollout (owner decision, 2026-11-19): only ONE site (the links page) turns on `landings` and `hops` on the Free plan for now. Two sites that both list both tables model at about 100,400 writes a day (the independent review reproduced 100,472 at 1 link id each and 100,376 at 0), over the 100,000 quota, and the kit routes fail closed (`loadKitLimits` throws `KitLimitsError` when worst exceeds quota). Even two sites with no new tables already model at 99,888 (99.9 percent), because page views cost about 20,000 a day per site, so the new tables only tip a full budget. A second site turns the tables on only after a paid plan or trimmed page-view caps. No cap number changes.

Rows read, INFERRED and not modeled by the existing check: each click runs
several `count(*)` subqueries over that site and day's cells to decide whether
to fold (`worker/src/clickfunnel.ts`). Two more tables of up to 40 cells add
roughly 80 rows scanned per click, a few hundred more with the reserved-cell
checks. If D1 bills a read per scanned row (INFERRED, check the vendor page
before relying on it), reads, not writes, may be the limit first on a busy day.
The measurement week should record reads as well as writes.

Storage: well under a megabyte per site for 90 days of day cells (tens of bytes
a row, about 140 to 460 cells a day). INFERRED and not a constraint.

### 10.1 Click write ceiling (supersedes "counting never stops" for volume)

Added after a security review: a per-client limit does not bound click-route writes across clients, and each accepted click mutates up to eight rows. The click route now charges every click, before any write, to an atomic per-UTC-day ceiling of 7500 row writes, split equally over the configured sites (5 rows for a site with no optional lists, up to 8 with source, landing and hop), plus a dropped-count cap of 500. The model's click figure is that sum (8000, whatever the number of sites) and is derived from the enforced constants. Past the ceiling a click gets the same empty 204 and only the counting stops. This changes the earlier rule "counting never stops": it still holds across the fold (existing cells and the overflow row keep adding) but no longer holds for volume once the day's write ceiling is spent. It fails closed on purpose, because exhausting the account's D1 write allowance would stop every other feature. Owner decision needed if a higher ceiling or a front-of-D1 aggregation is wanted; see docs/deploy.md, Click write ceiling.

## 11. How #334's report consumes the tables

#334 added the source and country sections; this design only extends the
pattern, so the report change is small.

- **Prerequisite: destination kind in the export key.** Migration 0016 made
  `(site, day or month, link_id, destination_kind, label)` the counter key. The
  Python side did not follow: the four existing click `Spec` entries
  (`src/reach_around/funnel/export.py` lines 148 to 203) list `destination_kind`
  as a column, but their `key` is `(site, day or month, link_id, label)`, so a
  `link` row and a `social_icon` row with the same id and label would be
  refused as duplicates or merged. This must be fixed first, in its own change
  before the new specs: add `destination_kind` to `key` and to `required` on
  those four specs, then carry it through the report grouping, the CSV
  (`csvout.py`), the owner page and the archive rows (a `destination_kind`
  field on each row, in v2). The four new specs are written with kind in the
  key from the start. Test: two rows differing only in kind are accepted and
  kept apart through export, report, CSV and archive (negative: two rows with
  the same kind are still refused as duplicates). The value is only `link` or
  `social_icon`, as the 0016 CHECK says.
- **Export.** `reach_around.funnel.export` gets four more `Spec` entries (the
  list at `src/reach_around/funnel/export.py` lines near 148 to 190 is the
  model: column map, required columns, strict row validation). A missing table
  is "not in export", never zero.
- **Sections.** "Clicks by landing and destination" and "Clicks by hop and
  destination", beside the source and country matrices
  (`src/reach_around/funnel/report.py`). Same rules: fixed period only
  (`--period`), the release ledger and its head file, floor 5, remainder line,
  joint suppression, totals as sums of printed cells.
- **Ratios, not rates.** Each path shows its clicks and its ratio of the shown
  clicks for that destination, computed only from cells that passed the floor
  (the #334 acceptance criterion). A hop ratio is "share of the shown clicks on
  this destination", never a conversion rate.
- **Plain words in the report.** A high `none` hop share means most visits did
  not come through an internal link; it is not a failure. A note appears like
  the existing "half or more are `direct`" note.
- **Not released** (prints "not collected for this period") for periods before
  `from_day` (section 8) or when the export lacks the tables.
- **CSV and owner page.** `owner_clicks_by_landing.csv` and
  `owner_clicks_by_hop.csv` (INFERRED names, following
  `owner_clicks_by_source.csv` in `src/reach_around/funnel/csvout.py`), and two
  more groups in `owner_view` (`src/reach_around/funnel/owner.py`).
- **Docs.** `docs/report.md` drops the sentences "landing and hop attribution
  are not collected" in the same PR that ships collection, not before.
- **Archive and Grafana.** Monthly landing and hop rows pass through the
  report's own remainder rule before the archive writes them
  (`src/reach_around/funnel/archive.py`), as the existing two do.

## 12. Privacy and threat notes

An attacker here is any public sender: the click route is anonymous and
`Origin` is only a noise filter (`docs/analytics-design.md`, A1 and section 6.1).
The click route's request limiter and the hour budget are the cost controls.

| What a crafted value can do | Outcome | Mitigation |
|---|---|---|
| Send thousands of distinct `s`, `h` or `l` values (cardinality) | Nothing new is stored: a value not on the closed list becomes the fixed `other`, a malformed one becomes `none`. The stored label set is bounded by the owner's config, not by the sender | Closed list per site; raw regex judged before lowercasing (Kelvin sign and dotless i are refused); exactly one value or none |
| Cycle through every valid (landing, hop) pair to create many cells | Bounded: tables are marginals, never a pair; cells are at most labels times destinations, then the pool caps | Per-table pools, reserved cells, hour slice, overflow row; the day is marked in `click_capacity` |
| Burn the hour's new-cell budget so real new cells fold | New cells fold to `other` for that hour; counting continues; the report shows only a coarse capacity status | Hour slices per (site, day, hour); optional separate slice for these tables (Owner decision 5) |
| Inflate a real label (forge `h=blog`) | Counts of listed labels can be raised by anyone, as `s` can today | Accepted and already stated: labels are forgeable by an anonymous collector; per-client limiter bounds volume; the report says counts are estimates |
| Inject SQL, HTML or log text through a label | None reaches storage: only list members or the constants `other`/`none` are bound as parameters; the value text is dropped at once. Reports render labels as data and escape them | Bound parameters only (never string-built SQL); `CHECK` on the column; label text from config is validated at load; tests below |
| Use `h` to rebuild a visitor's path | One step only, overwritten at each internal link; no storage, no stored pair with a view | Build emits `h` as a single value; the Worker never reads a chain |
| Read the tables to single out a person | A cell of 1 to 4 is possible in the database; the report floor, remainder rule and joint suppression hide it; the pair that would identify (landing with hop with destination, or source with hop) is never stored | Marginal tables only; schema test that no table holds two label columns |
| Make the report differenceable across sections | Section totals per destination match, but each section is a different partition; the remainder rule is applied per destination in each | Same remainder rule; the joint rule of section 7 |
| A shared URL carries `h` or `s` to another person | Misattribution only (a copied link with `h=links`); the labels are harmless. Not a privacy leak: a label names our own surface or a listed source | Accepted and documented; the privacy page says labels are a closed list |

Privacy contract, restated for the new tables (the do-not-collect list in
`docs/audience-analysis-privacy.md` still holds): no address or part of one, no
user agent, no cookie or browser storage, no identifier, no referrer URL or
query string, no join to a person. New: `l` is a build-time constant, not read
from any storage; `h` is read from the URL of the page the visitor already
holds, as `s` is. The generated privacy page and `docs/privacy.md` need the same
plain-words phrase per new column, enforced by the existing text-coverage test
pattern (`docs/analytics-design.md` section 9, item 12).

Threat-model: add these as additions to T-07 (anonymous cost exhaustion and
forgery) and T-05 (polluted data), as section 14 of the analytics design did
for A1 to A7. The register is `docs/threat-model/reach-around.threat-model.json`;
this design adds no new top-level threat. Rank them at build time.

## 13. Test plan

Tests are written first, per repo practice. Each guard test collects loosely,
asserts strictly (including that something was collected) and has a negative
case proving the guard bites. Reviewers revert-prove each fix.

Worker (vitest, `worker/test`):

1. **Parsing.** For `l` and `h`: one valid listed value is kept; unlisted valid is `other`; missing is `none`; repeated, over 30 chars, non-ASCII, Kelvin sign, dotless i, empty, whitespace, and a percent escape that decodes to anything outside `[A-Za-z0-9-]` (for example `%20`, `%C4%B1`) are all `none` and store no text; a percent escape that decodes to a listed label (`inst%61gram`) is the listed label, exactly as `s` is today (Owner decision 8, recommended A). Add the same case to the `s` tests so the three parameters cannot drift. A site without `landings` or `hops` writes no cell (negative case: a site with them does).
2. **Counting.** One click writes exactly one to each enabled table; sealed day writes nothing; link and social-icon kinds stay separate cells.
3. **Caps and fold.** A flood of distinct valid labels past the pool folds into `overflow-all` and `other`; existing cells keep counting; reserved cells survive a flood of fake labels on another destination; the hour slice caps new cells and folds the rest.
4. **Budget model.** Extend `worker/test/kit/funnelbudget.test.ts`: the modeled rows are at least the rows a real flood writes (negative: a deliberately reduced model fails); `FUNNEL_CAPS` still the only copy.
5. **Rollup.** Months sum landing and hop with the fold caps; seal covers all four tables; retention removes day cells at 90 days and the ceiling at 125; seals and budget rows leave together.
6. **Migration.** `0017` applies on a database that has 0011 to 0016 with data; existing rows are untouched; CHECK refuses a bad label and a count of 0.
7. **No pair stored.** Schema test: no new table has two label columns. Negative case: a table with two fails the test.
8. **Injection.** Quote, semicolon, comment, template and script payloads as `s`, `h`, `l` leave the tables unchanged and the label set equals the config.

Python (`tests/`):

9. Export spec accepts valid rows and refuses an unknown column, a negative or zero count and a label with a space; missing table prints "not in export".
10. Report: floor and remainder per destination, joint suppression across the four sections, `from_day` gate (period before it prints "not collected"), ratios labeled as ratios (#334 criteria), a note when `none` is most of a destination.
11. CSV and owner page include the new files and escape labels (script-like label is inert).
12. Docs: `tests/test_deploy_limits_doc.py` total updated for the new funnel figure; text test that every new stored column has its privacy phrase.

Operational (not CI): the one-week Free-plan measurement records rows written and
rows read per funnel table and compares them with section 10.

## 14. Rollout (INFERRED, for Owner decision 4)

1. Build PR 1: config lists, parser, migration, counters, seal and roll-up, budget model. Ship with the report saying "collected but not yet shown in the report", since the Worker collects from this PR (this supersedes the earlier plan to keep saying "not collected"; the sentence is dropped here, as section 13 says).
2. Let the tables fill during the Free-plan measurement week (`docs/deploy.md` open items) so one week of data covers all six counter tables.
3. Build PR 2: export, report, CSV, owner page, docs (#334's section for these tables).

## Owner decisions

1. **What does "landing" mean?**
   - A (recommended): the page of our site the click was made on, baked into the page by the build, sent as `l`. Nothing stored client-side.
   - B: the first page of the visit, carried forward in the URL across internal links. Richer, but a forwarded entry page plus `h` rebuilds more of a path, and it needs rules for every internal link.
   - C: no landing table; use the link-to-page map the funnel already has (`docs/analytics-design.md` section 6.1). Cheapest, but gives no cut for a destination that appears on several pages.

2. **Does an internal link keep the visitor's `s` source?**
   - A (recommended, chosen 2026-11-19; the link writer is planned and tracked in #593, so until it exists the hop table collects only `none`): the build drops `s` and writes only `h`. The hop table then says "from our surface", the source table says "from outside", with no chain.
   - B: forward `s` along with `h`. Source survives a hop, but it makes the source count mean "first outside source", changing what the existing table means.

3. **Which caps for the Free profile?** (section 10 shows headroom of about 0.8 percent of the 100000 figure at 20 link ids)
   - Decided 2026-11-19: only ONE site (the links page) turns on `landings` and `hops` on the Free plan for now. Two sites that both list both tables model at about 100,400 writes a day (the independent review reproduced 100,472 at 1 link id each and 100,376 at 0), over the 100,000 quota, and the kit routes fail closed (`loadKitLimits` throws `KitLimitsError` when worst exceeds quota). Even two sites with no new tables already model at 99,888 (99.9 percent), because page views cost about 20,000 a day per site, so the new tables only tip a full budget. A second site turns the tables on only after a paid plan or trimmed page-view caps. No cap number changes.
   - A (recommended): landing 24, hop 16, reserved 1, monthly 40 and 30, hour budget 16, as proposed, then trim reserved to 0 if a site passes about 15 link ids.
   - B: smaller still (landing 12, hop 8, reserved 0): fits any link count, but folds earlier.
   - C: wait for the measurement week to set any number, and ship the tables with the existing caps only. Safest on budget; delays the build.

4. **When does the report show them?**
   - A (recommended): collect first (PR 1), measure alongside #332 for a week, then ship the report (PR 2).
   - B: ship tables and report together.
   - C: collect only, show the report after the paid-plan decision.

5. **Hour budget slice for these tables.**
   - A (recommended): a separate slice for landing and hop (one extra small table or a `scope` column rebuild, as 0016 did), so source and country floods cannot starve them and the reverse.
   - B: share the existing slice, raised from 12 to 16. No schema change; one flood can starve all four.

6. **Wording of "A hop carries only a closed source label"** in `docs/audience-analysis-privacy.md`.
   - A (recommended): change it to "a closed label" and say hop and landing labels come from the site's own list.
   - B: keep it and require every hop label to also be on the site's `sources` list (one vocabulary, fewer lists, but mixes "outside" and "our surface").

7. **Where the vocabularies live.**
   - A (recommended): per-site config lists (`landings`, `hops`), like `sources`.
   - B: one global list shared by every site (simpler, but a site shows labels it never uses and the cell count grows).
   - C: fixed in code (no config work, but every new page is a code change and a deploy).

8. **Percent-encoded values of `s`, `h` and `l`.** Verified in `clickSource`: `URLSearchParams` decodes first, so `?s=inst%61gram` is the listed label today.
   - A (recommended): keep one shared parser for all three. Percent escapes that decode to a valid label are accepted as that label; escapes that decode to anything else are `none`. No behavior change for `s`, nothing is stored (the decoded text is only compared against the closed list), and the three parameters cannot disagree.
   - B: reject any raw query value containing `%` for all three. This changes `s` (a visible behavior change, with a test update) and needs a raw-query parser instead of `URLSearchParams`, with its own edge cases (`+`, repeated keys).
   - C: strict for `h` and `l` only. Two parser contracts for one kind of value; not recommended.
