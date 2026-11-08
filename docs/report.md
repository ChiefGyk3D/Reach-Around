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
| `metadata.csv` | `key,value` | `generated_at_utc`, `window_since_utc`, `window_until_utc`, `days_with_data`, `requested_since_utc`, `requested_until_utc`, `requested_days`, `timezone` (always `UTC`), `site`, `privacy_floor_views`, `counted_classes`, `geography_level`, `report_version` |
| `funnel.csv` | `step,value,status` | page views, clicks, kit views, kit unlocks, the two ratios; status `ok`, `below_floor`, `not_in_export`, `not_available`, `n/a` |
| `pages.csv`, `sources.csv`, `geography.csv`, `links.csv`, `devices.csv`, `browsers.csv`, `systems.csv`, `bot_reasons.csv` | `label,views,row_type` | `row`, one `below_floor` row for the merged remainder, or a single row typed `withheld` or `not_in_export` |
| `bot_classes.csv` | `class,name,views,share_percent,row_type` | as above |

Decision: the generation time and the window go in a separate `metadata.csv`,
not in comment lines at the top of each file, so every section file is a plain
RFC 4180 table (a header row, then data) that a spreadsheet or a parser reads
without special cases. File names, columns and their order are fixed.

Format: UTF-8 without a byte order mark, CRLF line ends, a field with a comma,
a quote or a line break is quoted and a quote is doubled. All dates and times
are UTC and labeled so (`generated_at_utc` is an ISO 8601 stamp ending in `Z`).
A viewer-selectable display zone is not part of this command.

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
| `click_events` | `ts, link_id` [`site, country, src`] |
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
