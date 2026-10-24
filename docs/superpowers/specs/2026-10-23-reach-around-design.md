# Reach Around: design

Status: **draft for the maintainer's approval**. Alpha. Written 2026-10-23 for issue #24, part of epic #1.
Tagline: *Reach Around. We've got your back.*

How to read this: **[Decided]** is the maintainer's own decision from the design conversation. **[My call]** is a choice the author made and the maintainer has not agreed to. **[Builder]** is left to whoever builds it. Anything in the research notes marked UNVERIFIED is still open.

## 1. Intent

Reach Around replaces Beacons.ai for a creator as close to 1:1 as possible, and then goes further. Beacons gave the maintainer a link page at `links.chiefgyk3d.com`, an always-current media kit that partners can open, and analytics. It is dated and limited: it skips Bluesky, Mastodon and Kick, ignores Discord and Matrix community sizes, needed a Facebook Page to link Instagram, and its PDF export prints the email-gate boxes and blurs the content even after unlocking.

The deeper aim is one pane for all of a creator's analytics, open source, so other creators can run it themselves. The name is deliberately bigger than "links": the link page and media kit are modules of a creator analytics hub.

## 2. Who it is for

1. **The maintainer first [Decided].** Their own accounts, their own infrastructure (Cloudflare, a homelab, Grafana in the lab). Every first-release decision is tested against this person.
2. **Other creators second [Decided].** Self-hosted, with their own API keys. Some will run Grafana in the cloud or not at all, so Grafana is optional and the output is pluggable [Decided].

## 3. Goals, non-goals, anti-goals

**Goals**
- 1:1 replacement of what the maintainer uses in Beacons: link page, media kit, click and visit tracking, partner email capture and per-visitor tracking, view alerts [Decided].
- One daily-history store of numbers across every platform, including the ones Beacons skips.
- A polished, professional look that "pops" and can be shown to partners [Decided].
- A private, complete PDF media kit the maintainer generates and hands out; everyone else uses the website [Decided].
- Static to the visitor, to keep the attack surface small [Decided].
- Education: every security control and design choice is documented so readers learn from it, as in the maintainer's other projects [Decided].

**Non-goals**
- Invoicing. Zoho Books does it. A later add-on may read paid invoices from it (#3) [Decided].
- Replacing `support.chiefgyk3d.com` (repo `support-me`). It stays its own thing for streaming and is consolidated at the very end [Decided].
- Scraping platforms against their terms, or reading data the platform does not give the account owner.
- A hosted multi-tenant service. Reach Around is self-hosted.

**Anti-goals (a failure even if it technically works)**
- Visitor data (emails, clicks) readable from the internet, or the Worker usable as a spam relay.
- A public page that shows data a platform's terms forbid showing publicly (for example YouTube Analytics data about the owner's audience).
- Secrets in the public repository or in logs, including the Kick stream key that appears in the channel response.
- Personal details of the maintainer (address, callsign, grid square, hostnames, machine identifiers) in the public repository, issues, logs or examples [Decided].
- A first release that only works on the maintainer's exact hardware or accounts.

## 4. Architecture

```
 owner's private instance repo (config, copy, partners)        Cloudflare
              |                                              +--------------------+
              v                                              | Pages (static site)|
  engine (this repo, public) --build--> static site -------->|  link page, kit    |
              ^                                              |  public half       |
              |                                              +--------------------+
   daily collectors --> snapshot store --> numbers JSON          |        |
   (homelab or CI)        (SQLite/Postgres)    |                 |        v
              |                                |                 |  Worker (write-only)
              v                                v                 |   gate, click log,
   sinks: Grafana (lab or cloud), CSV/JSON export,               |   owner alert, email
   executive summary, private PDF (run locally)                  +-- D1: leads, events
```

**4.1 Engine and instance [My call].** The public repo is the engine. A creator's own configuration, partner list, copy and branding live in a separate private "instance" repo, so nothing personal is in the public code. The engine builds the site from the instance configuration.

**4.2 Configuration [My call, format is Builder].** A single human-edited file describes: the creator, tabs and sections, links (with affiliate disclosure and show or hide dates), platforms and accounts, media kit content (about, partners, case studies, rates), alert destinations and sink options. The format is validated, and refuses ambiguous input. Format and file layout are [Builder].

**4.3 The static site [Decided: Cloudflare Pages].** Pages are fully usable without client JavaScript: tabs have their own URLs, collapsible sections use native disclosure elements, and the layout is responsive (a centered column on desktop with art in the margins; full width on phone with a tab bar that does not clip). Design quality is a first-class requirement. The generator and styling stack are [Builder]. Parity targets from the Beacons recon (#22 will extend them): four tabs, an icon row, per-link detail menus, collapsible groups, affiliate disclosure text.

**4.4 The Worker [Decided: one small write-only Worker].** "Write-only" means it never returns visitor data (emails, clicks, leads). Its only outputs are: an acknowledgement, the gated media kit content after a valid gate pass, and an owner alert. Responsibilities:
- `gate`: accept an email with a Turnstile token, apply limits, store the lead, return a short-lived signed token that unlocks the full kit in the browser.
- `event`: record link clicks and kit views as minimal events (no IP address stored; country level at most from Cloudflare headers; bot filtering).
- `alert`: tell the owner when someone unlocks or views the kit (email, Matrix or Discord, configurable).
- `send`: optional "email me a link" message through the mail provider.
The Worker holds no admin interface. The owner reads data through the Cloudflare API from their own machine with a scoped read token, never through a public route.

**4.5 The gate [My call: instant reveal, optional verified link].** A partner enters an email and the kit opens immediately. A separate "email me a copy" button sends a link; clicking it marks the lead verified. This favors partner convenience and keeps the Mailgun budget for deliberate requests. Verified-link-first is a one-setting alternative.

**4.6 Email delivery [Decided: Mailgun first; SES later possible].** A transactional provider over its API, from a sending subdomain with SPF, DKIM and DMARC. Free tier figures (100 per day) are the maintainer's and need checking. Controls: Turnstile on every form, per-IP and per-address limits, one message per address per day, a fixed template with no visitor-supplied text, and a global daily send budget so a flood cannot exhaust the quota and block real partners (when the budget is spent the kit still opens, just without the email).

**4.7 Collectors and the snapshot store [My call].** One small collector per platform, behind a common interface: given account config and credentials, return metrics for today. A scheduler runs them daily. Results go into an append-only **snapshot store** with one row per platform, account, metric and day, plus the source and whether the value was fetched, imported or typed in. Reasons this is central: several platforms keep little or no history (GitHub traffic is 14 days; Discord, Matrix and Kick expose no join/leave history), so the store *is* the history. Default store is SQLite; Postgres is optional. The language is [Builder]; Python 3.11+ is the default because it matches the maintainer's CI. Run location is configurable: a scheduled CI job for polling collectors, and a long-running homelab process for listeners (StreamElements tips websocket, Twitch viewer sampling).

**4.8 Manual and import paths are first-class [Decided in spirit].** Where an API is closed or restricted (LinkedIn, TikTok demographics, Kick followers), the user can type values or import CSV or XLSX, flagged as imported. The media kit can mark a number as self-reported.

**4.9 Token health [My call].** Tokens expire on a schedule (Instagram and Threads 60 days, Bluesky public clients 2 weeks, YouTube refresh tokens 7 days while the consent screen is in Testing). A token registry tracks expiry, refreshes what it can, and alerts the owner well before something lapses. A collector failing is shown as "stale since" on dashboards, never silently zero.

**4.10 Outputs [Decided: optional Grafana, exports on demand].**
- A built-in static dashboard, so Grafana is not required.
- Sinks for Grafana in the lab or in the cloud: a scrape endpoint, a push, or a database it reads. Grafana never holds a Cloudflare token. The homelab collector holds the one token that can read the Worker's database, copies events into the local store (section 4.11), and Grafana reads only the local store or the sink the collector feeds.
- `export`: a dated bundle of clicks, visits, follower history and leads (CSV and JSON in one archive). Leads stay local; they never go to Grafana.
- A polished **executive summary** PDF from the same data, suitable for showing to partners [Decided].
- The **full media kit PDF** is rendered on the maintainer's machine from the same data as the site, paginated, complete, never uploaded or linked [Decided].

**4.11 Data flow, gated content, secrets and retention [My call; added after the builder check].**
- **Gated content.** The instance build produces two bundles: the public static site, and a gated bundle (the demographics, partners detail, case studies and rates that are not public). The gated bundle is uploaded to storage bound to the Worker (KV or R2) and is never part of the Pages deploy. The Worker serves it only against a valid token.
- **The token [Decided: not forwardable].** An unlocked kit must not be shareable by link, because a forwarded link should send the recipient to the gate so they unlock it themselves and are counted. The unlock is a short-lived signed token (HMAC with a Worker secret), lifetime 24 hours by default and configurable, tied to the lead record, and carried only in an HttpOnly, Secure, SameSite cookie. It never appears in a URL. The emailed "send me a link" is single use: the first click sets the cookie in that browser and marks the lead verified; a second click or a forwarded copy lands on the gate. A partner who opens it on another device simply unlocks again, which records another event. Tests must prove that an expired, tampered or missing token gets nothing, that a used email link gets nothing, and that no URL carries the token.
- **System of record.** The collector's snapshot store is the system of record. A daily pull job copies new events from the Worker's database (through the Cloudflare API, with a token held only by the collector host) into the local store as daily aggregates. The Worker keeps raw events for a short window (default 90 days) and then deletes them. Dashboards, exports and Grafana read only the local store.
- **Leads stay separate.** Lead emails are copied to the owner's local store only when the owner runs an export or a follow-up, never into aggregate tables, and never to Grafana.
- **Where collectors run, and secrets.** Collectors that use only public endpoints, plain API keys or a GitHub App can run as scheduled CI jobs. Collectors that use rotating OAuth refresh tokens (YouTube, Instagram, Threads, TikTok, X) run in the homelab container with a persistent volume, so a refreshed token is written back to a secrets manager or an encrypted local file. Credentials live in the secrets manager or environment, never the instance repo. The instance repo holds configuration only, never snapshots, leads or tokens.
- **The `event` endpoint.** Click and view events are unauthenticated writes. They get no Turnstile challenge (it would hurt the link page), but they are rate-limited per client, bot user agents and missing-referrer cases are dropped, and counts are documented as approximate.
- **Display policy.** The engine ships a versioned table of per-platform rules, each citing the platform's terms with a date (for example: do not show YouTube owner analytics publicly; LinkedIn numbers must be self-reported). The instance owner can override a rule, which logs a build warning, and is accountable for the override. The build refuses to put a forbidden field on a public page.
- **JavaScript.** The public half of the site, including the link page and tabs, works with no JavaScript. The gate needs JavaScript because Turnstile does; the unlocked kit may use it too. The private PDF is the offline, no-JavaScript form, and only the owner has it.
- **Retention and deletion.** Leads are kept 12 months by default (configurable). A deletion command run by the owner removes a lead by email from the Worker's database and the local store. The privacy page names a contact for deletion requests.

## 5. Per-platform plan (from the research notes; all drafts)

| Platform | First-release approach | Main caveat |
|---|---|---|
| Bluesky, Mastodon | Public endpoints, no review | Field names and Pixelfed parity unverified |
| GitHub | GitHub App (stars, followers, traffic) | Traffic is 14 days, so the store keeps it |
| Discord, Matrix | Bot or account counts, daily snapshot deltas | No join/leave history endpoints |
| Twitch | Followers, subscribers, VODs, plus a viewer sampler | Developer Agreement not yet read |
| YouTube | Public counts with a key; OAuth for owner analytics | Owner-authorized data must not be shown publicly; keep the consent screen out of Testing |
| Instagram, Threads | Official APIs, professional account, no Facebook Page needed for Instagram Login | 60-day tokens; display rules unverified |
| TikTok | Display API in Sandbox for the maintainer's own accounts | App review for others; no demographics API; no new-video webhook |
| Kick | Official OAuth API (subscribers, live status, viewers) | No follower count; the response includes the stream key and must be stripped |
| X | Official API, pay-per-use credits | Needs a payment card; per-call price to check |
| LinkedIn | Import and manual entry | API gated; terms bar scheduled refresh and public display |
| StreamElements | Realtime tips websocket | REST reference and JWT lifetime unverified |
| Patreon | Official v2 API (patron counts) | Income not exposed; derive or import |
| Social Blade | Optional gap-filler for YouTube, TikTok, Twitch, Facebook, Instagram, about $2 a month | Own key per user; data is "for your use only", public sharing needs credit |

## 6. Security, abuse and privacy

A full threat model is a separate deliverable (issue to be filed) and is written as an education page. The design assumes these abuse cases and requires a mitigation and a test for each:
- **Spam relay and quota exhaustion** through the email form: Turnstile, limits, fixed template, daily budget.
- **Bypassing the gate** to read gated content: the gated content is only served by the Worker after a valid pass and is never in the static bundle.
- **Fake or hostile emails and bot traffic** polluting leads and click counts: verification link, bot filtering, no trust in client-supplied fields.
- **Secret exposure:** credentials live in a secrets manager or environment, never the repo; API responses are filtered at the edge of each collector (the Kick stream key is dropped there).
- **Personal data:** collect the minimum (email, time, source, country); no IP addresses; a retention setting; deletion on request; leads never leave the owner's machine in exports meant for sharing.
- **Supply chain:** pinned dependencies, the maintainer's standard CI and secret scanning.
- **Terms compliance:** a per-platform display policy in the config; the site refuses to render a field the policy forbids for public pages (YouTube owner analytics, LinkedIn).

## 7. Hosting, cost and cutover

Cloudflare Pages and Worker on free tiers; Mailgun free tier; Social Blade about $2 a month if used; X pay-per-use if used. Everything is tested on a **test subdomain [Decided]**. `links.chiefgyk3d.com` switches at cutover, with a DNS rollback to Beacons as the fallback. `support.chiefgyk3d.com` stays separate until the very end, then consolidates [Decided]; the engine's look and configuration format are designed so that is a small step.

## 8. Repository, versions, containers and process [Decided]

**Versions and tags [Decided].** The project uses semantic versions, and tags and releases start when the first container image exists. Until then there are no tags. While alpha, versions are `0.x`, and a breaking change to the configuration format bumps the minor version and is called out in release notes. Releases go through the git-your-ship-together release workflows, and published images are verified after publishing.

**Containers: Docker and Podman both [Decided].** The collectors, scheduler and local tools ship as an OCI image built from a `Containerfile`, runnable under Docker and under Podman. That means: no Docker-socket dependency, rootless-friendly (non-root user, read-only root filesystem where possible, writable paths declared), no Docker-only features, and image references pinned by digest. Documentation covers `docker compose`, `podman compose`, and a systemd/Quadlet unit for Podman. The Worker and the Pages site deploy through Cloudflare's tooling and are not containers; the site build itself can run inside the same image. Long-running listeners (StreamElements, Twitch sampler) run as their own container so a crash does not take the daily collectors with it [My call].

Private `ChiefGyk3D/Reach-Around`, public later by the maintainer's choice, Apache-2.0. CI calls the git-your-ship-together reusable workflows. Work is tracked on GitHub Project 12 with Scrum Around and Find Out: an issue and a card before any work, every PR links its issue. The README is marked alpha and ends with the Support the Project, tipping and socials block.

## 9. First-release scope and order [My call; the plan will split it into issues]

0. Foundation: repo baseline, CI, instance/engine split, config schema, docs skeleton. The first container image (Docker and Podman) and the first tag, `v0.1.0`, come as soon as there is something to run in it.
1. Link page with click tracking and the owner's own-accounts-first collectors for the easy platforms (Bluesky, Mastodon, GitHub, Discord, Matrix, Twitch, Kick, StreamElements).
2. Snapshot store, token health, built-in dashboard.
3. Media kit, gate, email, view alerts, private PDF.
4. Grafana sinks, exports, executive summary.
5. Hard platforms: YouTube OAuth, Instagram, Threads, TikTok Sandbox, X, Social Blade.
Later: Zoho Books (#3), asking-rate guidance (#4), monetization tracking (#5), parity extras from #22, other creators' onboarding.

## 10. Decisions summary

| Decision | Owner |
|---|---|
| Cloudflare Pages plus one write-only Worker; static to the visitor | Maintainer |
| Private PDF generated by the maintainer; others use the website | Maintainer |
| Mailgun first, SES later possible; abuse controls required | Maintainer |
| Test subdomain first; links.chiefgyk3d.com swaps at cutover | Maintainer |
| support-me stays separate until the end | Maintainer |
| Optional Grafana (lab or cloud); exports and an executive version | Maintainer |
| Own accounts first, other creators second | Maintainer |
| Apache-2.0; README alpha with support block; SAFO and GYST | Maintainer |
| Semantic versions, tags start with the first container image | Maintainer |
| Docker and Podman both supported (OCI image, rootless-friendly) | Maintainer |
| Engine/instance split; snapshot store; token registry | Author |
| Instant-reveal gate with optional verified link | Author |
| Unlocked kit is not forwardable: cookie-bound unlock, single-use email link | Maintainer |
| D1 for leads and events; SQLite default store | Author |
| Python 3.11+ default for collectors | Author |

## 11. Open items

- Platform terms still to be read by a human: Kick Developer Terms, Twitch Developer Agreement, StreamElements REST reference, YouTube sensitive-scope verification.
- Whether a general-purpose open-source tool counts as "another product" under Social Blade's terms.
- The exact per-call price of the X API and whether a free tier remains.
- The Beacons parity inventory (#22) will add features and may change the first-release scope.
