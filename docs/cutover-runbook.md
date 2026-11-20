<!-- SPDX-License-Identifier: Apache-2.0 -->
# Cutover runbook: from Beacons to Reach Around

Issue #597 (rollback rehearsal), under #344 (the switch) and epic #246. This is
the one document for the whole cutover, from the frozen release candidate to the
day Beacons is cancelled. It holds placeholders only: `example.com` for the
zone, `links.example.com` for the production host and
`links-staging.example.com` for the staging host. Real hostnames, account ids,
database ids, tokens, exports and the owner's private notes stay in the private
instance repository and the private tracker, never here.

How to read the marks: **verified** means read from a named source on the date
given; **inferred** means reasoned and not yet run, so rehearse it before the
real switch.

## The decisions this runbook carries out

1. **The switch and the shutdown are two separate steps.** The switch moves the
   production host from Beacons to Reach Around. The shutdown cancels Beacons.
   Beacons stays live, with nothing deleted on its side, as the fallback until
   Reach Around is 1:1 with it, **including the media kit with real generated
   data**, and for **at least 30 days**, whichever is later.
2. **The real site is built from the private instance repository**, never from
   the engine's demo (`examples/instance`). The engine is public and pinned; the
   instance is private and consumes the pin.
3. **Release path:** engine release candidate frozen and approved, engine
   released, the instance repository pins that release by commit and image
   digest, the instance deploys to its `staging` environment, and `production`
   is deployed only by a manual run behind the reviewer's approval.
4. **Every DNS write waits for the owner's explicit word in chat**, quoted with
   its time in the record of the step. No agent writes DNS on a general go-ahead.
5. **The kit gate is off at cutover** (#605, #263), the funnel `landings` and
   `hops` lists are off in the production config (#595), and the Workers paid
   plan is approved (#610).

## Who does what

Marks: **O** = the owner (credentials, an account, or the explicit word in
chat); **A** = the assistant (runs read-only checks, prepares and reviews, files
the results); **O+A** = the assistant prepares, the owner does the write.

| # | Step | Who | Needs |
|---|---|---|---|
| 1 | Approve the release candidate SHA (#595) | O | the owner's word in chat |
| 2 | Tag the engine release, verify signature, SBOM, provenance ([releasing.md](releasing.md)) | A | tag push is the maintainer's step, done once the owner approves |
| 3 | Open the instance PR that moves the three `engine.lock` lines | A | none |
| 4 | Merge the instance PR, deploy to staging | O+A | the owner's merge |
| 5 | Approve and run the production deploy (manual dispatch, then the reviewer click) | O | GitHub account, reviewer approval |
| 6 | Set Worker secrets (`VIEW_HASH_SECRET` and the others) | O | Cloudflare login or a short-lived token from the secret manager; values never in chat |
| 7 | Apply D1 migrations with the Worker quiet (all three switches on) | O+A | Cloudflare D1 write; a short-lived scoped token |
| 8 | Take the backup and the Time Travel bookmark | O+A | D1 read for the export, D1 edit only for a restore |
| 9 | Import the Beacons history (#376) | A | the private CSV, supplied by the owner |
| 10 | Export the Beacons data (links list, analytics CSV, forms and leads) | O | Beacons login |
| 11 | Lower the TTL to 300 seconds, at least 48 hours ahead | O | DNS write, explicit word |
| 12 | Run the rollback rehearsal (#597) | O+A | DNS write on a throwaway hostname, explicit word |
| 13 | The switch: change the production record | O | DNS write, explicit word, quoted |
| 14 | Verification commands and the phone and laptop check | A and O | none for A; the phone is the owner's |
| 15 | Rollback, if called | O | DNS write, explicit word |
| 16 | Day-one monitoring and the 48 hour report | A | read-only |
| 17 | Parity run at day 30 and the 1:1 sign-off | A prepares, O signs | the owner's word |
| 18 | Cancel Beacons (the shutdown) | O | Beacons login and billing |
| 19 | Remove the old provider's DNS records after 7 days, revoke cutover scopes (#346) | O | DNS write, explicit word; token revocation |

## Part 1. The release path

Everything here happens before any DNS change.

### 1.1 Freeze and approve the candidate (#595)

1. Record the release candidate SHA in #595. The cutover ships this frozen SHA,
   not the tip of `main`. Any code change after the freeze is a new candidate and
   restarts the 48 hour soak.
2. The security review rounds on the candidate are closed: no open Critical or
   High finding, the findings tracked privately are fixed or carded (#605, #271,
   #373). Reviews run on the owner's cyber-program tier.
3. The owner approves the SHA in chat. Quote the message and its time in #595.

### 1.2 Release the engine

Follow [releasing.md](releasing.md): `__version__`, `CHANGELOG.md`, merge with
`ci / CI green`, signed tag, watch the `Release` run, then verify the image from
outside (`cosign verify`, `cosign verify-attestation`, `gh attestation verify`).
Record the image digest the run prints. A bad release is never retagged; fix
forward with the next number.

### 1.3 Pin it in the instance repository

The instance repository pins the release in `engine.lock`, three lines that move
together: `ENGINE_IMAGE` (tag **and** digest), `ENGINE_REPO`, and `ENGINE_REF`
(the commit of the release tag). Renovate opens one grouped pull request; it can
also be done by hand ([instance-repos.md](instance-repos.md)). The pull
request's `Preview` run builds the real site with the new engine and comments the
result. Read the engine `CHANGELOG.md`, then merge once `CI green` is green.

### 1.4 Staging, then production

The instance repository has two GitHub environments, `staging` and `production`.

- A push to `main` deploys to **staging** (the host `links-staging.example.com`).
- **Production** is deployed only by a manual run of the `Deploy` workflow with
  `target: production`, and the job then waits for the `production` environment's
  required reviewer. Deployment branches are limited to `main`.
- The Cloudflare token comes from the secret manager over OIDC, one masked value
  handed to the publish step only. Use the dev configuration for staging; the
  production configuration is used only after the owner says in his own words
  that the project is live in production.

The Worker and the D1 migrations ship at the switch (#596), carrying the
configuration that was proven on staging. See 2.3 for the order.

## Part 2. Preconditions checklist

Every box must be checked, with the evidence in the cutover record (private) and
the result lines (no values) in #344. Do not start the DNS steps with a box open.

### 2.1 Release and soak

- [ ] The release candidate SHA is frozen and approved (1.1).
- [ ] **48 hour soak** on the **same frozen SHA**, running through the instance
      deploy path (the instance's staging deploy, not the engine's
      `deploy-test.yml`). The soak restarts if the candidate changes.
- [ ] During the soak: `bash scripts/smoke.sh https://links-staging.example.com`
      exits 0, and `python3 -I scripts/check-dump.py dump.sql` on a fresh export
      exits 0 (no address, no user agent, no unexpected column).
- [ ] 48 hour analytics reconciliation passes with Beacons live:
      `python3 -I scripts/check-analytics.py --beacons beacons-days.csv --ours ours-days.csv --since <day1> --until <day2>`
      exits 0 ([analytics-acceptance.md](analytics-acceptance.md)).
- [ ] The synthetic visit and click appear in the report (B3).
- [ ] Security review rounds closed on the final candidate, on the cyber tier,
      bounded to three rounds (#271, #373, #605, #610). Hardening detail stays
      in the private tracker.
- [ ] The privacy page is approved by the owner (#598 merged; the approval is the
      owner's word, quoted in #344).
- [ ] The dump check is re-run on the production host after the 48 hour window
      (#373).

### 2.2 Configuration

- [ ] **Kit gate off at cutover**: `features.kit_gate` is `false` in the
      production config, so no cookie is set and no cookie notice shows.
- [ ] The funnel `landings` and `hops` lists are **absent** from the production
      config (#595). Migration `0017` is applied later, with the Worker quiet (all three switches on, section 2.3).
- [ ] **Beacon token mode** is the mode chosen under #610. The public repository
      does not define it; the design and its owner decisions are in the private
      tracker. Record the mode name (not its detail) in the cutover record.
- [ ] `cutover_date` is **not** set yet. It is set on the day the DNS switch is
      verified end to end ([deploy.md](deploy.md), "Setting the cutover date").
- [ ] `redirects.socials` is `301` in the production build and was tested as
      `302` on staging (query string kept, other paths unaffected).
- [ ] `reach-around check --config site.yaml --production` has no warning other
      than the unset `cutover_date`.
- [ ] Link inventory parity against Beacons:
      `reach-around parity --site out --expected old-links.csv` exits 0 (no
      missing, extra, reordered or changed link). The export of the Beacons link
      list stays in the private instance repository. Then a scripted request of
      each external URL, and the affiliate flags (`rel` contains `sponsored`),
      which `parity` does not cover.
- [ ] Tier 1 follower numbers (Twitch, YouTube, TikTok, Threads, Instagram) show
      an `as_of` within 30 days and the owner has compared each with Beacons by
      eye (A2).

### 2.3 Cloudflare

- [ ] **Workers paid plan** is active (#610). This reconciles the Free-first
      decision in [deploy.md](deploy.md): the funnel caps and budgets are
      re-checked against the paid plan before the production config is frozen.
- [ ] Worker secrets are set, by name only, never in a file, variable or chat:
      `VIEW_HASH_SECRET` (32 or more random bytes, base64) and every other
      secret the production config needs (`TOKEN_HMAC_KEY` only if the gate is
      on, which it is not at cutover; alert channel credentials if alerts are on).
      Check by name: `npx wrangler secret list`.
- [ ] **D1 migrations applied with the Worker quiet.** "Quiet" means no
      request route and no scheduled job writes to D1. The two route switches
      alone do not do that: the hourly scheduled run (cron `5 * * * *`: lead
      retention, health, purges, rollups, funnel rollups, counter purges) keeps
      writing, so a third switch pauses it. Order, from the proven staging run:
      1. Quiet the Worker: deploy with all three switches on,
         `npx wrangler deploy --var CLICKS_KILL:1 --var VIEWS_KILL:1 --var SCHEDULED_KILL:1`.
         `SCHEDULED_KILL` makes every scheduled trigger (the hourly jobs and
         the Live poller) return at once with no D1 read or write
         ([deploy.md](deploy.md)). It does not stop a run that had already
         started when the deploy landed, so verify before migrating: wait for
         the deploy to land and for any run in progress to end (at least one
         full minute), then watch `npx wrangler tail` (or the Worker's logs in
         the Cloudflare dashboard) through the next scheduled trigger and
         confirm no scheduled invocation is active. Do not start migrations
         until that is confirmed. Apply them well clear of minute five of the
         hour, so the check sees a trigger fire and return.
      2. Take a Time Travel bookmark and a backup (2.4).
      3. `npx wrangler d1 migrations list <DATABASE> --remote`, then apply.
         Migrations are forward only. Migration `0008` empties `visitor_seen`:
         apply it right after 00:05 UTC. Migration `0016` goes before the Worker
         that writes the field. Read the migration notes in
         [deploy.md](deploy.md) first.
      4. Deploy the Worker (same configuration as staging).
      5. Smoke the route, then clear the switches once every migration has
         completed: `--var CLICKS_KILL:0 --var VIEWS_KILL:0 --var SCHEDULED_KILL:0`.
         Leave all three unset in the committed `wrangler.toml`. Retention,
         purges and rollups catch up on the next hourly run.
- [ ] The `/e/*` route exists on the production host (zone route, or by hand,
      [deploy.md](deploy.md), "Adding the route by hand"). Without it clicks are
      not counted.
- [ ] **Edge rules**: Browser Cache TTL is "Respect Existing Headers"; no zone
      rule rewrites `/` or `/socials`; the Pages `*.pages.dev` hostname and the
      Worker `workers.dev` hostname are handled as in the threat model (T-13);
      Pages production branch is `main`.
- [ ] Health alerts: the `analytics_health` alert is on and a test alert reached
      the owner's device ([alerts.md](alerts.md)). `QUERY_BUDGET` is 12 or more.
- [ ] The write budget check printed under 80 percent of the plan quota.

### 2.4 Backups and history

- [ ] A Time Travel bookmark is saved and a backup taken
      (`scripts/backup-d1.sh <DATABASE> <BACKUPS> --apply`), then a restore
      drill on a scratch database passed ([backup-restore.md](backup-restore.md)).
- [ ] The Beacons history import ran: `reach-around counts history-import-beacons`
      with `--dry-run`, then for real into its own file, then
      `reach-around counts history-verify --file beacons-pre-cutover-history.jsonl`.
      The record count is twice the daily-row count
      ([beacons-history-import.md](beacons-history-import.md)). The original CSV
      is in the private instance repository.
- [ ] The full Beacons export (step 10 in the who-does-what table) is in the private instance
      repository before the switch, as a second copy of what week 1 requires (D1).

### 2.5 DNS inputs

- [ ] The current record at the production host is written down in the private
      cutover record: type, name, content, TTL, proxy status, and every other
      record under that name (wildcard, certificate-validation records; #346).
- [ ] The Pages project has `links.example.com` in its **Custom domains**, and
      the status is Active (see 3.1 for the order and why).
- [ ] TTL is already 300 seconds, set at least 48 hours ahead (3.2).

## Part 3. The DNS plan

DNS is a Cloudflare write or a write at whichever provider holds the zone. The
assistant never writes it. The owner writes it on the explicit word, and the
assistant verifies.

### 3.1 The record change, generically

Before: the production host points at Beacons.

```
links.example.com.   300   IN   CNAME   <old-provider-target>.
```

After: it points at the Pages project.

```
links.example.com.   300   IN   CNAME   <pages-project>.pages.dev.
```

Notes:

- A subdomain needs only the CNAME. If the host were the zone apex the zone would
  need to be on Cloudflare (CNAME flattening). Verified, Cloudflare Pages
  "Custom domains" page, read 2026-11-19: a subdomain does not need the site to be
  a Cloudflare zone; an apex custom domain must be a zone in the same account.
- **Associate the domain with the Pages project first** (Pages project, Custom
  domains, Set up a custom domain). Verified, same page: a manual CNAME to
  `<project>.pages.dev` without first going through that process fails to resolve
  and returns a 522. When the zone is on Cloudflare the CNAME is then added
  automatically after confirming; here an old record already exists at that
  name, so the owner edits the existing record, not adds a second one.
  **Inferred, not tested**: how the dashboard behaves when a record with the
  same name already points elsewhere. The rehearsal (Part 5) must include a
  pre-existing record to settle this.
- **Proxy status.** Keep whatever the record has now unless the owner decides
  otherwise; the Pages certificate and the `/e/*` Worker route both work with the
  proxied setting on a Cloudflare zone (inferred). Write down the current value.
- **Leave the old provider's other records alone** (wildcard,
  certificate-validation). They are removed 7 days after the switch (#346).
  Removing them early can break the fallback.
- **The old provider keeps its custom-domain setting** for the whole fallback
  window, so restoring the record brings Beacons back at once.

### 3.2 Lower the TTL in advance

At least 48 hours before the switch, with the owner's word, set the TTL of the
production record to 300 seconds. Change nothing else. Verify it later than one
old TTL after the change (the old TTL may be hours), from two resolvers:

```sh
dig +noall +answer links.example.com @1.1.1.1
dig +noall +answer links.example.com @8.8.8.8
```

The second column is the remaining TTL and counts down from 300 once caches
refresh. Do not switch until both show 300 or less as the maximum on a fresh
query (query twice, a minute apart, and compare).

### 3.3 The switch, in order

1. All of Part 2 is checked. Beacons is up (open it in a browser).
2. The owner says "switch the record" in chat. Note the time. This is the word.
3. The owner edits the one record to the new content (3.1). Note the time `T0`.
4. Verify propagation:

   ```sh
   for r in 1.1.1.1 8.8.8.8 9.9.9.9; do
     echo "== $r"; dig +noall +answer links.example.com @$r
   done
   curl -sI https://links.example.com/ | head -n 12
   bash scripts/smoke.sh https://links.example.com
   ```

   With the record proxied, `dig` shows proxy addresses, not the Pages target, so
   the HTTP check is the proof: the response carries the Reach Around headers
   (`Content-Security-Policy`, `X-Content-Type-Options: nosniff`) and the page
   carries the Reach Around title, where Beacons does not.
5. Check the redirect and routes:

   ```sh
   curl -sI "https://links.example.com/socials?x=1" | grep -i -e '^HTTP' -e '^location'
   curl -s -o /dev/null -w '%{http_code}\n' -X POST https://links.example.com/e/c/unknown   # expect 204
   ```

6. Real devices: see 3.4. Then the owner sets `cutover_date` (the UTC day of the
   verified switch), rebuilds and deploys the instance through the same path, and
   `reach-around check --config site.yaml --production` has no unset warning.

### 3.4 What the owner checks on the phone and the laptop

- Phone, on mobile data (not the home network, not a VPN): open the production
  host in a fresh private tab. It shows the new page. Open one tab, tap one
  outbound link, tap the media kit button (the kit page opens, the gate is off).
- Phone: open an old shared link ending in `/socials`. It lands on the home page.
- Laptop, in a private window, and again in a normal window: the same page, no
  certificate warning (click the lock and check the host name only).
- Laptop: hard reload once. If the old page still shows after 10 minutes, flush
  the OS resolver cache and try the phone on mobile data before concluding
  anything.
- Tell the assistant the time of each check. A page that looks right on one
  device and wrong on another is a propagation or cache difference, not yet a
  rollback reason (see 4.1).

## Part 4. Rollback

### 4.1 When to roll back

Roll back to Beacons if any of these holds after the switch:

- the host does not answer, or answers with a certificate error, for more than
  10 minutes after propagation should have finished;
- the link page differs from Beacons in a way that costs visitors (a missing or
  wrong link that cannot be fixed by a quick deploy);
- the owner says to.

If only analytics or the Worker misbehaves, do **not** roll back DNS: use the
kill switches and leave the static page up
([operations.md](operations.md), "Roll back analytics"):

```sh
npx wrangler deploy --var CLICKS_KILL:1
npx wrangler deploy --var VIEWS_KILL:1
```

A bad site deploy is rolled back from the Pages deployments list (Rollback to
this deployment) or by re-running the instance deploy from the previous good
commit. A bad Worker is rolled back with `wrangler rollback`.

### 4.2 The DNS rollback

What the owner does:

1. Says "roll back" in chat (the explicit word). Note the time `R0`.
2. Edits the one record back to the exact value written down in 2.5 (type,
   content, TTL 300, proxy status as it was).
3. Leaves the Pages custom domain attached for now. Removing it is not part of
   the rollback.

How long it takes: resolvers that cached the new answer keep it for at most the
TTL, so **about 5 minutes (300 seconds) plus a minute or two**, when the TTL was
lowered in advance. Browsers, operating systems and some mobile carriers can hold
an answer longer; plan for up to 15 minutes and say so to the owner. The
rehearsal records the real figure (Part 5). If the TTL was **not** lowered, the
time is the old TTL, which can be hours: that is why the switch does not go ahead
without 3.2.

How to know it worked:

```sh
for r in 1.1.1.1 8.8.8.8 9.9.9.9; do
  echo "== $r"; dig +noall +answer links.example.com @$r
done
curl -s https://links.example.com/ | grep -i -c '<the Beacons page marker>'
```

The record shows the old content on all three resolvers and the page is
Beacons' page again, checked on the owner's phone on mobile data. Leads collected
during the window are kept and exportable; rolling back deletes nothing.

### 4.3 After a rollback

Keep the Worker's kill switches on so the analytics do not count a dead path.
Open an issue for the cause (private detail stays private), fix forward on a new
candidate, and restart the 48 hour soak. The switch is repeated only with a fresh
explicit word.

## Part 5. Rollback rehearsal script (#597)

Purpose: prove the whole switch-and-back cycle, on a **throwaway hostname**,
before the real switch, and record how long each step took. The record goes in
#597 as one comment, **times only**: no hostnames, no addresses, no values.

Setup, owner actions (the assistant prepares nothing in DNS):

- A throwaway host such as `rehearsal.example.com` in a zone the owner controls.
  It must carry a **pre-existing record** pointing at a placeholder old target,
  so the edit-in-place behavior (3.1) is exercised.
- Two tiny Pages pages that differ by one visible marker word each: the "old"
  target serves the word `OLD`, the "new" one serves `NEW`. The real staging
  project can serve as the new one.
- The TTL on the throwaway record is set to 300 seconds, as the real one will be,
  and the script is run from the owner's laptop on its normal network.

The script never writes DNS. It waits for the owner to do each edit, timestamps
it, and then polls (bounded) until the page shows the expected marker.

```sh
#!/usr/bin/env bash
# Rollback rehearsal for the Beacons cutover (issue #597).
# Usage: HOST=rehearsal.example.com ./rehearse.sh
# Writes no DNS. Every record edit is done by the owner; this only times it.
set -euo pipefail

HOST="${HOST:?set HOST to the throwaway hostname}"
OLD_MARK="${OLD_MARK:-OLD}"     # word the old target's page contains
NEW_MARK="${NEW_MARK:-NEW}"     # word the new target's page contains
INTERVAL="${INTERVAL:-10}"      # seconds between polls
MAX_WAIT="${MAX_WAIT:-1800}"    # give up after this many seconds (bounded)
RESOLVERS=(1.1.1.1 8.8.8.8 9.9.9.9)

now() { date +%s; }

page_has() {   # page_has MARK -> 0 if the page body holds the word
  curl -fsS --max-time 10 "https://${HOST}/" 2>/dev/null | grep -q -- "$1"
}

resolvers_show() {   # print one answer line per resolver (info only)
  local r
  for r in "${RESOLVERS[@]}"; do
    printf '%s: ' "$r"
    dig +short +time=3 +tries=1 "$HOST" @"$r" | tr '\n' ' '
    echo
  done
}

wait_for() {   # wait_for MARK START -> seconds from START until the page shows MARK
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
echo "TTL as the first resolver sees it (second column):"
dig +noall +answer "$HOST" @"${RESOLVERS[0]}" || true

echo
read -r -p "Step 1: owner edits the record to the NEW target, then presses Enter. " _
T0=$(now)
echo "switch edit at: $(date -u +%H:%M:%SZ)"
SWITCH_SECONDS=$(wait_for "$NEW_MARK" "$T0") || true
echo "switch visible after: ${SWITCH_SECONDS}s"
resolvers_show

echo
read -r -p "Step 2: check the owner's phone on mobile data shows ${NEW_MARK}, then press Enter. " _
PHONE_SWITCH=$(( $(now) - T0 ))
echo "phone confirmed ${PHONE_SWITCH}s after the edit"

echo
read -r -p "Step 3: owner edits the record BACK to the OLD target, then presses Enter. " _
R0=$(now)
echo "rollback edit at: $(date -u +%H:%M:%SZ)"
ROLLBACK_SECONDS=$(wait_for "$OLD_MARK" "$R0") || true
echo "rollback visible after: ${ROLLBACK_SECONDS}s"
resolvers_show

echo
read -r -p "Step 4: check the owner's phone on mobile data shows ${OLD_MARK}, then press Enter. " _
PHONE_ROLLBACK=$(( $(now) - R0 ))
echo "phone confirmed ${PHONE_ROLLBACK}s after the edit"

echo
echo "=== Record this in #597 (times only) ==="
echo "switch visible on laptop: ${SWITCH_SECONDS}s; on phone: ${PHONE_SWITCH}s"
echo "rollback visible on laptop: ${ROLLBACK_SECONDS}s; on phone: ${PHONE_ROLLBACK}s"
echo "TTL used: 300s"
```

Pass criteria: the switch and the rollback both show on the laptop within
about 600 seconds and on the phone within about 900 seconds; the bounded wait
never times out; the edit-in-place worked with the pre-existing record
(including the Pages custom-domain association, 3.1). If a figure is worse, the
comment says so and the real switch waits for an explanation. The comment in
#597 is the record, in this form (times only):

```
Rollback rehearsal, <UTC date>
TTL 300 s
Switch: laptop <n> s, phone <n> s
Rollback: laptop <n> s, phone <n> s
No timeout; edit in place worked: yes/no
```

After the rehearsal the owner restores or removes the throwaway record and
removes the throwaway custom domain from the Pages project (the dangling-DNS
risk in the threat model, T-13), and the assistant lists the records for the
throwaway host to confirm none is left.

## Part 6. Day-one monitoring

For the first 48 hours after the switch the assistant checks the list below at
the switch, at 1 hour, at 6 hours, at 24 hours and at 48 hours, read-only, and
posts one line each to #344 (counts and results, never values). The owner glances
at the alert channel as usual ([operations.md](operations.md)).

| Check | How | Expected |
|---|---|---|
| Site answers | `bash scripts/smoke.sh https://links.example.com` | exit 0 |
| Health alerts | the alert channel; `analytics_health` conditions ([alerts.md](alerts.md)) | none, or understood: `zero_views`, `rollup_stale`, `view_budget_hit`, `click_ceiling_reached` |
| Rollup heartbeat | the `view_meta` query in [deploy.md](deploy.md) | `rollup_err` is `none`; `rollup_last_ok` under 2 days old |
| Page views and clicks against Beacons | the same days on both sides; `check-analytics.py` with default tolerance once a full UTC day has closed | pass, or a gap explained by the expected-differences table |
| Error rate | Worker observability and `npx wrangler tail` for `exceededCpu` and error 1102; the 4xx and 5xx share in Cloudflare Analytics | none; a handful of 404s is normal |
| Click ceiling | `click_ceiling_reached` alert | not reached; if reached, record the day and decide on the ceiling, do not roll back |
| D1 writes | rows written per day against the plan quota | under 80 percent of the plan |
| The old path | `/socials` returns the redirect | 301 to `/` |
| Beacons | its own dashboard still counts visits | it will, until the TTL drains; the drop in Beacons visits is itself a signal that the switch is working |
| Cache | one hashed and one stable file `Cache-Control` ([deploy.md](deploy.md), "Caching and stale files") | immutable and revalidate |
| Counts | `reach-around counts stale --config site.yaml` | exit 0 |
| Owner tasks | social bios updated to the new host (#348) | done within the week |

A missing stretch is a gap, never a zero ([failure-modes.md](failure-modes.md)).

## Part 7. The 30 day parity and shutdown checklist

The shutdown is a separate decision from the switch. It needs **both**: at least
30 days since the switch, **and** Reach Around 1:1 with Beacons.

### 7.1 What 1:1 means

| Area | Check | Evidence |
|---|---|---|
| Links | `reach-around parity --site out --expected old-links.csv` exits 0 against a fresh Beacons export; every URL answers | exit code and counts |
| Analytics history | the Beacons `Daily` history is imported and verified (`history-verify`); the imported window is shown in its own labeled block, never summed into collected numbers (C3) | the success line, record count twice the daily rows |
| Analytics now | `check-analytics.py` over the 30 UTC days since the switch exits 0 at default tolerance (A3) | the table in the cutover record |
| Social counts | each Tier 1 platform number has an `as_of` within 30 days and matches Beacons and the platform dashboard by eye (A2) | the owner's word |
| Media kit | the kit shows **real generated data** (live numbers, the dated "Live since" line, the PDF generated from a real build) and equals what Beacons' kit offered; the owner compares them side by side on desktop and phone. This is the item that decides whether the kit gate has to be turned on before shutdown (see open questions) | the owner's word |
| Forms and leads | every form Beacons had is either rebuilt or deliberately dropped (the parity doc rules out forms beyond the kit gate); any leads Beacons collected are exported and in the private repository | the export |
| QR codes | every QR code that points at the old page still resolves to the new page (scan each on a phone); none is a shortener of the old host | the owner's phone |
| Redirects | `/socials` and `/socials/` redirect to `/` with the query string kept; any other old path people shared answers or redirects; the `301` is in place | `curl -sI` lines |
| Privacy and safety | the privacy page lists every feature that is on; the dump check passes on the production host; the kill switches were tested on staging | exit codes |
| Cost | the D1 write quota and the paid plan figure are measured over the 30 days and the owner's decision on the plan is recorded (#332) | the owner's word |

### 7.2 Export before cancelling (D1, D2)

All files go to the private instance repository, never to this repository, an
issue or a chat.

1. The all-time **Link in Bio analytics** CSV (the verified format is in
   [beacons-history-import.md](beacons-history-import.md)). Take a fresh one at
   day 30 as well as the earlier one.
2. The list of links and the social icon targets, as the `url,label` file used by
   `parity`.
3. Every lead, subscriber or form submission Beacons holds, and the kit content
   (partners, case studies, rates card) as the owner wrote it.
4. Screenshots of the page and the kit on desktop and phone, for the record.
5. Check each file opens and has the expected row counts; record counts only.

### 7.3 Cancellation steps (owner actions)

The steps in Beacons' own account screens are the owner's to read and follow; this
runbook does not describe them from memory. The order is:

1. The owner says "the 1:1 check is green, cancel" in chat (the word, quoted).
2. Confirm 7.1 and 7.2 are done and the day-30 table is in the cutover record (D3).
3. Remove the custom domain from the Beacons account only **after** the DNS no
   longer needs it as a fallback.
4. Cancel the Beacons subscription and keep the confirmation in the private
   record.
5. After 7 more days: remove the old provider's wildcard and certificate-validation
   DNS records, and revoke the write scopes granted for the cutover (#346).
6. Close #344 and #246's cutover items with the final evidence; move the board
   cards in the same response.

Cancelling earlier than this list is a rollback hazard: the fallback is gone.

## Part 8. The cutover record

Keep one private record (private instance repository) with, per step: the
time, who did it, the owner's quoted word where one is required, and the result
line. Public issues get counts, exit codes and times only. Template:

| Step | UTC time | Who | Word quoted (time) | Result |
|---|---|---|---|---|
| RC SHA approved | | O | | |
| Engine tagged, image verified | | A | | |
| TTL lowered to 300 s | | O | | |
| Rehearsal done (#597) | | O+A | | |
| Migrations applied, Worker quiet (three switches) | | O+A | | |
| Switch edit | | O | | |
| Verified on phone and laptop | | O+A | | |
| Day-one checks (1 h, 6 h, 24 h, 48 h) | | A | | |
| Day-30 parity run | | A | | |
| 1:1 sign-off | | O | | |
| Beacons cancelled | | O | | |
| Old records removed, scopes revoked (#346) | | O | | |
