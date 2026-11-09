<!-- SPDX-License-Identifier: Apache-2.0 -->
# Failure modes and recovery

A short runbook for the analytics side of Reach Around: what can go wrong, what
you see, and what to do. It is generic: names are the ones in this repository's
config, not those of any one deployment. Times are UTC. The commands show the
shape; check the current Cloudflare documentation for the exact flags before you
use one on a live system, and do not paste a token or a webhook URL into a ticket
or a chat.

The rule that holds through all of it: **a missing stretch is a gap, never a
zero.** If nothing was collected, the report and the Grafana export say so with a
reason ([report.md](report.md#gaps)); they never print a number built over the
gap as if the gap were quiet.

## What the system watches

With the `analytics_health` alert on ([alerts.md](alerts.md#analytics-health-alerts))
the hourly cron tells you when the rollup is late, stuck or behind, when no views
arrive on a site that had some, when the hourly view budget is hit and when the
alert budget is nearly spent. It records hourly coverage whether or not alerts
are on. It cannot tell you that the cron itself stopped: for that, add an
external probe (any uptime monitor that requests the link page and checks the
status code) and keep it pointed at the public page, not at the event routes.

## By alert or symptom

| You see | Likely cause | Do this |
| --- | --- | --- |
| `rollup_overdue` | The rollup keeps failing, the cron stopped, or D1 is down | Check the Worker's cron runs and logs in the Cloudflare dashboard. The heartbeat in `view_meta` names a closed class: `rollup_err` is `none`, `mismatch` or `db`. `db` is a database error: retry after Cloudflare status clears. Raw rows are kept until the purge can safely delete them, so a late rollup loses nothing inside the raw keep window (`RAW_KEEP_DAYS`, default 7). |
| `rollup_stale` | A day failed to roll and later runs stop at it | The key `rollup_failed_day:<site>` names the first failed day. Fix the cause (usually `db`), then the next hourly run continues from that day. Past the raw keep window a day cannot be rebuilt; it stays a gap. |
| `zero_views` | The page-view script was removed or blocked, the route is mis-routed, the kill switch is on in another form, or a real outage | Open the public page and watch the network tab for the beacon. Check `VIEWS_KILL`, `VIEWS_KILL_SITES`, the route pattern and that `VIEW_HASH_SECRET` is set (without it the route records nothing). |
| `view_budget_hit` | Traffic above `VIEWS_PER_HOUR` or the account ceiling, or a flood | Views over the budget are dropped, not queued: that stretch is a `quota` gap in the report. If the traffic is real, raise the budgets within the D1 daily write allowance (see below). |
| `alert_budget_near` | Many alerts today | Fix what is alerting. The budget is `DAILY_ALERT_BUDGET` per UTC day; at zero no alert is sent. |
| A day shows as `outage` in the report | The Worker did not run that hour | See the Cloudflare status page and the Worker's deployments. Nothing to rebuild: the day stays a gap. |
| A day shows as `kill_switch` | `VIEWS_KILL` (or every site in `VIEWS_KILL_SITES`) was on when the hour was sampled | Expected after you flipped it. |
| A day shows as `site_kill_switch` | `VIEWS_KILL_SITES` named some sites. Coverage is account-wide, so the report cannot say which | Expected; check the list you set. |

A health alert is recorded per channel as soon as a channel takes it. A crash or
a failed write between delivery and the record can repeat the same alert once per
hour until the condition clears or the daily alert budget runs out (at most one
repeat per condition per hour). That is a known, bounded limit.

## Kill switches

Each is a plain Worker variable, read on every request, and turns a route into
its usual empty 204 that touches neither D1 nor the rate limiter. They are for
an incident, a flood or a bill you do not want. A switch stops counting for as
long as it is on. The coverage log tracks the page-view switch (`VIEWS_KILL`): those
hours are marked `k`, so the report shows the days as gaps with the reason
`kill_switch` instead of as zero. The click and live switches are not sampled; a
stretch with clicks off is not marked.

| Variable | Effect |
| --- | --- |
| `VIEWS_KILL` (`1` or `true`) | the page-view route records nothing |
| `VIEWS_KILL_SITES` (comma list of site ids) | the same for the sites named |
| `CLICKS_KILL`, `CLICKS_KILL_SITES` | the same for the click route |
| `LIVE_KILL` | the live status route and poller; unlike the others it serves the all-unknown body |
| `ALERTS_CONFIG` unset or empty | no alerts at all |

Flip one with a deploy of the variable (see [deploy.md](deploy.md) for the
exact command and the token it needs), then check the next report: the hours show
as `kill_switch` and nothing else changes. Turn it back off the same way.

## Quota and budgets

The Free plan allows a limited number of D1 rows written and queries per day and
per invocation. This project never lets a budget decide silently: every limit
fails closed and is visible.

- **Hourly view budgets** (`VIEWS_PER_HOUR`, `BOT_VIEWS_PER_HOUR`, the account
  ceilings): a view over the budget is dropped. The hour is a `quota` gap.
- **Write quota**: `D1_DAILY_WRITE_QUOTA` is checked at deploy against the worst
  case; a deploy that cannot fit is refused ([deploy.md](deploy.md), kit limits).
- **Cron query budget**: `QUERY_BUDGET` (default 40) bounds one run; a long
  backlog continues on the next run. Keep it at 12 or more so coverage and
  health run.
- **Alert budget**: `DAILY_ALERT_BUDGET` per UTC day, and a per-channel minute
  limit. A refused alert is dropped and counted, never queued.

## Restore

- **Before any schema change**, export the database
  (`wrangler d1 export <database> --remote`) and keep the file somewhere you
  control. Migrations only go forward; to undo one, ship a new migration.
- **A bad deploy**: roll the Worker back from its deployments list, or redeploy
  an earlier good commit ([deploy.md](deploy.md), "Rolling back").
- **Lost or damaged data**: restore from your export into a new database, bind
  it, and redeploy. Cloudflare may also offer point-in-time recovery for D1;
  read its current documentation before relying on it (this runbook has not
  verified its limits).
- **After a restore**, the hours between the backup and the restore have no
  coverage rows. They appear as `outage` gaps in the report. That is correct:
  nothing is guessed to fill them.
- **Do not backfill zeros** into any table to make a chart look continuous.
  A zero is a claim that nothing happened; a gap says that nothing is known.

## What an owner should check weekly

Run `reach-around report` with a recent export that includes `analytics_coverage`
and `--until` set to yesterday (UTC). If it prints "Collection gaps", read the
reasons. If it prints nothing about gaps, the window was collected in full.
