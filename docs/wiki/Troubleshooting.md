<!-- SPDX-License-Identifier: Apache-2.0 -->
# Troubleshooting

Each row gives the symptom, the usual cause and the first thing to check. The detail lives in the repository docs linked from each section. Routine upkeep and the weekly and monthly checks are in [Operations.md](Operations.md); what each service costs is in [Costs.md](Costs.md). Both pages arrive in separate pull requests (#828 and #821), so those two links resolve once those merge.

One rule runs through all of it: a missing stretch of data is a gap, never a zero (`docs/failure-modes.md`). If a report shows a gap with a reason, believe the reason before you suspect the report.

Never paste a token, a webhook URL or a visitor email address into an issue or a chat while you debug.

## The build and config

| Symptom | Likely cause | First check |
|---|---|---|
| `reach-around check` prints findings | A config value fails the strict schema; unknown keys are refused | Read each finding, which names the block and the field. Fix the config, then run `check` again (`docs/config.md`). |
| A warning but exit status 0 | The check found something worth a look that is not an error, such as a number older than its stale limit | Read the warning. `check --production` is the stricter gate and exits 2 on findings (`docs/config.md`). |
| The build fails right after a config edit | The edit is invalid in one block | Undo the last edit, or use the editor's undo, then rerun `check`. |
| Built links differ from the old page | A link was dropped, renamed or never imported | Run `reach-around parity --site OUT --expected old-links.csv`. Exit 1 means differences, exit 2 means the inputs could not be read (`docs/parity.md`). |

## Deploy

The full table is in [deploy.md](../deploy.md#troubleshooting). The usual ones:

| Symptom | Likely cause | First check |
|---|---|---|
| A file looks old after a deploy | A removed path is still cached at the edge, or the zone's browser cache TTL holds a stable file | Purge the URL, or set the zone's Browser Cache TTL to respect existing headers (`docs/deploy.md`, "Caching and stale files"). |
| The custom domain serves old or different content | The domain is attached to a Worker rather than the Pages project, or the deploy went to a preview because the production branch is not `main` | Check that the newest deployment is tagged Production (`docs/deploy.md`, "Production branch"). |
| Route rejected with "No access to the specified resource" | The token lacks Workers Routes edit or zone read, or it is an account token, which cannot carry zone permissions | Use a user token with the zone permissions, or leave the zone name unset and add the route by hand (`docs/deploy.md`, "Token type"). |
| `d1 migrations apply` says nothing to apply | Every migration is already recorded | Normal on a repeat deploy. Check with `wrangler d1 migrations list`. |
| A deploy is refused because it cannot fit the write quota | The configured budgets exceed the daily D1 write allowance in the worst case | Lower a budget or change plan, and see "Quota and budgets" in [failure-modes.md](../failure-modes.md). |
| A GitHub runner stalls | A hosted runner hiccup; a stuck run also blocks the next in the same concurrency group | Cancel the run and rerun it (`docs/deploy.md`). |
| `check-config.sh` in the instance repo exits 3 | The engine image could not be pulled or run | Check that the pinned engine tag exists and the runner can pull it (`docs/instance-repos.md`; the pull step is #654). |

## Analytics and the report

These map to the health alerts, once `analytics_health` is on in `alerts.events` ([alerts.md](../alerts.md)). Full table: [failure-modes.md](../failure-modes.md).

| Symptom or alert | Likely cause | First check |
|---|---|---|
| `zero_views` | The view script was removed or blocked, the route is mispointed, a kill switch is on, or the view hashing secret is missing | Open the public page and watch for the beacon request. Check `VIEWS_KILL`, `VIEWS_KILL_SITES`, the route pattern and that `VIEW_HASH_SECRET` is set. |
| `rollup_overdue` or `rollup_stale` | The daily rollup keeps failing, the cron stopped, or D1 is down | Check the Worker's cron runs and logs. The heartbeat names a closed class: `none`, `mismatch` or `db`. A day past the raw keep window cannot be rebuilt and stays a gap. |
| `view_budget_hit` | Real traffic or a flood above the hourly view budget | Views over budget are dropped, not queued, and the hour shows as a `quota` gap. Raise a budget only inside the daily write allowance. |
| `click_ceiling_reached` | The day's click write ceiling is spent | Counting resumes at midnight UTC (#566; `CLICK_DAILY_WRITE_CEILING` in `docs/deploy.md`). |
| `click_setup_invalid` or no clicks at all | The view hashing secret is missing or unusable, or a click setting holds a value it does not accept | Set the secret (a click-only Worker needs it too) and fix the setting (`docs/deploy.md`, "Kit limits"; #610). |
| `alert_budget_near` | Many alerts today | Fix what is alerting. At zero budget no alert is sent. |
| A day shows as `outage` | The Worker did not run that hour | Check the Cloudflare status page and the Worker's deployments. Nothing to rebuild. |
| A day shows as `kill_switch` or `site_kill_switch` | A kill switch was on when the hour was sampled | Expected after you flipped it. For the second, check the list you set. |
| A small cell is missing from a report | The privacy floor hides cells too small to publish | Expected. See [report.md](../report.md) and the floors in #397. |
| Report times look a day off | Storage and reports use UTC; a display zone is optional | Pass `--display-zone` (#372). |
| A restore does not look right | A backup holds some data and not others | Follow the drill in [backup-restore.md](../backup-restore.md). |

## Counts, tokens and history

| Symptom | Likely cause | First check |
|---|---|---|
| A platform number stopped updating | The token expired or was rejected, or the platform changed its API | Run `reach-around counts tokens`. States are `ok`, `expiring`, `expired` and `rejected`; exit 4 means expiring and 3 means expired or rejected (#390, `docs/token-health.md`). |
| An Instagram or Threads count stops | The long-lived token needs a manual refresh | Read the `token-health` line for days left, then refresh by hand (#387, `docs/instagram-threads-sources.md`). |
| A hand-typed number is flagged old | Its `as_of` day is past the stale limit | `reach-around counts stale --config site.yaml` exits 4 when something is due (`docs/operations.md`). |
| Refresh fails with exit 2 | The numbers file does not validate | Read the message, which names the row, never the value (`docs/operations.md`). |
| A history append fails | The history file is locked, unreadable or a record is malformed | Check the message and file permissions; records with a trailing newline in a platform, metric or run id are refused (#624). |
| History export fails on a hard link | The file system has no hard links (FAT or some network mounts) | Export to a local file system (#659). |
| A retention warning appears | A platform's retention window ends soon | Export or import before the window closes (#620, `numbers.retain_days` in `docs/config.md`). |

## The media kit and gate

| Symptom | Likely cause | First check |
|---|---|---|
| New visitors unlock the kit but are not stored | `lead_cap_reached`: the cap on leads or new leads per day was reached | Unlocks still work; leads age out after the retention period (`docs/kit.md`, `docs/privacy.md`). |
| `gate_busy` alert | The optional gate watch limiter was exceeded in one location | It is a hint, not an account-wide count; nobody is refused for it (`docs/failure-modes.md`). |
| The private PDF fails its trust legend check | A known open bug under a real Chromium render | See issue #749 and the build output (`docs/kit.md`). |
| A kit number shows a trust label you did not expect | Each number is labeled by how it was obtained | See the trust labels in [kit.md](../kit.md) (#626). |

## The live tab

| Symptom | Likely cause | First check |
|---|---|---|
| The live badge shows unknown | The live Worker route is off, stale or the poller is paused | Check `LIVE_KILL` and `SCHEDULED_KILL`; the page serves a safe unknown state by design (`docs/live.md`). |
| Live status goes stale after maintenance | `SCHEDULED_KILL` pauses every scheduled run, including the live poller | Turn it back off after migrations (`docs/failure-modes.md`, "Kill switches"). |

## Still stuck

Search the open issues, then open one with the symptom, the version of the engine you pin and the exit code, with no secrets. [OWNER INPUT NEEDED] Whether and how fast the maintainer answers is his to state; see the [FAQ](FAQ.md).
