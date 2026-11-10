<!-- SPDX-License-Identifier: Apache-2.0 -->
# Operating analytics: the owner's routine

A short, general runbook for the person who runs a Reach Around deployment.
It holds no deployment values: use your own names in place of `<DATABASE>`,
`<BACKUPS>` and the like, and keep your own notes (dates, results, bookmarks)
in a private place, never in this repository. The detailed steps for backups
and restores are in [backup-restore.md](backup-restore.md); the cutover and
deploy steps are in [deploy.md](deploy.md); the report is in
[report.md](report.md).

Plan the routine at well under your free time. If a week is missed, do the
weekly list the next time you sit down; do not try to catch up on each missed
week.

## Every day (a minute, no commands)

- Glance at your alert channel ([alerts.md](alerts.md)). Alerts are the daily
  check; there is nothing else to open. What each alert means and what to do
  is in [failure-modes.md](failure-modes.md); this page does not repeat it.
- If something looks wrong, turn collection off first (see "Roll back
  analytics" below), then look.

## Every week (about 20 minutes)

1. **Read the report.** Make the export as described in
   [report.md](report.md) ("Making the export") into a private folder, then:

   ```bash
   reach-around report --input <EXPORT> --days 7
   ```

   For the source and country tables, one complete period at a time, always
   with the release ledger (kept in your private instance repository):

   ```bash
   reach-around report --input <EXPORT> --period week --ledger <PRIVATE-REPO>/funnel-ledger.jsonl
   ```

   Include the `analytics_coverage` table in the export and set `--until` to
   yesterday (UTC): if the report prints "Collection gaps", read the reasons
   ([failure-modes.md](failure-modes.md)). A gap is never a zero.

   Look for: a day with zero views, a sudden drop, a capacity warning, and a
   figure that is far from last week. A cell under the privacy floor prints as
   withheld; that is normal.
2. **Take the backup.** The three commands in
   [backup-restore.md](backup-restore.md) ("Take a backup"): the bookmark, the
   dry run, then `--apply`. Copy the new folder to the offline copy and check
   the sums there.
3. **Check the history file.**

   ```bash
   reach-around counts history-verify --config site.yaml
   ```

   Commit and push the private instance repository (config, numbers, history,
   ledger).
4. **Type the manual numbers.** List what is overdue, then add each value:

   ```bash
   reach-around counts stale --config site.yaml
   reach-around counts history-add --config site.yaml --platform <PLATFORM> \
     --value <NUMBER> --as-of <YYYY-MM-DD> --note "read from the dashboard"
   ```

   `counts stale` exits 0 when nothing is due and 4 when something is; it
   never prints a value. [social-history.md](social-history.md) has more
   examples (corrections, definition changes). A number older than its stale
   limit gives a build warning.
5. **Check the site answers.** `bash scripts/smoke.sh https://<YOUR-SITE>`
   (read-only; point it at the site you mean to check).
6. **Token dates.** Look at the expiry dates you recorded for the Cloudflare
   token, the Doppler service token and each platform token. Renew anything
   inside two weeks of expiry. Platform token steps are in
   [instagram-threads-sources.md](instagram-threads-sources.md),
   [twitch-source.md](twitch-source.md) and [tiktok-source.md](tiktok-source.md);
   a `token-health:` line in the daily refresh output is the early warning.

## Every month (about an hour)

1. **Restore drill** on a scratch database
   ([backup-restore.md](backup-restore.md), "The restore drill"). Record the
   result in your private log. A backup that has not been restored lately is a
   guess.
2. **Prune old backups** by the retention below.
3. **Review the tokens**: rotate any that are older than your own policy, and
   confirm no export credential is sitting in a repository or a workflow
   secret.
4. **Close the month in the ledger.** Run the monthly report with `--period
   month` and the ledger once the month has rolled up (the Worker logs the
   rollup; the report refuses an incomplete month and says why).
5. **Check the kill switches still work** on the test deployment: deploy with
   `CLICKS_KILL:1`, confirm the click route answers an empty 204, then turn it
   back. Do not do this on production just to test.
6. **Update the Worker and the tool**: read the changelog for anything marked
   as a migration or a deploy order, and take a bookmark and a backup first.

## What to export, where it lives, how long to keep it

| What | Taken | Lives in | Keep |
|---|---|---|---|
| D1 export (`scripts/backup-d1.sh`) | weekly, and before any risky write | `<BACKUPS>` (private disk) and an offline copy | latest 8 weekly, then 1 per month for 12 months |
| Time Travel bookmark | at the same time as the export | your private log | until the export is verified; usable for 30 days (Paid) or 7 days (Free) |
| Report exports for the report | weekly | a private folder | 90 days, they are rebuilt from the database |
| Instance config, numbers file, history, ledger | every change | private instance repository | history stays in git; never rewrite it |
| Drill records | monthly | your private log | 12 months |

The retention numbers are a recommendation for one owner, not a rule of the
software: choose your own and write them down. Delete an expired export with a
plain `rm -r` of its dated folder, after checking the path.

## What is private

Private, never in a public repository, issue, pull request, chat or artifact:
every export file, every bookmark, database and account ids, tokens, the lead
list, the offline copy's location, your drill log, your instance config and the
ledger. Public: this documentation, the code, and floored, labeled aggregates
that the report prints. The rule for exports is in
[backup-restore.md](backup-restore.md) ("The privacy rule").

## Who is alerted

Owner alerts go to the channels you enabled in the `alerts` config (Discord
or Matrix; email is not built). Keep one of them on a device you check every
day. An alert is content-free about visitors by design. If alerts stop arriving
and nothing else changed, check the webhook or token first; a silent channel is
not proof that everything is fine.

## Roll back analytics

Order of preference, least damage first:

1. **Pause collection.** Each switch is read on every request and takes effect
   when the deploy lands ([deploy.md](deploy.md)). The link page keeps working
   because links carry their real address.

   ```bash
   npx wrangler deploy --var CLICKS_KILL:1
   npx wrangler deploy --var VIEWS_KILL:1
   ```

   Turn them back on with `:0`. `LIVE_KILL` does the same for the Live poller,
   and `VIEWS_KILL_SITES` / `CLICKS_KILL_SITES` pause named sites only (full
   table in [failure-modes.md](failure-modes.md#kill-switches)). Hours with the
   page-view switch on show as `kill_switch` gaps in the report.
   Leave all of them unset in the committed `wrangler.toml`.
2. **Roll back the Worker or the site** to the previous deployment (see
   "Rolling back" in [deploy.md](deploy.md)).
3. **Fix forward** with a new migration. Migrations never go backward.
4. **Restore the database** from a Time Travel bookmark or an export, only as
   the last resort, because it overwrites the whole database in place
   ([backup-restore.md](backup-restore.md), "Restore, last resort").

## When something fails

| Symptom | First look |
|---|---|
| The backup script exits 3 | the folder is unsafe (inside a repository, open to others, or a link); fix the folder, never the check |
| The backup script exits 1 | a wrangler call failed or timed out, or the dump check found something; read its one-line reason, and rerun once |
| The dump check names a column | a new column reached the database; update the allowlist and its test in the project, do not skip the check |
| The report refuses a period | the export is incomplete or the ledger does not match; the message says which |
| `history-verify` fails | restore the last commit that verifies ([social-history.md](social-history.md#restore-from-git-history)) |
| A token is rejected | renew it; never paste it into a chat or an issue |
