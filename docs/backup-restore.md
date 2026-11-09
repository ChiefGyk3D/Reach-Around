<!-- SPDX-License-Identifier: Apache-2.0 -->
# Backups and the restore drill

This page is for the owner of one deployment. It says what to back up, where
the copies live, how to take one, how to prove it restores, and what to do in
the worst case. Every command uses placeholders: replace `<DATABASE>` with your
D1 database name, `<SCRATCH_DATABASE>` with a throwaway database name and
`<BACKUPS>` with your private folder. Never put a real name, id or bookmark in
a repository, an issue or a chat. The weekly and monthly routine that uses
these steps is in [operations.md](operations.md).

How to read the marks: **verified** means checked this way and the source is
named; **inferred** means reasoned from the docs or the code and not yet run
against a live database. Re-check an inferred step on your scratch database
before you depend on it.

## What there is to lose

| Item | Where it lives | How it is protected |
|---|---|---|
| Analytics aggregates (views, clicks, sources, countries, seals, rollup logs) | the D1 database | export to `<BACKUPS>`; Time Travel for the last 30 days |
| Social-stats history (`numbers.history_file`) | your private instance repository | git history, see [social-history.md](social-history.md#restore-from-git-history) |
| Release ledger (`funnel-ledger.jsonl` and its `.head` file) | your private instance repository | git history and the offline copy |
| Instance config (`site.yaml`, `numbers.json`, your deploy settings) | your private instance repository | git history and the offline copy |
| Secrets and tokens | your secrets manager | not backed up here; rotate instead of restoring |

The site itself needs no backup: it is rebuilt from the repository and the
config ([deploy.md](deploy.md) has the rollbacks).

## What a database backup contains, and what it leaves out

`scripts/backup-d1.sh` exports one file per table, with its schema and rows:
the click and view aggregate tables, the seal and rollup logs, `click_events`
(a source label; see the country note below), the counters, the alert quota
tables and `d1_migrations` (the list of migrations already applied, which a
rebuilt database needs so that "apply migrations" only runs newer ones).

**Country in `click_events`.** The Worker now writes no country there, but the
column still exists and rows written before migration 0010 and the new Worker
can still hold one. The export copies the table as it is, so an old backup can
contain a country for old clicks. If you do not want that in any copy, run
`scripts/clear-click-country.sh` first (its order of steps is in the script
header), then back up.

It never exports four tables, on purpose:

- `leads` (email addresses of people who asked for the kit),
- `kit_events` (carries a lead id),
- `visitor_seen` (the daily visitor hash, deleted within about an hour of its
  day ending anyway),
- `view_events` (raw page-view rows, short-lived by design).

Losing `kit_events` means the kit funnel counts before the loss are gone; that
is the price of keeping lead data out of every copy. Lead data has its own
retention, see [deploy.md](deploy.md) ("Lead retention").

After the export the script runs `scripts/check-dump.py` over everything it
wrote, against the allowlist minus those four tables. It fails, and deletes the
run, if it finds an unknown table or column, an address or a user agent. It
prints line numbers and kinds, never a value.

## The privacy rule

An export is private data. It never goes in a git repository, a workflow
artifact, a chat, an issue or a pull request, not even a private one.

- `scripts/backup-d1.sh` refuses (exit 3) a folder inside a git work tree, a
  symbolic link, a folder you do not own, a folder that group or others can
  open, and a folder with an ACL (`ls -ld` shows a `+` after the mode bits),
  because an ACL can grant access that the mode hides and new files could
  inherit it. Other limits that it cannot see (a backup agent, a synced folder,
  a network share) are yours to check.
- Files are written with mode 600 in a mode 700 directory.
- The script prints table names and counts only. It sends wrangler's own output
  to nowhere, because that output names the database id.
- Keep `<BACKUPS>` on an encrypted disk, and the offline copy on encrypted
  media. Share nothing from it.

## One-time setup

```bash
mkdir -p "$HOME/<BACKUPS>" && chmod 700 "$HOME/<BACKUPS>"
```

You need `npx wrangler` signed in with your own login, run from the directory
whose wrangler config names `<DATABASE>` (inferred: wrangler resolves a
database name through the config in the current directory). The login needs
D1 read for exports and Time Travel info. A restore, and creating or deleting
the scratch database, need D1 edit. Use a short-lived token for the edit parts,
held in your secrets manager, and drop it when the job is done. Never put an
export credential in a repository or in a workflow secret.

## Take a backup (about five minutes)

1. Save a Time Travel bookmark. It marks "now" so you can return to this exact
   moment if a later write goes wrong.

   ```bash
   npx wrangler d1 time-travel info <DATABASE>
   ```

   Expected output (verified, Cloudflare Time Travel page, read 2026-11-09):
   a line `The current bookmark is '<BOOKMARK>'` and a ready-made restore
   command. Write the bookmark and the UTC time in your private drill log (see
   below). A bookmark is only valid for 30 days on the Workers Paid plan and
   7 days on Free (verified, same page). A newer current bookmark does not mean
   your data changed: bookmarks also move on Cloudflare maintenance (verified,
   same page).

2. See what the script would do. This is the default and makes no network call.

   ```bash
   scripts/backup-d1.sh <DATABASE> "$HOME/<BACKUPS>"
   ```

   Expected: `dry run: nothing is written and no network call is made.`, the
   folder it would create and the list of tables, then
   `never exported: leads, kit_events, visitor_seen, view_events`.

3. Take the export.

   ```bash
   scripts/backup-d1.sh <DATABASE> "$HOME/<BACKUPS>" --apply
   ```

   **This is not a single snapshot.** The script makes one export call per
   table, and the Worker keeps counting between calls, so the tables can differ
   by a few minutes of traffic. For a weekly copy that is fine: the counters
   only grow, and a restored copy is at worst slightly behind in some tables.
   When you need one consistent copy (before a risky write, or before a
   restore), pause both write routes for the whole run, then turn them back on:

   ```bash
   npx wrangler deploy --var CLICKS_KILL:1 --var VIEWS_KILL:1
   scripts/backup-d1.sh <DATABASE> "$HOME/<BACKUPS>" --apply
   npx wrangler deploy --var CLICKS_KILL:0 --var VIEWS_KILL:0
   ```

   Visits during the pause are not counted. Inferred: the Time Travel bookmark
   from step 1 is the other point-in-time anchor, because Time Travel restores
   the whole database at one moment (verified, Time Travel page).

   Expected: `done: N tables written to <BACKUPS>/d1-<UTC timestamp>`. Tables
   an older database does not have are listed as `skipped (not in this
   database)`. Each wrangler call is stopped after 120 seconds
   (`BACKUP_TIMEOUT=300` raises it, to at most 900). A running export blocks
   other requests to the database while it runs (verified, Cloudflare import
   and export page, last updated 2026-04-21, read 2026-11-09), so do not run it
   during a busy hour. Export does not support databases with virtual tables
   (same page); Reach Around creates none.

   The manual form for one table, if you ever need it (verified, same page):

   ```bash
   npx wrangler d1 export <DATABASE> --remote --table=<TABLE> --output=<FILE>.sql
   ```

4. Check the files (verified by the script's tests).

   ```bash
   ls -l "$HOME/<BACKUPS>"/d1-*/ | head -30
   (cd "$HOME/<BACKUPS>/d1-<UTC timestamp>" && sha256sum -c SHA256SUMS)
   ```

   Expected: every file `-rw-------`, every line of `sha256sum -c` ends in
   `OK`, and no `leads.sql`, `kit_events.sql`, `visitor_seen.sql` or
   `view_events.sql` in the folder. The first listing of a sample export is how
   you verify the exclusions.

5. Compare one count with the live database (read-only).

   ```bash
   npx wrangler d1 execute <DATABASE> --remote --json \
     --command "SELECT count(*) AS n FROM view_daily"
   ```

   The count should be the same as, or a little above, the number of
   `INSERT` lines in `view_daily.sql` (the live database keeps counting while
   you work): `grep -c '^INSERT' "$HOME/<BACKUPS>/d1-<UTC timestamp>/view_daily.sql"`.
   Inferred: the exact `INSERT` line shape of the export. If your count of
   lines differs, compare by `SELECT` instead.

6. Copy the folder to the offline copy, then run the `sha256sum -c` check
   there too.

7. Back up the files that are not in the database. They live in your private
   instance repository, so commit and push them there:
   `site.yaml`, `numbers.json`, the history file, `funnel-ledger.jsonl` and its
   `.head` file. Then check the history file is whole:

   ```bash
   reach-around counts history-verify --config site.yaml
   ```

   Expected: it reports the file verified; a broken hash chain is named. Never
   force-push the history ([social-history.md](social-history.md)).

## Before a risky write

A risky write is a migration, the click cleanup script, a deploy that changes
the Worker's database use, or anything that edits rows by hand.

1. Take the bookmark (step 1 above) and a full backup (steps 2 to 4).
2. Read-only check before: count the rows of the tables you are about to touch
   and write the numbers down.

   ```bash
   npx wrangler d1 execute <DATABASE> --remote --json \
     --command "SELECT count(*) AS n FROM <TABLE>"
   ```

3. Make the write.
4. Read-only check after: the same counts. Compare with the numbers you wrote.
5. If they are as expected, you are done; keep the backup for the retention
   period. If not, go to "Restore, last resort".

Migrations are forward only ([deploy.md](deploy.md), "Rolling back"). Ship a
new migration to undo one; restore is the backstop.

## The restore drill

Do it once now and then monthly (see [operations.md](operations.md)). It proves
that the files you hold can rebuild a working database. Never practice on the
live database.

### Part A: offline, no Cloudflare

This loads the export into a throwaway SQLite file on your own machine and
compares counts. It needs only `sqlite3` (version 3.37 or newer, because the
tables are `STRICT`). Verified: the test suite does the same load with
synthetic export files made by a stand-in for wrangler. Inferred: that a real
`wrangler d1 export` file loads the same way.

```bash
cd "$HOME/<BACKUPS>/d1-<UTC timestamp>"
rm -f /dev/shm/restore-drill.sqlite
bash -euo pipefail -c 'for f in *.sql; do sqlite3 /dev/shm/restore-drill.sqlite < "$f" || { echo "FAILED: $f"; exit 1; }; done'
sqlite3 /dev/shm/restore-drill.sqlite "SELECT count(*) FROM view_daily"
rm -f /dev/shm/restore-drill.sqlite
```

Expected: no `FAILED` line (the loop stops at the first failure, and if it
prints one, stop here and do not record the drill as passed), and the count matches the table's `INSERT` lines.
(`/dev/shm` keeps the scratch copy in memory so it never lands on disk; the
last line removes it.)

### Part B: a scratch D1 database (inferred; run it once and record the result)

1. Create an empty scratch database. This needs D1 edit (a short-lived token).

   ```bash
   npx wrangler d1 create <SCRATCH_DATABASE>
   ```

   Expected: a confirmation that prints the new database id. Do not copy that
   id anywhere public. Add it to your own private wrangler config if wrangler
   asks for it.

2. Import each file (verified command form, Cloudflare import and export page,
   read 2026-11-09; it is limited to 5 GiB per file).

   ```bash
   cd "$HOME/<BACKUPS>/d1-<UTC timestamp>"
   bash -euo pipefail -c 'for f in *.sql; do npx wrangler d1 execute <SCRATCH_DATABASE> --remote --file="$f" || { echo "FAILED: $f"; exit 1; }; done'
   ```

   Expected: no `FAILED` line (the loop stops at the first failure; if it
   fails, do not record the drill as passed). Inferred: the files import in any order because
   no table has a foreign key to another; if one fails on a constraint, import
   it again after the others.

3. Compare read-only counts with the live database, table by table.

   ```bash
   npx wrangler d1 execute <SCRATCH_DATABASE> --remote --json \
     --command "SELECT count(*) AS n FROM view_daily"
   ```

4. Run the project's own check over a report from the scratch data, so you know
   the restored tables are usable and not only present: take the report export
   from the scratch database as in [report.md](report.md) ("Making the
   export"), and run `reach-around report --input <EXPORT> --days 7`.

5. Delete the scratch database.

   ```bash
   npx wrangler d1 delete <SCRATCH_DATABASE>
   ```

   Expected: it asks you to confirm. Check the name twice before you answer:
   this deletes whatever database carries that name. A deleted database can be
   undeleted for 7 days (verified, Cloudflare changelog, 2026-10-23, read
   2026-11-09).

6. Record the drill in your private log, with no ids: the date, which export
   folder you used, Part A and Part B outcome, the tables compared and whether
   the counts matched, how long it took, and anything that surprised you. The
   first record is the acceptance record for the drill.

## Restore, last resort

Use this only when the live data is wrong and a fix-forward is not possible.
**Time Travel restore is destructive: it overwrites the whole database in
place** (verified, Cloudflare Time Travel page, read 2026-11-09). Everything
written after the chosen moment is lost, in every table, not only the one that
went wrong. Queries in flight are cancelled.

1. Stop new writes first so nothing is lost mid-restore. Deploy the Worker with
   its kill switches on (from [deploy.md](deploy.md); each takes effect when the
   deploy lands):

   ```bash
   npx wrangler deploy --var CLICKS_KILL:1 --var VIEWS_KILL:1
   ```

2. Take a bookmark and an export of the damaged state, so even the bad state can
   be examined later (steps 1 to 3 above).

3. Restore to the bookmark you saved before the risky write (verified command
   form, same page; wrangler asks you to confirm, `y/N`):

   ```bash
   npx wrangler d1 time-travel restore <DATABASE> --bookmark=<BOOKMARK>
   ```

   Or by time, as Unix seconds in UTC or an ISO 8601 string:
   `--timestamp=<UNIX_SECONDS>`. Expected: the output ends with the previous
   bookmark. **Write it down at once.** It is your undo.

4. Read-only checks (the counts you wrote down before the write).

5. Turn writes back on by deploying with `--var CLICKS_KILL:0 --var VIEWS_KILL:0`,
   then watch the next report.

6. Undo, if the restore was a mistake: run the same restore command with the
   previous bookmark from step 3 (verified, same page).

If the bookmark is older than the retention window, or the database is gone,
use the export instead. Inferred (run Part B first to learn the exact steps
for your setup): create a new empty database, import the files as in Part B,
put the new database's id into your deploy settings (`D1_DATABASE_ID`,
[deploy.md](deploy.md)), apply any migrations newer than the export, deploy the
Worker, and check the counts. The export includes `d1_migrations`, so the
import restores the record of what has been applied. Verified (Cloudflare
D1 Migrations page, read 2026-11-09): wrangler keeps that record in the
`d1_migrations` table and `migrations list` shows the unapplied ones. That
`migrations apply` skips the restored ones after an import is inferred: run
`npx wrangler d1 migrations list <DATABASE> --remote` first and confirm it
lists only the newer migrations before you apply anything. Do not import into a database that already holds
rows: the keys would collide.

## Rollback of a backup routine

Nothing here changes the live database except the restore. To stop taking
backups, stop running the script. To undo a restore, use the previous
bookmark. To remove old copies, see retention in [operations.md](operations.md).

## Sources and dates

- Cloudflare, "Time Travel and backups", D1 reference, page last updated
  2026-11-06, read 2026-11-09: bookmarks, `time-travel info`, `time-travel
  restore`, the in-place overwrite warning, the previous bookmark returned, the
  30 day and 7 day windows.
- Cloudflare, "Import and export data", D1 best practices, page last updated
  2026-04-21, read 2026-11-09: `wrangler d1 export` forms, `--table`,
  `--output`, `execute --file`, the virtual table and 5 GiB limits, an export
  blocks other requests while it runs.
- Cloudflare changelog, "D1 database undelete requests limited to the last 7
  days", 2026-10-23, read 2026-11-09 (through the documentation search).
- Cloudflare, "Migrations", D1 reference, read 2026-11-09 (through the
  documentation search): the `d1_migrations` table and `migrations list`.
- This repository: `scripts/backup-d1.sh` and `tests/test_backup_d1.py`
  (the script, the exclusions, the file modes, the refusal inside a git work
  tree and the offline restore were run in the tests).
