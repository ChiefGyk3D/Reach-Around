# Reach Around threat model

Status: draft for maintainer review, revision 2 (2026-10-24, issue #49), plus the local-editor addendum in section 10 (2026-10-25, issue #158). Revision 1 (2026-10-23) was written from the design only. Revision 2 answers an independent review of that draft and adds what has shipped since: the Worker click endpoint and retention job, instance template overrides with the strict CSP, and the asset pipeline. Nothing was run against a live system and no system was probed. Test names marked "proposed" do not exist yet; names marked "exists" were found in the repository.

Inputs read as data: the design spec (`docs/superpowers/specs/2026-10-23-reach-around-design.md`, including the section 4.11 amendment), Plan 1 (`docs/superpowers/plans/2026-10-23-plan-1-foundation-and-link-page.md`), the roadmap, the platform research notes in `docs/research/`, and, new in revision 2, the code in `worker/src`, `src/reach_around/site/{overrides,assets,assetcheck}.py`, their tests, and `docs/education/csp-and-overrides.md`. Where those documents contradict each other, this model says so and picks the safer reading.

How mitigations are marked. Every mitigation carries one of three marks:

- **[shipped]** the code is on `main` and a test exists. The test name is given.
- **[planned: where]** not built. "where" is a Plan 1 task, or a roadmap phase and work package (the step-level plan for phases after Plan 1 is not written yet, so no task number exists for those).
- **[open: question]** the maintainer has to decide before it can be planned. Section 7a lists every open decision in one place.

A mitigation with no mark is a defect in this document. Rank numbers in section 6 were re-ordered in revision 2; risk IDs were not renumbered, and new risks start at T-16.

This is a ranked list of how the design can be abused and what proves each defense works. It is not an audit, and it does not say the system is secure. Section 9 lists what it did not cover.

A plain-language version for readers learning from the project is in `docs/education/threat-model-walkthrough.md`.

## 1. Scope

In scope: the engine (config loader, site builder, instance-file asset pipeline and template overrides), the Cloudflare Pages site, the Worker (click, gate, event, alert, send), D1 and the gated bundle store, the collectors and snapshot store, the collector host's Cloudflare read token, the CI and deploy workflows, the container image, the private PDF and export path on the owner's machine (renderer, temp files, output), and the platform terms that limit what may be shown.

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
| A14 | Worker and D1 availability and spend (request quota, D1 rows read and written, storage, log volume, Mailgun budget, and on a paid plan the bill) | Availability and money | The Worker is one account-wide resource. Exhausting its request or storage quota on the click route also stops the gate, the kit and the unlock link; on a paid plan the same traffic is a bill |
| A15 | Private exports and PDFs (full media kit PDF, executive summary, share export) and their temp files | Business confidential, may hold personal data | Rendered from the same data as the gated bundle; a leak is as bad as a gate bypass and has no gate in front of it |
| A16 | The display-policy table and the build that enforces it | Integrity | If an override or a code path skips it, every surface (site, gated bundle, assets, exports, PDFs) can show barred data |
| A17 | Inventory of every copy of lead data (Worker database, local store, exports, backups, provider logs) | Personal data | A deletion or retention promise holds only for copies someone has listed |

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
| X11 | Compromised or tricked renderer (the headless browser or PDF library that makes the private PDFs) | Runs on the owner's machine with read access to the data it renders and, by default, network access and a writable temp area; hostile text or an image in the rendered data can steer it |
| X12 | Distributed anonymous flood (many addresses, valid-looking user agents, forged `Ping-From`) | Defeats per-client limits by spreading requests; aims at quota, storage, log volume or the bill rather than at data |
| X13 | Holder of a forwarded or intercepted emailed link | Not the lead; has the single-use URL before the lead does (forward, shared mailbox, mail-gateway copy, proxy log) |
| X14 | Holder of a stolen collector or CI credential | Has one platform token, the Cloudflare read token, or a CI secret, from a leaked log, a laptop, a fork-PR run or a breached dependency |

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
 [B13] snapshot store / gated data ---> renderer input (PDF, executive summary, share export)
 [B14] renderer ----------------------> filesystem (output file, temp files, caches, logs)
 [B15] filesystem / export ------------> recipient (email attachment, shared drive, a partner)
 [B16] renderer ----------------------> network (remote images, fonts, stylesheets it is asked to fetch)
 [B17] instance files and overrides --> build (assets, templates, custom.css; shipped)
 [B18] fork or PR code ---------------> CI jobs that can see secrets
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
| B13 | Stored data (some of it hostile text: names, case-study copy, platform strings) | Renderer | Render from a typed allowlist of fields, never from free-form blobs; leads and owner-only fields excluded by construction |
| B14 | Renderer process | Filesystem | Private temp directory, output only to a named path, nothing left behind, restrictive permissions |
| B15 | Owner's machine | Recipient | Strip metadata, name files neutrally, treat the file as disclosed once sent; never upload to Worker storage |
| B16 | Renderer | Network | No network at all while rendering; every asset local |
| B17 | Instance directory and overrides | Build | Path confinement, content screening, allowlisted SVG, template sandbox, CSS refusals (shipped; see T-20) |
| B18 | Untrusted code from a fork | Secrets | No secrets, no `pull_request_target`, a protected environment for anything that holds a credential |

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
- F13. Spec 4.6 says "when the budget is spent the kit still opens, just without the email". That is a fail-open choice for the email path. The same decision is not written down for Turnstile verification outages, and it must be (see T-01). Revision 2 checked: the spec still does not record it, so it is an open decision (D-02).
- F14 (new, from code). `worker/src/click.ts` never reads the request body, so a large body is not parsed. Whether the platform still accepts and bills a very large upload to the route is not controlled by this code and is unverified. The id from the URL path is run through `decodeURIComponent` before the allowlist check with no length cap on the path. No body-size or path-length cap is written down for the gate and `send` routes either.
- F15 (new, from code). The only per-request limiter is `CLICK_LIMITER`, keyed on `CF-Connecting-IP` (20 per 60 seconds per client in `wrangler.toml`). It does not cap a distributed flood. Requests that pass the user-agent, origin and limiter checks each write a D1 row, and nothing caps the row count between purges (daily at 04:17 UTC, 90-day window). The `Ping-From` and `Origin` check is a header the sender controls (F3), so it does not bound an attacker who reads the public page.
- F16 (new, from code). There is no kill switch, no per-route global budget, no storage quota alarm and no log policy. `wrangler.toml` sets no `observability` block and no `workers_dev` value, so the logging and default-hostname behavior is whatever the account defaults are; this model has not checked those defaults against current documentation.
- F17 (new, from code). `worker/src/index.ts` sends every request, to any path and any `Host`, to `handleClick`, which returns 204. T-13 proposes a `Host` check that returns 404; today neither the check nor the 404 exists, and `worker/test/host.test.ts` does not exist. Today the effect is harmless (nothing is recorded for a wrong host because the origin check fails), but the claim in T-13 is a plan, not a fact.
- F18 (new, review). Spec 4.11 says the token is "[Decided: not forwardable]". What the design can actually enforce is narrower: a single-use link is dead after its first redemption, and the cookie is not placed in a URL. Neither stops a forwarded copy that is redeemed first, nor replay of a stolen cookie from another device. T-21 narrows the claim and lists binding options.
- F19 (new, review). The spec says an override of the display policy "logs a build warning". A warning is not a control: nothing stops a copied example config from carrying the override, and the build still succeeds and publishes. T-18 replaces it with an explicit approval.
- F20 (new, review). Spec 4.10 and 4.11 require that leads never reach exports meant for sharing, but nothing defines the registry of exports and backups that the deletion command must reach, and unverified leads (F6) have the same 12-month ceiling as verified ones. T-17 adds both.
- F21 (new, Copilot review on PR 54). Three wording defects in revision 1, fixed here: the `__Host-` cookie prefix requires `Path=/`, which contradicts "Path limited to the kit routes" (T-04 item 3 now chooses one); the Turnstile widget needs `frame-src` and `connect-src` as well as `script-src` (T-08); and T-03 said the full PDF is uploaded to the store, which contradicts spec 4.10 (the private PDF is never uploaded; T-03 item 6 and T-16 now say so).
- F22 (new, roadmap). The roadmap builds the gated bundle, the gate and the PDFs in Phase 3 and the display-policy table and its build enforcement in Phase 5. Phase 3 would therefore ship surfaces that have no audience filter at all, and T-11 and T-18 depend on one. Recorded as D-11.
- F23 (new, from code). `collect_assets` copies an instance image into the site byte for byte after a signature and trailer check. Nothing strips metadata, so a photo that carries a GPS location, a camera serial or an author name is published with it. Recorded in T-20 and D-08.
- F24 (new, from code). An override template can print anything in its render context, and the immutable sandbox does not limit that. The policy has to remove data before it enters any context; a template cannot be trusted to leave a field out. Recorded in T-18.

## 6. Risk register

Impact: 1 (minor) to 5 (severe). Prerequisite: how hard it is to start (1 needs a skilled insider or a merged PR, 5 needs only an HTTP client). Rank is a judgment, weighted toward high impact reachable by anyone, and reflects that a public Worker with an email sender will be attacked within days of going live. "Phase" is the roadmap phase where the control is built.

| Rank | ID | Abuse case (actor does X to reach Y) | Impact | Prereq | Phase |
|---|---|---|---|---|---|
| 1 | T-01 | X1/X3 posts many addresses (victims or throwaways) to the email form to relay mail, burn the Mailgun quota, or damage the sending domain | 4 | 5 | 3 |
| 2 | T-07 | X1/X12 floods the anonymous Worker routes (click now; gate, kit, send later) to exhaust the request quota, D1 storage or log volume, or run up the bill, which also takes down the gate and the kit; plus link-id forgery and log injection | 4 (was 2) | 5 | 1, 3 |
| 3 | T-03 | X1/X4 reads gated content without a valid unlock (static bundle leak, shared-cache hit, missing check, public R2) | 4 | 4 | 3 |
| 4 | T-04 | X1/X2 forges, replays or shares an unlock (stolen cookie, secret leak, algorithm confusion) | 4 | 3 | 3 |
| 5 | T-21 | X13 redeems a forwarded or intercepted emailed link before the lead does, so "not forwardable" is not enforced | 3 | 3 | 3 |
| 6 | T-02 | X6/X5/X8 obtains a deploy token, the signing secret, or a collector credential from CI, logs, the repo or a compromised dependency | 5 | 2 | 1, 3 |
| 7 | T-19 | X14 uses a stolen collector, Cloudflare read or OAuth refresh token and the owner cannot enumerate, revoke or rotate it quickly | 5 | 2 | 2, 5 |
| 8 | T-05 | X1 posts forged and bot events and fake emails to pollute leads, counts and owner alerts | 3 | 5 | 1, 3 |
| 9 | T-06 | Kick stream key, or any secret, reaches a log, the store, a fixture or an export | 5 | 3 | 1 or 2 (collector) |
| 10 | T-16 | X11 or a careless export leaks gated content or leads through the renderer, its network fetches, temp files or file metadata | 4 | 3 | 3, 4 |
| 11 | T-18 | X7/X8 uses an instance override, a template or a new output surface to publish data the display policy bars, or private fields | 4 | 3 | 3, 5 |
| 12 | T-11 | Platform-terms abuse: a build shows owner-authorized or terms-barred data publicly or to partners, or stores it past allowed time | 3 | 4 (self-inflicted) | 2, 3 |
| 13 | T-17 | Lead data outlives its purpose: unverified third-party addresses, exports, backups and provider copies with no ceiling, and deletion that misses a copy | 4 | 3 | 3 |
| 14 | T-12 | Personal data retention and deletion failure: leads kept past the setting, deletion that leaves copies, emails in logs or alerts | 3 | 3 | 1, 3 |
| 15 | T-08 | Page-level injection and framing: XSS through config text, clickjacking of the gate, mixed content | 3 | 3 | 1 |
| 16 | T-10 | X7 hostile or careless configuration: YAML bombs, aliases, path traversal, template injection, bad URLs | 3 | 3 | 1 |
| 17 | T-09 | X5/X6 supply chain: dependency, GitHub Action, base image, or release workflow is tampered | 5 | 2 | 1 |
| 18 | T-13 | Worker and Pages exposure through default hostnames, preview deployments, dangling DNS on the test subdomain | 3 | 4 | 1 |
| 19 | T-20 | The shipped instance-file pipeline and override loader are bypassed or abused (path escape, hostile SVG, template reach, CSS that fetches or hides content, metadata in published images) | 3 | 3 | 1 (shipped) |
| 20 | T-14 | Owner alert channel abuse: flooding, markdown or mention injection through an email, webhook URL leak | 2 | 4 | 3 |
| 21 | T-15 | Snapshot store integrity and token-health failure: stale or zero values shown as fact, a poisoned import | 2 | 2 | 2 |

Why the ranks moved in revision 2. T-07 was ranked 7 with impact 2 because the only thing at stake was an approximate counter. That reasoning was wrong for two reasons. The Worker and its D1 database are one account-wide resource (A14), so a flood on the cheapest route spends the same request and storage quota that the gate and the kit will need, and on a paid plan it is a bill. And the per-client limiter does not bound a distributed flood (F15). Anyone with an HTTP client can start it, so it joins T-01 at the top. T-21 sits just below T-04 because the prerequisite (holding the emailed link before the lead) is easy, but the result is one extra viewer of the kit, not a mass disclosure. T-19 and T-02 stay high on impact despite a heavier prerequisite. T-17 is new in the register but not new in kind: it was folded into T-12 before and had no ceiling.

### T-01 Spam relay and quota exhaustion through the email form

Abuse: an attacker posts the gate or `send` form with many victim addresses. Even with a fixed template, each message arrives from the owner's domain, naming the owner, and a flood to one victim or to many victims harms the sending reputation and can get the Mailgun account suspended. A second goal is denial of service: spend the daily budget so a real partner cannot get "email me a link".

Why it is ranked first: anonymous, needs only an HTTP client, the payoff is immediate, and the controls are the only barrier.

Status: every mitigation below is **[planned: Phase 3, work package "Email: Mailgun send, single-use link, limits, budget" and "Gate flow: Turnstile, lead, lead-bound cookie"]**; none exists in `worker/src` yet. The Turnstile outage choice is **[open: D-02]**. The body-size and global-budget points that apply to every anonymous route are in T-07 and apply here too.

Mitigations (design 4.6 plus these specifics):
1. `send` is a separate route from `gate`; the gate never sends mail. The kit opens without email (instant reveal), so mail is only sent on a deliberate second action.
2. Turnstile is verified server-side on the Worker (siteverify) on every gate and send request, with the hostname and an action name checked. A token is single use. If Turnstile cannot be reached the request fails closed for `send`; for `gate` the choice is a documented decision for the maintainer (default recommended: fail closed and alert), because fail open makes an outage the way around the control. [open: D-02, the spec does not record the choice for `gate`; a reviewer also asked for it to be recorded in the spec.]
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

Status: items 1 to 4 and 7 are **[planned: Plan 1 Task 11 for the deploy workflow; Phase 3 for the signing-key `kid`]**. Item 5 is partly **[shipped]**: the repo calls the GYST security workflow through `ci.yml`, and `persist-credentials: false` is set on checkouts (`.github/workflows/worker-ci.yml`). Item 6 is **[planned: Phase 2 collector framework]**. Token inventory, rotation owners, revocation and fork-PR isolation moved to T-19 so they are not buried here.

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

Status: **[planned: Phase 3, work package "Gated bundle and Worker serving"]** for all items; no gated bundle, kit route or gate exists on `main` today. The only Worker route that exists is the click route, and it returns data to no one.

Mitigations:
1. The builder writes the gated bundle to a separate directory and never to the Pages output. The Pages deploy step refuses to run if the output directory contains any file listed in the gated manifest.
2. The gated store has no public access. Documentation requires R2 public access off and no custom domain on the bucket; the Worker binding is the only reader.
3. Every gated response sends `Cache-Control: private, no-store` and `Vary: Cookie`; the Worker never calls the cache API for gated responses.
4. The kit fetch route looks up the content by a fixed key chosen by the Worker, never by a client-supplied path.
5. The token check happens before any store read. A missing, expired, tampered, or revoked token gets the same response as no token.
6. The gated bundle is not a CI artifact with long retention; the build uploads it straight to the store from the deploy job, and artifact retention is set to the minimum. The private full PDF is never uploaded, never a CI artifact and never in the store (spec 4.10); it is rendered only on the owner's machine (T-16). Revision 1 listed the PDF here by mistake.
7. The deploy guard in item 1 also refuses `*.pdf` and export archives in the Pages output, so a rendered file dropped into the wrong directory cannot publish.

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

What this does and does not prove. The spec calls the unlock "not forwardable". Revision 2 narrows that: the design enforces that (a) the unlock token is never in a URL, so copying the address bar gives nobody a pass, and (b) an emailed link dies after its first redemption. It does not enforce that the person who redeems the link is the person it was sent to, and it does not stop a stolen live cookie from working elsewhere. The first of those is its own risk, T-21, with the binding options. This section keeps the token-forgery and replay items.

Status: **[planned: Phase 3, "Gate flow: Turnstile, lead, lead-bound cookie" and "Email: Mailgun send, single-use link, limits, budget"]** for all items. Nothing here exists on `main`.

Mitigations and requirements for the plan:
1. Fixed algorithm (HMAC-SHA-256 over a canonical encoded payload, no algorithm field read from the token). Constant-time comparison. Payload: lead id, issued at, expiry, `kid`. Validity is checked against server time.
2. On every kit request the Worker also checks that the lead still exists and is not revoked. Deleting a lead (T-12) or revoking one therefore invalidates its tokens before their expiry.
3. Cookie flags: `HttpOnly; Secure; SameSite=Lax` (or `Strict` if the emailed-link flow still works), no `Domain` attribute, and the `__Host-` prefix. The `__Host-` prefix requires `Path=/`, so revision 1's "Path limited to the kit routes" cannot be combined with it. This model picks the prefix and `Path=/`: the cookie is only ever checked by the kit routes, so path scoping was never a security boundary. [open: D-03 if the maintainer prefers path scoping over the prefix.]
4. The emailed link carries a random token (at least 128 bits) stored hashed, with an expiry (for example 60 minutes). Consumption is one atomic SQL update (`UPDATE ... SET used = 1 WHERE hash = ? AND used = 0 AND expires > now RETURNING lead_id`); a second use gets the gate.
5. Mail scanners (F5): the link opens a landing page that shows a button; the button posts to consume the token and set the cookie. A scanner that only fetches the page does not burn the token. (A fully JavaScript-free form post is enough here.) The page and the redirect both send `Referrer-Policy: no-referrer`, and the Worker's logs never record the query string.
6. The unlock token never appears in a URL, a response body, a log line, or an owner alert.
7. Signing key rotation via `kid` with at most two active keys.

Verification:
- `worker/test/token.test.ts::tampered_payload_rejected`, `::tampered_signature_rejected`, `::expired_rejected`, `::future_issued_at_rejected`, `::unknown_kid_rejected`, `::alg_none_style_token_rejected`, `::truncated_and_empty_rejected`
- `::signature_compare_is_constant_time_helper_used` (static check: a test greps the module for the compare helper and for the absence of `===` on signature strings)
- `::deleted_lead_invalidates_token`
- `worker/test/email_link.test.ts::first_consume_sets_cookie_and_marks_verified`, `::second_consume_gets_gate`, `::forwarded_copy_redeemed_after_first_gets_gate` (proves only the second redemption; the first-redeemer case is T-21), `::expired_link_gets_gate`, `::concurrent_consume_exactly_one_wins`, `::get_without_post_does_not_consume`
- `worker/test/cookie.test.ts::cookie_flags_are_httponly_secure_samesite_host_prefix` (asserts `Path=/` and no `Domain`, the only combination `__Host-` accepts)
- `worker/test/no_leak.test.ts::no_response_or_log_line_contains_unlock_token` (captures all response bodies, headers other than Set-Cookie, console output, and the URL of every redirect)

Residual: a stolen live cookie works for up to 24 hours from another machine; no control in this section stops that. T-21 lists a per-lead session cap and a shorter lifetime as options the maintainer can choose; until one is chosen the lifetime is the only bound. A partner who screen-shares or prints the kit cannot be stopped. A leaked signing secret allows forged unlocks until rotation. Reopen if the lifetime is raised or the cookie is ever put in a URL.

### T-05 Fake emails, bots and polluted data

Abuse: bots fill the gate with junk or victim addresses; scripted traffic inflates click and view counts; the owner is alerted on every junk unlock.

Status: Turnstile, lead and alert items are **[planned: Phase 3]**. The click-side items (bot user agents dropped, per-client limit, same response for accepted and ignored requests, country from the Cloudflare header only) are **[shipped]** in `worker/src/click.ts` (tests exist in `worker/test/click.test.ts`: "bot user agent records nothing", "burst from one client is limited", "response is identical for accepted and ignored requests", "stores a two-letter country from Cloudflare and nothing like an IP"). A distributed flood is T-07.

Mitigations: Turnstile on the gate; no trust in client-supplied fields (country comes from the Cloudflare header, time from the server); lead email is stored as typed plus a normalized form for dedupe; leads are marked `verified = 0` until the link is used, and reports and alerts distinguish verified from unverified; bot user agents and missing user agent are dropped on `event`; per-client rate limits; alert aggregation (T-14). Counts are documented as approximate. Disposable-domain blocking is optional and off by default.

Verification:
- `worker/test/gate.test.ts::request_without_turnstile_stores_no_lead`, `::client_supplied_country_and_timestamp_ignored`, `::duplicate_address_does_not_create_second_lead`
- `worker/test/click.test.ts` (plan Task 8): bot user agents (`Googlebot/2.1`, `curl/8`, missing), burst limit, identical response for accepted and ignored requests
- `tests/reports/test_leads_report.py::test_unverified_leads_are_labeled_unverified`

Residual: an attacker with a real browser and a Turnstile farm can still add some unverified leads and a modest number of clicks. Stated openly on the counts page and in the walkthrough.

### T-06 Kick stream key and other secrets in platform responses

Abuse: the Kick Channels response includes `stream.key` (research note, verified 2026-10-23). If the collector logs the response, stores it, includes it in a traceback, records it into a test fixture, or exports it, anyone with the file can broadcast as the channel.

Status: **[planned: Phase 2, work package "Kick collector (strip the stream key)" and "Collector framework and scheduler"]**. No collector code exists on `main`.

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

### T-07 Anonymous Worker cost exhaustion, link-id forgery and the click endpoint

Abuse: anyone with an HTTP client sends a high volume of requests to the public Worker routes to use up the free request quota, fill D1 with rows, fill the logs, or run up the bill on a paid plan. The first route is `POST /e/c/:linkId`; the gate, kit and `send` routes join it in Phase 3 and share the same account quota (A14). Secondary goals: redirect victims, inject text into logs or the database, or record events for ids that do not exist.

Why it was re-ranked. Revision 1 rated this impact 2 (counts only). The loss is larger: when the quota is gone the gate and the kit stop for real partners, which is the denial of service T-01 already treats as serious for the email budget. Per-client limits cannot cap it, because a distributed sender never repeats a client key.

What the code does today (`worker/src/click.ts`, `index.ts`, `retention.ts`, `wrangler.toml`) and what it does not:

| Control | State |
|---|---|
| Only `POST /e/c/:id` can record anything; every other method and path returns the same empty 204; no redirect route | **[shipped]**; tests `only POST on /e/c/:id records; other methods do not`, `never redirects, whatever the caller supplies` |
| Request body never read on the click route. After the kill switch, method, URL, path and id checks, a request with a body is ignored (uniform 204, nothing recorded) unless `Content-Length` is present, plain digits, at most 1 KiB (`MAX_BODY_BYTES`), and no `Transfer-Encoding` is set; in every case the stream is cancelled without a single read, so a huge first chunk or a lying length is never pulled | **[shipped]**; tests in `worker/test/click.test.ts` under "cost guards" (`one huge first chunk is never read`, `lying low content-length with a huge body`, `body with no content-length is ignored`, `transfer-encoding with a body`, `wrong method, wrong path and unlisted id never touch the body`) |
| Check order: kill switch, method, URL/path/id length caps, id allowlist, body check, user agent (512 cap and bot list), origin, rate limit, insert; ignored requests spend no limiter budget | **[shipped]**; tests `checks run in order: ignored requests do not spend rate-limit budget`, `a 600-character non-bot user agent records nothing` |
| Per-client rate limit (20 per 60 s). The `CLICK_LIMITER` key is a purpose-separated (`clk`) keyed hash of the canonical form of `CF-Connecting-IP` (`worker/src/address.ts`, shared with the page-view route: case, zero compression and IPv4-mapped spellings of one address share one key; IPv6 is cut to its /64; an absent or malformed address, including one with a zone id, hashes the literal `unknown` and shares one bucket), never the raw address: under `VIEW_HASH_SECRET` when it is set (stable key, no day component), otherwise under a random per-isolate key (the allowance then is per isolate, not shared across isolates or restarts; the click route keeps working with no secret) | **[shipped]**; tests `burst from one client is limited`, and in `worker/test/clicklimiter.test.ts` `no raw address or part of it reaches CLICK_LIMITER with the secret` / `without the secret`, `a client over the limit is still limited without the secret`. Bounds one client, not a distributed flood |
| Same response whether accepted, ignored or failed, so no oracle | **[shipped]**; tests `response is identical for accepted and ignored requests`, `a database failure still returns the same 204` |
| 90-day purge of click rows, daily | **[shipped]**; tests in `worker/test/retention.test.ts`. No heartbeat records its last success, and nothing caps the row count between runs |
| Origin check on `Ping-From` or `Origin`; a header over 256 characters (`MAX_ORIGIN_HEADER`) is rejected before it reaches the URL parser (test `a 1,000,000 character Origin or Ping-From is never parsed and records nothing`) | **[shipped]** but only a noise filter: the sender controls both headers and the ids are public (F3, F15) |
| Cap on total URL length (2048, before the URL is parsed), path length (128) before `decodeURIComponent` and the allowlist, and id length (40) | **[shipped]** (F14); tests `a 1,000,000 character query is rejected before the URL is parsed`, `path longer than the cap records nothing and is not decoded`, `an id longer than the id cap records nothing even if listed` |
| Body-size cap before parsing on gate, `send` and any route that reads a body | Does not exist; those routes do not exist yet (the click-route cap is shipped, above) |
| Global per-route budget (a ceiling on accepted requests per route per time window, regardless of client) | Does not exist |
| Storage cap on D1 rows or an aggregate design that bounds rows | Does not exist |
| Log volume policy and bounded-cardinality logging | Does not exist; `wrangler.toml` sets no `observability` block (F16) |
| Kill switch for the click route: `CLICKS_KILL` = `1` or `true` (any case), read per request, checked first, returns the uniform 204 without touching D1 or the limiter | **[shipped]** for the click route; tests `kill switch: same 204, no D1, no limiter, no body read`, `kill switch is read on every request`. The gate and kit kill switch is [planned: Plan 3 Task 16] |
| Page-view route `POST /e/v/:pageId` (Plan 4 A4): same uniform 204; check order kill switch, method, URL/path/id caps, `PAGE_IDS` allowlist, `Sec-GPC`/`DNT`, Origin or Ping-From (256 cap), `Content-Length` at most 256 with no `Transfer-Encoding`, `VIEW_LIMITER`, bounded body read, derive, budgets, insert; no raw address, user agent or referrer URL is stored, logged or echoed | **[shipped]** in `worker/src/view.ts`; tests in `worker/test/views/` (`every_outcome_is_the_same_empty_204_with_the_same_headers`, `kill_switch_makes_zero_io`, `gpc_and_dnt_record_nothing`, `no_sentinel_reaches_any_table`, `no_sentinel_in_console_across_accepted_rejected_limited_and_failing_requests`, `random_inputs_never_produce_a_stored_value_outside_its_domain`) |
| Page-view write budget: global hourly counters (default 120 human, 30 bot) plus ONE shared per-address hourly cap (default 60, ceiling 1000, clamped to half the human budget or a quarter of the bot budget), keyed on a daily keyed hash of the address under its own label, 64 bits wide (it was 32, where a birthday collision between two clients needs only about 65,000 of them); a stricter `VIEW_LIMITER` burst limit. Accepting a view is one atomic D1 batch: both counters, the event and the visitor row are written only if the global budget and the cap both have room, so a spent budget creates no cap rows and no events however many distinct clients arrive, and a failed statement rolls everything back. Both rate-limiter bindings are keyed by purpose-separated (`lim`, `sus`) HMACs of the address under a STABLE key derived from `VIEW_HASH_SECRET` with no day component (`worker/src/ratekey.ts`), never the raw address. A daily-rotating key would give every client a fresh allowance at midnight UTC (a boundary burst); the stable key does not, and it is a different derivation from the daily visitor and cap keys, so a limiter key never equals a stored value. The unique-visitor hash and the cap hash still rotate daily and are the only address-derived values stored. Rotating `VIEW_HASH_SECRET` resets every address-based allowance and per-client cap at once and can count a visitor twice in that UTC day (the visitor hash changes mid-day), so rotate at UTC midnight or accept the burst. Residual: the stable limiter key is a pseudonym that lives only in the rate-limit service and is linkable across days by whoever can read that service's keys, which needs the binding's internals, not D1 | **[shipped]**; tests `after_the_global_budget_is_spent_distinct_clients_create_no_rows_and_no_writes`, `accept_is_one_batch_and_a_later_failure_rolls_everything_back`, `limiter_keys_are_purpose_separated_and_stable_across_the_utc_midnight_boundary`, `the_visitor_hash_still_rotates_daily_while_limiter_keys_do_not`, `one_shared_cap_covers_bot_and_human_traffic_from_one_address`, `client_cap_is_clamped_below_the_class_budget`, `the_raw_address_never_reaches_a_rate_limiter_binding`. **Fails closed**: without a usable `VIEW_HASH_SECRET` or a parsable client address nothing is recorded (test `without_a_usable_secret_or_address_nothing_is_written`). A distributed flood from many addresses can still spend the global budget (silent undercount, Plan 4 P-6); a zone rule in front is the owner's step. The click route still passes the raw address to its own limiter binding (existing behavior, not changed here) |
| Page-view retention (daily cron, bounded batches, never throws, independent of the click purge): `visitor_seen` before today (UTC), raw `view_events` past `RAW_KEEP_DAYS` (default 7, ceiling 90), `bud:<hour>:*` counters over 48 hours and `cap:<hour>:*` counters older than the previous hour (hour-sortable keys, so a primary-key range delete); a run that hits its batch budget records `purge_backlog` and the next run continues | **[shipped]** in `worker/src/viewretention.ts`; tests in `worker/test/views/retention.test.ts`. The rollup tables and job (Plan 4 A5) are **[planned]**; until then raw rows are deleted by age with no aggregate kept. Plan 4's own threat entries (T-22 to T-24) are **[planned: Plan 4 A11]** |
| Page-view geography: with the default `GEO_LEVEL=city` the country, region code and city name are stored in raw `view_events` rows for the raw retention window (default 7 days, ceiling 90) and, when the rollup job (Plan 4 A5, planned) exists, in daily aggregates. `GEO_LEVEL=region` drops the city, `GEO_LEVEL=country` removes region and city entirely. Never coordinates, postal code or organization. The privacy text must say this plainly (maintainer decision: city ON) | **[shipped]**; tests `country_mode_stores_no_region_or_city_anywhere`, `cloudflare_region_and_city_are_stored_at_the_default_level`. Residual: a small-town city plus other raw columns is close to identifying for the raw window; the aggregates are separate marginals with small-count folding [planned: A5] |
| Page-view write budget: global hourly counters (default 120 human, 30 bot) plus ONE shared per-address hourly cap (default 60, ceiling 1000, clamped to half the human budget or a quarter of the bot budget), keyed on a daily keyed hash of the address under its own label; a stricter `VIEW_LIMITER` burst limit. Accepting a view is one atomic D1 batch: both counters, the event and the visitor row are written only if the global budget and the cap both have room, so a spent budget creates no cap rows and no events however many distinct clients arrive, and a failed statement rolls everything back. Both rate-limiter bindings are keyed by purpose-separated daily HMACs of the address, never the raw address | **[shipped]**; tests `after_the_global_budget_is_spent_distinct_clients_create_no_rows_and_no_writes`, `accept_is_one_batch_and_a_later_failure_rolls_everything_back`, `one_shared_cap_covers_bot_and_human_traffic_from_one_address`, `client_cap_is_clamped_below_the_class_budget`, `the_raw_address_never_reaches_a_rate_limiter_binding`. **Fails closed**: without a usable `VIEW_HASH_SECRET` or a parsable client address nothing is recorded (test `without_a_usable_secret_or_address_nothing_is_written`). A distributed flood from many addresses can still spend the global budget (silent undercount, Plan 4 P-6); a zone rule in front is the owner's step. The click route still passes the raw address to its own limiter binding (existing behavior, not changed here) |
| Page-view retention (daily cron, bounded batches, never throws, independent of the click purge): `visitor_seen` before today (UTC), raw `view_events` past `RAW_KEEP_DAYS` (default 7, ceiling 90) but only for days that are rolled up (an unrolled day is kept to min(3x that, 90 days), then dropped with `view_meta('rollup_overdue')` set; the flag is cleared by the first later clean run; raw rows of a rolled day are deleted only up to that day's counted `max_id`), aggregate rows after 90 days, `bud:<hour>:*` counters over 48 hours and `cap:<hour>:*` counters older than the previous hour (hour-sortable keys, so a primary-key range delete); a run that hits its batch budget records `purge_backlog` and the next run continues | **[shipped]** in `worker/src/viewretention.ts`; tests in `worker/test/views/retention.test.ts`. The daily rollup is **[shipped]** in `worker/src/viewrollup.ts` (tests `worker/test/views/rollup.test.ts`, `rollup_budget.test.ts`): a day is one write transaction whose last statement is the checkpoint, so it is idempotent and never half sealed; hard row caps; a hard per-invocation D1 query budget counted in one wrapper (`d1budget.ts`); a lease with a per-write guard so overlapping runs write nothing; a grace period after midnight and a `max_id` per day so a late row is neither lost nor purged uncounted (it is folded into its day once); `MIN_CELL` small-cell folding on every dimension (a rare browser, language, country or source cannot be singled out of a small day; residual: `MIN_CELL` 1 turns it off); heartbeat in `view_meta` (`rollup_last_ok`, `rollup_err` as a closed class, `rollup_failed_day`, `rollup_late_rows`); run order click purge, rollup, view purge. `visitor_seen` is deleted as soon as the UTC day ends regardless of rollup state (the rollup never reads it), so a stuck rollup cannot keep daily hashes; a failing day is recorded (`rollup_failed_day`) and skipped while later days still roll. Residual: the Python pull's stale-heartbeat alert is still **[planned]**. Plan 4's own threat entries (T-22 to T-24) are **[planned: Plan 4 A11]** |
| Page-view geography: with the default `GEO_LEVEL=city` the country, region code and city name are stored in raw `view_events` rows for the raw retention window (default 7 days, ceiling 90) and, in the daily geography aggregate (shipped, A5), where `GEO_LEVEL` is applied again. `GEO_LEVEL=region` drops the city, `GEO_LEVEL=country` removes region and city entirely. Never coordinates, postal code or organization. The privacy text must say this plainly (maintainer decision: city ON) | **[shipped]**; tests `country_mode_stores_no_region_or_city_anywhere`, `cloudflare_region_and_city_are_stored_at_the_default_level`. Residual: a small-town city plus other raw columns is close to identifying for the raw window; the aggregates are separate marginals with small-count folding [shipped: A5 `foldGeo`; a city or region under 5 views in a day folds upward] |
| Page-view timing: responses are byte-uniform (same 204, same headers) but NOT timing-uniform, because latency depends on how far a request got (refusal before any I/O versus a database batch). Accepted: the routes and page ids are public and nothing secret is revealed. The body read has a fixed 2 second deadline (`BODY_DEADLINE_MS`) and returns the same 204 | **[shipped / accepted residual]**; test `a_stalled_body_is_abandoned_after_the_deadline_with_the_same_204` |
| `Host` check and `workers_dev` off | Does not exist (F17, T-13) |

Mitigations to add, with the mark for each:
1. **Path and id cap before any decoding.** Reject a path longer than a small constant (for example 128 characters) before `decodeURIComponent`, and an id longer than the slug limit (40, as the config models allow) before the allowlist lookup. [shipped for click: URL cap 2048, path cap 128, id cap 40, checked before parsing, decoding and the allowlist]
2. **Body cap before parsing, everywhere a body is read.** Click: keep not reading it, and pin that with a test. Gate and `send` (Phase 3): check `Content-Length` against a small cap (for example 2 KiB), and also count bytes while reading, because `Content-Length` can be absent (chunked) or false; abort the read past the cap and return the uniform response. [shipped and tested for click; planned: Phase 3 for gate and `send`]
3. **Global per-route budget.** A coarse ceiling per route per minute and per day that ignores who is asking, checked before the insert or the mail call. Mechanism is [open: D-04]: the Workers Rate Limiting binding with a constant key (cheap, approximate and per location), a D1 counter row updated atomically (exact, costs a write per request, and the write itself is the cost being protected), or a rule in front of the Worker at the Cloudflare zone (no code, configured outside the repo and documented in `docs/deploy.md`, Plan 1 Task 11). When the budget is spent the route returns the same uniform response and records nothing.
4. **Storage quota.** Either cap the number of rows (refuse to insert past a configured maximum, and tell the owner), or change the table to aggregate on write: one row per link, per hour and country, incremented by an upsert, so rows are bounded by links times hours times countries times the 90-day window no matter how many requests arrive. [open: D-05. The aggregate form bounds storage by construction but drops per-event timestamps finer than the bucket; the spec's "raw events for 90 days" would change.]
5. **Log and cost quotas, bounded cardinality.** Log fixed event codes with counts (`click.ignored.ua`, `click.rate_limited`), never the id, user agent, address, path or header values, so an attacker cannot grow the label set or inject text. Set the platform's log sampling and retention explicitly in `wrangler.toml` instead of relying on account defaults. Turn on the provider's usage notifications at a fraction of the quota. [planned: new Worker hardening task to be filed; the account-level notifications are an owner step in `docs/deploy.md`, Plan 1 Task 11]
6. **Kill switch.** [shipped for the click route as `CLICKS_KILL`, on or off only; the three-level form and the kit routes are planned] A Worker variable or secret read first on every route (`off`, `clicks_only`, `read_only_kit`) that makes the route return the uniform response without touching D1, Mailgun or the store, changeable from the dashboard or `wrangler secret put` without a code deploy (today: `wrangler deploy --var CLICKS_KILL:1`, see `docs/deploy.md`). The link page keeps working with the Worker dark because clicks are a `ping` and links carry their real `href` (the existing design, covered by `tests/site/test_click_attrs.py::test_every_link_block_has_ping_and_real_href`).
7. **Spend ceiling.** Stay on the free plan, where the ceiling is a hard stop (availability loss, no bill), or move to a paid plan with notifications. [open: D-06]
8. **Host and default hostnames.** See T-13.

Verification (all proposed unless marked exists):
- `worker/test/click.test.ts::request_body_is_never_read` (pass a body stream that throws when read; expect 204 and no read)
- `::path_longer_than_cap_records_nothing_and_skips_decoding` and `::id_longer_than_cap_records_nothing_and_is_not_logged`
- `::id_with_encoded_slash_or_dots_records_nothing`
- `::get_head_options_return_same_204_without_body`, `::response_has_no_location_header_ever`, `::no_cors_headers_on_any_response`
- `::global_budget_spent_returns_same_204_and_writes_nothing` (db stub asserts zero statements)
- `::row_cap_reached_writes_nothing` or, for the aggregate design, `::many_requests_for_one_link_hour_make_one_row`
- `::kill_switch_returns_uniform_response_and_never_touches_db_or_mailgun`
- `::log_lines_use_fixed_codes_and_contain_no_request_data` (capture console during hostile requests)
- `worker/test/config.test.ts::observability_and_log_sampling_are_set` (parses `wrangler.toml`)
- `worker/test/gate.test.ts::oversized_body_is_rejected_before_parse_with_content_length_missing_or_false` (Phase 3)
- `tests/site/test_no_redirect_routes.py::test_worker_exposes_no_route_that_returns_3xx` (walks the router table)
- exists: `tests/site/test_click_attrs.py::test_every_link_block_has_ping_and_real_href`, `::test_ping_paths_use_only_ids_from_the_manifest`, and `tests/site/test_privacy.py::test_click_tracking_on_adds_ping_and_exactly_one_script_per_page`
- Load drill (not run; needs the owner's written go-ahead): the owner, against their own test subdomain only, in a window they set and with a stop condition (stop on the first 5xx or when 80 percent of the daily quota is spent), sends synthetic traffic from several source addresses and confirms that D1 rows stay at or below the cap, every response is the uniform 204, and the kill switch stops writes within one minute. No agent runs this.

Residual: an attacker who accepts the cost can still spend the free-tier request quota with traffic that passes the front checks, because the platform counts a request before the Worker's code decides to ignore it (general knowledge, unverified against current documentation). The budget and kill switch limit the damage to the data stores and the mail budget and give the owner a fast stop; they cannot make the quota unspendable. Counts can still be inflated by anyone with patience (T-05). Reopen when the provider's usage notification fires, when the quota is spent on any day, or when the gate ships.

### T-08 Page-level injection and framing

Abuse: script injection through a link title, subtitle, disclosure text or privacy text; framing the gate page to trick clicks; mixed content; leaking the one-time email token through Referer.

Status: autoescape, https-only URLs, the CSP and the other headers are **[shipped]** (`src/reach_around/site/overrides.py::build_csp` and `render_headers`; tests exist: `tests/site/test_build.py::test_html_in_titles_is_escaped`, `tests/site/test_overrides.py::test_headers_file_has_a_strict_csp`, `::test_headers_file_has_no_unsafe_directives`, `::test_csp_is_exactly_the_specified_policy`, `::test_every_page_still_passes_with_the_csp`, `::test_csp_scanner_bites`, `tests/config/test_models.py::test_https_urls_only`). The gate page relaxation and the Worker-set headers are **[planned: Phase 3]**.

Two corrections to revision 1 from the shipped code and a reviewer. First, the Turnstile widget needs `frame-src` and `connect-src` for its origin as well as `script-src`; `build_csp` can extend `frame-src`, `connect-src` and `script-src` for one path (`_EXTENDABLE`), so the gate page extra must name all three, and the test must assert all three. Second, the per-path mechanism in `render_headers` only varies the CSP. `Referrer-Policy: no-referrer` for the emailed-link landing page has to be set by the Worker on that response, because that page is a Worker route, not a Pages file; the site-wide value today is `strict-origin-when-cross-origin`.

Mitigations: Jinja2 autoescape with `StrictUndefined`, never `from_string` or `|safe` on config text; the schema rejects `javascript:` and `data:` URLs (https only); the site sends a Content Security Policy via `_headers` (`default-src 'self'`; script only from `'self'`; `frame-ancestors 'none'`; `form-action 'self'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` on gate and email-link routes (and `strict-origin-when-cross-origin` elsewhere), and HSTS. The Turnstile script needs an explicit allowance on the gate page only.

Verification:
- `tests/site/test_build.py::test_html_in_titles_is_escaped` (plan Task 6)
- `tests/site/test_templates.py::test_no_safe_filter_or_from_string_on_config_values` (greps templates and `build.py`)
- exists: `tests/site/test_overrides.py::test_headers_file_has_a_strict_csp` (the revision 1 name `tests/site/test_headers.py::test_headers_file_sets_csp_frame_ancestors_and_nosniff` was never created)
- proposed: `tests/site/test_overrides.py::test_gate_page_csp_allows_only_the_turnstile_origin_in_script_frame_and_connect_src`
- proposed: `worker/test/headers.test.ts::all_worker_responses_carry_nosniff_and_no_store_where_private` and `::emailed_link_page_sends_no_referrer`
- `tests/site/test_privacy.py::test_banner_text_comes_from_config_when_set` (plan Task 6A; confirm it asserts escaping)

Residual: browsers with weak CSP support. Third-party icon hosts, if ever allowed, would weaken the policy, so icons stay local.

### T-09 Supply chain

Abuse: a malicious or compromised Python or npm package, GitHub Action, base image, or release workflow runs in CI, the image, or the Worker bundle. The Worker bundle is the most sensitive target because it holds D1 access and the signing secret (F2).

Status, checked against the repository: hashed Python requirements (`pip install --require-hashes` in `ci.yml`), the base image pinned by digest (`Containerfile`), actions pinned by commit with a version comment, `permissions: contents: read` and no `pull_request_target` are **[shipped]**. None of the `tests/test_supply_chain.py` tests below exists yet; the properties hold today by inspection, not by a test that would go red. `npm ci` runs in `worker-ci.yml`; `--ignore-scripts` is not set there (proposed). SBOM, provenance and `verify-published` are **[planned: Plan 1 Task 12]**.

Mitigations: hashed Python requirements (`--require-hashes`), `package-lock.json` with `npm ci --ignore-scripts`, a small dependency list for the Worker (ideally zero runtime dependencies), Dependabot for pip, npm and Actions, GYST reusable workflows and third-party actions pinned by commit with a version comment, image bases pinned by digest, explicit `permissions:` on every workflow, no `pull_request_target`, build and deploy jobs separated (T-02), SBOM and provenance on the release image (through GYST container-release where supported), and `verify-published` after release. Dependabot PRs are reviewed, not auto-merged, for the Worker and for workflows.

Verification:
- `tests/test_supply_chain.py::test_requirements_have_hashes`, `::test_every_uses_line_is_a_40_char_sha`, `::test_no_pull_request_target`, `::test_dockerfile_base_is_digest_pinned`, `::test_worker_runtime_dependencies_are_listed_and_few` (fails if the count grows without a change to an allowlist file)
- CI step `npm ci --ignore-scripts` and `npm audit --omit=dev` (report-only gate, reviewed)
- GYST security workflow green; release workflow runs `verify-published` and the container smoke test under Docker and Podman.

Residual: a compromise upstream of a pinned hash still reaches us until detected. A reviewer must read dependency diffs. Reopen on any advisory affecting a direct dependency.

### T-10 Hostile configuration files

Abuse: a config (copied from a shared example, pasted from an issue, or edited by mistake) tries to exhaust memory with aliases, deep nesting or size, to shadow keys with duplicates, to traverse paths via ids, avatars or icons, to inject template syntax, to point at unsafe URLs, or to smuggle a forbidden field past the display policy.

Status: the loader, the closed schema, the https-only rule and the asset path rules are **[shipped]** (tests exist, see below and T-20). The reserved tab-id rule is not in `models.py` or `build.py` as far as this review found: **[planned: add with the gate's route names in Phase 3; proposed test below]**. `RESERVED_FOLDERS` (`static`, `privacy`) only guards asset output paths.

Mitigations: the hardened loader from Plan 1 Task 2 (size cap, depth cap, no duplicates, anchors, aliases or merge keys); closed schema (`extra="forbid"`); slug ids; https-only URLs; length caps on every string; `avatar` and `icon` accepted only as an enumerated icon name or a path that resolves inside the instance directory (reject absolute paths, `..`, symlinks that leave the root); reserved ids (`privacy`, `static`, `e`, `manifest`, `kit`) rejected for tabs so a tab cannot overwrite a generated page; timezone only from the zoneinfo database; config values are data, never templates; the build writes only below `--out` and verifies each resolved path, plus the link manifest, which goes beside `--out` (or to `--manifest`) and is refused when it resolves inside `--out`, because `--out` is published.

Verification:
- `tests/config/test_loader.py` (plan Task 2: duplicate key, anchor, alias, merge key, oversize, deep nesting)
- exists: `tests/config/test_models.py` (`test_https_urls_only`, `test_unknown_keys_refused`, `test_ids_are_slugs`, `test_unknown_timezone_refused`, `test_title_length_is_bounded`) and, for paths, `tests/site/test_assets_security.py` (`test_every_slot_goes_through_the_same_check`, `test_intermediate_directory_symlink_is_refused`, `test_final_component_symlink_is_refused`, `test_a_fifo_is_refused_without_hanging`). These replace the revision 1 names `test_avatar_path_traversal_refused`, `test_icon_absolute_path_refused` and `test_symlink_leaving_instance_dir_refused`.
- proposed: `tests/config/test_models.py::test_reserved_tab_ids_refused`
- exists, partly: `tests/site/test_build.py::test_build_refuses_an_out_dir_that_is_the_config_directory_or_a_parent`; proposed: `::test_build_never_writes_outside_out_dir` (builds a hostile config, snapshots the file tree outside `--out`)
- `tests/config/test_template_syntax.py::test_jinja_syntax_in_titles_renders_literally` (a title of `{{ 7*7 }}` appears literally)

Residual: the owner is trusted to author their own config; the loader defends against mistakes and copied files, not a malicious owner.

### T-11 Platform-terms abuse

Abuse (usually self-inflicted): the site, a partner view or a PDF shows data a platform bars from display, or the store keeps data longer than a platform allows.

Evidence from the notes: YouTube forbids public display of owner-authorized Analytics data (III.E.3.b), and limits retention of non-authorized statistics to 30 days and requires re-verifying authorization every 30 days; LinkedIn terms bar scheduled refresh and public display (numbers must be self-reported); Social Blade data is "for Your use only" and needs credit when shared; several platform terms are still unread (Kick, Twitch, StreamElements, Patreon).

Status: **[planned: Phase 5, work package "Display-policy table and build enforcement"]**. Nothing is built. The roadmap puts this in Phase 5, after the gated bundle and the PDFs ship in Phase 3, so those would go out with no enforcement; see F22 and D-11.

Mitigations: a versioned display-policy table with the platform, field, allowed audiences (public, gated partner, owner-only), source and date (F7 adds the gated audience), and a retention limit per source; the builder refuses to place a forbidden field on a page for a disallowed audience, and this includes the gated bundle and the PDFs; overrides are not a warning: they need the explicit approval described in T-18, and some classes cannot be overridden at all; fields marked self-reported are labeled as such; a Social Blade credit appears wherever its data does; unread terms default to the most restrictive audience (owner-only) until a human records the reading with a date.

Verification:
- `tests/policy/test_display_policy.py::test_youtube_analytics_field_on_public_page_fails_build`, `::test_youtube_analytics_field_in_gated_bundle_fails_build`, `::test_youtube_analytics_field_in_pdf_fails_build`, `::test_linkedin_number_requires_self_reported_flag`, `::test_unreviewed_platform_defaults_to_owner_only`, `::test_override_emits_warning_and_is_listed_in_build_report`
- `tests/policy/test_retention.py::test_youtube_unauthorized_stats_purged_after_30_days` and `::test_every_policy_row_has_source_url_and_date`
- `tests/policy/test_credit.py::test_social_blade_credit_present_when_its_data_is_shown`

Residual: the policy is only as good as the readings behind it, and none of this is legal advice. A platform can change terms without notice. The 30-day YouTube retention limit conflicts with the permanent snapshot store as designed; the maintainer must decide whether YouTube data is stored at all, stored only as owner-authorized and kept while consent is verified, or reduced to figures typed in by hand.

### T-12 Personal data retention and deletion

Abuse and failure modes: leads or events kept past the configured window; the purge job silently fails; deletion removes the Worker row but not copies in the local store, exports, Grafana, backups or Mailgun logs; emails appear in logs or owner alerts; an unverified third-party address sits in the table.

Status: the click-event purge is **[shipped]** (`worker/src/retention.ts`, tests exist) and it refuses a zero or negative window rather than wiping the table. Everything about leads is **[planned: Phase 3, "Leads: storage, retention, deletion command"]**. The copy inventory, ceilings and deletion registry moved to T-17; this section keeps the original items.

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

Status: **[planned: Plan 1 Task 11 for the deploy docs; new Worker task for the `Host` check]**. `wrangler.toml` today sets neither `workers_dev` nor a route (the route is a commented placeholder), and `index.ts` answers every `Host` with the uniform 204 (F17). The tests named below do not exist.

Mitigations: `workers_dev = false` and no preview routes for the Worker; Pages preview deployments disabled or protected; the Worker accepts requests only when the `Host` equals `SITE_HOST` (this also helps the origin filter); the test subdomain runbook removes the DNS record before deleting the project, and cutover and rollback steps list the DNS records to change.

Verification:
- `worker/test/host.test.ts::request_with_other_host_gets_404_and_records_nothing`
- `tests/test_wrangler_config.py::test_workers_dev_disabled` (parses `wrangler.toml`)
- `docs/deploy.md` includes a checklist item with the command to list DNS records for the test hostname and the expected result after teardown (no record).

Residual: Cloudflare account settings are outside the repo. The owner checks them at cutover.

### T-14 Owner alert channel abuse

Abuse: each unlock triggers a message to email, Matrix or Discord. Flood it with junk unlocks; put markup or a mention in the address (valid local parts can contain many characters) to inject formatting; leak the webhook URL, which is a credential.

Status: **[planned: Phase 3, "Owner alerts on unlock and view"]**.

Mitigations: aggregate and cap alerts (at most N per hour, then a digest); the alert shows the address only as inert text with all markdown and mention syntax neutralized, or just the domain; webhook URLs and Matrix tokens are Worker secrets; unverified leads are labeled in the alert.

Verification:
- `worker/test/alert.test.ts::alert_text_neutralizes_markdown_and_mentions`, `::more_than_cap_unlocks_collapse_to_digest`, `::webhook_url_never_in_logs_or_responses`

Residual: a patient attacker can still produce a steady trickle of low-value alerts.

### T-15 Snapshot store integrity and token health

Abuse and failure: a failing collector writes zero, so a partner sees a collapse; an imported CSV with a formula injection (`=cmd...`) runs in a spreadsheet when the owner opens an export; a refresh token write-back fails and the collector loses access silently; a poisoned import or a typed-in number is shown as fetched.

Status: token registry, stale marking and import are **[planned: Phase 2]**; export neutralization is **[planned: Phase 4, "Metrics export bundle"]**. Rotation owners and overdue-rotation alerts are added in T-19.

Mitigations: a collector failure records "stale since", never a value; each row records its source (fetched, imported, typed); exports neutralize leading `=`, `+`, `-`, `@` in text cells; token registry alerts well before expiry; token write-back uses an atomic file replace with restrictive permissions; imports are validated against a schema and size cap.

Verification:
- `tests/store/test_stale.py::test_collector_failure_never_writes_zero`
- `tests/export/test_csv.py::test_formula_prefix_cells_are_neutralized`
- `tests/tokens/test_registry.py::test_alert_fires_before_expiry_window` and `::test_refresh_write_back_is_atomic_and_0600`
- `tests/imports/test_import.py::test_imported_rows_carry_source_imported`

Residual: the store is trusted local state; a person with access to the collector host can alter history.

### T-16 The private PDF and export boundary

Abuse: the full media kit PDF, the executive summary and the share export are rendered on the owner's machine from the same data as the site. The path is store, then renderer, then filesystem, then recipient (B13 to B15), with a possible network leg (B16). An actor (X11) who controls any text or image that reaches the renderer, or a plain mistake, can leak gated content or leads, make the owner's machine fetch an attacker's URL, leave readable copies behind, or ship identifying metadata.

Cases:
(a) **Remote asset fetches.** The public site's CSP allows `img-src https:` because a creator may link remote art by choice. A renderer that loads the same HTML will fetch those URLs from the owner's machine, which tells a third party when the PDF was made and from which address, and a hostile URL can reach loopback or internal addresses (server-side request forgery from the owner's workstation).
(b) **Compromised or steered renderer.** A headless browser or PDF library parsing hostile data is a large attack surface. It runs with the owner's file access and, by default, their environment variables, which can hold credentials.
(c) **Temp-file leakage.** Renderers write caches, intermediate HTML and partial PDFs to a temp area; a crash leaves them behind, often world-readable and in backups.
(d) **Metadata disclosure.** PDF Info and XMP fields, embedded file paths in link annotations, the producing tool, user and host names, and EXIF in embedded photos (F23).
(e) **Leads accidentally included.** The store holds the leads table; a renderer given a store handle can reach it, and a share export built by "everything except X" includes whatever X forgot.
(f) **Wrong audience.** The executive summary is for partners; the full PDF is for the owner. A field marked owner-only on the display policy (T-11) must not appear in the summary.
(g) **Output in the wrong place.** A PDF dropped into the Pages output or a CI artifact is published.

Status: **[planned: Phase 3, "Private full PDF, rendered locally"; Phase 4, "Executive summary PDF" and "Metrics export bundle"]** for every item. The PDF toolchain is not chosen, and this model has not reviewed one (section 9). The decisions D-07 and D-09 are open.

Mitigations:
1. **Typed input, no store handle.** The renderer receives one immutable document model built from an allowlist of named fields per audience, as a file or value, never a database connection or the store path. The model has no lead field. Anything not named is absent. [planned: Phase 3]
2. **Leads excluded by construction and by test.** A sentinel lead address in the store must not appear in any rendered byte, the text layer, or any export. [planned: Phase 3 and 4]
3. **No network while rendering.** Run the renderer with networking off (a rootless container with `--network none`, or a separate network namespace), and also make the renderer's own request hook abort every non-local URL and fail the render when one is attempted, so a config change cannot quietly turn it back on. Remote images named in the config are either refused for PDFs or embedded earlier by a vetted fetcher (https only, no redirects to other hosts, public addresses only, the same 8 MiB cap and `check_content` screening as the asset pipeline). [planned: Phase 3; the choice between refuse and embed is open: D-07]
4. **Contain the renderer.** Run it as an unprivileged user in a rootless container with a read-only root, only the input file and a private output directory mounted, no inherited environment (an empty environment, so collector and Cloudflare tokens are not present), JavaScript disabled in the rendered document, and a CPU, memory and time limit. [planned: Phase 3]
5. **Private temp area.** Create a fresh directory with mode 0700 under a configured path (a memory-backed location where available), set a restrictive umask, point every renderer cache and profile directory inside it, and remove it in a `finally` block; at the next start remove stale ones. [planned: Phase 3]
6. **Metadata policy.** Write a fixed Info and XMP set (generic title, no author, no timestamps beyond the date the owner chose), strip producer paths and any `file:` link targets, and strip EXIF and other metadata from every embedded image. Name the output file neutrally (no partner name, no host name), write it with mode 0600 to a directory the owner names. [planned: Phase 3; the strip step for published site images is D-08]
7. **Never uploaded, never published.** The deploy guard (T-03) refuses `*.pdf` and export archives in the Pages output; the Worker has no route that accepts one; CI jobs do not render private PDFs. [planned: Phase 3]
8. **Recipient.** Once a file is sent, the owner's controls end. The summary is rendered only from fields the display policy allows for the partner audience. A per-recipient watermark would let the owner trace a leak at the cost of putting a name in the file. [open: D-09]
9. **Share export.** Built from a named list of tables; an archive manifest lists what is inside; text cells neutralize leading `=`, `+`, `-`, `@` (T-15). [planned: Phase 4]

Verification (all proposed):
- `tests/pdf/test_inputs.py::test_renderer_input_type_has_no_lead_field` and `::test_sentinel_lead_address_absent_from_every_output_byte`
- `tests/pdf/test_network.py::test_render_makes_zero_requests_to_a_canary_listener` (the document contains an `<img>` pointing at a listener the test starts on loopback; the test asserts the listener saw zero requests and the render refused or dropped the image) and `::test_remote_image_in_config_is_refused_or_embedded_by_the_fetcher_per_decision`
- `tests/pdf/test_network.py::test_fetcher_refuses_loopback_private_and_redirects` (if the embed option is chosen)
- `tests/pdf/test_sandbox.py::test_renderer_environment_contains_no_credential_names` (sentinel variables set in the parent are absent in the child)
- `tests/pdf/test_tempfiles.py::test_temp_area_is_0700_and_empty_after_success_and_after_a_forced_crash`
- `tests/pdf/test_metadata.py::test_info_and_xmp_match_the_allowlist_and_contain_no_user_host_or_path_strings` and `::test_embedded_image_exif_is_stripped` (a fixture image with a GPS tag and a fake author)
- `tests/pdf/test_audience.py::test_owner_only_field_absent_from_executive_summary`
- `tests/test_deploy_guard.py::test_deploy_refuses_pdf_and_export_archive_in_public_dir` (extends the T-03 test)
- `tests/export/test_share.py::test_manifest_lists_every_table_and_no_leads_table`

Residual: the renderer is third-party code parsing partly attacker-influenced text; containment limits what a compromise can reach but does not remove it. A PDF in a partner's hands is out of the owner's control; a watermark traces but does not prevent. Reopen when a PDF toolchain is chosen (it needs its own review), when the renderer gains a network feature, or when a lead appears in any output.

### T-17 Lead data copies, retention ceiling and deletion that reaches every copy

Abuse and failure: lead addresses sit in more places than the Worker's table, each with its own lifetime, and a promise of "12 months" or "deleted on request" holds only for copies somebody listed. The reviewer's gap was that there is no ceiling a build or a job can enforce, and that an unverified address (F6: a third party's address typed by someone else) is kept as long as a verified one.

**Data-copy inventory.** Every row is a copy that must have an owner, a ceiling and a deletion path. A copy not on this list does not exist as far as the deletion command is concerned, so adding a new place that holds lead data means adding a row here first.

| Copy | Holder | Contains | Ceiling | Deletion reaches it by | Mark |
|---|---|---|---|---|---|
| Worker D1 `leads` and `unlock` tables | Cloudflare, owner's account | Address, time, country, verified flag | Unverified: short (proposal 30 days). Verified: setting, default 12 months. Hard maximum: proposal 24 months, refused by the schema above it | Scheduled purge plus the deletion command | [planned: Phase 3] |
| D1 point-in-time recovery or backups | Cloudflare | Whatever the table held, for the provider's window | Provider's window (to be read from current documentation) | Rolls off; disclosed; the deletion ledger below is re-applied after any restore | [planned: Phase 3; window unverified] |
| Local store lead table | Collector host | Copied leads, only when the owner runs an export or follow-up (spec 4.11) | Same ceilings as D1; the copy job copies the expiry with the row | Deletion command; purge in the same job that copies | [planned: Phase 3] |
| Export archives | Owner's disk, anywhere the owner moved them | CSV and JSON including leads | The export command writes an expiry date and registers the archive in an export index | Deletion command lists every registered archive containing the address, deletes or rewrites it, and reports what it could not find (a copy moved by hand is not registered) | [planned: Phase 4, "Metrics export bundle"] |
| Backups of the local store | Backup tool | Whatever the store held | Leads live in a separate database file excluded from general backups, or the backup has an age limit no longer than the hard maximum | Roll-off; the runbook states the number of days | [planned: Phase 2 and 3; decision on the backup design: D-10] |
| Owner alert messages | Email, Matrix or Discord provider | Possibly the address (T-14) | Alerts show the domain only by default | Not reachable; avoided by not putting the address in the alert | [planned: Phase 3] |
| Mailgun logs and message storage | Mailgun | Address, message metadata | Provider's window (read from current documentation); message body storage off | Not reachable; disclosed on the privacy page | [planned: Phase 3; window unverified] |
| Recipient's mailbox | The recipient | The emailed link | Not ours | Not reachable | Disclosed |
| Cloudflare request logs and analytics | Cloudflare | URL paths and query strings (the emailed token is in a URL) | Provider's window | The token is single use and hashed; the unlock cookie is not in any URL | [planned: Phase 3] |
| Private PDFs and share exports | Owner's disk | No leads by construction (T-16) | n/a | n/a | [planned: Phase 3 and 4] |

Mitigations:
1. **Short life for unverified leads.** An unverified address is a stranger's data until the link is used. It is purged after a short window (proposal 30 days) unless verified, and alerts and reports label it. [planned: Phase 3; the numbers are open: D-10]
2. **Hard maximum.** The config schema refuses a lead retention above the ceiling (proposal 24 months), and the purge job deletes anything older than the ceiling even if a setting says otherwise. A setting above the ceiling is a validation error, not a warning. [planned: Phase 3]
3. **A registry the deletion command reads.** The command removes the address from D1, the local store, and every export archive listed in the export index, and prints what it removed, what it could not reach (provider copies with their roll-off date), and what was in no registry. It exits non-zero if a registered copy could not be updated. [planned: Phase 3 and 4]
4. **A deletion ledger.** The command records a salted hash of each deleted address and its date. After any restore of D1 or the local store, a job re-applies the ledger so a backup restore cannot bring a deleted lead back. The ledger is itself personal data (a hash), kept for the hard maximum and no longer. [planned: Phase 3; whether a ledger is acceptable at all is open: D-10]
5. **Purge heartbeat for every purge job.** Each job records its last success, and the owner dashboard shows it; a stale heartbeat is an alert. The click purge that exists today (`worker/src/retention.ts`) returns the deleted count but records no heartbeat and the scheduled handler discards the result. [planned: Phase 3, and a small addition to the existing handler]
6. **Backups.** Leads are kept out of general backups, or backups have an age limit at or below the hard maximum; the runbook states the roll-off time. [planned: Phase 2 and 3]
7. **No lead in a log.** Already required by T-12; the test covers console output, alert text and error messages.

Verification (all proposed):
- `worker/test/retention.test.ts::unverified_leads_older_than_the_short_window_are_deleted`, `::verified_leads_older_than_setting_are_deleted`, `::nothing_older_than_the_hard_maximum_survives_whatever_the_setting`
- `tests/config/test_models.py::test_lead_retention_above_the_ceiling_is_refused`
- `worker/test/retention.test.ts::scheduled_handler_records_last_success_for_every_job` and `tests/cli/test_status.py::test_stale_purge_heartbeat_is_reported`
- `tests/cli/test_delete_lead.py::test_removes_address_from_d1_local_store_and_every_indexed_export` and `::test_nonzero_exit_when_a_registered_copy_cannot_be_updated` and `::test_report_lists_provider_copies_with_roll_off_dates`
- `tests/cli/test_delete_lead.py::test_restore_does_not_resurrect_a_deleted_lead` (restore a snapshot, run the ledger job, the address is gone)
- `tests/test_data_copies.py::test_every_table_or_file_that_stores_an_address_is_in_the_inventory` (guard: collects loosely by scanning the schema and the writers for address columns, asserts strictly that each is listed in a machine-readable inventory, asserts the scan found at least one, and has a negative case that adds an unlisted column and expects failure)
- `tests/export/test_share.py::test_export_with_leads_writes_an_expiry_and_registers_itself`

Residual: provider-side copies (Cloudflare recovery windows, Mailgun log retention, the recipient's mailbox) and any archive the owner copied by hand are outside the registry. They are disclosed, not fixed. Reopen if a deletion request cannot be completed in the time the privacy page promises, or if a new place that stores an address is found.

### T-18 Overrides and new output surfaces that defeat policy enforcement

Abuse: the design lets the instance owner override a display-policy rule and "logs a build warning" (spec 4.11, F19). A warning stops nothing. Cases: (a) a copied example or shared config carries an override that publishes data a platform's terms bar; (b) a template override prints a field the policy removed from one audience because the template receives a richer context (F24); (c) a new output surface (gated bundle, export, PDF, Grafana sink, asset) is added with its own formatting code that skips the check; (d) an override on a private field (a lead, a secret) is accepted because the override mechanism does not know the class; (e) CSS "hiding" is mistaken for removal (data in the HTML is published even if `custom.css` hides it).

What is shipped that bears on this: template and CSS overrides are confined, sandboxed and screened (T-20), but nothing yet decides which data a template or an asset may show; there is no display-policy table (Phase 5). Visibility by date (`show_from`, `show_until`, Task 5) removes a block at build time, which is the right model: policy must remove data, never hide it.

Status: **[planned: Phase 5, "Display-policy table and build enforcement"]**, with the ordering problem in F22 and D-11. The override form is **[open: D-12]** and the non-overridable list is **[open: D-13]**.

Mitigations:
1. **Policy removes data before any template, asset or writer sees it.** One function builds the render context for an audience (public, gated partner, owner-only) from the policy table; every template (packaged or override), block, export writer, PDF renderer and sink consumes only that context. A template cannot print what it was never given. [planned: Phase 5; needed before Phase 3 ships a gated surface: D-11]
2. **An override is an approval, not a warning.** Each override is an entry in the instance config naming the rule id, the field, the audience, a reason, the approver and a date, plus an acknowledgement token derived from the exact text of the rule (so a rule change invalidates it), and it expires (proposal 90 days). The build fails when an entry is missing any part, is expired, or does not match the current rule. A human then has to run the build with an explicit flag (or an instance-repo workflow that the owner reviewed) to accept the active list; the build never accepts overrides silently in CI. The example configs shipped with the engine are tested to contain none. [planned: Phase 5; mechanism open: D-12]
3. **Some things cannot be overridden.** Lead data, credentials and tokens (including the Kick stream key), and any rule the policy table marks `override: never` (proposal: a platform's explicit bar on public display of owner-authorized data, such as YouTube III.E.3.b, and the owner-only default for unread terms) are refused whatever the config says. This narrows spec 4.11, which lets the owner override any rule; it is the maintainer's decision. [planned: Phase 5; open: D-13]
4. **One check, every surface.** The same enforcement runs for the public site, the gated bundle, copied assets, share exports, the executive summary, the full PDF and each sink. A registry lists the surfaces; adding one without a policy test fails a guard test. [planned: Phase 5 and the Phase 3 and 4 surfaces]
5. **Overrides are visible.** The build report (kept out of the public output directory) lists each active override with its approver and expiry, and the privacy and kit pages mark self-reported or overridden figures where the policy requires. [planned: Phase 5]
6. **Assets and typed-in text are the owner's responsibility.** The policy covers fetched fields. A screenshot of a barred dashboard saved as `analytics.png` passes `check_content` because it is a real PNG, and typed-in numbers are labeled self-reported rather than checked. This is a stated limit, not a gap that code can close. [accepted residual]

Verification (all proposed unless marked exists):
- `tests/policy/test_overrides_approval.py::test_override_without_approver_reason_expiry_or_ack_fails_build`, `::test_expired_override_fails_build`, `::test_ack_token_stale_after_rule_text_changes`, `::test_override_is_not_accepted_without_the_explicit_flag`
- `tests/policy/test_overrides_approval.py::test_override_on_lead_credential_or_never_class_is_refused_whatever_the_config_says`
- `tests/policy/test_surfaces.py::test_every_registered_output_surface_runs_the_policy_check` (guard: asserts the registry is non-empty, fails for a surface added without a test, and has a negative case that registers a fake surface and expects failure)
- `tests/policy/test_context.py::test_template_override_printing_every_context_key_cannot_show_an_owner_only_field` (an override template that dumps its whole context)
- `tests/policy/test_display_policy.py` cases from T-11 repeated per surface: public page, gated bundle, asset copy, share export, executive summary, full PDF
- `tests/test_example_variants.py::test_shipped_examples_contain_no_policy_override` (extends an existing file; the file exists)
- `tests/policy/test_hiding.py::test_barred_field_is_absent_from_the_html_even_when_custom_css_is_present`

Residual: the owner is accountable for what they type and for images they supply; code cannot read a screenshot. A platform can change its terms after an approval; the acknowledgement token and the expiry force a re-read. Reopen when a new output surface is added, or when a platform's terms change.

### T-19 Collector and CI credential compromise and recovery

Abuse: one credential leaks (a log line, a stolen laptop, a fork-PR run, a breached dependency, a backup that holds an old refresh token) and the owner cannot say what it can reach, who rotates it, how it is killed, or whether a copy is still valid elsewhere. Revision 1 covered write-back atomicity and the `kid` for the signing key; it did not cover recovery.

Status: the inventory fields and overdue-rotation alert are **[planned: Phase 2, "Token registry, refresh and expiry alerts"]**; per-collector scope manifests are **[planned: Phase 2 and Phase 5 with each collector]**; the runbook is **[planned: Plan 1 Task 11 for deploy credentials, Phase 2 for collector credentials]**. The fork-PR items are partly **[shipped]**: no workflow uses `pull_request_target`, `permissions: contents: read` is set, and `persist-credentials: false` is set on checkouts. One thing is not verified here: `ci.yml` passes an optional `DOPPLER_TOKEN` to the GYST reusable workflow on pull requests. A fork PR does not receive repository secrets, but a branch in this repository does; the comment in `ci.yml` says the reusable workflow keeps it off the test job, and this model did not read that workflow at the pinned commit.

**Token inventory.** Values live only in the secrets manager or Worker secrets. The inventory holds names, never values, and is kept in the token registry (spec 4.9) with these fields for each entry: name, holder (where the value lives), scopes, what it can reach (blast radius), owner (a named role), rotation interval, last rotated, how to revoke it, and what depends on it.

| Credential | Lives in | Least scope (target) | Blast radius if stolen | Owner |
|---|---|---|---|---|
| Cloudflare deploy token (A4) | GitHub environment secret | Pages edit, Workers scripts edit, D1 edit on one account and named resources, expiry | Change what the site and Worker serve | Maintainer |
| Cloudflare read token (A5) | Collector host | Read on the one D1 database only; if a read-only scope cannot run the queries (F12), use a read-only replica or an export job instead of a wider scope | All leads and events | Maintainer |
| Worker secrets: signing key (A3), Turnstile secret, Mailgun key, alert webhooks | Worker secret store | Mailgun: sending only, one domain | Forge unlocks; send mail as the owner; post to the alert channel | Maintainer |
| Collector API keys and app passwords (Bluesky, Mastodon, Discord bot, Matrix, Twitch client secret, Patreon, StreamElements JWT, Social Blade) | Secrets manager | Read-only scopes each platform offers; no posting, no moderation | Account data read; some accounts acted upon | Maintainer |
| OAuth refresh tokens (YouTube, Instagram, Threads, TikTok, X) | Homelab encrypted token file | Read-only analytics scopes (for YouTube, the read-only analytics scope, never the full account scope) | Read the account's data until the grant is removed | Maintainer |
| GitHub App private key | Secrets manager | App permissions limited to read metadata, traffic and followers | Read repo traffic; mint installation tokens | Maintainer |
| Kick OAuth token and stream key (A7) | Secrets manager; the key is never stored | Channel read only | Broadcast on the channel (the key) | Maintainer |
| Doppler service token or OIDC identity | Doppler and GitHub | Read-only to one project and config | Read the secrets in that config | Maintainer |
| Grafana push or database credential | Sink config | Write to one data source only; never a Cloudflare token | Write bogus metrics | Maintainer |

Mitigations:
1. **Inventory in the registry.** The registry refuses an entry without owner, scope list, rotation interval and a revocation step, and alerts on overdue rotation, not only on expiry. [planned: Phase 2]
2. **Least scopes by manifest.** Each collector declares the scopes it needs in a manifest; a test fails when a collector requests a scope outside its manifest or the manifest includes a known-broad scope. [planned: Phase 2 and 5]
3. **Rotation owners and intervals.** Each credential has a named owner role and an interval (proposal: 90 days for tokens the owner mints, at consent expiry for OAuth). Rotating the signing key logs partners out (T-02); that is documented. [planned: Phase 2; intervals open: D-14]
4. **Revocation runbook** (`docs/` runbook, to be written with the registry). Order: contain first (pause the collector schedule; use the Worker kill switch from T-07 if a Worker secret is involved), revoke at the provider (per-platform steps, with the settings page named), rotate and re-issue, prove the old value is dead (a call with it returns an authorization error), read the provider's audit log for use between leak and revocation, clean history only after rotation (for the Kick key, rotate first), then record the incident. [planned: Plan 1 Task 11 and Phase 2]
5. **Refresh-token families.** Where a provider rotates refresh tokens and revokes the whole family when an old one is replayed, a stolen token that gets used makes the owner's next refresh fail. Treat `invalid_grant` on refresh as a possible compromise, not just expiry: a separate alert, and the runbook step is to remove the app's access at the provider (kills the family), then re-consent. Where a provider does not rotate, the refresh token is a long-lived bearer secret: keep its scopes read-only, keep the consent screen out of Testing mode where that shortens its life only by accident, and revoke by removing the grant. Never keep an old token file: write-back is atomic and the replaced file is not left in a backup (backups exclude the token store). [planned: Phase 2 and 5]
6. **Fork-PR secret isolation.** No `pull_request_target`; pull-request workflows get no secret; any job that holds a credential runs only on `main`, `schedule` or `workflow_dispatch` in a protected GitHub environment with a branch policy and, for production, a required reviewer; scheduled collector jobs never check out or run PR-supplied code; the repository setting that requires approval before workflows run for outside contributors is on. A repository-level check lists secrets and environments at release. [shipped in part: no `pull_request_target`, `contents: read`, `persist-credentials: false`; planned: the environment and the checks, Plan 1 Task 11]
7. **Detection.** The Cloudflare and provider audit logs are read at each rotation, and unexplained authentication failures from a collector alert the owner. [planned: Phase 2]
8. **Restrict by network where the provider allows.** A Cloudflare API token can be limited to a source address range; if the collector host has a stable address, set it. The deploy token is not usable from the collector host and the read token is not available to CI. [open: D-14, depends on the provider feature and the host's addressing]

Verification (all proposed):
- `tests/tokens/test_registry.py::test_entry_without_owner_scope_interval_or_revocation_step_is_refused` and `::test_overdue_rotation_alerts_before_expiry`
- `tests/collectors/test_scopes.py::test_every_collector_requests_only_its_manifest_scopes` and `::test_known_broad_scope_in_a_manifest_fails`
- `tests/tokens/test_refresh.py::test_invalid_grant_raises_a_compromise_alert_not_an_expiry_alert` and `::test_old_refresh_token_is_not_left_on_disk_after_write_back`
- `tests/test_workflows.py::test_no_workflow_uses_pull_request_target` (the property holds today by inspection; this test makes it a gate) and `::test_secrets_are_referenced_only_in_jobs_with_a_protected_environment_or_a_non_pr_trigger`
- `tests/test_workflows.py::test_no_workflow_that_holds_a_secret_checks_out_pr_head` (guard: has a negative case)
- Runbook drill, run by the owner (not an agent) once before the first collector goes live: rotate one non-critical token end to end following the runbook and confirm the old value is rejected; the expected output is an authorization error from the provider.

Residual: a stolen token is usable until revoked; the controls shorten the time to notice and the radius, they do not prevent theft. A provider that cannot revoke a grant is a risk the owner accepts or avoids by not using that provider. Reopen on any secret scanning alert, any unexplained `invalid_grant`, or a collector added without a scope manifest.

### T-20 The shipped instance-file pipeline and override loader

This section records what exists on `main`, what the review rounds found, and what is left. It is a risk of its own because the code is already trusted by every build, and because the same classes of mistake will recur in the next surfaces (the gate routes, the renderer, the export writers).

What shipped (all in `src/reach_around/site/`):
- **Asset pipeline** (`assets.py`, `assetcheck.py`). The config names the only files that are published (avatar, favicon, theme background and mascot, theme fonts, image blocks). Each goes through one check function, `_check_slot`: an extension allowlist per kind (images; fonts `.woff2 .woff .ttf .otf`), a plain relative path (no absolute path, no `.`, `..` or empty segment), and a refusal of the folders the build writes itself. The file is then read one path component at a time, each opened relative to the previous descriptor with `O_NOFOLLOW`, so a symlink anywhere in the path, the root included, is refused and nothing depends on `/proc`; only regular files are read (a FIFO is refused without hanging); the size is capped at 8 MiB by counting bytes actually read. Content is then screened by `check_content`: a signature plus a cheap structural fact per format (PNG ends with IEND, JPEG with the end marker, GIF with its trailer, WebP and WOFF carry a length that must match, fonts a plausible table directory) so a renamed YAML file is refused.
- **SVG allowlist and caps.** SVG is parsed with a handler-based expat pass that refuses everything not on an allowlist: elements and attributes by name, namespaces (at most 8 declarations, only SVG and xlink), `href` only as a same-file `#fragment`, `url(` only as `url(#id)`, transform attributes only with the listed numeric functions, no processing instructions or CDATA. `DOCTYPE` and `ENTITY` are refused by a text check before the parser runs, which defuses entity expansion. Caps: 20,000 elements, depth 64, 64 attributes per element, 1 MiB of attribute text in total; the parse stops at the first cap exceeded.
- **Template overrides** (`overrides.py`). A confined loader (no absolute path, backslash, NUL or `..` segment; the resolved file must be inside the directory), an immutable sandboxed Jinja environment, a 256 KiB cap on each template counted on bytes read, files opened with `O_NOFOLLOW` and checked with `fstat`, symlinked overrides directory, `templates/` and `custom.css` refused, `.js` files ignored with a warning (custom JavaScript is not supported).
- **CSS refusals.** `custom.css` is refused for: any backslash; `@import`, `@namespace`, `expression(`, `behavior:`, `-moz-binding`, `javascript:`, legacy `progid:`, `DXImageTransform` and `AlphaImageLoader`; any URL-taking function (`image()`, `image-set()`, `src()`, `cross-fade()`, with or without a vendor prefix); any `url()` that is not a relative same-site path. Comments are stripped by CSS tokenization rules (strings respected) before checking, and an unterminated comment is refused.
- **Headers** (`build_csp`, `render_headers`): `fullmatch` on every source, directive and path, whitespace and control characters refused, no `unsafe-*` and no wildcards (see T-08).

What the review rounds found, by class of bug (not exploit strings). The four Daybreak review rounds are summarized here from the commit history and test names on `main` (commits `4adcbb1` and `e64d19d`); the review reports themselves were not among the inputs, so a class may be listed under the wrong round or missing. The classes, and the lesson each carries to the next surface:
1. **Code reach from content.** Templates are code. Without a sandbox an attribute walk reaches the interpreter. Fixed with an immutable sandbox. Lesson: any text that is interpreted (templates, CSS, SVG, later the PDF's HTML) needs an interpreter-level boundary, not only escaping.
2. **Path and file-handle confinement.** Symlinks at the last component, in a parent directory, or swapped between check and use; reliance on `/proc`; special files such as a FIFO that block a read. Fixed with `O_NOFOLLOW`, a descriptor-relative walk, `fstat` for a regular file, and non-blocking opens. Lesson: check and use must be one operation on a descriptor. A parent-directory swap in the override loader still needs `openat2` and stays a documented residual (below).
3. **Validate at one choke point.** One slot (the image block) had its own weaker check and let any extension through. Fixed by routing every slot through `_check_slot`, with a test that forges a value past validation. Lesson: when a second path to the same sink appears, put the check at the sink.
4. **Allowlist, not denylist.** The SVG screen began as a list of bad things and was replaced by a list of permitted elements, attributes, namespaces and value forms; each later gap (namespace removal reported as a null URI, `xml:base`, nested elements in style, function-style values) was a hole in a denylist. Lesson: default deny, and add a test with each new allowed feature.
5. **Parser disagreement.** The checker and the browser tokenize differently: CSS comments and escapes that hide a keyword, a string that contains a comment opener, a `$` in a regular expression that matches before a final newline and so admits a header-splitting character, a UTF-16 file that hides a DOCTYPE from a text check, vendor-prefixed and mixed-case function names. Fixed by following the real tokenization, refusing instead of cleaning, `fullmatch`, UTF-8 only. Lesson: refuse what you cannot parse the same way as the consumer.
6. **Side channels around the policy.** A CSP that restricts script does not stop a stylesheet from making every visitor's browser contact a third party (an IP address leak). Fixed by refusing URL-taking CSS functions and remote `url()`. Lesson: privacy leaks are a separate class from script injection; list what can make the client fetch.
7. **Caps on what is actually consumed.** Size limits trusted a claimed size; complexity limits were missing for small-but-expensive input. Fixed by counting bytes read, stopping the parse at the first cap, and bounding elements, depth, attributes, namespaces and total attribute text. Lesson: every parser gets a byte cap and a complexity cap, counted as it runs.
8. **Test quality.** Tests that stayed green with the control removed, tests that could hang a run (a preview server fixture, fixed so a regression fails fast), and a requirement that every escape test must produce a refusal rather than silence. Lesson: this document's own rule, that a test must go red when the control is removed.
9. **Local tooling is a server too.** The live preview was found vulnerable to cross-site and DNS-rebinding requests by an automated review and now checks `Host`, `Origin` and `Sec-Fetch-Site` and listens on loopback only. Lesson: a development server on a developer machine is an attack surface.

Mitigations: all of the above are **[shipped]**.

Verification: tests exist (examples: `tests/site/test_assets_security.py::test_every_slot_goes_through_the_same_check`, `::test_confinement_does_not_depend_on_proc`, `::test_a_fifo_is_refused_without_hanging`, `::test_the_size_cap_is_enforced_on_bytes_read`; `tests/site/test_assets_content.py::test_a_yaml_renamed_to_any_image_extension_is_refused`, `::test_every_element_outside_the_allowlist_is_refused`, `::test_a_billion_laughs_payload_is_refused_before_it_is_parsed`, `::test_a_utf16_svg_cannot_hide_a_doctype`; `tests/site/test_assets_limits.py::test_element_count_cap`, `::test_the_parse_stops_early_once_a_cap_is_exceeded`; `tests/site/test_overrides_hardening.py::test_the_globals_escape_is_a_security_error`, `::test_template_read_uses_o_nofollow`, `::test_css_tokenizer_tricks_are_refused`, `::test_css_functions_that_take_a_url_are_refused`, `::test_legacy_ie_filter_and_expression_are_refused`, `::test_header_path_with_trailing_control_or_space_is_refused`).

Still open or planned in this area:
1. **Metadata in published images** (F23). Not stripped. Either strip or re-encode at build (a build-time image dependency and its own review) or warn the owner and document it. [open: D-08]
2. **Fonts and images are structurally checked, not decoded.** The check proves the file is the format it claims, not that the font or image parser in the browser will handle it safely; that remains the browser's job, with `nosniff` and a same-origin CSP in front. Accepted.
3. **Parent-directory swap during a build** in the override loader (documented in `docs/education/csp-and-overrides.md`): the overrides directory must not be writable by anyone untrusted while a build runs. The asset walk does not have this weakness, the override loader does. Accepted, documented.
4. **No render-time or output-size cap** was found for override templates beyond the sandbox's range limit. A hostile or copied template can make a build slow or large; it cannot read files. Accepted for an owner-authored directory; reopen if instance configs are ever accepted from another party.
5. **Template context** (T-18) is not constrained yet. [planned: Phase 5]
6. **A guard test that enumerates every config field that can name a file** and asserts each goes through `_check_slot`, so a new slot cannot be added without the check. [planned: proposed `tests/site/test_assets_security.py::test_every_config_field_that_names_a_file_is_a_registered_slot`, with a negative case]

Residual: the pipeline defends against mistakes and copied files, not against a malicious owner. Reopen when a new kind of instance file is accepted, when instance configs come from another party, or when a review round finds a new class.

### T-21 "Not forwardable" is not enforced: first-redeemer-wins and cookie replay

Abuse: the spec decides that an unlocked kit "must not be shareable by link" and calls the unlock "not forwardable". The design enforces two weaker things (T-04): the unlock token is never in a URL, and the emailed link is single use. Two gaps remain. (a) A forwarded copy of the emailed link works for whoever redeems it first. If the recipient forwards it, or a mail gateway, a shared mailbox or an assistant opens it before the lead does, the first redeemer gets the cookie and the lead gets the gate. (b) A stolen live unlock cookie works from any machine for its lifetime; `HttpOnly` stops script from reading it, not a person with the browser profile, a malware-infected machine, a proxy that terminates TLS, or a compromised extension. Cookie binding as designed ties the token to the lead record, not to the browser or the person.

What can be said as fact today: nothing of this exists on `main` (Phase 3). What the design can claim once built is the narrow statement below, and the spec should say that instead.

**The narrowed claim.** "The kit link is single use and the pass is not placed in any URL. A pass is held in the browser that redeemed the link or passed the gate, for at most the configured lifetime. Forwarding the email before it is opened can give the kit to the first person who opens it. Forwarding the page address gives nobody a pass." Anything stronger needs a binding from the list below.

Mitigations in the design (what is enforced, once built):
1. Single use, atomic, expiring, hashed at rest (T-04 item 4); a second use gets the gate. [planned: Phase 3]
2. The landing page asks for a button press (T-04 item 5) so scanners do not burn the link. [planned: Phase 3]
3. Every redemption and every gate pass records an event with country level only, so the owner can see two redemptions from different places for one lead. [planned: Phase 3]
4. The privacy page and the spec say what is and is not enforced. [planned: Phase 3 documentation; the spec wording is the maintainer's: D-15]

**Stronger binding options.** These are for the maintainer to choose among; this model does not decide. Each is a different trade between friction, the "open it on another device" flow the spec allows, mail cost and privacy.

| Option | What it stops | What it costs | What it still allows |
|---|---|---|---|
| A. Accept the narrowed claim (no binding) | Nothing new | None; one sentence in the spec and the privacy page | First redeemer wins; cookie replay |
| B. Address confirmation on redemption: the landing page asks the visitor to type the address the link was sent to, compared to the stored hash | A casual forward to someone who does not know the address | One more field; typing errors; an oracle for address guessing, so it needs its own rate limit and uniform failure | Anyone who sees the email also sees the address; weak against a forwarder |
| C. Request-bound redemption: pressing "email me a link" sets a short-lived HttpOnly pending cookie, and the link redeems only in a browser that holds it; elsewhere it lands on the gate | Forwarded copy; mail-gateway and shared-mailbox opening; link opened by an assistant | Breaks the spec's "partner opens it on another device" flow, which then needs a fresh gate pass there; a pending cookie to manage; users on privacy-hardened browsers | Cookie replay after redemption; the requester themselves forwarding the unlock after redeeming |
| D. Emailed one-time code typed back (a verified-link-first variant): the page asks for the address, the Worker mails a short code, the visitor types it into the same page | Forwarding and gateways (the code is typed into the page that asked for it); also proves the address, which gives F6's verification | Every unlock sends mail, which enlarges the T-01 surface and the Mailgun budget; slower for partners; spec 4.5 chose instant reveal to avoid this | Cookie replay; a person with the mailbox |
| E. Per-lead session cap and short lifetime: each token has an id stored with the lead; more than N active ids (for example 2) revokes all of them and alerts the owner; lifetime cut to a few hours | Wide sharing of a live cookie; narrows the replay window | A D1 write per unlock; a partner on a phone and a laptop may trip the cap; owner alert noise | A single stolen cookie within the cap and its lifetime |
| F. Browser-bound session credentials (a standard that ties a session to a device key; support varies and was not checked for this model) | Cookie replay from another machine | Depends on browser support, a fallback is still needed | Anything on the same device |

Options combine: C with E is a common pairing; D alone also covers C's aims but costs mail. [open: D-01]

Verification (proposed; the first two document the current behavior so the claim cannot drift):
- `worker/test/email_link.test.ts::forwarded_copy_redeemed_first_wins_and_the_lead_then_gets_the_gate` (an executable statement of the limit, named so a reader sees it is known; if option C or D is chosen this test is replaced by the one that proves the binding)
- `tests/test_claims.py::test_docs_do_not_say_the_unlock_is_not_forwardable_without_naming_the_binding` (greps the docs and the privacy template for the phrase; passes when the phrase is absent or the binding option is named on the same page; has a negative case with a fixture page that contains the bare phrase and expects failure)
- If option B: `::wrong_address_gets_gate_and_counts_against_the_limiter` and `::address_confirmation_failure_is_uniform`
- If option C: `::link_without_pending_cookie_gets_gate` and `::pending_cookie_expires`
- If option D: `::code_is_single_use_and_expires` and `::code_attempts_are_limited`
- If option E: `::third_active_token_revokes_all_and_alerts` and `::lifetime_cap_applies`

Residual: with option A only, a forwarded email can hand the kit to its first opener, and a stolen cookie works until it expires. The owner accepts that, sees it in the event log as a second location for one lead, and can revoke a lead by deleting it (T-04 item 2). Reopen if rates or partner names in the gated kit are used in a negotiation where a leak would matter, or if a leak of the kit is reported.

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
| Anonymous traffic can still spend the request quota before any check runs (T-07) | Maintainer | A usage notification fires, the quota is spent on any day, or the gate ships |
| First redeemer of a forwarded link, and replay of a stolen live cookie (T-21) | Maintainer, after choosing a binding option (D-01) | A reported kit leak, or rates used in a negotiation |
| Renderer is third-party code on hostile-ish text; a file sent to a partner is out of control (T-16) | Maintainer | A PDF toolchain is chosen, the renderer gains network features, or a lead appears in any output |
| Provider copies and hand-copied exports of lead data (T-17) | Maintainer, disclosed | A deletion request cannot be completed in the promised time |
| Owner-supplied images and typed-in numbers are not checked against platform terms (T-18) | Maintainer | A platform complains or terms change |
| A stolen token works until revoked (T-19) | Maintainer | Any secret scanning alert or unexplained `invalid_grant` |
| Override loader parent-directory swap during a build; metadata in published images until D-08 is decided (T-20) | Maintainer | Instance configs come from another party, or a review round finds a new class |

## 7a. Decisions left for the maintainer

None of these is decided by this document. Each names the risk it closes, the options, and what I would need to plan it. Section 6 and the risk sections point here.

| ID | Decision | Risk | Options and trade-offs |
|---|---|---|---|
| D-01 | How strongly to bind the emailed link and the unlock to the lead | T-21, T-04 | Options A to F in T-21. A costs nothing and is honest; C breaks cross-device opening; D costs mail and friction; E needs a D1 write per unlock |
| D-02 | Turnstile outage on `gate`: fail closed (and alert) or fail open; record it in the spec | T-01 | Closed protects the control and blocks real partners during an outage; open keeps the kit available and makes an outage the way around Turnstile. `send` fails closed either way |
| D-03 | Cookie: `__Host-` prefix with `Path=/`, or path-scoped without the prefix | T-04 | The prefix blocks subdomain overwrites; path scoping is not a security boundary here |
| D-04 | Mechanism for a global per-route budget | T-07 | Rate Limiting binding with a constant key (cheap, approximate, per location); a D1 counter (exact, costs a write per request); a zone rule in front (no code, configured by the owner) |
| D-05 | Click storage: keep one row per click with a row cap, or aggregate on write | T-07 | Aggregate bounds storage by construction and loses fine timestamps; the spec's raw 90-day events change |
| D-06 | Spend ceiling: stay on the free plan (hard stop) or move to a paid plan with notifications | T-07 | Hard stop means an outage instead of a bill |
| D-07 | Remote images named in the config when rendering PDFs: refuse, or embed through a vetted fetcher at build time | T-16 | Refuse is simplest and breaks Image blocks that use https art; embedding adds a fetcher that needs its own SSRF review |
| D-08 | Strip metadata from published site images (strip or re-encode), or warn only | T-20, T-16 | Stripping needs an image library in the build and a review; warning leaves the owner's mistake in place |
| D-09 | Per-recipient watermark on the PDF | T-16 | Traces a leak; puts a name in the file |
| D-10 | Lead retention numbers (proposals: unverified 30 days, verified 12 months, hard maximum 24 months), whether a deletion ledger of salted hashes is acceptable, and the backup design for the lead table | T-17 | Shorter limits reduce exposure and follow-up value; a ledger is itself personal data |
| D-11 | Reorder the roadmap so a minimal audience filter ships before the gated bundle and PDFs (Phase 3), or block Phase 3 release on Phase 5 enforcement | T-11, T-18 | Moving it earlier delays Phase 3 by part of one work package; not moving it ships unfiltered surfaces |
| D-12 | Form of the explicit override approval (config entry with acknowledgement token and expiry, plus a human-passed flag, or a separate reviewed file) | T-18 | A separate file keeps overrides out of shareable configs; an in-config entry is simpler |
| D-13 | Which rules are non-overridable (proposal: lead data, credentials, explicit platform bars, unread-terms default) | T-18 | Narrows spec 4.11's "the owner can override a rule" |
| D-14 | Credential rotation intervals and owners; network restriction of the read token | T-19 | Intervals trade owner effort for exposure time; a source-address limit depends on the host's addressing |
| D-15 | Spec wording changes: 4.4 (token wording, F1), 4.11 ("not forwardable", F18), 4.11 (override form, F19) | T-04, T-21, T-18 | These edits are the maintainer's; this model proposes the narrowed sentence in T-21 |

Work that has no task yet and needs an issue and a card before it starts: Worker hardening (path and id cap, body cap pin, global budget, row cap or aggregate, log policy, kill switch, `Host` check, `workers_dev`) against Plan 1 Task 8 and 9 code; the supply-chain tests in T-09; the workflow tests in T-19; the guard test for file-naming config fields in T-20.
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
19. `worker/test/email_link.test.ts::forwarded_copy_redeemed_after_first_gets_gate` - proves a second redemption of the link lands on the gate. It does not prove the copy is unusable if it is redeemed first (T-21). Only a gate pass, not the landing on the gate, records an unlock event.
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
49. `worker/test/click.test.ts::request_body_is_never_read`, `::path_longer_than_cap_records_nothing_and_skips_decoding`, `::global_budget_spent_returns_same_204_and_writes_nothing`, `::row_cap_reached_writes_nothing` (or the aggregate equivalent), `::kill_switch_returns_uniform_response_and_never_touches_db_or_mailgun`, `::log_lines_use_fixed_codes_and_contain_no_request_data` - proves the anonymous-route cost controls (T-07).
50. `worker/test/gate.test.ts::oversized_body_is_rejected_before_parse_with_content_length_missing_or_false` - proves the body cap on routes that read a body (T-07).
51. `tests/pdf/test_network.py::test_render_makes_zero_requests_to_a_canary_listener`, `tests/pdf/test_inputs.py::test_sentinel_lead_address_absent_from_every_output_byte`, `tests/pdf/test_sandbox.py::test_renderer_environment_contains_no_credential_names`, `tests/pdf/test_tempfiles.py::test_temp_area_is_0700_and_empty_after_success_and_after_a_forced_crash`, `tests/pdf/test_metadata.py::test_info_and_xmp_match_the_allowlist_and_contain_no_user_host_or_path_strings` - proves the renderer boundary (T-16).
52. `worker/test/retention.test.ts::unverified_leads_older_than_the_short_window_are_deleted`, `::nothing_older_than_the_hard_maximum_survives_whatever_the_setting`, `tests/cli/test_delete_lead.py::test_removes_address_from_d1_local_store_and_every_indexed_export`, `::test_restore_does_not_resurrect_a_deleted_lead`, `tests/test_data_copies.py::test_every_table_or_file_that_stores_an_address_is_in_the_inventory` - proves the retention ceiling and deletion that reaches every registered copy (T-17).
53. `tests/policy/test_overrides_approval.py::test_override_without_approver_reason_expiry_or_ack_fails_build`, `::test_override_on_lead_credential_or_never_class_is_refused_whatever_the_config_says`, `tests/policy/test_surfaces.py::test_every_registered_output_surface_runs_the_policy_check`, `tests/policy/test_context.py::test_template_override_printing_every_context_key_cannot_show_an_owner_only_field` - proves overrides cannot defeat the policy (T-18).
54. `tests/tokens/test_registry.py::test_entry_without_owner_scope_interval_or_revocation_step_is_refused`, `tests/collectors/test_scopes.py::test_every_collector_requests_only_its_manifest_scopes`, `tests/tokens/test_refresh.py::test_invalid_grant_raises_a_compromise_alert_not_an_expiry_alert`, `tests/test_workflows.py::test_no_workflow_uses_pull_request_target` - proves token inventory, least scope, refresh-family handling and fork-PR isolation (T-19).
55. `worker/test/email_link.test.ts::forwarded_copy_redeemed_first_wins_and_the_lead_then_gets_the_gate` and `tests/test_claims.py::test_docs_do_not_say_the_unlock_is_not_forwardable_without_naming_the_binding` - proves the narrowed claim is the documented one, plus the tests for the chosen binding option (T-21).
56. `tests/site/test_assets_security.py::test_every_config_field_that_names_a_file_is_a_registered_slot` (proposed) - proves a new file slot cannot skip the shared check (T-20). The shipped tests for the pipeline are listed in T-20.

The mutation step in item 48 applies to items 49 to 56 as well.

## 9. What this model did not cover

- Revision 2 read the shipped code: the Worker click route and retention job, the asset pipeline, the override loader and the CSP builder. Everything in Phase 2 and later (collectors, store, gate, kit, email, PDF, display policy) still does not exist, so those controls are requirements on a plan and those test names are proposals. Re-run this model against the implemented gate before it ships. The checks of shipped code were by reading and by listing test names; the Python and Worker test suites were not re-run for this document beyond the repository gate noted in the pull request.
- The Daybreak review reports were not among the inputs. T-20 groups the findings by class from commit messages and test names, so it may mislabel a class or miss one.
- The GYST reusable workflows were not read at the pinned commit; what they do with `DOPPLER_TOKEN` on pull requests is unverified (T-19).
- Cloudflare, Mailgun and Turnstile behavior was taken from the design and general knowledge, not from fresh reading of their current documentation. In particular, whether a read-only Cloudflare token can run the D1 queries the collector needs is unknown (F12); the permissions D1 queries require, Turnstile outage semantics, rate-limiting binding accuracy across locations, and R2 and KV access defaults need checking against current documentation before the plan commits to them.
- The terms of Kick, Twitch, StreamElements, Patreon, X, TikTok, Instagram and Threads have not been read by a human; T-11 treats them as restrictive by default.
- Legal questions (privacy law, retention duties, terms interpretation) are not addressed and this is not legal advice.
- Denial of service at the network level, Cloudflare account takeover, endpoint security of the owner's machines, and physical access are out of scope.
- The Grafana, Zoho Books, executive summary and PDF rendering stacks are not designed yet. T-16 states the boundary and the controls the toolchain must meet; the toolchain (a headless browser or a PDF library rendering local data) needs its own review when chosen.
- Cloudflare quota figures, D1 recovery windows, Mailgun log retention, the behavior of the Rate Limiting binding across locations, and which browsers support device-bound sessions were not checked against current documentation. Numbers in this document that look like limits are proposals, not provider facts.
- No load test was run and none is authorized; the drill in T-07 is for the owner to run against their own test subdomain.
- No active testing was done or authorized.

## 10. Addendum: the local visual editor (issue #158, phase 3 of #103)

Status: design review, written 2026-10-25 from `docs/superpowers/specs/2026-10-25-local-editor-design.md`. Nothing is built. Every mitigation below is **[planned: editor phase N]** (phase numbers are those of section 16 of the spec: 0 core additions, 1 MVP, 2 drag and drop, 3 uploads, 4 theme picker) unless it is marked **[shipped]**. Every test name for the editor is proposed. I read `src/reach_around/preview.py`, `editing.py`, `edit_cli.py`, `schema.py`, `site/assets.py`, `site/assetcheck.py` and the CSP builder in `site/overrides.py` to ground this; I did not run anything against a live system and did no active testing. Questions for the maintainer are in section 10.5 and in section 17 of the spec.

The editor is new attack surface of a different kind than the rest of this model. The deployed site and Worker are anonymous and remote. The editor is local, runs as the owner, and can write the owner's config and a media folder. The main attackers are therefore not strangers on the internet but web pages the owner visits, other software on the owner's machine, and text inside a config file the owner was given.

### 10.1 Added assets, actors, boundaries

| ID | Item | Why it matters |
|---|---|---|
| A18 | The instance config file, its history folder and the media folder | What the editor writes. Tampering changes what the public site says and links to, after the next deploy |
| A19 | The editor session (token, open ports, the running process) | Whoever holds a valid token can change A18 |
| A20 | Draft and scheduled content shown in the editor and preview | It is not public yet; disclosure leaks unpublished links and partner names |

| ID | Actor | Capability |
|---|---|---|
| X15 | A web page the owner visits while the editor runs | Runs script in the owner's browser; can send requests to `127.0.0.1` and, with DNS tricks, to names that resolve there; cannot read cross-origin responses unless a check fails |
| X16 | Another local user or process on the same machine | Can connect to loopback ports; can list processes; may read the same user's files if it runs as the same user |
| X17 | A browser extension with broad host permissions | Can read and modify pages, including the editor tab |
| X18 | The author of a config the owner opened (a shared example, a pasted file, a repo from a helper) | Controls every string the editor will display and every file path it will be asked to open |

Trust boundaries added: (B1) browser to editor server over loopback; (B2) editor origin to preview origin; (B3) editor process to the config folder (read, lock, write, rename); (B4) uploaded bytes to the media folder.

### 10.2 Risk register for the editor

Impact 1 to 5; prerequisite 5 (needs only a web page the owner opens) down to 1 (needs code execution as the owner). Ranked by impact and ease, then by how much the design depends on a single control.

| Rank | ID | Abuse case | Impact | Prereq | Phase |
|---|---|---|---|---|---|
| 1 | T-22 | X15 uses DNS rebinding to make the owner's browser treat an attacker's name as the editor's origin, then reads or writes the config | 5 | 4 | 1 |
| 2 | T-23 | X15 sends a cross-origin form post or fetch from another page to the editor's loopback port (CSRF, including a "simple" request that needs no preflight) | 5 | 5 | 1 |
| 3 | T-32 | X18's config text (title, handle, URL) runs as script in the editor page, which holds the token and can write the config | 5 | 3 | 1 |
| 4 | T-24 | The session token leaks (history, referrer, log, screen, another origin) or is guessable | 4 | 3 | 1 |
| 5 | T-28 | A write corrupts the YAML, drops comments, introduces anchors or tags, races an outside edit, or loses data on a crash | 4 | 4 (mostly self-inflicted) | 0, 1 |
| 6 | T-26 | X18 or a hostile request makes the editor read or write outside the config and media folders (path, `..`, symlink, FIFO) | 4 | 3 | 0, 1 |
| 7 | T-27 | An uploaded file is not what it claims: renamed script, SVG with script, polyglot, size bomb, name collision, overwrite of an existing file | 4 | 3 | 3 |
| 8 | T-34 | The preview iframe runs owner-authored or shared content with access to the editor (parent, token, API) or to the network | 4 | 3 | 1 |
| 9 | T-33 | Front-end supply chain: a package, a CDN script, or a vendored library with a backdoor or a changed hash | 5 | 2 | 1 onward |
| 10 | T-31 | X16 or X17 talks to the editor, reads the page or steals the token | 4 | 2 | 1 |
| 11 | T-29 | The editor reads, shows or logs a secret from the instance (Doppler token, Cloudflare token, Worker secret, `.env`) | 4 | 2 | 1 |
| 12 | T-25 | A request is too big, too slow, malformed or too frequent: memory or thread exhaustion, history or disk filling, parser confusion from a wrong content type | 3 | 4 | 1 |
| 13 | T-30 | The editor is framed by another page and the owner is tricked into clicking (clickjacking) | 3 | 4 | 1 |
| 14 | T-35 | Logs record config content, tokens, paths or upload names | 2 | 3 | 1 |
| 15 | T-36 | The server keeps running after the owner leaves: an open, authenticated port for hours or days | 2 | 3 | 1 |

### T-22 DNS rebinding and non-loopback reach

Abuse: the owner runs the editor, then visits `evil.example`. The attacker's DNS answers with a short TTL, first with their own address (so the page loads), then with `127.0.0.1`. The browser now treats `evil.example:<port>` as the same origin as the page, so the attacker's script can call the editor with no cross-origin check and read the answers. Separately: a bind address other than loopback exposes the editor to the LAN.

Mitigations:
1. Bind only to loopback; refuse everything else at start; reuse `_is_loopback`. **[shipped for the preview in `tests/test_preview.py`; planned for the editor: phase 1]**
2. `Host` must equal `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` with the exact port of that listener; anything else gets 421. This is stricter than the shipped preview, which accepts the names without checking the port. **[planned: phase 1]**
3. A valid token is required on every API request, `GET` included. A successful rebinding still has nothing to authenticate with: the token lives in a JavaScript variable of the real origin and was delivered in a fragment (T-24), so it is never sent to a rebound name. **[planned: phase 1]**
4. `Origin`, when present, must equal the request's own origin; on `POST` it must be present. **[planned: phase 1]**
5. The editor listens on an ephemeral port printed at start, so a page must sweep ports first. This is noise, not a control.

Verification (proposed): `tests/editor/test_guard.py::test_rebound_host_name_is_421_for_every_route`, `::test_host_with_wrong_port_is_421`, `::test_missing_host_is_421`, `::test_host_with_userinfo_or_zone_is_421`, `::test_non_loopback_bind_is_refused_at_start` (0.0.0.0, a LAN address, a name resolving to both). Mutation: widen the allowlist to any host and confirm the rebinding test fails.

Residual: a browser with a flawed same-origin policy. The token is the second wall.

### T-23 CSRF and cross-origin requests from other local pages

Abuse: another page posts a form or `fetch` to `http://127.0.0.1:<port>/__edit/api/op`. A "simple" cross-origin request (form post, `text/plain` fetch) is sent without a preflight and reaches the handler even though the page cannot read the answer. The write happens anyway.

Mitigations:
1. A per-session random token in a custom header `X-RA-Token` on every API request. A custom header forces a CORS preflight for any cross-origin request; the server never answers a preflight (`OPTIONS` gets 405, no `Access-Control-*` header exists), so the browser blocks the request. The token is not a cookie, so nothing is attached automatically. **[planned: phase 1]**
2. `Content-Type` must be exactly `application/json` (or `application/octet-stream` on upload); a form or `text/plain` post gets 415 before the body is read. **[planned: phase 1]**
3. `Origin` is required and must match on `POST`; `Sec-Fetch-Site` must be `same-origin` when sent. Old browsers that send neither still fail on the token. **[planned: phase 1]**
4. State-changing operations are `POST` only; `GET` never changes anything. **[planned: phase 1]**

Verification (proposed): `::test_post_without_token_is_401_and_changes_nothing`, `::test_form_encoded_post_is_415`, `::test_cross_origin_origin_header_is_403`, `::test_options_is_405_and_has_no_cors_headers`, `::test_get_never_modifies_the_file` (hash before and after a sweep of every GET route). A browser test serves a second page from another loopback port that tries a form post and a `fetch`, and asserts the file hash is unchanged. Mutations: remove the token check; remove the content-type check; each turns a test red.

Residual: a bug in a browser's preflight enforcement. None known.

### T-32 XSS inside the editor, from config text

Abuse: a shared example config has a link title like `<img src=x onerror=...>` or a `javascript:` URL. The editor shows it. If it runs as script, it runs on the editor origin, holds the token, and can write the config, upload files and read drafts.

Why it is unlikely, and what limits it if it happens:
1. Every config value enters the DOM through `textContent` or a fixed attribute set. No `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function` or string `setTimeout`. A source grep test enforces it. **[planned: phase 1]**
2. URLs are shown as text and made links only after a check that the scheme is `https` (the loader's rule), with `rel="noopener noreferrer"`. **[planned: phase 1]**
3. CSP: `default-src 'none'; script-src 'self'` with no `unsafe-inline`, no `unsafe-eval`, no nonce and no remote origin; `style-src 'self'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'none'`, `connect-src 'self'`. Injected markup cannot run an inline handler, load a remote script, or post data anywhere but the editor itself. **[planned: phase 1]**
4. Server side, the loader already refuses control characters in text (`_plain_text`, `check_text`).

Verification (proposed): `tests/editor/test_sources.py::test_no_dangerous_sinks_in_editor_sources`; `::test_csp_has_no_unsafe_directive_on_any_editor_response`; a browser test `test_hostile_titles_render_as_text` loads a config whose titles, handles and subtitles hold `<script>`, `<img onerror>`, `"><svg onload>` and `javascript:` and asserts no dialog, no extra network request, and the text appears literally. Mutation: switch one render to `innerHTML`; the grep and the browser test must fail.

Residual: a browser CSP bug, or a sink added later that the grep list does not know. The list is reviewed each phase.

### T-24 Session token: delivery, storage, leakage

Design: 256 bits from `secrets.token_urlsafe(32)`, per process, in memory only. It is delivered in the URL fragment printed to the terminal (`/__edit/#t=...`). The page moves it into a JavaScript variable and calls `history.replaceState` to clear the address bar. It is sent as `X-RA-Token` and compared with `hmac.compare_digest`. It is never in a cookie, `localStorage`, `sessionStorage`, a query string, the HTML, a log line or an error message, and never in the command line or environment of the process.

Why a fragment: it is not sent to the server, so it is not in a request line, an access log or `Referer`. Why a header and not a cookie: a cookie is sent automatically on a cross-site request, which is the CSRF problem (T-23). Why not embed it in the HTML: any local process or rebinding page that can fetch `/__edit/` would receive it.

Left over: the token is visible in the terminal and briefly in the address bar and the browser's history entry; a shared screen shows it; a clipboard manager may keep it. All of that ends when the process exits, because the token is never reused. A reload loses the token (nothing is stored); the owner opens the printed URL again.

Verification (proposed): `::test_shell_html_contains_no_token`, `::test_token_not_in_any_response_body_header_or_log` (run a session, grep all captured output), `::test_wrong_token_of_correct_length_is_401`, `::test_token_differs_between_runs`, `::test_token_not_in_argv_or_environ`; browser test `test_token_removed_from_address_bar_and_not_in_storage`.

Residual: same-user local access (T-31). A user who pastes the full URL into a chat; the editor docs say not to.

### T-25 Request size, rate, content type, malformed input

Abuse: send an enormous body, a slow body, thousands of mutating requests to fill the history or the disk, `multipart/form-data` to reach a different parser, chunked bodies to dodge length checks, or deeply nested JSON.

Mitigations **[planned: phase 1; upload caps phase 3]**: the caps in section 8.5 of the spec (JSON 64 KiB; upload 8 MiB and 2 MiB for avatars; headers 16 KiB; 16 concurrent connections; 20 mutations and 5 uploads per minute; read timeouts; 100 history entries and 5 MiB of history; 200 editor-created media files). `Content-Length` is required and chunked bodies are refused. JSON is parsed with a depth limit and unknown keys are refused. Operation arguments are validated against the schema before they reach the edit core.

Verification (proposed): `::test_json_over_cap_is_413_and_not_fully_read`, `::test_missing_content_length_is_411`, `::test_chunked_is_refused`, `::test_slow_body_times_out`, `::test_21st_mutation_in_a_minute_is_429_and_file_unchanged`, `::test_history_cap_drops_oldest`, `::test_deeply_nested_json_is_422`, plus a fuzz test that no random request produces a 500 or a write.

Residual: a local process can still burn CPU. This is a single-user tool and is not built to resist local denial of service.

### T-26 Path confinement and symlinks (config and media)

Abuse: the config names `media/../../.ssh/id_ed25519` as the avatar; a symlink in the media folder points at a private file; the config path is a symlink or a FIFO; a request tries to reference an arbitrary path through an upload name or an operation argument.

Mitigations:
1. The config path is resolved by `editing.resolve_config`, which refuses a symlink unless `--force-path`; the editor does not offer `--force-path`. The file is opened with `O_NOFOLLOW`. **[shipped for the commands; reused by the editor in phase 0]**
2. The editor takes no file path from a request except one it issued itself (the upload reply, checked against the media listing). The avatar value goes through the asset pipeline's `_check_slot` (extension allowlist, no absolute path, no `.` or `..` segment, no build-reserved folder) and the component-by-component `O_NOFOLLOW` read, which refuses a symlink anywhere on the path and any non-regular file. **[shipped in `tests/site/test_assets_security.py`; the editor adds the save-time hook in phase 0]**
3. The history and media folders are opened relative to the config folder's directory descriptor and refused if they are symlinks. **[planned: phase 0]**
4. The editor serves no file by path: its static files are an allowlist of its own asset names, and there is no "download any file" route. **[planned: phase 1]**

Verification (proposed): `::test_set_avatar_dotdot_is_refused_at_save`, `::test_symlinked_media_folder_is_refused`, `::test_symlink_inside_media_is_refused`, `::test_fifo_as_media_file_does_not_hang`, `::test_history_dir_symlink_refuses_to_start`, `::test_static_route_serves_only_allowlisted_names` (`..`, `%2e%2e`, `%2f`, backslash, NUL), plus the shipped asset tests.

Residual: a parent directory renamed between check and use (the same residual T-20 records for the override loader).

### T-27 Uploads

Abuse: upload a script named `a.png`; an SVG with `<script>` or an external reference; a polyglot valid as two formats; a huge image or compression bomb; a name that collides with an existing file and overwrites it; a path in the file name.

Mitigations (all **[planned: editor phase 3]**, built on checks that are **[shipped]**):
1. The client's file name is ignored entirely. The server picks `media/<kind>-<8 random hex>.<ext>`; the extension comes from the sniffed format. Creation uses `O_EXCL`, so an existing file is never overwritten.
2. Format by magic number, then `check_content` (signature plus structural facts: PNG IEND, JPEG end marker, GIF trailer, WebP and WOFF lengths). A text file renamed to an image fails. [shipped]
3. SVG goes through the shipped allowlist screen (no DOCTYPE or ENTITY, no script, no event handler, no foreign content, no external reference). [shipped: `tests/site/test_assets_content.py`] The editor never serves an uploaded SVG itself; the preview origin serves it as part of the built site, under the site's CSP.
4. Size: the pipeline's 8 MiB cap, 2 MiB for avatars, enforced by counting bytes read and not by trusting `Content-Length`. Pixel dimensions are read from the header and capped (proposed 8000 by 8000) before anything decodes the image. The editor itself never decodes images; the browser shows a `blob:` preview.
5. The upload route does not edit the config, and only a `kind` from a fixed list is accepted.
6. EXIF and GPS data in a JPEG is not stripped (D-08, F23); the editor warns. A published avatar would carry it. This is a disclosure risk to the owner, not to a third party.

Verification (proposed): `tests/editor/test_upload.py::test_text_file_named_png_is_422`, `::test_svg_with_script_is_422`, `::test_svg_with_doctype_is_422`, `::test_client_filename_with_path_is_ignored`, `::test_name_collision_never_overwrites`, `::test_oversize_by_bytes_read_not_header`, `::test_dimension_cap_from_header`, `::test_stored_mode_is_0644_with_no_exec_bit`, `::test_failed_reference_leaves_a_listed_orphan`. Mutation: skip `check_content`; the renamed-text test must fail.

Residual: a valid image that exploits a browser decoder bug (the exposure of any image on the web); EXIF disclosure until D-08 is decided.

### T-28 YAML write safety

Abuse or failure: the write drops comments, reorders keys, re-quotes strings, introduces an anchor, alias or tag (`!!python/object`), changes line endings, races an outside edit and overwrites it, or is cut off by a crash and leaves half a file.

Mitigations:
1. The editor adds no YAML writer. It calls `editing.apply`, which locates nodes with the composer and splices whole lines of the original text, so every byte outside the edit is untouched. Anchors, tags, flow style, CRLF and a dash on its own line are refused rather than rewritten. **[shipped: `tests/test_edit.py`]**
2. Values come from `scalar_text` (quotes anything ambiguous) after `check_text` (no control characters). The core compares "the loaded edited file" with "the original plus exactly the change"; a mismatch writes nothing. **[shipped]**
3. `fcntl.flock` on the config's own descriptor for the whole span from read to rename; a re-check of identity and hash right before the rename; a staged temp file with `fsync`; a backup written before the rename; `os.replace`; `fsync` of the folder. **[shipped: `tests/test_edit_durability.py`]**
4. New operations (spec 6.3) get the same two checks and the same hostile-input tests, plus a test that a sweep of random edits introduces no `&`, `*` or `!` token the input did not have. **[planned: phase 0]**
5. An outside change is detected by hash and by comparing the `expect` values (spec 9.2); an edit never overwrites a field someone else changed. **[planned: phase 1]**
6. History snapshots are mode 0600 in a 0700 folder; undo is refused when the file is not what the editor last wrote. **[planned: phase 1]**

Verification (proposed): `tests/editor/test_ops.py::test_each_op_matches_the_command_byte_for_byte`, `::test_comments_blank_lines_and_quotes_survive_every_op`, `::test_no_anchor_alias_or_tag_is_ever_introduced`, `tests/editor/test_stale.py::test_expect_mismatch_is_409_and_file_unchanged`, `::test_unrelated_outside_edit_is_kept`, `tests/editor/test_history.py::test_undo_refused_after_outside_edit`, `::test_crash_between_stage_and_rename_leaves_the_original`, using the shipped seams `_during_backup` and `_before_replace`. Mutation: remove the `expect` comparison; the stale test must fail.

Residual: a writer that ignores locks and lands in the very short window between the last check and the rename (documented in `editing.py`; it cannot be closed on POSIX). A crash after the backup and before the rename leaves a harmless extra `.bak`.

### T-29 Secrets

Abuse: the editor, a path bug or a debug route reads `.env`, a Doppler token, a Cloudflare token or Worker secrets and shows or logs them; the state endpoint dumps more than the form needs.

Mitigations **[planned: phase 1]**: the editor opens only the config, its history, the media folder and files it created. It reads no environment variable except through its own flags. `/api/state` is built from an allowlist of schema fields, not by serializing a whole object. The config schema holds no secret by design. A file next to the config whose name looks like a secret (`.env`, `*.key`, `*.pem`, `doppler*`) is not listed in the media view. No debug or environment route exists.

Verification (proposed): `::test_state_contains_only_schema_fields`, `::test_dotenv_next_to_config_is_never_read` (a sentinel file, with a spy on `open` and `os.open`), `::test_sentinel_secret_in_environment_absent_from_every_response_and_log`, `::test_media_listing_excludes_secret_like_names`.

Residual: a creator who types a secret into a link title puts it in the config, which is theirs and ends up on the public site. The editor cannot know; its help text says so.

### T-30 Clickjacking

Abuse: a hostile page frames the editor, makes it invisible, and tricks the owner into clicking "Remove link" or "Restore anyway".

Mitigations **[planned: phase 1]**: `Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY` on every editor response. The preview origin allows framing only by the editor. Destructive actions need a second explicit confirmation in the page.

Verification (proposed): `::test_every_editor_response_has_frame_ancestors_none_and_xfo_deny`; a browser test frames the editor from a second loopback origin and asserts the frame stays empty; `::test_preview_frame_ancestors_lists_only_the_editor_origin`.

Residual: browser bugs only.

### T-31 Hostile local process or browser extension

What each can do, stated plainly:
- **Same-user process.** It can read the config and the history folder directly, so the editor adds no new read. It can connect to the loopback port but cannot call the API without the token. It could find the token in the terminal scrollback, in process memory, or by driving the browser; if it can do that it can already edit the config file itself. The editor does not defend against code running as the owner. That is the stance of the existing edit commands too.
- **Another user on a shared machine.** Loopback ports are reachable by every local user. The token stops them calling the API; the shell HTML and static assets are public code. They could still make connection attempts (T-25 caps). The documentation says not to run the editor on a machine with other untrusted users. A Unix-domain-socket mode is a possible later option.
- **A browser extension with page access.** It can read the editor tab, including the token in memory, and drive it. It can already read the owner's logged-in sessions everywhere. The editor does not defend against it; the docs recommend a profile without extensions for editing, and the browser tests run in a clean profile.
- **Process listing.** The token is never passed on a command line or in the environment, so `ps` and `/proc/<pid>/environ` do not show it. The port alone does nothing.

Mitigations **[planned: phase 1]**: token never in argv or environment; 0700 history folder; the stance documented; no remote-debugging port; no `--open` by default.

Verification (proposed): `::test_token_not_in_argv_or_environ` (reads `/proc/<pid>/cmdline` and `environ` of the running server), and a documentation test that the editor docs name this limit.

Residual: accepted. Reopen if the editor is ever run on shared, remote or hosted machines.

### T-33 Front-end supply chain

Abuse: a compromised npm package, a changed CDN file, or a vendored library with a backdoor runs in the editor page, where it holds the token and can write the config.

Mitigations:
1. No npm, no bundler, no build step, no CDN, and no package manager file for the front end. Plain ES modules written in this repository, loaded by relative path. The CSP forbids any script origin but `'self'`, so even a stray `<script src="https://...">` does not run. **[planned: phase 1]**
2. A library may be added only by a pull request that vendors the exact file under `editor_assets/vendor/`, records its version, source URL, SHA-256 and license in `editor_assets/vendor/MANIFEST.json` and in the third-party notices, and a test recomputes the hash. No library is planned for the MVP.
3. Test-only tools (an accessibility checker, hypothesis) are not packaged; a wheel-contents test checks that nothing from `tests/` ships. axe-core, if used, is vendored and hashed under `tests/` (spec D-14).
4. The Python side adds no runtime dependency; it uses the standard-library server the preview uses.

Verification (proposed): `tests/editor/test_vendor.py::test_every_vendored_file_matches_its_manifest_hash_and_has_a_license`, `::test_no_package_json_or_lockfile_in_editor_assets`, `::test_no_remote_url_in_any_editor_asset`, `::test_wheel_contents_exclude_tests`.

Residual: a vendored file changed together with its hash in one pull request; review catches it by size and content. The standard-library server is as trusted as Python.

### T-34 Preview iframe isolation

Abuse: the preview renders built site HTML and scripts from the owner's config and overrides, possibly from a shared example. That page could try to read the parent, call the editor API, steal the token, navigate the top window, open popups, or fetch from the network (remote images and fonts the site CSP allows).

Mitigations **[planned: phase 1]**:
1. Separate origin: the preview is served on its own port, so it is cross-origin to the editor; it cannot read the editor's DOM, storage or memory, and cannot call the API without the token it never receives.
2. `<iframe sandbox="allow-scripts">` only, with no `allow-same-origin`, `allow-top-navigation`, `allow-popups`, `allow-forms`, `allow-modals` or `allow-downloads`, and `referrerpolicy="no-referrer"`. The frame gets an opaque origin and has no storage. (Spec D-05.)
3. The editor's CSP `frame-src` lists the preview origin only.
4. The preview origin sends `frame-ancestors <editor origin>` and, in editor mode, `img-src 'self' data:` and `connect-src 'self'`, so the previewed page makes no outside request by default (spec D-06). Remote thumbnails show as a placeholder.
5. The preview keeps today's behavior: GET and HEAD only, Host and Origin checks, nothing outside the temporary build directory.
6. No `postMessage` between the frames. The width buttons resize the iframe from outside.

Verification (proposed): browser tests `test_preview_cannot_read_parent_dom`, `test_preview_cannot_fetch_editor_api` (a script in a test override tries `fetch` to the editor port and asserts failure and an unchanged file), `test_preview_makes_no_outside_request` (a route handler records every request and asserts all are loopback), `test_sandbox_attribute_is_exactly_allow_scripts`; unit `::test_preview_headers_in_editor_mode`.

Residual: a browser sandbox escape. Owner-authored scripts in an overrides directory run in the preview with the site's own powers (they are the owner's code; T-20).

### T-35 Logging

By default the editor prints the URL once and one line per mutation: time, operation name and result code (`ok`, `refused:stale`, `refused:invalid`). No values, no titles, no URLs, no file names, no request headers, no token, no `Host` or `Origin` text. The existing handler's `log_message` is silent and stays so. Errors shown to the user quote config paths like `tabs[0].blocks[2].url`; the loader's own message can include a bad value and goes to the browser only, never to the log. `--verbose` is not offered in the MVP. **[planned: phase 1]**

Verification (proposed): `::test_log_lines_use_fixed_codes_and_contain_no_request_data` (run a session with sentinel strings in every field and header; grep captured stdout and stderr), `::test_token_never_logged`.

Residual: terminal scrollback keeps the printed URL (T-24).

### T-36 Shutdown and idle timeout

Abuse: the owner closes the tab and forgets; an authenticated port stays open for days on a laptop that moves between networks.

Mitigations **[planned: phase 1]**: a default idle timeout of 30 minutes since the last mutating or explicit keep-alive request (version polls do not count). On timeout the server stops both listeners, deletes the temporary build directory and exits with a message. `POST /api/shutdown` and Ctrl+C do the same. Closing the tab sends a best-effort shutdown request on `pagehide`; the idle timer covers the case where it never arrives. The token dies with the process. The history folder stays, since it is the owner's undo.

Verification (proposed): `::test_idle_timeout_stops_listeners_and_removes_temp_dir` (fake clock), `::test_polls_do_not_reset_idle`, `::test_shutdown_route_requires_token`, `::test_port_is_closed_after_exit`.

Residual: up to 30 minutes of an open, token-protected port.

### 10.3 Residual risk summary for the editor

| Risk | Accepted by | Reopen when |
|---|---|---|
| Code running as the owner, or a browser extension, can drive the editor or read the config directly (T-31) | Maintainer | The editor is run on shared, remote or hosted machines |
| A writer that ignores locks lands between the last hash check and the rename (T-28) | Maintainer | Instances are edited by more than one tool at once |
| EXIF and other metadata in uploaded photos is published unless the owner strips it (T-27, D-08) | Maintainer | D-08 is decided, or a leak is reported |
| Up to 30 minutes of an open port after the owner leaves (T-36) | Maintainer | A shorter timeout is requested |
| Owner-authored scripts in an overrides directory run in the preview (T-34, T-20) | Maintainer | Overrides come from a third party |
| Browser or CSP engine bugs (T-23, T-30, T-32, T-34) | Maintainer | Any advisory for the supported browsers |

### 10.4 Abuse tests the editor plan must contain

Beyond the named tests above, the editor plan carries these. Numbering continues the list in section 8.

57. `tests/editor/test_guard.py` (all of T-22, T-23, T-25) plus the browser tests for rebinding and cross-origin posts, each paired with the mutation step of item 48.
58. A rebinding drill with a test-only mapping: the test sends requests to the loopback listener with a fake `Host` such as `rebind.example:<port>` and asserts 421 on every route. No real DNS is touched and no outside host is contacted.
59. `tests/editor/test_sources.py` and the browser hostile-title test (T-32).
60. `tests/editor/test_upload.py` (T-27), and an extension of `tests/test_browser_tests_never_navigate.py`: a browser test may not call `webbrowser.open`, pass `--open`, or load a non-loopback URL. This protects the maintainer's desktop from a test launching a mail client or an external page.
61. A final whole-branch security review of the MVP on the cyber-program tier before the first release, and a separate one for phase 3 (uploads).

Scope of testing: all of the above are local tests against a server the test starts on loopback, with files in a temporary directory. No test probes a host the project does not own.

### 10.5 Questions for the maintainer

They are in section 17 of the spec. The ones that change this model: D-01 token delivery, D-03 ephemeral port, D-05 iframe sandbox without `allow-same-origin`, D-06 block remote images in the preview, D-08 strip image metadata, D-09 undo after an outside edit, D-10 idle timeout, D-18 no hosted editor from this design.

Not covered in this addendum: the hosted editor (phase 4 of #103), a git-based CMS front end, and the assistant workflow (phase 5), each of which needs its own model; the security of the browser the owner uses; the instance repo's CI and deploy path (T-02, T-19).
