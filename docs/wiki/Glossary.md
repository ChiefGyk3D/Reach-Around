<!-- SPDX-License-Identifier: Apache-2.0 -->
# Glossary

Plain definitions of the terms you meet when you build, run or contribute to Reach Around. Reach Around is pre-release: nothing here describes a live service, and a term marked "planned" names something that is not on the main branch yet. For how the pieces fit together, read [Concepts](Concepts).

<!-- TODO-verify: confirm whether a v0.1.0 release has been tagged; the README still says the first release is pending. -->

## The page and the kit

**Link page.** The public page a visitor opens from your bio. It is built ahead of time as plain static files: a header, one or more tabs, and the blocks inside them.

**Tab.** One page of the link page, such as Home or Support. A site has one or more tabs, and each tab holds a list of blocks. Tabs are set in the config file.

**Block.** One item on a tab. The block types today are link, section (a group of links), text, image, crypto (addresses to copy), email (a contact address kept off the page), platforms (handles with counts), live (stream status) and divider. Every block has an `id` that must be unique across the site. The field list is in the config reference, `docs/config.md`.

**Kit (media kit).** A pair of pages for brands and partners, with matching PDFs. The public page shows a name, a short description and headline numbers. The gated page holds the detail you only want to show people who pass the gate. See [Concepts](Concepts#the-media-kit-and-its-gate).

**Media kit gate.** The short form a visitor completes to see the gated kit page: an email address and a bot check. Passing it unlocks the gated page in that browser. The gate is off unless you turn it on in the config.

**Lead.** The record the gate keeps when someone submits it: the email address as typed (lowercased), a two-letter country code, timestamps and the kit events tied to that lead. It proves only that someone typed the address; the address is not verified. Leads are deleted after a retention period, 30 days by default and never more than 365.

**Measure.** One value with a unit, a source and a date, such as a follower count from one platform on one day. Planned: the kit v2 work generalizes the kit's separate number types into one measure model. It is in open draft pull requests, not on the main branch.

**Trust label.** The short tag printed next to every number in the kit. There are exactly three: API (read from the platform's API), Hand-kept (typed by the owner or imported) and Estimate (the owner marked it as an estimate). The label is decided in code at build time, never in a template.

**Floor (privacy floor).** The rule that a count of 1 to 4 is never printed. It prints "Under 5" instead. Zero prints as zero and 5 prints as 5. The floor protects small groups from being singled out in a published number.

**Public PDF and private PDF.** Two exports of the kit. The public PDF carries only what the public page may show. The private PDF is the complete version for the owner's own offline use.

## The wall and its levels

**The wall.** The structural boundary between what is public and what is gated. The public kit page is built from a public projection of the config and the gated page from a gated projection, written to two separate directories, so a template cannot reach a gated field. The build also searches every public file for gated text and stops if it finds any. See [Concepts](Concepts#what-the-wall-guarantees).

**Public.** Visible to anyone who opens the site, including the public kit page and the public PDF.

**Gated.** Visible only to a visitor who passed the kit gate. This includes partners, case studies, rates and testimonials. The gated page is kept apart from the public output.

**Private.** Never published. It stays in your config or on your machine, such as the private PDF, the owner report and settings that only control the build.

## Numbers and history

**Collector.** A scheduled job that reads follower or performance counts from one platform's own API with your credentials and appends them to your numbers file and history. Collectors are optional, and a platform without one can be kept by hand.

**Numbers file.** Your own file of current counts per platform. The site build reads it to print counts on the link page and in the kit. A refresh writes to it; you can also edit it by hand.

**Snapshot.** A reading at an instant, such as a follower count on one day. A day's snapshot freezes when the day closes, and it is never rewritten to look better.

**History store.** The append-only file of dated readings that the collectors and your own entries write to. A missing stretch is stored as a gap, never as zero.

**Era marker (era).** A label on records either side of a break in what a number means, such as a platform changing how it counts subscribers. It stops a reader from taking the jump for growth. You add one with `--era` when you type a value or mark a fetched record with a correction.

**Cutover.** The day you switch your real bio link from the old provider to Reach Around. The config can carry a `cutover_date` (a UTC calendar day), which the kit shows as "Live since". The `docs/cutover-runbook.md` file walks through the switch.

**Parity.** Whether the new page has every link the old page had. `reach-around parity` compares a built site against a link list exported from the old page and reports what is missing, extra or changed. It reads local files and makes no network call.

**Rollup.** The daily summary of page views and clicks. Raw events are folded into per-day totals and then deleted after a short keep window.

**Beacon.** The small request a page sends so the Worker can count a page view. The route stores no IP address, user agent or referrer URL. Measurement has limits and a configurable bot check.

**Bot check.** A configurable check that separates likely-human from automated traffic on the gate and on measurement. Which check runs is a setting.

**GPC and DNT.** Global Privacy Control and Do Not Track, two browser signals meaning "do not track me". The Worker honors both: a request that carries either one records nothing.

## Staying up

**Kill switch.** A Worker setting that turns a route off at once, for an incident, a flood or a bill you do not want. A switched-off route answers with an empty success and writes nothing. See [Concepts](Concepts#how-it-stays-up).

**Budget.** A cap on how much work a route may do in an hour or a day. Over the cap, requests are dropped, not queued, and the stretch shows as a gap in reports.

**Gap.** A stretch with no collected data, reported with a reason. Reach Around prints a gap, not a zero, because a zero would claim that nothing happened.

## Where the code lives

**Engine.** The public Reach Around repository (github.com/ChiefGyk3D/Reach-Around): the config loader, the static builder, the Worker and the command line tools. It is versioned and released as a container image.

**Instance repo.** Your own private repository: your `site.yaml`, images and the workflow that deploys them. It holds no engine code and only says which engine release to use. See `docs/instance-repos.md`.

**Worker.** A Cloudflare Worker, a small program that runs at Cloudflare's edge. Reach Around uses one for the media kit gate, click and view counting, lead retention and the live status route. The rest of the site is static files.

## Cloudflare services

**D1.** Cloudflare's SQLite-based database. The Worker keeps leads, daily rollups and related bookkeeping in it. The storage layout is being redesigned; see [Concepts](Concepts#what-runs-at-build-time-and-what-runs-at-the-edge).

**KV.** Cloudflare's key-value store. On the main branch the gated kit page is stored in KV, and the live status route can use it.

**Durable Object.** A Cloudflare object with its own small piece of state and one place where it runs. Planned: the storage design uses Durable Objects to buffer events before they are written. This is in an open draft design and is not on the main branch.

**Analytics Engine.** Cloudflare's store for counters written from a Worker. Planned: it is under consideration for daily floored counters only. It is not on the main branch.

**R2.** Cloudflare's object storage. <!-- TODO-verify: no R2 binding appears in worker/wrangler.toml on main; confirm whether the project will use R2 at all. --> Reach Around does not use it on the main branch.

## Planned pages

Deeper pages are planned for the terms above: Architecture, Operations, Deploy, Costs and Security and privacy. They are listed in the [sidebar](_Sidebar) and are not written yet.
