# Privacy: lead retention and deletion requests

> **Stale in parts (state STALE, issue #801). Covers lead retention and deletion only. The 2026-11-24 privacy decisions supersede it where they differ: region-only raw rows, hour-rounded raw events, masked alert emails by default, and the operator duties still to be written. The wording tested in this page is unchanged. See the ADR index in `docs/wiki/adr/` once merged (PR #835).**

For the analytics boundary that applies across tables, logs, caches, queues and
exports, see the [Audience-analysis do-not-collect list](audience-analysis-privacy.md).

This page is for the site owner who runs the media kit gate. It covers what the
Worker keeps about a person who typed an email address into the gate, for how
long, and how you honor a request to delete it. It is not legal advice, and the
public privacy page your site shows is generated from the site config
(`features`, `analytics`, the live block and `alerts`) separately, in the
owner-approved wording in `src/reach_around/site/privacy.py`.

## The fixed privacy notice and the gate form notice

The generated page keeps the per-item what, why and kept sections and adds one
fixed section after them (`NOTICE_TEMPLATE` in `privacy.py`, issue #777). It cannot
be turned off. Owner-approved copy is used verbatim; only the placeholders are
filled:

- `controller_name` and `contact` from `privacy.controller_name` and
  `privacy.contact`. Both are required when `features.kit_gate` is on or alerts
  are on (the config is refused, and the build refuses too, naming the field and
  never the value). With neither on, an unset value prints as "the site owner".
- `raw_view_days` from `analytics.raw_keep_days`; `raw_view_max_days` is the
  Worker's cap, `min(3 x raw_keep_days, 90)`, the same rule as the per-item text.
- `click_days` is `RAW_CLICK_KEEP_DAYS`, `daily_totals_days` is
  `AGGREGATE_KEEP_DAYS`, `monthly_totals_months` is `MONTHLY_KEEP_MONTHS` (25,
  `MONTHS_KEPT` in `worker/src/clickfunnel.ts`; a test reads the Worker source).
- `geo_level` is `analytics.geo_level` when the config has that field, else the
  Worker's default (`region`, set by PR #793; a test reads the Worker source).
- `alert_providers` is the list of configured `alerts.channels`, names only. The
  one sentence that uses it is dropped when alerts are off.
- `dpa_note` and `turnstile_storage_note` are optional text, empty by default.
- `lead_days` is `privacy.lead_retention_days`.

The build fails if any placeholder has no value, and a test scans every generated
page for a leftover one. The same notice, ending with a link to the privacy page,
sits beside the email field of the kit gate form on the kit page and the retry
page (issue #780, `GATE_NOTICE_TEMPLATE`). The form is the only place it appears.

## Live status request

The generated page carries this when the site has a live block (the item after
the page views, followed by a player item that says the streaming platform sees
the visitor's IP address once the visitor clicks to play). The page's short
wording is `LIVE_WHAT` in `privacy.py`; the fuller description of the request is:

When Live is enabled, your browser asks my site for the current stream status as
soon as you open any tab page or the privacy page, then asks again every minute
while the page is visible. My code sets no cookies and does not collect or store
identifiers from those requests. The response contains a format version, a cache
time, and the time the status was checked. For each stream it contains whether
the stream is live, when it went live, and a fixed diagnostic code such as `ok`
or `auth_failed`; the code never contains platform or visitor data.

## What a lead is

A lead is one row in the Worker's D1 `leads` table: the address as the visitor
typed it (lowercased), a two-letter country code the edge supplied, timestamps,
and the kit events tied to that lead (an hour-rounded "unlocked", "viewed" or
"verified" record). There is no column for an IP address, a user agent or a
referrer. The row does not record a consent choice beyond the fact that the
visitor submitted the form: the lead proves only that someone typed the
address.

## How long it is kept

A lead becomes due for deletion `LEAD_RETENTION_DAYS` after its `created_ts`.
Seeing the same address again does not extend it. The hourly scheduled job
removes due leads in bounded batches, so the real deletion time is the due time
plus however long the job needs to reach that lead; it is not an exact deadline.
The bounds are below. The generated public privacy page states the number of
days only and does not mention this lag, so do not promise a hard deadline in
your own wording.

| Setting | Range | Default | Meaning |
|---|---|---|---|
| `privacy.lead_retention_days` in `site.yaml` | 1 to 365 | 30 | Days a lead is kept, for every lead |

This is the one source of truth. The build prints it on the generated privacy
page and records it in the build manifest, and the deploy writes the same number
to the Worker as `LEAD_RETENTION_DAYS` (the Worker's variable, below). The
repository variable `LEAD_RETENTION_DAYS` is optional; when it is set and differs
from `privacy.lead_retention_days` the deploy stops and names both numbers, so
the page can never promise one period while the purge uses another. A config
value above 365 (the old limit was 3650) is refused when the config is loaded:
the Worker never keeps a lead longer than 365 days, so a larger number could only
have been a false promise. Lower it to 365 or less.

The Worker reads the variable `LEAD_RETENTION_DAYS`:

- The default is 30 days. The hard maximum is 365 days; there is no setting
  above it.
- A value that is not plain digits in that range (0, 366, a sign, a space, text)
  is ignored: the job uses 30 days and logs one fixed "rejected" line. A typo
  never keeps data longer and never wipes the table.
- A lead exactly at the limit is kept; one second older is deleted. The kit
  events of a deleted lead go with it.
- Each run is one D1 query that deletes at most 80 kit events of expired leads
  and at most 20 expired leads. A lead is deleted only once its events are gone,
  so a lead with many events takes several runs, and a big backlog continues on
  the next hourly runs. The true window: when the job runs every hour, it
  removes at most 20 leads and 80 events an hour (480 leads and 1,920 events a
  day), so a lead that owns E events and sits behind N other due leads is gone
  after at least about max(ceil(E / 80), ceil(N / 20)) runs, which is that many
  hours. A normal day's expiries clear within the hour; a large backlog (an
  import, or a long stretch with the job off) takes days, and a purge may not
  run at all while `SCHEDULED_KILL` is on or, below `QUERY_BUDGET` 9, when the
  rollup uses up the budget first, so check `last_purge_ts`. This keeps the purge inside the D1 write allowance (docs/deploy.md,
  "Kit limits and the daily write budget"). The same run records `last_purge_ts` in
  `kit_meta`; a stale value means the job is not running.
- The job runs on the hourly cron only. The 2-minute live-poller cron never
  touches leads.
- The purge logs a fixed word and a count, never an address.

To check the last run (the query returns a time in milliseconds, not a person):

```
npx wrangler d1 execute reach-around-clicks --remote --command "SELECT value FROM kit_meta WHERE key = 'last_purge_ts'"
```

Not covered by this retention: Cloudflare's own recovery windows and logs for
the D1 database, owner alert copies in a chat service or mailbox (below), and any export you made by hand. Those follow the provider's
rules and your own housekeeping (docs/security/threat-model.md, T-17).

## Owner alerts and the address

If you turn on owner alerts (`alerts` in `site.yaml`, see [alerts](alerts.md)),
the Worker forwards a short notice to the Discord room, Matrix room or mailbox
you chose when someone unlocks the kit or a new lead is stored. That notice can
carry the visitor's address (`alerts.visitor_email`: `full`, `masked` or
`none`; masked by default) and the UTC time. It never carries an IP address, user agent, cookie,
token or country. The copy in your chat service or mailbox is outside the
Worker's retention: the `LEAD_RETENTION_DAYS` purge does not reach it, so delete
it there when you honor a deletion request, and mention the channel in your own
privacy wording. With alerts off (the default) nothing leaves the Worker.

## Access and erasure requests (GDPR)

This section is the factual procedure. The notice sentences that tell visitors
about these rights are TODO-copy: they are written separately and are not part
of this procedure.

**Response time.** Answer an access or erasure request within one month of
receiving it. State the date you received it, and put the reply and what you did
in writing. If you cannot finish in the month, say so inside the month. Only
you can send the answer: the tools below print what you need, they send nothing.

**What can be found.** A lead (the address someone typed into the gate, with the
country, created and verified times, and the kit events tied to it) can be
found, because it holds the address. The raw page view rows and click rows hold
no address, no IP address and no identifier, so a request cannot be matched to
them by design (Art. 11: you are not required to collect more data to identify
a person only to answer a request). Say that in your reply instead of
searching. The hour-rounded counters are aggregate and are not personal data
about one person.

### Access: what you hold about one address

1. Export the lead and its events to a private local file in a fresh private
   directory (the file holds every address, so delete the directory when you
   are done). The export must be these two queries, in this order: the tool
   refuses an export with a missing, extra or swapped result set, because a
   missing events result would otherwise read as "no events":

   ```
   umask 077
   WORK=$(mktemp -d "$HOME/leads-request-XXXXXX") && cd "$WORK" && set -o noclobber
   npx wrangler d1 execute reach-around-leads --remote --json \
     --command "SELECT id, email, created_ts, verified_ts, country FROM leads; SELECT lead_id, kind, ts FROM kit_events" > leads-export.json
   ```

2. Put the address in a file or on stdin, never on the command line, and show
   the lead:

   ```
   read -r ADDR   # type the address, press Enter
   printf '%s\n' "$ADDR" | reach-around leads show --input leads-export.json --email-file - --database reach-around-leads
   ```

   It prints the masked address, the country, the created and verified times
   (UTC) and each kit event kind with its hour (`unlock`, `view`, `verify`).
   It prints nothing else: no id, no other lead, no SQL, no last-seen time.
   Exit 1 means no lead has that address (it may already be gone, or the
   spelling differs). It makes no network call and writes no file. Its error
   messages are fixed and never carry the address, even masked, because stderr
   can reach a log. The export must hold exactly the five lead columns and the
   three event columns above, and a lead id that appears twice refuses the whole
   export. An event time that is not on the hour is shown as its hour.

   The command prints the database it expects the export to come from
   (`database: reach-around-leads`) and requires `--database reach-around-leads`
   every time, with exactly that name. What this proves and what it does not: a
   wrangler export does not name its database, so the flag is your statement of
   where the file came from. It stops a wrong or missing name; it cannot prove
   the file came from the leads database. Take the export from the leads
   database only.

   An address on the command line is refused by both `leads show` and
   `leads delete` (for `delete`, only `--email -` is accepted), before the
   export is read. That does not undo what a shell already did: if you typed it
   after `--email`, your shell history and the process list already hold it.
   Use stdin or a file.

3. Copy the printed lines into your written reply, and add the copies the tool
   cannot see (alert copies in your chat room or mailbox, inquiry text, hand-made
   exports; the list is below).

### Erasure: the known copies, and what you must check by hand

`reach-around leads delete --checklist` prints this list and needs no export and
no address. It checks nothing: it does not find copies and cannot confirm a
deletion outside the SQL it prints, so it is not proof that no other copy
exists. Deleting the database rows (the steps in the next section) is only the
first item:

1. The `leads` row and its `kit_events` rows in D1.
2. Alert copies in the Discord room, the Matrix room or the mailbox, when owner
   alerts are on.
3. Inquiry text the person sent you, in your mailbox or inbox.
4. D1 Time Travel. D1 keeps point-in-time recovery for 7 days on the Free plan
   and 30 days on the Paid plan. Deleted rows stay restorable in that window and
   age out on their own. Say so in your reply, with the date they are gone. Do
   not restore to a point before the deletion without deleting the lead again.
5. Hand-made exports, dumps and spreadsheets (including the files you made for
   the steps above).
6. Shell history, tickets and notes that hold the address.
7. The `alert_dedup` hash (automated by `leads delete`, #905; see the store
   table below). `reach-around leads delete --yes --alerts-database NAME`
   checks the schemas, prints the lead SQL, and after you apply it
   `--finish` reads the lead tables back, clears the whole table and reads it
   back as 0 twice. Only exit 0 is complete; exit 3 means the lead rows are
   erased but the alert hashes are not confirmed cleared, and exit 4 means the
   lead rows are not confirmed erased. A quiet site keeps up to 500 rows until
   the next alert or a clear. Clearing it only resets repeat-alert suppression
   for up to 10 minutes. Close the kit gate (the kill switch) while you erase.
8. By hand, where neither this list nor the tool can look: Worker logs and any
   collector logs or exception output; alert delivery logs at the chat or mail
   provider; automated backups and exports of the database beyond Time Travel;
   CI artifacts and deploy logs; monitoring and error-tracking systems; any
   system wired to the Worker that stores request or alert payloads. Record the
   result of each check, even "nothing found".

### Every store, checked against the schema

Checked 2026-11-26 against every table, column, view and trigger the
migrations create. The test `tests/test_erasure_coverage.py` runs the
migrations in order in an in-memory SQLite database and reads the schema from
it (not from the migration text), and fails when something appears that
`scripts/erasure-stores.json` has not classified, when a column in a table not
classed lead or lead_hash has a personal-looking name, a blob or untyped
declaration or an expression default without its own recorded reason, or when
the recorded column lists or the schema digest no longer match. Columns are read
with `table_xinfo`, so generated (virtual or stored) and hidden columns are in
the inventory; each one needs its own recorded reason, and a table that has one
also pins its normalized CREATE text, which holds the generating expression. The
schema digest covers every column, type, default, key and hidden flag, and the
full text of every view, trigger and index (whitespace is collapsed between
tokens only, never inside a string, a quoted name or a comment), so changing only
a view or trigger body fails until it is looked at again. Class "lead" means a
row holds an address or carries a lead id; the delete tool removes those.

What this classification proves, and what it only infers. The test proves that
every table and column the migrations create is classified, that a column whose
name, declared type or default looks like it could hold personal data needs a
recorded reason, and that a table linked to `leads` by a foreign key is classed
lead. It does not trace what the Worker writes into a column: a TEXT column with
an innocuous name in a table classed aggregate, with a reviewer's reason, would
pass. So `leads` and `kit_events` are the only tables this review classified as
holding an address or a lead id; that is a statement about the classification,
not a data-flow proof (Worker D1 writes are not analyzed; only storage
primitives and bindings are inventoried). The test also runs on SQLite, which
stands in for D1. Inferred and not tested against D1 itself: that D1's SQL
dialect and `table_xinfo` behave as SQLite's do on these migrations, that
foreign keys are enforced and `ON DELETE CASCADE` fires, that the statements of
one `db.batch` run as one unit, and what D1 does with the rest of a file after
one statement fails. The purge and dedup checks run the real statements on
SQLite; whether the Worker calls them is covered by the vitest suite in
`worker/test`, not by the Python test.

What the test does check about the Worker's other stores: every file under
`worker/src` that names a storage primitive or a storage binding from any
wrangler config is listed, and so is each statement that mentions one (a short
hash of the statement text, comments and spacing removed). A new write through an
already approved binding, or an edit to a reviewed one, fails until it is read
and recorded. This is a lexical inventory of where storage is named. It is not a
proof of where data flows: it pins statement text, it does not follow data, and
a write that never names a primitive or a binding (an indirect call through an
object passed in, a computed method name) is not seen. What an operator must
still verify by hand: that the KV namespace holds only the gated page and live
status state (list its keys), that no R2, Durable Object or queue binding exists
in the deployed configuration, and that nothing outside this repository was wired
to the Worker to store requests or alert payloads.

Every export command is found by scanning every text file in the repository
outside dependency and cache directories and Markdown (scripts anywhere, every
`package.json`, Makefile and justfile, workflows, composite actions, a
Containerfile, deploy files, JSON and YAML command arrays). A file larger than
2 MB is scanned in overlapping chunks, not skipped; a file larger than 64 MB is
not read and fails the test by name unless it is listed with a reason under
`export_paths.unscanned_large_files`. In Markdown, each command line is judged
on its own (a fenced line, an inline code span, a piece of prose), so a later
`--table` on another command does not hide a whole-database export. A command
built at run time from pieces that never appear together is not seen.

What the test cannot do: it cannot tell that a person read a registry change.
The registry carries a digest of itself, so the schema digest, the column lists
and the reviewed call sites cannot be edited one at a time; that proves the
records were recomputed together, not that someone else approved them. Review of
changes to `scripts/erasure-stores.json` (and to this table) by a second human is
a policy control, not something CI enforces. That includes every `known_gaps`
entry: it must cite an issue listed under `reviewed_issues` with its title, and
the registry digest changes with either list, so a waiver cannot be added
quietly, but the test is not the reviewer and cannot tell whether a person agreed
to it. A table classed `lead_hash` also needs a reason that names how its value is
derived and how long it lives, and a recorded reason for every column, the hash
column included. The repository's CODEOWNERS file
does not cover the registry and does not require a code owner review (see OPEN
QUESTION 5).

| Store | Holds | Touched by the delete tool | How erasure is done or checked |
|---|---|---|---|
| `leads` (11 columns) | the address, country, times, session floor | yes | `DELETE`, read-back counts the row; also purged by age |
| `kit_events` (4 columns) | hour-rounded unlock, view and verify events by lead id | yes | `DELETE`, read-back counts the rows; the foreign key also cascades |
| `alert_dedup` (3 columns) | keyed hash of a recent alert, which can include the address; a row stops counting as a repeat after 10 minutes but is deleted only when the next alert event is admitted, so on a quiet site it can stay stored (at most 500 rows) until then or until cleared by hand | yes (#905): `leads delete --yes --alerts-database NAME`, then `--finish` | the whole table is cleared and read back to 0 twice, after the lead read-back; exit 3 if not confirmed; the owner closes the kit gate during erasure (statements below); a keyed hash is not erased by being hard to match |
| `visitor_seen`, `view_events`, `click_events` | coarse visitor rows with no address, no IP address, no lead id | no | not matchable to a person by design (Art. 11); removed by retention |
| 26 other tables (counters, rollups, budgets, logs, cursors) | counts and bookkeeping | no | aggregate, no row per person; counts cannot be un-counted |
| KV, edge cache | the gated page, live status state, the public status body | no | no lead data found in the listed call sites; a text scan, not a data-flow proof (verify the namespace's keys by hand); the Worker never writes the gated page key |
| R2, Durable Objects, queues | not used | not applicable | the test fails if one is added without review |
| Unlock cookie | a signed token, no server copy | not applicable | stops opening the kit once the `leads` row is gone |
| `scripts/backup-d1.sh` exports | `leads`, `kit_events`, `visitor_seen`, `view_events` are never exported | not applicable | the only lead-related data this script exports is the `alert_dedup` hashes; the test runs the script against a recording stub and judges every wrangler call it makes, and inventories every export command in any text file of the repository |
| Hand-made whole-database exports | a `wrangler d1 export` with no `--table` (shown in `docs/deploy.md`, `docs/failure-modes.md` and the wiki as a backstop before a migration) holds every table, `leads` and `kit_events` included | no | by hand, checklist item 5; the test lists the docs that show one, so a new one is noticed |
| D1 Time Travel | the whole database, 7 days Free, 30 days Paid | no | ages out; see the checklist |
| Alert copies, inquiry text, hand-made exports, logs | outside the Worker | no | by hand, items 2, 3, 5, 6 and 8 of the checklist |

Alert hash erasure (#905). `reach-around leads delete` clears the alert hash
table itself, in two steps, and a quiet site otherwise keeps up to 500 rows until
the next alert is admitted:

1. `--yes --alerts-database NAME --alerts-database-id UUID --leads-database-id
   UUID` (all three are required with `--yes`; without them the command exits 2
   before it prints any SQL). The two ids are the `database_id` values of the
   `[[d1_databases]]` entries in the Worker's own configuration (the private
   instance repository holds the real ones; this repository's `wrangler.toml`
   carries placeholders): the entry for the binding the Worker reads alerts
   from, and the one for the leads binding. The command asks Cloudflare for the
   id behind each name (`wrangler d1 info NAME --json`) and refuses unless it
   equals the id you gave, so a decoy database with the right tables, or a
   name that points somewhere else, ends in exit 2 with nothing changed. If one
   database holds both, give the same name and the same id for both. Then it
   reads the schema of the leads database and of the named database. The named
   database must hold `alert_dedup` and must NOT hold `leads` or `kit_events`,
   unless it is the leads database itself. An absent table is a refusal, never
   a clear. It then prints the lead SQL and its apply command and **exits 10:
   the checks passed and NOTHING is erased.** Exit 10 is never a completed
   erasure; only the `--finish` run can exit 0.
2. Close the kit gate (the kill switch) first and keep it closed until this
   step ends: it is a Worker setting this tool cannot read, so it is a
   precondition you must meet, not a check. After you apply the SQL, run the
   same command again with `--finish`. It repeats the identity and schema
   checks and reads the lead tables back for the exported id AND for the
   address (must be 0 and 0: a lead created again for the same address, with a
   new id, counts; else exit 4 and the alert table is untouched). The address
   is compared trimmed and lower-cased on both sides, which is how the Worker
   stores it (the table refuses any other spelling); plus-addresses and dots are
   not folded together, because the store does not treat them as equal. It checks both
   databases again, clears the whole alert table (not only expired rows) with
   one statement that deletes only while the alert database holds no lead
   table, and reads it back, repeating the clear up to three times if a row
   arrives. It waits two seconds, reads the alert table back once more, checks
   the identity and schema a last time and reads the lead tables for the
   address one last time. Any alert row left, including one whose life began
   after the erasure started, a lead row for the address that exists again, or
   a database that changed during the run, is exit 3: the lead rows were read
   as erased but the erasure is NOT complete. Once `--finish` is running it
   never exits 2: before the lead tables read clean every failure is exit 4,
   after that every failure is exit 3.

Database identity. `wrangler d1 execute` takes a database name or binding, not
an id, and its JSON answer is not documented to carry the database id
(Cloudflare D1 wrangler commands page, read 2026-11-26), so nothing available
to this tool proves which database one execute call used. What it does: every
remote statement runs between two checks (`d1 info NAME --json`, before and
after) that the name resolves to the id you gave, and a changed id at either
check discards the answer (exit 3 or 4 as above). So the ids bind the name to
the database the Worker's configuration names at the moment of each check. A
name that is re-pointed to another database and back inside one call is NOT
detected (the call could then read 0 on a decoy), and neither is a change
between the last check and the end of the run. Treat the result as verified
only for a database whose name and bindings nobody else can change during the
erasure.

The wrangler. The command runs only the wrangler installed in this repository
(`cd worker && npm ci --ignore-scripts`; `--wrangler PATH` takes a path inside
`worker/node_modules/.bin` or `worker/node_modules/wrangler`, nothing else),
never one found through `PATH` or `npx`. It hands your Cloudflare token to that
program, which is code you already trust with the token as the same OS user; a
printed version number proves nothing, so the tool never asks for one. What it
checks: the real path (symlinks resolved) stays inside the wrangler package; the
package's `package.json` and npm's install record
(`worker/node_modules/.package-lock.json`) name wrangler at the version and
tarball digest in `worker/package-lock.json`; the file and every directory above
it belong to root or you (the file too, so a root-owned `0755` file is
accepted) and are not writable by others; inside this repository not by group
either (fix with `chmod -R go-w worker/node_modules`). Outside it, a sticky
shared directory such as `/tmp` is accepted only when root owns it, and so is
a group of your own that nobody else belongs to. The wrangler package, and the
packages it lists in `dependencies`, `optionalDependencies` or
`peerDependencies` (resolved the way node resolves them under
`worker/node_modules`, recursively), are walked: every file and directory must
belong to root or you with no group or other write bit, a symlink to a file must
stay in the repository, a symlink to a directory must resolve inside
`worker/node_modules` (the directories between its target and the repository
root are checked, and the target is walked like any other directory), and the
walk stops with a refusal past 200000 entries. A dependency a package requires
(`dependencies`, or a peer dependency not marked optional) that is not installed
under `worker/node_modules` is a refusal; only `optionalDependencies` and
optional peers may be absent. A `node_modules` directory in the repository root
or in any directory above it, or a `.node_modules` or `.node_libraries` folder
in your home directory, is a refusal too, because node would search it and it is
not checked (the message names no path). The program's environment never holds
`NODE_PATH`, `NODE_OPTIONS` or `NPM_CONFIG_*`. Only that walked tree is checked:
a package no `dependencies` field names, or one node loads from outside
`worker/node_modules` (for example the system module folder next to node
itself), is not seen. Step 1 prints the apply command with the wrangler path
relative to the repository root, never an absolute path. What it does not
prove: that the installed files are those of the
published package (npm keeps no per-file digests and the lock file's digest
covers the tarball, which is not re-downloaded), that `node` is genuine, or that
nothing changes between the check and the run. Anyone who can write as your OS
user can defeat all of it. The program gets only the token, the account id,
`HOME` and a fixed `PATH` (node must be in `/usr/bin` or `/bin`), runs in `/`
with no stdin and in its own process group, and its output is read with a hard
size cap (on the cap or the time limit the whole group is killed, not only the
program; a descendant that starts a session of its own is not reached).

Exit codes: 0 only for the verified erasure of `--finish` (a dry run, which
requests nothing and changes nothing, also exits 0); 10 `--yes` step 1: checks
passed, SQL printed, NOT erased; 1 no match; 2 usage problem, refused export,
database, database id or wrangler, in step 1 or before `--finish` makes any
remote call (this tool changed nothing); 3 the lead rows were read as erased but
the alert hashes are not confirmed cleared, a database changed, a check failed
after that point, or a lead for the address exists again; 4 lead rows not
confirmed erased (any failure before that point; the alert table was not
touched). Residual windows: a person who can still open the kit or trigger an
alert can create a new lead or hash after the last read-back, and the tool
cannot close that; the kit gate cannot be read by this tool, so closing it is
your precondition; and the identity checks and the statements are separate
remote calls (see "Database identity" for what that leaves). The ids bind the
name to the database the Worker's configuration names; they cannot prove that
the Worker you run uses that configuration.
Clearing every row (not only the lead's own hashes, which cannot be told apart
without the Worker's secret key) resets repeat-alert suppression for up to 10
minutes for everyone; that is the cost of this step and the owner accepts it by
running it.
Recovery after exit 3: run `--finish` again (it uses the checked wrangler). To do
it by hand, use the same pinned wrangler, in the directory whose configuration
names the database, and read back to 0 before you call the erasure complete:

```
"$WRANGLER" d1 execute <DATABASE> --remote --command "DELETE FROM alert_dedup"
"$WRANGLER" d1 execute <DATABASE> --remote --command "SELECT COUNT(*) AS alert_dedup_remaining FROM alert_dedup"
```

(`WRANGLER` is the absolute path of `worker/node_modules/.bin/wrangler`, as in the
block under "Honoring a deletion request".) For routine housekeeping only, this deletes just
the rows already past their 10 minute life, the point after which they no longer
count as repeats; it leaves a recent row, so it is not the erasure step. It takes
the current time in milliseconds as the number:

```
"$WRANGLER" d1 execute <DATABASE> --remote --command "DELETE FROM alert_dedup WHERE expires_at <= <NOW_MS>"
```

OPEN QUESTION 1, backups beyond Time Travel. The checklist can only tell you to
look: the tool does not know where your automated backups go. Options: (a)
accept that a copy outside the database ages out with that system's own
retention and state the date in your reply (current text); (b) set a maximum
age for any backup you keep and delete older ones on a schedule; (c) stop
keeping backups of anything that can hold an address (the export script already
leaves the lead tables out). Needs the owner's choice.

OPEN QUESTION 2, the point-in-time read-back. Two runs reading 0 and 0 are still
a moment in time: the Worker can write an event after the read. Options: (a)
keep the repeat-until-two-clean-runs rule (current); (b) pause the event writes
with the kit switch during the erasure window, then re-enable (loses unlock
events for a few minutes); (c) rely on the foreign key cascade as well (already
in the schema; D1 turning foreign keys on is inferred and unverified here).
Needs the owner's choice.

OPEN QUESTION 3, short-lived alert hashes. Today an expired row is removed only
when the next alert event is admitted (checked in the code: nothing prunes it on
a timer), so on a quiet site it stays until then. Options: (a) the whole-table
clear and read-back run by `leads delete --finish` after a schema check of the
named database (current, #905); (b) prune expired
`alert_dedup` rows in the scheduled handler too, so a quiet site never holds one
past its life (a Worker change; it would still not remove a row made minutes
ago); (c) add `DELETE FROM alert_dedup` to the delete SQL, which is the one-command
erasure but fails the whole file if the alert tables are in another database than
the leads one. Needs the owner's choice.

OPEN QUESTION 5, who reviews the registry. A test cannot enforce that a second
person approves a change to `scripts/erasure-stores.json`. Options: (a) add the
registry, its test and this page to CODEOWNERS and turn on required code owner
review in branch protection (the CODEOWNERS file is a guard file, so this is
the owner's change); (b) keep review as a stated policy only (current). Needs
the owner's choice.

OPEN QUESTION 4, aggregate counts. Hour-rounded counters (gate attempts, kit
views by day) include the erased person's events and cannot be un-counted. The
position written here is that they are not personal data about one person.
Options: (a) keep that position (current); (b) say so in the visitor notice
sentence; (c) drop counters that fall under a small threshold. Needs the owner's
choice.

TODO-copy: the visitor-facing sentence for the right of access, the right to
erasure, the one-month answer and the Art. 11 position on page view rows.

## Honoring a deletion request

There is no web route that deletes a lead, on purpose: anyone could call it.
You run a local command, and then apply the SQL it prints with `wrangler`.

The export holds every address, so the procedure below keeps it in a private
temporary directory and removes it however the run ends: on success, on a
failed step (an expired token, a dropped network, no matching lead), or on
Ctrl-C. Run it as one block from your project directory. The steps inside are:

1. Export the `leads` table to a file in the private directory (mode 700, files
   mode 600). The database is `reach-around-leads`; the tool refuses an export
   from any other database name, and `--database` is required on every call.
2. Dry run. This is the default and changes nothing. The address goes on stdin
   so it stays out of your shell history and the process list. It prints, for
   example, `dry run: 1 lead matches p***@***.test`. Exit 1 means no lead has
   that address (it may already be gone, or the spelling differs), and the block
   stops and cleans up. More than one match refuses and prints nothing, because
   the table cannot hold two.
3. Ask you to confirm, then print the delete SQL with `--yes --sql-out`. The
   export is a local file and cannot be trusted to say who owns an id, so every
   deleting statement names the lead's random id and its address: the live
   database deletes the lead and its kit events only if that row still pairs
   them. The file therefore holds the address; the block removes it on exit.
   `--sql-out` creates the file itself, new, mode 0600, and refuses a path that
   already exists or whose last component is a symlink; give it a path inside
   the private directory. Do not use a shell `>` redirect for this file.
4. Apply it, wait a minute, apply it again (it is safe to repeat, and its
   cleanup statement removes events left for a lead that is gone). Each run's
   last statement shows two numbers, `leads_remaining` and `events_remaining`.
   Treat the erasure as done only after two consecutive runs both read `0` and
   `0`, and say in your reply that this is a point-in-time check: D1 offers no
   transaction in a file and the Worker can write an event after the read.
   `leads_remaining` 1 means the id did not belong to that address in the live
   database (a stale or wrong export): nothing was deleted, stop and re-export.
   `events_remaining` above 0 means events remain; run the block again. The two
   tables read here are the only tables this review classified as holding an
   address or a lead id (see "Every store, checked against the schema" above for
   what that classification does and does not prove).
5. The block erases the lead rows only. The `--yes` run inside it exits 10
   (checks passed, SQL printed, NOTHING erased), which the block expects. Run
   the same `leads delete` command again with `--finish` (checklist item 7,
   #905): it reads the lead tables back, clears the alert hashes and reads them
   back as 0 twice. Call the erasure complete only on exit 0 of that `--finish`
   run (exit 3: the alert hashes are not confirmed cleared; exit 4: the lead
   rows are not confirmed erased). Keep the kit gate closed (the kill switch)
   until it exits 0. Set `ALERTS_DB`, `ALERTS_ID` and `LEADS_ID` from the
   Worker's own configuration first (see the alert hash erasure section).

```
(
  set -eu
  umask 077
  WORK="$(mktemp -d)"
  cleanup() { rm -rf -- "$WORK"; }
  trap cleanup EXIT
  trap 'exit 130' HUP INT TERM
  echo "private work directory (removed on exit): $WORK" >&2
  WRANGLER="$PWD/worker/node_modules/.bin/wrangler"   # pinned: cd worker && npm ci --ignore-scripts
  ALERTS_DB="<the database the Worker writes alerts to>"
  ALERTS_ID="<its database_id from the Worker's own configuration>"
  LEADS_ID="<the database_id of reach-around-leads from the same configuration>"
  "$WRANGLER" d1 execute reach-around-leads --remote --json \
    --command "SELECT id, email FROM leads" > "$WORK/leads-export.json"
  printf 'Address to delete: ' >&2
  read -r ADDR
  printf '%s\n' "$ADDR" | reach-around leads delete --input "$WORK/leads-export.json" --email - --database reach-around-leads
  printf 'Print the delete SQL and apply it? Type yes: ' >&2
  read -r OK
  [ "$OK" = yes ]
  RC=0
  printf '%s\n' "$ADDR" | reach-around leads delete --input "$WORK/leads-export.json" --email - --database reach-around-leads --alerts-database "$ALERTS_DB" --alerts-database-id "$ALERTS_ID" --leads-database-id "$LEADS_ID" --yes --sql-out "$WORK/delete-lead.sql" || RC=$?
  [ "$RC" -eq 10 ]   # 10 = checks passed, SQL printed, nothing erased yet
  "$WRANGLER" d1 execute reach-around-leads --remote --file "$WORK/delete-lead.sql"
  sleep 60
  "$WRANGLER" d1 execute reach-around-leads --remote --file "$WORK/delete-lead.sql"
  printf '%s\n' "$ADDR" | reach-around leads delete --input "$WORK/leads-export.json" --email - --database reach-around-leads --alerts-database "$ALERTS_DB" --alerts-database-id "$ALERTS_ID" --leads-database-id "$LEADS_ID" --yes --finish
)
```

The subshell ends the shell variables (`ADDR`) with it, so nothing is left to
unset. A kill signal that cannot be trapped (`kill -9`, a power loss) skips the
cleanup; the work directory is printed on the first line so you can remove it
yourself, and you should check that it is gone before you report the request
done.

The dry run reads only the file you give it and makes no network call. With
`--yes` the command also talks to Cloudflare, only through the repository's
wrangler and only to read the database ids and schemas (step one, exit 10) or,
with `--finish`, to read the lead tables, clear `alert_dedup` and read both back
(checklist item 7, #905). It
writes no file except the `--sql-out` file and never prints remote text. It
refuses an address that
is not valid (ASCII, one `@`, a dotted domain; whitespace around it and upper
case are normalized), and refuses an export that has a result block without
`success: true`, a column other than the `leads` columns, a duplicate key, a
lead id that appears twice, an id that is not 32 lowercase hex characters, or an
export file whose last component is a symlink. The address on the command line is refused:
use `--email -`.

After a deletion, a lead who comes back and types the address again is a new
lead. Remove copies that sit outside D1 yourself: see the inventory in
docs/security/threat-model.md (T-17), and say in your reply what you could not
reach.

## Click counters (funnel rollups, aggregate only)

Besides the hour-rounded click rows (kept 90 days), the Worker keeps counts of
clicks per day: by link and source label, by link and country, by link and landing
label (the page of ours the click was made on) and by link and hop label (the page
of ours the visitor came from, one step). A counter row is a count and closed labels
(a link id, a label from the site's fixed list or `direct`, `none` or `other`, a
two-letter country code). The landing label is a constant the build puts on the page, and the hop
label is read from the page address; neither is stored in the browser, and each is a
separate count that is never joined to another. Not collected: a cookie, a
stored identifier, an IP address, a user agent, a referrer address, a timestamp
finer than a day on these tables, or any link between a click and a page view or
a person. A raw click row carries at most a source label and never a country; country exists only in the counter (rows written before this change are cleared by an owner-run script, `scripts/clear-click-country.sh`, after the new Worker is counting). Source, country, landing and hop are never stored together, and no
report prints a three-way cell. Day counts are kept 90 days, with a site-wide daily cap on their number; the monthly
totals made from them are kept 25 months. Because nothing identifies a person,
a deletion request cannot be matched to a counter row; the retention above is
the control.

What the public privacy page says today (owner decision 2026-11-19, #598): the
hourly click record holds the link, its source label and the hour, with no IP
address and no country; daily totals count clicks by source and by country,
never together and never linked to a page view or a person; hourly records are
deleted after 90 days (the Worker's fixed window in `worker/src/retention.ts`;
`privacy.retention_days` accepts only 90 so the page cannot promise another
number), daily counts after 90 days and monthly
totals are kept up to 25 months; and clicks honor Global Privacy Control and
Do Not Track: a browser sending either signal is not counted or stored, the
site's own script sends nothing, and the link still opens (`click.js` and the
Worker's `click.ts` both check the signal, and a test fails if the page
promises this while either one does not). The page does not say the browser
sends nothing, because it may: the generated links keep the native `ping`
attribute so clicks still count without JavaScript, and a visitor whose script
is blocked or fails may still send a small request (a browser can have
hyperlink auditing turned off or unsupported); the Worker discards any request
that carries the signal, whatever the page did. The guarantee covers the
site's own script (it removes the `ping` attribute at load, on click, on
middle-click and on the context menu, and treats an unreadable signal as an
opt-out) plus that server-side discard. A `ping` attribute removed at load
is deliberately not restored if the signal is cleared later in the same page
view (it fails toward privacy; a page reload re-reads the signal). It does not cover other scripts on the
page (one that runs later can put the attribute back), visitors without
JavaScript, or links inside shadow roots or iframes. The Worker's reply to a
discarded click has the same status, headers and body as a recorded one; its
timing is not equalized. A header value counts as the signal when any
comma-separated token, trimmed of spaces and tabs only, is `1`. The page is silent on the
landing and hop counters because those tables are not collected yet; the
sentence about them returns when they are switched on.

Location follows `analytics.geo_level` (default `region`, the Worker's default
`GEO_LEVEL`). A stored view record holds the country and at most the region; it never
holds a city. At `city` the Worker also adds the view to a daily city total
(`view_city_daily`, no page, source, device kind or time of day with it). The daily
rollup combines a city with fewer than five views (or `MIN_CELL`, if larger) into its
region before anything is exported or reported, and the city total is deleted on
the same schedule as the view records. The page states which of the three applies.
TODO-copy: the visitor-facing location sentences are authored separately.

Page view records follow the same rule of stating the ceiling. The public page
says view records are normally deleted after the configured number of days
(`analytics.raw_keep_days`, default 7). A day's records stay longer until the
site has added them to its daily totals, and the page states when they are due
for deletion no matter what: three times the keep window, never more than 90
days (the Worker's rule in `worker/src/viewretention.ts`; the page computes the
number from the same rule, and a test pins the two together). The purge is
bounded and best effort, so records past that point are removed during the next
cleanup runs, which can occasionally be delayed for a short time. The daily code goes at
the end of each day, abuse counters within about two hours, anonymous totals
after 90 days, and records for a specific person cannot be deleted because they
do not identify anyone.
