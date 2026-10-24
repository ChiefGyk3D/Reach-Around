# Reach Around Plan 3: Media Kit and Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tagged `v0.3.0` with a media kit whose public half is a static page, whose full content is served by the Worker only to a visitor holding a valid lead-bound cookie, with email capture, a single-use emailed link, owner alerts, abuse controls that are each proven by a test, and a private PDF rendered locally.

**Architecture:** The Python engine renders two bundles from the instance config: the public site (Pages) and a gated bundle (one HTML page uploaded to Workers KV, never part of the Pages deploy). The existing write-only Worker gains four routes under `/k/`. A visitor passes Turnstile, a lead is stored in D1, and the Worker answers with a 303 and an HttpOnly cookie holding a short-lived HMAC token bound to the lead. Mailgun sends a fixed template carrying a single-use nonce link. The owner deletes leads and renders the full PDF from their own machine.

**Tech Stack:** Python 3.11+, pydantic v2, Jinja2, WeasyPrint and pypdf (optional `pdf` extra), pytest; TypeScript Cloudflare Worker, D1, KV, Workers rate-limiting binding, Web Crypto, Turnstile, Mailgun HTTP API, vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-reach-around-design.md` (sections 4.3 to 4.6, 4.11, 6 and the decisions table). Roadmap: `docs/superpowers/plans/2026-10-23-roadmap.md` (Phase 3, 68 h). Builds on `2026-10-23-plan-1-foundation-and-link-page.md`: `SiteConfig`, `Features(kit_gate)`, `Privacy` (`lead_retention_days`), `collected_items`, `sets_cookies`, `BuildManifest`, the Worker router in `worker/src/index.ts` with its scheduled handler, `purgeOldEvents`, and D1 migration `0001`. Research notes that bear on the kit: `docs/research/youtube.md`, `linkedin.md`, `social-blade.md`.

## Global Constraints

Copied from Plan 1, then extended.

- Python 3.11 or newer; tests run on 3.11, 3.12 and 3.13.
- License Apache-2.0; every new source file starts with `# SPDX-License-Identifier: Apache-2.0` (TypeScript: `// SPDX-License-Identifier: Apache-2.0`).
- American English in code, docs and messages.
- The public site is static and works with no JavaScript. The only dynamic server code is the one write-only Worker. The Worker never returns visitor data (emails, clicks, leads). Its only content output is the gated kit page, after a valid pass.
- Do not store IP addresses. At most a two-letter country code from Cloudflare headers.
- No personal details of the maintainer (address, callsign, grid square, hostnames, machine identifiers, real emails) in the repository, examples, tests or logs. Examples use synthetic data (`example.com`, `203.0.113.x`, "Example Creator") and the placeholder callsign `N0CALL` if one is ever needed.
- No secrets in the repository. Credentials come from the environment or a secrets manager.
- CI calls git-your-ship-together reusable workflows pinned by commit with a version comment: `ChiefGyk3D/git-your-ship-together/.github/workflows/python-ci.yml@8fdb8d106b11aae0f37e893bb57e9bd8e8b0e7b7 # v1.24.0`. The gate job is named `CI green`.
- Every PR links its issue (`Refs #n` or `Closes #n`). Every task has an issue and a board card before work starts.
- Versions are semantic; alpha is `0.x`. This plan ends at `v0.3.0`.
- Containers build from a `Containerfile`, run rootless, depend on no Docker socket, and pin base images by digest. The PDF renderer is a local owner tool and is not part of the image.
- The README stays marked alpha.
- Time zone for anything date-based defaults to `America/New_York`. Worker day counters (send budget, address limit) use the UTC day, via `utcDay(nowMs)`.
- Privacy: the disclosure page and cookie notice are generated from enabled features (Plan 1 Task 6A) and must name everything Phase 3 collects.
- **Cookie-only unlock.** The unlock token travels only in an HttpOnly, Secure, SameSite cookie named `__Host-ra_kit`. It never appears in a URL, a query string, a redirect `Location`, a response body, an email, a log line or the build output. A token sent any other way (query, header, form field) is ignored.
- **Token lifetime.** 24 hours by default (`TOKEN_TTL_SECONDS=86400`), configurable from 1 to 168 hours, fixed (no sliding renewal), bound to a lead that must still exist.
- **No personal data in logs.** No email address, IP address, token or nonce in any `console.*` call or error message. Tests spy on `console` and fail on a match.
- **Fail closed.** A missing secret, a Turnstile error or timeout, a missing gated bundle, or a signing key under 32 bytes means no unlock, never a weaker one.
- **Every control has a test that fails without it.** Each guard is one line ending in `// CONTROL:<id>` (ids in Task 13); the mutation check in Task 13 comments each one out and requires the abuse suite to go red.
- All D1 statements are parameterized; no SQL is built by string interpolation.

## Review Focus

The five failure modes the spec implies that most likely bite, most likely first. Each has a test in the task named.

1. **Recipient and header injection in the email form.** An address with a newline, a comma, a second recipient or an angle bracket must never reach Mailgun; the request carries exactly one `to` and no `cc`, `bcc` or `h:` fields. (Tasks 5 and 9)
2. **Races on single-use and counters.** A double click on the emailed link yields one cookie; ten concurrent sends against a budget of three yield exactly three. (Tasks 7 and 9)
3. **A deleted lead holding a live cookie.** After the owner deletes a lead, its still-unexpired cookie gets nothing. (Tasks 4 and 8)
4. **Clock and day boundaries.** A token at exactly `exp` is refused, an `iat` in the future is refused, and the budget and address limit reset at the UTC day change. (Tasks 3 and 7)
5. **Provider outage and missing content.** Turnstile down means the gate stays closed; Mailgun down or over budget means the kit still opens; an empty KV key means a 404 and no cookie is wasted on a blank page. (Tasks 5, 8 and 9)

---

## Contracts shared by all tasks

Fixed here so tasks can run in parallel.

**Routes** (all under the Plan 1 Worker; the Pages site serves `/kit/*`):

| Route | Success | Failure |
|---|---|---|
| `POST /k/gate` (form: `email`, `cf-turnstile-response`) | `303 /k/view` + `Set-Cookie` | `303 /kit/retry/`, identical for every cause |
| `POST /k/send` (same fields) | `303 /kit/sent/` | invalid input or Turnstile failure: `303 /kit/retry/`; throttled, over address limit, over budget or mail error: `303 /kit/sent/` (reveals nothing) |
| `GET /k/v/:nonce` | `200` confirmation page with one button; consumes nothing, sets no cookie | malformed nonce: `303 /kit/` |
| `POST /k/v/:nonce` (the button; also `POST /k/v/` with no nonce) | `303 /k/view` + `Set-Cookie` | missing, unknown, used or expired nonce: `303 /kit/` |
| `GET /k/view` | `200` gated page | `303 /kit/` |
| anything else under `/k/` | | `404`, empty body |

Every response from every `/k/` route, success or failure, carries `Cache-Control: private, no-store` and never `public` or `s-maxage`.

**Storage.** KV binding `KIT`, key `kit/v1` holds the gated page. D1 binding `DB` (the binding Plan 1 Task 8 declared; rename in this plan if Plan 1 used another name). Migration `worker/migrations/0002_kit.sql` (Task 4) defines `leads`, `kit_events`, `email_links`, `counters`.

**Cookie.** `__Host-ra_kit=<token>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=<ttl>` (`__Host-` requires Secure, `Path=/` and no `Domain`). Lax, not Strict, so the emailed link's top-level navigation can set and then use it.

**Token.** `v1.<base64url(JSON {lid, iat, exp})>.<base64url(HMAC-SHA256)>` over the first two segments.

**Fixed copy.** Redirect targets and the error/sent pages (`/kit/retry/`, `/kit/sent/`) are static pages built in Task 2.

## File Structure

```
src/reach_around/kit/models.py        KitSettings, KitMetric, Partner, CaseStudy, Rate, MediaKit (Task 1)
src/reach_around/kit/policy.py        versioned display rules, check_kit_display (Task 1)
src/reach_around/kit/build.py         build_kit: public pages, gated page, worker vars (Task 2)
src/reach_around/kit/templates/       kit.html, kit_state.html, gated.html, kit_pdf.html
src/reach_around/kit/pdf.py           render_kit_pdf, refuse_remote (Task 12)
src/reach_around/leads/delete.py      D1Client, CloudflareD1, delete_lead (Task 11)
worker/src/kit/env.ts                 KitEnv (Task 3)
worker/src/kit/token.ts               sign/verify, cookie helpers (Task 3)
worker/src/kit/leads.ts               lead storage, events, retention (Task 4)
worker/src/kit/input.ts, turnstile.ts email normalization, Turnstile verify (Task 5)
worker/src/kit/alerts.ts              owner alert sinks (Task 6)
worker/src/kit/limits.ts              minute, daily, address and budget limits (Task 7)
worker/src/kit/gate.ts                handleGate, handleView, unlockResponse (Task 8)
worker/src/kit/email.ts               handleSend, handleVerifyLink, Mailgun (Task 9)
worker/migrations/0002_kit.sql
worker/test/kit/*.test.ts, worker/test/abuse/*.test.ts, worker/scripts/mutate.mjs, worker/controls.json
tests/kit/*.py, tests/fixtures/email_vectors.json
docs/threat-model.md, docs/education/{the-gate,email-abuse}.md
```

## Dependencies and parallel groups

- Group A, no dependencies: Task 1 (Python) and Task 3 (Worker).
- Group B, after Task 3 (Task 1 for 2, 2A and 12; Plan 1 Task 6D for 2A): Tasks 2, 2A, 4, 5, 6 and 12 touch disjoint files and run in parallel.
- Group C: Task 7 (needs 4's migration) and Task 11 (needs 5's email vectors).
- Task 8 needs 3, 4, 5, 6, 7. Task 9 needs 8. Task 10 needs 2, 2A and 9. Task 13 needs everything.

---

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
- Produces: `build_kit(config: SiteConfig, out_dir: Path, gated_out: Path, *, today: date) -> KitBuildResult`; `KitBuildResult(public_pages: list[Path], gated_page: Path | None, vars_path: Path, warnings: list[str])`. Writes into `out_dir`: `kit/index.html`, `kit/sent/index.html`, `kit/retry/index.html` (each carries the gate forms and widgets; headers are Task 2A). Writes into `gated_out`: `kit.html` and `worker-vars.json` (`TOKEN_TTL_SECONDS`, `LEAD_RETENTION_DAYS`, `MAIL_DAILY_BUDGET`, `ALERT_SINKS`). The builder raises `ConfigError` for any `error` finding and returns `warning` findings in `warnings`.

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
  - `interface KitEnv extends Env` with bindings `DB: D1Database`, `KIT: KVNamespace`, `GATE_LIMITER: RateLimit`, `SEND_LIMITER: RateLimit`; vars `SITE_HOST`, `SITE_NAME`, `MAIL_FROM`, `OWNER_EMAIL`, optional `TOKEN_TTL_SECONDS` (default 86400), `LEAD_RETENTION_DAYS` (365), `EVENT_RETENTION_DAYS` (90), `MAIL_DAILY_BUDGET` (50), `ALERT_DAILY_CAP` (100), `IP_SEND_DAILY_CAP` (5), `LINK_TTL_SECONDS` (86400), `ALERT_SINKS` (JSON array), `MAILGUN_API_BASE` (default `https://api.mailgun.net`); secrets `TOKEN_HMAC_KEY`, `TURNSTILE_SECRET`, `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, and optional `DISCORD_WEBHOOK_URL`, `MATRIX_HOMESERVER`, `MATRIX_TOKEN`, `MATRIX_ROOM_ID`.
  - `signToken(key: string, claims: { lid: string; iat: number; exp: number }): Promise<string>` (throws if `key` is under 32 bytes)
  - `verifyToken(key: string, token: string, nowSec: number): Promise<{ lid: string; exp: number } | null>`
  - `unlockCookie(token: string, ttlSeconds: number): string`; `readUnlockCookie(header: string | null): string | null`; `COOKIE_NAME = "__Host-ra_kit"`.

- [ ] **Step 1: Write failing tests** (key `k = "x".repeat(32)`, `lid = "a1b2c3"`, `iat = 1_000`, `exp = 1_000 + 86_400`):
  - `round trip returns lid and exp`.
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
  - Tables: `leads(id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, created_ts INTEGER NOT NULL, last_seen_ts INTEGER NOT NULL, verified_ts INTEGER, source TEXT NOT NULL CHECK (source IN ('gate','email')), country TEXT, last_link_day TEXT, last_view_alert_ts INTEGER)`; `kit_events(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE, kind TEXT NOT NULL CHECK (kind IN ('unlock','view','verify')))`; `email_links(id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE, created_ts INTEGER NOT NULL, expires_ts INTEGER NOT NULL, used_ts INTEGER)`; `counters(key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (key, day))`. Indexes on `kit_events.ts` and `leads.last_seen_ts`.
  - `upsertLead(db, email: string, source: "gate" | "email", country: string | null, nowMs: number): Promise<{ id: string; created: boolean }>`; `getLead(db, id): Promise<Lead | null>`; `markVerified(db, id, nowMs): Promise<void>`; `touchLead(db, id, nowMs): Promise<void>`; `recordKitEvent(db, leadId, kind, nowMs): Promise<void>`
  - `purgeKitData(env: KitEnv, nowMs: number): Promise<{ leads: number; events: number; links: number; counters: number }>`

- [ ] **Step 1: Write failing tests** (apply `0002` in the test setup):
  - `upsertLead is case-insensitive and returns the same id with created false the second time`; `id is 32 hex characters`.
  - `a lead has no ip column` (`PRAGMA table_info(leads)` and every other kit table lists no name matching `/ip|addr/i`).
  - `deleting a lead cascades to events and links` (the schema's foreign keys are enforced; this is what lets the owner command in Task 11 be one statement).
  - `purgeKitData removes leads idle over 365 days and keeps newer ones`; `LEAD_RETENTION_DAYS is configurable` (30 days); `lead retention counts from last_seen_ts, so a returning lead survives`.
  - `events older than 90 days are purged, newer kept`; `used or expired email links older than 7 days are purged, a live one is kept`; `counters older than 2 days are purged`.
  - `the scheduled handler calls both purgeOldEvents and purgeKitData`.
  - `no SQL is built by interpolation`: reads every file in `worker/src/kit/` and `retention.ts` and asserts no template literal containing `SELECT|INSERT|UPDATE|DELETE` also contains `${`.
- [ ] **Step 2: Run** `npm test -- leads retention`. Expected: FAIL.
- [ ] **Step 3: Implement** the migration and functions. Use `INSERT ... ON CONFLICT(email) DO UPDATE SET last_seen_ts` so concurrent upserts cannot duplicate. Lead ids come from `crypto.getRandomValues` (16 bytes).
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add lead storage, kit events and retention`.

### Task 5: Input validation and Turnstile

**Files:**
- Create: `worker/src/kit/input.ts`, `worker/src/kit/turnstile.ts`, `worker/test/kit/input.test.ts`, `worker/test/kit/turnstile.test.ts`, `tests/fixtures/email_vectors.json`

**Interfaces:**
- Produces: `normalizeEmail(raw: unknown): string | null`; `readForm(request: Request): Promise<{ email: string | null; turnstile: string | null } | null>` (null when content type is not `application/x-www-form-urlencoded`, body over 4096 bytes, or a field repeats); `verifyTurnstile(env: KitEnv, token: unknown, action: "gate" | "send", fetchFn?: typeof fetch): Promise<boolean>`.
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
- Produces: `type KitAlert = { kind: "unlock" | "view"; email: string; country: string | null; verified: boolean; ts: number }`; `formatAlert(alert: KitAlert): string` (single line, plain text); `notifyOwner(env: KitEnv, alert: KitAlert, fetchFn?: typeof fetch): Promise<void>` (never throws).

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
- Produces: `handleGate(request, env, ctx): Promise<Response>`; `handleView(request, env, ctx): Promise<Response>`; `unlockResponse(env: KitEnv, leadId: string, nowMs: number): Promise<Response>` (the `303 /k/view` plus cookie; Task 9 reuses it); `alertOnce(env, ctx, lead, kind)` applying the debounce (one `view` alert per lead per 6 hours via `leads.last_view_alert_ts`) and the daily cap (`reserveBudget(db, "alert", day, ALERT_DAILY_CAP)`), then `ctx.waitUntil(notifyOwner(...))`.
- Gate order, each guard a single line: route and method, origin `// CONTROL:origin`, bot user agent `// CONTROL:bot-ua`, `checkIpMinute` `// CONTROL:ip-minute`, `readForm`, `normalizeEmail`, `verifyTurnstile` `// CONTROL:turnstile`, `upsertLead`, `recordKitEvent(unlock)`, alert, `unlockResponse`. View: `readUnlockCookie`, `verifyToken`, `getLead` `// CONTROL:lead-exists`, `KIT.get("kit/v1")`.

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
- Send order, each guard one line: origin, bot UA, `checkIpMinute(SEND_LIMITER)`, `readForm`, `normalizeEmail`, `verifyTurnstile(action "send")`, `reserveIpDaily` `// CONTROL:ip-daily`, `upsertLead(source "email")`, `reserveAddressDay` `// CONTROL:address-day`, `reserveBudget("mail")` `// CONTROL:budget`, create nonce (32 random bytes, base64url, 43 characters), store `SHA-256(nonce)` hex with `expires_ts = now + LINK_TTL_SECONDS`, send. The link is `https://<SITE_HOST>/k/v/<nonce>`.
- Link page and verify (scanner-safe): the emailed URL is `GET /k/v/<nonce>`, which renders a static confirmation page (one `<form method="post">` with a single button, no script, `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex`, CSP `default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'`) and reads nothing from the database. Only the button's `POST /k/v/<nonce>` consumes: origin check `// CONTROL:origin`, nonce must match `^[A-Za-z0-9_-]{43}$`, consume with `UPDATE email_links SET used_ts = ? WHERE id = ? AND used_ts IS NULL AND expires_ts > ?` and require `meta.changes === 1` `// CONTROL:single-use`; then `markVerified`, `recordKitEvent(verify)`, `recordKitEvent(unlock)`, `alertOnce`, `unlockResponse`. `HEAD` returns the same headers as GET with no body and touches nothing.

- [ ] **Step 1: Write failing tests** (fake Mailgun via `fetchFn`, `MAIL_DAILY_BUDGET` set per test):
  - relay and injection (Review Focus 1): `address with newline, comma, semicolon, space or angle brackets never reaches Mailgun` (fetch call count 0, redirect `/kit/retry/`); `exactly one Mailgun call has a single to equal to the normalized address and only fields from, to, subject, text` (no `cc`, `bcc`, `h:*`, `o:*`); `repeated email fields are refused`.
  - template: `rendered text is identical for five different addresses except the URL`; `body does not contain the recipient's local part` (`zzqx@example.com`); `subject is constant`; `body states the link works once, in one browser, for 24 hours`.
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
- [ ] **Step 5: Commit** `feat: add the owner lead deletion command`.

### Task 12: Private full PDF

**Files:**
- Create: `src/reach_around/kit/pdf.py`, `templates/kit_pdf.html`, `tests/kit/test_pdf.py`
- Modify: `pyproject.toml` (extra `pdf = ["weasyprint", "pypdf"]`, pinned with hashes in the dev requirements), `src/reach_around/cli.py` (`kit-pdf`), `docs/education/the-gate.md` (PDF section)

**Interfaces:**
- Consumes: `MediaKit`, `check_kit_display` (Task 1).
- Produces: `render_kit_pdf(config: SiteConfig, out_file: Path, *, today: date, include_owner: bool = False) -> PdfResult`; `PdfResult(pages: int, bytes: int)`; `refuse_remote(url: str) -> None` (raises `ValueError` for any scheme except `file`, and for paths outside the config directory); CLI `reach-around kit-pdf --config PATH --out FILE [--include-owner]`.

- [ ] **Step 1: Write failing tests** (`pytest.importorskip("weasyprint")`, marked `pdf`):
  - `test_pdf_contains_public_and_gated_text` (extract with pypdf: the canary rate, partner note and a public metric label are present).
  - `test_pdf_has_no_gate_artifacts` (text contains none of "Turnstile", "Unlock", "Enter your email", "Send me a link").
  - `test_owner_metrics_excluded_by_default_and_included_with_flag` (flag run also prints a warning "do not share" to stderr).
  - `test_policy_error_refuses_render`.
  - `test_refuse_remote_blocks_http_https_ftp_data_and_parent_paths`.
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
- Create: `worker/test/abuse/harness.ts`, `ab1-spam-relay.test.ts`, `ab2-quota.test.ts`, `ab3-gate-bypass.test.ts`, `ab4-token.test.ts`, `ab5-forwarded-link.test.ts`, `ab6-fake-emails.test.ts`, `ab7-bots.test.ts`, `ab8-privacy.test.ts`, `ab9-alert-abuse.test.ts`, `worker/controls.json`, `worker/scripts/mutate.mjs`, `docs/threat-model.md`, `docs/education/email-abuse.md`, `tests/test_threat_model_doc.py`
- Modify: `worker/package.json` (`test:abuse`, `mutation-check`), `.github/workflows/ci.yml` (job `mutation-check` in the `CI green` needs list)

**Interfaces:**
- Consumes: the whole Worker. `harness.ts` exports `makeEnv(overrides?)`, `post(path, form, opts?)`, `get(path, opts?)`, `fakeFetch` (records Mailgun, Turnstile and sink calls), `issuedCookie(res)`.
- `controls.json`: `{ "<id>": { "file": "worker/src/kit/…", "abuse": ["AB-1", …] } }` for ids `turnstile`, `origin`, `bot-ua`, `ip-minute`, `ip-daily`, `address-day`, `budget`, `email-validate`, `single-use`, `token-signature`, `token-expiry`, `lead-exists`, `cookie-only`.

Abuse cases (these are the threat-model rows; each file asserts end to end through `worker.fetch`):

| Id | Threat | Suite asserts |
|---|---|---|
| AB-1 | Spam relay | no Mailgun call without Turnstile; no call for multi-recipient or header-injected addresses; one fixed template; one message per address per UTC day; per-IP cap |
| AB-2 | Quota exhaustion | 100 requests across 100 addresses stop at `MAIL_DAILY_BUDGET`; the gate still unlocks when the budget is spent |
| AB-3 | Gate bypass | every route and method without a cookie returns no canary; token in query, header or form is ignored; the canary is absent from the Pages output (reads the Task 2 build); unknown KV paths 404; every `/k/` response carries `private, no-store` and never `public` or `s-maxage` |
| AB-4 | Token tampering and replay | altered payload, signature, key, expiry, `lid`; a token for a deleted lead; a cookie replayed after `exp`; the nonce used as a token |
| AB-5 | Forwarded or reused link | GET and HEAD never consume (scanner prefetch), POST with a missing, reused or expired nonce, other client, concurrent POSTs, wrong origin all land on the gate; no URL anywhere carries a token |
| AB-6 | Fake emails | invalid syntax rejected; client-supplied `verified`/`lid`/`country` ignored; `verified_ts` set only by a link click; unverified leads are distinguishable in the table |
| AB-7 | Bot traffic | bot and missing user agents, wrong origin, missing Turnstile, and bursts produce no lead, no event, no mail, and the same redirect as any other failure |
| AB-8 | Personal data | no IP column or value in any table (scan all rows for `203.0.113.`); no email, token or nonce in any `console` call across all flows; no response body contains the submitted email |
| AB-9 | Alert abuse | a view flood produces at most one alert per lead per 6 hours and at most `ALERT_DAILY_CAP` per day; hostile text cannot mention or inject into a sink message |

- [ ] **Step 1: Write the suite** (each case above becomes one or more named tests in its file, reusing assertions from Tasks 3 to 9 only where the end-to-end path adds nothing; AB-3's build check shells out to `reach-around build` on the example config and greps its output directory).
- [ ] **Step 2: Write `worker/scripts/mutate.mjs`.** For each id in `controls.json`: fail if `// CONTROL:<id>` is not found exactly once in `worker/src` or its line is not a single statement; copy `worker/` to a temp directory; comment out that line; run `npm run test:abuse`; require a non-zero exit; print `ok <id> (caught by <n> failing tests)`. Exit non-zero if any control survives. No test-only switches exist in production code; the mutation happens only in the temp copy.
- [ ] **Step 3: Run** `npm run test:abuse` then `npm run mutation-check`. Expected: abuse suite PASS; every control line `ok`, final `13 controls, 13 caught`. A surviving control means a missing test: add it to the owning AB file, not to the script.
- [ ] **Step 4: Write `docs/threat-model.md`** (assets, actors, trust boundaries, abuse cases AB-1 to AB-9 with mitigation, test file and control id, ranked risks, residual risks) and `docs/education/email-abuse.md`. Residual risks to state plainly: a mail scanner that prefetches the link only loads the confirmation page and cannot burn it, but a scanner that submits forms would, and the visitor then lands on the gate (fail-safe, not a leak); a stolen cookie works until it expires or the lead is deleted; the 24-hour fixed lifetime means a lost cookie cannot be refreshed by an attacker; an attacker can cause at most one email per victim address per day within the global budget; a Turnstile outage locks the gate, so the owner sends the PDF directly; the gated page is cached at the edge for about a minute after an upload; Cloudflare and Mailgun see the addresses they process.
- [ ] **Step 5: Write `tests/test_threat_model_doc.py`**: `test_every_abuse_case_names_an_existing_test_file` (parse `AB-n` rows, assert each cited file exists), `test_every_control_in_controls_json_is_documented`, `test_every_ab_case_in_the_table_above_is_in_the_doc`.
- [ ] **Step 6: Run** `pytest tests/test_threat_model_doc.py -v`. Expected: PASS.
- [ ] **Step 7: Commit** `test: add the gate abuse suite, mutation check and threat model`.

---

## Release

After Task 13 merges and CI is green: bump to `0.3.0`, update `CHANGELOG.md`, tag `v0.3.0` through the Plan 1 release workflow. The maintainer's steps before the first live test are in `docs/deploy.md` (Turnstile site, Mailgun domain, secrets, KV namespace).

## Self-review notes

- Spec coverage: public page and gated bundle outside Pages (4.11, Task 2); Worker `gate` (4.4, Task 8); instant reveal with optional verified link (4.5, Tasks 8 and 9); Turnstile, per-IP and per-address limits, one message per address per day, fixed template, global budget degrading to no email (4.6, Tasks 5, 7, 9); token HMAC, 24 h, cookie-only, lead-bound, not forwardable, single-use link, tests for expired, tampered, missing, used and no token in URLs (4.11, Tasks 3, 8, 9, 13); strict CSP with a Turnstile-only exception (Task 2A); protected deploy environment and no-store, non-public gated storage (Task 10); owner alerts (4.4, Task 6); retention 12 months and deletion command (4.11, Tasks 4 and 11); private PDF never uploaded or linked (4.10, Task 12); display policy and credits (4.11 and research, Tasks 1 and 2); no JS for public half and the gate needing it (Task 2); abuse tests per section 6 (Task 13).
- Decisions the spec leaves open: lead retention counts from last activity, not creation; cookie is `SameSite=Lax`; the gated bundle is one full HTML page in KV, served by `/k/view` (no client-side injection); owner-authorized YouTube analytics have a `private` visibility that only the owner's PDF can show, behind a flag; the emailed link opens a confirmation page and only the button's POST consumes it (scanner-safe, one extra click); the per-IP daily counter stores a keyed daily hash, disclosed on the privacy page; audience is public, partner or owner, and a rule says whether partners may see what the public may not; LinkedIn numbers that are not self-reported and YouTube owner analytics are owner-only (maintainer to confirm the LinkedIn reading); the minimal display policy here is the seed of Phase 5's table.
- Not planned here: lead export and follow-up (Phase 4), the daily pull of kit events into the local store (Phase 2 job; it reads `kit_events` and `leads` by the schema in Task 4), Grafana, platform collectors.
