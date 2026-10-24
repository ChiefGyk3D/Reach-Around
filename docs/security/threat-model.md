# Reach Around threat model

Status: draft for maintainer review. Written 2026-10-23 for issue #49, from the design only. No code exists yet beyond the plans, nothing was run, and no system was probed.

Inputs read as data: the design spec (`docs/superpowers/specs/2026-10-23-reach-around-design.md`, including the section 4.11 amendment), Plan 1 (`docs/superpowers/plans/2026-10-23-plan-1-foundation-and-link-page.md`), the roadmap, and the platform research notes in `docs/research/`. Where those documents contradict each other, this model says so and picks the safer reading.

This is a ranked list of how the design can be abused and what proves each defense works. It is not an audit, and it does not say the system is secure. Section 9 lists what it did not cover.

A plain-language version for readers learning from the project is in `docs/education/threat-model-walkthrough.md`.

## 1. Scope

In scope: the engine (config loader, site builder), the Cloudflare Pages site, the Worker (click, gate, event, alert, send), D1 and the gated bundle store, the collectors and snapshot store, the collector host's Cloudflare read token, the CI and deploy workflows, the container image, and the platform terms that limit what may be shown.

Out of scope: Cloudflare, Mailgun, Turnstile and each platform's own security; the security of the owner's laptop or homelab beyond the credentials this design places there; any Beacons migration data.

## 2. Assets

| ID | Asset | Data class | Why it matters |
|---|---|---|---|
| A1 | Lead emails and unlock events (D1, then the local store) | Personal data | Partner contacts; disclosure harms real people and the owner's business reputation |
| A2 | Gated media kit content (demographics, partner detail, case studies, rates) | Business confidential | Rates and audience data are negotiating leverage |
| A3 | Worker signing secret (HMAC key for unlock tokens) | Secret | Whoever holds it can mint unlocks for any lead |
| A4 | Cloudflare deploy API token (CI) | Secret | Can change what the site and Worker serve |
| A5 | Cloudflare read token on the collector host | Secret | Reads the Worker database, which holds leads |
| A6 | Collector credentials (OAuth refresh tokens, API keys, GitHub App key, Social Blade key) | Secret | Account access on the creator's platforms |
| A7 | Kick stream key (appears in the Kick channel response) | Secret | Lets anyone broadcast on the channel |
| A8 | Mailgun credentials and the sending domain reputation | Secret and reputation | Quota, deliverability, and the owner's name on every message |
| A9 | Snapshot store (daily metrics, token registry) | Mixed; some owner-authorized platform data | The long-term history; integrity decides whether numbers shown to partners are true |
| A10 | Click and view counts | Low | Integrity matters because partners may read them |
| A11 | Instance repo (config, copy, partner list) | Business confidential | Holds the partner list and may hold draft rates |
| A12 | The public site and the repo's release artifacts (image, site build) | Integrity | A tampered build reaches every visitor or every user of the image |
| A13 | The owner's standing with platforms (API access, accounts) | Reputational | Violating terms can cost API access |

## 3. Actors

| ID | Actor | Capability |
|---|---|---|
| X1 | Anonymous remote visitor or bot | Any HTTP request to the Pages site and Worker routes; can solve Turnstile cheaply through human farms |
| X2 | Partner (legitimate gated visitor) | Holds a valid unlock; may forward links or share a screen |
| X3 | Spammer or harasser | Wants the email form to send mail to third parties |
| X4 | Competitor or curious third party | Wants rates, partner names or audience data without being counted |
| X5 | Contributor with a fork or a PR | Can propose code, workflow and dependency changes |
| X6 | Compromised dependency or action | Runs inside CI, the container, or the Worker bundle |
| X7 | Hostile or careless configuration author | Another creator copying a shared example, or a pasted config |
| X8 | Insider with access to the instance repo or the collector host | Owner, helper, or malware on the collector host |
| X9 | A platform (policy change) | Changes terms, revokes access, rotates fields |
| X10 | Text-reading model or tool (agents drafting docs or configs) | Reads attacker-controlled text such as issue bodies or API responses |

## 4. Trust boundaries

```
 [B1] visitor browser ---------------> Pages static site (public bundle only)
 [B2] visitor browser ---------------> Worker: /e/c/:id, gate, send, kit fetch
 [B3] Worker -------------------------> D1 (leads, events), KV/R2 (gated bundle)
 [B4] Worker -------------------------> Turnstile verify, Mailgun, owner alert channel
 [B5] emailed link (recipient inbox) -> Worker (single-use token in URL)
 [B6] instance repo / config file ----> engine (loader, builder)       owner-trusted, still validated
 [B7] build ---------------------------> two bundles: public (Pages), gated (Worker store)
 [B8] CI runner (secrets) ------------> Cloudflare API (deploy)
 [B9] collector host -----------------> Cloudflare API (read D1) ; platform APIs
 [B10] platform API responses --------> collector --> snapshot store   (stream key crosses here)
 [B11] snapshot store ----------------> sinks: dashboard, Grafana, exports, PDF (audience rules)
 [B12] third-party code -------------> build, image, Worker bundle (dependencies, actions)
```

| Boundary | Less trusted side | More trusted side | Controls that must hold |
|---|---|---|---|
| B1 | Browser | Static host | Public bundle contains nothing gated; security headers |
| B2 | Anonymous internet | Worker | Validate every input, Turnstile on gate and send, per-client limits, uniform responses on click |
| B3 | Worker code | Data stores | Parameterized queries, minimal columns, no IP column |
| B4 | Worker | Third parties | Fixed templates, secrets only in Worker secrets, fail-closed choices documented |
| B5 | Inbox and email scanners | Worker | Atomic single use, short expiry, hashed at rest |
| B6 | Config text | Engine | Hardened loader, closed schema, escape on render |
| B7 | Build logic | Deploy targets | Gated content never reaches Pages; asserted by a test |
| B8 | CI job | Cloudflare | Least-privilege token, protected environment, separated build and deploy jobs |
| B9 | Collector host | Cloudflare | Read-scoped token, restricted by account, resource and expiry where possible |
| B10 | Platform response | Local store | Allowlist fields at the collector edge |
| B11 | Store | Audiences | Display policy by audience (public, gated, owner-only) |
| B12 | Upstream | Everything | Pins, hashes, review, no install scripts in CI |

## 5. Findings from the design text

These are specific points in the documents that the plans must resolve. Each feeds a risk below.

- F1. Spec 4.4 says the gate returns "a short-lived signed token that unlocks the full kit in the browser". Spec 4.11 amends this to an HttpOnly cookie. Section 4.4 should be updated so a plan author does not implement the older wording.
- F2. The Worker is called "write-only" because its routes never return visitor data. It still holds read access to D1, KV or R2 and the signing secret. The claim protects against route-level disclosure only; a compromised Worker bundle (X6) reads everything. The model treats the Worker code as high-value for that reason.
- F3. Plan 1 sends clicks by the `ping` attribute plus a `sendBeacon` fallback. The `ping` request carries a `Ping-From` header the client controls and usually no useful `Origin`. The origin check is a noise filter, not a security control. Link ids are printed in the public page, so the allowlist does not stop a bot from inflating counts for real ids; only rate limiting and bot filtering do. Some browsers disable `ping` while still exposing the attribute, so feature detection can undercount. Counts must stay labeled approximate.
- F4. Plan 1 gives the Worker `LINK_IDS` as a variable. Between a site deploy and a Worker deploy the two lists can differ. This causes dropped clicks, not a security problem, but the deploy order should be fixed (Worker first or both in one job).
- F5. Spec 4.11 says the emailed link is single use and "sets the cookie". Corporate mail scanners and link previewers fetch URLs before the human does. A link that is consumed by a GET will be consumed by the scanner, and the partner will land on the gate. The emailed token is also a secret in a URL, which the "no URL carries the token" test must not be allowed to forbid by accident: it is the unlock token that never appears in a URL, not the one-time email token.
- F6. Spec 4.5 reveals the kit on an unverified email. The lead record therefore proves only that someone typed an address, not that the owner of that address was present. Lead data and "per-visitor tracking" must be described that way, and a third party's address can land in the leads table without consent.
- F7. Spec 4.10 keeps a permanent daily-history store. The YouTube note (`docs/research/youtube.md`, lines 42 to 43) says non-authorized statistics may be kept 30 days at most and authorized data may be kept only while the user consents with authorization re-verified every 30 days, and that owner-authorized data may not be shown publicly (III.E.3.b). The display policy in spec 4.11 has audiences "public" and the rest; it needs three audiences (public, gated partner, owner-only), because a partner viewing an owner-authorized figure in the gated kit or the PDF is still a third party viewing it. The retention conflict is not addressed anywhere in the design.
- F8. Plan 1 specifies no HTTP security headers (CSP, frame-ancestors, referrer policy, HSTS) for the Pages site and none for the Worker. Pages supports a `_headers` file.
- F9. Plan 1 `Creator.avatar` and `Link.icon` are bare strings. The https-only URL rule is stated for link and social URLs only. If an icon or avatar can be a path or URL, it is a path-traversal or mixed-content vector at build time.
- F10. The deploy workflow (Plan 1, Task 11) "refuses to run from any ref other than `main`" inside the workflow file. A person with write access can edit the workflow on a branch and remove the check. The restriction has to live in GitHub (an environment with a deployment branch policy and required reviewers), not only in the file.
- F11. Cloudflare Workers are reachable on a `workers.dev` hostname by default and Pages makes preview deployments on `pages.dev` hostnames. Both bypass any rule set up on the custom hostname unless turned off.
- F12. The collector host reads the Worker database through the Cloudflare API. Whether a token scoped only to read can run the needed queries is unverified here. If the only workable scope allows writes, the collector host holds a token that can delete or alter leads.
- F13. Spec 4.6 says "when the budget is spent the kit still opens, just without the email". That is a fail-open choice for the email path. The same decision is not written down for Turnstile verification outages, and it must be (see T-01).

## 6. Risk register

Impact: 1 (minor) to 5 (severe). Prerequisite: how hard it is to start (1 needs a skilled insider or a merged PR, 5 needs only an HTTP client). Rank is a judgment, weighted toward high impact reachable by anyone, and reflects that a public Worker with an email sender will be attacked within days of going live. "Phase" is the roadmap phase where the control is built.

| Rank | ID | Abuse case (actor does X to reach Y) | Impact | Prereq | Phase |
|---|---|---|---|---|---|
| 1 | T-01 | X1/X3 posts many addresses (victims or throwaways) to the email form to relay mail, burn the Mailgun quota, or damage the sending domain | 4 | 5 | 3 |
| 2 | T-02 | X6/X5/X8 obtains a deploy token, the signing secret, or a collector credential from CI, logs, the repo or a compromised dependency | 5 | 2 | 1, 3 |
| 3 | T-03 | X1/X4 reads gated content without a valid unlock (static bundle leak, shared-cache hit, missing check, public R2) | 4 | 4 | 3 |
| 4 | T-04 | X1/X2 forges, replays or shares an unlock (stolen cookie, forwarded email link, secret leak, algorithm confusion) | 4 | 3 | 3 |
| 5 | T-05 | X1 posts forged and bot events and fake emails to pollute leads, counts and owner alerts | 3 | 5 | 1, 3 |
| 6 | T-06 | Kick stream key, or any secret, reaches a log, the store, a fixture or an export | 5 | 3 | 1 or 2 (collector) |
| 7 | T-07 | X1 abuses the click endpoint: link-id forgery, open redirect, log injection, resource exhaustion | 2 | 5 | 1 |
| 8 | T-08 | Page-level injection and framing: XSS through config text, clickjacking of the gate, mixed content | 3 | 3 | 1 |
| 9 | T-09 | X5/X6 supply chain: dependency, GitHub Action, base image, or release workflow is tampered | 5 | 2 | 1 |
| 10 | T-10 | X7 hostile or careless configuration: YAML bombs, aliases, path traversal, template injection, bad URLs | 3 | 3 | 1 |
| 11 | T-11 | Platform-terms abuse: a build shows owner-authorized or terms-barred data publicly or to partners, or stores it past allowed time | 3 | 4 (self-inflicted) | 2, 3 |
| 12 | T-12 | Personal data retention and deletion failure: leads kept past the setting, deletion that leaves copies, emails in logs or alerts | 3 | 3 | 1, 3 |
| 13 | T-13 | Worker and Pages exposure through default hostnames, preview deployments, dangling DNS on the test subdomain | 3 | 4 | 1 |
| 14 | T-14 | Owner alert channel abuse: flooding, markdown or mention injection through an email, webhook URL leak | 2 | 4 | 3 |
| 15 | T-15 | Snapshot store integrity and token-health failure: stale or zero values shown as fact, a poisoned import | 2 | 2 | 2 |

### T-01 Spam relay and quota exhaustion through the email form

Abuse: an attacker posts the gate or `send` form with many victim addresses. Even with a fixed template, each message arrives from the owner's domain, naming the owner, and a flood to one victim or to many victims harms the sending reputation and can get the Mailgun account suspended. A second goal is denial of service: spend the daily budget so a real partner cannot get "email me a link".

Why it is ranked first: anonymous, needs only an HTTP client, the payoff is immediate, and the controls are the only barrier.

Mitigations (design 4.6 plus these specifics):
1. `send` is a separate route from `gate`; the gate never sends mail. The kit opens without email (instant reveal), so mail is only sent on a deliberate second action.
2. Turnstile is verified server-side on the Worker (siteverify) on every gate and send request, with the hostname and an action name checked. A token is single use. If Turnstile cannot be reached the request fails closed for `send`; for `gate` the choice is a documented decision for the maintainer (default recommended: fail closed and alert), because fail open makes an outage the way around the control.
3. The recipient is exactly one address taken from the form, validated (single `@`, length caps, no control characters or newlines, no comma or semicolon lists), normalized (lowercase domain), never taken from any other field. No visitor-supplied text appears in the message; subject, body and sender are fixed.
4. One message per address per day, a per-client request limit, and a global daily send budget counted with a single atomic SQL statement, so concurrent requests cannot overspend.
5. When the budget is spent, `send` returns the same response shape and the kit still opens. The response never says whether an address received mail (no recipient oracle).
6. The Mailgun domain is a sending subdomain with SPF, DKIM and DMARC, and a Mailgun-side cap as a second ceiling.

Verification:
- `worker/test/send.test.ts::missing_or_failed_turnstile_sends_nothing`
- `::reused_turnstile_token_sends_nothing`
- `::one_message_per_address_per_day` (two posts, one Mailgun call)
- `::newline_in_address_is_rejected` and `::address_list_is_rejected`
- `::message_body_has_no_visitor_text` (snapshot of the outbound payload with a hostile `name` field present in the request)
- `::concurrent_requests_cannot_exceed_daily_budget` (50 parallel posts against a budget of 5, exactly 5 sends)
- `::budget_spent_still_opens_kit_and_same_response_shape`
- `::turnstile_outage_fails_closed` (matches the decision recorded in the spec)

Residual: a human farm can solve Turnstile, so a determined attacker can still make the owner send up to the daily budget to chosen victims, one message per victim. The budget caps this at a small number a day (the maintainer's free tier figure is 100 and needs checking). Accepted by the maintainer; reopen if the sending domain shows a complaint rate above the provider's thresholds or a Mailgun warning.

### T-02 Credential and secret exposure through CI, logs or the repo

Abuse: a deploy token, the signing secret, a Mailgun key, or a collector refresh token is read by an attacker through a workflow change, a leaked log, a build step running attacker-influenced code while the token is present, or a committed file.

Mitigations:
1. Separate jobs: the build job (templates, dependency install, tests) has no Cloudflare secret. A second deploy job gets the token, runs only fixed commands, and consumes the build output as an artifact.
2. The deploy job runs in a GitHub Environment with a deployment branch policy restricted to `main` and, for production, a required reviewer. The in-file "refuse other refs" check is kept as a second layer, not the only one (F10).
3. The Cloudflare deploy token has only the permissions the workflow needs (Pages edit, Workers scripts edit, D1 edit for migrations) on one account and the named resources, with an expiry. The collector host's read token is a different token and is never available to CI.
4. Worker secrets (signing key, Turnstile secret, Mailgun key, alert webhook URLs) live in Worker secrets, never in `wrangler.toml` `vars`. Only non-secret values (`SITE_HOST`, `LINK_IDS`) are variables.
5. A secret scan runs on every push and PR through the GYST security workflow, and gitleaks scans all branches. A repo-wide rule: no `.env` files, no recorded HTTP fixtures that include real tokens.
6. Logs: collectors and the Worker never log request or response bodies at normal levels; the HTTP client's redaction hook is tested.
7. Rotation: the signing secret is versioned (`kid` in the token) so it can be rotated without a flag day. The runbook says what to rotate after a suspected leak (all of A3 to A8) and that rotating the signing secret logs out all partners.

Verification:
- `tests/test_no_secrets_in_repo.py::test_workflows_pass_secrets_only_to_deploy_job` (parses `.github/workflows/*.yml`, asserts `CLOUDFLARE_API_TOKEN` appears only in the deploy job, and that job has an `environment:` key)
- `tests/test_workflows.py::test_deploy_workflow_permissions_are_contents_read` and `::test_actions_are_pinned_by_sha`
- Run `actionlint` and the GYST workflow lint (zizmor) in CI; expected zero findings.
- `worker/test/config.test.ts::wrangler_vars_contain_no_secret_names` (fails if a `vars` key matches `*_SECRET`, `*_KEY`, `*_TOKEN`, `*_WEBHOOK*`)
- `tests/collectors/test_redaction.py::test_logger_redacts_authorization_and_token_fields`
- Manual check at release: the secret scanning alert list for the repo is empty.

Residual: GitHub itself, Cloudflare, and the maintainer's own machine are trusted. A reviewer approving a malicious workflow change is a human control and remains. Reopen on any secret scanning alert.

### T-03 Gate bypass and reading gated content

Abuse: an attacker requests the gated bundle without a valid pass.

Paths: (a) the gated bundle is accidentally included in the Pages deploy; (b) the gated storage is public (R2 public development URL or a public bucket binding); (c) the Worker serves gated content with caching headers, so a shared cache serves it to the next visitor, or uses an unsafe cache key; (d) a route that returns the content before the check, or a path traversal into the store key; (e) a PDF or build artifact in CI is retained and readable.

Mitigations:
1. The builder writes the gated bundle to a separate directory and never to the Pages output. The Pages deploy step refuses to run if the output directory contains any file listed in the gated manifest.
2. The gated store has no public access. Documentation requires R2 public access off and no custom domain on the bucket; the Worker binding is the only reader.
3. Every gated response sends `Cache-Control: private, no-store` and `Vary: Cookie`; the Worker never calls the cache API for gated responses.
4. The kit fetch route looks up the content by a fixed key chosen by the Worker, never by a client-supplied path.
5. The token check happens before any store read. A missing, expired, tampered, or revoked token gets the same response as no token.
6. The gated bundle and the full PDF are not CI artifacts with long retention; the build uploads them straight to the store from the deploy job, and artifact retention is set to the minimum.

Verification:
- `tests/site/test_gate_separation.py::test_public_output_contains_no_gated_sentinel` (builds with a sentinel string in a gated field, greps every file in the public output tree, expects none)
- `tests/test_deploy_guard.py::test_deploy_refuses_when_gated_file_present_in_public_dir`
- `worker/test/kit.test.ts::no_cookie_gets_gate_response_not_content`, `::tampered_cookie_gets_nothing`, `::expired_cookie_gets_nothing`, `::path_parameter_cannot_select_key` (tries `../`, encoded variants)
- `worker/test/kit.test.ts::gated_response_is_no_store_and_vary_cookie`
- Release checklist item with a command: confirm bucket access policy shows public access disabled (named in `docs/deploy.md`, run by the owner; expected output "disabled").

Residual: Cloudflare configuration drift (someone turns on public R2 access later) is not caught by tests. A periodic owner check, listed in the runbook, covers it.

### T-04 Token tampering, replay and forwarded links

Abuse: forge an unlock; reuse a captured cookie; forward the emailed link; replay an expired token; exploit algorithm or format confusion; use a leaked signing secret.

Design (spec 4.11, as amended): the unlock is a signed token (HMAC with a Worker secret), 24 hours by default, tied to the lead record, carried only in an HttpOnly, Secure, SameSite cookie, never in a URL. The emailed link is single use.

Mitigations and requirements for the plan:
1. Fixed algorithm (HMAC-SHA-256 over a canonical encoded payload, no algorithm field read from the token). Constant-time comparison. Payload: lead id, issued at, expiry, `kid`. Validity is checked against server time.
2. On every kit request the Worker also checks that the lead still exists and is not revoked. Deleting a lead (T-12) or revoking one therefore invalidates its tokens before their expiry.
3. Cookie flags: `HttpOnly; Secure; SameSite=Lax` (or `Strict` if the emailed-link flow still works), `Path` limited to the kit routes, no `Domain` attribute, `__Host-` prefix.
4. The emailed link carries a random token (at least 128 bits) stored hashed, with an expiry (for example 60 minutes). Consumption is one atomic SQL update (`UPDATE ... SET used = 1 WHERE hash = ? AND used = 0 AND expires > now RETURNING lead_id`); a second use gets the gate.
5. Mail scanners (F5): the link opens a landing page that shows a button; the button posts to consume the token and set the cookie. A scanner that only fetches the page does not burn the token. (A fully JavaScript-free form post is enough here.) The page and the redirect both send `Referrer-Policy: no-referrer`, and the Worker's logs never record the query string.
6. The unlock token never appears in a URL, a response body, a log line, or an owner alert.
7. Signing key rotation via `kid` with at most two active keys.

Verification:
- `worker/test/token.test.ts::tampered_payload_rejected`, `::tampered_signature_rejected`, `::expired_rejected`, `::future_issued_at_rejected`, `::unknown_kid_rejected`, `::alg_none_style_token_rejected`, `::truncated_and_empty_rejected`
- `::signature_compare_is_constant_time_helper_used` (static check: a test greps the module for the compare helper and for the absence of `===` on signature strings)
- `::deleted_lead_invalidates_token`
- `worker/test/email_link.test.ts::first_consume_sets_cookie_and_marks_verified`, `::second_consume_gets_gate`, `::forwarded_copy_gets_gate`, `::expired_link_gets_gate`, `::concurrent_consume_exactly_one_wins`, `::get_without_post_does_not_consume`
- `worker/test/cookie.test.ts::cookie_flags_are_httponly_secure_samesite_host_prefix`
- `worker/test/no_leak.test.ts::no_response_or_log_line_contains_unlock_token` (captures all response bodies, headers other than Set-Cookie, console output, and the URL of every redirect)

Residual: a stolen live cookie works for up to 24 hours from another machine; the design accepts that, and the lifetime is configurable and short. A partner who screen-shares or prints the kit cannot be stopped. A leaked signing secret allows forged unlocks until rotation. Reopen if the lifetime is raised or the cookie is ever put in a URL.

### T-05 Fake emails, bots and polluted data

Abuse: bots fill the gate with junk or victim addresses; scripted traffic inflates click and view counts; the owner is alerted on every junk unlock.

Mitigations: Turnstile on the gate; no trust in client-supplied fields (country comes from the Cloudflare header, time from the server); lead email is stored as typed plus a normalized form for dedupe; leads are marked `verified = 0` until the link is used, and reports and alerts distinguish verified from unverified; bot user agents and missing user agent are dropped on `event`; per-client rate limits; alert aggregation (T-14). Counts are documented as approximate. Disposable-domain blocking is optional and off by default.

Verification:
- `worker/test/gate.test.ts::request_without_turnstile_stores_no_lead`, `::client_supplied_country_and_timestamp_ignored`, `::duplicate_address_does_not_create_second_lead`
- `worker/test/click.test.ts` (plan Task 8): bot user agents (`Googlebot/2.1`, `curl/8`, missing), burst limit, identical response for accepted and ignored requests
- `tests/reports/test_leads_report.py::test_unverified_leads_are_labeled_unverified`

Residual: an attacker with a real browser and a Turnstile farm can still add some unverified leads and a modest number of clicks. Stated openly on the counts page and in the walkthrough.

### T-06 Kick stream key and other secrets in platform responses

Abuse: the Kick Channels response includes `stream.key` (research note, verified 2026-10-23). If the collector logs the response, stores it, includes it in a traceback, records it into a test fixture, or exports it, anyone with the file can broadcast as the channel.

Mitigations:
1. Allowlist at the collector edge: parse the response into a typed model that names only the fields needed (live status, viewer count, subscriber counts). Everything else is dropped in the same function that parses the response, before logging, caching or error handling can see it. A denylist for `key` is not enough, because a renamed field would pass.
2. The raw response body is never passed to a logger or an exception message; HTTP error handling logs status code and endpoint only.
3. Test fixtures use synthetic payloads containing a sentinel such as `STREAM-KEY-SENTINEL-0000`. Recorded real responses are forbidden by a repo check.
4. A generic rule for all collectors: the collector interface returns metrics only, so a collector cannot return a free-form blob that reaches the store.
5. If a key is ever logged or committed, the runbook says: rotate the key in Kick's settings first, then clean the history.

Verification:
- `tests/collectors/test_kick.py::test_stream_key_never_reaches_return_value`, `::test_stream_key_never_in_logs` (captures all log records at DEBUG), `::test_stream_key_never_in_exception_text` (forces a parse error), `::test_stream_key_never_in_snapshot_table` (reads every column of every row)
- `tests/test_fixtures_policy.py::test_no_fixture_contains_a_real_looking_token` (rejects long high-entropy strings in `tests/**/fixtures`)
- Secret scanning in CI (T-02).

Residual: the Kick API could rename the field or add another secret. The allowlist makes a new field invisible by default. Reopen if Kick changes the response shape.

### T-07 Open redirect, link-id forgery and abuse of the click endpoint

Abuse: use `POST /e/c/:linkId` to redirect victims, to inject text into logs or the database, to record events for ids that do not exist, or to exhaust the Worker.

Plan 1 removed the redirect route and uses `ping` plus a beacon. That removes the open redirect by construction, as long as no future route adds one.

Mitigations: only the listed route and method; id length cap (for example 40) before any other work; id must match the slug pattern and the `LINK_IDS` allowlist; every outcome returns the same empty `204`; the response never echoes the id; no CORS headers; bot, origin and rate checks as in Task 8; ids are written with parameterized statements; logs never include the raw id. Because ids appear on the public page, the allowlist stops invented ids but not inflation of real ones, which is T-05.

Verification:
- `worker/test/click.test.ts`: the cases already in Task 8, plus `::id_with_encoded_slash_or_dots_records_nothing`, `::id_longer_than_cap_records_nothing_and_is_not_logged`, `::get_head_options_return_same_204_or_405_without_body`, `::response_has_no_location_header_ever`, `::no_cors_headers_on_any_response`
- `tests/site/test_no_redirect_routes.py::test_worker_exposes_no_route_that_returns_3xx` (walks the router table)
- `tests/site/test_click_attrs.py` (plan Task 9) for the `ping` attributes

Residual: counts can be inflated by anyone with patience; documented as approximate.

### T-08 Page-level injection and framing

Abuse: script injection through a link title, subtitle, disclosure text or privacy text; framing the gate page to trick clicks; mixed content; leaking the one-time email token through Referer.

Mitigations: Jinja2 autoescape with `StrictUndefined`, never `from_string` or `|safe` on config text; the schema rejects `javascript:` and `data:` URLs (https only); the site sends a Content Security Policy via `_headers` (`default-src 'self'`; script only from `'self'`; `frame-ancestors 'none'`; `form-action 'self'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` on gate and email-link routes (and `strict-origin-when-cross-origin` elsewhere), and HSTS. The Turnstile script needs an explicit allowance on the gate page only.

Verification:
- `tests/site/test_build.py::test_html_in_titles_is_escaped` (plan Task 6)
- `tests/site/test_templates.py::test_no_safe_filter_or_from_string_on_config_values` (greps templates and `build.py`)
- `tests/site/test_headers.py::test_headers_file_sets_csp_frame_ancestors_and_nosniff` and `::test_gate_page_csp_allows_only_turnstile_origin`
- `worker/test/headers.test.ts::all_worker_responses_carry_nosniff_and_no_store_where_private`
- `tests/site/test_privacy.py::test_banner_text_comes_from_config_when_set` (plan Task 6A; confirm it asserts escaping)

Residual: browsers with weak CSP support. Third-party icon hosts, if ever allowed, would weaken the policy, so icons stay local.

### T-09 Supply chain

Abuse: a malicious or compromised Python or npm package, GitHub Action, base image, or release workflow runs in CI, the image, or the Worker bundle. The Worker bundle is the most sensitive target because it holds D1 access and the signing secret (F2).

Mitigations: hashed Python requirements (`--require-hashes`), `package-lock.json` with `npm ci --ignore-scripts`, a small dependency list for the Worker (ideally zero runtime dependencies), Dependabot for pip, npm and Actions, GYST reusable workflows and third-party actions pinned by commit with a version comment, image bases pinned by digest, explicit `permissions:` on every workflow, no `pull_request_target`, build and deploy jobs separated (T-02), SBOM and provenance on the release image (through GYST container-release where supported), and `verify-published` after release. Dependabot PRs are reviewed, not auto-merged, for the Worker and for workflows.

Verification:
- `tests/test_supply_chain.py::test_requirements_have_hashes`, `::test_every_uses_line_is_a_40_char_sha`, `::test_no_pull_request_target`, `::test_dockerfile_base_is_digest_pinned`, `::test_worker_runtime_dependencies_are_listed_and_few` (fails if the count grows without a change to an allowlist file)
- CI step `npm ci --ignore-scripts` and `npm audit --omit=dev` (report-only gate, reviewed)
- GYST security workflow green; release workflow runs `verify-published` and the container smoke test under Docker and Podman.

Residual: a compromise upstream of a pinned hash still reaches us until detected. A reviewer must read dependency diffs. Reopen on any advisory affecting a direct dependency.

### T-10 Hostile configuration files

Abuse: a config (copied from a shared example, pasted from an issue, or edited by mistake) tries to exhaust memory with aliases, deep nesting or size, to shadow keys with duplicates, to traverse paths via ids, avatars or icons, to inject template syntax, to point at unsafe URLs, or to smuggle a forbidden field past the display policy.

Mitigations: the hardened loader from Plan 1 Task 2 (size cap, depth cap, no duplicates, anchors, aliases or merge keys); closed schema (`extra="forbid"`); slug ids; https-only URLs; length caps on every string; `avatar` and `icon` accepted only as an enumerated icon name or a path that resolves inside the instance directory (reject absolute paths, `..`, symlinks that leave the root); reserved ids (`privacy`, `static`, `e`, `manifest`, `kit`) rejected for tabs so a tab cannot overwrite a generated page; timezone only from the zoneinfo database; config values are data, never templates; the build writes only below `--out` and verifies each resolved path.

Verification:
- `tests/config/test_loader.py` (plan Task 2: duplicate key, anchor, alias, merge key, oversize, deep nesting)
- `tests/config/test_models.py` (plan Task 3) plus new: `::test_avatar_path_traversal_refused`, `::test_icon_absolute_path_refused`, `::test_symlink_leaving_instance_dir_refused`, `::test_reserved_tab_ids_refused`, `::test_string_length_caps`, `::test_timezone_with_path_chars_refused`
- `tests/site/test_build.py::test_build_never_writes_outside_out_dir` (builds a hostile config, snapshots the file tree outside `--out`)
- `tests/config/test_template_syntax.py::test_jinja_syntax_in_titles_renders_literally` (a title of `{{ 7*7 }}` appears literally)

Residual: the owner is trusted to author their own config; the loader defends against mistakes and copied files, not a malicious owner.

### T-11 Platform-terms abuse

Abuse (usually self-inflicted): the site, a partner view or a PDF shows data a platform bars from display, or the store keeps data longer than a platform allows.

Evidence from the notes: YouTube forbids public display of owner-authorized Analytics data (III.E.3.b), and limits retention of non-authorized statistics to 30 days and requires re-verifying authorization every 30 days; LinkedIn terms bar scheduled refresh and public display (numbers must be self-reported); Social Blade data is "for Your use only" and needs credit when shared; several platform terms are still unread (Kick, Twitch, StreamElements, Patreon).

Mitigations: a versioned display-policy table with the platform, field, allowed audiences (public, gated partner, owner-only), source and date (F7 adds the gated audience), and a retention limit per source; the builder refuses to place a forbidden field on a page for a disallowed audience, and this includes the gated bundle and the PDFs; overrides are explicit, logged as build warnings, and never silent; fields marked self-reported are labeled as such; a Social Blade credit appears wherever its data does; unread terms default to the most restrictive audience (owner-only) until a human records the reading with a date.

Verification:
- `tests/policy/test_display_policy.py::test_youtube_analytics_field_on_public_page_fails_build`, `::test_youtube_analytics_field_in_gated_bundle_fails_build`, `::test_youtube_analytics_field_in_pdf_fails_build`, `::test_linkedin_number_requires_self_reported_flag`, `::test_unreviewed_platform_defaults_to_owner_only`, `::test_override_emits_warning_and_is_listed_in_build_report`
- `tests/policy/test_retention.py::test_youtube_unauthorized_stats_purged_after_30_days` and `::test_every_policy_row_has_source_url_and_date`
- `tests/policy/test_credit.py::test_social_blade_credit_present_when_its_data_is_shown`

Residual: the policy is only as good as the readings behind it, and none of this is legal advice. A platform can change terms without notice. The 30-day YouTube retention limit conflicts with the permanent snapshot store as designed; the maintainer must decide whether YouTube data is stored at all, stored only as owner-authorized and kept while consent is verified, or reduced to figures typed in by hand.

### T-12 Personal data retention and deletion

Abuse and failure modes: leads or events kept past the configured window; the purge job silently fails; deletion removes the Worker row but not copies in the local store, exports, Grafana, backups or Mailgun logs; emails appear in logs or owner alerts; an unverified third-party address sits in the table.

Mitigations: raw events purged by a scheduled handler (default 90 days) and leads by age (default 12 months); the scheduled handler records its last success and the owner dashboard shows "last purge" so a silent failure is visible; a single deletion command removes a lead by normalized email from the Worker database and the local store, and prints what it removed; leads never go to Grafana or aggregate tables and exports meant for sharing exclude them; the Worker never logs addresses; the privacy page states honestly the processors involved (Cloudflare, Mailgun, Turnstile) and the retention of their logs and D1 recovery features, as documented by the providers; a deletion request is answered at the contact named on the privacy page. Use the minimal columns spec 4.4 names: no IP address, country at most.

Verification:
- `worker/test/retention.test.ts` (plan Task 9: older than 90 days deleted, newer kept, configurable) plus `::leads_older_than_setting_are_deleted`
- `worker/test/retention.test.ts::scheduled_handler_records_last_success`
- `tests/cli/test_delete_lead.py::test_removes_from_worker_db_and_local_store_and_reports_counts`, `::test_normalization_matches_case_and_plus_tag_policy`, `::test_unknown_email_reports_zero_and_exits_nonzero_or_zero_as_documented`
- `tests/test_exports.py::test_share_export_excludes_leads` and `::test_leads_table_never_read_by_grafana_sink`
- `worker/test/no_leak.test.ts::no_email_address_in_console_output` (shared with T-04)
- `tests/site/test_privacy.py::test_default_disclosure_lists_click_counts_and_says_no_ip_address` (plan Task 6A)

Residual: provider-side copies (Cloudflare recovery windows, Mailgun log retention, the recipient's mailbox) are outside our deletion and are disclosed, not fixed. Backups of the local store keep a deleted lead until they roll off; the runbook states the roll-off period. Reopen if a regulator or a requester challenges the disclosure.

### T-13 Exposure through default hostnames, previews and dangling DNS

Abuse: reach the Worker through its `workers.dev` hostname or a Pages preview URL, where hostname-scoped rules, headers or rate limits are missing; take over the test subdomain after the Pages project is deleted while the DNS record remains.

Mitigations: `workers_dev = false` and no preview routes for the Worker; Pages preview deployments disabled or protected; the Worker accepts requests only when the `Host` equals `SITE_HOST` (this also helps the origin filter); the test subdomain runbook removes the DNS record before deleting the project, and cutover and rollback steps list the DNS records to change.

Verification:
- `worker/test/host.test.ts::request_with_other_host_gets_404_and_records_nothing`
- `tests/test_wrangler_config.py::test_workers_dev_disabled` (parses `wrangler.toml`)
- `docs/deploy.md` includes a checklist item with the command to list DNS records for the test hostname and the expected result after teardown (no record).

Residual: Cloudflare account settings are outside the repo. The owner checks them at cutover.

### T-14 Owner alert channel abuse

Abuse: each unlock triggers a message to email, Matrix or Discord. Flood it with junk unlocks; put markup or a mention in the address (valid local parts can contain many characters) to inject formatting; leak the webhook URL, which is a credential.

Mitigations: aggregate and cap alerts (at most N per hour, then a digest); the alert shows the address only as inert text with all markdown and mention syntax neutralized, or just the domain; webhook URLs and Matrix tokens are Worker secrets; unverified leads are labeled in the alert.

Verification:
- `worker/test/alert.test.ts::alert_text_neutralizes_markdown_and_mentions`, `::more_than_cap_unlocks_collapse_to_digest`, `::webhook_url_never_in_logs_or_responses`

Residual: a patient attacker can still produce a steady trickle of low-value alerts.

### T-15 Snapshot store integrity and token health

Abuse and failure: a failing collector writes zero, so a partner sees a collapse; an imported CSV with a formula injection (`=cmd...`) runs in a spreadsheet when the owner opens an export; a refresh token write-back fails and the collector loses access silently; a poisoned import or a typed-in number is shown as fetched.

Mitigations: a collector failure records "stale since", never a value; each row records its source (fetched, imported, typed); exports neutralize leading `=`, `+`, `-`, `@` in text cells; token registry alerts well before expiry; token write-back uses an atomic file replace with restrictive permissions; imports are validated against a schema and size cap.

Verification:
- `tests/store/test_stale.py::test_collector_failure_never_writes_zero`
- `tests/export/test_csv.py::test_formula_prefix_cells_are_neutralized`
- `tests/tokens/test_registry.py::test_alert_fires_before_expiry_window` and `::test_refresh_write_back_is_atomic_and_0600`
- `tests/imports/test_import.py::test_imported_rows_carry_source_imported`

Residual: the store is trusted local state; a person with access to the collector host can alter history.

## 7. Residual risk summary

| Risk | Accepted by | Reopen when |
|---|---|---|
| Turnstile farms can relay up to the daily budget (T-01) | Maintainer | Mailgun warning, complaint rate, or any blocklisting |
| A live cookie can be used for up to its lifetime (T-04) | Maintainer | Lifetime raised, or any URL carries a token |
| Approximate counts can be inflated (T-05, T-07) | Maintainer | Counts are used in a pricing negotiation |
| Provider-side copies of personal data (T-12) | Maintainer, disclosed on the privacy page | A deletion request is disputed |
| Upstream compromise inside a pinned dependency (T-09) | Maintainer | An advisory or a changed hash |
| Platform terms change or are misread (T-11) | Maintainer | Any platform terms update; each dated reading expires after a set interval (suggest 6 months) |
| Cloudflare account configuration drift (T-03, T-13) | Maintainer | Quarterly owner check fails |

## 8. Abuse tests the plans must contain

Each item names the control it proves. A plan author can copy an item into a task as a failing test to write first. File names are suggestions; keep the test name or the described behavior. "Red without it" means the test must fail when the control is removed, and the plan should include a step that removes the control once to prove it.

1. `worker/test/send.test.ts::missing_or_failed_turnstile_sends_nothing` - proves server-side Turnstile verification on `send` (T-01).
2. `worker/test/send.test.ts::reused_turnstile_token_sends_nothing` - proves Turnstile tokens are single use (T-01).
3. `worker/test/send.test.ts::one_message_per_address_per_day` - proves the per-address limit (T-01).
4. `worker/test/send.test.ts::newline_in_address_is_rejected` and `::address_list_is_rejected` - proves recipient validation and header-injection defense (T-01).
5. `worker/test/send.test.ts::message_body_has_no_visitor_text` - proves the fixed template, with a hostile `name` field in the request (T-01).
6. `worker/test/send.test.ts::concurrent_requests_cannot_exceed_daily_budget` - proves the atomic global budget (T-01).
7. `worker/test/send.test.ts::budget_spent_still_opens_kit_and_same_response_shape` - proves graceful degradation and no recipient oracle (T-01).
8. `worker/test/send.test.ts::turnstile_outage_fails_closed` - proves the documented outage decision (T-01).
9. `tests/site/test_gate_separation.py::test_public_output_contains_no_gated_sentinel` - proves gated content never reaches the Pages bundle (T-03).
10. `tests/test_deploy_guard.py::test_deploy_refuses_when_gated_file_present_in_public_dir` - proves the deploy-time backstop to the build separation (T-03).
11. `worker/test/kit.test.ts::no_cookie_gets_gate_response_not_content`, `::tampered_cookie_gets_nothing`, `::expired_cookie_gets_nothing` - proves the token check precedes any store read (T-03, T-04).
12. `worker/test/kit.test.ts::path_parameter_cannot_select_key` - proves the Worker, not the client, chooses the store key (T-03).
13. `worker/test/kit.test.ts::gated_response_is_no_store_and_vary_cookie` - proves gated content is never shared-cached (T-03).
14. `worker/test/token.test.ts::tampered_payload_rejected`, `::tampered_signature_rejected`, `::unknown_kid_rejected`, `::alg_none_style_token_rejected`, `::truncated_and_empty_rejected` - proves signature verification with a fixed algorithm (T-04).
15. `worker/test/token.test.ts::expired_rejected` and `::future_issued_at_rejected` - proves lifetime checks (T-04).
16. `worker/test/token.test.ts::deleted_lead_invalidates_token` - proves tokens are bound to a live lead record, so deletion revokes (T-04, T-12).
17. `worker/test/token.test.ts::key_rotation_accepts_old_and_new_then_rejects_retired` - proves `kid` rotation works (T-02, T-04).
18. `worker/test/email_link.test.ts::first_consume_sets_cookie_and_marks_verified` and `::second_consume_gets_gate` - proves the single-use link (T-04).
19. `worker/test/email_link.test.ts::forwarded_copy_gets_gate` - proves the forwarded link lands on the gate and records a new unlock event (T-04).
20. `worker/test/email_link.test.ts::concurrent_consume_exactly_one_wins` - proves atomic consumption (T-04).
21. `worker/test/email_link.test.ts::get_without_post_does_not_consume` - proves mail scanners cannot burn the link (T-04).
22. `worker/test/cookie.test.ts::cookie_flags_are_httponly_secure_samesite_host_prefix` - proves cookie attributes (T-04).
23. `worker/test/no_leak.test.ts::no_response_or_log_line_contains_unlock_token` - proves "no URL carries the token" for the unlock token (T-04).
24. `worker/test/no_leak.test.ts::no_email_address_in_console_output` - proves addresses are not logged (T-12).
25. `worker/test/gate.test.ts::request_without_turnstile_stores_no_lead`, `::client_supplied_country_and_timestamp_ignored`, `::duplicate_address_does_not_create_second_lead` - proves the gate trusts no client field and dedupes (T-05).
26. `worker/test/click.test.ts` (Task 8 cases) plus `::id_with_encoded_slash_or_dots_records_nothing`, `::id_longer_than_cap_records_nothing_and_is_not_logged`, `::response_has_no_location_header_ever`, `::no_cors_headers_on_any_response` - proves the allowlist, the uniform response and the absence of redirects (T-07).
27. `tests/site/test_no_redirect_routes.py::test_worker_exposes_no_route_that_returns_3xx` - proves no open redirect route exists, now or later (T-07).
28. `worker/test/host.test.ts::request_with_other_host_gets_404_and_records_nothing` and `tests/test_wrangler_config.py::test_workers_dev_disabled` - proves default hostnames are closed (T-13).
29. `tests/site/test_build.py::test_html_in_titles_is_escaped`, `tests/site/test_templates.py::test_no_safe_filter_or_from_string_on_config_values`, `tests/config/test_template_syntax.py::test_jinja_syntax_in_titles_renders_literally` - proves escaping and no template injection (T-08, T-10).
30. `tests/site/test_headers.py::test_headers_file_sets_csp_frame_ancestors_and_nosniff` and `::test_gate_page_csp_allows_only_turnstile_origin` - proves security headers (T-08).
31. `tests/config/test_loader.py` (duplicate key, anchor, alias, merge key, oversize, deep nesting) - proves the hardened loader (T-10).
32. `tests/config/test_models.py::test_https_urls_only`, `::test_avatar_path_traversal_refused`, `::test_icon_absolute_path_refused`, `::test_symlink_leaving_instance_dir_refused`, `::test_reserved_tab_ids_refused`, `::test_string_length_caps`, `::test_timezone_with_path_chars_refused` - proves the closed schema (T-10).
33. `tests/site/test_build.py::test_build_never_writes_outside_out_dir` - proves path confinement during the build (T-10).
34. `tests/collectors/test_kick.py::test_stream_key_never_reaches_return_value`, `::test_stream_key_never_in_logs`, `::test_stream_key_never_in_exception_text`, `::test_stream_key_never_in_snapshot_table` - proves the allowlist at the collector edge (T-06).
35. `tests/test_fixtures_policy.py::test_no_fixture_contains_a_real_looking_token` - proves no recorded secrets in fixtures (T-02, T-06).
36. `tests/collectors/test_redaction.py::test_logger_redacts_authorization_and_token_fields` - proves log redaction (T-02).
37. `tests/test_no_secrets_in_repo.py::test_workflows_pass_secrets_only_to_deploy_job` and `tests/test_workflows.py::test_deploy_workflow_permissions_are_contents_read`, `::test_actions_are_pinned_by_sha` - proves secret scoping and workflow pinning (T-02, T-09).
38. `worker/test/config.test.ts::wrangler_vars_contain_no_secret_names` - proves secrets are not in plain variables (T-02).
39. `tests/test_supply_chain.py::test_requirements_have_hashes`, `::test_every_uses_line_is_a_40_char_sha`, `::test_no_pull_request_target`, `::test_dockerfile_base_is_digest_pinned`, `::test_worker_runtime_dependencies_are_listed_and_few` - proves supply chain pins (T-09).
40. `tests/policy/test_display_policy.py::test_youtube_analytics_field_on_public_page_fails_build`, `::test_youtube_analytics_field_in_gated_bundle_fails_build`, `::test_youtube_analytics_field_in_pdf_fails_build`, `::test_unreviewed_platform_defaults_to_owner_only`, `::test_override_emits_warning_and_is_listed_in_build_report` - proves the display policy by audience (T-11).
41. `tests/policy/test_retention.py::test_youtube_unauthorized_stats_purged_after_30_days` and `::test_every_policy_row_has_source_url_and_date` - proves per-source retention limits (T-11).
42. `worker/test/retention.test.ts::leads_older_than_setting_are_deleted`, `::scheduled_handler_records_last_success` - proves lead retention and a visible purge heartbeat (T-12).
43. `tests/cli/test_delete_lead.py::test_removes_from_worker_db_and_local_store_and_reports_counts` - proves deletion reaches both stores (T-12).
44. `tests/test_exports.py::test_share_export_excludes_leads` and `::test_leads_table_never_read_by_grafana_sink` - proves leads stay local (T-12).
45. `worker/test/alert.test.ts::alert_text_neutralizes_markdown_and_mentions`, `::more_than_cap_unlocks_collapse_to_digest`, `::webhook_url_never_in_logs_or_responses` - proves alert hygiene (T-14).
46. `tests/store/test_stale.py::test_collector_failure_never_writes_zero`, `tests/export/test_csv.py::test_formula_prefix_cells_are_neutralized`, `tests/tokens/test_registry.py::test_alert_fires_before_expiry_window` - proves store honesty and token health (T-15).
47. `tests/test_deploy_environment.py::test_deploy_job_uses_protected_environment` - proves the deploy restriction lives in GitHub and not only in the workflow file (T-02, F10). Pair with a documented manual check of the environment's branch policy.
48. Mutation step for every test above: each plan task includes one step that removes the control, confirms the test goes red, then restores it. Proves the tests bite and are not decorative.

## 9. What this model did not cover

- No code exists yet; every control is a requirement on a plan, and every test name is a proposal. Re-run this model against the implemented Worker before the gate ships.
- Cloudflare, Mailgun and Turnstile behavior was taken from the design and general knowledge, not from fresh reading of their current documentation. In particular, whether a read-only Cloudflare token can run the D1 queries the collector needs is unknown (F12); the permissions D1 queries require, Turnstile outage semantics, rate-limiting binding accuracy across locations, and R2 and KV access defaults need checking against current documentation before the plan commits to them.
- The terms of Kick, Twitch, StreamElements, Patreon, X, TikTok, Instagram and Threads have not been read by a human; T-11 treats them as restrictive by default.
- Legal questions (privacy law, retention duties, terms interpretation) are not addressed and this is not legal advice.
- Denial of service at the network level, Cloudflare account takeover, endpoint security of the owner's machines, and physical access are out of scope.
- The Grafana, Zoho Books, executive summary and PDF rendering stacks are not designed yet; the PDF toolchain (a headless browser rendering local data) needs its own review when chosen.
- No active testing was done or authorized.
