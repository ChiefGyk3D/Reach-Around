# Beacons parity and cutover checklist

Issue #22, under epic #1. This is the definition of done for replacing the maintainer's Beacons.ai page with Reach Around. It lists what Beacons does, what Reach Around does today, what the cutover includes, and how each item is checked.

State was verified by reading `origin/main` at `5ec0b3c` on 2026-11-06. "Verified" means the file or pull request named in the evidence column exists on that commit. It does not mean the behavior was run in a browser, except where a check says so.

This page is generic on purpose. The repository is public, so it holds no addresses, affiliate identifiers, partner names, rates or visitor data. Those live in the maintainer's private instance.

Status words:

- DONE: built on main and covered by tests or goldens.
- PARTLY: some of the feature is built; the gap is named.
- NOT BUILT: no code on main.

Cutover words: **v1** means required before DNS moves. **Deferred** names a reason. **Not included** is a deliberate decision.

## Feature table

### Link page

| Feature | Beacons behavior | Reach Around on main | Cutover state | How we verify |
|---|---|---|---|---|
| Tabs | Four tabs (support, socials, product recommendations, contact), default tab first, each with its own URL. | DONE. `tabs` layout with a page per tab (`src/reach_around/site/templates/tab.html`, `tests/site/test_build.py`). | v1 | Scripted: build the production config and assert one `index.html` per tab and that the tab titles and order match the Beacons list in the private snapshot. |
| Single-page option | Not offered. | DONE. `page_layout: sections` (#220, #221). | Not required. The page keeps the tabs layout to match Beacons. | Config choice, covered by the existing golden tests. |
| Header icon row | Social icons repeated on every tab; the email icon leads to the contact tab instead of a mail link. | DONE. `creator.socials` with `tab:` links (`src/reach_around/config/models.py`, `docs/config.md`, `tests/site/test_internal_links.py`); icons in `src/reach_around/site/icons.py`. | v1 | The scripted link check (HTML part) covers every icon target, internal ones included, on every tab. Observable: open each tab on desktop and phone and compare the icon row. |
| Link cards | Title, optional subtitle, optional thumbnail, a featured pill. | DONE. `src/reach_around/site/templates/blocks/link.html` and `_macros.html`; thumbnails (#113). | v1 | Link check; visual comparison on desktop and phone. |
| Old `/socials` path | The old page lives at `/socials` on the same host; shared links point there. | DONE as a build option (#356): `redirects.socials` writes a `_redirects` file sending `/socials` and `/socials/` to `/` (301, or 302 while testing). `tests/site/test_redirects.py`. Host behavior is unverified (`docs/deploy.md`, "Redirecting the old /socials path"). | v1 | Scripted: the build test above. Observable on the staging host with 302: `/socials` and `/socials/` land on `/` with a query string kept, other paths answer as before. Repeat with 301 after DNS moves. |
| Eight support links | A support tab with eight links, one featured. | DONE as a capability (link blocks, `featured`). Content is entered in the private instance. | v1 | Scripted link check (HTML anchors plus manifest). |
| Collapsible sections | Groups on the socials and recommendations tabs open and close. | DONE. `<details>` section block (`src/reach_around/site/templates/blocks/section.html`, `collapsed` option). | v1 | Observable: sections start open or closed as on Beacons, and work with the keyboard. Golden test covers the markup. |
| Per-link menu | A per-link three-dot menu. | NOT BUILT. Links are plain anchors; there is no menu. | Deliberately not included in v1. A menu adds script and page surface, and nothing in the cutover criteria needs it. The maintainer can reverse this. | Recorded in the "Not included" list; no check. |
| Affiliate disclosure | A sentence shown on pages that carry affiliate links. | DONE. `affiliate` flag per link, `rel="sponsored"` on those links, and a `disclosure` text in the footer when a page has any (`models.py`, `_footer.html`, `_macros.html`). | v1 (wording needs the maintainer's approval) | Scripted: every link marked affiliate has `rel` containing `sponsored`, and every page with one shows the disclosure. |
| Profile card | Name, topic tags and a media kit button at the bottom of each tab. | DONE (#116, `profile_card.py`, `_profile_card.html`). | v1 | Observable on every tab. The button target is checked by the link check (manifest has it under the reserved id). |
| Follower counts on the socials tab | Count chips for connected platforms. | DONE for platforms with a collector or typed number (#143 platforms block, #154 `counts refresh`, #176 scheduled refresh). | v1 for the platforms Beacons shows; manual numbers with an as-of date for those with no usable API. | Scripted: `counts` output is fresh (as-of within 30 days) for each platform shown. |
| Contact by email | An email entry that is not a mail link. | DONE (#149 email block: spelled-out address, reveal on click, nothing for scrapers in the HTML; `tests/site/test_email.py`). | v1 | Scripted: built HTML contains no `mailto:` and no plain address; click reveals it. |
| Phone layout | Centered column on desktop, full width on phone, tab bar scrolls sideways, icon row wraps. | DONE (#147 responsive layout, #90 tabs fit phone, #179). `tests/site/test_layout.py` and visual tests. | v1 | Observable on a real phone and a desktop browser against Beacons side by side. |
| Print / save as PDF of the link page | Not a Beacons feature. | DONE for print (#147). | Not required. | None. |
| Page titles, 404, robots | Basic. | DONE (#91, #134, #122). | v1 | Scripted: 404 page exists, `robots.txt` and noindex headers match config. |

### Link correctness

| Feature | Beacons behavior | Reach Around on main | Cutover state | How we verify |
|---|---|---|---|---|
| Links match Beacons | The live set of links is the reference. | PARTLY. `reach-around build` writes a link manifest (`--manifest`, `manifest_json` in `src/reach_around/site/build.py`), but it is only an id-to-URL map serialized with sorted keys. It has no page, no order, no affiliate flag, and it omits `tab:` links (icon targets such as the email icon) and tab-bar entries. It cannot prove order, placement, affiliate flags or internal targets. The HTML comparison (a) is built as `reach-around parity` (#340, `docs/parity.md`) for the icon row, link cards, section links and the profile card button, read from an export with `url` and `label`. Still deferred: comparing the tab bar and the support and contact tab entries (the tool counts internal targets but does not match them), the manifest cross-check (b), and the request-every-URL step. | v1 | **Scripted link check, in two parts.** Parity scope: only the header icon row, the tab bar, the links inside sections, the profile card button, and the support and contact links are compared. Ignored: the skip link, the footer privacy link, the optional project credit link, the affiliate disclosure text and any other Reach Around-only anchor. The scope is a fixed list in the script, so a new anchor outside it is not silently compared. (a) Parse the built HTML for each page and collect the ordered anchors (page, section, position, text, `href`, `rel`, whether the page shows the disclosure); this covers order, placement, `rel="sponsored"` and internal `tab:` targets. (b) Use the manifest for the external id-to-URL map as a cross-check. Compare against the private Beacons snapshot: fail on any missing, extra, reordered or changed target (tracking parameters stripped), then request every external URL once. Building the check is a task that must finish before it is relied on as the cutover gate: either write the HTML parser (preferred, no engine change) or extend the manifest with page, order, affiliate flag and every anchor target including internal ones. Output goes in the PR as counts and ids, never URLs. |
| Click tracking | Click counts per link. | DONE. Worker click route and D1 (`worker/src/click.ts`, `worker/migrations/0001_click_events.sql`), kill switch `CLICKS_KILL` (#108). | v1 | Scripted: a test click on a staging host shows in D1 within one minute. |

### Media kit

| Feature | Beacons behavior | Reach Around on main | Cutover state | How we verify |
|---|---|---|---|---|
| Public kit page | Header plus follower counts, visible to anyone. | NOT BUILT. Only the profile card button exists. Plan: `docs/superpowers/plans/2026-10-23-plan-3-media-kit-and-gate.md`. | v1 | Observable: open the kit page logged out on desktop and phone; header and counts show, gated parts do not. |
| Email gate | Visitor enters an email; the page then shows partners, case studies, the rates card, per-platform analytics and audience demographics. | NOT BUILT. | v1 as email plus Turnstile with instant reveal. The emailed verification link is v1.1. | Observable on desktop and phone: gated text is absent from the HTML until the gate passes (view source before and after). Scripted: bad email, missing Turnstile token and over-limit requests are refused. |
| Lead record and alert | Beacons records the visitor email per visit. | NOT BUILT. | v1: store the lead, alert the maintainer in chat within a minute, add a delete command. Email alerts follow when the mail domain is ready. | Scripted: submit a test lead on staging; alert arrives in under 60 seconds; the delete command removes it. |
| Case studies, partner list, rates card | Gated content blocks. | NOT BUILT (no gated block type). Content stays in the private instance. | v1 | Observable after unlock. The public repo build must contain none of it. |
| Per-platform analytics and demographics | Platform numbers and audience breakdown inside the kit. | NOT BUILT. Some platform APIs offer none (see the platform notes in this folder). | v1 for follower counts and typed numbers with as-of dates. Demographics are typed or pasted by hand where no API allows them. | Observable: each figure shows its as-of date. |

### Media kit PDF

| Feature | Beacons behavior | Reach Around on main | Cutover state | How we verify |
|---|---|---|---|---|
| Export | Beacons prints the gate boxes and blurs the gated parts even after unlock, so the maintainer's PDF is unusable. | NOT BUILT. | v1, generated locally by the maintainer, never served from the site. | Scripted and observable: render offline (network disabled), count pages, extract text, and assert no gate placeholder and no blur; check the page breaks by eye; the maintainer reviews the final file. |
| Scrubbing | Not applicable. | NOT BUILT. | v1: the PDF carries only content the maintainer approved for partners. | Scripted: scan the extracted text for private fields marked as not-for-PDF. |

### Analytics

| Feature | Beacons behavior | Reach Around on main | Cutover state | How we verify |
|---|---|---|---|---|
| Page views | Views over time. | DONE. Write-only Worker route and beacon (`worker/src/view.ts`, `src/reach_around/site/static/view.js`, #141, #150), daily rollup (#161), multi-site loader (#235). Not yet enabled on the test page. | v1 (enable on the staging host and collect 48 hours) | Scripted: after a test visit, a row appears; D1 holds no raw address or user agent. |
| Clicks, CTR | Clicks per link and rate against views. | PARTLY. Clicks and views are both stored; the CTR figure needs a report. | v1 | `reach-around report` prints views, clicks and CTR for a date range. |
| Sources | Referrer breakdown. | PARTLY. Referrer host is stored; no report. | v1 | Report shows referrer hosts. |
| Devices | Device breakdown. | PARTLY. Browser, OS and device class are stored (`worker/src/ua.ts`); no report. | v1 | Report shows device classes. |
| Location | Country (and similar) breakdown. | PARTLY. Country, and city by config, are stored from Cloudflare metadata; no report. | v1 | Report shows geography. |
| Bots | Not separated. | DONE as a classifier (`worker/src/botclass.ts`, `docs/research/bot-classifier.md`); reported separately once the report exists. | v1 | Report splits people from bots. |
| CSV export | Download of the data. | NOT BUILT. | v1 as report output the maintainer can save; a file export is allowed to follow. | Observable: the report can be saved as CSV and opens in a spreadsheet. |
| Funnel | Not offered. | PARTLY. Source to page to click is stored; the report is missing. | v1 | Report shows the funnel. |
| Blog page views | Not offered. | PARTLY. The collector accepts several sites (#235). The blog's own wiring is outside this repo. | v1, required by the owner's ruling of 2026-11-06. | Scripted: a test view from the blog lands under the blog's site id. |
| Connected platforms | Seven connected platforms with follower history. | PARTLY. Counts exist for several platforms (#154); history, Instagram, TikTok, X, Threads and LinkedIn depend on API access or typed numbers. | v1 for counts with as-of dates. History for the Tier 1 platforms (Twitch, YouTube, TikTok, Threads, Instagram) is also v1 and blocks v0.3.0; see "Owner requirements of 2026-11-08". Tier 2 history is a bonus. | Each platform shown has a number and an as-of date under 30 days. |
| Privacy page | Beacons policy. | DONE. Generated from enabled features (`src/reach_around/site/privacy.py`). | v1, wording approved by the maintainer. | Observable: page lists every feature that is on. |

## Every Beacons sidebar section

Sections as listed in issue #22 (seen 2026-10-23). The owner ruled on 2026-11-06 ("Accept all as proposed") on every disposition that had been proposed; those rows are marked "Owner ruled 2026-11-06". The table has 22 rows, one per sidebar entry or sub-page.

| Beacons section | What it is | Reach Around state | Disposition |
|---|---|---|---|
| Home | Dashboard landing with summary and shortcuts. | NOT BUILT; a reporting command is planned. | Covered by the v1 report. Owner ruled 2026-11-06. |
| Link in Bio (link page) | The tabbed link page and its editor. | DONE (see Link page); the local editor exists (#198, #241). | Covered, v1. |
| Website | A small site builder. | Out of this tool's purpose; the link page and the maintainer's blog cover it. | Deliberately not included. Owner ruled 2026-11-06. |
| Media Kit | Public kit page, gated detail, PDF export. | NOT BUILT (see Media kit). | Covered, v1. The kit-viewed alert is also v1 (chat first, email when the mail domain is ready). |
| Brand Deal Profile | A profile of the creator's brand for sponsors, part of the kit. | NOT BUILT; it would be a section of the kit page. | Covered, v1, as part of the kit content. Owner ruled 2026-11-06. |
| Smart Reply | Automated replies to social messages and comments. | NOT BUILT. Needs write access to platform accounts. | Deliberately not included. Owner ruled 2026-11-06. |
| Affiliate Products | A catalog and tracking of affiliate offers. | PARTLY. Affiliate links with `rel="sponsored"` and a disclosure are DONE; clicks are counted; no earnings tracking. | Links covered, v1. Earnings tracking is later (see issue #5). Owner ruled 2026-11-06. |
| Digital Products | Selling files and courses. | NOT BUILT. | Deliberately not included; external stores cover it. Owner ruled 2026-11-06. |
| Marketing | Email and campaign tools. | NOT BUILT. | Deliberately not included. Owner ruled 2026-11-06. |
| Analytics: socials | Follower counts and history per platform. | PARTLY (see Analytics). | Counts and Tier 1 history in v1 (owner, 2026-11-08, supersedes "history later"). Tier 2 history is a bonus. |
| Analytics: link in bio | Views, clicks, CTR, sources, devices, location, CSV. | PARTLY (see Analytics). | Covered, v1. |
| Analytics: website | Visits to the Beacons-hosted website. | Page views support more than one site (#235). | Covered for the maintainer's own sites; blog collector is v1. |
| Analytics: digital products | Sales analytics. | NOT BUILT. | Out of scope with Digital Products. Owner ruled 2026-11-06. |
| Analytics: affiliate products | Clicks and earnings per offer. | PARTLY. Clicks only. | Clicks in v1; earnings later. Owner ruled 2026-11-06. |
| Analytics: media kit | Kit views and visitors. | NOT BUILT. | Covered, v1, as part of the kit work and report. |
| Sales and Payouts | Revenue from Beacons products and payout settings. | NOT BUILT. | Out of scope; no products are sold here. Income tracking is a later idea (issue #5). Owner ruled 2026-11-06. |
| Audience: members | A contact list. | Gate leads only (planned, v1). | Leads covered, v1; a general list is not included. Owner ruled 2026-11-06. |
| Audience: groups | Segments of contacts. | NOT BUILT. | Deliberately not included. Owner ruled 2026-11-06. |
| Audience: memberships | Paid memberships. | NOT BUILT. Direct and platform memberships appear as links (`docs/research/support-embeds.md`). | Links only. Owner ruled 2026-11-06. |
| Audience: forms | Sign-up and contact forms. | NOT BUILT; the kit gate is the only form planned. | Deliberately not included beyond the gate. Owner ruled 2026-11-06. |
| Business Tools and invoice generator | Invoices and small business helpers. | NOT BUILT. | Out of scope: Zoho Books handles invoicing (earlier ruling, issue #3). |
| Referrals | Beacons' own referral program. | n/a | Out of scope. Owner ruled 2026-11-06. |
| Settings | Account, domain, connected platforms, billing. | PARTLY. Site config and the local editor replace page settings; connected platforms are collector config. | Covered by config files. Account and billing are not applicable. |

## Beacons weaknesses worth beating

- The PDF export prints the gate and blurs gated content even after unlock; the target is a complete, paginated, offline PDF.
- Platforms Beacons does not connect (for example Bluesky, Mastodon, Kick, Discord and Matrix community sizes) can be added.
- Visitor data stays in the maintainer's own store, with no raw address or user agent kept.

## Owner requirements of 2026-11-08

These amend the table above. Where a row above says "history later" or "deferred", this section wins. Each item is a checkbox with an owner, a way to verify it and the issue that delivers it. Tier 1 items are blockers for v0.3.0 (#274): the tag is not cut while one is open. Epic: #246; platform tiers: #364.

Tiers (owner's order of priority):

- **Tier 1, cutover-critical:** Twitch, YouTube, TikTok, Threads, Instagram. Their numbers must match what Beacons shows.
- **Tier 2, bonus:** Bluesky and Mastodon first, then Kick, Discord and GitHub. None blocks cutover.
- **LinkedIn:** the existing hand-kept follower count only. No collector.
- **Later:** PeerTube. Not on the cutover path.

Rules that apply to every item: all times are stored and reported in UTC; no paid API; no visitor identifier is stored or reported.

### Requirement A: analytics from the owner's socials

- [ ] **A1. Tier 1 history store exists** (BLOCKER for v0.3.0). Owner: maintainer, built by the agents. Verify: after two daily refreshes, the store holds one dated row per Tier 1 platform per day, in UTC, appended and never rewritten. Delivered by #383 (store), gated by the research issues #380, #382, #385.
- [ ] **A2. Tier 1 collectors run** (BLOCKER). Owner: maintainer. Verify: each of Twitch, YouTube, TikTok, Threads and Instagram has a followers value with an `as_of` day within the last 30 days in the report; a platform that cannot be collected says so by name instead of showing zero. Delivered by #387, #388, #390 and the views collectors #391, #392, #393, #394, #395; hand-kept hygiene for numbers typed by hand is #389.
- [ ] **A3. Tier 1 numbers match Beacons** (BLOCKER). Owner: maintainer, with Beacons still live. Verify: `scripts/check-analytics.py` over the 48 hour window and again at day 30, exit 0, result recorded in the cutover pull request (`docs/analytics-acceptance.md`). Delivered by #378 (the script) and #376 (the importer that maps the export).
- [ ] **A4. Views, watch time and demographics shown with privacy floors** (BLOCKER for the Tier 1 platforms that offer them). Owner: maintainer. Verify: the kit and `reach-around report` show them and print nothing under the floor. Delivered by #384 and #397.
- [ ] **A5. Tier 2 platforms** (bonus, not a blocker). Verify: Bluesky and Mastodon first, then Kick, Discord, GitHub each show a followers value with an `as_of` day. Delivered by #396, #381, #386.
- [ ] **A6. LinkedIn stays hand-kept** (not a blocker). Verify: the follower count shows with its `as_of` day, and no LinkedIn collector exists. Delivered by existing hand-kept counts (#258).
- [ ] **A7. PeerTube** is tracked as a backlog item and is not scheduled (not a blocker). Verify: the backlog issue exists and is linked from #364.

### Requirement B: funnel traffic

- [ ] **B1. Source to destination is reported** (BLOCKER). Owner: maintainer. Verify: after a synthetic visit from a labeled source and a click on an outbound link on staging, the report shows that source against that destination ("came from Instagram, went to Twitch") within the hour. Delivered by #329 (source on clicks), #409 (source by destination table) and #398 (one unified report).
- [ ] **B2. Aggregate only, no identifiers** (BLOCKER). Verify: a database dump passes `scripts/check-dump.py`; the report prints no row under the floor; no cookie is set (`scripts/smoke.sh`). Delivered by #408 (do-not-collect list and the test) and #331 (rollups with caps and retention).
- [ ] **B3. Synthetic visit and click appear in the report** (BLOCKER). Verify: the step in `docs/analytics-acceptance.md`. Delivered by #378 with #366 (analytics enabled on staging, 48 hour proof).

### Requirement C: the cutover date and the Beacons history

- [ ] **C1. The kit shows "Live since <date> (UTC)"** (BLOCKER). Verify: with `cutover_date` set, the built kit page and the PDF print the line; `reach-around check --production` has no unset warning. Delivered by #374 (PR #404, merged).
- [ ] **C2. Every public number carries a before or after mark** (BLOCKER). Verify: a number computed from both eras is labeled as such in the kit and the report. Delivered by #375, with the report and Grafana marker in #371.
- [ ] **C3. Beacons history sits in its own labeled block** (BLOCKER). Verify: the kit shows pre-cutover link-page history in a separate block labeled as imported from the old provider; it is never summed into collected numbers. Delivered by #377 (kit block) and #376 (importer with provenance, kept apart from collected data).

### Handling the old provider's data

- [ ] **D1. Export in week 1.** Owner: maintainer. Verify: the export file exists in the private instance repository (never this one) before the end of the first week after cutover. Tracked by #347.
- [ ] **D2. Import by day 30.** Owner: maintainer. Verify: the importer (#376) has run and `scripts/check-analytics.py` passes on the imported window.
- [ ] **D3. Cancel the old provider only after verification.** Owner: maintainer. Verify: A3, C3, D1 and D2 are all checked, with the day-30 comparison recorded in the cutover pull request. Cancelling earlier is a rollback hazard (see the rollback plan).
- [ ] **D4. No paid APIs.** Verify: every collector uses a free tier or a public endpoint; the research issues #380 and #382 record the terms. A platform that needs a paid API is reported as not collected, and the owner decides.

## Cutover checklist

All seven must hold, with Beacons still live. The items under "Owner requirements of 2026-11-08" are part of items 1 and 5.

1. **Parity doc is green.** Every v1 row above has been checked and its result recorded in the cutover pull request.
2. **The page equals Beacons.** The scripted link check passes: no missing, extra, reordered or changed link, affiliate flags match, every URL answers. Desktop and phone look-alike review done by the maintainer.
3. **The kit gate works on desktop and phone.** Gated text is absent until unlock. A test lead raises an alert in under a minute. The delete command removes the lead.
4. **The PDF is complete.** Paginated, readable offline, scrubbed, and reviewed by the maintainer.
5. **Analytics are real.** 48 hours of click and page-view data on the staging host. The report shows funnel, geography and bots. A dump of the database contains no raw address and no user agent. Tier 1 numbers match Beacons within the tolerance (A3), and a source-to-destination pair shows in the report (B1). Items A1 to A4, B1 to B3 and C1 to C3 above are blockers for `v0.3.0` (#274).
6. **Safety and release.** Kill switch tested. Security review findings closed. Privacy page approved. `v0.3.0` tagged.
7. **The old path redirects.** `/socials` and `/socials/` redirect to `/` with the query string kept, other paths unaffected (#356). Checked on the staging host with `status: 302`, then switched to `301` for the cutover build. See `docs/deploy.md`, "Redirecting the old /socials path".

## Rollback plan

- Beacons stays live for at least 30 days after cutover. Nothing on the Beacons side is deleted in that window.
- Stage on a spare hostname first. The final step is one DNS change.
- Before the change: write down the current record and its TTL. Lower the TTL to 300 seconds two days ahead.
- To roll back: restore the written-down record. With the short TTL, visitors return to Beacons within minutes.
- If only the kit or analytics misbehaves: use the kill switches (`CLICKS_KILL`, `VIEWS_KILL`) and leave the static page up.
- Leads collected during the window are kept and exportable; rolling back does not delete them.

## Deliberately not included

Owner rulings, 2026-11-06 unless noted:

- **Emailed unlock link is v1.1.** v1 reveals after email plus Turnstile. The gap (the address is not proven to belong to the visitor) goes in the threat model.
- **Network-type dataset later.** VPN, proxy and hosting detection from local IP-range data (`ip-network-datasets.md`) is not needed for cutover. The ruling on this item is still open and this line records the current plan.
- **Tier 2 collectors and the dashboard later.** Grafana, an executive summary, Social Blade, StreamElements alerts and the Tier 2 platform collectors (Bluesky, Mastodon, Kick, Discord, GitHub) are not part of cutover. Tier 1 platform history is part of cutover (owner, 2026-11-08, which replaces the earlier ruling that all automated history was deferred). LinkedIn stays a hand-kept follower count. PeerTube is later.
- **Blog collector is required.** Blog page views must flow into the same collector before cutover (owner ruling, 2026-11-06). This is in v1, not deferred.
- **Per-link menu is not copied.** See the table; reversible on request.
- **Anything not in the tables above** is out of scope for cutover.
