<!-- SPDX-License-Identifier: Apache-2.0 -->
# Roadmap

This page says what Reach Around has, what is being drafted, what is planned and what is deliberately left out. It is derived from the [changelog](../../CHANGELOG.md) and the open issues, and every line names its source. Nothing here is a promise or a date.

How to read the words:

- **Released** means the changelog lists it under a version that was published. Reach Around is alpha: every release is `0.x` and anything can change between them (changelog header).
- **Pre-release** means it is in a `0.3.0-rc.N` candidate. A candidate is an image for previews and staging proof, not the frozen soak candidate (changelog, `0.3.0-rc.6`, #595).
- **Merged, unreleased** means it is on `main` as a changelog fragment and in no tag yet.
- **Draft** means an open draft pull request exists. It may change or be dropped.
- **Planned** means an open issue and nothing built yet.

Last checked against the changelog and open issues on 2026-11-24. If you see a mismatch, open an issue.

## What exists

### Released versions

The changelog lists these as published: `0.1.0` (2026-10-24), `0.2.0` (2026-10-25) and `0.2.2` (2026-11-06). `0.2.1` was tagged but never released because its image scan failed; use `0.2.2` (changelog, `0.2.2` and `0.2.1`).

- A static link page builder with tabs, a strict config loader, a generated privacy page, click counting by `ping` with a beacon fallback, a loopback-only live preview, and a container image with a release workflow (changelog, `0.1.0`).
- `reach-around check` for config validation, a `platforms` block, an `email` block that never puts the address in the page, a 404 page, content-hashed asset names (#155), a footer credit that can be turned off, and `counts refresh` for follower counts (changelog, `0.2.0`).
- Optional page-view counting and a daily rollup in the Worker (changelog, `0.2.0`).
- A single-page `sections` layout (#220) and a local editor with forms, undo and redo (#172, #173, #188, #189, #190, #191), plus a daily counts refresh workflow in the instance template (changelog, `0.2.1`).

### Pre-release: the 0.3.0 candidates

These are in `0.3.0-rc.2` through `0.3.0-rc.6`. None is a stable release (changelog, `0.3.0-rc.6`, #595).

- Link page: the smart stack layout (#573), group styles (#672), live badges on Watch links (#673), spoke pages (#676), a support page built by the engine (#683), optional border frames (#444, #448, #467, #469) and old tab and `/socials` redirects (#341, #356).
- Media kit: a public page and a gated page behind a gate form, a private PDF and a public-half PDF, structured demographics, case-study cards, recent posts, a business inquiry form, a trust label on every kit number, and a first screen redesign (#256, #267, #425, #547, #550, #548, #492, #626, #684).
- Numbers: collectors for Bluesky, Mastodon-compatible servers, GitHub, Discord, Matrix and YouTube (changelog, `0.2.0`), plus Twitch, TikTok, Instagram and Threads follower sources (#385, #388, #387), an append-only social-stats history store (#383), a token health report (#390), and a token sink for unattended refresh (#464).
- Reports: `reach-around report` with funnel, geography, devices, a dark owner page, a unified history report, privacy floors for small cells, a Grafana local-store export and a source-attributed click funnel (#262, #368, #369, #398, #397, #335, #329, #331).
- Safety and operations: kill switches, analytics health alerts, backups with a restore drill, a deploy smoke check and a dump check (#401, #402, #400, #403, #272). The click and beacon routes honor Global Privacy Control and Do Not Track (#599).
- Deployment: a reusable Worker deploy workflow (#596) and the instance repo model with a pinned engine release (see [instance-repos.md](../instance-repos.md)).

### Merged, unreleased

These fragments sit in `changelog.d/` and are in no tag yet:

- Warning before a platform's retention window ends (#620) and stricter history record checks (#624, #651).
- Kit audience summary as a range (#627), the kit inquiry route ahead of the URL cap (#649) and the gate claim rows in the D1 write model (#650).
- Group-level analytics rollup in the report (#678) and a smaller `live.js` (#699).
- Support page QR codes made at build time (#713) and TikTok source docs and a sandbox test (#714).
- Imported history export errors (#659), instance template config check fix (#654) and Grafana dashboards as code (#727).

## What is in draft

Each item has an open draft pull request. None is merged.

- Storage architecture design for the cloud side: Durable Object counters, Analytics Engine events, KV stats and D1 snapshots (#753, draft PR #755). The issue says nothing is live yet, so the foundation can change before production.
- Privacy and data-protection baseline (epic #776): region by default for page views, with city only in a floored daily counter (#778, PR #793); a fixed privacy page block and gate form notice (#777, #780, PR #797); lead access and erasure tooling (#781, PR #786); masked alert emails by default (#779, PR #785); an imports guard against person-level fields (#784, PR #787).
- Kit v2: measure model and compile shim (#628, PR #739), the live block with Twitch and Kick side by side (#629, PR #742), the fediverse block (#630, PR #744), more measures (#633, PR #745), the owner-only measures file (#632, PR #747) and hand-kept entries (#631, PR #756).
- Kit visual work: PDF renders dark (PR #750), the "Consulting instead" line (#686, PR #751), trust legend page break fix (PR #752) and a property bar test (PR #748).
- Link page: the deck arrangement for the smart stack (#677, PR #720) and support QR on the brand tile (PR #746).
- Deployment: the instance template calling the reusable Worker deploy (#694, PR #735), edge settings as code with a plan-only OpenTofu module (#613, PR #734) and a spec for a labeled history rewrite (#619, PR #733).
- Hardening: a set of draft pull requests is open under epic #757. Hardening is tracked there; this page does not list the items.
- Documentation: the wiki itself (epic #795), including the costs page (PR #821), the operations entry page (PR #828), the architecture pages (PR #827) and the deploy pages (PR #829).
- CI: running the Python test legs in parallel (#826, PR #831).

## What is planned

Each item is an open issue with no pull request yet, unless noted.

- **Before production:** a whole-system review of confidentiality, integrity and availability before the production MVP (#754); records of processing, a data protection impact screening note and a breach procedure (#782); a switchable bot check for the beacon, Turnstile or proof of work, named on the privacy page (#783); leads in their own D1 database (#772).
- **Platform numbers:** a fediverse collector design for public post counts (#743); Facebook counts and insights, targeted for the week after Thanksgiving (#711); TikTok Display API counts with refresh-token rotation (#714, the source docs part is merged); a human read of the Twitch and Kick developer terms before any API data beyond public follower counts (#625); live measure sampling (#635).
- **Kit v2:** deprecating the old kit keys (#634), a direction tokens and property bar (#685), a QR treatment (#688), a PDF theme (#687) and a fix for the kit PDF trust legend check under a real Chromium render (#749).
- **Link page and main site:** hub content cleanup (#669), property bar and vanity redirects (#680), avatar and art (#682), switching the instance to the deck (#679) and owner operations for the redirect and email obfuscation (#681).
- **Sponsor tooling (epic #636):** rate card schema (#637), quote documents (#638), invoice PDF (#639), a local pipeline with reminders (#640), a price table on the sponsor page (#641), Worker quote routes (#642), optional Discord and Matrix deal alerts (#643), template-mode outreach drafts (#644), an optional send function behind per-message confirmation (#645), an AI foundation with a ledger, caps and a kill switch (#646), AI providers local first (#647) and docs (#648).
- **Health and reporting:** a token health step in the instance workflow with a per-source history and a repeated-failure alert (#662); Grafana export store v5 (#732); a refreshed parity checklist (#655).
- **Documentation:** the remaining wiki pages under epic #795 (#799 to #818).
- **Project tooling, not part of the product:** a remote runner for heavy test and build work (#794) and the agent box work (#819, #822 to #825, #830).

## What is not planned

These are recorded owner rulings from the parity record. They can be reversed; a ruling is not a law (`docs/research/beacons-parity.md`, "Deliberately not included").

- A website builder, automated replies to social messages and comments, digital product sales, email and campaign marketing tools, audience groups and a general contact list, and sign-up forms beyond the media kit gate and the business inquiry form (owner ruled 2026-11-06).
- A per-link menu (parity record: deliberately not included in v1).
- For v1 specifically, the emailed unlock link for the media kit gate, which is deferred to a later version, and network-type analytics (VPN, hosting and similar classes) in the report (cutover slice plan, non-goals, `docs/superpowers/plans/2026-11-06-cutover-slice.md`).
- Collecting any identifier beyond the daily hash, per-visit sessions and any third-party analytics (analytics plan 4, out of scope; see also [audience-analysis-privacy.md](../audience-analysis-privacy.md)).

## Where to look next

- The full change list: [CHANGELOG.md](../../CHANGELOG.md).
- Open work: the repository's issue list and the project board.
- Running it: [Operations.md](Operations.md) (the operations entry page, added in PR #828) and [Costs.md](Costs.md) (the costs page, added in PR #821). Both links resolve once those pull requests merge.
- Something broken: [Troubleshooting.md](Troubleshooting.md).
- Common questions: [FAQ.md](FAQ.md).
