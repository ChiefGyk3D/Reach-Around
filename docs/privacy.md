# Privacy: lead retention and deletion requests

For the analytics boundary that applies across tables, logs, caches, queues and
exports, see the [Audience-analysis do-not-collect list](audience-analysis-privacy.md).

This page is for the site owner who runs the media kit gate. It covers what the
Worker keeps about a person who typed an email address into the gate, for how
long, and how you honor a request to delete it. It is not legal advice, and the
public privacy page your site shows is generated from the site config
(`features`, `analytics`, the live block and `alerts`) separately, in the
owner-approved wording in `src/reach_around/site/privacy.py`.

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
  days. This keeps the purge inside the D1 write allowance (docs/deploy.md,
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
`none`) and the UTC time. It never carries an IP address, user agent, cookie,
token or country. The copy in your chat service or mailbox is outside the
Worker's retention: the `LEAD_RETENTION_DAYS` purge does not reach it, so delete
it there when you honor a deletion request, and mention the channel in your own
privacy wording. With alerts off (the default) nothing leaves the Worker.

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
is blocked or fails sends that small request; the Worker discards any request
that carries the signal, whatever the page did. The guarantee covers the
site's own script (it removes the `ping` attribute at load, on click, on
middle-click and on the context menu, and treats an unreadable signal as an
opt-out) plus that server-side discard. It does not cover other scripts on the
page (one that runs later can put the attribute back), visitors without
JavaScript, or links inside shadow roots or iframes. The Worker's reply to a
discarded click has the same status, headers and body as a recorded one; its
timing is not equalized. A header value counts as the signal when any
comma-separated token, trimmed of spaces and tabs only, is `1`. The page is silent on the
landing and hop counters because those tables are not collected yet; the
sentence about them returns when they are switched on.

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
