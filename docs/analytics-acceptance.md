<!-- SPDX-License-Identifier: Apache-2.0 -->
# Analytics acceptance: Beacons reconciliation

`scripts/check-analytics.py` compares the daily views and clicks the old provider (Beacons) shows with Reach Around's own numbers for the same window. It is the scripted check behind issue #378 and the parity item A3 in `docs/research/beacons-parity.md` (epic #246).

```
python3 -I scripts/check-analytics.py --beacons beacons-days.csv --ours ours-days.csv
```

It is read-only and offline. It reads the two files you name, prints a table and exits. It opens no network connection, writes no file, touches no datastore and needs no token. Errors name the file, the row and the field, never the value.

## The two input files

Both files have the same shape: one row per UTC day, CSV or JSON (the file name must end in `.csv` or `.json`).

| Column | Required | Meaning |
|---|---|---|
| `day` | yes | `YYYY-MM-DD`, a UTC calendar day from 2000-01-01. |
| `views` | one of the two | Whole number, 0 or more. |
| `clicks` | one of the two | Whole number, 0 or more. |

A metric present in only one file is not compared; the output names it. Extra columns are ignored. JSON is a list of objects or `{"days": [...]}`.

```csv
day,views,clicks
2026-11-01,120,31
2026-11-02,98,22
```

Limits, all enforced: 5 MiB, 10,000 rows, UTF-8, a regular file (a symlink, directory or pipe is refused), no day twice, no negative or fractional count, no `NaN`, no repeated JSON key.

This page defines only the shape it needs. The Beacons export format itself is not assumed: the importer (#376) maps whatever the owner downloads to this shape. Until it exists, build the file by hand or with a spreadsheet. The Beacons file stays out of the repository.

### Reach Around's own numbers

Make `ours-days.csv` from the same local D1 export that `reach-around report` reads (see `docs/report.md`), with read-only queries run in a place you control:

```text
SELECT day, SUM(views) AS views FROM view_daily WHERE site = 'links' AND bot_class = 'h' GROUP BY day
SELECT date(ts / 1000, 'unixepoch') AS day, COUNT(*) AS clicks FROM click_events WHERE site = 'links' GROUP BY day
```

Both queries are scoped to one site (`links` here; use the site id that matches the Beacons page). Without the filter, an export that also holds another site, such as a blog, inflates the numbers and can fail the run for no real reason. Join the two results on `day` into one file. Click timestamps are stored in milliseconds; `date(..., 'unixepoch')` yields the UTC day. Views count the likely-human class only, which is the number to set against Beacons.

## What it does

For every UTC day in the window it compares each shared metric and prints one row:

```text
Window (UTC days): 2026-11-01 to 2026-11-03
day         metric     beacons      ours    diff  result
2026-11-01  views          100        97      -3  PASS
2026-11-02  views          120        40     -80  FAIL
2026-11-03  -                                      GAP (missing in ours)
Summary: 1 pass, 1 fail, 1 gap day(s)
```

- The window is the Beacons file's first to last day, or `--since` and `--until`.
- A day one side lacks is a **gap**. It is reported, never read as zero, and it makes the run fail, because a day nobody can compare is a day nobody verified.
- A day passes when the difference is within `--tolerance-abs` (default 5 views or clicks) **or** within `--tolerance-pct` (default 10 percent) of the larger side. The absolute allowance keeps quiet days from failing on one visitor.
- `--json` prints the same result as one JSON line.
- Exit codes: 0 every compared day passes, 1 a mismatch or a gap, 2 bad usage or bad input.

### Expected differences

The two systems will not agree exactly. These are the known causes, and the tolerance exists to absorb them. If the gap is larger than they explain, treat it as a finding.

| Cause | Direction |
|---|---|
| Bots and crawlers: Reach Around separates them; Beacons does not. Only the likely-human class is compared. | Ours lower |
| Global Privacy Control and Do Not Track are honored; Beacons may count those visitors. | Ours lower |
| Visitors without JavaScript send no page-view beacon. Clicks are counted by the Worker route, so they are less affected. | Ours lower on views |
| Day boundaries: Reach Around uses UTC days. A Beacons day cut in another time zone shifts traffic by a day. | Either |
| Beacons counts unique or sessions differently from raw views. | Either |

### The UTC off-by-one

If the series fail but would match with the Beacons days moved one day, the script says so and names the flag:

```text
Hint: the series match when the Beacons days move -1 day (use --shift-days -1 if its day boundary is not UTC)
```

`--shift-days N` (from -3 to 3) moves every Beacons day by N before comparing. Use it only when you know Beacons cuts its day in a local zone, and record that choice with the result. The edge days of a shifted series can show as gaps; widen the window or set `--since` and `--until` to the days both sides cover.

## When to run it

Keep Beacons live for both runs. Record each result in the cutover pull request as the table or its summary line.

**The 48 hour window (before cutover, parity item A3 and issue #366).** After analytics have been on for two full UTC days on the staging host:

1. Export the Beacons daily views and clicks for the same two days and map them to the file shape above.
2. Make `ours-days.csv` as shown.
3. Run the script with `--since` and `--until` set to the two full days (not the partial day when you started or stopped).
4. Expect exit 0. A gap means one side is missing a day; fix the export or wait for the day to close. A fail needs an explanation from the table above before the cutover goes ahead.

**The 30 day window (after cutover, before the old provider is cancelled, items D2 and D3).** After the Beacons history is imported (#376) and Reach Around has been live for 30 days:

1. Repeat the steps for the full 30 UTC days since the cutover date.
2. Run it with the default tolerance, then record the table.
3. The old provider is cancelled only after this run passes, the export from week 1 (D1) is stored in the private instance repository, and the imported history is shown in its own labeled block (C3).

## The synthetic visit and click (issue #378, item B3)

The script compares numbers; the second acceptance check proves the path. On the staging host, after `scripts/smoke.sh` passes:

1. Open the page once from a labeled source (a URL with the configured source label) in a normal browser with Do Not Track off, and click one outbound link.
2. Within the hour, make a fresh read-only export and run `reach-around report --input export.json --days 1`. The page view (under its source) and the click (under its link id) appear. The report does not yet tie a click to its source, so the source-to-destination pair is parity item B1 and is checked when #329, #409 and #398 land, not here. The hourly rollup runs on the Worker's cron, so a view can take up to an hour to show in the rollup tables; a click shows from the raw table at once.
3. Note the result in the cutover pull request. Do not paste the export.

## Not covered

- Follower counts and platform numbers. This script compares link-page views and clicks. Tier 1 follower and view numbers are checked against what the platform dashboards show, by hand, using the as-of dates in the report (parity item A2).
- Whether either export is right. It compares two files; it cannot tell which one is wrong.
- Unique visitors. The shape has no column for them.
