# Records of processing (template)

An engineering template, not legal advice. See the [README](README.md). Copy
this page to a private place, fill in every OWNER blank, and keep it current.
Whether you must keep such a record at all, and in what form, is a question for
your lawyer.

Markers: **Code today** is behavior in the repository now. **PLANNED** is a
privacy baseline item that is tracked but not built; do not present it as live.
**OWNER** is a blank only you can fill. **LAWYER** is an open legal question.

## 1. Controller

| Item | Value |
|---|---|
| Controller (person or entity running the site) | OWNER |
| Contact for data requests (email or postal address) | OWNER |
| Representative in the EU or UK, if one is needed | OWNER; LAWYER: is one needed? |
| Data protection officer | OWNER (most small sites have none; LAWYER: confirm) |
| Supervisory authority | OWNER (see [breach-procedure.md](breach-procedure.md)) |
| Date of this record, next review date | OWNER |

## 2. Processors and recipients

| Party | Role | What it receives | Status |
|---|---|---|---|
| Cloudflare (Workers, D1; Turnstile when the gate uses it) | Processor for hosting and storage. For Turnstile, Cloudflare describes itself as processor for bot protection and controller for improving detection | Requests to the Worker, stored rows, Turnstile signals (IP, TLS fingerprint, user agent, site key, origin) | OWNER: record the date you accepted Cloudflare's data processing terms. LAWYER: read the terms themselves. |
| Chat or mail provider for owner alerts (Discord, Matrix, mail) | Separate recipient | Alert text, which can include a lead's email address | OWNER: list which are switched on. LAWYER: processor terms and role. Off by default. |
| Secrets manager | Holds deploy secrets only | No visitor data | OWNER: name yours |
| Twitch, Kick, YouTube | Own controllers of their audience data | The site receives aggregate numbers only | Code today |

## 3. Processing activities

Retention numbers are the shipped defaults; your `site.yaml` may differ.
Fill the last column from your own deployment.

| # | Activity | Purpose | People | Data | Legal basis | Retention | Recipients and transfers | Security measures | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Page view record (D1 `view_events`) | Audience measurement: which pages and sources get visits | Site visitors | Page, source kind and label, referrer host, campaign label, window-size letter, hour; country and at most a region (`analytics.geo_level`, default `region`); browser and version, OS, device kind, language; bot class | OWNER, with LAWYER. Often legitimate interest, which needs a short written balancing note. A national audience-measurement exemption may apply in some countries; LAWYER | Raw 7 days by default, held up to three times that, never more than 90; daily totals 90 days; monthly totals 25 months | Cloudflare; transfer see section 4 | No IP stored, no analytics cookie, hour-rounded time, small cells combined before publishing, Global Privacy Control and Do Not Track honored (record dropped), scheduled purge | Code today. Counts as personal data in combination: INFERRED. |
| 2 | Daily hashed visitor id (D1 `visitor_seen`) | Count unique visitors per day, abuse limits | Site visitors | Keyed hash of the IP under a daily key | OWNER, with LAWYER (often legitimate interest) | Deleted at the end of the UTC day | Cloudflare | Daily key rotation; the IP is read in memory only | Code today. Personal data while it exists: INFERRED. |
| 3 | IP address in transit | Handling the request | Site visitors | IP address | OWNER, with LAWYER | Not stored by the site; Cloudflare handles it as the network provider | Cloudflare | Not persisted by the Worker | Code today |
| 4 | Click record and daily click counters | Count outbound link clicks | Site visitors | Link id, source label, hour; daily counts by link and source, and by link and country, never joined | OWNER, with LAWYER | Hourly records 90 days; daily counts 90 days; monthly totals 25 months | Cloudflare | GPC and DNT clicks are not counted; no cookie, IP or user agent stored | Code today. Probably not personal data: INFERRED. |
| 5 | Leads (D1 `leads` and kit events) | Media kit gate: record who asked for the kit so you can reply | Sponsors and brand contacts | Email as typed (unverified), country code, timestamps, hour-rounded kit events | OWNER, with LAWYER (business contact; no marketing mail unless you add its own basis) | `privacy.lead_retention_days`, 1 to 365, default 30; Cloudflare's recovery window for the database is outside this | Cloudflare; alert copy in section 3 row 8 | Purge job, delete command, kit events removed with the lead. No consent record beyond the act of submitting. | Code today. A separate leads database is PLANNED (#772). A built-in access tool for one person's lead is PLANNED. |
| 6 | Unlock cookie | Remember that the visitor passed the gate | Gate users | One host-locked, HTTP-only cookie | OWNER, with LAWYER (often treated as needed for the requested function) | 24 hours | None | Host-locked, HTTP-only | Code today |
| 7 | Business inquiry form | Let a sponsor send a request | Sponsors and brands | Name, brand, budget, free text, optional email | OWNER, with LAWYER (often steps before a contract) | Not stored by the Worker; the receiving room or mailbox follows your own rule | Chat or mail provider | Sent once, fields capped | Code today. A visible "send no sensitive data" notice is PLANNED. OWNER: set a retention rule for the receiving room. |
| 8 | Owner alerts | Tell you about a new lead, unlock or inquiry | Leads, inquirers | Short notice; the email address shown per `alerts.visitor_email` (`full`, `masked`, `none`) | OWNER, with LAWYER | Not purged by the site; follows your chat or mail rule | Chat or mail provider; may leave your region | Off by default; no IP, user agent, cookie or country in the text | Code today, default `full`. A masked default and a purge routine for alert copies are PLANNED. Until then, OWNER: set `masked` or `none` and a room rule. |
| 9 | Turnstile at the gate | Bot and abuse protection | Visitors reaching the gate | IP, TLS fingerprint, user agent, site key, origin, sent to Cloudflare | OWNER, with LAWYER (security) | Cloudflare's own policy; verify the current text | Cloudflare | Loaded only where the feature is on | Code today at the gate. Use at the analytics beacon is PLANNED, with never loading it for GPC or DNT visitors. LAWYER: whether it counts as strictly necessary where an ePrivacy rule applies. |
| 10 | Beacon token and proof of work | Confirm a real browser sent the view | Visitors | Short-lived token held in memory only | OWNER, with LAWYER | Valid for its short window only | None | Memory only | PLANNED |
| 11 | Daily counters in a managed analytics store | Cheap daily aggregates | Visitors (not identifiable) | Floored daily counts | OWNER, with LAWYER | Provider's window, no early delete | Cloudflare | No exact timestamps | PLANNED, optional. Re-screen with [dpia-screening.md](dpia-screening.md) if the design changes. |
| 12 | Platform collector numbers | Audience size on the media kit | None (aggregates) | Totals from Twitch, Kick, YouTube | Not applicable while aggregate | Platform's own windows | None | Aggregates only | Code today. Becomes personal data if person-level fields are ever imported. |

## 4. Transfers outside your region

Cloudflare can process data in the United States. Common mechanisms are an
adequacy framework or standard contractual clauses in the processing terms.
Which one holds today, and whether it still holds after any court ruling, is a
LAWYER question. OWNER: record the mechanism in Cloudflare's terms you accepted
and the date you read them. Alert providers in section 2 may be additional
transfers.

## 5. Rights requests

| Request | How it is handled today |
|---|---|
| Delete a lead | `reach-around leads delete`, then apply the SQL it prints; see [privacy.md](../privacy.md). Copies outside the database (alert room, mailbox, your own exports, the provider's recovery window) are yours to remove. |
| See a lead | No built-in tool; a manual query. A tool is PLANNED. |
| Page views and clicks | No person-level key exists, so a record cannot be matched to a person; retention and the GPC and DNT drop are the controls. |
| Object | A Global Privacy Control or Do Not Track signal drops views and clicks. A visible opt-out page is PLANNED. |

OWNER: write your reply time limit and who answers.

## 6. Open questions for a lawyer

1. Legal basis for each row, and the written balancing note where you rely on
   legitimate interest.
2. Whether a national audience-measurement exemption fits the beacon, and
   whether a browser-signal opt-out is enough.
3. Turnstile as strictly necessary.
4. Whether an EU or UK representative is needed.
5. Processor terms and roles for alert providers.
6. Email rules for business contacts in your countries.
7. The transfer mechanism and its stability.

## 7. OWNER blanks, collected

Controller name, contact, representative decision, regulator, which alert
providers are on, Cloudflare terms date, alert room rule, next review date.
