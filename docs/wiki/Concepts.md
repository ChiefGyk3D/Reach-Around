<!-- SPDX-License-Identifier: Apache-2.0 -->
# Concepts

How the pieces of Reach Around fit together, in plain language. Terms in bold are defined in the [Glossary](Glossary).

Status: Reach Around is pre-release. The code is public and under the Apache-2.0 license, but nothing is deployed as a service, there is no stable configuration format and no upgrade path between `0.x` releases. Where a feature is only in an open draft pull request, this page calls it planned.

<!-- TODO-verify: confirm whether a v0.1.0 container image has been published; the README says it will be once released. -->

## What Reach Around is

Reach Around is an open-source tool for creators who want to own their link page, media kit and audience numbers. You describe your page in a config file. A builder turns the config into static files. A small Cloudflare Worker handles the few things that need a server: the media kit gate, click and view counting, lead retention and live status. You run it yourself with your own platform credentials. There is no hosted version.

Its goal is to cover what a hosted link-in-bio service does for a creator, and then add a media kit that stays current and unified numbers across platforms. Which features exist today is tracked on the status page (planned) and in the changelog.

## The instance model

Two repositories are involved.

- The **engine** is the public repository at github.com/ChiefGyk3D/Reach-Around. It is versioned and released as a container image.
- Your **instance repo** is private. It holds your `site.yaml`, images, overrides and the workflow that deploys them. It contains no engine code; it pins one engine release.

Updating means changing the pinned release in your instance repo and letting its workflow rebuild. The model is described in `docs/instance-repos.md`.

## What runs at build time and what runs at the edge

At build time, the builder reads your config and your numbers file and writes static files: the link page, its tabs, the public kit page and both PDFs. The gated kit page is written to a separate directory. The build fails on a bad config, on a gated value found in a public file, and on a number that breaks the privacy floor. The builder runs in a container, rootless, with no network access needed.

At the edge, the visitor gets static files and nothing else, unless a feature needs the Worker. The Worker:

- authorizes the media kit gate and serves the gated page from KV,
- counts clicks and page views and rolls them up into daily totals,
- deletes leads and raw events after their retention period,
- answers the live status route, and
- sends owner alerts when something needs attention.

On the main branch the Worker keeps its state in D1 and KV. The storage design is being reworked: a draft design proposes buffering events in Durable Objects and keeping daily floored counters in Analytics Engine. That work is planned, not merged. <!-- TODO-verify: confirm the storage design (Durable Objects, Analytics Engine, two D1 databases) against the merged code before treating it as present. -->

Deeper pages on each service are planned under Architecture; see the [sidebar](_Sidebar).

## What is collected and what is never collected

Collection is decided by the config. Turn a feature off and what it collects disappears from the generated privacy page. With every feature off, the site collects nothing.

When on, the Worker collects:

- Click counts per link, with the visitor's country. No IP address is stored.
- Page views, bucketed by site and page, with coarse categories such as referrer host, device class and geography. No IP address, user agent or referrer URL is stored. A signal from GPC or DNT records nothing.
- Leads, only if the media kit gate is on: the typed email address, a country code, timestamps and the kit events tied to the lead. Leads are kept for 30 days by default and never more than 365.

Never collected: IP addresses, user agents, referrer URLs, cookies for tracking, fingerprints, or any field that identifies a person in a view or click row. The gate stores an address the visitor typed, which is not verified. Emailed verification is planned.

Raw events are short-lived. They are folded into daily totals and the raw rows are deleted after a keep window, 7 days by default on the main branch. <!-- TODO-verify: the planned storage design changes raw-row precision (hour-rounded timestamps, region only) and retention; update this paragraph when that merges. --> A request to delete "my views" cannot be met, because nothing identifies a person; the privacy page says so.

Because the privacy page is generated from the same config that enables features, it cannot drift from what the site does. See the planned Security and privacy section for the full inventory.

## The media kit and its gate

The kit is two pages for two audiences. The public page shows your name, a short description, headline numbers and recent posts to anyone. The gated page holds detail you only want brands to see: partners, case studies, rates, testimonials, audience demographics and a disclosure statement.

The gate is a short form with an email address and a configurable bot check. A visitor who passes it gets a browser cookie that unlocks the gated page. The Worker records a **lead** for each submission, subject to caps. If the gate is off, there is no gated page.

Every number in the kit carries one of three **trust labels**: API, Hand-kept or Estimate. A number derived from others takes the weakest label of its inputs, and the formula is printed under it. The label comes from how the number was recorded, not from a template, so a number cannot claim a source it did not have.

The kit also applies the **privacy floor**: no count of 1 to 4 is printed, and totals, changes and demographic shares are held back when printing them would reveal a hidden small number.

You also get a public PDF, which carries only what the public page may show, and a private PDF for your own offline use. The private PDF is never published.

The field list is in `docs/kit.md` and `docs/config.md`.

## What the wall guarantees

The **wall** separates three levels of visibility.

| Level | Who sees it | Examples |
|---|---|---|
| Public | Anyone | Kit title and description, headline numbers, the public PDF |
| Gated | A visitor who passed the gate | Partners, case studies, rates, testimonials, demographics |
| Private | Only you | The private PDF, the owner report, fields that only control the build |

What the build enforces:

1. The public and gated pages are rendered from two separate views of the config, so a template cannot reach a gated field.
2. The two outputs go to separate directories that may not overlap, compared by filesystem identity, not by name.
3. Before anything is written, the build searches every public file for each gated value, including decoded forms (HTML entities, URL encoding, JSON and CSS escapes). A hit stops the build and names the field, never the text.
4. The gated page may not load remote resources, so opening it sends nothing to a third party.

What the wall does not guarantee: the search cannot catch every transformation a custom template might apply, and values under 6 characters are not searched. If you override the kit templates, review them as code that handles gated-adjacent text. These limits are written down in `docs/kit.md`.

## How numbers get in

Numbers reach the link page and the kit by three routes.

- **Collectors.** A scheduled refresh reads counts from a platform's own API with your credentials and writes them to the numbers file and the history store. A collector is optional per platform. Reach Around does not use paid third-party data APIs for collectors.
- **Hand-kept entries.** You type a value with a date. It prints as Hand-kept. This is the route for platforms with no usable API.
- **Imports.** You can import history exported from a previous provider. Imported numbers print as Hand-kept, with the export named in the line.

Whatever the route, a missing stretch is a **gap**, not a zero. Reports and exports say why the data is missing instead of building a number over it.

## How history and eras are labeled

The **history store** is append-only. Each record has a platform, a metric, a whole-number value and the date it applies to. Corrections are new records, not edits.

When a platform changes what a number means, you tag the records on either side with an **era marker**, so no one reads the jump as growth. The research notes record examples: a platform adding gifted subscriptions to its subscriber count, and a platform changing its view definition.

The **cutover** date is the day you switch your real bio link. If you set `cutover_date`, the kit shows "Live since" with that UTC day, and history from before it is labeled as from before Reach Around. The `parity` command checks that the new page carries every link the old one did.

All stored and reported times are UTC.

## How it stays up

Reach Around is designed to fail closed and to say so when it does.

- **Kill switches.** Each Worker route has a plain variable that turns it off at once, answering with an empty success and writing nothing. Use one for an incident, a flood or a bill you do not want. A switched-off stretch is reported as a gap with its reason.
- **Budgets.** Hourly and daily caps bound how much work a route may do. Over a cap, a request is dropped, not queued, and the stretch appears as a `quota` gap. A deploy that cannot fit the daily write quota is refused.
- **Health alerts.** An hourly job can tell you when a rollup is late, when views stop arriving on a site that had some, or when a budget is near its limit. It cannot tell you the job itself stopped, so use an external uptime probe on the public page.
- **Backups and restore.** Export the database before any schema change. Migrations only go forward. The restore drill is in `docs/backup-restore.md`.

Measurement has limits and a configurable bot check, so a count is an estimate of human traffic, not a census. The runbook is `docs/failure-modes.md`.

## What it costs

You pay Cloudflare for what the Worker uses, and nothing to this project. The page itself is static files, which are free to serve. The Worker's database writes are the constraint that decides which Cloudflare plan you need, and the project plans to treat the Paid Workers plan as its baseline. The Costs section is planned and will carry a worksheet with each published price, its source and the date it was read. Until it exists, treat any cost figure as unverified. <!-- TODO-verify: the Paid-plan baseline decision is not yet in the checked-in docs on main; confirm before this sentence is published. -->

## Where to go next

- The [Glossary](Glossary) for any term above.
- The [sidebar](_Sidebar) for the planned page tree.
- `docs/` in the repository for the reference pages that exist today.
