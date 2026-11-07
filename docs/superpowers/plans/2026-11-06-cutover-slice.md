# Reach Around Cutover Slice: Freezing v1 for Replacing Beacons

> Status: planning record for issue #48 (epic #46), written 2026-11-06 against `origin/main` at `5ec0b3c`. It narrows `2026-10-23-plan-3-media-kit-and-gate.md` (Plan 3) and does not replace it: where this file is silent, Plan 3 stands; where they disagree, this file wins for v1. Hour figures are guesses (G) for agent work plus owner oversight.

**Goal:** Replace Beacons for the link page, the media kit and its gate, and basic analytics, in the smallest slice that is safe to put on the public internet. Everything else waits for v1.1 or later.

## 1. v1 scope and non-goals

**In v1**

- The link page at parity with Beacons (parity doc, #22), served as a static site from a spare staging host first, then the production host.
- Click and page-view collection on the existing Worker, enabled on the test site first (#185), with a minimal report command (`reach-around report`) showing funnel, geography and bot share.
- A blog collector (see rulings), and typed numbers with an `as_of` date for platforms that have no API path.
- The media kit: a static public half, plus a gated page served by the Worker to a visitor who passes Turnstile and types an address.
- Owner alerts when the kit is viewed, to Discord or Matrix first, email later.
- A privacy page and threat-model update that describe exactly what is collected and what the gate does not prove.
- A kill switch, request and budget limits, bounded lead retention, an owner delete command, and a private PDF rendered locally from a scrubbed copy.
- A tagged `v0.3.0`.

**Explicit non-goals for v1**

- The emailed unlock link and everything that depends on it (Plan 3 T9, T14, and the redemption half of T15).
- Lead purge across exports and backups (T21), bounded structured logs as a standalone task (T18).
- Network-type analytics (VPN, hosting and similar classes): the report ships without it.
- Plan 2 collectors and dashboard beyond the blog collector and the manual numbers path; Plan 2b; Phase 4 and 5 (Grafana, executive summary, Social Blade, StreamElements, Instagram, TikTok, Threads and X collectors, YouTube OAuth).
- Zoho, rate guidance, monetization tracking, editor follow-ups, network-namespace isolation for the PDF renderer as a requirement.

## 2. Owner rulings (recorded 2026-11-06)

| Topic | Ruling |
|---|---|
| Capacity | About 15 to 20 h/week of owner oversight, not 30. Plan 3's fallback cuts apply. Never cut: the cyber-tier review and the kill switch. Cutover target is mid-December 2026 (G). |
| Blog collector | Required for cutover (no-php-no-cry#84). It adds roughly 15 to 20 h and is scheduled in wave 1. |
| Unlock link | No verified (emailed) unlock link in v1. The gate takes an address and a Turnstile pass. A verified link is v1.1. |
| Alerts | Kit-viewed alerts go to Discord or Matrix first; Mailgun email follows as soon as the sending domain is ready and does not block cutover. |
| Analytics report | Ships without network-type. |
| Hosting plan | Cloudflare free Workers plan. An outage when a limit is hit is accepted over any bill. No paid features are enabled without a new ruling. |
| Manual numbers | For platforms with no API path (Kick, TikTok, Instagram, X, Threads, LinkedIn as the plan record lists them), the owner types the value with an `as_of` date. The report warns on values older than 30 days. |
| Beacons | Stays live for 30 days after cutover. |

**Residual risk of the unverified gate (stated, not hidden).** The v1 gate proves that a client passed a bot challenge and typed an address. It does not prove the person owns that address. Consequences: the lead list contains unverified addresses and must be treated that way; anyone can view the kit with any address; a copied unlock cookie works until it expires and misattributes views. So the gated page may contain only material the owner is willing to show to anyone who completes the challenge. The emailed link (v1.1) is the control that closes this. The privacy page and the threat model record the residual; the threat-model entries remain in `docs/security/threat-model.md`, and hardening details are tracked privately.

## 3. Defaults for Plan 3 decisions D1 to D6

All rows are **proposed, owner to confirm** unless a ruling above covers them. Values map to the Plan 3 field names.

| Decision | Proposed default for v1 | Notes |
|---|---|---|
| D1 link binding | Not applicable in v1 (no emailed link). When the link returns in v1.1, propose `cookie_pair`. | The build must not fail on an unset `kit.link_binding` while the link is off; Task 1 gets a `kit.unlock_link: false` switch that makes the field optional. Proposed, owner to confirm. |
| D2 stolen cookie | `kit.country_change: alert` (one alert per 6 hours, no friction). Cookie lifetime 24 h (`kit.token_ttl_hours: 24`). | `revoke` adds friction for roaming visitors and buys little while the gate is unverified. Proposed, owner to confirm. |
| D3 cost limits | Route budgets per hour: gate 150, view 300 (send and link routes do not exist in v1). `MAX_LEADS` 5000; at the cap new addresses are refused and the owner is alerted once a day. Kill switch answers with the 503 page. | Sized well inside the free plan; revisit after 48 h of real traffic. Proposed, owner to confirm. |
| D4 retention | Unverified leads 30 days (`unverified_lead_retention_days: 30`); hard maximum 365 days from creation; engine ceiling 730. | Every v1 lead is unverified, so the Worker holds each for 30 days; the daily pull to the local store keeps the follow-up list. Proposed, owner to confirm. |
| D5 backups and exports | No lead tables in any backup or export in v1; the local store is the only copy and `privacy.lead_artifacts` stays empty. | This removes the need for T21. Revisit if an export is added. Proposed, owner to confirm. |
| D6 PDF isolation | `--isolation auto`; output inside any git work tree is refused outright. | `require` stays available. Isolation is deferred as a hard requirement. Proposed, owner to confirm. |

**Report read path (still open).** Plan 2's O-1 default is a read-only D1 API token (Plan 2 Task 16S spike, in wave 0). Proposed: decide after the spike; if a token cannot be limited to read access on D1, the owner exports the click and page-view aggregates by hand and the report reads the file. Owner to confirm.

## 4. Tasks per wave

Task ids are Plan 3's (T1 to T21). Hours are Plan 3's revision-2 figures; "cut" means not in v1. Items without a Plan 3 id are tracked by their issue.

**Wave 0 (about 19 h): decide and de-risk**

- This document and the defaults above; parity doc (#22); the Cloudflare spike for analytics (#101); Plan 2 Task 16S read-token spike; the remainder of the deploy workflow hardening (#206, security-relevant).

**Wave 1 (about 85 h): Worker core, builder, analytics on**

| Track | Tasks |
|---|---|
| A: Worker kit core | T3 environment and token (4), T4 lead storage and events (4), T5 input validation and Turnstile (4), T6 owner alerts (4; Discord and Matrix sinks only), T7 limits and send budget (4; the send-budget half is cut with the send route), T16 body cap and kill switch (4), T17 route budgets and storage caps (5; gate and view routes only) |
| B: Python | T1 kit models and display policy (4), T2 kit builder (8), T2A gate-page CSP (2); manual numbers path for the platforms without an API; design pass |
| C: analytics | Enable page views on links-test (#185); minimal A13 from the analytics design; `reach-around report`; blog collector |

**Wave 2 (about 40 h): gate, retention, PDF, deploy**

- T8 gate and gated-content serving (7; only `POST /k/gate` and `GET /k/view`; the send and link routes are not built).
- T10 privacy disclosure and deploy wiring (5; drops the Mailgun, link and log wiring of the cut tasks).
- T20 lead retention in the Worker (4).
- T11 owner lead deletion command (4).
- T12 private PDF renderer (7) and T19 staged copy, scrub, temp directory, explicit output (7).
- T15 trimmed to the country-change alert on `GET /k/view` (about 1 of 3 h); the redemption alert is cut.
- Kit content into the private instance (content, not a Plan 3 task).

**Wave 3 (about 29 h): prove and ship**

- T13 abuse suite, mutation check and threat-model update (9), run last as Plan 3 requires.
- Cyber-tier review of Worker, gate and PDF (section 6).
- Post-deploy smoke test and the no-raw-IP dump test; production staging host; parity checklist run with Beacons live; `v0.3.0`.

**Cut from v1:** T9 emailed link (7 h), T14 link binding (6 h), T15 redemption alert (2 h of 3), T18 bounded logs as its own task (3 h; the no-raw-address-in-logs rule is still enforced by T13's tests), T21 purge across artifacts (5 h). Total cut 23 h; Plan 3's 110 h becomes about 87 h (G) for the kit, before the analytics, blog collector and parity work in the waves above.

**Follow-up, not gating:** T6b Mailgun alert sink (about 3 h, G), after the sending subdomain has SPF, DKIM and DMARC in place.

**Definition of done.** Parity doc green and the page equal to Beacons by a scripted link check; the gate works on desktop and phone with the alert arriving in under a minute, and the delete command works; the PDF is complete, paginated, readable offline, scrubbed and reviewed by the owner; 48 h of click and page-view data with a report showing funnel, geography and bot share, and a dump that contains no address or user agent; the kill switch tested; review findings closed; privacy page approved; `v0.3.0` tagged.

**Rollback.** Stage on a spare hostname. Cutover is one DNS change; the previous record and its TTL are written down first, and the TTL is lowered to 300 s two days before. Beacons stays live 30 days. Flip `KIT_KILL`, `CLICKS_KILL` and `VIEWS_KILL` to stop the Worker routes at once.

## 5. Interface notes: where Plan 3 predates main

1. **Migration numbering.** Main holds `0001` (clicks) and `0005` to `0008` (page views, rollups, rollup max id, sites). Plan 3 expects `0002_kit.sql`. **Rule:** a new migration takes the next number above the highest one on main at the time it is written (today `0009`). Numbers are never reused or inserted below an applied one, and gaps stay gaps. Plan 3's "unreleased, so edit in place" applies only until the file merges to main; after that, every change is a new numbered file. Wherever Plan 3 or its task text says `0002`, read "next free number"; the kit tables become `0009_kit.sql` unless another migration lands first.
2. **`KitEnv`.** Plan 3 Task 3 has `KitEnv extends Env`. On main, `Env` is declared in `worker/src/click.ts` and already carries `DB`, `CLICK_LIMITER`, optional `SITES`, `SITE_HOST`, `LINK_IDS`, `PAGE_IDS`, `VIEW_HASH_SECRET`, `CLICKS_KILL` and `CLICKS_KILL_SITES`. `SITE_HOST` is optional on main (multi-site `SITES` is the primary form), so the kit must resolve its one origin explicitly and fail closed when it cannot. `KitEnv` therefore adds only what is new: `KIT` (KV), `GATE_LIMITER`, `KIT_KILL`, `TOKEN_HMAC_KEY`, `TURNSTILE_SECRET`, the alert sink variables and the budget variables. Do not redeclare `DB` or `SITE_HOST`.
3. **Name collision.** `click.ts` exports a constant `MAX_BODY_BYTES = 1024` for the click route. Plan 3 uses the same name for the kit's variable (4096). The kit's cap is named `KIT_MAX_BODY_BYTES`.
4. **Kill switches.** Main has `CLICKS_KILL`, `VIEWS_KILL` and per-site lists. `KIT_KILL` is a separate switch with the same truthy rule ("1" or "true", any case, read on every request); the runbook flips all of them.
5. **`wrangler.toml` bindings.** Main declares `DB`, the `CLICK_LIMITER` rate-limit binding (placeholder namespace 1001), a commented `VIEW_LIMITER` (1002) and one hourly cron (`5 * * * *`). Add a `KIT` KV binding and a `GATE_LIMITER` binding (next free placeholder namespace id). `SEND_LIMITER` is not added in v1. Placeholders only in the repository; real ids stay out of git.
6. **Scheduled handler.** Lead retention (T20) runs inside the existing hourly run and the existing per-run D1 query budget (40 by default; the free plan allows 50). No second cron trigger is added.
7. **Route table.** For v1, the table in Plan 3 "Contracts shared by all tasks" shrinks to `POST /k/gate`, `GET /k/view`, a 404 for anything else under `/k/`, the route-budget and kill-switch rows. The guard order, the `Cache-Control: private, no-store` rule, the cookie format and the token format are unchanged; `cc` in the token still feeds the country-change alert.

## 6. Security review gate

The cyber-tier adversarial review runs on the tier the owner is individually approved for, never on a default model. A review cut off by a content filter means the tier was not set; fix the tier and rerun.

| Gate | What must pass | When |
|---|---|---|
| Per-PR, before merge | T3 token and cookie, T5 input and Turnstile, T8 gate and serving, T16 kill switch and body cap, T17 budgets, T19 PDF pipeline (the file-handling controls), and the #206 deploy-workflow changes | Each PR carries the review result or a link to it before merge. |
| Whole-slice, before `v0.3.0` | Worker (all `/k/` routes, retention, delete), gate page, and the PDF chain end to end | Wave 3, after T13. |
| Self-review plus tests | T1, T2, T2A, T4, T6, T7, T11, T12, T15 (trimmed), T20; analytics and report changes | Normal review; the T13 abuse suite covers them, and any finding that touches a control moves the task into the per-PR set. |

Rules: findings of Critical or High severity are fixed before merge. Each control keeps its `// CONTROL:` marker and a test that goes red when the control is removed. Details of findings and hardening work are tracked privately and are not written into this repository until fixed.

## 7. Risks to watch

Owner capacity; free-plan limits that are not yet measured under load; the account-wide deploy token (#206); the unverified gate; manual numbers going stale; PDF page-break iteration time; DNS and mail-domain propagation delays; and plans written against code about two weeks older than main (section 5 lists the known differences, and every task starts by re-reading the files it touches).
