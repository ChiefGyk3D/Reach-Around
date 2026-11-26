<!-- SPDX-License-Identifier: Apache-2.0 -->
# 5. Going live: moving from a link-in-bio service

This page is the cutover from a hosted link-in-bio service to Reach Around, with
the rollback. It uses `<prod-host>` for the production hostname, `<old-target>`
for where that hostname points today, and `<pages-project>` for your Pages
project. The long form, with the owner-specific record, is
[docs/cutover-runbook.md](../../cutover-runbook.md).

How to read the marks: **verified** means read from a named source on the date
given; **inferred** means reasoned and not yet run, so rehearse it before the real
switch.

## The decisions this carries out

1. **The switch and the shutdown are two separate steps.** The switch moves the
   production host from the old service to Reach Around. The shutdown cancels the
   old service. Keep the old service live, with nothing deleted on its side, as
   the fallback until Reach Around matches it (analytics and, if you use it, the
   media kit with real generated data) **and** for at least 30 days, whichever is
   later.
2. **The real site is built from your private instance repository**, never from
   the engine's demo.
3. **Every DNS write waits for your explicit word.** If an assistant or an agent
   helps, it prepares and verifies; it never writes DNS on a general go-ahead.
4. **Use the dev configuration for the test and staging deploys.** Use the
   production configuration only after you have decided the project is live
   ([Secrets](04-secrets.md)).

## Before any DNS change

- [ ] The release you will ship is frozen, and the instance pins it by commit and
      image digest ([page 3](03-instance-repo-and-pinning.md)).
- [ ] A soak of 48 hours on that same frozen release, through the instance's own
      deploy path. The soak restarts if you change the release, the image or the
      workflow pin.
- [ ] During the soak, the smoke check passes (`bash scripts/smoke.sh https://<staging-host>`)
      and a fresh D1 export passes the dump check
      (`python3 -I scripts/check-dump.py dump.sql`: no address, no user agent, no
      unexpected column).
- [ ] Your analytics are reconciled against the old service for the same days
      while it is still live. The acceptance steps are in the engine's
      `docs/analytics-acceptance.md`.
- [ ] Your privacy page is approved by you.
- [ ] `reach-around check --config site.yaml --production` has no warning other
      than the unset `cutover_date`.
- [ ] Link inventory parity: export the old service's link list to a private file
      and run `reach-around parity --site out --expected old-links.csv`. It must
      exit 0 (no missing, extra, reordered or changed link). Then request each
      external URL, and check affiliate flags (`rel` contains `sponsored`), which
      `parity` does not cover.
- [ ] Follower numbers show an `as_of` within 30 days and you compared each with
      the old service by eye.
- [ ] A backup is taken and a restore drill passed
      ([docs/backup-restore.md](../../backup-restore.md)), and the old service's
      full data (links, analytics history, forms and leads) is exported to your
      private instance repository.
- [ ] Worker secrets are set by name only, never in a file, variable or chat.
      Check by name: `npx wrangler secret list`.
- [ ] The `/e/*` route exists on the production host. Without it clicks are not
      counted.
- [ ] Edge rules: Browser Cache TTL is "Respect Existing Headers", no zone rule
      rewrites `/` or an old path, and the Pages production branch is `main`.
- [ ] The analytics health alert is on and a test alert reached your device.
- [ ] The Worker's write-budget check printed under 80 percent of your plan quota.
      The Paid plan is the baseline; if you run on Free, the click ceiling does
      not fit (see [Costs](../Costs.md)).

### Migrations with the Worker quiet

D1 migrations are forward only. Run them with nothing writing:

1. Deploy the Worker with all three switches on. This stops the click route, the
   page-view route and the scheduled jobs:

   ```sh
   npx wrangler deploy --var CLICKS_KILL:1 --var VIEWS_KILL:1 --var SCHEDULED_KILL:1
   ```

   `SCHEDULED_KILL` does not stop a run already in progress, so verify before
   migrating: watch `npx wrangler tail` until the next scheduled trigger returns
   immediately and no earlier invocation is still active. While it is on, health
   sampling and its alerts are paused and the window shows in reports as an
   outage gap, so keep it short.
2. If you use the media kit gate, engage `KIT_KILL` too: the three switches cover
   the click, view and scheduled paths only, not the kit routes.
3. Take a Time Travel bookmark and a backup, then list and apply migrations:

   ```sh
   npx wrangler d1 migrations list <d1-database> --remote
   npx wrangler d1 migrations apply <d1-database> --remote
   ```

   Read the migration notes in the long reference first. One migration empties a
   table that holds a daily pseudonymous code, so apply it right after 00:05 UTC.
4. Deploy the Worker with the same configuration as staging, smoke the route,
   then clear the switches:

   ```sh
   npx wrangler deploy --var CLICKS_KILL:0 --var VIEWS_KILL:0 --var SCHEDULED_KILL:0
   ```

   Leave all three unset in the committed `wrangler.toml`. Retention, purges and
   rollups catch up on the next hourly run.

The reusable Worker deploy does this with a `quiet: true` call and a normal call
(see "Deploying the Worker from an instance repo" in
[docs/deploy.md](../../deploy.md)). It does not turn on `KIT_KILL` for you.

## The DNS change

Associate `<prod-host>` with the Pages project **first** (Pages project > Custom
domains > Set up a custom domain). **Verified**, Cloudflare Pages Custom domains
page, read 2026-11-19: a manual CNAME to `<pages-project>.pages.dev` without that
association fails to resolve and returns a 522. A subdomain does not need to be a
Cloudflare zone; an apex custom domain must be a zone in the same account.

Before:

```text
<prod-host>.   300   IN   CNAME   <old-target>.
```

After:

```text
<prod-host>.   300   IN   CNAME   <pages-project>.pages.dev.
```

- Edit the **existing** record; do not add a second one. **Inferred, not tested:**
  how the dashboard behaves when a record with that name already points
  elsewhere. The rehearsal below must include a pre-existing record to settle it.
- **Proxy status.** Keep whatever the record has now unless you decide otherwise.
  The Pages certificate and the `/e/*` Worker route both work proxied on a
  Cloudflare zone (**inferred**). Write down the current value.
- **Leave the old service's other records alone** (wildcard, certificate
  validation). Remove them 7 days after the switch. Removing them early can break
  the fallback.
- **The old service keeps its custom-domain setting** for the whole fallback
  window, so restoring the record brings it back at once.
- Write down the current record (type, name, content, TTL, proxy status and every
  other record under that name) in your private cutover record before you change
  anything.

### Lower the TTL in advance

At least 48 hours before the switch, set the production record's TTL to 300
seconds and change nothing else. Verify it after one old TTL has passed, from two
resolvers:

```sh
dig +noall +answer <prod-host> @1.1.1.1
dig +noall +answer <prod-host> @8.8.8.8
```

The second column counts down from 300 once caches refresh. Do not switch until
both show 300 or less on a fresh query (query twice, a minute apart).

### The switch, in order

1. Everything above is checked, and the old service is up (open it in a browser).
2. You decide to switch. Note the time `T0`, then edit the one record.
3. Verify propagation:

   ```sh
   for r in 1.1.1.1 8.8.8.8 9.9.9.9; do echo "== $r"; dig +noall +answer <prod-host> @$r; done
   curl -sI https://<prod-host>/ | head -n 12
   bash scripts/smoke.sh https://<prod-host>
   ```

   With the record proxied, `dig` shows proxy addresses, not the Pages target, so
   the HTTP check is the proof: the response carries a Content-Security-Policy and
   `X-Content-Type-Options: nosniff`, and the page carries your page's title.
4. Check the old-path redirect and a route:

   ```sh
   curl -sI "https://<prod-host>/socials?x=1" | grep -i -e '^HTTP' -e '^location'
   curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<prod-host>/e/c/unknown
   ```

   Expect your redirect status with a `Location` ending at `/` and the query
   string kept, and `204` from the route.
5. On a phone, on mobile data (not your home network, not a VPN), open the
   production host in a fresh private tab, open a tab, tap an outbound link and,
   if you use it, the media kit button. Open an old shared link; it should land on
   the home page. On a laptop, check a private window and a normal one, with no
   certificate warning. If the old page still shows after 10 minutes, flush the OS
   resolver cache and try the phone on mobile data before concluding anything.
6. Set `cutover_date` in `site.yaml` to the UTC day the switch was verified end to
   end (the UTC day, not the local one), rebuild and deploy:

   ```yaml
   cutover_date: 2026-01-15
   ```

   `reach-around check --config site.yaml --production` then has no unset warning,
   and the media kit prints "Live since <date> (UTC)". The production build uses
   a `301` redirect (the default); it was tested as `302` on staging because a
   `301` can stay cached in browsers.

## Rollback

### When to roll back

Roll back to the old service if the host does not answer, or answers with a
certificate error, for more than 10 minutes after propagation should have
finished; if the link page differs in a way that costs visitors and cannot be
fixed by a quick deploy; or if you decide to.

If only analytics or the Worker misbehaves, **do not roll back DNS**. Use the kill
switches and leave the static page up:

```sh
npx wrangler deploy --var CLICKS_KILL:1
npx wrangler deploy --var VIEWS_KILL:1
```

A bad site deploy is rolled back from the Pages deployments list (Rollback to this
deployment) or by re-running the deploy from the previous good commit. A bad
Worker is rolled back with `wrangler rollback`. A bad migration is not undone by a
redeploy: restore from the Time Travel bookmark and backup
([docs/backup-restore.md](../../backup-restore.md)).

### The DNS rollback

1. Decide to roll back. Note the time `R0`.
2. Edit the one record back to the exact value you wrote down (type, content, TTL
   300, proxy status as it was).
3. Leave the Pages custom domain attached for now. Removing it is not part of the
   rollback.

Resolvers that cached the new answer keep it for at most the TTL, so expect about
5 minutes plus a minute or two when the TTL was lowered in advance. Browsers,
operating systems and some mobile carriers can hold an answer longer; plan for up
to 15 minutes. If the TTL was **not** lowered, the time is the old TTL, which can
be hours, which is why the switch does not go ahead without lowering it.

Know it worked:

```sh
for r in 1.1.1.1 8.8.8.8 9.9.9.9; do echo "== $r"; dig +noall +answer <prod-host> @$r; done
curl -s https://<prod-host>/ | grep -i -c '<a-word-only-the-old-page-has>'
```

The record shows the old content on all three resolvers and the old page is back,
checked on a phone on mobile data. Leads collected during the window are kept and
exportable; rolling back deletes nothing.

After a rollback, keep the kill switches on so analytics do not count a dead path,
open an issue for the cause (keep exploit detail out of public issues), fix
forward on a new release candidate and restart the 48 hour soak.

## Rehearse the rollback first

Prove the whole switch-and-back cycle on a **throwaway hostname** before the real
switch, and record how long each step takes. Set up:

- A throwaway host such as `rehearsal.<your-domain>` with a **pre-existing
  record** pointing at a placeholder old target, so the edit-in-place behavior
  above is exercised.
- Two tiny Pages pages that differ by one visible marker word: the old target
  serves `OLD`, the new one serves `NEW`.
- TTL 300 seconds on the throwaway record, run from your laptop on its normal
  network.

The script below writes no DNS. You edit the record at each prompt; it timestamps
the edit and polls, bounded, until the page shows the expected marker. Save it as
`rehearse.sh` and run `HOST=rehearsal.<your-domain> bash rehearse.sh`.

```sh
#!/usr/bin/env bash
# Rollback rehearsal. Writes no DNS: every record edit is done by you.
set -euo pipefail

HOST="${HOST:?set HOST to the throwaway hostname}"
OLD_MARK="${OLD_MARK:-OLD}"
NEW_MARK="${NEW_MARK:-NEW}"
INTERVAL="${INTERVAL:-10}"      # seconds between polls
MAX_WAIT="${MAX_WAIT:-1800}"    # give up after this many seconds (bounded)
RESOLVERS=(1.1.1.1 8.8.8.8 9.9.9.9)

now() { date +%s; }

page_has() {
  curl -fsS --max-time 10 "https://${HOST}/" 2>/dev/null | grep -q -- "$1"
}

resolvers_show() {
  local r
  for r in "${RESOLVERS[@]}"; do
    printf '%s: ' "$r"
    dig +short +time=3 +tries=1 "$HOST" @"$r" | tr '\n' ' '
    echo
  done
}

wait_for() {
  local mark="$1" start="$2" t
  while :; do
    t=$(( $(now) - start ))
    if page_has "$mark"; then echo "$t"; return 0; fi
    if (( t >= MAX_WAIT )); then echo "TIMEOUT"; return 1; fi
    sleep "$INTERVAL"
  done
}

echo "Step 0: baseline. The page must show ${OLD_MARK}."
resolvers_show
page_has "$OLD_MARK" || { echo "baseline is not ${OLD_MARK}; stop"; exit 1; }

read -r -p "Step 1: edit the record to the NEW target, then press Enter. " _
T0=$(now)
SWITCH_SECONDS=$(wait_for "$NEW_MARK" "$T0") || true
echo "switch visible after: ${SWITCH_SECONDS}s"
resolvers_show

read -r -p "Step 2: check your phone on mobile data shows ${NEW_MARK}, then press Enter. " _
PHONE_SWITCH=$(( $(now) - T0 ))

read -r -p "Step 3: edit the record BACK to the OLD target, then press Enter. " _
R0=$(now)
ROLLBACK_SECONDS=$(wait_for "$OLD_MARK" "$R0") || true
echo "rollback visible after: ${ROLLBACK_SECONDS}s"
resolvers_show

read -r -p "Step 4: check your phone on mobile data shows ${OLD_MARK}, then press Enter. " _
PHONE_ROLLBACK=$(( $(now) - R0 ))

echo "switch visible on laptop: ${SWITCH_SECONDS}s; on phone: ${PHONE_SWITCH}s"
echo "rollback visible on laptop: ${ROLLBACK_SECONDS}s; on phone: ${PHONE_ROLLBACK}s"
echo "TTL used: 300s"
```

Pass criteria: the switch and the rollback both show on the laptop within about
600 seconds and on the phone within about 900 seconds, the bounded wait never
times out, and the edit-in-place worked with the pre-existing record, including
the Pages custom-domain association. If a figure is worse, find out why before the
real switch. Record times only, no hostnames or addresses.

Afterward, restore or remove the throwaway record, remove the throwaway custom
domain from the Pages project (a dangling DNS record pointing at a removed
project is a takeover risk), and list the records for the throwaway host to
confirm none is left.

## First 48 hours

Check at the switch, then at 1, 6, 24 and 48 hours, read-only:

| Check | How | Expected |
|---|---|---|
| Site answers | `bash scripts/smoke.sh https://<prod-host>` | exit 0 |
| Health alerts | your alert channel | none, or understood |
| Rollup heartbeat | the `view_meta` query in the long reference | `rollup_err` is `none`; `rollup_last_ok` under 2 days old |
| Views and clicks against the old service | the same days on both sides, once a full UTC day has closed | pass, or a gap explained |
| Error rate | Worker observability and `npx wrangler tail` for `exceededCpu` and error 1102 | none; a handful of 404s is normal |
| D1 writes | rows written per day against the plan quota | under 80 percent |
| The old path | `/socials` returns the redirect | 301 to `/` |
| Cache | one hashed and one stable file's `Cache-Control` | immutable and revalidate |
| Counts | `reach-around counts stale --config site.yaml` | exit 0 |

A missing stretch is a gap, never a zero.

## The shutdown (day 30 or later)

The shutdown needs **both**: at least 30 days since the switch **and** Reach
Around matching the old service. Check, with evidence:

| Area | Check |
|---|---|
| Links | `reach-around parity --site out --expected old-links.csv` exits 0 against a fresh export; every URL answers |
| Analytics history | The old service's history is imported and verified, shown in its own labeled block, never summed into collected numbers |
| Analytics now | The reconciliation over the 30 UTC days since the switch exits 0 |
| Social counts | Each platform number has an `as_of` within 30 days and matches the platform dashboard by eye |
| Media kit | If you use it, it shows real generated data and equals what the old service offered, compared side by side on desktop and phone |
| Forms and leads | Every form is rebuilt or deliberately dropped; any leads are exported to your private repository |
| QR codes | Every QR code that points at the old page resolves to the new page; none is a shortener of the old host |
| Redirects | The old path redirects with the query string kept; other shared old paths answer or redirect |
| Privacy and safety | The privacy page lists every feature that is on; the dump check passes on the production host; the kill switches were tested on staging |
| Cost | The D1 write quota and plan figure are measured over the 30 days and your plan decision is recorded |

Export before cancelling, all to your private instance repository (never to a
public repository, an issue or a chat): a fresh all-time analytics export, the
links list and social icon targets, every lead or subscriber the old service
holds, the kit content, and screenshots of the page on desktop and phone. Check
each file opens and has the expected row count; record counts only.

Then, in order: confirm the table above is done, remove the custom domain from the
old service only after DNS no longer needs it as a fallback, cancel the
subscription and keep the confirmation privately, and 7 days later remove the old
provider's wildcard and certificate-validation records and revoke any write scopes
granted for the cutover. Cancelling earlier removes the fallback.

Keep one private cutover record with the time, who did it and the result line for
each step. Public issues get counts, exit codes and times only.

## Next

[Updating](06-updating.md).
