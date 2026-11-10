<!-- SPDX-License-Identifier: Apache-2.0 -->
# Grafana path: `reach-around grafana-export`

Grafana reads one small SQLite file on your machine. It never gets a Cloudflare
token, the Worker's database or a raw table. A local job makes the file from
the same read-only export that [`reach-around report`](report.md) reads (see
"Making the export" there), and only floored daily aggregates reach it.

```
wrangler d1 execute ... --json  >  export.json      (you, read-only token)
reach-around grafana-export --input export.json --output grafana.sqlite3
Grafana SQLite data source      ->  grafana.sqlite3
```

The job makes no network call and needs no token. Creating the read-only
Cloudflare token is an owner step outside this repository.

## Run it

```
reach-around grafana-export --input export.json --output grafana.sqlite3 \
    [--site links] [--floor 5] [--cutover YYYY-MM-DD]
```

- `--input` is repeatable (CSV needs one file per table). The loader is the
  report's: bounded, fail closed, and it refuses a file that carries an
  address, user agent, header, lead column or any column outside the list.
- `--site` picks one site id (default `links`); one file holds one site.
- `--floor` can only be raised (default and minimum 5).
- `--cutover` is the config's `cutover_date`. Once that UTC day has arrived,
  the store carries it in metadata, labels the retained window as `before`,
  `after`, or `spans`, and adds a marker that Grafana can use as an annotation.
- `--output` must be in an existing directory that is not itself a symbolic
  link. The file is built in memory, written beside the target through that
  directory (no link is followed) and moved into place whole, so Grafana never
  sees a half-written file. The job replaces only a file it made (it carries a
  marker in the SQLite header) and refuses a symbolic link, a directory, or any
  other file.
- Exit 0 on success; 2 for a usage problem, a refused export or an unusable
  output path. On failure nothing is written.
- The output is deterministic: the same export gives the same bytes, whatever
  the row order in the input. Nothing in the file is a clock reading.

## The views subset

Only these tables exist. Days are UTC `YYYY-MM-DD`.

| Table | Columns | Content |
|---|---|---|
| `grafana_views_daily` | `day`, `bot_class`, `views` | all four bot classes (`h` likely human, `v` verified crawler as claimed, `a` automated, `s` suspicious) |
| `grafana_pages_daily` | `day`, `page`, `views` | likely-human views per page |
| `grafana_sources_daily` | `day`, `source`, `views` | likely-human views per source: a referrer host, `utm:<name>`, `(direct)`, `(internal)`, `(other)` |
| `grafana_countries_daily` | `day`, `country`, `views` | likely-human views per country (two letters, or `other`) |
| `grafana_clicks_daily` | `day`, `link_id`, `clicks` | link clicks per day and link |
| `grafana_gaps_daily` | `day`, `status`, `reason` | days with no (`gap`) or partial (`partial`) collection and why: `outage`, `quota`, `kill_switch`, `site_kill_switch` (joined with `+` on a partial day). Coverage is account-wide, not per site. Draw these days as gaps; no other table has a row of zeros for them. Empty when `analytics_coverage` is not in the export. Schema version 2 |
| `grafana_annotations` | `day`, `text` | one `Reach Around cutover` marker when an active cutover is supplied |
| `grafana_meta` | `key`, `value` | `site`, `floor`, `schema_version`, plus `cutover_utc` and `window_era` when an active cutover is supplied. Schema version 3 |

Join `grafana_gaps_daily` to every panel whose date range can cross a gap, and
show the reason in the panel annotation or title. See the generic
[collection gaps runbook](collection-gaps-runbook.md) for response and recovery.

Left out on purpose: unique-visitor counts (they do not add up across cells),
region and city, browser, system, language, window size, network type, bot
reasons, the media kit, leads, and every raw row. Click rows are counted; none
is copied.

## The floor

Per day and per table, a cell under the floor is never written alone. The small
cells of that day are merged into one row labeled `(below the floor)`, and only
when the merge reaches the floor. To make that merge safe, whenever any cell is
hidden the smallest shown cell joins the merged row too (the report does the
same). In `grafana_views_daily` the `h` row is never the shown row that gets
pulled into the merge, so if a remainder cannot be made safe without it, the
small classes are dropped. When `h` itself is under the floor it is merged, so
the `(below the floor)` row in that table (it sits in the `bot_class` column)
can hold human views.

This is label suppression, not k-anonymity. Someone who holds two days or two
tables can still subtract one from another. Share a chart, not the file.

The file follows the same 90 day ceiling as every other local copy
(`docs/analytics-design.md`, threat B2): the job keeps only the newest 90 days,
counted back from the newest day in the export for the site, and drops older
rows. Rebuild it from a fresh export and delete old copies.

## Example queries

Each block runs against a store made by the job (a test runs them).

Likely-human views per day (a stat or time series panel):

```sql
SELECT day AS time, views FROM grafana_views_daily WHERE bot_class = 'h' ORDER BY day;
```

Share of not-human traffic per day, among the classes shown. The suppression
row is left out because it can hold human views too, so a day where `h` is
hidden reads as a share of the visible classes only:

```sql
SELECT day, SUM(CASE WHEN bot_class = 'h' THEN 0 ELSE views END) * 100.0 / SUM(views) AS not_human_percent FROM grafana_views_daily WHERE bot_class <> '(below the floor)' GROUP BY day ORDER BY day;
```

Top pages:

```sql
SELECT page, SUM(views) AS views FROM grafana_pages_daily GROUP BY page ORDER BY views DESC LIMIT 10;
```

Sources:

```sql
SELECT source, SUM(views) AS views FROM grafana_sources_daily GROUP BY source ORDER BY views DESC;
```

Countries:

```sql
SELECT country, SUM(views) AS views FROM grafana_countries_daily GROUP BY country ORDER BY views DESC;
```

Clicks per 100 likely-human views (a ratio, not a conversion rate):

```sql
SELECT c.day, c.clicks * 100.0 / v.views AS clicks_per_100_views FROM (SELECT day, SUM(clicks) AS clicks FROM grafana_clicks_daily GROUP BY day) AS c JOIN grafana_views_daily AS v ON v.day = c.day AND v.bot_class = 'h' ORDER BY c.day;
```

Cutover annotation:

```sql
SELECT day AS time, text FROM grafana_annotations ORDER BY day;
```

The `(below the floor)` row is a real total of several small cells, not a
category: filter it out of pie charts, or label it.

## Not covered

- The Grafana side (installing the SQLite data source plugin, dashboards,
  push or scrape sinks) is not part of this job.
- The store is as fresh as the export. A stale export gives a stale chart.
- A funnel with sources joined to clicks is not offered: clicks carry no
  source.
