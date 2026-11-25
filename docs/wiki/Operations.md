<!-- SPDX-License-Identifier: Apache-2.0 -->
# Operations

The entry page for the person who runs a Reach Around deployment. It says what
to do each week and month, what to watch, what each alert means, how backups and
the restore drill fit in, which kill switches exist, where the cost watch lives
and how to roll back. It summarizes and links the detailed runbooks in `docs/`;
it does not replace them. Follow the links before you run a command on a live
system.

Marks carry over from the source pages. **Verified** means the source page names
what was checked and when. **Inferred** or **unverified** means reasoned, not yet
run against a live system; check it on a scratch or test deployment first.
**Planned** means the part is not built.

Conventions that hold everywhere on this page:

- Times are UTC. Names in commands are placeholders (`<DATABASE>`, `<BACKUPS>`,
  `<EXPORT>`); keep your real names, ids, bookmarks and logs in a private place,
  never in a public repository, issue or chat.
- **A missing stretch is a gap, never a zero.** If nothing was collected, the
  report says so with a reason. Do not backfill zeros to make a chart look
  continuous ([failure-modes.md](../failure-modes.md)).
- Plan the routine well under your free time. If you miss a week, do the weekly
  list the next time you sit down; do not catch up on each missed week
  ([operations.md](../operations.md)).

## The routine at a glance

| When | Time | What |
|---|---|---|
| Daily | about a minute, no commands | Glance at your alert channel. Alerts are the daily check. |
| Weekly | about 20 minutes | Read the report, take the backup, verify the history file, type the manual numbers, run the smoke check, check token dates. |
| Monthly | about an hour | Restore drill on a scratch database, prune old backups, review tokens, close the month in the ledger, test the kill switches on the test deployment, read the changelog before updating. |
| Every 30 days | per the kit runbook | Refresh the media kit: hand-kept numbers, collectors, rebuild, stale gate, deploy, both PDFs, lead count and purge time. |

Full steps and commands: [operations.md](../operations.md). The kit refresh is
its own list in the same page ("Every 30 days: refresh the media kit").

### Weekly, in short

1. **Read the report** from a fresh export that includes the
   `analytics_coverage` table, with `--until` set to yesterday (UTC). If it
   prints "Collection gaps", read the reasons. A cell under the privacy floor
   prints as withheld; that is normal. See [report.md](../report.md).
2. **Take the backup**: bookmark, dry run, then `--apply`; copy the folder to the
   offline copy and check the sums there
   ([backup-restore.md](../backup-restore.md)).
3. **Verify the history file** with `reach-around counts history-verify`, then
   commit and push your private instance repository.
4. **Type the manual numbers.** `reach-around counts stale` exits 0 when nothing
   is due and 4 when something is; it never prints a value
   ([social-history.md](../social-history.md)).
5. **Check the site answers** with `scripts/smoke.sh` pointed at your site. It is
   read-only and exits 0 when every check passes, 1 when any fails.
6. **Check token dates** and renew anything inside two weeks of expiry
   ([token-health.md](../token-health.md)).

### Monthly, in short

Restore drill, prune backups, review tokens, close the month in the ledger with
`--period month` (the report refuses an incomplete month and says why), test the
kill switches on the test deployment only (never on production just to test),
and read the changelog for anything marked as a migration or a deploy order
before updating the Worker or the tool (take a bookmark and a backup first).

## What to watch

| Signal | Where | What a healthy state looks like |
|---|---|---|
| Owner alerts | Discord or Matrix channel you configured | Quiet. A silent channel is not proof of health: if alerts stop and nothing else changed, check the webhook or token first. |
| Collection coverage | `analytics_coverage` in the report export | No "Collection gaps" section. An exported table with no rows means "not monitored", never "complete". |
| Report sanity | `reach-around report` | No zero-view day, no sudden drop, no capacity warning, no figure far from last week. |
| Stale numbers | `reach-around counts stale` | Exit 0. Exit 4 means a hand-kept number is due. |
| Token health | `token-health:` lines in the daily refresh, the table in the workflow step summary, `reach-around counts tokens` | Every token `ok`. |
| Site answers | `scripts/smoke.sh` | Exit 0. |
| Lead purge (kit) | the 30-day kit refresh, last step | The last purge time is not older than the expected hourly run ([privacy.md](../privacy.md#how-long-it-is-kept)). |
| Cost and quota | [Costs](Costs.md) | See "Quota and cost watch" below. |

Two limits to know up front:

- The health checks run inside the same cron they watch. If the cron or the
  Worker does not run at all, nothing can alert. Add an external uptime probe
  that requests the public link page (not the event routes)
  ([failure-modes.md](../failure-modes.md), [alerts.md](../alerts.md)).
- Stale social numbers and token problems are reported by the CLI
  (`counts stale`, `counts tokens`), not by the Worker alerts.

## What each alert means

Alerts are off until you turn them on in the `alerts` block of `site.yaml`, and
nothing is sent to a channel you did not set up. Channels: Discord and Matrix are
built; **email is planned** (the provider interface and a disabled no-op ship, no
adapter does). The `analytics_health` event sends one message when a condition
starts and one when it clears, not every hour. Alert text is content-free about
visitors. Setup, bindings and limits: [alerts.md](../alerts.md).

The runbook for each condition is the "By alert or symptom" table in
[failure-modes.md](../failure-modes.md). In short:

| Alert | Plain meaning |
|---|---|
| `rollup_overdue` | The hourly rollup has not reported in for a while: it keeps failing, the cron stopped, or D1 is down. |
| `rollup_stale` | A day failed to roll up and later runs stop at it. |
| `zero_views` | A site that had views now has none and is not switched off. Often the beacon script was removed or blocked, or the route is wrong. Per site, but the alert names none. |
| `view_budget_hit` | The account-wide hourly view budget was reached; further views were dropped (a `quota` gap). |
| `alert_budget_near` | Today's alert budget is mostly spent: fix what is alerting. |
| `click_ceiling_reached` | The day's click write ceiling is spent; counting resumes at midnight UTC. |
| `click_setup_invalid` | No clicks are counted: the hashing key or a click setting is missing or unusable. |
| `lead_cap_reached` | New media kit visitors still unlock, but their addresses are not stored until old leads age out. Raised at most once per UTC day. |
| `gate_busy` | The optional gate watch limiter saw the kit gate far busier than usual in one location. A per-location hint, not an account-wide count. Nobody is refused because of it. |

Kit event alerts (`kit_unlocked`, `lead_captured`, `gate_failed_repeatedly`,
`inquiry_received`) are notifications, not faults. By default the visitor address
is in the alert (`visitor_email: full`); choose `masked` or `none` if your chat
room is shared.

A known, bounded limit: a crash or failed write between delivery and the record
can repeat one health alert once per hour until it clears or the daily alert
budget runs out.

## Backups and the restore drill

What there is to lose and how each item is protected
([backup-restore.md](../backup-restore.md)):

| Item | Protected by |
|---|---|
| Analytics aggregates (D1) | An export to a private folder, plus Time Travel bookmarks. |
| Social-stats history, release ledger, instance config | Git history in your private instance repository, plus the offline copy. |
| Secrets and tokens | Not backed up: rotate instead of restoring. |
| The site | No backup needed: it is rebuilt from the repository and the config. |

Points to keep in mind:

- `scripts/backup-d1.sh` is a dry run by default; `--apply` writes. It never
  exports `leads`, `kit_events`, `visitor_seen` or `view_events`, then checks its
  own output with `scripts/check-dump.py` and deletes the run if anything looks
  wrong. It prints table names and counts only.
- An export is private data: never in a git repository, a workflow artifact, a
  chat, an issue or a pull request. The script refuses (exit 3) an unsafe folder
  (inside a git work tree, a symbolic link, open to others, with an ACL). Fix the
  folder, never the check.
- The export is **not a single snapshot**: it is one call per table while the
  Worker keeps counting. Fine for a weekly copy. When you need one consistent
  copy (before a risky write or a restore), pause both write routes for the run.
- Time Travel bookmarks are valid for 30 days on Workers Paid and 7 days on Free
  (verified in the source page, Cloudflare Time Travel page read 2026-11-09). A
  newer bookmark does not mean your data changed.
- Unverified: whether D1 Read permission alone is enough for exports. Prove the
  required permission before choosing a backup credential. Use a short-lived
  token for the parts that need D1 edit, and drop it afterward.

Suggested retention (one owner's recommendation, not a rule of the software):
latest 8 weekly exports, then one a month for 12 months; drill records for 12
months. Choose your own and write them down.

### The restore drill

Run it monthly on a scratch target, never on the live database.

- **Part A, offline.** Load the export into a throwaway SQLite file and compare
  counts. Verified by the test suite with synthetic export files. Inferred: that
  a real `wrangler d1 export` file loads the same way.
- **Part B, scratch D1 database.** Create, import, compare counts, run a report
  from the scratch data, then delete the scratch database. **Inferred: run it
  once and record the result.** Also inferred: that files import in any order
  (no table has a foreign key to another).
- Record the result in your private log with no ids. A backup that has not been
  restored lately is a guess.

### Restore as a last resort

Time Travel restore is destructive: it overwrites the whole database in place
and loses everything written after the chosen moment, in every table. Use it only
when the live data is wrong and a fix-forward is not possible. Order: pause
writes, bookmark and export the damaged state, restore, read-only checks, turn
writes back on. The restore prints the previous bookmark; write it down at once,
it is your undo. Restoring from an export into a new database is **inferred**
until you have run Part B for your setup; do not import into a database that
already holds rows. Full steps: [backup-restore.md](../backup-restore.md).

After any restore, the hours between the backup and the restore show as
`outage` gaps. That is correct.

## Kill switches

Each switch is a plain Worker variable read on every request. It turns a route
into its usual empty 204 that touches neither D1 nor the rate limiter, and takes
effect when the deploy lands. They are for an incident, a flood or a bill you do
not want. Leave all of them unset in the committed `wrangler.toml`. Turn one back
off with `:0`.

| Variable | Effect |
|---|---|
| `VIEWS_KILL`, `VIEWS_KILL_SITES` | Page-view route records nothing (all sites, or the sites named). Sampled: those hours show as `kill_switch` (or `site_kill_switch`) gaps. |
| `CLICKS_KILL`, `CLICKS_KILL_SITES` | The same for the click route. **Not sampled**: a stretch with clicks off is not marked in coverage. |
| `LIVE_KILL` | The live status route and poller; serves the all-unknown body. |
| `SCHEDULED_KILL` | Maintenance only, for D1 migrations. Every scheduled run returns at once, so health alerts, notifications and the live status pause, and the window is a `-` outage gap. Not for an analytics rollback. See [cutover-runbook.md](../cutover-runbook.md). |
| `ALERTS_CONFIG` unset or empty | No alerts at all. |

Note the difference in what the report shows: a click pause is invisible in the
coverage log, so write the UTC start and end of every pause in your private log.
Before using a switch, note its start time and scope; afterward, remove it,
verify one complete collection interval and record the end time. The stopped
interval stays a gap and is never rewritten as normal operation
([collection-gaps-runbook.md](../collection-gaps-runbook.md)).

## Quota and cost watch

The cost and quota story lives on its own page: [Costs](Costs.md). That page is
in draft PR #821 and may not be on the default branch yet, so this link works
only once that PR merges.

What matters for operators:

- Limits fail closed and are visible. A view over the hourly budget is dropped
  and the hour is a `quota` gap; a refused alert is dropped and counted, never
  queued. A later successful run does not recover dropped events
  ([failure-modes.md](../failure-modes.md), "Quota and budgets").
- Raise a limit only after confirming its worst case is affordable. The D1 write
  quota is checked at deploy against the worst case; a deploy that cannot fit is
  refused ([deploy.md](../deploy.md), "Kit limits and the daily write budget").
- Treat `view_budget_hit`, `click_ceiling_reached` and `alert_budget_near` as the
  early warnings to open the Costs page and re-check your numbers.
- Some figures in the source pages were written for the Free plan; check the
  Costs page for the current baseline before you change a budget.

## Rollback

Order of preference for analytics, least damage first
([operations.md](../operations.md), "Roll back analytics"):

1. **Pause collection** with the kill switches above. The link page keeps working
   because links carry their real address.
2. **Roll back the Worker or the site** to the previous deployment: the Pages
   deployments list (or re-run the workflow from an earlier good commit on
   `main`), and the Worker deployments list or `wrangler rollback`
   ([deploy.md](../deploy.md), "Rolling back").
3. **Fix forward** with a new migration. Migrations only go forward.
4. **Restore the database** from a Time Travel bookmark or an export, only as the
   last resort.

Other rollbacks, from the same deploy section: remove the custom domain or the
`CNAME` to stop the hostname resolving (nothing else is affected); roll or delete
a leaked token in Cloudflare and update the secret; disable the workflow to stop
all deploys. Before any schema change, take a bookmark and a full backup, count
the rows you are about to touch, and compare after.

## Troubleshooting

Built only from the existing runbooks. Each row links to its source.

| Symptom | Likely cause | First check |
|---|---|---|
| `rollup_overdue` alert | Rollup failing, cron stopped, or D1 down | Worker cron runs and logs; Cloudflare status. Raw rows are kept until the purge can safely delete them, so a late rollup loses nothing inside the raw keep window ([failure-modes.md](../failure-modes.md)). |
| `rollup_stale` alert | A day failed to roll and later runs stop at it | The `rollup_failed_day:<site>` key in `view_meta` names the first failed day. Past the raw keep window that day stays a gap. |
| `zero_views` alert | Beacon script removed or blocked, mis-routed route, a kill switch, or a real outage | Open the public page and watch the network tab for the beacon; check `VIEWS_KILL`, `VIEWS_KILL_SITES`, the route pattern and that `VIEW_HASH_SECRET` is set. |
| No clicks counted at all | `click_setup_invalid`: hashing key or click setting missing or unusable | Set the secret (a click-only Worker needs it too) or fix the value ([alerts.md](../alerts.md)). |
| Click counts stop partway through a day | `click_ceiling_reached`: the day's write ceiling is spent | Counting resumes at midnight UTC; see "Click write ceiling" in [deploy.md](../deploy.md). |
| Kit visitors unlock but addresses are not stored | `lead_cap_reached` | Check the cap settings and the 30-day default aging; see the gate routes in [kit.md](../kit.md). |
| A day shows as `outage` in the report | The Worker did not run that hour, or `SCHEDULED_KILL` was on, or a restore skipped hours | Cloudflare status and the Worker's deployments. Nothing to rebuild: it stays a gap. |
| A day shows as `kill_switch` or `site_kill_switch` | A view kill switch was on when the hour was sampled | Expected after you flipped it; coverage is account-wide, so check the list you set. |
| No alerts arrive and nothing else changed | Webhook or token problem; or the cron itself stopped | Check the webhook or token first; confirm an external probe on the link page ([alerts.md](../alerts.md)). |
| Backup script exits 3 | Unsafe folder (in a git work tree, open to others, a link, or has an ACL) | Fix the folder, never the check ([backup-restore.md](../backup-restore.md)). |
| Backup script exits 1 | A wrangler call failed or timed out, or the dump check found something | Read the one-line reason and rerun once. |
| Dump check names a column | A new column reached the database | Update the allowlist and its test in the project; do not skip the check. |
| Report refuses a period | The export is incomplete or the ledger does not match | The message says which ([operations.md](../operations.md)). |
| `history-verify` fails | History file damaged | Restore the last commit that verifies ([social-history.md](../social-history.md#restore-from-git-history)). |
| A token is rejected or `token-health:` shows `expiring` | Expired, refused or about to expire | Renew it; never paste it into a chat or an issue ([token-health.md](../token-health.md)). |
| `counts tokens` exits 0 but you doubt it | A missing or damaged status file is not healthy | With no status file only the dates count; check the file exists and is fresh ([token-health.md](../token-health.md)). |
| YouTube source `rejected` (`http_400`) | Unverified: may be a malformed request rather than a bad key | Look at the key and confirm in the console ([token-health.md](../token-health.md)). |

## What is private

Never in a public repository, issue, pull request, chat or artifact: every
export file, bookmark, database or account id, token, the lead list, the offline
copy's location, your drill log, your instance config and the ledger. Public: the
documentation, the code, and the floored, labeled aggregates the report prints.

## Not covered here

- An incident and breach procedure is **planned**; this page does not carry one.
- Wiring token-health or analytics alerts to Pushover, Discord or Matrix from your
  own workflow is yours to build later; the engine only prints
  ([token-health.md](../token-health.md)).
- First-time setup, DNS and secrets: [deploy.md](../deploy.md) and
  [instance-repos.md](../instance-repos.md).
