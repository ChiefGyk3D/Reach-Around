# Privacy: lead retention and deletion requests

This page is for the site owner who runs the media kit gate. It covers what the
Worker keeps about a person who typed an email address into the gate, for how
long, and how you honor a request to delete it. It is not legal advice, and the
public privacy page your site shows is generated from `features.kit_gate`
separately.

## What a lead is

A lead is one row in the Worker's D1 `leads` table: the address as the visitor
typed it (lowercased), a two-letter country code the edge supplied, timestamps,
and the kit events tied to that lead (an hour-rounded "unlocked", "viewed" or
"verified" record). There is no column for an IP address, a user agent or a
referrer. The row does not record a consent choice beyond the fact that the
visitor submitted the form: the lead proves only that someone typed the
address.

## How long it is kept

The hourly scheduled job deletes every lead older than `LEAD_RETENTION_DAYS`,
counted from the lead's `created_ts`. Seeing the same address again does not
extend it.

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
  the next hourly runs: deletion can lag by hours or, with very many events,
  days. This keeps the purge inside the free D1 write allowance (docs/deploy.md,
  "Kit limits and the free-plan budget"). The same run records `last_purge_ts` in
  `kit_meta`; a stale value means the job is not running.
- The job runs on the hourly cron only. The 2-minute live-poller cron never
  touches leads.
- The purge logs a fixed word and a count, never an address.

To check the last run (the query returns a time in milliseconds, not a person):

```
npx wrangler d1 execute reach-around-clicks --remote --command "SELECT value FROM kit_meta WHERE key = 'last_purge_ts'"
```

Not covered by this retention: Cloudflare's own recovery windows and logs for
the D1 database, and any export you made by hand. Those follow the provider's
rules and your own housekeeping (docs/security/threat-model.md, T-17).

## Honoring a deletion request

There is no web route that deletes a lead, on purpose: anyone could call it.
You run a local command, and then apply the SQL it prints with `wrangler`.

1. Export the `leads` table to a private local file. Use a directory only you
   can read, and delete the file when you are done (it holds every address):

   ```
   umask 077
   npx wrangler d1 execute reach-around-clicks --remote --json \
     --command "SELECT id, email FROM leads" > leads-export.json
   ```

2. Dry run. This is the default and changes nothing. Put the address on stdin
   so it stays out of your shell history and the process list:

   ```
   read -r ADDR   # type the address, press Enter
   printf '%s\n' "$ADDR" | reach-around leads delete --input leads-export.json --email -
   ```

   It prints, for example, `dry run: 1 lead matches p***@***.test`. Exit 1 means
   no lead has that address (it may already be gone, or the spelling differs).
   More than one match refuses and prints nothing, because the table cannot
   hold two.

3. Print the delete SQL with `--yes`. The SQL names the lead by its random id,
   not by the address:

   ```
   printf '%s\n' "$ADDR" | reach-around leads delete --input leads-export.json --email - --yes > delete-lead.sql
   ```

4. Apply it, then read the result back. The last statement counts the lead
   after the delete and must show `0`:

   ```
   npx wrangler d1 execute reach-around-clicks --remote --file delete-lead.sql
   ```

5. Delete `leads-export.json` and `delete-lead.sql`, and `unset ADDR`.

The command reads only the file you give it. It makes no network call, needs no
token and writes no file. It refuses an address that is not valid (ASCII, one
`@`, a dotted domain; whitespace around it and upper case are normalized), and
refuses an export that has a result block without `success: true`, a column other than the `leads` columns, a duplicate
key, or an id that is not 32 lowercase hex characters. If you type the address
on the command line (`--email ADDR`) instead of through stdin, your shell keeps
it in its history; argparse can also repeat an unrecognized extra word in its
own usage error. Prefer `--email -`.

After a deletion, a lead who comes back and types the address again is a new
lead. Remove copies that sit outside D1 yourself: see the inventory in
docs/security/threat-model.md (T-17), and say in your reply what you could not
reach.

## Click counters (funnel rollups, aggregate only)

Besides the hour-rounded click rows (kept 90 days), the Worker keeps counts of
clicks per day: by link and source label, and by link and country. A counter
row is a count and closed labels (a link id, a label from the site's fixed list
or `direct` or `other`, a two-letter country code). Not collected: a cookie, a
stored identifier, an IP address, a user agent, a referrer address, a timestamp
finer than a day on these tables, or any link between a click and a page view or
a person. Source and country are never stored together in one row, and no
report prints a three-way cell. Day counts are kept 90 days; the monthly
totals made from them are kept 25 months. Because nothing identifies a person,
a deletion request cannot be matched to a counter row; the retention above is
the control. The public privacy page states this in plain words.
