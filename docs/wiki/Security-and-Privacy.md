<!-- SPDX-License-Identifier: Apache-2.0 -->
# Security and privacy

This page is the public overview of what Reach Around collects, what it does not,
how the public and gated parts of a site are kept apart, and how long data lives.
It is written to be honest about the current state. Anything not built yet is
labeled planned, and the section "What is not done yet" lists it in one place.

Reach Around is open source. The privacy page a site shows its visitors is
generated from the site's own config, so it follows what is switched on. This page
describes the engine's defaults and behavior; your own site's generated privacy
page is the statement for your visitors.

## Not legal advice

Nothing here is legal advice, and nothing here says a site is compliant with the
GDPR, the CCPA or any other law. Whether a given site meets those laws depends on
who runs it, where its visitors are, and what the operator adds. The project
owner has a separate legal review of these questions in progress and has not
published a conclusion. If you run a site that collects a visitor's email address
or measures visitors, get your own legal advice for your own situation.

## What is collected

What a site collects depends on which features its config turns on. With
everything off, the generated privacy page says the site collects nothing.

- **Link clicks (on by default).** The link that was clicked, a source label from
  the site's own fixed list, the hour (rounded), and daily counts by link, source
  and country. A raw click row carries no country. Source, country, landing page
  and previous page are never stored together in one row.
- **Page views (when the analytics feature is on).** The page, a closed source
  label, the referring host only, a coarse window-size letter, the hour,
  browser, operating system, device kind and language, a location level, and a
  bot class. A short-lived daily code lets the site count unique visitors; it is
  made from a keyed hash, changes every day and is deleted at the end of the day.
  The address behind it is read in memory and is not stored.
- **Kit gate (off by default).** The email address a visitor types to unlock the
  media kit, a two-letter country code from the edge, timestamps, and hour-rounded
  "unlocked", "viewed" and "verified" records tied to that entry. The gate sets
  one cookie that remembers the unlock for a short time.
- **Live status (when a live block is on).** The page asks the site for the current
  stream status and gets back whether each stream is live and a fixed status code.
  The site sets no cookies and stores no identifier from these requests.
- **Inquiry form (if you use it).** The Worker does not store what is typed. It
  forwards it once to the room or mailbox the owner chose.
- **Owner alerts (off by default).** A short notice to a chat room or mailbox when
  someone unlocks the kit or a new lead is stored. See "What is not done yet" for
  what the notice can contain today.

## What is not collected

These limits apply to every analytics table, log, cache, queue and export (the
full list is in the [audience-analysis privacy page](../audience-analysis-privacy.md)):

- No full or partial IP address is stored.
- No user-agent string is stored as such, and no fingerprinting signal is used.
  The site keeps a coarse browser, system and device class, not the raw header.
- No cross-site or third-party identifier, and no third-party trackers or ad
  scripts on the public site.
- No cookie, `localStorage`, `sessionStorage` or `IndexedDB` value is used to
  recognize a returning visitor.
- No analytics row is joined to a person: not an email, account, handle, lead or
  donation.
- No precise location: no coordinates, postal code, network number or organization.
- No full referrer URL and no query string.
- No sale or sharing of data. The lead list is not exported to partners.

Clicks and views from a browser that sends Global Privacy Control or Do Not Track
are not counted or stored. The site's own script sends nothing for those visitors,
and the server discards a request that still carries the signal. The link still
opens. This covers the site's own script and the server; it cannot cover another
script a site owner adds to the page.

## The wall between public and gated

A Reach Around site has a public side and a gated side, and they are built apart:

- The public site is static files. It has no JavaScript for visitors by default,
  and it escapes every config value it prints, so text from a config file cannot
  become markup.
- Gated kit content is not in the public files. It lives in a separate store behind
  the Worker and is served only to a visitor holding a valid unlock cookie. A
  failed check returns a plain answer that does not say which check failed.
- Nothing is stored before the bot check passes at the gate.
- Public totals shown on a site are floored: a value under the privacy floor is not
  shown, and the margins of a table are computed from the cells that are safe to
  show, so a hidden number cannot be recovered by subtraction.

For the reasoning and the code behind these, see
[privacy by construction](../education/privacy-by-construction.md),
[escaping and no JavaScript](../education/escaping-and-no-js.md),
[CSP and overrides](../education/csp-and-overrides.md) and the
[architecture page](Architecture.md).

## Retention

| Data | How long | Notes |
|---|---|---|
| Lead (email, country, timestamps, kit events) | 30 days by default; the owner can set 1 to 365 | Counted from creation; seeing the address again does not extend it. The kit events go with the lead. |
| Raw page view records | 7 days by default; at most 90 | A day's records can wait until they are added to the daily totals, but never more than three times the keep window and never more than 90 days. |
| Daily unique-visitor code | Deleted at the end of the UTC day | Not a stored identifier of a person. |
| Hourly click records | 90 days | Fixed. The privacy page cannot promise a different number. |
| Daily counts | 90 days | Aggregate only. |
| Monthly totals | Up to 25 months | Aggregate only. |
| Unlock cookie | A short time, set by the Worker | Not readable by page scripts. |

The retention number on a site's privacy page, the build manifest and the Worker
all come from one config value, and a deploy stops if they disagree, so the page
cannot promise one period while the purge uses another. Cleanup runs on a schedule
and is bounded, so a deletion can lag by hours, or by days after a large backlog.

Not covered by these windows: the hosting provider's own recovery copies and logs,
copies of owner alerts sitting in a chat service or mailbox, and anything an
operator exported by hand. The operator-side detail, including how to delete one
lead, is in the [privacy and retention guide](../privacy.md).

## What the visitor sees

- A generated privacy page, built from the site's config. It lists each thing the
  enabled features collect, why, and for how long. Turn a feature off and its item
  disappears.
- A cookie notice only when an enabled feature actually sets a cookie (today, the
  kit gate). It works without JavaScript.
- A plain notice at the kit gate about the email address being asked for.
- A normal, working link when a click is not counted because of a privacy signal.

## Bot checks

Two places use a bot check. The kit gate runs a check before it stores anything.
Measurement also classifies traffic and counts likely bots apart from people, so
bots do not inflate audience numbers.

- The gate check today is Cloudflare Turnstile. When a visitor passes it, the
  visitor's IP address and browser details are seen by Cloudflare as part of that
  check. A site that uses it should name it on its privacy page.
- A proof-of-work alternative, switchable by config, is planned (see below). The
  design is that Turnstile is never loaded for a visitor who sends Global Privacy
  Control or Do Not Track.

## Abuse and rate limits

Every public route that writes data is limited. In words only:

- Each route has its own ceilings, and there is also a shared ceiling above them,
  so one busy or hostile source cannot use up the whole allowance.
- A per-client limit applies, built from a keyed hash of request fields that is not
  stored as a profile.
- Bot traffic gets a smaller allowance than human traffic.
- Daily write budgets protect the database from being filled on purpose or by
  accident.
- An owner can switch off a route for a site with a kill switch, without a deploy of
  new code.
- Limits are tuned by the owner, and the exact numbers and internals are
  deliberately not published, because publishing them would help someone get around
  them.

## Reporting a vulnerability

Report a vulnerability privately through GitHub: on the repository's main page,
open the **Security and quality** tab and click **Report a vulnerability**. The full
policy, including supported versions and what to expect, is in
[SECURITY.md](https://github.com/ChiefGyk3D/Reach-Around/blob/main/SECURITY.md).

- Do not post exploit detail, proof-of-concept code or stolen data in a public
  issue, pull request or discussion.
- If the **Report a vulnerability** button is not there, open a public issue that
  says only that you have a security report and want a private channel, and wait for
  the maintainer to reply.

## What is not done yet

Planned means decided but not built. Nothing here should be read as already true
unless the engine on the main branch does it. Each item marked "today" was checked
against the code on main.

- **Region-only raw view rows.** Planned. Today the Worker's default location level
  for page views is city. Operators can set it to region or country now; the default
  change is planned.
- **Masked email in owner alerts by default.** Planned. Today the alert setting
  defaults to the full address, and the copy sits in a chat service or mailbox
  outside the site's purge. Setting it to masked or none is available now.
- **Fuller privacy page content.** Planned: the controller's identity and a
  required contact line when the kit gate or alerts are on, the legal basis, the
  recipients (the hosting provider and any alert service), the rights a visitor has,
  and how to complain to a regulator. Today the contact line is optional and the
  page does not carry all of this.
- **Softer "does not identify anyone" wording.** Planned. Some current text on the
  privacy page is more absolute than the data supports, because view records hold
  several attributes together while they exist.
- **Bot-check switch and naming.** Planned: the proof-of-work option, the rule that
  the Turnstile check is never loaded for Global Privacy Control or Do Not Track
  visitors, and naming it on the privacy page.
- **Easier deletion and access.** Planned. Deleting a lead today is a manual
  five-step export-and-SQL job, and there is no access tool beyond a manual query.
  Copies in chat services and recovery windows are not reached by it.
- **Gate email verification.** Planned for a later version. The gate stores the
  address as typed, with a notice, and does not verify it.
- **Operator paperwork helpers.** Planned: a records-of-processing template, a
  screening note for data protection impact, a breach-response procedure and a
  record of the hosting provider's data-processing terms.
- **No marketing mail to leads.** This is the owner's decision and is not enforced
  by code, because the engine has no feature to send marketing mail; the lead list
  is never exported or shared.
- **Public threat-model summary.** The project keeps a threat model and a risk
  register. A public summary of the method and of closed findings is planned after
  the owner approves it. Open items are tracked privately and are not listed here.
- **Legal review.** The owner's own review of the questions above is in progress.
  This page will not claim compliance before it is done.

## Where to read more

- [Privacy and retention guide for operators](../privacy.md)
- [Audience-analysis do-not-collect list](../audience-analysis-privacy.md)
- Teaching pages: [privacy by construction](../education/privacy-by-construction.md),
  [click endpoint](../education/click-endpoint.md),
  [config hardening](../education/config-hardening.md),
  [CSP and overrides](../education/csp-and-overrides.md),
  [escaping and no JavaScript](../education/escaping-and-no-js.md),
  [snapshot store](../education/snapshot-store.md),
  [threat model walkthrough](../education/threat-model-walkthrough.md)
- [Operations](Operations.md) and [Architecture](Architecture.md) (wiki)
