<!-- SPDX-License-Identifier: Apache-2.0 -->
# Compared with Beacons.AI

Draft for owner review. Every line marked `[OWNER INPUT NEEDED: confirm or replace]` is opinion or personal history that only the maintainer can state. Nothing marked that way is a fact claim, and the marked lines are placeholders until he edits them.

## Read this first

This page is the maintainer's perspective, not a neutral review. Reach Around is not affiliated with, endorsed by or sponsored by Beacons.AI. The name is used as plain text to say which service the comparison is about. Beacons.AI is a trademark of its owner.

Last checked: 2026-11-24. The other service may have changed since. If something here is out of date or wrong, open an issue in the repository and it will be fixed or removed.

## How the sources are labeled

- VERIFIED (public): read on a public page, with the URL and the date read.
- VERIFIED (repo): the file or pull request named exists on `origin/main` at commit `6d4c646`, read on 2026-11-20 (see `docs/research/beacons-parity.md`). This covers what Reach Around does, not what Beacons.AI does.
- OWNER OBSERVATION: what the maintainer saw in his own logged-in Beacons.AI account on 2026-10-23, as recorded in the project's parity research. It is one person's view of one account, not a public source, and it may differ for other plans or dates.
- INFERRED: not sourced. None of these appear in the table; any such sentence is marked in the text.

Public Beacons.AI pages read for this page: `https://beacons.ai/i/pricing`, read 2026-11-24. It lists the plan names Free, Creator, Creator Plus and Creator Max, and the feature names Link in Bio, Smart Reply, Media Kit, Digital products (Store) and Analytics. Nothing else from that page is used here: no prices, limits or plan contents. Those were not recorded, and they change.

## The short version

Beacons.AI is a hosted service for link pages, media kits and creator tools. VERIFIED (public, 2026-11-24, feature names above). Reach Around is open source software you run yourself, on your own accounts, with your own API keys. VERIFIED (repo, README). They overlap on link pages, media kits and analytics. They differ on who runs it and who controls it.

## Why the maintainer built it

Beacons.AI worked for the maintainer for years. [OWNER INPUT NEEDED: confirm or replace]

He built Reach Around because he wanted control over the design of his page and kit. [OWNER INPUT NEEDED: confirm or replace]

He found the media kit did not do what he needed, and features he asked for did not arrive. [OWNER INPUT NEEDED: confirm or replace]

Dates, where he asked, and what answer he got: [OWNER INPUT NEEDED: confirm or replace]

The repository states the goal as replacing Beacons.AI for creators, close to 1:1, and then going further (README). VERIFIED (repo). Whether that goal is a good one for you is your call.

## Feature parity

Status words: Done means built on main. Partly means some of it is built. Not built means no code on main. Not included means a deliberate decision by the maintainer (ruled 2026-11-06 unless noted). Planned means a named open item.

The Beacons.AI column is OWNER OBSERVATION unless it says VERIFIED (public). The Reach Around column and the evidence are VERIFIED (repo).

### What Reach Around does today

| Feature | Beacons.AI (source) | Reach Around today | Evidence |
|---|---|---|---|
| Link in Bio page | Offered. VERIFIED (public, pricing page, 2026-11-24) | Done: tabs layout, single-page option, link cards, profile card | `docs/research/beacons-parity.md`, Link page |
| Media kit | Offered. VERIFIED (public, pricing page, 2026-11-24) | Done: public kit page, email gate with a bot check, lead record and alert, case studies and rates blocks | `docs/research/beacons-parity.md`, Media kit; `docs/kit.md` |
| Media kit PDF | Offered. OWNER OBSERVATION | Done: generated locally; public half only on request | `docs/research/beacons-parity.md`, Media kit PDF |
| Analytics | Offered. VERIFIED (public, pricing page, 2026-11-24) | Done for page views, clicks, sources, devices, location, bots, funnel, CSV export | `docs/research/beacons-parity.md`, Analytics; `docs/report.md` |
| Social follower counts | Shown on the page. OWNER OBSERVATION | Partly: collectors for some platforms, typed numbers with an as-of date for the rest | `docs/research/beacons-parity.md`, Analytics |
| Affiliate links | Offered as Affiliate Products. OWNER OBSERVATION | Partly: affiliate flag, sponsored link attribute, disclosure text, click counts; no earnings tracking | `docs/research/beacons-parity.md`, section parity table |
| Link parity check for migrating | Not applicable | Done: `reach-around parity` compares a built site with an exported link list | `docs/parity.md` |

### What is planned or not finished

| Feature | Beacons.AI (source) | Reach Around | Evidence |
|---|---|---|---|
| Twitch viewer tiles | Not recorded | Not built; priority is an open owner decision | `docs/research/beacons-parity.md`, Media kit |
| Share and copy-link control, deeper-kit request flow | Not offered. OWNER OBSERVATION | Not built; not needed for parity | same |
| Emailed verification link on the kit gate | Not recorded | Planned for v1.1 | same |
| Instagram and Twitch channel analytics | Not recorded | Partly: some collectors merged, others open | same |
| Earnings tracking for affiliate links | Not recorded | Planned, later | same |
| Per-link menu | Offered. OWNER OBSERVATION | Not built; reversible on request | `docs/research/beacons-parity.md`, Link page |

### What Reach Around deliberately does not do

| Feature | Beacons.AI (source) | Reach Around | Evidence |
|---|---|---|---|
| Smart Reply | Offered. VERIFIED (public, pricing page, 2026-11-24) | Not included: it needs write access to platform accounts | `docs/research/beacons-parity.md`, section list |
| Digital products and store | Offered. VERIFIED (public, pricing page, 2026-11-24) | Not included: external stores cover it | same |
| Sales and payouts | Offered. OWNER OBSERVATION | Not included: nothing is sold here | same |
| Marketing tools | Offered. OWNER OBSERVATION | Not included | same |
| Website builder | Offered. OWNER OBSERVATION | Not included: out of this tool's purpose | same |
| Audience groups, general member lists, forms beyond the gate | Offered. OWNER OBSERVATION | Not included; gate leads only | same |
| Business tools and invoicing | Offered. OWNER OBSERVATION | Not included: out of scope | same |
| Hosted account, billing and support | Offered as a service. INFERRED from it being a hosted product | Not applicable: you run your own copy | `docs/wiki/Costs.md` |

## Where Beacons.AI is the better fit

If you want a page running in minutes with no setup, no Cloudflare account and no API keys to manage, a hosted service is simpler. If you sell digital products, use Smart Reply or want payouts handled for you, Reach Around does not do those things and does not plan to. If you do not want to be responsible for your own deployment, updates and secrets, that is a real cost of self-hosting. See [Costs](Costs) for what running it involves. Whether the trade is worth it depends on you. [OWNER INPUT NEEDED: confirm or replace]

## Where the maintainer wanted something different

Design control: [OWNER INPUT NEEDED: confirm or replace]

Media kit needs: [OWNER INPUT NEEDED: confirm or replace]

Requested features and the response: [OWNER INPUT NEEDED: confirm or replace]

Reach Around keeps the page as files you own and can edit, and the data in your own accounts. VERIFIED (repo, `docs/customizing.md`, `docs/instance-repos.md`).

## What this page does not claim

It makes no claim about Beacons.AI as a company: its business, security, reliability, support or motives. It compares features and architecture only.

## Corrections

Send corrections as an issue in the repository. A claim that cannot be sourced is removed. The date at the top is updated whenever the table is rechecked.
