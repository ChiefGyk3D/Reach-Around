<!-- SPDX-License-Identifier: Apache-2.0 -->
# `reach-around report`

Prints the funnel (page views, then link clicks, then kit views), geography,
devices, browsers, operating systems and bot share from an export you make yourself. It makes no network call, reads no
token and never talks to Cloudflare. You export from the Worker's D1 database
with a read-only query, then point the command at the file.

```text
reach-around report --input export.json
reach-around report --input export.json --json --days 2
reach-around report --input views.csv --input clicks.csv --geo city
reach-around report --input export.json --csv out/report-csv
```

Exit code 0 means a report was printed. Exit code 2 means a usage problem or an
export that was refused; the error line says why and never repeats a value from
the file.

## What it prints

| Section | Source table | Notes |
|---|---|---|
| Funnel | `view_daily`, `click_events`, `kit_events` | Page views are likely-human views (bot class `h`). Clicks per 100 views is a ratio, not a conversion rate, and a note appears above 100. Clicks are counts, not people. |
| Pages, Sources | `view_daily` | Likely-human only, unless `--include-bots`. Sources are direct, internal, a referrer host, or `utm:<label>`. |
| Geography | `view_geo_daily` | `--geo country` (default), `region` or `city`. The Worker folds small cities and regions upward before the export, so a finer level may show `other`. Country codes only, no coordinates. |
| Link clicks | `click_events` | Per link id. |
| Link clicks by source | `click_events` | `source -> link` pairs, from the closed source label the page sent with the click (the `s` parameter; `none` when the link carried no label, `other` for a label not on the site's list). A count of clicks, never of people, and not tied to any view. Joint suppression with the per-link table: a link's breakdown prints only when every part is at or above the floor and its total is a printed row; otherwise only the link total (or the merged line) prints. The matrix presentation is a later change (#409). |
| Clicks by source and destination, Top flows | `click_src_daily`, `click_src_monthly` | The matrix of "came from Instagram, went to Twitch": rows are source labels (`direct` for a click that carried none, `other` for a label not on the site's list), columns are destinations (link ids), cells are clicks. Top flows is the same printed cells as a ranked `source -> destination` list with a share of the clicks shown (never a conversion), top N then `other`. Released for one fixed period only (`--period`, below). Within a destination, cells under the floor are merged into one `(below the floor)` line built so it bounds nothing (see the section below); a destination that cannot be made safe prints nothing, and when `click_events` is in the export a destination must also be a printed row of the link table. Totals are sums of printed cells only. A note appears when half or more of the shown clicks are `direct`. |
| Clicks by country and destination | `click_country_daily`, `click_country_monthly` | Country codes only (no region or city on clicks), counted for EVERY click when it arrives, whether or not it carried a source, so it does not depend on the raw click rows. Same period, remainder line and floor. The top N countries print; the rest, with the Worker's own `ZZ` (unknown or folded), are summed per destination into `other`. |
| Devices, Browsers, Systems | `view_dim_daily` | Device class, browser family and operating system, from the Worker's closed-vocabulary dimension rows (`dim` = `device`, `browser`, `os`). Likely-human only unless `--include-bots`. A value the Worker folded into `other` is shown as `other`. Floored and suppressed like the pages table, and part of the same view population (see below). Version numbers, screen size, language and network type are not shown. |
| Bots | `view_daily`, `view_dim_daily` | Share of all recorded views per class (likely human, verified crawler as claimed, automated, suspicious) and the reason codes. |

Human-likely is an estimate. Crawlers that never run the page-view script are
invisible to page views. Days are UTC days.

## The privacy floor

No row under 5 views is printed: not a page, a source, a country, a city, a
link, a class, a reason, or a funnel step. `--floor` can raise the number and
cannot lower it. Rows under the floor are merged into one "rows below the floor"
line. That line sits beside exact totals, so a bare remainder could be recovered
by subtracting the shown rows from a total. To prevent it, whenever a table has
any row under the floor, the largest shown row joins the merged line too (this
is why a big neighbour can disappear along with a tiny row). The rule does not
depend on the values. The human class is printed on its own in the funnel and
never joins that line. If a table has only small rows, or with `--include-bots`
the non-human classes cannot be merged safely, every figure derived from page
views (funnel page views and ratios, pages, sources, geography, devices, browsers, systems, class shares) is
withheld and the report says so; clicks, kit figures and bot reasons stay.

That is the whole guarantee, and it is label suppression, the same kind the
Worker applies when it folds small cells (`MIN_CELL`). It is not k-anonymity and
it is not differential privacy. It holds inside one report, and a test enumerates small datasets and checks that no printed report pins a hidden cell of 1 to the floor minus 1. It can still be beaten at the edges by a reader who knows more than the report shows (for example exact bounds on the other rows). It does not hold
against a reader who gets overlapping reports (for example the same export with
two windows one day apart, which differ by that day's exact totals), or who
joins different tables of the same report (a country table and a page table
cut the same views two ways). Publish one fixed-window report, not a series,
and share the report, never the export.

## CSV output

`--csv DIR` writes one file per section into `DIR` instead of printing, and
prints one line naming the count. It cannot be combined with `--json`. The files
are built from the same floored report, so a suppressed cell stays suppressed
and nothing is in a file that the text and JSON do not show. Every row is an
aggregate label and a count; there is no per-visitor row and no unique count.

| File | Columns | Rows |
|---|---|---|
| `metadata.csv` | `key,value` | `generated_at_utc`, `window_since_utc`, `window_until_utc`, `days_with_data` (not in a released `--period` report), `requested_since_utc`, `requested_until_utc`, `requested_days`, `timezone` (always `UTC`), `site`, `privacy_floor_views`, `counted_classes`, `geography_level`, `report_version` |
| `funnel.csv` | `step,value,status` | page views, clicks, kit views, kit unlocks, the two ratios; status `ok`, `below_floor`, `not_in_export`, `not_available`, `n/a` |
| `pages.csv`, `sources.csv`, `geography.csv`, `links.csv`, `devices.csv`, `browsers.csv`, `systems.csv`, `bot_reasons.csv` | `label,views,row_type` | `row`, one `below_floor` row for the merged remainder, or a single row typed `withheld` or `not_in_export` |
| `bot_classes.csv` | `class,name,views,share_percent,row_type` | as above |
| `click_matrix.csv`, `click_countries.csv` | `source,destination,clicks,row_type` and `country,destination,clicks,row_type` | `cell` (a printed cell of a whole destination), or a single row typed `withheld` or `not_in_export` |
| `click_flows.csv` | `flow,clicks,share_of_shown_clicks_percent,row_type` | `row`, one `other` row, or a single state row |

Decision: the generation time and the window go in a separate `metadata.csv`,
not in comment lines at the top of each file, so every section file is a plain
RFC 4180 table (a header row, then data) that a spreadsheet or a parser reads
without special cases. File names, columns and their order are fixed.

Format: UTF-8 without a byte order mark, CRLF line ends, a field with a comma,
a quote or a line break is quoted and a quote is doubled. All dates and times
are UTC and labeled so (`generated_at_utc` is an ISO 8601 stamp ending in `Z`).
Day buckets are UTC days and stay so.

### Display zone and cutover

`--display-zone ZONE` reads the window's first and last instants in one of a
fixed list of zones (UTC, `America/New_York`, `Europe/Berlin`, `Asia/Tokyo` and
others; `reach-around report --help` lists them; any other name is refused and
not echoed). Default: UTC only. Stored and exported data stay UTC. Only when the
flag is given, the text report adds a line naming the zone, the JSON adds a
`display` block (`zone`, `window_start`, `window_end_exclusive`, `note`) and
`metadata.csv` adds `display_timezone`, `generated_at_display`,
`window_start_display` and `window_end_exclusive_display`. `timezone` stays
`UTC`.

`--cutover YYYY-MM-DD` (the config's `cutover_date`) labels the window: the text
report prints "Cutover: <day> UTC; this window is before Reach Around" (or
"after", or "spans the cutover"), the JSON adds `era` (`cutover_utc`, `window`,
`text`) and `metadata.csv` adds `cutover_utc` and `window_era`. A window with
data on both sides is labeled as spanning it; its counts are added together.
Unset, in the future, or no data in the window: nothing is added.

Formula safety: a text cell that starts with `=`, `+`, `-`, `@`, a tab, a
carriage return or a line feed is written with a leading apostrophe, so a
spreadsheet shows it as text and does not run it as a formula. Counts and
ratios are written as numbers. The export loader already refuses labels with
control characters or markup, so this is a second layer. The files are written
owner-only (mode 0600); a symlinked directory or file is refused, and an
existing regular file of the same name is replaced.

What devices are: the Worker's `ua.ts` reduces a browser to a closed family code,
an operating system to a short code and a device to a class, then keeps only the
top values per day and folds the rest into `other`. The report adds the same
floor on top of that.

## Making the export

The export holds only the columns below. Anything else is refused. Every text
column also has a strict format: pages and click link ids are lowercase slugs or
paths, a referrer is a host name, a UTM source is a short slug, a region is a
code of up to three capital letters or digits, a city is letters, spaces and
apostrophes, a dimension value is a short lowercase code and a reason must be
one of the Worker's reason codes. Email-like and token-like values, and values
that look like an IP address or a browser user-agent string after percent
decoding and Unicode normalization, are refused too. A text value that
contains a control character. The Worker never stores an address or a user
agent, so a correct export never trips this. If it does, the export is wrong.

| Table | Columns (all optional columns are listed in brackets) |
|---|---|
| `view_daily` | `day, page, src_kind, src, country, bot_class, views` [`site, day_tz, uniques`] |
| `view_dim_daily` | `day, dim, value, bot_class, views` [`site, day_tz, uniques`]; also feeds Devices, Browsers, Systems and the bot reasons |
| `view_geo_daily` | `day, country, region, city, bot_class, views` [`site, day_tz, uniques`] |
| `click_seal` | `day, sealed_at` [`site`] (needed for a week) |
| `click_month_log` | `month, rolled_at, exhausted_days, folded_clicks` [`site`] (needed for a month) |
| `click_capacity` | `day, marked_at, first_hour, folded` [`site`] (days counting capacity ran out) |
| `click_events` | `ts, link_id` [`site, src`]; the `country` column is always empty now (country lives only in the click counters), do not rely on it |
| `click_src_daily`, `click_country_daily` | `day, link_id, src` (or `country`), `clicks` [`site`] |
| `click_src_monthly`, `click_country_monthly` | `month` (`YYYY-MM`), `link_id, src` (or `country`), `clicks` [`site`] |
| `kit_events` | `ts, kind` [`site`] |

`kit_events` also has a `lead_id` column in the database. Do not select it; a
file that has it is refused. Select `ts, kind` only.

The kit table has no site column in the database. A `kit_events` row with no `site` is counted for the default site (`links`) only. For `--site blog` the kit figures print "not available for this site" (JSON: `null`, listed under `funnel.not_scoped`), never zero, unless every kit row carries a `site` value. A real zero prints `0`; a count of 1 to 4 prints "below the privacy floor" (JSON: `null`, listed under `funnel.withheld`). `--days N` ends at the newest day in any table, kit included. Rows of `click_events` and
`kit_events` are counted by the report; the aggregate tables are read as stored.

Run each query read-only against a copy or with a read-only token, in a place
you control, for example:

```text
wrangler d1 execute <database> --remote --json \
  --command "SELECT site, day, day_tz, page, src_kind, src, country, bot_class, views, uniques FROM view_daily"
```

`wrangler` writes a list of result blocks; the report accepts that shape
directly and recognizes each table by its columns. Several `--input` files may
be given, one table per file at most. A table that is not in the export prints
"not in this export" rather than zero.

Accepted file shapes: JSON `{"version": 1, "tables": {"<table>": [rows]}}`, a
JSON list of rows or of result blocks, or a CSV with a header row (one table per
CSV). Limits: 16 MiB per file, 200,000 rows per table, no duplicate JSON keys,
no `NaN`, a repeated primary key in an aggregate table is refused, files must be
regular files. A symlink as the final path component is refused and so is a pipe; directories earlier in the path are followed as the operating system resolves them, so keep the export in a directory you control.

## Example with synthetic data

Everything below is made up. Save it as `export.json`.

```json
{
  "version": 1,
  "tables": {
    "view_daily": [
      {"day": "2026-11-01", "page": "home", "src_kind": "r", "src": "news.example", "country": "US", "bot_class": "h", "views": 120},
      {"day": "2026-11-01", "page": "home", "src_kind": "d", "src": "", "country": "DE", "bot_class": "h", "views": 60},
      {"day": "2026-11-02", "page": "kit", "src_kind": "u", "src": "newsletter", "country": "US", "bot_class": "h", "views": 40},
      {"day": "2026-11-02", "page": "home", "src_kind": "d", "src": "", "country": "FR", "bot_class": "h", "views": 3},
      {"day": "2026-11-02", "page": "home", "src_kind": "d", "src": "", "country": "US", "bot_class": "a", "views": 30}
    ],
    "view_geo_daily": [
      {"day": "2026-11-01", "country": "US", "region": "NY", "city": "Springfield", "bot_class": "h", "views": 40},
      {"day": "2026-11-01", "country": "US", "region": "NY", "city": "", "bot_class": "h", "views": 120},
      {"day": "2026-11-01", "country": "DE", "region": "", "city": "", "bot_class": "h", "views": 60}
    ],
    "view_dim_daily": [
      {"day": "2026-11-01", "dim": "reason", "value": "ua-declared-bot", "bot_class": "a", "views": 30}
    ],
    "click_events": [
      {"ts": 1793491200000, "link_id": "shop"},
      {"ts": 1793491200000, "link_id": "shop"},
      {"ts": 1793491200000, "link_id": "shop"},
      {"ts": 1793491200000, "link_id": "shop"},
      {"ts": 1793491200000, "link_id": "shop"},
      {"ts": 1793491200000, "link_id": "shop"}
    ],
    "kit_events": [
      {"ts": 1793491200000, "kind": "view"},
      {"ts": 1793491200000, "kind": "view"},
      {"ts": 1793491200000, "kind": "view"},
      {"ts": 1793491200000, "kind": "view"},
      {"ts": 1793491200000, "kind": "view"}
    ]
  }
}
```

`reach-around report --input export.json` prints the funnel with 223 likely-human
page views, 6 clicks and 5 kit views; the geography table shows US and DE and
leaves nothing under the floor out of sight; the bots section shows the automated
share. Add `--json` for the same data as JSON, `--geo city` for cities.

## Not covered

No live read: this command never calls Cloudflare, and the choice of read path
for the owner (a read-only D1 token or a manual export) is a separate decision.
The report does not attribute a click to a source, and it does not show
languages, viewport size or network type (they are in `view_dim_daily` but are
not reported). Devices, browsers and systems are counts per value, never joined
with each other: there is no "Safari on iOS" row, because the Worker stores each
dimension on its own.

## Funnel counters: fixed periods, months and the floor

The Worker keeps two marginal counters (never a three-way cell): source by link
and country by link, per UTC day. They are incremented when a click arrives, so
the country counter covers every click, including those that carry a source (a
raw click row keeps a source label at most, never a country).

**Threat model.** The cell report is run by you, on your own machine, over your
own private data, and you are a trusted operator. The ledger and the checks
below defend against accidents: releasing a week and then a month over the same
days, repeating a period with a partial or stale export, losing the ledger or
restoring an old copy. They do not stop an operator who edits his own files on
purpose, or a hostile caller inside the process. The adversary the design does
defend against is a public visitor to the link page (poisoned cells, flooded
counters, shaped labels).

**Fixed periods, always through a release ledger.** The source by destination
and country by destination tables are released for ONE complete UTC calendar
period, chosen with `--period week|month` and `--offset N` (0 is the latest
complete one; the period containing today is never released), and ONLY with a
release ledger, for weeks as much as months:

```text
reach-around funnel-ledger-init PRIVATE-REPO/funnel-ledger.jsonl   # once
reach-around report --input EXPORT --period week --ledger PRIVATE-REPO/funnel-ledger.jsonl
```

Without `--ledger`, or with a ledger that is missing, unreadable or fails its
checks, the command stops with a message and prints no cell values. Totals-only
output (views, clicks, click rate, the link table over any `--since` / `--until`
window) needs no ledger and prints no source by destination or country by
destination values.

- **Weeks** (Monday start) are read from day cells. A week reaching back past
  the 90 day day-cell range, or crossing its edge, is refused.
- **Months** are read from the monthly rollup rows only.

**Evidence the period is complete.** The export must prove it, or nothing is
released: for a week, a `click_seal` row for each of its seven days (the Worker
seals every closed day, quiet ones too); for a month, its `click_month_log` row
(the rollup marker). Export both tables with the others (the columns are in the
table below). A partial or early export is refused with the reason, and nothing
is recorded.

**Counting capacity (a warning, never a veto).** If the Worker ran out of
counting capacity on a day (an hour's slice of the new-cell budget or the cell
pools, usually a flood of requests), it records the day in `click_capacity` (the
UTC hour it began and how many clicks went into the one `overflow-all` row, each
click counted once) and keeps counting. The hourly budget counts NEW CELLS
created, not clicks, so a flood of clicks on existing cells cannot use it up.
Public traffic must never veto your release, so the release always goes ahead.
The report carries only a COARSE status, in text, JSON (`capacity.status` and
`capacity.warning`) and `metadata.csv` (`counting_capacity` and
`counting_capacity_warning`):

- `none`: nothing ran out; the warning is empty.
- `exhausted`: ANY folding happened in the period. Individual cells may be
  selectively missing or biased even when the share of clicks affected is small,
  so this is never described as slightly incomplete.
- `over_5_percent`: more than 5 percent of the period's clicks went into overflow
  (or capacity ran out with no clicks counted). The tables are incomplete and may
  be biased; you decide whether to publish.

For both non-`none` statuses the machine-readable warning code is
`cell_tables_may_be_incomplete_or_biased`. The exact days, folded clicks and
share are never in a released report or file: run with `--diagnostics` to see
them on your terminal (stderr) only, and do not publish them. A month carries
the exhausted-day and folded-click counts in its rollup marker
(`click_month_log`).

**Quiet periods.** The Worker seals every closed day and logs every closed month
for each configured site, from the day it first saw the site, even with no click
at all (a small `click_funnel_state` row records that day). Catch-up is bounded
at 31 days per site and 8 months per run and never reaches back past the
retention window. So a quiet week or month can be released as an honest empty or
low-count report instead of failing the completeness evidence.

Which kind a period uses never depends on today's date, so the same week or the
same month gives the same cells and the same suppression on any run date
(decided by that period's data alone).

**The release ledger** is an append-only, hash-chained JSON-lines file (the same
approach as the social-stats history), created only by the explicit
`funnel-ledger-init` command (which refuses to touch an existing file), never
silently. Next to it sits a small `.head` file (sequence number and the last
chain hash, written atomically). **Keep both in your private instance repo and
commit them together after each release**, so the history is not lost and a
ledger cut short or replaced by an older copy is noticed: if the ledger is
shorter than its head, or a different chain, or the head is missing on a
non-empty ledger, the command stops and tells you to restore both files together
from git (never edit them by hand; `funnel-ledger-reanchor FILE` rewrites a lost
head only when you know the ledger is whole). The ledger records site, period
kind, first and last UTC day, release time and a digest of the exact input cells
and parameters behind the release. That digest is a hash of private counts: keep
the ledger private, never publish it.

Every cell release (week or month; text, JSON or CSV) is recorded BEFORE
anything is shown, by the one function that builds and records
(`reach_around.funnel.release.release_report`; `build_report` refuses a period).
If the record cannot be written nothing is printed, and a build that fails
writes no record. If a later step (writing the CSV) fails, the period stays
recorded as released, which is the safe direction. The command REFUSES a release
that shares any UTC day with a previously released period of the OTHER kind, in
both directions: a reader holding a week and the month over it could difference
them. Repeating the exact same period is allowed only with the same data (same
digest) and is not written again. If the data behind a period changed (a late or
partial export) the command refuses and says so: there is no correction flag,
because two releases of one period can be differenced. Let late counts appear
in the next period instead.

The floor and the remainder rule apply to the period alone, and the same period
also bounds every other section of that report, so the sections agree. Why not
a free window: a floor applied to a free window can be beaten by asking for
overlapping windows (three days of 3 clicks with a floor of 6: days 1-2 and days
2-3 each print 6, and the daily values follow). So `--period` cannot be combined
with `--since`, `--until` or `--days`, and without `--period` these two tables
print "not released" while the aggregate sections still honor a free window.
`metadata.csv` records the period kind, its start and end, the coarse capacity status and its warning code.

Day cells are kept 90 days; a closed month is rolled into the monthly tables,
kept 25 months. A month period is read from its monthly row only; a week from day cells only.

Suppression is joint across these sections and the link table: within a
destination, cells under the floor merge into one remainder line, and every
total in these sections is a sum of printed cells. The remainder is built so
that it and the order of the printed cells bound nothing: the largest printed
cell always joins it, and more of the largest join until the remainder minus the
larger of the floor and the largest printed cell left is at least the floor
minus one (a reader knows every merged cell is at least that large, so the
hidden sum can be no more tightly bounded than "each cell is under the
floor"). If that cannot be reached, nothing of the destination prints. One
visitor-made tiny cell therefore cannot hide a destination, and no printed
number pins a cell under the floor. The Worker holds a bounded number of rows per site per day (a general pool, a
few reserved cells per destination, and one `overflow-all` destination that
gathers everything past them or past an hour's slice of the new-cell budget); it
prints as the destination `overflow-all` and its small cells follow the same
rule. The sections count clicks, not people: a source is the label on the link
the visitor came in on, and no click is tied to a view or to a person. A high
`direct` share means few shared links carry a label. CSV `metadata.csv` carries
`period`, `period_start_utc` and `period_end_utc`.

Not part of this change: grouping destinations by platform, the landing and hop
tables (#334), device and hour-by-weekday pairs (#410), and a marker for
windows that span the cutover (#371).

## Owner report page (`--html`)

```text
reach-around report --input export.json --days 7 --html owner.html --csv out/csv
```

`--html FILE` writes the owner report as ONE self-contained HTML file: inline
CSS and inline SVG, no script, no font, no image, no external request, and a
content security policy that blocks any. It is dark only, static, and for you
alone (file mode 0600): never publish it and never add it to a site build.
Without `--since`, `--until`, `--days` or `--period`, `--html` covers the last 7
days of data; use `--days 30`, `--days 90` or an explicit UTC range for others.

The page is rendered from the same floored report dictionary as the text, JSON
and CSV output (`reach_around.funnel.owner.owner_view`), never from the export,
so it shows no figure the floored JSON does not hold. Sections: overview tiles
(views, clicks, click rate, each with the change against the previous window of
the same length when both figures are printable), a daily views and clicks chart
and a daily click rate chart (UTC days), the daily table, clicks per link,
sources, devices, browsers, operating systems, countries, and the released
clicks by source and country with the source by link and country by link
tables when `--period` was used. The matching CSV files are `owner_tiles.csv`,
`owner_daily.csv`, `owner_links.csv`, `owner_sources.csv`, `owner_devices.csv`,
`owner_countries.csv`, `owner_clicks_by_source.csv` and
`owner_clicks_by_country.csv`; Grafana can read the same data.

Rules the page follows:

- **Click rate** is clicks divided by likely-human page views in the same window,
  printed only when both counts are printable. A value above 100 is shown as 100
  and labeled "capped" with the raw counts beside it. The per-link figure is
  clicks per 100 views of the page, because a link has no views of its own.
- **Gaps are not zeros.** A UTC day with no exported rows is drawn as a gap. A day
  under the floor is drawn as a tick on the baseline and left out of the rate
  chart. When any day is under the floor the largest printed day is hidden too,
  so the printed total minus the printed days pins no small day; when no day can
  join them the series is withheld.
- The chart axes print only 0 and the largest printed value.
- **Ad-hoc versus released.** The daily series, the previous window and the gap
  marks exist only in the ad-hoc owner report (`--days`, `--since`, `--until`),
  computed from the one requested window and the one previous window through the
  same floors. They are owner-private and can be differenced against another
  window, so the page says so and they must not be published. A released
  `--period` report (through the ledger) carries no daily series and no previous
  window at all, in the JSON, the page and the CSV, so a gap and a day under the
  floor cannot be told apart and two releases cannot be subtracted to recover a
  small day. The ad-hoc path never writes to or feeds the ledger.
- **Released metadata and repeats.** A released report prints the fixed bounds of
  its week or month (never the first and last day that had rows) and no count of
  days with data, so a day with no rows and a day under the floor look the same in
  the JSON, text, metadata CSV and page window line. The ledger entry binds the
  complete public projection of the report (every figure and field, before any
  display zone, cutover label or timestamp), so a repeat of one period whose page
  views or any section differ is refused, not accepted as a repeat.

Not in this version (issue #438 stays open for them): the world map shaded by
views, the Links and Social icons tabs and pagination (the page shows `--top`
rows), and click rate by source, device and country (these need clicks and
views for the same cut; the released clicks by source and country are shown
beside the views tables instead).
