# Reach Around Plan 3: Media Kit and Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tagged `v0.3.0` with a media kit whose public half is a static page, whose full content is served by the Worker only to a visitor holding a valid lead-bound cookie, with email capture, a single-use emailed link bound to the browser that asked for it (binding mode chosen by the maintainer, see Open decisions), owner alerts, abuse and cost controls that are each proven by a test, bounded lead retention with a purge command, and a private PDF rendered locally from a scrubbed copy.

**Architecture:** The Python engine renders two bundles from the instance config: the public site (Pages) and a gated bundle (one HTML page uploaded to Workers KV, never part of the Pages deploy). The existing write-only Worker gains four routes under `/k/`. A visitor passes Turnstile, a lead is stored in D1, and the Worker answers with a 303 and an HttpOnly cookie holding a short-lived HMAC token bound to the lead. Mailgun sends a fixed template carrying a single-use nonce link that is redeemable only with the binding the maintainer picked (a pending cookie, a code shown on the requesting page, or a short expiry with a redemption alert). Every `/k/` request passes a kill switch, a body cap and per-route budgets before it can cost a D1 write, a Turnstile call or an email. The owner deletes or purges leads (Worker, local store, registered exports and backups) and renders the full PDF from their own machine, from a staged copy, in an owner-only temp directory.

**Tech Stack:** Python 3.11+, pydantic v2, Jinja2, WeasyPrint and pypdf (optional `pdf` extra), pytest; TypeScript Cloudflare Worker, D1, KV, Workers rate-limiting binding, Web Crypto, Turnstile, Mailgun HTTP API, vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-reach-around-design.md` (sections 4.3 to 4.6, 4.11, 6 and the decisions table). Roadmap: `docs/superpowers/plans/2026-10-23-roadmap.md` (Phase 3 was sized at 68 h; revision 2 of this plan is 110 h, see Hours). Threat model: `docs/security/threat-model.md` (PR #54; T-01, T-03, T-04, T-12, T-14 land here). Builds on `2026-10-23-plan-1-foundation-and-link-page.md`: `SiteConfig`, `Features(kit_gate)`, `Privacy` (`lead_retention_days`), `collected_items`, `sets_cookies`, `BuildManifest`, the Worker router in `worker/src/index.ts` with its scheduled handler, `purgeOldEvents`, and D1 migration `0001`. Research notes that bear on the kit: `docs/research/youtube.md`, `linkedin.md`, `social-blade.md`.

## Global Constraints

Copied from Plan 1, then extended.

- Python 3.11 or newer; tests run on 3.11, 3.12 and 3.13.
- License Apache-2.0; every new source file starts with `# SPDX-License-Identifier: Apache-2.0` (TypeScript: `// SPDX-License-Identifier: Apache-2.0`).
- American English in code, docs and messages.
- The public site is static and works with no JavaScript. The only dynamic server code is the one write-only Worker. The Worker never returns visitor data (emails, clicks, leads). Its only content output is the gated kit page, after a valid pass.
- Do not store IP addresses. At most a two-letter country code from Cloudflare headers.
- No personal details of the maintainer (address, callsign, grid square, hostnames, machine identifiers, real emails) in the repository, examples, tests or logs. Examples use synthetic data (`example.com`, `203.0.113.x`, "Example Creator") and the placeholder callsign `N0CALL` if one is ever needed.
- No secrets in the repository. Credentials come from the environment or a secrets manager.
- CI calls git-your-ship-together reusable workflows pinned by commit with a version comment: `ChiefGyk3D/git-your-ship-together/.github/workflows/python-ci.yml@b9db99287236b19f0249f83b54acc5cc41126e42 # v1.25.0`. The gate job is named `CI green`.
- Every PR links its issue (`Refs #n` or `Closes #n`). Every task has an issue and a board card before work starts.
- Versions are semantic; alpha is `0.x`. This plan ends at `v0.3.0`.
- Containers build from a `Containerfile`, run rootless, depend on no Docker socket, and pin base images by digest. The PDF renderer is a local owner tool and is not part of the image.
- The README stays marked alpha.
- Time zone for anything date-based defaults to `America/New_York`. Worker day counters (send budget, address limit) use the UTC day, via `utcDay(nowMs)`; hourly route budgets use `utcHour(nowMs)` (Task 17).
- Privacy: the disclosure page and cookie notice are generated from enabled features (Plan 1 Task 6A) and must name everything Phase 3 collects.
- **Cookie-only unlock.** The unlock token travels only in an HttpOnly, Secure, SameSite cookie named `__Host-ra_kit`. It never appears in a URL, a query string, a redirect `Location`, a response body, an email, a log line or the build output. A token sent any other way (query, header, form field) is ignored. The pending-link cookie `__Host-ra_pend` (Task 14) is not an unlock token: it grants nothing on its own and `/k/view` ignores it. The emailed nonce and the on-page code are not unlock tokens either.
- **Token lifetime.** 24 hours by default (`TOKEN_TTL_SECONDS=86400`), configurable from 1 to 168 hours, fixed (no sliding renewal), bound to a lead that must still exist.
- **No personal data in logs.** No email address, IP address, token, nonce or code in any log line or error message. The only code that may call `console.*` is `worker/src/kit/log.ts` (Task 18), which emits closed-enum fields only. A static test fails on `console.` anywhere else in `worker/src`, and runtime tests spy on `console` and fail on a match.
- **Fail closed.** A missing secret, a Turnstile error or timeout, a missing gated bundle, or a signing key under 32 bytes means no unlock, never a weaker one.
- **Every control has a test that fails without it.** Each guard is one line ending in `// CONTROL:<id>` (ids in Task 13); the mutation check in Task 13 comments each one out and requires the abuse suite to go red.
- All D1 statements are parameterized; no SQL is built by string interpolation.
- **Link binding is explicit.** `kit.link_binding` has no default. With `send_link: true` and no value the build fails with a message pointing to Open decisions D1. A forwarded copy of the emailed link must never be redeemable by the first person who clicks it in the `cookie_pair` and `code` modes; the `expiry_alert` mode accepts that residual and says so in its docs.
- **Cheap checks first, and a refusal costs nothing.** On every `/k/` request the order is fixed (Contracts, "Guard order"). A request refused by the kill switch, method, origin, user agent, size cap or per-client limiter makes zero D1 writes, zero KV reads of the gated page, zero Turnstile calls and zero mail calls. Tests count statements.
- **Bounded growth.** Every table the Worker writes has an upper bound that does not depend on attacker effort: leads by `MAX_LEADS`, events by one view event per lead per hour, links by one per address per day, counters by a 2-day purge. Build refuses a configuration whose worst-case daily D1 writes exceed the configured quota (Task 17).
- **Retention is a hard maximum.** A lead is deleted when `now - created_ts >= lead_retention_days` (ceiling 730, default 365), whatever its activity, and an unverified lead when it reaches `unverified_lead_retention_days` (default 30). Deletion by the owner reaches the Worker, the local store, and every registered export and backup (Tasks 11 and 21).
- **The PDF renderer is sealed.** It makes no network request, reads only a staged copy with lead tables removed, scrubs metadata, works in an owner-only temp directory that is removed on every exit path, and writes only to an explicit output path outside the repository and every build output (Tasks 12 and 19).

## Review Focus

The five failure modes most likely to bite, most likely first. Each has a test in the task named. Revision 2 reordered this list after an independent threat-model review found that a single-use link does not make a link "not forwardable", and that cost, retention and the PDF pipeline had no controls.

1. **A forwarded or intercepted unlock link redeemed first.** Single use only means the first redeemer wins, and a forwarded copy, a mail scanner that submits forms, or a stolen cookie all defeat "not forwardable". The mode chosen in D1 must make a copy redeemed in a different browser land on the gate, and a redemption must alert the owner. Tests: Task 14 (`cookie_pair` and `code` forwarded-copy cases, attempt limit, pending cookie never unlocks), Task 15 (redemption and country-change alerts), AB-5 in Task 13.
2. **Cost exhaustion of the free tiers.** A body parsed before it is capped, a counter that itself burns a D1 write per refused request, a view flood that writes a row per view, a kill switch cached at module load, or a log line per hostile request. Tests: Task 16 (`body-cap`, `kill-switch`), Task 17 (`route-budget`, `lead-cap`, `view-debounce`, write-count bound), Task 18 (`log-cap`), AB-10 in Task 13.
3. **Retention that never ends, or deletion that misses a copy.** A sliding window that a returning lead renews forever, unverified addresses kept as long as verified ones, a Worker delete that leaves the local store, an export or a backup holding the address. Tests: Task 20 (`lead-hard-max`, `lead-unverified`, shared vectors), Task 21 (dry run equals execute, artifact scrub, partial-failure rerun), Task 11, AB-12 in Task 13.
4. **The private PDF leaking.** A network fetch for a remote image or font, lead tables in the rendered input, a user name or path in the metadata, a world-readable temp file, or output written into a directory that gets published or committed. Tests: Task 12 (fetcher confinement), Task 19 (staging, scrub, temp dir, output), AB-11 in Task 13.
5. **Recipient and header injection in the email form.** An address with a newline, comma, second recipient or angle bracket must never reach Mailgun; exactly one `to` and no `cc`, `bcc` or `h:` fields. Tests: Tasks 5 and 9, AB-1.

Also covered, no longer in the top five: races on single-use and counters (Tasks 7 and 9; the `code` attempt counter in Task 14), a deleted lead holding a live cookie (Tasks 4 and 8), clock and day boundaries (Tasks 3 and 7), and provider outage or empty KV (Tasks 5, 8 and 9).

---

## Contracts shared by all tasks

Fixed here so tasks can run in parallel.

**Routes** (all under the Plan 1 Worker; the Pages site serves `/kit/*`):

| Route | Success | Failure |
|---|---|---|
| `POST /k/gate` (form: `email`, `cf-turnstile-response`) | `303 /k/view` + `Set-Cookie` | `303 /kit/retry/`, identical for every cause |
| `POST /k/send` (same fields) | modes `cookie_pair` and `expiry_alert`: `303 /kit/sent/` (`cookie_pair` also sets `__Host-ra_pend`); mode `code`: `200` fixed page showing a 6-digit code (Task 14) | invalid input or Turnstile failure: `303 /kit/retry/`; throttled, over address limit, over budget or mail error: the same shape as success, including the pending cookie or a dummy code (reveals nothing) |
| `GET /k/v/:nonce` | `200` confirmation page with one button (mode `code`: plus one code field); consumes nothing, sets no cookie | malformed nonce: `303 /kit/` |
| `POST /k/v/:nonce` (the button; also `POST /k/v/` with no nonce) | `303 /k/view` + `Set-Cookie` | missing, unknown, used, expired or unbound nonce: `303 /kit/`; mode `code`, wrong code with attempts left: `200` the same page with fixed "that code did not match" text |
| `GET /k/view` | `200` gated page | `303 /kit/` |
| anything else under `/k/` | | `404`, empty body |
| any route over its hourly route budget (Task 17) | | `303 /kit/retry/`, identical to any other failure |
| any `/k/` request while `KIT_KILL` is set (Task 16) | | `503`, `Retry-After: 3600`, a fixed 60-byte HTML body, no database, KV, Turnstile or mail call |

Every response from every `/k/` route, success or failure, carries `Cache-Control: private, no-store` and never `public` or `s-maxage`.

**Guard order** (every `/k/` request; one place, so no task reorders it). Cheap and free first, anything that writes last:

1. kill switch (`KIT_KILL`, read from `env` on this request) `// CONTROL:kill-switch`
2. route and method
3. origin `// CONTROL:origin`, then bot user agent `// CONTROL:bot-ua`
4. `Content-Length` present, an integer and at most `MAX_BODY_BYTES` (header only, nothing read)
5. per-client minute limiter binding `// CONTROL:ip-minute`
6. hourly route budget, one D1 counter write `// CONTROL:route-budget`
7. bounded body read, then parse (`readBounded`, `// CONTROL:body-cap` on the streaming limit), `normalizeEmail`
8. Turnstile `// CONTROL:turnstile`
9. per-IP daily cap, lead upsert (lead cap), per-address day, mail budget, then the work

Steps 1 to 5 never touch D1. A request that fails any of steps 1 to 5 makes zero statements; step 6 is the first write and is bounded by the budget itself.

**Pending cookie** (mode `cookie_pair`, Task 14). `__Host-ra_pend=<43 char random>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=<LINK_TTL_SECONDS>`. Only its SHA-256 is stored. It is set on every `/kit/sent/` redirect, whether or not mail was sent, so the response reveals nothing. It never unlocks anything.

**Storage.** KV binding `KIT`, key `kit/v1` holds the gated page. D1 binding `DB` (the binding Plan 1 Task 8 declared; rename in this plan if Plan 1 used another name). Migration `worker/migrations/0002_kit.sql` (Task 4) defines `leads`, `kit_events`, `email_links`, `counters`, `kit_meta`. Migration `0002` is unreleased, so later tasks edit it in place; once Task 10 deploys it, any change is a new numbered migration.

**Cookie.** `__Host-ra_kit=<token>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=<ttl>` (`__Host-` requires Secure, `Path=/` and no `Domain`). Lax, not Strict, so the emailed link's top-level navigation can set and then use it.

**Token.** `v1.<base64url(JSON {lid, iat, exp, cc})>.<base64url(HMAC-SHA256)>` over the first two segments. `cc` is the two-letter country at issue time or the empty string; Task 15 compares it with the country on later views. A lead's `session_floor_ts` (Task 4, written by Task 15 in `revoke` mode) refuses any token with `iat` below it.

**Fixed copy.** Redirect targets and the error/sent pages (`/kit/retry/`, `/kit/sent/`) are static pages built in Task 2.

## File Structure

```
src/reach_around/kit/models.py        KitSettings, KitMetric, Partner, CaseStudy, Rate, MediaKit (Task 1)
src/reach_around/kit/policy.py        versioned display rules, check_kit_display (Task 1)
src/reach_around/kit/build.py         build_kit: public pages, gated page, worker vars (Task 2)
src/reach_around/kit/templates/       kit.html, kit_state.html, gated.html, kit_pdf.html
src/reach_around/kit/pdf.py           render_kit_pdf, refuse_remote (Task 12)
src/reach_around/kit/stage.py         staged copy, lead tables removed, image and PDF scrub (Task 19)
src/reach_around/kit/workdir.py       owner-only temp dir (Task 19)
src/reach_around/kit/output.py        explicit, confined output path (Task 19)
src/reach_around/kit/isolate.py       network-isolated child render (Task 19)
src/reach_around/kit/budget_check.py  worst-case D1 writes versus quota (Task 17)
src/reach_around/leads/purge.py       purge with dry run (Task 21)
src/reach_around/leads/artifacts.py   registered exports and backups: scrub, delete (Task 21)
src/reach_around/leads/delete.py      D1Client, CloudflareD1, delete_lead (Task 11)
worker/src/kit/env.ts                 KitEnv (Task 3)
worker/src/kit/token.ts               sign/verify, cookie helpers (Task 3)
worker/src/kit/leads.ts               lead storage, events, retention (Task 4)
worker/src/kit/input.ts, turnstile.ts email normalization, Turnstile verify (Task 5)
worker/src/kit/alerts.ts              owner alert sinks (Task 6)
worker/src/kit/limits.ts              minute, daily, address and budget limits (Task 7)
worker/src/kit/gate.ts                handleGate, handleView, unlockResponse (Task 8)
worker/src/kit/email.ts               handleSend, handleVerifyLink, Mailgun (Task 9)
worker/src/kit/binding.ts             link binding modes, pending cookie, code attempts (Task 14)
worker/src/kit/cost.ts                readBounded, kill switch, fixed 503 (Task 16)
worker/src/kit/budgets.ts             utcHour, route budgets, lead cap (Task 17)
worker/src/kit/log.ts                 the only logger: closed enums, per-minute cap (Task 18)
worker/migrations/0002_kit.sql
worker/test/kit/*.test.ts, worker/test/abuse/*.test.ts, worker/scripts/mutate.mjs, worker/controls.json
tests/kit/*.py, tests/leads/*.py, tests/fixtures/email_vectors.json, tests/fixtures/retention_vectors.json
docs/security/plan-3-abuse-cases.md (new), docs/security/threat-model.md (status lines only, from PR #54), docs/education/{the-gate,email-abuse}.md
```

## Dependencies and parallel groups

- Group A, no dependencies: Task 1 (Python) and Task 3 (Worker).
- Group B, after Task 3 (Task 1 for 2, 2A and 12; Plan 1 Task 6D for 2A): Tasks 2, 2A, 4, 5, 6 and 12 touch disjoint files and run in parallel.
- Group C: Task 7 (needs 4's migration), Task 11 (needs 5's email vectors) and Task 20 (needs 4; shares retention vectors with Task 21).
- Task 8 needs 3, 4, 5, 6, 7. Task 9 needs 8.
- Group D, after Task 9 and Task 8 (the router and handlers exist): Task 14 (needs 5, 7, 9), Task 16 (needs 5, 8), Task 17 (needs 7, 8, 9) and Task 19 (needs 12) touch mostly disjoint files; Task 14 and Task 17 both edit `email.ts`, so land 14 first.
- Group E: Task 15 (needs 6, 8, 14), Task 18 (needs 16, 17), Task 21 (needs 11, 20).
- Task 10 needs 2, 2A, 9, 14, 16, 17, 18 and 20 (it wires their vars, bindings and disclosure). Task 13 needs everything and runs last, even though its number is lower than 14 to 21 (numbers are kept so existing issue links hold).

Critical path: 3, 4, 5, 6, 7, 8, 9, 14, 15, 10, 13. The PDF chain (12, 19) and the retention chain (11, 20, 21) are parallel tracks.

## Hours

The roadmap sized Phase 3 at 68 h (4 L and 4 M packages). Per task, revision 2:

| Task | Hours | Task | Hours |
|---|---|---|---|
| 1 Models and policy | 4 | 12 Private PDF (renderer) | 7 |
| 2 Builder | 8 | 13 Abuse suite, mutation, threat model (+3) | 9 |
| 2A CSP exception | 2 | 14 Link binding (new) | 6 |
| 3 Env and token | 4 | 15 Redemption and country alerts (new) | 3 |
| 4 Lead storage | 4 | 16 Body cap and kill switch (new) | 4 |
| 5 Input and Turnstile | 4 | 17 Route budgets and storage caps (new) | 5 |
| 6 Owner alerts | 4 | 18 Bounded logs (new) | 3 |
| 7 Limits | 4 | 19 PDF staging, scrub, temp dir, output (new) | 7 |
| 8 Gate | 7 | 20 Lead retention in the Worker (new) | 4 |
| 9 Email link | 7 | 21 Purge command and artifacts (new) | 5 |
| 10 Privacy and deploy (+1) | 5 | | |
| 11 Delete command (+1) | 4 | | |

Tasks 1 to 13 as originally planned: 68 h. Revision 2: 68 + 5 (growth in Tasks 10, 11, 13) + 37 (Tasks 14 to 21) = **110 h**, a delta of +42 h. The roadmap's Phase 3 heading and the cumulative table are not edited here; recompute them when the maintainer accepts this revision (v0.3.0 moves from 247 to 289 cumulative hours, and every later row moves by the same 42).

## Open decisions for the maintainer

The plan builds every option below and refuses to guess where noted. None of these is decided. Nothing in the spec's approved decisions (Mailgun first, SES later; audiences public, partner, owner; PDF private to the owner; kit link not forwardable) is changed by any option.

**D1. How the emailed link is bound (required; the build fails until `kit.link_binding` is set).** The spec says the link is single use and not forwardable. Single use alone is not enough: whoever redeems first wins, so a forwarded copy, or a copy seen by a scanner that submits forms, can unlock. Also set `kit.link_ttl_minutes` (5 to 1440, default 60).

| Option | How it binds | Forwarded copy | Friction | Costs and risks |
|---|---|---|---|---|
| A `cookie_pair` | The browser that submitted the send form gets a pending cookie (random value, only its hash stored). Redeeming needs that cookie. | Lands on the gate (and unlocks itself there, counted as its own lead). | None when the visitor opens the mail on the same browser. Opening it on a phone while the form was on a laptop lands on the gate and the lead stays unverified. In-app mail browsers drop the cookie, same result. | No new page. A user who clears cookies between steps is not verified. Attacker who can read the victim's mail but not their browser cannot redeem. |
| B `code` | The send response (same browser) shows a 6-digit code; the link page asks for it. Five attempts per link, then the link dies. | Lands on the gate: the recipient never saw the code. Works across devices if the person reads the code off the first screen. | One extra field. | Spec says "clicking" the link marks verified; this adds a typed step, so it needs a one-line spec amendment. The Worker renders a page for the send response (a 200 instead of a 303). 10^6 codes with 5 attempts per link is a 5 in 10^6 guess per link, and the nonce is also required. |
| C `expiry_alert` | No browser binding. Link life is capped at 30 minutes and every redemption alerts the owner with a country mismatch flag. | The first redeemer wins within the window. The owner is told. | None. | Does not meet "not forwardable"; it detects, it does not prevent. Smallest build. |

Combinations are not offered: the alert in Task 15 runs in all three modes already.

**D2. A stolen unlock cookie.** The kit is open to anyone who passes Turnstile with any address, so a stolen cookie does not expose content that the gate would withhold; it misattributes views to another lead and shares an unlock without the sharer being counted. Choose the response in `kit.country_change`: `ignore`; `alert` (owner is told once per 6 hours; no friction; the code default only so the field is never blank, change it if you disagree); `revoke` (a view from a different country than the one at issue sends the visitor back to the gate; roaming and VPN users re-unlock). Also choose the lifetime: 24 h (spec default), 4 h, or 1 h (`kit.token_ttl_hours`).

**D3. Cost limits.** Confirm or change: hourly route budgets (defaults gate 150, send 50, link 100, view 300), `MAX_LEADS` 5000, and what happens at the cap (default: new leads are refused, the gate stays closed to new addresses, the owner is alerted once per day; the alternative is to evict the oldest unverified lead). Also whether a kill switch response is the 503 page (default) or a redirect to the retry page.

**D4. Retention numbers.** The plan replaces its earlier "retention counts from last activity" with a hard maximum counted from creation: default 365 days (the spec's 12 months), engine ceiling 730, unverified leads 30 days. Gate-only leads (the default flow) are unverified, so the Worker holds them for 30 days; the daily pull into the local store (Phase 2) is what keeps a follow-up list. Choose: keep 30, raise `unverified_lead_retention_days` up to the hard maximum, or drop the unverified rule.

**D5. Backups and exports.** Declare each lead export and backup in `privacy.lead_artifacts`. SQLite and CSV or JSONL artifacts are scrubbed in place; opaque archives cannot be edited, so they are deleted when older than their `max_age_days`, and a purge or delete reports them as retained until their roll-off date. Alternative that removes the problem: never include lead tables in backups (the staged-copy rules in Task 19 do this for the PDF).

**D6. PDF isolation strictness.** `--isolation auto` (default) runs the renderer in an unshared network namespace when `unshare` works and otherwise warns and relies on the fetcher guard; `require` refuses to render without it. Also whether output inside any git work tree is refused outright (default) or allowed when the path is git-ignored.

### Task 1: Kit config models and display policy

**Files:**
- Create: `src/reach_around/kit/__init__.py`, `models.py`, `policy.py`, `tests/kit/test_models.py`, `tests/kit/test_policy.py`
- Modify: `src/reach_around/config/models.py` (add `media_kit: MediaKit | None = None`, `kit: KitSettings = KitSettings()` to `SiteConfig`), `examples/instance/site.yaml`, `docs/config.md`

**Interfaces:**
- Consumes: `SiteConfig`, `Features`, `Privacy` (Plan 1).
- Produces:
  - `KitSettings(token_ttl_hours: int = 24, mail_daily_budget: int = 50, alert_sinks: list[Literal["email","matrix","discord"]] = [], send_link: bool = True, turnstile_site_key: str | None = None, d1_database_id: str | None = None)`; `token_ttl_hours` 1..168, `mail_daily_budget` 0..1000.
  - `KitMetric(id: str, platform: str, label: str, value: int | str, as_of: date, source: Literal["public_api","self_reported","imported","social_blade","owner_analytics"], audience: Literal["public","partner","owner"] = "public", group: Literal["audience","demographic"] = "audience")`
  - `Partner(name, note: str | None, url: https-only | None)`, `CaseStudy(title, summary, result)`, `Rate(item, price_text)`
  - `MediaKit(title: str, about: str, metrics: list[KitMetric], partners: list[Partner], case_studies: list[CaseStudy], rates: list[Rate], policy_overrides: list[str] = [])`
  - `Audience = Literal["public","partner","owner"]` (public: anyone; partner: an unlocked visitor; owner: the maintainer only, PDF with a flag). `Rule(id, cite, forbidden_public: bool, partner_ok: bool, applies: Callable[[KitMetric], bool])`; `RULES: tuple[Rule, ...]`; `POLICY_VERSION = "2026-10-23"`; `PolicyFinding(rule_id: str, severity: Literal["error","warning"], message: str, cite: str)`; `check_kit_display(kit: MediaKit, rules: Sequence[Rule] = RULES) -> list[PolicyFinding]`; `required_credits(kit: MediaKit) -> list[str]`.

- [ ] **Step 1: Write failing tests** (`extra="forbid"` everywhere):
  - models: `test_ttl_bounds` (0 and 169 raise, 24 and 168 pass); `test_budget_bounds`; `test_partner_url_https_only` (`javascript:x`, `http://a.example` raise); `test_gated_content_requires_kit_gate_feature` (partners/rates/case studies or any `audience: partner` metric with `features.kit_gate: false` raises `ConfigError` naming `features.kit_gate`); `test_gate_requires_turnstile_site_key`; `test_metric_ids_unique`; `test_example_config_loads_with_a_kit`.
  - policy: `test_youtube_owner_analytics_must_be_owner_only` (a youtube metric with `source="owner_analytics"` and `audience` of `public` or `partner` gives an error finding `rule_id == "yt-owner-analytics"`, `cite` containing "III.E.3.b"; with `audience="owner"` gives none); `test_linkedin_must_be_self_reported` (`linkedin` with `source="imported"` gives error `linkedin-self-reported`; `self_reported` passes); `test_social_blade_requires_credit` (`required_credits` returns `["Data from Social Blade"]` when any metric has that source, else `[]`); `test_partner_audience_is_enforced_per_rule` (using an injected synthetic rule with `forbidden_public=True, partner_ok=True`: the metric is an error at `public` and passes at `partner`; with `partner_ok=False` it is an error at both; this is the three-audience mechanism, the real rules only exercise the `partner_ok=False` side); `test_linkedin_non_self_reported_is_owner_only`; `test_override_demotes_error_to_warning` (`policy_overrides=["yt-owner-analytics"]` makes the finding a warning); `test_unknown_override_id_refused`.
- [ ] **Step 2: Run** `pytest tests/kit -v`. Expected: FAIL, import error.
- [ ] **Step 3: Implement** the models and rules. Each rule is a table entry (`id`, `cite` with source name and date checked, predicate). Sources: YouTube API Developer Policies III.E.3.b (checked 2026-10-23); LinkedIn API Terms (no scheduled pull or public display of API content, so numbers must be typed in); Social Blade Developer Terms 3.4 (public sharing needs credit). Phase 5 generalizes this table; keep the rule shape stable.
- [ ] **Step 4: Run tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add media kit config models and display policy`.

### Task 2: Media kit builder (public half, gated bundle)

**Files:**
- Create: `src/reach_around/kit/build.py`, `templates/kit.html`, `kit_state.html`, `gated.html`, `tests/kit/test_build.py`, `tests/kit/golden/`
- Modify: `src/reach_around/site/build.py` (call `build_kit` when `media_kit` is set; `--gated-out DIR` on `reach-around build`), `site/static/site.css` (print rules), `docs/education/the-gate.md` (first section)

**Interfaces:**
- Consumes: `MediaKit`, `check_kit_display`, `required_credits` (Task 1); `build_site` (Plan 1).
- Produces: `build_kit(config: SiteConfig, out_dir: Path, gated_out: Path, *, today: date) -> KitBuildResult`; `KitBuildResult(public_pages: list[Path], gated_page: Path | None, vars_path: Path, warnings: list[str])`. Writes into `out_dir`: `kit/index.html`, `kit/sent/index.html`, `kit/retry/index.html` (each carries the gate forms and widgets; headers are Task 2A). Writes into `gated_out`: `kit.html` and `worker-vars.json` (`TOKEN_TTL_SECONDS`, `LEAD_RETENTION_DAYS`, `UNVERIFIED_RETENTION_DAYS`, `MAIL_DAILY_BUDGET`, `ALERT_SINKS`, `LINK_BINDING`, `LINK_TTL_SECONDS`, `COUNTRY_CHANGE`, `ROUTE_BUDGETS`, `MAX_LEADS`, `MAX_BODY_BYTES`; Tasks 14, 15, 16, 17 and 20 add theirs, each with a test that the value reaches the file). The builder raises `ConfigError` for any `error` finding and returns `warning` findings in `warnings`.

- [ ] **Step 1: Write failing tests** using synthetic data with a canary string `CANARY-GATED-7f3a` placed in a rate, a partner note and a case study:
  - `test_canary_is_in_gated_page_only` (the canary appears in `gated_out/kit.html` and in no file anywhere under `out_dir`, including `manifest.json`, sitemaps and the Worker vars file).
  - `test_gated_out_inside_out_dir_refused` (equal, child and parent paths each raise `ConfigError`).
  - `test_public_page_shows_only_public_metrics_with_date_and_self_reported_label`.
  - `test_owner_metrics_in_neither_bundle` (an `audience="owner"` canary appears nowhere in either output); `test_partner_only_field_never_in_public_bundle` (a metric marked `audience="partner"` shows in `gated_out/kit.html` and in no file under `out_dir`; a public-forbidden field set to `public` fails the build); `test_public_forbidden_field_may_reach_gated_page_only_if_rule_allows_partners` (synthetic rule injected as in Task 1).
  - `test_html_in_kit_text_is_escaped` (`<script>` in `about`, a partner name and a case study).
  - `test_gate_forms_have_turnstile_widget_and_post_actions` (form `action="/k/gate"` and `action="/k/send"`, widgets with `data-action="gate"` and `data-action="send"`, no token or email field value is prefilled, no `?t=` link anywhere).
  - `test_noscript_explains_javascript_needed` and `test_public_text_present_without_javascript` (about text and public metrics are in the HTML, not injected).
  - `test_social_blade_credit_rendered_on_both_pages_when_used`.
  - `test_gated_page_is_noindex_and_loads_no_script`.
  - `test_no_gate_forms_when_feature_off` and `test_policy_error_fails_build_and_warning_is_reported`.
  - `test_golden_output_for_example_config`.
- [ ] **Step 2: Run** `pytest tests/kit/test_build.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `build_kit`** with the Plan 1 Jinja environment (`autoescape=True`, `StrictUndefined`). The gated page is a complete HTML document linking `/static/site.css`, with `<meta name="robots" content="noindex">` and print CSS that shows everything with no blur or form controls. Add `--gated-out` to the CLI (required when `media_kit` is set).
- [ ] **Step 4: Run** `pytest tests/kit tests/site -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: build the media kit public half and gated bundle`.

### Task 2A: Gate-page CSP relaxation

Plan 1 Task 6D generates the Pages `_headers` file with a strict CSP for every path. The gate needs one narrow, explicit exception for Turnstile and nothing else.

**Files:**
- Create: `src/reach_around/kit/headers.py`, `tests/kit/test_headers.py`
- Modify: Plan 1 Task 6D's `_headers` generator (call `kit_header_rules`), `docs/education/the-gate.md`

**Interfaces:**
- Consumes: the strict policy string and header generator from Plan 1 Task 6D; `SiteConfig.features.kit_gate`.
- Produces: `GATE_PAGES = ("/kit/", "/kit/sent/", "/kit/retry/")` (exactly the pages that render a widget); `TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"`; `kit_header_rules(config: SiteConfig, strict_csp: str) -> dict[str, str]` mapping each path in `GATE_PAGES` to a CSP, empty when `kit_gate` is false.

- [ ] **Step 1: Write failing tests** (parse the generated `_headers`):
  - `test_relaxation_only_on_gate_pages` (every path other than `GATE_PAGES` has a CSP byte-identical to the strict one, including `/`, link tabs, `/privacy/`, `/static/*`).
  - `test_relaxed_csp_differs_from_strict_only_by_turnstile_origin` (removing `https://challenges.cloudflare.com` from the relaxed policy yields the strict policy; the origin appears only in `script-src` and `frame-src`).
  - `test_no_unsafe_inline_unsafe_eval_wildcard_or_data_script` (relaxed policy contains none of `'unsafe-inline'`, `'unsafe-eval'`, `*`, `http:`, `data:` in `script-src`).
  - `test_no_relaxation_when_gate_off` (`kit_gate: false` gives byte-identical headers to Plan 1's output).
  - `test_frame_ancestors_none_kept_on_gate_pages`.
- [ ] **Step 2: Run** `pytest tests/kit/test_headers.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `kit_header_rules` by appending the origin to the strict policy's `script-src` and `frame-src` directives. In a Playwright run (`--run-visual`) load `/kit/` with Turnstile's published test site key `1x00000000000000000000AA` and record console CSP violations; add `connect-src` for the same origin only if a violation names it, and extend the tests to match.
- [ ] **Step 4: Run** `pytest tests/kit -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add a narrow Turnstile CSP exception for the gate pages`.

### Task 3: Kit environment and token

**Files:**
- Create: `worker/src/kit/env.ts`, `worker/src/kit/token.ts`, `worker/test/kit/token.test.ts`

**Interfaces:**
- Produces:
  - `interface KitEnv extends Env` with bindings `DB: D1Database`, `KIT: KVNamespace`, `GATE_LIMITER: RateLimit`, `SEND_LIMITER: RateLimit`; vars `SITE_HOST`, `SITE_NAME`, `MAIL_FROM`, `OWNER_EMAIL`, optional `TOKEN_TTL_SECONDS` (default 86400), `LEAD_RETENTION_DAYS` (365, ceiling 730), `UNVERIFIED_RETENTION_DAYS` (30), `EVENT_RETENTION_DAYS` (90), `MAIL_DAILY_BUDGET` (50), `ALERT_DAILY_CAP` (100), `IP_SEND_DAILY_CAP` (5), `LINK_TTL_SECONDS` (3600), `LINK_BINDING` (`cookie_pair` | `code` | `expiry_alert`; no default, absent means send fails closed), `COUNTRY_CHANGE` (`ignore` | `alert` | `revoke`, default `alert`), `ROUTE_BUDGETS` (JSON `{gate, send, link, view}` per hour; defaults 150, 50, 100, 300), `MAX_LEADS` (5000), `MAX_BODY_BYTES` (4096), `KIT_KILL` and `CLICKS_KILL` (`1` or `true` engages), `LOG_MAX_PER_MINUTE` (60), `ALERT_SINKS` (JSON array), `MAILGUN_API_BASE` (default `https://api.mailgun.net`); secrets `TOKEN_HMAC_KEY`, `TURNSTILE_SECRET`, `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, and optional `DISCORD_WEBHOOK_URL`, `MATRIX_HOMESERVER`, `MATRIX_TOKEN`, `MATRIX_ROOM_ID`.
  - `signToken(key: string, claims: { lid: string; iat: number; exp: number; cc: string }): Promise<string>` (throws if `key` is under 32 bytes or `cc` does not match `^([A-Z]{2})?$`)
  - `verifyToken(key: string, token: string, nowSec: number): Promise<{ lid: string; iat: number; exp: number; cc: string } | null>`
  - `unlockCookie(token: string, ttlSeconds: number): string`; `readUnlockCookie(header: string | null): string | null`; `COOKIE_NAME = "__Host-ra_kit"`.

- [ ] **Step 1: Write failing tests** (key `k = "x".repeat(32)`, `lid = "a1b2c3"`, `iat = 1_000`, `exp = 1_000 + 86_400`):
  - `round trip returns lid, iat, exp and cc`; `a token without cc or with cc "usa" or 1 is refused`.
  - `tampered payload is refused` (re-encode payload with `lid = "ffff"` and keep the signature: `null`); `tampered signature is refused` (flip last char); `token signed with another key is refused`.
  - `token at exactly exp is refused, one second earlier is accepted` (`now = exp` null, `now = exp - 1` ok).
  - `iat more than 60 s in the future is refused`.
  - `exp minus iat above 168 hours is refused` (verifier-side cap).
  - `malformed tokens return null and never throw`: `""`, `"a.b"`, `"v1.a.b.c"`, `"v2.<valid-looking>"`, non-base64, payload JSON that is an array, missing `lid`, numeric `lid`, string `exp`, 10 KiB token.
  - `signToken throws on a 31 byte key`.
  - `verify uses crypto.subtle.verify` (spy; no string comparison of signatures).
  - cookie: `unlockCookie` equals `__Host-ra_kit=<t>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=86400` and has no `Domain`; `readUnlockCookie` finds the value among other cookies, ignores `ra_kit=` and `__Host-ra_kit_x=`, and returns null for two duplicate `__Host-ra_kit` cookies.
- [ ] **Step 2: Run** `npm test -- token` in `worker/`. Expected: FAIL.
- [ ] **Step 3: Implement** with Web Crypto HMAC-SHA256 and base64url without padding. Guard lines carry `// CONTROL:token-signature` (signature check) and `// CONTROL:token-expiry` (`now >= exp`). Add `KitEnv` to `wrangler.toml` as bindings with placeholder ids only (real ids come from Task 10).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the kit environment and HMAC unlock token`.

### Task 4: Lead storage, events and retention

**Files:**
- Create: `worker/migrations/0002_kit.sql`, `worker/src/kit/leads.ts`, `worker/test/kit/leads.test.ts`
- Modify: `worker/src/retention.ts` (add `purgeKitData`), scheduled handler in `worker/src/index.ts`, `worker/test/retention.test.ts`

**Interfaces:**
- Consumes: `KitEnv` (Task 3).
- Produces:
  - Tables: `leads(id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL, verified_ts INTEGER, source TEXT NOT NULL CHECK (source IN ('gate','email')), country TEXT, last_link_day TEXT, last_view_alert_ts INTEGER, last_view_event_ts INTEGER, last_cc_alert_ts INTEGER, session_floor_ts INTEGER)`; `kit_events(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE, kind TEXT NOT NULL CHECK (kind IN ('unlock','view','verify')))`; `email_links(id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE, created_ts INTEGER NOT NULL, expires_ts INTEGER NOT NULL, used_ts INTEGER, pending_hash TEXT, code_hash TEXT, attempts INTEGER NOT NULL DEFAULT 0, send_country TEXT)`; `counters(key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (key, day))`; `kit_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)` (heartbeats such as `last_purge_ts`). Indexes on `kit_events.ts`, `leads.created_ts` and `leads.verified_ts`. The `counters.day` column holds a UTC day or, for route budgets, a UTC hour string (Task 17).
  - `upsertLead(db, email: string, source: "gate" | "email", country: string | null, nowMs: number): Promise<{ id: string; created: boolean }>`; `getLead(db, id): Promise<Lead | null>`; `markVerified(db, id, nowMs): Promise<void>`; `touchLead(db, id, nowMs): Promise<void>`; `recordKitEvent(db, leadId, kind, nowMs): Promise<void>`
  - `purgeKitData(env: KitEnv, nowMs: number): Promise<{ events: number; links: number; counters: number }>` (events, links and counters only; lead retention is `purgeLeads` in Task 20)

- [ ] **Step 1: Write failing tests** (apply `0002` in the test setup):
  - `upsertLead is case-insensitive and returns the same id with created false the second time`; `id is 32 hex characters`.
  - `a lead has no ip column` (`PRAGMA table_info(leads)` and every other kit table lists no name matching `/ip|addr/i`).
  - `deleting a lead cascades to events and links` (the schema's foreign keys are enforced; this is what lets the owner command in Task 11 be one statement).
  - lead retention tests live in Task 20 (hard maximum from `created_ts`); this task's test is `purgeKitData does not delete leads`.
  - `the migration declares the Task 14, 15, 17 and 20 columns` (`PRAGMA table_info` lists `pending_hash`, `code_hash`, `attempts`, `send_country`, `last_view_event_ts`, `last_cc_alert_ts`, `session_floor_ts`, and `kit_meta` exists).
  - `events older than 90 days are purged, newer kept`; `used or expired email links older than 7 days are purged, a live one is kept`; `counters older than 2 days are purged`.
  - `the scheduled handler calls purgeOldEvents and purgeKitData, and a throw in one does not stop the other` (`Promise.allSettled`; Task 20 adds `purgeLeads` to the same handler).
  - `no SQL is built by interpolation`: reads every file in `worker/src/kit/` and `retention.ts` and asserts no template literal containing `SELECT|INSERT|UPDATE|DELETE` also contains `${`.
- [ ] **Step 2: Run** `npm test -- leads retention`. Expected: FAIL.
- [ ] **Step 3: Implement** the migration and functions. Use `INSERT ... ON CONFLICT(email) DO UPDATE SET last_seen_ts` so concurrent upserts cannot duplicate. Lead ids come from `crypto.getRandomValues` (16 bytes).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add lead storage, kit events and retention`.

### Task 5: Input validation and Turnstile

**Files:**
- Create: `worker/src/kit/input.ts`, `worker/src/kit/turnstile.ts`, `worker/test/kit/input.test.ts`, `worker/test/kit/turnstile.test.ts`, `tests/fixtures/email_vectors.json`

**Interfaces:**
- Produces: `normalizeEmail(raw: unknown): string | null`; `readForm(request: Request): Promise<{ email: string | null; turnstile: string | null } | null>` (null when content type is not `application/x-www-form-urlencoded`, body over 4096 bytes, or a field repeats; this task ships a simple length check, Task 16 replaces it with `readBounded`, which caps before parsing and before reading, and re-runs these vectors); `verifyTurnstile(env: KitEnv, token: unknown, action: "gate" | "send", fetchFn?: typeof fetch): Promise<boolean>`.
- `tests/fixtures/email_vectors.json`: `{ "valid": [[input, normalized], ...], "invalid": [input, ...] }`, shared with Python (Task 11).

- [ ] **Step 1: Write failing tests.** Input vectors (the fixture file and the test that iterates it):
  - valid: `"Partner@Example.COM "` -> `"partner@example.com"`; `"a+tag@sub.example.com"` -> same lowercased.
  - invalid (all `null`): `"a@b.com\r\nBcc: x@y.com"`, `"a@b.com\nx"`, `"a@b.com,c@d.com"`, `"a@b.com;c@d.com"`, `"a@b.com c@d.com"`, `"<a@b.com>"`, `"\"a\"@b.com"`, `"a@@b.com"`, `"a@b"`, `"a@b.com "`, `"é@b.com"`, `"@b.com"`, `"a@"`, `""`, 255-character address, 65-character local part, and non-strings (`undefined`, `42`, `["a@b.com","c@d.com"]`, `{}`).
  - `readForm` returns null for JSON bodies, a 5 KiB body, and a repeated `email` field.
  - Turnstile (fake `fetchFn`): `accepts success with matching action and hostname`; `rejects success false`; `rejects action mismatch` (`send` token on `gate`); `rejects hostname mismatch`; `rejects non-200`; `rejects when fetch throws or times out after 3 s` (returns false, no throw); `makes no network call for a missing, non-string or 2049 character token`; `posts only secret and response to https://challenges.cloudflare.com/turnstile/v0/siteverify` (no `remoteip`, so no address leaves the Worker).
- [ ] **Step 2: Run** `npm test -- input turnstile`. Expected: FAIL.
- [ ] **Step 3: Implement** `normalizeEmail` with an ASCII allowlist (`[a-z0-9._%+-]` local, `[a-z0-9.-]` labels, one `@`, at least one dot in the domain). Turnstile uses `AbortSignal.timeout(3000)`. The check that rejects a failed verification is a single line marked `// CONTROL:turnstile` in `gate.ts` and `email.ts` (Tasks 8 and 9); this task marks `// CONTROL:email-validate` on the normalization failure return.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add strict email validation and Turnstile verification`.

### Task 6: Owner alerts

**Files:**
- Create: `worker/src/kit/alerts.ts`, `worker/test/kit/alerts.test.ts`

**Interfaces:**
- Consumes: `KitEnv`, `sendMailgun` is not used here (alerts own their own fixed Mailgun call so Task 9 is not a dependency).
- Produces: `type KitAlert = { kind: "unlock" | "view"; email: string; country: string | null; verified: boolean; ts: number }`; `formatAlert(alert: KitAlert): string` (single line, plain text; Tasks 15 and 17 extend `kind` with `redeem`, `country-change` and `capacity`, each with a fixed subject kept in one table); `notifyOwner(env: KitEnv, alert: KitAlert, fetchFn?: typeof fetch): Promise<void>` (never throws).

- [ ] **Step 1: Write failing tests** (fake `fetchFn`):
  - `email sink sends one fixed-subject message to OWNER_EMAIL` (subject `"Reach Around: media kit unlocked"` or `"... viewed"`; form fields are only `from,to,subject,text`).
  - `discord sink posts content with allowed_mentions parse empty` (`{"content": ..., "allowed_mentions": {"parse": []}}`).
  - `matrix sink PUTs an m.notice to the encoded room id with a transaction id and bearer token`.
  - `only the sinks named in ALERT_SINKS fire`; `no sinks configured sends nothing`.
  - `one failing sink does not stop the others and notifyOwner resolves` (Promise.allSettled).
  - `formatAlert strips control characters and newlines from a hostile email string` (`"x@y.com\n@everyone **bold**"` yields one line).
  - `no console call contains the email, a token or a webhook URL` (spy on every `console` method during success and failure runs).
  - `each sink call times out after 3 s`.
- [ ] **Step 2: Run** `npm test -- alerts`. Expected: FAIL.
- [ ] **Step 3: Implement** the three sinks. Webhook and Matrix URLs come only from secrets.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add owner alert sinks for email, Matrix and Discord`.

### Task 7: Limits and send budget

**Files:**
- Create: `worker/src/kit/limits.ts`, `worker/test/kit/limits.test.ts`

**Interfaces:**
- Consumes: `counters`, `leads.last_link_day` (Task 4).
- Produces: `utcDay(nowMs: number): string`; `checkIpMinute(limiter: RateLimit, ip: string | null): Promise<boolean>`; `reserveIpDaily(env: KitEnv, ip: string | null, nowMs: number, cap: number): Promise<boolean>`; `reserveAddressDay(db, leadId: string, day: string): Promise<boolean>`; `reserveBudget(db, key: string, day: string, limit: number): Promise<boolean>` (keys used elsewhere: `mail`, `alert`).
- The daily IP key is `HMAC-SHA256(HMAC(TOKEN_HMAC_KEY, "ipkey:" + day), ip)` truncated to 16 bytes and hex encoded: keyed, rotates daily, never the address, purged after 2 days by Task 4.

- [ ] **Step 1: Write failing tests:**
  - `utcDay at 2027-03-14T23:59:59Z is 2027-03-14 and one second later 2027-03-15`.
  - `reserveBudget allows exactly the limit then refuses` (limit 3: true, true, true, false); `ten concurrent reservations with limit 3 grant exactly three` (`Promise.all`); `budget resets on the next UTC day`; `limit 0 always refuses`; `different keys do not share a count`.
  - `reserveAddressDay grants once per lead per day, refuses the second, grants the next day`; `concurrent double call grants once`.
  - `reserveIpDaily refuses after cap` (cap 5, sixth false); `stored key does not contain the address` (select all counters; none contains `203.0.113.9`); `key differs between days`; `a missing ip (null) is refused`.
  - `checkIpMinute returns the limiter's verdict and refuses a null ip`.
- [ ] **Step 2: Run** `npm test -- limits`. Expected: FAIL.
- [ ] **Step 3: Implement** with one atomic statement per reservation: `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 WHERE count < ?` and check `meta.changes === 1`; address-day is `UPDATE leads SET last_link_day = ? WHERE id = ? AND (last_link_day IS NULL OR last_link_day < ?)`. Mark guards `// CONTROL:ip-minute`, `// CONTROL:ip-daily`, `// CONTROL:address-day`, `// CONTROL:budget` at their call sites (Tasks 8 and 9).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add atomic send limits and a daily budget`.

### Task 8: Gate and gated-content serving

**Files:**
- Create: `worker/src/kit/gate.ts`, `worker/test/kit/gate.test.ts`
- Modify: `worker/src/index.ts` (routes from the contract table; every other `/k/` path returns `404` empty)

**Interfaces:**
- Consumes: Tasks 3 to 7; click-endpoint bot and origin helpers from Plan 1 (extract `isBotUserAgent(ua)` and `originAllowed(request, env)` from `click.ts` into `worker/src/http.ts` if they are not already shared).
- Produces: `handleGate(request, env, ctx): Promise<Response>`; `handleView(request, env, ctx): Promise<Response>`; `unlockResponse(env: KitEnv, leadId: string, nowMs: number, country: string | null): Promise<Response>` (the `303 /k/view` plus cookie, token claim `cc = country ?? ""`; Task 9 reuses it); `alertOnce(env, ctx, lead, kind)` applying the debounce (one `view` alert per lead per 6 hours via `leads.last_view_alert_ts`) and the daily cap (`reserveBudget(db, "alert", day, ALERT_DAILY_CAP)`), then `ctx.waitUntil(notifyOwner(...))`.
- Gate order is the Contracts "Guard order", each guard a single line: this task writes steps 2 to 5, 7 (parse) and 8, then `upsertLead`, `recordKitEvent(unlock)`, alert, `unlockResponse`. Tasks 16 and 17 add steps 1 and 6 and the cap; until they land, those steps are absent, and the Task 16 and 17 tests prove their order. View: `readUnlockCookie`, `verifyToken`, `getLead` `// CONTROL:lead-exists` (also refuses a token whose `iat` is below `session_floor_ts`), `KIT.get("kit/v1")`.

- [ ] **Step 1: Write failing tests** (full `worker.fetch`, in-memory D1, fake KV holding a page containing `CANARY-GATED-7f3a`, fake Turnstile that approves token `ok`):
  - `valid gate post returns 303 to /k/view with the cookie and a lead row and an unlock event`; `the cookie's token verifies and its lid equals the lead id`; `Location and body contain no token`.
  - `every failure returns the identical response`: for missing Turnstile, bad Turnstile, invalid email, bot UA, missing UA, wrong origin, rate-limited, oversized body, JSON body, and GET method (GET gives `404`), assert status `303`, `Location: /kit/retry/`, no `Set-Cookie`, empty body, and zero rows in `leads`.
  - `client-supplied lead fields are ignored`: extra form fields `verified=1`, `lid=zzz`, `country=XX`, `source=email` leave `verified_ts` null, `source = 'gate'` and country from `CF-IPCountry`.
  - `turnstile outage fails closed` (fetch throws: no lead, no cookie).
  - `view without cookie returns 303 /kit/ with an empty body and none of the canary`.
  - `view ignores a valid token in the query string, in an Authorization header and in a custom header` (still 303).
  - `view with a valid cookie returns 200 with the page, Cache-Control: private, no-store, X-Robots-Tag: noindex, Referrer-Policy: no-referrer and a CSP with default-src 'none'`.
  - `expired cookie, tampered cookie and cookie signed with another key each get 303`.
  - `a cookie for a deleted lead gets 303` (delete the lead row after the cookie is issued).
  - `every /k/ response from the gate and view routes carries Cache-Control: private, no-store` (table test over success and each failure).
  - `a valid cookie with an empty KV key returns 404, no body`.
  - `view records a view event and alerts once per six hours` (two views within 6 h: one alert; a view 6 h and 1 s later: second alert); `alert cap stops alerts at ALERT_DAILY_CAP while views still succeed`.
  - `a failing alert sink does not fail the unlock`.
  - `canary and gated page appear only in /k/view responses`: request `/k/content`, `/k/kit/v1`, `/k/view/`, `/kit/v1`, `/k/view` with `POST`, `HEAD` without cookie; none returns the canary.
  - `TOKEN_HMAC_KEY of 31 bytes makes gate fail closed`.
  - `no console call contains the email or the token`.
- [ ] **Step 2: Run** `npm test -- gate`. Expected: FAIL.
- [ ] **Step 3: Implement** per the order above. Country is `request.headers.get("CF-IPCountry")` kept only if it matches `^[A-Z]{2}$`. Mark the cookie-only guard (view reads only the cookie) `// CONTROL:cookie-only`.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Turnstile gate and cookie-bound kit serving`.

### Task 9: Emailed single-use link

**Files:**
- Create: `worker/src/kit/email.ts`, `worker/test/kit/email.test.ts`
- Modify: `worker/src/index.ts` (add `POST /k/send`, `GET` and `POST /k/v/:nonce`)

**Interfaces:**
- Consumes: Tasks 3 to 8 (`unlockResponse`, `alertOnce`, limits, `upsertLead`, `markVerified`).
- Produces: `handleSend(request, env, ctx): Promise<Response>`; `handleLinkPage(request: Request, env: KitEnv): Promise<Response>` (GET: confirmation page); `handleVerifyLink(request, env, ctx): Promise<Response>` (POST: the only consumer); `renderLinkEmail(siteName: string, url: string): { subject: string; text: string }`; `sendMailgun(env, to: string, subject: string, text: string, fetchFn?: typeof fetch): Promise<boolean>`.
- Scope note: this task makes the link single use and scanner-safe. It does not bind the link to a browser, so until Task 14 the first redeemer of a forwarded copy wins. The Task 9 test named `a forwarded copy` therefore covers only "after first use". Task 14 closes the first-use case and Task 15 reports it.
- Send order, each guard one line: origin, bot UA, `checkIpMinute(SEND_LIMITER)`, `readForm`, `normalizeEmail`, `verifyTurnstile(action "send")`, `reserveIpDaily` `// CONTROL:ip-daily`, `upsertLead(source "email")`, `reserveAddressDay` `// CONTROL:address-day`, `reserveBudget("mail")` `// CONTROL:budget`, create nonce (32 random bytes, base64url, 43 characters), store `SHA-256(nonce)` hex with `expires_ts = now + LINK_TTL_SECONDS`, send. The link is `https://<SITE_HOST>/k/v/<nonce>`.
- Link page and verify (scanner-safe): the emailed URL is `GET /k/v/<nonce>`, which renders a static confirmation page (one `<form method="post">` with a single button, no script, `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex`, CSP `default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'`) and reads nothing from the database. Only the button's `POST /k/v/<nonce>` consumes: origin check `// CONTROL:origin`, nonce must match `^[A-Za-z0-9_-]{43}$`, consume with `UPDATE email_links SET used_ts = ? WHERE id = ? AND used_ts IS NULL AND expires_ts > ?` and require `meta.changes === 1` `// CONTROL:single-use`; then `markVerified`, `recordKitEvent(verify)`, `recordKitEvent(unlock)`, `alertOnce`, `unlockResponse`. `HEAD` returns the same headers as GET with no body and touches nothing.

- [ ] **Step 1: Write failing tests** (fake Mailgun via `fetchFn`, `MAIL_DAILY_BUDGET` set per test):
  - relay and injection (Review Focus 1): `address with newline, comma, semicolon, space or angle brackets never reaches Mailgun` (fetch call count 0, redirect `/kit/retry/`); `exactly one Mailgun call has a single to equal to the normalized address and only fields from, to, subject, text` (no `cc`, `bcc`, `h:*`, `o:*`); `repeated email fields are refused`.
  - template: `rendered text is identical for five different addresses except the URL`; `body does not contain the recipient's local part` (`zzqx@example.com`); `subject is constant`; `body states the link works once, in one browser, for LINK_TTL_SECONDS / 60 minutes` (60 by default; Task 14 makes the wording depend on the binding mode).
  - limits: `second send to the same address on the same UTC day makes no Mailgun call and still redirects to /kit/sent/`; `next UTC day sends again`; `per-IP daily cap of 5 stops the sixth address from one client`; `Turnstile failure sends nothing and redirects /kit/retry/`; `the redirect is identical whether the address is new, throttled or over budget`.
  - budget (Review Focus 2 and 5): `budget 2: the third request makes no Mailgun call, and a gate post in the same run still returns a cookie`; `20 distinct addresses from 20 clients with budget 5 produce exactly 5 Mailgun calls`; `Mailgun 500 or a thrown fetch still redirects /kit/sent/ and never blocks the gate`.
  - link (scanner-safe): `a plain GET returns 200 with the confirmation form, sets no cookie, and the link stays unused` (assert `used_ts` null and no Set-Cookie after ten GETs); `a HEAD never consumes the link`; `a GET with Sec-Purpose or Purpose prefetch headers or a mail-scanner user agent never consumes it`; `the first POST returns 303 /k/view with a cookie, sets verified_ts, records verify and unlock`; `a second POST returns 303 /kit/ with no cookie`; `a POST with a missing nonce (/k/v/), an unknown nonce or a malformed nonce (including ' OR 1=1 --) returns 303 /kit/ and never reaches SQL as raw text`; `a POST after a scanner GET still unlocks once`; `a forwarded copy: after first use, GET shows the form but POST from a different client gets 303 /kit/`; `two concurrent POSTs produce exactly one Set-Cookie`; `expired link POST gets 303 /kit/`; `a cross-origin POST (wrong Origin) gets 303 /kit/ and does not consume`; `the confirmation page has no script tag and its form posts to the same path`; `the table stores the hash, never the nonce`; `using the nonce as the cookie value gets nothing from /k/view`; `the emailed URL and every Location header contain no unlock token`; `deleting the lead invalidates its unused link`; `every /k/ response (200, 303, 404, GET, POST, HEAD) carries Cache-Control: private, no-store and none carries public or s-maxage`.
  - `no console call contains the email or nonce`.
- [ ] **Step 2: Run** `npm test -- email`. Expected: FAIL.
- [ ] **Step 3: Implement** to the order above. Mailgun: `POST {MAILGUN_API_BASE}/v3/{MAILGUN_DOMAIN}/messages`, Basic auth `api:{MAILGUN_API_KEY}`, form body, 5 s timeout, no retry (a retried send would double-spend the one-per-day limit).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the single-use emailed kit link with abuse limits`.

### Task 10: Privacy disclosure and deploy wiring

**Files:**
- Modify: `src/reach_around/site/privacy.py`, `tests/site/test_privacy.py`, `worker/wrangler.toml`, `docs/deploy.md`, `.github/workflows/deploy-test.yml`, `docs/config.md`
- Create: `tests/kit/test_deploy_wiring.py`

**Interfaces:**
- Consumes: `Features.kit_gate`, `Privacy.lead_retention_days`, `KitSettings` (Tasks 1 and 2).
- Produces: extra `Disclosure` items when `features.kit_gate` is true (below); `wrangler.toml` bindings `KIT`, `GATE_LIMITER` (5 per 60 s), `SEND_LIMITER` (3 per 60 s), vars from `worker-vars.json`.

- [ ] **Step 1: Write failing tests:**
  - `test_gate_items_name_email_cookie_turnstile_events_and_counters` (items mention: the email and the lead retention days from config; cookie `__Host-ra_kit`, its purpose and `24 hours` from `kit.token_ttl_hours`; Cloudflare Turnstile as a third party; which lead opened the kit and when; a keyed one-way daily hash of the network address kept up to 2 days for abuse limits; the optional verified-link email via Mailgun), and `test_no_item_claims_no_ip_processing_when_gate_on` (no gate-on disclosure may say "no IP address" without the counter item beside it).
  - `test_disclosure_changes_with_ttl` (set 48 hours, text says 48).
  - deploy: `test_pages_deploy_never_includes_gated_dir` (parse `deploy-test.yml`; the `wrangler pages deploy` argument is the site output directory, and no step passes the gated directory to it); `test_workflow_uploads_gated_page_to_kv_key_kit_v1` (a step runs `wrangler kv key put --binding KIT kit/v1 --path <gated>/kit.html --remote`); `test_workflow_applies_migrations_before_worker_deploy`; `test_deploy_job_uses_protected_environment` (the job in `deploy-test.yml` declares `environment: test-deploy`, the Cloudflare token is read from that environment's secrets, and the workflow does not rely on an in-workflow ref check as its control; the environment itself, with required reviewers and a `main`-only deployment branch rule, is the control Plan 1 Task 11 and `docs/deploy.md` reference, so add `environment: test-deploy` here only if Plan 1's workflow lacks it); `test_gated_store_has_no_public_access` (`wrangler.toml` binds `KIT` as a `kv_namespaces` entry, declares no `r2_buckets` entry, and no route or custom domain points at the namespace; a KV namespace has no public URL; if a later change moves the page to R2 this test must be extended to assert no public bucket domain); `test_post_deploy_smoke_checks_gate_headers` (a final workflow step requests `/k/view` without a cookie and fails unless the response is a 303 with `cache-control` containing `no-store` and a body free of the canary); `test_wrangler_toml_declares_kit_bindings_and_no_secrets` (no value for `TOKEN_HMAC_KEY`, `TURNSTILE_SECRET`, `MAILGUN_API_KEY`).
  - revision 2 additions: `test_disclosure_names_pending_cookie_when_link_binding_is_cookie_pair` (names `__Host-ra_pend`, its purpose and `kit.link_ttl_minutes`; absent for the other two modes); `test_disclosure_states_hard_retention_and_unverified_window` (names `lead_retention_days` as a maximum from creation, `unverified_lead_retention_days`, and that deletion also reaches registered backups within `max_age_days`, Task 21); `test_disclosure_says_logs_hold_no_personal_data_and_names_the_sample_rate`; `test_wrangler_toml_sets_observability_sampling` (parsed with `tomllib`: `head_sampling_rate` at most `0.1`, `invocation_logs` false); `test_deploy_docs_have_a_kill_switch_runbook` (`docs/deploy.md` contains `wrangler deploy --var KIT_KILL:1` and the matching off command, and the D1 quota table); `test_worker_vars_json_has_every_var_the_worker_reads` (parse `worker/src/kit/env.ts` var names and compare with `worker-vars.json` keys plus the defaults list; a var read but not documented fails).
- [ ] **Step 2: Run** `pytest tests/site/test_privacy.py tests/kit/test_deploy_wiring.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the disclosure items and the workflow steps; the workflow reads `worker-vars.json` with `jq` into `--var` flags. In `docs/deploy.md` add the maintainer steps: create the KV namespace, the Turnstile site (hostnames limited to the test host), the Mailgun sending subdomain with SPF, DKIM and DMARC, and `wrangler secret put` for each secret name in `KitEnv`. Note the KV page is eventually consistent for up to about a minute after an upload.
- [ ] **Step 4: Run** the tests plus `actionlint .github/workflows/deploy-test.yml`. Expected: PASS, no findings.
- [ ] **Step 5: Commit** `feat: disclose and deploy the media kit gate`.

### Task 11: Owner lead deletion command

**Files:**
- Create: `src/reach_around/leads/__init__.py`, `src/reach_around/leads/delete.py`, `tests/leads/test_delete.py`
- Modify: `src/reach_around/cli.py` (`leads delete`), `docs/deploy.md` (token scope)

**Interfaces:**
- Consumes: `tests/fixtures/email_vectors.json` (Task 5); `KitSettings.d1_database_id`.
- Produces: `normalize_email(raw: str) -> str` (raises `ValueError`; same vectors as the Worker); `class D1Client(Protocol): def execute(self, sql: str, params: list[object]) -> int` (rows changed); `CloudflareD1(account_id: str, database_id: str, token: str, *, base_url: str = "https://api.cloudflare.com/client/v4")`; `delete_lead(email: str, *, d1: D1Client, local_db: Path | None) -> DeleteResult`; `DeleteResult(worker_rows: int, local_rows: int)`. CLI: `reach-around leads delete --config PATH --email ADDRESS --yes`, reading `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` from the environment.

- [ ] **Step 1: Write failing tests:**
  - `test_normalize_email_matches_shared_vectors` (iterates the fixture: every valid pair and every invalid input).
  - `test_delete_runs_one_parameterized_statement` (fake `D1Client` records `("DELETE FROM leads WHERE email = ?", ["a@example.com"])`; the address is lowercased first).
  - `test_delete_also_removes_from_local_leads_table` (temp SQLite with `leads(email)`; two rows differing in case are both removed; other rows stay); `test_missing_local_table_counts_zero`; `test_local_db_none_skips_local`.
  - `test_cli_requires_yes` (without `--yes`, exit code `2`, no client call); `test_cli_output_never_contains_the_address` (capsys); `test_cli_missing_token_env_exits_with_message_without_value`.
  - `CloudflareD1` against a local `http.server`: `test_sends_bearer_token_and_json_body`; `test_redirect_is_not_followed_and_token_not_sent_to_second_host`; `test_5xx_retried_at_most_three_times_then_error` (deletes are idempotent); `test_error_message_contains_neither_token_nor_email`; `test_timeout_is_ten_seconds`.
- [ ] **Step 2: Run** `pytest tests/leads -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `urllib` and a no-redirect opener. The Worker schema (Task 4) cascades to events and links, so one statement suffices. Document in `docs/deploy.md` that this token needs D1 edit, a different and broader scope than the read-only pull token, and must be kept off CI.
- [ ] **Step 4: Run** `pytest tests/leads -v`. Expected: PASS.
- Task 21 extends `delete_lead` to scrub registered exports and backups and to reorder the steps so a failure leaves the addresses findable for a rerun. Keep `delete_lead`'s keyword-only signature extensible (`artifacts: Sequence[Artifact] = ()` arrives there).
- [ ] **Step 5: Commit** `feat: add the owner lead deletion command`.

### Task 12: Private full PDF

**Files:**
- Create: `src/reach_around/kit/pdf.py`, `templates/kit_pdf.html`, `tests/kit/test_pdf.py`
- Modify: `pyproject.toml` (extra `pdf = ["weasyprint", "pypdf"]`, pinned with hashes in the dev requirements), `src/reach_around/cli.py` (`kit-pdf`), `docs/education/the-gate.md` (PDF section)

**Interfaces:**
- Consumes: `MediaKit`, `check_kit_display` (Task 1).
- Produces: `render_kit_pdf(config: SiteConfig, out_file: Path, *, today: date, include_owner: bool = False) -> PdfResult`; `PdfResult(pages: int, bytes: int)`; `refuse_remote(url: str, root: Path) -> None` (raises `ValueError` for any scheme except `file`, for any resolved path, symlinks followed, outside `root`, and for a `file:` URL with a host; `root` is the config directory here and the staged directory after Task 19); CLI `reach-around kit-pdf --config PATH --out FILE [--include-owner]`.

- [ ] **Step 1: Write failing tests** (`pytest.importorskip("weasyprint")`, marked `pdf`):
  - `test_pdf_contains_public_and_gated_text` (extract with pypdf: the canary rate, partner note and a public metric label are present).
  - `test_pdf_has_no_gate_artifacts` (text contains none of "Turnstile", "Unlock", "Enter your email", "Send me a link").
  - `test_owner_metrics_excluded_by_default_and_included_with_flag` (flag run also prints a warning "do not share" to stderr).
  - `test_policy_error_refuses_render`.
  - `test_refuse_remote_blocks_http_https_ftp_data_and_parent_paths`; `test_refuse_remote_follows_symlinks_out_of_root` (a symlink inside `root` pointing to a file outside it is refused); `test_refuse_remote_blocks_file_url_with_host_and_unc_forms`.
  - `test_remote_font_face_and_css_import_are_refused` (a template with `@font-face { src: url(https://example.invalid/f.woff2) }` and `@import url(...)` makes the render raise and the fetcher counts the refusals); `test_pdf_link_annotations_are_never_fetched` (a partner `https` URL is allowed as link text and annotation but the fetcher is called with no `http` URL); `test_dns_lookup_is_also_blocked` (patch `socket.getaddrinfo` to fail the test if called, alongside `socket.socket.connect`).
  - `test_render_makes_no_network_call` (a template with an external `<img src="https://example.invalid/x.png">` fails the fetcher and the render raises; patch `socket.socket.connect` to fail the test if called).
  - `test_refuses_to_write_into_a_site_output_dir` (target directory containing `index.html` or `manifest.json`, or equal to a path passed as `--site-out`).
  - `test_built_site_and_gated_bundle_contain_no_pdf_or_download_link` (run `build_site` and `build_kit`; no file ends in `.pdf`; no `download` attribute; no string `kit.pdf`).
  - `test_pdf_has_at_least_one_page_and_creation_date_is_today`.
- [ ] **Step 2: Run** `pytest tests/kit/test_pdf.py -v`. Expected: FAIL (or skipped if the extra is missing; install it first with `pip install -e '.[pdf]'`).
- [ ] **Step 3: Implement** with WeasyPrint and a custom `url_fetcher` calling `refuse_remote`; the template reuses the gated page's content blocks with print CSS and no form controls or blur. If the shared `python-ci` workflow cannot install Pango, add a local `pdf-tests` job and file a GYST issue for an apt-packages input rather than copying a workaround.
- [ ] **Step 4: Run** `pytest tests/kit -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: render the private full media kit PDF locally`.

### Task 13: Abuse suite, mutation check and threat model

**Files:**
- Create: `worker/test/abuse/harness.ts`, `ab1-spam-relay.test.ts`, `ab2-quota.test.ts`, `ab3-gate-bypass.test.ts`, `ab4-token.test.ts`, `ab5-forwarded-link.test.ts`, `ab6-fake-emails.test.ts`, `ab7-bots.test.ts`, `ab8-privacy.test.ts`, `ab9-alert-abuse.test.ts`, `ab10-cost.test.ts`, `ab12-retention.test.ts`, `tests/kit/test_pdf_abuse.py` (AB-11), `worker/controls.json`, `worker/scripts/mutate.mjs`, `docs/security/plan-3-abuse-cases.md`, `docs/education/email-abuse.md`, `tests/test_threat_model_doc.py`
- Modify: `worker/package.json` (`test:abuse`, `mutation-check`), `.github/workflows/ci.yml` (job `mutation-check` in the `CI green` needs list), `docs/security/threat-model.md` from PR #54 (one "Plan 3 status" line under each of T-01, T-03, T-04, T-12 and T-14 naming the AB cases and controls below; PR #54 must be merged first, and if it is not, stop and ask rather than creating a second threat model)

**Interfaces:**
- Consumes: the whole Worker. `harness.ts` exports `makeEnv(overrides?)`, `post(path, form, opts?)`, `get(path, opts?)`, `fakeFetch` (records Mailgun, Turnstile and sink calls), `issuedCookie(res)`.
- `controls.json`: `{ "<id>": { "files": ["worker/src/kit/…"], "sites": <n>, "abuse": ["AB-1", …] } }` for ids `turnstile`, `origin`, `bot-ua`, `ip-minute`, `ip-daily`, `address-day`, `budget`, `email-validate`, `single-use`, `token-signature`, `token-expiry`, `lead-exists`, `cookie-only` (Tasks 3 to 9) and, added by revision 2, `link-binding`, `code-attempts` (Task 14), `body-cap`, `kill-switch` (Task 16), `route-budget`, `lead-cap`, `view-debounce` (Task 17), `log-cap` (Task 18), `lead-hard-max`, `lead-unverified` (Task 20): 23 ids. An id may have several call sites (`origin` guards the gate, the send and the verify routes); `sites` is how many.

Abuse cases (these are the threat-model rows; each file asserts end to end through `worker.fetch`):

| Id | Threat | Suite asserts |
|---|---|---|
| AB-1 | Spam relay | no Mailgun call without Turnstile; no call for multi-recipient or header-injected addresses; one fixed template; one message per address per UTC day; per-IP cap |
| AB-2 | Quota exhaustion | 100 requests across 100 addresses stop at `MAIL_DAILY_BUDGET`; the gate still unlocks when the budget is spent |
| AB-3 | Gate bypass | every route and method without a cookie returns no canary; token in query, header or form is ignored; the canary is absent from the Pages output (reads the Task 2 build); unknown KV paths 404; every `/k/` response carries `private, no-store` and never `public` or `s-maxage` |
| AB-4 | Token tampering and replay | altered payload, signature, key, expiry, `lid`; a token for a deleted lead; a cookie replayed after `exp`; the nonce used as a token |
| AB-5 | Forwarded or reused link | GET and HEAD never consume (scanner prefetch), POST with a missing, reused or expired nonce, other client, concurrent POSTs, wrong origin all land on the gate; no URL anywhere carries a token. Per binding mode (run the whole case as a table over `cookie_pair`, `code`, `expiry_alert`): a copy redeemed from a second browser first lands on the gate in `cookie_pair` and `code` and leaves the link usable by the requester; a forwarded copy with a guessed code dies after five attempts; in `expiry_alert` the first redeemer wins and the owner alert fires (the documented residual); an unknown or unset `LINK_BINDING` sends no mail |
| AB-6 | Fake emails | invalid syntax rejected; client-supplied `verified`/`lid`/`country` ignored; `verified_ts` set only by a link click; unverified leads are distinguishable in the table |
| AB-7 | Bot traffic | bot and missing user agents, wrong origin, missing Turnstile, and bursts produce no lead, no event, no mail, and the same redirect as any other failure |
| AB-8 | Personal data | no IP column or value in any table (scan all rows for `203.0.113.`); no email, token or nonce in any `console` call across all flows; no response body contains the submitted email |
| AB-9 | Alert abuse | a view flood produces at most one alert per lead per 6 hours and at most `ALERT_DAILY_CAP` per day; hostile text cannot mention or inject into a sink message |
| AB-10 | Cost exhaustion | an oversized or lying-length body is rejected before any read, parse, D1 or Turnstile call; refused requests make zero D1 statements; the hourly route budgets stop writes at their limit and refused budget calls write nothing; 1000 views make one event row; the lead cap holds under 20 concurrent distinct addresses; `KIT_KILL` flips per request and stops all `/k/` work while the purge still runs; a flood of hostile inputs emits at most `LOG_MAX_PER_MINUTE` log lines drawn from a closed set |
| AB-11 | Private PDF leakage | the renderer makes no network or DNS call; renders only the staged copy, which has no lead tables; output has no user name, host name, temp path or tool version; the temp dir is 0700 and gone after success, error, interrupt and SIGTERM; the output path is explicit, outside the repo and every build output, and written 0600 atomically. Lives in `tests/kit/test_pdf_abuse.py`, which re-runs the Task 12 and 19 guards end to end through the CLI |
| AB-12 | Retention and deletion | a lead older than the hard maximum is purged whatever its activity; an unverified lead is purged at its shorter window; the Worker purge is batched and heartbeat-recorded; the owner purge dry run changes nothing and equals the real run; deletion reaches the local store and every registered export and backup, in the order that keeps a rerun possible |

- [ ] **Step 1: Write the suite** (each case above becomes one or more named tests in its file, reusing assertions from Tasks 3 to 9 only where the end-to-end path adds nothing; AB-3's build check shells out to `reach-around build` on the example config and greps its output directory).
- [ ] **Step 2: Write `worker/scripts/mutate.mjs`.** For each id in `controls.json`: fail if `// CONTROL:<id>` is not found exactly `sites` times in the listed `files`, or any such line is not a single statement; mutate each site in its own run; copy `worker/` to a temp directory; comment out that line; run `npm run test:abuse`; require a non-zero exit; print `ok <id> site <k>/<sites> (caught by <n> failing tests)`. Exit non-zero if any control survives, or if the count of `// CONTROL:<id>` lines differs from `sites`. No test-only switches exist in production code; the mutation happens only in the temp copy.
- [ ] **Step 3: Run** `npm run test:abuse` then `npm run mutation-check`. Expected: abuse suite PASS; every control line `ok`, final `23 controls, <sites> sites, all caught`. A surviving control means a missing test: add it to the owning AB file, not to the script.
- [ ] **Step 4: Write `docs/security/plan-3-abuse-cases.md`** (abuse cases AB-1 to AB-12 with mitigation, test file and control id, the T-numbers each answers, ranked risks, residual risks; the assets, actors and trust boundaries stay in PR #54's `threat-model.md`, which gets only the status lines) and `docs/education/email-abuse.md`. Residual risks to state plainly: a mail scanner that prefetches the link only loads the confirmation page and cannot burn it, but a scanner that submits forms would, and the visitor then lands on the gate (fail-safe, not a leak); a stolen cookie works until it expires or the lead is deleted; the 24-hour fixed lifetime means a lost cookie cannot be refreshed by an attacker; an attacker can cause at most one email per victim address per day within the global budget; a Turnstile outage locks the gate, so the owner sends the PDF directly; the gated page is cached at the edge for about a minute after an upload; Cloudflare and Mailgun see the addresses they process. Revision 2 residuals: in `expiry_alert` mode a forwarded link redeemed first unlocks and only alerts; a stolen unlock cookie works until it expires or the lead is deleted or revoked; a distributed flood can still spend the hourly route budgets and lock real visitors out until the next hour (the kill switch and the budgets cap cost, not availability); a patient attacker with a human farm can still create leads up to the route budget and `MAX_LEADS`; the PDF isolation falls back to the fetcher guard where `unshare` is unavailable; a PDF the owner copies elsewhere is outside every control here; provider-side copies (Cloudflare recovery windows, Mailgun logs, mailboxes) and unregistered backups are outside deletion, and opaque registered backups are deleted only at their roll-off date.
- [ ] **Step 5: Write `tests/test_threat_model_doc.py`** against `docs/security/plan-3-abuse-cases.md`: `test_every_abuse_case_names_an_existing_test_file` (parse `AB-n` rows, assert each cited file exists), `test_every_control_in_controls_json_is_documented`, `test_every_ab_case_in_the_table_above_is_in_the_doc` (AB-1 to AB-12), `test_threat_model_status_lines_cite_existing_ab_cases` (each T-01, T-03, T-04, T-12 and T-14 section in `docs/security/threat-model.md` has a "Plan 3 status" line and every `AB-n` it cites exists), `test_every_CONTROL_marker_in_worker_src_is_in_controls_json` (the reverse of the mutation script's check).
- [ ] **Step 6: Run** `pytest tests/test_threat_model_doc.py -v`. Expected: PASS.
- [ ] **Step 7: Commit** `test: add the gate abuse suite, mutation check and threat model`.


### Task 14: Unlock-link binding (revision 2)

Closes the gap that "not forwardable" is not enforced by a single-use link. Implements all three binding modes in Open decisions D1 behind one setting; the maintainer picks, the build refuses until they do.

**Files:**
- Create: `worker/src/kit/binding.ts`, `worker/test/kit/binding.test.ts`
- Modify: `src/reach_around/kit/models.py` (`KitSettings`: `link_binding: Literal["cookie_pair","code","expiry_alert"] | None = None`, `link_ttl_minutes: int = 60`, 5..1440), `src/reach_around/kit/build.py` (vars `LINK_BINDING`, `LINK_TTL_SECONDS = link_ttl_minutes * 60`), `worker/src/kit/email.ts` (send and verify call the binding), `worker/src/index.ts`, `tests/kit/test_models.py`, `tests/kit/test_build.py`, `docs/education/email-abuse.md`

**Interfaces:**
- Consumes: Tasks 4 (columns `pending_hash`, `code_hash`, `attempts`, `send_country`), 7, 9.
- Produces:
  - `type LinkBinding = "cookie_pair" | "code" | "expiry_alert"`; `PEND_COOKIE = "__Host-ra_pend"`.
  - `newPending(): Promise<{ secret: string; hash: string }>` (32 random bytes, base64url 43 characters; hash is SHA-256 hex); `pendingCookie(secret: string, ttlSeconds: number): string`; `readPendingCookie(header: string | null): string | null` (null on duplicates, like `readUnlockCookie`).
  - `newCode(): string` (six digits from `crypto.getRandomValues` by rejection sampling, leading zeros kept); `codeMac(env: KitEnv, linkId: string, code: string): Promise<string>` (HMAC-SHA256 hex of `code:` + linkId under a key derived from `TOKEN_HMAC_KEY` with the label `"code"`).
  - `bindOnSend(env, mode, link: { id: string }): Promise<{ pendingSecret?: string; code?: string }>` stores `pending_hash` or `code_hash` and returns what the response must carry. It returns a dummy `pendingSecret` or `code` and stores nothing when no mail is sent (throttled, over budget), so the responses match.
  - `checkBinding(env, mode, request, linkId: string, form: { code: string | null }): Promise<"ok" | "fail" | "retry">`. `retry` only in `code` mode, a wrong code with attempts left.
  - `takeAttempt(db, linkId): Promise<boolean>`: `UPDATE email_links SET attempts = attempts + 1 WHERE id = ? AND used_ts IS NULL AND attempts < 5`, true when `meta.changes === 1`.
  - `handleCodePage` is not a route: the confirmation page from Task 9 renders a code field when the mode is `code`.
- Behavior: `checkBinding` runs before the consuming `UPDATE` of Task 9 and never consumes. A failed binding leaves the link unused so the real requester can still redeem it. `cookie_pair` compares SHA-256 of the pending cookie with `pending_hash`; `code` takes an attempt slot first, then compares `codeMac`; `expiry_alert` returns `ok`. `LINK_BINDING` missing or unknown makes `handleSend` send nothing (the same redirect) and `checkBinding` return `fail`. The guard lines are `if (!(await takeAttempt(env.DB, linkId))) return "fail"; // CONTROL:code-attempts` and, in `email.ts`, `if ((await checkBinding(...)) !== "ok") return toGate(); // CONTROL:link-binding`.
- Email copy by mode (all fixed text, no visitor text): `cookie_pair`: "This link works once, in the browser where you asked for it, for N minutes."; `code`: "This link works once, for N minutes. Enter the six digit code shown on the page where you asked for it."; `expiry_alert`: "This link works once, for N minutes." `N` is `LINK_TTL_SECONDS / 60`.

- [ ] **Step 1: Write failing tests.**
  - Python: `test_link_binding_required_when_send_link_true` (`KitSettings(send_link=True)` without it raises `ConfigError` whose message names `kit.link_binding` and `Open decisions D1`); `test_link_binding_not_required_when_send_link_false`; `test_expiry_alert_requires_ttl_at_most_30_minutes`; `test_link_ttl_bounds` (4 and 1441 raise, 5 and 1440 pass); `test_worker_vars_carry_binding_and_ttl_seconds` (`LINK_TTL_SECONDS == 3600` for 60 minutes).
  - Worker, run as one table over the three modes with a two-browser harness (`jarA`, `jarB`: separate cookie stores; Mailgun is the fake, the emailed URL is read from its captured text):
    - `cookie_pair: the requester's send response sets __Host-ra_pend with HttpOnly, Secure, SameSite=Lax, Path=/ and Max-Age equal to LINK_TTL_SECONDS and no Domain`; `the secret is not stored (email_links.pending_hash is 64 hex characters and differs from the cookie value)`; `a client-supplied __Host-ra_pend on /k/send is replaced by a fresh one`; `the response shape is identical for sent, throttled and over-budget (same status, same headers, a Set-Cookie of equal length)`; `the pending cookie does not unlock /k/view`.
    - `cookie_pair: forwarded copy`: `jarB` GETs the link (200, no cookie) then POSTs: `303 /kit/`, no `Set-Cookie`, `used_ts` still null, `verified_ts` still null. Then `jarA` POSTs: `303 /k/view` with `__Host-ra_kit`, `verified_ts` set. Then `jarB` posts the gate form and unlocks as its own lead (a second `unlock` event).
    - `code: send returns 200 with a six digit code in the body, Cache-Control: private, no-store, the same status and body length for sent, throttled and over-budget`; `the stored code_hash is hex and the six digits appear nowhere in the table`; `GET /k/v/<nonce> renders a form with one numeric code field and still consumes nothing`; `forwarded copy: POST without the code, with 000000 or with a wrong code leaves used_ts null`; `the requester's code unlocks: 303 /k/view`; `the code read off the first screen and typed on a second device unlocks there` (documented as intended); `five wrong codes: the first four get 200 with the fixed "did not match" text, the fifth and every later POST get 303 /kit/, and the correct code after that also gets 303 /kit/`; `ten concurrent wrong POSTs record attempts == 5 exactly and evaluate five codes` (spy on `codeMac`); `newCode 2000 samples: all match ^\d{6}$, the mean digit is within 0.3 of 4.5, a forced all-zero random source yields "000000"`.
    - `expiry_alert: no pending cookie is set, jarB redeems first and wins` (this documents the residual), and `a ttl above 30 minutes is refused in the Worker too (LINK_TTL_SECONDS 1801 with expiry_alert: send mails nothing)`.
    - All modes: `unknown or missing LINK_BINDING sends no mail and redeems nothing`; `the link expires: a POST at expires_ts is refused, one second earlier is accepted`; `a failed binding does not consume the link`; `email text by mode equals the three fixed strings with N = 60`; `no console call contains the code, the pending secret or the nonce`.
- [ ] **Step 2: Run** `pytest tests/kit/test_models.py -k link` and `npm test -- binding email` in `worker/`. Expected: FAIL.
- [ ] **Step 3: Implement.** Keep binding in `binding.ts`; `email.ts` calls it from one place in `handleSend` and one in `handleVerifyLink`. Read the attempt and compare in `code` mode with a fixed-length string compare via `crypto.subtle.verify` on the MAC, not `===`.
- [ ] **Step 4: Run** `npm test` and `pytest tests/kit -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: bind the emailed link to the browser that asked for it`.

### Task 15: Redemption and country-change alerts (revision 2)

**Files:**
- Create: `worker/test/kit/anomaly.test.ts`
- Modify: `worker/src/kit/alerts.ts`, `worker/src/kit/gate.ts`, `worker/src/kit/email.ts`, `src/reach_around/kit/models.py` (`KitSettings.country_change: Literal["ignore","alert","revoke"] = "alert"`), `src/reach_around/kit/build.py` (`COUNTRY_CHANGE`), `worker/test/kit/alerts.test.ts`, `tests/kit/test_models.py`

**Interfaces:**
- Consumes: Tasks 6, 8, 9, 14; token claim `cc` and `leads.session_floor_ts` (Tasks 3 and 4).
- Produces: `KitAlert.kind` gains `"redeem"` and `"country-change"`, with `detail: "country-match" | "country-differs" | "country-unknown"` for `redeem` (compares `email_links.send_country` with the redeeming request's country); `alertOnce` takes the kind and debounces `country-change` through `leads.last_cc_alert_ts` (6 hours); `redeem` alerts are not debounced (a link redeems once) but count against `ALERT_DAILY_CAP`. In `handleView`: if the token's `cc` and the request country are both non-empty and differ, then `ignore` serves normally; `alert` serves and alerts; `revoke` sets `leads.session_floor_ts = nowSec`, answers `303 /kit/` and clears nothing else.

- [ ] **Step 1: Write failing tests.**
  - `a successful link redemption alerts once with kind redeem` in every binding mode; `detail is country-differs when the send country was DE and the redeem country is US`, `country-match` when equal, `country-unknown` when either is null.
  - `a failed binding (a forwarded copy refused in cookie_pair or code mode) sends no redeem alert and no unlock alert`; `a refused attempt in expiry_alert mode cannot exist, so the redeem alert fires on the first redeemer even when it is not the requester (documents the residual)`.
  - view with a matching country: no alert, 200. Different country with `alert`: 200 plus exactly one `country-change` alert; a second view within 6 hours: no second alert; at 6 hours and 1 second: one more. Different country with `ignore`: 200, no alert. With `revoke`: `303 /kit/`, `session_floor_ts` equals now, and the same cookie is refused afterwards; a fresh gate post for that lead then yields a token that works (its `iat` is not below the floor); the old cookie still fails.
  - `empty cc or empty request country never counts as a change`.
  - `alert text contains only the two letter codes and the lead's verified flag, never the nonce, code or token`; `country values outside ^[A-Z]{2}$ are dropped before comparing`.
  - `an invalid COUNTRY_CHANGE value behaves as alert`.
  - Python: `test_country_change_default_is_alert_and_bad_value_refused`.
- [ ] **Step 2: Run** `npm test -- anomaly alerts`. Expected: FAIL.
- [ ] **Step 3: Implement.** The comparison is one function `countryChanged(cc: string, now: string | null): boolean`.
- [ ] **Step 4: Run** `npm test` and `pytest tests/kit -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: alert the owner on link redemption and country change`.

### Task 16: Request cost guard: body cap and kill switch (revision 2)

**What `worker/src` already has (from Plan 1), and what it lacks.** The click endpoint is already cheap by design: every outcome is one empty 204; the id allowlist, user-agent length cap (512), bot filter and origin check run before any write; a per-client `CLICK_LIMITER` binding runs before the insert; one `INSERT` per accepted click; no body is read at all; a `try/catch` makes failures look like success; `purgeOldEvents` deletes click rows daily with a bound parameter and refuses non-positive windows. It has no body-size cap (not needed, nothing is read), no global per-route budget, no D1 write or row cap, no logging at all (so no log quota problem yet), and no kill switch. The `/k/` routes in this plan read bodies, call Turnstile and Mailgun, and write several rows, so they need all of these.

**Files:**
- Create: `worker/src/kit/cost.ts`, `worker/test/kit/cost.test.ts`
- Modify: `worker/src/kit/input.ts` (`readForm` calls `readBounded`), `worker/src/index.ts` (kill switch first), `worker/src/click.ts` (`CLICKS_KILL`), `worker/src/kit/env.ts`, `docs/deploy.md` (runbook)

**Interfaces:**
- Produces: `readBounded(request: Request, maxBytes: number): Promise<string | null>`; `killed(env: { KIT_KILL?: string }): boolean`; `killedResponse(): Response`.
- `readBounded`: null without reading when `Content-Length` is absent, not a non-negative integer, or above `maxBytes`; otherwise reads the stream chunk by chunk, counts bytes, and on reaching `maxBytes + 1` cancels the stream and returns null (a lying `Content-Length` does not help); decodes with `TextDecoder("utf-8", { fatal: true })` and returns null on invalid UTF-8.
- `killed` reads `env.KIT_KILL` at call time (no module-level cache); true for `"1"` or `"true"` (case-insensitive), false otherwise.
- `killedResponse`: `503`, `Retry-After: 3600`, `Cache-Control: private, no-store`, `Content-Type: text/html; charset=utf-8`, CSP `default-src 'none'`, a fixed body `<!doctype html><title>Unavailable</title><p>Temporarily unavailable.` and nothing else.

- [ ] **Step 1: Write failing tests.**
  - `content-length above the cap returns null and the body stream is never pulled` (a `ReadableStream` with a `pull` spy: pulls == 0).
  - `no content-length returns null and nothing is pulled`; `non-integer, negative or duplicated content-length returns null`.
  - `content-length says 10 but 5000 bytes arrive: returns null and at most maxBytes + one 512 byte chunk were pulled, and the stream was cancelled` (cancel spy).
  - `exactly maxBytes is returned, maxBytes + 1 is null`; `invalid UTF-8 returns null`; `a cap of 4096 is read from MAX_BODY_BYTES and a value above 65536 or below 64 is clamped into that range`.
  - `an oversized body on /k/gate, /k/send and POST /k/v/<nonce> makes zero DB statements, zero Turnstile and zero Mailgun calls, and returns the generic failure redirect` (spies on `env.DB.prepare` and `fetchFn`).
  - kill switch engaged (`"1"`, `"true"`, `"TRUE"`): every method (GET, HEAD, POST, OPTIONS) on `/k/gate`, `/k/send`, `/k/view`, `/k/v/<nonce>`, `/k/anything` returns the fixed 503 with the five headers above; zero `DB.prepare`, zero `KIT.get`, zero fetch calls; the request body stream is never pulled. Not engaged (`""`, `"0"`, `"false"`, `"yes"`, undefined): behavior unchanged.
  - `the flag is read on every request`: one imported module, `env.KIT_KILL` toggled between calls: 303, then 503, then 303 again with no re-import.
  - `non-/k/ paths are unaffected by KIT_KILL`; `the scheduled handler still purges while KIT_KILL is set` (events, kit data and leads rows are deleted).
  - `CLICKS_KILL engaged: POST /e/c/<id> from an allowed origin returns the usual 204 and inserts nothing`.
  - `the kill switch response has no Set-Cookie and never mentions the flag name`.
- [ ] **Step 2: Run** `npm test -- cost`. Expected: FAIL.
- [ ] **Step 3: Implement.** `checkLength(request, maxBytes): boolean` is the header-only step 4 of the Guard order and runs before the per-client limiter; `readBounded` is step 7. The streaming limit is a single line ending `// CONTROL:body-cap`; the router's first statement is `if (killed(env)) return killedResponse(); // CONTROL:kill-switch`. Document in `docs/deploy.md`: `npx wrangler deploy --var KIT_KILL:1` turns the kit off, `--var KIT_KILL:0` turns it on; both take effect on the next request after the deploy lands, and the committed `wrangler.toml` keeps it unset.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: cap request bodies and add a kill switch`.

### Task 17: Route budgets and storage caps (revision 2)

**Files:**
- Create: `worker/src/kit/budgets.ts`, `worker/test/kit/budgets.test.ts`, `src/reach_around/kit/budget_check.py`, `tests/kit/test_budget_check.py`
- Modify: `worker/src/kit/gate.ts`, `worker/src/kit/email.ts`, `worker/src/kit/leads.ts` (lead cap, view debounce), `src/reach_around/kit/models.py` (`KitSettings.route_budgets: RouteBudgets`, `max_leads: int = 5000` 1..100000, `d1_daily_write_quota: int = 100000`), `src/reach_around/kit/build.py` (vars `ROUTE_BUDGETS`, `MAX_LEADS`), `docs/deploy.md` (D1 and log quota table)

**Interfaces:**
- Produces: `utcHour(nowMs: number): string` (`2027-03-14T05`); `reserveRoute(env: KitEnv, route: "gate" | "send" | "link" | "view", nowMs: number): Promise<boolean>` (calls `reserveBudget(db, "route:" + route, utcHour(nowMs), limit)` from Task 7; keys are purged with the other counters after 2 days); `upsertLead` returns `{ id: string; created: boolean } | { full: true }` and inserts a new lead only when `(SELECT COUNT(*) FROM leads) < ?`; `recordViewOnce(db, leadId, nowMs, minIntervalMs): Promise<boolean>` is `UPDATE leads SET last_view_event_ts = ?, last_seen_ts = ? WHERE id = ? AND (last_view_event_ts IS NULL OR last_view_event_ts <= ?)` and true when `meta.changes === 1`; the view event row is written only when it returns true. Python: `RouteBudgets(gate_per_hour: int = 150, send_per_hour: int = 50, link_per_hour: int = 100, view_per_hour: int = 300)`, each 0..100000; `WRITES_PER_OP = {"gate": 5, "send": 7, "link": 7, "view": 2}`; `PURGE_RESERVE = 10_000`; `worst_case_daily_writes(settings: KitSettings) -> int` = `24 * sum(budget * WRITES_PER_OP) + PURGE_RESERVE`; `check_d1_budget(settings) -> list[str]` returns an error when above `d1_daily_write_quota` and a warning above 70 percent. (The default 100000 is the maintainer's figure for the D1 free plan and must be re-checked against Cloudflare's current limits; the setting exists so a paid plan is one number.)

- [ ] **Step 1: Write failing tests.**
  - `utcHour at 2027-03-14T05:59:59Z is 2027-03-14T05 and one second later 2027-03-14T06`.
  - `reserveRoute grants exactly the hourly limit then refuses` (limit 3); `fifty concurrent requests with limit 5 grant exactly five`; `resets at the next UTC hour`; `limit 0 refuses`; `routes do not share a count`.
  - `a refused reservation writes nothing`: after the limit, a further call reports `meta.rows_written === 0` in the test D1 (the live D1 meaning of `rows_written` for a no-op conditional upsert is NOT VERIFIED; the post-deploy smoke test in Task 10 measures it).
  - `over budget returns the generic failure on all four routes` (`303 /kit/retry/`, no body, no Turnstile call, no Mailgun call, no lead row).
  - ordering (the Contracts guard order): `requests refused by kill switch, method, origin, user agent, Content-Length or the per-client limiter make zero D1 statements` (spy count 0 for each cause); `the route budget is reserved before Turnstile` (an over-budget request with a failing Turnstile fake still makes zero Turnstile calls).
  - lead cap: `with MAX_LEADS 5 and 20 concurrent distinct addresses exactly five rows exist`; `an existing address at the cap still unlocks and its last_seen_ts updates`; `a refused new address gets the generic redirect and no cookie`; `the owner is alerted once per UTC day with kind capacity, however many are refused`.
  - view debounce: `1000 sequential views of a valid cookie within one hour write exactly one view event`, `sum of meta.rows_written across the 1000 views is at most 2`; `a view 3600 s and 1 ms after the first writes a second event, one at 3599 s does not`.
  - measured write bound: `each successful operation writes at most WRITES_PER_OP[route] rows` (gate with a new lead, send with mail, link redemption, first view; counted from `rows_written`).
  - Python `budget_check`: `defaults are below the 70 percent warning` (the test spells out the arithmetic `24 * (150*5 + 50*7 + 100*7 + 300*2) + 10000 = 67600` and asserts the function returns it, 67.6 percent of the default quota); `raising send_per_hour until the worst case exceeds the quota is an error naming the setting`; `a quota raised to 1000000 passes`; `build_kit raises ConfigError on the error and returns the warning in warnings`; `route budget of 0 is allowed and means the route always answers the generic failure`.
- [ ] **Step 2: Run** `npm test -- budgets` and `pytest tests/kit/test_budget_check.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Guard call sites are single lines ending `// CONTROL:route-budget`, `// CONTROL:lead-cap` (the `full` branch return) and `// CONTROL:view-debounce` (the `if (!(await recordViewOnce(...))) return served;` line). View serving still returns the page when debounced; only the writes are skipped. Compute the table in `docs/deploy.md` from `worst_case_daily_writes` by a docs test.
- [ ] **Step 4: Run** `npm test` and `pytest tests/kit -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add hourly route budgets, a lead cap and a view debounce`.

### Task 18: Bounded structured logs (revision 2)

**Files:**
- Create: `worker/src/kit/log.ts`, `worker/test/kit/log.test.ts`
- Modify: every `console.*` call site in `worker/src` (replace with `logEvent`), `worker/wrangler.toml` (`[observability]`)

**Interfaces:**
- Produces: `type LogEvent = "gate" | "send" | "link" | "view" | "alert" | "purge" | "budget" | "kill" | "log"`; `type Outcome = "ok" | "rejected" | "throttled" | "over_budget" | "closed" | "failed" | "dropped"`; `type Reason = "origin" | "bot_ua" | "size" | "turnstile" | "email" | "ip_minute" | "route_budget" | "address_day" | "mail_budget" | "binding" | "single_use" | "expired" | "lead_cap" | "kill" | "provider" | "other"`; `logEvent(event: LogEvent, outcome: Outcome, reason?: Reason, n?: number): void`. It prints `JSON.stringify({ e, o, r, n })` through `console.log` only, with unknown enum values replaced by `other` and `n` kept only for `purge` and `log` as a non-negative integer. Per-isolate cap `LOG_MAX_PER_MINUTE` (default 60, from env at first use, clamped 1..600); lines over the cap are counted and one `{"e":"log","o":"dropped","n":<count>}` is emitted when the next minute starts. `wrangler.toml` sets `[observability] enabled = true`, `head_sampling_rate = 0.1` and `[observability.logs] invocation_logs = false` (verify the key names against current wrangler docs in Step 3).
- No log line ever includes a request-derived string. Errors are logged as `logEvent(event, "failed", "other")`; the error message is dropped.

- [ ] **Step 1: Write failing tests.**
  - `a line is the JSON of exactly the allowed keys` and `at most 120 bytes`.
  - `unknown event, outcome or reason values become other and never appear verbatim` (feed an email, a 10 KiB string, `"__proto__"`, an object).
  - `n is dropped for events other than purge and log and for negative, fractional or non-numeric values`.
  - flood: `10000 calls with random hostile strings emit at most LOG_MAX_PER_MINUTE lines in the first minute (fake clock)`; `the distinct line set is at most 64`; `after the minute rolls, one dropped summary with the exact count is emitted, then normal lines resume`; `LOG_MAX_PER_MINUTE of 0 or 100000 is clamped`.
  - `an Error whose message is "a@b.com" thrown inside each handler (gate, send, link, view) produces log lines containing no "@"`.
  - `no console call outside log.ts`: a test reads every file under `worker/src` and fails on `console.` outside `log.ts`.
  - `no console.error or console.warn is ever called` (error stacks leak).
  - `wrangler.toml observability` parsed with a TOML reader: `head_sampling_rate` at most 0.1, `invocation_logs` false.
  - `the budget and kill events are logged at most once per minute per isolate each` (a flood of refused requests yields one `budget` line and one `kill` line per minute).
- [ ] **Step 2: Run** `npm test -- log`. Expected: FAIL.
- [ ] **Step 3: Implement.** The cap check is one line ending `// CONTROL:log-cap`. Record in `docs/deploy.md` that the free logging allowance is the maintainer's figure to verify and that sampling plus the cap keep a flood well under any daily log quota (worst case in one isolate: 60 lines per minute, 86400 per day; many isolates multiply it, which is why sampling is also set).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add a bounded structured logger`.

### Task 19: PDF pipeline: staged copy, scrub, owner-only temp dir, explicit output (revision 2)

Task 12 renders; this task seals the pipeline around it. The renderer works on a staged copy, never on the live config or store.

**Files:**
- Create: `src/reach_around/kit/stage.py`, `workdir.py`, `output.py`, `isolate.py`, `tests/kit/test_pdf_stage.py`, `test_pdf_workdir.py`, `test_pdf_output.py`, `test_pdf_isolate.py`
- Modify: `src/reach_around/kit/pdf.py` (use the pieces; `refuse_remote(url, root=stage_dir)`), `src/reach_around/cli.py` (`kit-pdf`: `--out FILE` required, `--overwrite`, `--isolation {auto,require,off}`), `pyproject.toml` (`pdf` extra adds `Pillow`, which WeasyPrint already requires; list it so the pin is explicit), `docs/education/the-gate.md` (PDF section)

**Interfaces:**
- `private_workdir(prefix: str = "ra-pdf-") -> ContextManager[Path]`: parent is `$XDG_RUNTIME_DIR` when it exists, is owned by the user and is mode 0700, else `tempfile.gettempdir()`; the directory is created 0700 with `umask 0o077` in force, owner and mode are re-checked after creation, and it is removed on every exit path: normal, exception, `KeyboardInterrupt`, and `SIGTERM` (the context installs a handler that raises `SystemExit`, restoring the old handler on exit). Refuses a symlinked parent, and a world-writable parent without the sticky bit.
- `stage_inputs(config: SiteConfig, config_dir: Path, work: Path, *, store: Path | None, include_owner: bool, include_leads: bool = False) -> StagedInputs`: copies by allowlist only: a configuration written from `config.model_dump()` restricted to `site` identity, `media_kit` (owner-audience metrics removed unless `include_owner`) and the theme images the kit uses; nothing else from the config directory. A store is copied with SQLite's backup API; unless `include_leads`, every table named `leads`, `kit_events`, `email_links` or starting with `lead` is dropped and the file is `VACUUM`ed with `PRAGMA secure_delete=ON`. `include_leads` is not exposed on the CLI in this plan (the PDF template never reads lead data; Phase 4 may add a flagged report). `StagedInputs(root: Path, config_file: Path, store: Path | None)`.
- `scrub_image(src: Path, dst: Path) -> None`: Pillow re-encode that drops EXIF, ICC, XMP and text chunks; SVG is accepted only when it has no `<script>`, `<image>`, `<foreignObject>`, `@import`, or `href`/`url(` to anything but a `#fragment`, else `ValueError`.
- `scrub_pdf_metadata(data: bytes, *, title: str, today: date) -> bytes`: rewrites with pypdf so the Info dictionary has only `/Title`, `/CreationDate` (`D:YYYYMMDD000000Z`, day precision) and, if pypdf insists, `/Producer` equal to `Reach Around`; drops `/ModDate`, `/Author`, `/Creator`, `/Subject`, `/Keywords` and the catalog `/Metadata` stream.
- `resolve_output(out: Path, *, config_dir: Path, site_out: Path | None, gated_out: Path | None, overwrite: bool) -> Path` and `write_private(path: Path, data: bytes) -> None` (temp name in the same directory opened with `O_CREAT | O_EXCL | O_NOFOLLOW`, mode 0600, `fsync`, then `os.replace`).
- `isolation_command(argv: list[str], *, mode: str, which=shutil.which, probe=...) -> tuple[list[str], str | None]`: for `auto` and `require`, wraps `argv` in `unshare --net` when a probe run of `unshare --net true` succeeds; returns a warning string when it falls back (`auto`) and raises `RuntimeError` when `require` cannot isolate. `child_env(work: Path) -> dict[str, str]`: only `PATH`, `LANG=C.UTF-8`, `HOME=<work>`, `TMPDIR=<work>`; nothing else is inherited.

- [ ] **Step 1: Write failing tests.**
  - workdir: `mode is 0700 and owned by the current uid`; `a file created inside with open() is 0600 (umask)`; `removed after success`, `after an exception`, `after KeyboardInterrupt`, and `after SIGTERM` (a child run with `python -I` creates the dir, writes a marker, sends itself SIGTERM; the parent asserts the directory is gone and the exit status is non-zero); `a listing of the parent is identical before and after`; `the directory name is the prefix plus random characters, with no title or user name`; `refuses a parent that is a symlink, and a 0777 parent without the sticky bit`.
  - staging: `test_staged_config_contains_only_allowlisted_keys` (a config with `privacy.contact`, `links`, `features`, `kit.d1_database_id` and `kit.turnstile_site_key` set: none appear in the staged file); `test_staged_store_has_no_lead_tables` (a synthetic store with `snapshots`, `leads`, `kit_events`, `email_links`, `lead_notes`: only `snapshots` remains; the address `partner@example.com` seeded in `leads` is absent from the staged file bytes, checked after `VACUUM`); `test_include_leads_true_keeps_lead_tables` (library only); `test_owner_metrics_stripped_unless_include_owner`; `test_staged_files_are_0600_and_inside_work`; `test_stage_refuses_paths_outside_config_dir` (a theme image `../x.png` or a symlink out is refused); `test_scrub_image_drops_exif_and_gps` (a JPEG built with Pillow carrying the comment `CANARY-EXIF-9c1d` and GPS tags: the staged copy has neither, bytes and `getexif()` both); `test_svg_with_script_or_remote_reference_refused`.
  - rendering end to end (`pdf` marked): `test_pdf_has_only_title_creationdate_and_fixed_producer` (set of Info keys is a subset of those three; no XMP); `test_pdf_bytes_contain_no_user_name_host_name_temp_path_config_path_or_tool_version` (`getpass.getuser()`, `socket.gethostname()`, `str(work)`, `str(config_dir)`, `os.path.expanduser("~")`, and the strings `WeasyPrint` and `pydyf`; if the test environment user name is shorter than 4 characters the test uses a synthetic `ra-test-user` set through `LOGNAME` and `HOME` instead); `test_pdf_bytes_do_not_contain_the_image_canary`; `test_creation_date_is_day_precision`; `test_render_reads_only_inside_the_staged_dir` (the fetcher is called with `root == stage root`; a template reference to the real config dir raises).
  - output: `test_out_is_required` (CLI exit 2, message names `--out`); `test_refuses_inside_config_dir_site_out_or_gated_out`; `test_refuses_a_dir_containing_index_html_or_manifest_json`; `test_refuses_inside_a_git_work_tree_unless_ignored` (temp repo; the path is refused, then accepted after a matching `.gitignore` entry, with the `.git` check done by walking parents and `git check-ignore -q` run with a fixed argv; if a `.git` is found and `git` is missing the output is refused); `test_refuses_existing_file_without_overwrite_and_replaces_with_it`; `test_refuses_symlink_target_and_non_pdf_extension`; `test_output_is_0600_and_written_atomically` (a crash injected between write and replace leaves no partial target); `test_stdout_is_exactly_the_absolute_path_and_one_newline` (warnings go to stderr); `test_include_owner_prints_do_not_share_to_stderr_only`.
  - isolation: `test_auto_wraps_in_unshare_net_when_probe_succeeds` (fake `which` and probe); `test_auto_warns_and_runs_unisolated_when_unavailable`; `test_require_raises_when_unavailable`; `test_off_never_wraps`; `test_child_env_has_only_the_four_keys` (seed `CLOUDFLARE_API_TOKEN`, `MAILGUN_API_KEY`, `TOKEN_HMAC_KEY` in the parent: absent in the child env); `test_child_runs_with_python_dash_I`.
- [ ] **Step 2: Run** `pytest tests/kit -v -k "pdf_stage or pdf_workdir or pdf_output or pdf_isolate"`. Expected: FAIL.
- [ ] **Step 3: Implement.** Pipeline in `render_kit_pdf`: `private_workdir` -> `stage_inputs` -> child render (isolated when possible) writing into the work dir -> `scrub_pdf_metadata` -> `resolve_output` -> `write_private`. On this machine confirm once by hand that `unshare --net true` works unprivileged (NOT VERIFIED here; the probe handles the case where it does not, and the flags are an assumption to check in this step).
- [ ] **Step 4: Run** `pytest tests/kit -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: seal the private PDF pipeline`.

### Task 20: Lead retention in the Worker: hard maximum and unverified window (revision 2)

Replaces the earlier rule that retention counts from last activity, which let a returning lead live forever and kept unverified third-party addresses as long as verified ones.

**Files:**
- Create: `worker/test/kit/retention-leads.test.ts`, `tests/fixtures/retention_vectors.json`
- Modify: `worker/src/retention.ts` (`purgeLeads`), `worker/src/index.ts` (scheduled handler), `src/reach_around/config/privacy.py` (`lead_retention_days` default 365, `le=730`; new `unverified_lead_retention_days: int = 30`, with a validator that it is at most `lead_retention_days`), `src/reach_around/kit/build.py` (`LEAD_RETENTION_DAYS`, `UNVERIFIED_RETENTION_DAYS`), `docs/config.md`, the privacy config tests under `tests/config/`. Plan 1 is not edited: it shipped `lead_retention_days` with a ceiling of 3650, so this task lowers the ceiling to 730 and says so in `docs/config.md` and the release notes

**Interfaces:**
- `purgeLeads(env: KitEnv, nowMs: number): Promise<{ leads: number; backlog: boolean }>`. A lead is expired when `created_ts <= now - LEAD_RETENTION_DAYS * DAY` or (`verified_ts IS NULL` and `created_ts <= now - UNVERIFIED_RETENTION_DAYS * DAY`). Deletes in batches: `DELETE FROM leads WHERE id IN (SELECT id FROM leads WHERE <expired> LIMIT 500)`, at most 20 batches per run; sets `kit_meta('purge_backlog', '1')` when a full batch remains and clears it otherwise; writes `kit_meta('last_purge_ts', nowMs)` and `kit_meta('last_purge_leads', n)` only when the run completes without throwing. `LEAD_RETENTION_DAYS` above 730 is clamped to 730; zero, negative, non-finite or non-integer throws `RangeError` and deletes nothing. `UNVERIFIED_RETENTION_DAYS` is clamped to at most the lead window and has the same invalid-value rule.
- `tests/fixtures/retention_vectors.json`: `{ "settings": {"lead_days": 365, "unverified_days": 30}, "rows": [{"age_days": 400, "seen_days_ago": 1, "verified": true, "expect": "purge"}, ...] }`, shared with Task 21's Python predicate.

- [ ] **Step 1: Write failing tests.**
  - vectors: every row of the fixture is inserted and the expectation checked (the fixture includes: verified 365 days old (purge, boundary is `>=`), verified 364 days (keep), verified 400 days but seen yesterday (purge: activity does not renew), unverified 30 days (purge), unverified 29 days (keep), verified 31 days (keep), unverified 400 days).
  - `a lead that verifies at day 20 gets the long window` and `markVerified does not change created_ts`.
  - `the hard maximum is created_ts, not last_seen_ts`; `a returning lead is not renewed` (upsert after day 300 leaves `created_ts` unchanged).
  - `the purge cascades to events and links`; `a purged lead's cookie gets 303 /kit/` (end to end through `worker.fetch`).
  - batching: `1200 expired leads are deleted in one run in three batches (500, 500, 200)`; `with 20 full batches remaining the run stops, reports backlog true and sets purge_backlog`; `the next run continues and clears it`.
  - values: `0, -1, NaN, 1.5, "abc" throw RangeError and delete nothing`; `1000 is clamped to 730`; `UNVERIFIED_RETENTION_DAYS above the lead window is clamped to it`.
  - heartbeat: `last_purge_ts is written on success and left unchanged when the purge throws`; `the scheduled handler runs purgeLeads even when purgeOldEvents throws` (`Promise.allSettled`).
  - Python: `test_privacy_lead_retention_ceiling_730` (731 raises), `test_unverified_default_30_and_may_not_exceed_lead_retention`, `test_worker_vars_carry_both_windows`, `test_docs_config_table_lists_both_keys` (the table in `docs/config.md` names both and the ceiling).
- [ ] **Step 2: Run** `npm test -- retention-leads` and `pytest tests/config -k privacy`. Expected: FAIL.
- [ ] **Step 3: Implement.** The two predicates are separate statements so the mutation check can tell them apart: `await deleteBatch(env, hardMaxPredicate) // CONTROL:lead-hard-max` and `await deleteBatch(env, unverifiedPredicate) // CONTROL:lead-unverified`. Update `docs/config.md` and note in the release notes that `lead_retention_days` now has a ceiling of 730 and counts from creation.
- [ ] **Step 4: Run** `npm test` and `pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: enforce a hard maximum and a short unverified window for leads`.

### Task 21: Owner purge command and artifact reach (revision 2)

Deletion must reach the Worker, the local store, exports and registered backups, in an order that lets a failed run be repeated. The command has a dry run.

**Files:**
- Create: `src/reach_around/leads/purge.py`, `src/reach_around/leads/artifacts.py`, `tests/leads/test_purge.py`, `tests/leads/test_artifacts.py`
- Modify: `src/reach_around/leads/delete.py` (artifacts; new order), `src/reach_around/cli.py` (`leads purge`), `src/reach_around/config/privacy.py` (`lead_artifacts`), `src/reach_around/site/privacy.py` and `tests/site/test_privacy.py` (name the roll-off window), `docs/config.md`, `docs/deploy.md` (runbook, token scope)

**Interfaces:**
- `Artifact(kind: Literal["export","backup"], path: str, format: Literal["csv","jsonl","sqlite","opaque"], max_age_days: int = 35)` in `privacy.lead_artifacts: list[Artifact]` (the 35-day default is a placeholder for D5; at most 20; `max_age_days` 1..365; a `path` is a glob, absolute or relative to the config directory; `..` segments and a leading `~` are refused; resolved matches must stay inside the glob's non-wildcard prefix after symlink resolution).
- `D1Client` gains `query(sql: str, params: list[object]) -> list[dict[str, object]]` (Task 11's `execute` stays).
- `is_expired(created_ts: int, verified_ts: int | None, now_ms: int, *, lead_days: int, unverified_days: int) -> bool`: the Python twin of Task 20's predicate, tested against `tests/fixtures/retention_vectors.json`.
- `scrub_artifact(artifact_path: Path, fmt: str, addresses: frozenset[str]) -> int` (rows removed): `csv` drops rows where any cell, normalized, is in the set and keeps the header; `jsonl` drops objects whose `email` normalizes into the set; `sqlite` runs `PRAGMA secure_delete=ON`, deletes from every table that has an `email` column, then `VACUUM`; `opaque` is never edited. Rewrites are atomic, 0600, and keep the file's directory.
- `plan_purge(*, d1, local_db, artifacts, config_dir, now_ms, lead_days, unverified_days) -> PurgePlan(worker_leads: int, local_leads: int, actions: list[ArtifactAction])` holds the address set in memory only; `ArtifactAction(path: Path, action: Literal["scrub","delete","retain"], rows: int, retained_until: date | None)`. An artifact older than its `max_age_days` is `delete` whatever its format; an `opaque` artifact younger than that is `retain` with its roll-off date; others are `scrub` when they contain a purged address.
- `execute_purge(plan, *, d1, local_db) -> PurgeResult`. Order: select the address set (Worker and local) -> scrub or delete artifacts -> delete from the local store -> delete from the Worker with the same predicate as Task 20, one parameterized statement -> append to `purge_log` in the local store. A failure at any step stops the run with exit 1 before later steps, so addresses remain findable and a rerun finishes. `delete_lead` uses the same order.
- CLI: `reach-around leads purge --config PATH [--yes] [--now ISO]`. Without `--yes` it is a dry run: prints counts and artifact lines, performs only `SELECT`s, exits 0. `--dry-run` is accepted and wins over `--yes`. Output never contains an address; paths and counts only. `retain` lines print `RETAINED until <date>` and the command still exits 0 on success.

- [ ] **Step 1: Write failing tests.**
  - `test_is_expired_matches_shared_vectors`.
  - dry run: `test_dry_run_changes_nothing` (hash every artifact and the local store file, and the fake D1 records only statements starting `SELECT`; identical hashes after); `test_dry_run_is_the_default_without_yes`; `test_dry_run_flag_wins_over_yes`; `test_dry_run_counts_equal_execute_counts` (same fixture, same `--now`); `test_output_never_contains_an_address` (capsys, with addresses in every store and artifact).
  - scope: `test_unverified_and_hard_max_both_applied_locally` (local `leads(email, created_ts, verified_ts)` rows from the vectors); `test_worker_delete_is_one_parameterized_statement` (the fake records `DELETE FROM leads WHERE created_ts <= ? OR (verified_ts IS NULL AND created_ts <= ?)` with two integers).
  - artifacts: `test_csv_scrub_keeps_header_and_other_rows_and_handles_quoted_commas`; `test_jsonl_scrub`; `test_sqlite_scrub_removes_rows_and_bytes` (after the scrub, `address.encode()` is not in the file bytes); `test_scrub_normalizes_case_and_plus_tag_like_the_worker` (uses `tests/fixtures/email_vectors.json`); `test_scrub_result_is_0600_and_atomic` (an injected failure mid-write leaves the original intact); `test_opaque_older_than_max_age_is_deleted_and_younger_is_retained_with_date`; `test_any_format_older_than_max_age_is_deleted`; `test_missing_artifact_is_reported_not_an_error`; `test_glob_escaping_via_dotdot_or_symlink_is_refused`; `test_config_rejects_dotdot_tilde_and_more_than_20_artifacts`.
  - ordering and failure: `test_d1_failure_exits_1_and_a_rerun_finishes_the_worker_part` (artifacts and the local store are already clean; the Worker still holds the rows, the second run selects them again and deletes them); `test_artifact_failure_stops_before_local_and_worker_deletes` (stores still contain the address); `test_rerun_after_partial_failure_completes`; `test_delete_lead_uses_the_same_order_and_scrubs_artifacts` (Task 11 command, extended); `test_unregistered_copies_warning_is_printed_when_no_artifacts_are_configured`.
  - heartbeat: `test_purge_log_records_time_and_counts_and_last_purge_is_reported`.
  - privacy: `test_disclosure_names_backup_roll_off_window_when_artifacts_are_configured` (uses the largest `max_age_days`).
- [ ] **Step 2: Run** `pytest tests/leads tests/site/test_privacy.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with the standard library only (`csv`, `json`, `sqlite3`, `glob`). Document in `docs/deploy.md` that the same D1 token as Task 11 is needed and must stay off CI, and that a backup not listed in `privacy.lead_artifacts` is not covered.
- [ ] **Step 4: Run** `pytest tests/leads -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the lead purge command with a dry run and artifact reach`.

---

## Release

After Tasks 14 to 21 and then Task 13 merge and CI is green, with the maintainer's answers to Open decisions D1 to D6 recorded in `docs/deploy.md`: bump to `0.3.0`, update `CHANGELOG.md`, tag `v0.3.0` through the Plan 1 release workflow. The maintainer's steps before the first live test are in `docs/deploy.md` (Turnstile site, Mailgun domain, secrets, KV namespace).

## Self-review notes

Revision 2 self-review, run against the spec (sections 4.3 to 4.6, 4.11, 6 and the decisions table) and the shipped `worker/src`.

**Spec coverage**
- Public page and gated bundle outside Pages (4.11): Task 2. Worker `gate` (4.4): Task 8. Instant reveal with optional verified link (4.5): Tasks 8 and 9. Mailgun first (4.6): Tasks 6 and 9 use the Mailgun HTTP API only; SES later is an `email.ts` provider swap and nothing in revision 2 assumes Mailgun beyond `sendMailgun`.
- Token: HMAC, 24 h default, configurable, lead-bound, cookie-only, never in a URL (4.11): Tasks 3 and 8. "Not forwardable": revision 2 changes this from "single use" to single use plus a binding, Task 14, with the maintainer's choice in D1; Task 15 adds the redemption alert in all modes. In `expiry_alert` mode the requirement is met only as detection; this is stated in D1 and the residual risks.
- Audiences public, partner, owner (4.11 and research): Tasks 1 and 2, unchanged. PDF private to the owner and never linked (4.10): Tasks 12 and 19; the PDF never reads lead data.
- Abuse requirements (section 6): spam relay and quota (Tasks 5, 7, 9, 17), gate bypass (Tasks 2, 8), fake emails (Tasks 5, 8, 9, 20), personal data (Tasks 4, 10, 18, 20, 21), cost exhaustion (Tasks 16, 17, 18), PDF leakage (Tasks 12, 19). Threat-model coverage: T-01 (Tasks 5, 7, 9, 17), T-03 (2, 8, 10), T-04 (3, 8, 9, 14, 15), T-12 (4, 11, 20, 21), T-14 (6, 15).
- Retention and deletion (4.11): the spec says 12 months by default, configurable, and a deletion command that removes a lead from the Worker's database and the local store. Revision 2 keeps 365 days as the default, makes it a hard maximum from creation, adds a ceiling (730) and a short unverified window (30), and extends deletion to registered exports and backups. The spec does not forbid any of these; D4 asks the maintainer to confirm the numbers.
- Spec 4.6 "when the budget is spent the kit still opens": preserved. Route budgets (Task 17) refuse the gate itself when exhausted, which is a different budget (a protection against flood, not mail quota); D3 names it.

**Type and name consistency**
- Token claim `cc` and `session_floor_ts`: defined in Task 3 and Task 4, used in Tasks 8, 15. `unlockResponse(env, leadId, nowMs, country)` is the same signature in Tasks 8, 9 and 14.
- `LINK_TTL_SECONDS` default is now 3600 (was 86400); Tasks 3, 9 and 14 agree; Task 9's email copy test uses minutes.
- `KitAlert.kind` grows in Tasks 15 and 17 (`redeem`, `country-change`, `capacity`) and Task 6's note says so.
- `reserveBudget(db, key, day, limit)` from Task 7 is reused by Task 17 with an hour string in the `day` column; the column holds either, and Task 4's purge (2 days) covers both.
- `purgeKitData` no longer returns `leads`; Task 20's `purgeLeads` does. The scheduled handler calls all three.
- Control ids: the original 13 plus ten new ones listed in Task 13 and `worker/controls.json`; every `// CONTROL:` marker in Tasks 14 to 20 appears in that list (`link-binding`, `code-attempts`, `body-cap`, `kill-switch`, `route-budget`, `lead-cap`, `view-debounce`, `log-cap`, `lead-hard-max`, `lead-unverified`). The original text said an id appears "exactly once"; `origin`, `turnstile` and others appear at several call sites, so Task 13 now declares `sites` per id and mutates each site.
- Task 13 no longer writes `docs/threat-model.md`, which would have collided with PR #54's `docs/security/threat-model.md`; it adds `docs/security/plan-3-abuse-cases.md` and status lines only.
- `refuse_remote(url, root)` takes a root in Task 12 (config dir) and Task 19 (staged dir).

**Gaps found and left**
- The threat model asks for `kid` key rotation (T-02, T-04, tests 14 and 17). Plan 3 has a single `TOKEN_HMAC_KEY` and no `kid`. Not part of this revision's brief; it should be raised with the maintainer before Task 3 is built, or accepted as a residual risk.
- The live meaning of D1 `rows_written` for a no-op conditional upsert, and the free-plan figures for D1, KV and logs, are the maintainer's numbers and are not verified here. Task 17 makes the quota a setting and the post-deploy smoke test (Task 10) should record the measured value.
- `unshare --net` flags and unprivileged availability are an assumption to confirm on the build machine in Task 19.
- Phase 4 must add the lead export's own registration in `privacy.lead_artifacts` and the shared-export exclusion test the threat model lists (test 44).

**Decisions the spec leaves open (plan-level)**: the cookie is `SameSite=Lax`; the gated bundle is one full HTML page in KV served by `/k/view`; owner-authorized YouTube analytics are owner-only (PDF only, behind a flag); LinkedIn numbers that are not self-reported are owner-only (maintainer to confirm the reading); the minimal display policy is the seed of Phase 5's table; the per-IP daily counter stores a keyed daily hash, disclosed on the privacy page; Turnstile outage on the gate fails closed.

**Not planned here**: lead export and follow-up (Phase 4), the daily pull of kit events into the local store (Phase 2 job; it reads `kit_events` and `leads` by the schema in Task 4), Grafana, platform collectors.
