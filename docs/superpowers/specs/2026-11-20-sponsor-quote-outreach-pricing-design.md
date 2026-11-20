<!-- SPDX-License-Identifier: Apache-2.0 -->
# Sponsor page, quote, outreach and pricing: design

Status: draft for owner decisions. Nothing here is built. Issue: #608. Epic: #245.
Date: 2026-11-20. Companion spec (separate document, other author): the media kit data model and trust labels.

This spec covers three things the maintainer ranked after the media kit: (1) the sponsor page and quote flow, (2) outreach and follow-up, and (3) pricing (the rate card and calculators). AI is an optional helper inside (2) and (3), never a dependency.

## How to read the labels

- **VERIFIED**: read on the publisher's own page on the date shown (2026-11-19 unless stated). The fetches went through a summarizing tool, so re-read the exact wording once before money depends on it.
- **INFERRED**: from a search snippet, a restatement, general knowledge or this spec's own reasoning. The reason is given.
- **Convention**: a named public source uses this formula or range.
- **Invented**: proposed here; no source established it. Every Invented number is printed as such on the page and the quote.
- **Repo**: a statement about this repository at the commit this branch was cut from.

## 1. Intent and why

A brand that wants to sponsor a creator today lands on the gated kit, reads packages and a rates table made of free text (`price_text`, often "Ask me!"), and fills the five-field inquiry form. The owner gets one chat alert, stores nothing, and starts a manual email thread. Every number a brand needs to say yes (what a stream integration costs, what a bundle across platforms costs, what the terms are) is something the owner types by hand each time.

Published sponsorship rates disagree by up to ten times for the same cell and only one marketplace states a sample size (Repo research; sources in section 5). So the honest tool is not a number generator. It is an open formula per format, ranges with sources named, and the creator's judgment applied on top.

The creator this serves streams on Twitch and Kick equally, publishes video and shorts, posts on Bluesky, Mastodon and the wider fediverse, and may also run a podcast, a newsletter and a blog. Each of those is priced in its own unit. Units are never added together; only dollars are.

## 2. Goals, non-goals, anti-goals

### Goals

1. A sponsor page (on the gated side of the kit) where a brand picks deliverables across platforms and gets an indicative price range, a printable quote, and a way to reach the owner, with no script required.
2. A rate card the owner edits in `site.yaml`: per-platform, per-format, in the native unit, with ranges, modifiers and a blended-package rule.
3. Calculators (owner-side) that turn the kit's own numbers into a rate card draft, and compare an incoming offer to the ranges (implied CPM, effective rate per concurrent-viewer-hour).
4. A lead and deal pipeline the owner runs locally: leads, quote status, deadlines, exclusivity windows, reminders.
5. Outreach drafts tailored from the kit data, written by a template first and optionally by a model, always approved by the owner before anything leaves.
6. An optional AI layer that is off by default, bring-your-own-key, local-model first, with a hard monthly spend cap per provider enforced in code.
7. Twitch and Kick get the same fields in the same order everywhere. Neither is a footnote to the other.

### Non-goals

- Invoicing. Zoho Books (or the owner's accounting tool) issues invoices (earlier ruling, Repo: `docs/superpowers/specs/2026-10-23-reach-around-design.md`, non-goals). Section 7 says where the hand-off sits and what changes if that is reversed.
- A hosted multi-tenant service (same earlier ruling). Everything here is self-hosted.
- Taking payment, holding money, or any transaction fee. Ever.
- Computing tax, VAT or sales tax, or advising on it.
- A marketplace, brand database, brand scraping or cold-email list building.
- Live currency conversion (needs a third-party call).
- Contract negotiation software or e-signature.

### Anti-goals (a failure even if it technically works)

- A message sent to a brand that the owner did not read and approve.
- Any visitor data (brand name, email, details) sent to a model, or any third-party call from the public or gated site.
- A single "your rate is $X" number presented as fact, or an Invented number that looks like a benchmark.
- A model spend that can exceed the cap, including through a retry loop or a crash.
- A quote that shows a metric without its source, as-of date and trust label (the FTC fake-indicator rule makes unlabeled typed numbers a risk; section 12).
- The owner's other deals, exclusivity windows or private rates visible to a brand.
- A claim that the tool complies with any platform's branded-content rules.

## 3. What exists today (Repo)

- The gated kit page is static, has no script, and makes no third-party request. It holds partners, case studies, `services` (packages, with `price_text`), `rates` (`item`, `price_text`), testimonials, demographics, a disclosure statement and the optional inquiry block (`docs/kit.md`, `docs/config.md#kit`).
- The inquiry form posts to `POST /k/inquiry` on the Worker. Five fields (`name`, `email`, `brand`, `budget`, `details`) plus a honeypot, an unlock cookie check, a per-client limiter and a 16 KiB cap. Nothing is stored. Delivery is the owner alert `inquiry_received`, one line, neutralized, capped at 800 characters for details, and bounded by the daily alert budget (`DAILY_ALERT_BUDGET`, default 20) (`docs/kit.md`, `docs/alerts.md`).
- Alerts go to Discord or Matrix only (email has no provider yet). Never in an alert: IP, user agent, cookie, token, country, lead id or hash (`docs/alerts.md`).
- Leads (the gate's addresses) live in D1 and are deleted after `LEAD_RETENTION_DAYS`, 30 by default, 365 at most (`docs/kit.md`, `docs/privacy.md`).
- The owner PDF (`reach-around kit pdf`) prints the gated kit offline through a Chromium binary, adds no Python dependency, and is never published (`docs/kit.md`).
- The Worker already carries "optional provider-backed features" and an `ALERTS_CONFIG`-style plain variable the deploy writes from the build manifest (`docs/alerts.md`, README).

The design reuses all of that. It adds a build-time price table, one new Worker route family, one new alert event, a local pipeline store, and an optional AI module on the owner side only.

## 4. The sponsor page and quote flow

### 4.1 What the brand sees

The sponsor page is a section of the gated kit, reached the same way (the existing gate and unlock cookie). The public page gets one "Work with me" link to the gate. The public page shows no rates, no ranges and no quote form.

Steps, all plain HTML forms and normal page loads (no script, no third-party request):

1. **Choose.** A checklist of deliverables grouped by platform and format, in one fixed order, with Twitch and Kick side by side using identical wording. Each row shows the unit ("per sponsored live hour", "per dedicated video", "per episode read") and a quantity box (1 to a small cap).
2. **Terms.** Radio and checkbox options: usage window (none, 30, 60, 90 days), exclusivity (none, category for 30, 60, 90 days), whitelisting or partner-ad rights, clip rights, rush (under 7 days), flight dates. A brand category pick from a short owner-defined list (used for the niche premium and for exclusivity-conflict checks the brand never sees).
3. **Your details.** Name, email, brand, optional budget and notes (the existing five fields, same caps).
4. **Quote page.** After posting, the brand sees an indicative range per line, modifiers as separate lines (a brand can see what dropping one saves), a blended-package line if the bundle rule applies, a total range, the validity date, the terms summary, the disclosure clause, the data-source labels for every creator metric the price rests on, and a "Print or save as PDF" button that is plain browser printing with a print stylesheet. No script is needed: the button is a link to print instructions, and the browser's own print command does the work.
5. **Send.** The same page carries "Send this request to the owner" (a form posting the quote reference). The brand can also just print and leave; sending is explicit.

What the brand never sees: the owner's other deals, the owner's internal floor, the formulas' creator-side inputs beyond the labeled metrics, the pipeline, or any owner-only note.

### 4.2 How the price is computed without script and without leaking inputs

The build precomputes. Formulas (section 5) run at build time, owner-side, over the kit's own metrics, and write a price table: for each deliverable and each modifier option, a low and a high amount, the formula text, the metric, its trust label and its as-of date. The Worker holds only that table (a small JSON plain variable or asset written by the deploy, the same pattern as `ALERTS_CONFIG`, so no new secret) and does arithmetic on ids: sum the selected lines, apply the blended-package rule, apply modifiers, round, and render. It has no metrics, no collectors and no formulas to get wrong, and it cannot be talked into a number outside the table.

Staleness is explicit: the quote shows the table's as-of date and a validity window (Invented default: 30 days). A table older than the owner's `max_table_age_days` (Invented default: 45) makes the sponsor page show "contact me" instead of ranges, rather than serving stale numbers.

### 4.3 How the request reaches the owner

On "Send":

1. The Worker re-validates the unlock cookie and the origin, applies the existing limiter and body cap, strict field rules and honeypot, and recomputes the quote from the table (the brand's posted totals are ignored, only ids are read).
2. It writes one **quote request row** to D1 (section 4.4) and fires one new alert event, `quote_requested`, over the owner's existing channels. Same rules as every alert: neutralized, one line, capped, mentions off, counted against the daily alert budget.
3. The alert carries only: a quote reference, the time, the brand, the sender's name and address (rendered per `visitor_email`), the number of lines, and the total range. It carries no IP, no cookie, no hash. The details text stays in the stored row, and the alert says "details on file", because a chat room is not a secret store (Repo: `docs/alerts.md`).
4. If no channel took the alert, the visitor sees the existing "not sent" style page, and nothing is lost silently (the row is not written). This is the same fail-closed behavior as the inquiry form.

The existing inquiry form stays as the "just talk to me" path and is unchanged. A quote request is an inquiry with a structured selection attached.

### 4.4 What is stored, and for how long

| Data | Where | Default retention | Why |
|---|---|---|---|
| Quote request row (reference, selection ids, computed range, brand, name, email, notes, time) | D1 `quote_requests`, owner pulls then it is deleted | 30 days (same default as leads), maximum 90, set with `privacy.quote_retention_days` | The owner needs the detail to answer, and the alert is deliberately thin |
| Alert copy | The owner's chat service | Provider's own rules (Repo: `docs/privacy.md`) | Existing behavior |
| Pipeline (leads, quotes issued, status, deadlines, notes) | Owner's local store under the instance directory, never on the Worker | Until the owner deletes it | It is the owner's own business record; it is not website visitor data |
| Browser print or PDF the brand saved | The brand's device | Not ours | |

Pull model: `reach-around deals pull` reads the new rows over the same authenticated path the analytics pull uses, writes them into the local pipeline, and deletes them from D1 (dry run by default, like `reach-around leads delete`). Anything not pulled in 30 days is purged by the existing hourly retention job. The privacy page gains one entry for the sponsor page when it is enabled, and the retention is stated in numbers taken from the config, not hard-coded (the existing rule).

An address that appears in a quote request is a person's data. It is therefore never sent to a model (section 9), never put in a log, and is deleted by the same `leads delete` family of commands.

### 4.5 Spam and abuse

The sponsor page inherits everything the inquiry form has: unlock cookie, origin check, limiter, size cap, strict characters, honeypot, daily budget (the budget caps quote requests too). Extra: a quote request is refused if the selection ids are not in the current table or the quantity is out of bounds; the quote reference is random (not sequential, so it leaks no volume); the Worker never renders a visitor-supplied string outside the escaped context it was posted in.

## 5. Rate card and calculators

### 5.1 Output rule

Every price is a **range** (low to high), printed with its formula, its inputs, each input's trust label and as-of date, and the source list. There is no single "suggested price" (owner decision 5). Where the sources disagree widely, the card says so ("sources differ by up to 10x for this cell") and shows both source tables beside the computed number. Where no benchmark exists, the line says "No market benchmark; creator-set" and the owner types the price.

### 5.2 Rate card in config (sketch)

```yaml
kit:
  rate_card:
    currency: USD                  # one per quote; no conversion
    valid_days: 30
    min_booking: 100               # Invented floor per line, creator-set
    lines:
      - id: live-twitch
        platform: twitch
        format: live_integration
        unit: ccv_hour
        metric: {key: avg_ccv, window: 30d}      # from the unified metric rows
        rate: {low: 0.80, high: 1.50}            # per CCV-hour
        hours: {default: 2, max: 8}
      - id: live-kick
        platform: kick
        format: live_integration
        unit: ccv_hour
        metric: {key: avg_ccv, window: 30d}
        rate: {low: 0.80, high: 1.50}
        hours: {default: 2, max: 8}
      - id: video-dedicated
        platform: youtube
        format: dedicated_video
        unit: cpm
        metric: {key: median_views, window: last_12_items}
        rate: {low: 30, high: 80}
      - id: fedi-post
        platform: mastodon
        format: written_post
        unit: flat
        price: {low: 40, high: 120}              # creator-set, no benchmark
    modifiers: [usage, clip_rights, whitelisting, exclusivity, rush, niche, bundle]
```

The metric keys follow the unified metric row in the companion media-kit spec (platform, account, key, value, unit, period, as_of, trust, method, verify_url). The calculator reads them; it never invents a metric, and a line whose metric has `trust: typed` or `estimate` prints that label on the quote.

### 5.3 Formulas and defaults

Defaults are starting points the owner overrides, shown as ranges. Sources were read 2026-11-19 unless noted.

| Format | Formula | Default range | Status and source |
|---|---|---|---|
| Sponsored live stream (Twitch, Kick, YouTube Live) | average CCV x hours x rate per CCV-hour | $0.80 to $1.50 per CCV-hour; technical or software audience up to $3.00 | Convention. VERIFIED that the page says it: https://www.streamscheme.com/twitch-sponsorship-rate-card/ (page "last updated December 26, 2025"); wider ranges INFERRED from search summaries of rate-guide blogs. The page covers Twitch; the unit applies to Kick because Kick publishes the same metrics (INFERRED) |
| In-stream mention or segment inside a normal stream | live formula x 0.5 | x0.4 to x0.6 | Convention, weak (INFERRED, one search summary) |
| Dedicated video | expected views / 1,000 x CPM | $30 to $80 | Convention. VERIFIED: https://www.trykarat.com/post/how-much-to-charge-for-a-brand-deal (no publication date on the page) |
| Integrated video segment | expected views / 1,000 x CPM | $20 to $50 | Convention, same Karat page |
| Short (TikTok integrated, TikTok dedicated, Reel) | expected views / 1,000 x CPM | $15 to $40, $25 to $60, $15 to $35 | Convention, same Karat page |
| Instagram feed, Story | expected views / 1,000 x CPM | $10 to $30, $5 to $15 | Convention, same Karat page |
| Podcast host-read | downloads per episode at 30 days / 1,000 x CPM x episodes | $25 to $40; business or B2B $40+ | Convention. VERIFIED: https://www.acast.com/en/news-and-insights/how-much-does-podcast-advertising-cost (the page's date is unreliable) |
| Newsletter placement | delivered / 1,000 x CPM, or subscribers x 0.025 to 0.05 | $10 to $30 general, $40+ niche; flat fee 2.5% to 5% of subscriber count in dollars | Convention. VERIFIED: https://www.beehiiv.com/blog/newsletter-cpm (2026-04-27) and https://www.paved.com/blog/newsletter-sponsorship-rates/ (2026-07-29). beehiiv states its range is not tied to cited benchmark data |
| Written post (blog, Mastodon, Bluesky, Lemmy, Pixelfed) | creator-set flat fee, shown next to the creator's median engagements per post | none | Invented. No source prices fediverse or Bluesky reach; the page says so (below) |
| Expected views | median views over the last 10 to 15 items (30-day views for video) | n/a | Karat says average of the last 10 to 15 pieces, not the best performers (Convention); using the median to resist outliers is Invented |

Fediverse note, stated on the page: no published sponsor valuation of fediverse or Bluesky audiences was found. The pitch for these lines is audience fit and numbers anyone can re-check, not size. INFERRED from the research gap; a Digiday report dated 2024-11-29 said Bluesky had no ad program (article URL not recorded in the research, so treat as INFERRED; nothing newer found).

Modifiers (multiplicative on both ends of the range, each a separate quote line):

| Modifier | Default | Status and source |
|---|---|---|
| Usage rights (brand reposts the content) | +20% to +50% per 30 to 90 days | Convention. Karat, same page |
| Clip rights for reuse elsewhere | +30% to +80% | Convention, weak (INFERRED, search summary) |
| Whitelisting or paid amplification | +30% to +50% | Convention. VERIFIED: https://later.com/blog/influencer-pricing-benchmarks-the-complete-2026-guide/ (2026-07-07) |
| Exclusivity (category, window) | +20% to +50%, scaled by window length (30, 60, 90 days) | Range Convention (Karat); scaling by days is Invented |
| Agency or manager fee | +10% to +20% | Convention (Later). Off by default; the owner turns it on only when an agent is involved |
| Rush (under 7 days notice) | +25% to +50% | Invented. No source found |
| Niche or technical audience premium | x1.25 to x2, owner-set multiplier labeled "Invented: your judgment" | Invented, supported indirectly (INFERRED) by technical CCV-hour ranges up to $3.00 against $1.00 standard and B2B CPMs of $40+ |
| Story or ephemeral format | x0.5 | Convention (Later: Stories about 50% less) |

### 5.4 Per-platform and blended multi-platform packages

- **Per line, native unit.** A Twitch line prices CCV-hours. A Kick line prices CCV-hours with the same defaults and the same wording. A video line prices views. A podcast line prices downloads. Never convert one audience unit into another.
- **Blend = sum of dollars, never of audiences.** The quote shows each line's formula and inputs; the package total is the sum of the line ranges.
- **Bundle rule (Invented):** a package with lines on two or more platforms takes a discount between 10% and 15%, applied conservatively: the low end takes the larger discount, the high end the smaller. Both numbers are owner-editable and printed.
- **Cross-post bundle** (same creative on several platforms) is the owner's package, priced as one flat range, not derived.
- **Audience statement beside the package.** The page shows "N platforms, X follows. Largest single audience Y. People who follow in more than one place are counted more than once; unique people are between Y and X." Live CCV, downloads and emails are never added into that line; they are shown in their own units. (Same rule as the media-kit spec. The Sainsbury independence formula, VERIFIED at https://support.mediaocean.com/tview/help/t/mixing_media.htm, is an upper bound under independence and is not shown as a headline.)
- **Evergreen versus live.** Video, blog and PeerTube lines price expected 30-day views and note a long tail. Live lines price CCV-hours and offer VOD or clip rights as a separate modifier. INFERRED.

### 5.5 Owner-side calculators

All run locally (`reach-around rates ...`), print the formula and sources, and output ranges.

1. **Rate card draft.** Reads the kit metrics and the config, fills each line's low and high from section 5.3, and prints a "where you sit" view. It warns when a computed range falls outside both published per-post tables for the creator's tier (Instagram micro, for example: Influencer Marketing Hub, last updated 2026-10-06, https://influencermarketinghub.com/influencer-rates/ and Later above, both VERIFIED, differ by up to 10x). The warning is an Invented sanity check.
2. **Offer check.** Paste an incoming offer: it prints the implied CPM (offer / (expected views / 1,000)) and the effective rate per CCV-hour (offer / (average CCV x hours)), next to the ranges. Convention (arithmetic).
3. **Net after platform and processor** (optional, off the critical path). Platform shares from the vendors' own pages: Kick 95/5 (https://help.kick.com/en/articles/15159722-understanding-kick-s-revenue-split), YouTube 55% of net for long-form ads and 70% for memberships (https://support.google.com/youtube/answer/72902), Twitch Plus Program levels (https://blog.twitch.tv/en/2024/01/24/an-update-to-several-streamer-payout-programs/), all VERIFIED 2026-11-19. Twitch's default 50/50 is INFERRED (secondary source). Printed as "before tax", with no tax advice.
4. **Value per follower** is shown only as descriptive context (quote / followers), never as a price driver. Invented.

### 5.6 What does not get a calculator

A "you should charge $X" button. A tax estimate. A currency converter. A guarantee that a range is current: every card prints its as-of date and "check the current numbers before sending".

## 6. The quote and invoice boundary

### 6.1 Recommendation

Quotes are in Reach Around. Invoices stay in the owner's accounting tool (Zoho Books today). The hand-off is an export, not an integration.

- A quote moves through states in the local pipeline: draft, sent, accepted, declined, expired. Accepting is the owner recording that the brand agreed in writing, outside the tool.
- On "accepted", `reach-around deals export <ref> --format csv|json` writes the accepted lines (deliverable, platform, format, quantity, unit price, scheduled date), the modifier lines, the deposit and payment terms the owner chose, and the tax lines the owner typed. The owner imports or retypes it in the accounting tool, which assigns the legal invoice number, computes tax and sends the invoice.
- Reach Around never computes tax, never assigns an invoice number and never holds a payment. The quote carries its own random reference, labeled "quote reference, not an invoice number".

Why: the earlier ruling stands (Repo), the accounting tool already does the regulated parts, and a quote export needs no new integration surface. The EU VAT invoice fields (sequential unique number, date, supply date, party names and VAT ids, quantity and nature, unit price, rate and amount, reverse-charge reference) are VERIFIED from Directive 2006/112/EC, Article 226 (eur-lex, read 2026-11-19); that is exactly the checklist the accounting tool carries so this tool does not have to. A later read-only add-on that imports paid status from Zoho Books remains the existing idea (#3) and is not part of this spec.

### 6.2 What the quote does carry

Quote reference, date, validity date, parties as typed by the owner, line items with per-line formula and trust labels, modifier lines, the total range, deposit (a common convention; INFERRED, no source read), payment terms (default net 30; EU B2B terms over 60 days need express agreement under Directive 2011/7/EU, VERIFIED), a disclosure clause (the brand and creator agree the FTC-style disclosure and the platform's paid-partnership tool; clause wording Invented), kill fee and revision rounds as owner-edited text (INFERRED convention), and a one-line "tax, if any, is added by the invoice" note.

W-9 or tax forms never travel through the inquiry or quote forms. The quote says "forms are exchanged outside this page" (a payer in the US needs a creator's taxpayer id for a 1099-NEC; VERIFIED basis, https://www.irs.gov/forms-pubs/about-form-w-9 and https://www.irs.gov/instructions/i1099mec).

### 6.3 If the owner reverses the ruling (builds invoicing in)

What would have to change, so the decision is made with the cost visible:

1. A sequential, gapless, unique invoice number per seller, which needs a durable counter. The Worker has no durable ledger today and the pipeline is a local file, so the owner-side store becomes the system of record and needs backup and restore like analytics.
2. Tax fields, reverse-charge handling and jurisdiction rules. At minimum typed tax lines; the moment the tool computes anything it takes on liability for being wrong.
3. Immutable issued documents with an audit trail (credit notes instead of edits).
4. A payment-state model, which pulls in payment processors and their webhooks, and with them secrets, a public callback surface and a new threat model. That conflicts with the "static to the visitor" principle and the Worker's small surface.
5. Retention rules for financial records (often years) that conflict with the 30-day privacy defaults elsewhere in this design.
6. A larger test and review burden, and a reason to revisit the "no hosted service" ruling, since most of the value of integrated invoicing is collection.

This is why the recommendation is export-only.

## 7. Outreach and follow-up

### 7.1 The pipeline

A local store (`reach-around deals ...`, a file or small database under the instance directory, covered by the existing backup and restore guidance) with these records:

| Record | Fields |
|---|---|
| Lead | id, brand, contact (name and address, optional), source (inbound quote request, inbound inquiry, referral, owner-added outbound), category, notes, stage, created, last touched |
| Stage | new, drafted, sent, replied, quoted, negotiating, accepted, declined, lost, expired. The owner moves the stage; nothing moves it automatically except `expired` on a date and `new` on import |
| Quote | reference, lead id, lines, total range, valid until, status |
| Deal | accepted quote, deliverables with due dates, flight dates, usage window, exclusivity category and window, disclosure method per platform |
| Reminder | what, due date, linked lead or deal |

Conflict check (owner-side only): a new quote that overlaps an accepted deal's exclusivity category and window prints a warning. A brand never sees this.

### 7.2 Deadlines and reminders

Reminders are for the owner only. They reach the owner through the existing alert channels (a new `deal_reminder` event type, daily at most, one summary line, no brand contact data) and as a `reach-around deals due` listing in the terminal. Nothing in a reminder is sent to a brand.

Defaults (Invented, owner-editable): follow up an unanswered quote at 3 and at 10 days; a maximum of two follow-ups per lead, then the stage becomes `lost` unless the owner moves it; quote expiry at the validity date; deliverable and flight-date reminders at 7 and 1 days. A reminder says "due", never "send".

### 7.3 Tailored pitch drafts from the kit data

Inputs the generator may use: the owner's own kit data (platform metrics with labels, case studies, partners marked public, the services text), the brand name and category the owner typed, and the owner's notes. It may not use: a visitor's email or name, free text a visitor submitted, or anything from the gated page's private fields (section 8.3).

Two generators, same output shape (subject, body, a "what this draft used" list, and the claims it makes with the kit number each rests on):

1. **Template mode (default, no model).** Fill-in templates with the numbers inserted. Always available, deterministic, tested.
2. **Model mode (optional).** The same inputs, a model drafts the wording. The prompt contains only the allowed inputs. The output is checked: every number in the draft must appear in the supplied kit data or the draft is rejected and shown with the offending numbers marked (a mechanical check; the model's prose is never trusted for facts).

### 7.4 What never gets sent automatically

- No outbound message of any kind, to any brand, from any code path in this feature. There is no send function in the outreach module. The output is text on the owner's screen, a file, or the clipboard.
- The owner copies a draft into their own mail client. This is deliberate: it keeps SMTP credentials, deliverability and bounce handling out of the tool, and puts a human at the only step that matters.
- Reminders reach only the owner's own channels.
- The tool never imports a contact list, never scrapes, and never guesses an address.

### 7.5 Outreach cautions

Cold email to businesses has its own rules in the US and elsewhere (for example CAN-SPAM, and consent rules in the EU and UK). This was not researched or verified for this spec: INFERRED, check before sending to anyone who did not contact the owner first. The tool prints a short "check the current rules" note beside outbound drafts, and its templates carry a sender identity and a way to say no, because those are the plain minimum (INFERRED).

## 8. AI usage design

### 8.1 Principles

1. **Optional.** Off by default. Nothing in sections 4 to 7 needs it. The page, the calculators, the pipeline and template-mode drafts all work with no model and no key.
2. **Owner-side only.** No model call exists on the public site, the gated site or the Worker. A visitor's request never causes a model call. The sponsor page has no AI.
3. **Bring your own key.** Keys live in the secret manager and reach the process as environment variables (never in the config, repo, argv, logs or a GitHub secret). The config names which variable to read, not its value.
4. **Local first.** The router tries a local OpenAI-compatible endpoint (for example Ollama) before any hosted provider. A hosted provider is used only when listed in `ai.providers` and only when the local one is unreachable or the task is marked as needing a hosted model. Local calls cost nothing and are not metered against a cap (they are still logged).
5. **Fail closed.** If the ledger cannot be read or written, the cap cannot be computed, or the config is invalid, no hosted call is made.
6. **Privacy by data class** (8.3).

### 8.2 Spend caps, enforced in code

```yaml
ai:
  enabled: false
  local:
    endpoint: http://localhost:11434/v1     # owner-controlled, optional
    model: example-local-model
  providers:
    - name: openai
      key_env: OPENAI_API_KEY
      model: example-model-id
      monthly_cap_usd: 5                    # hard cap, per provider, per calendar month
      per_call_cap_usd: 0.10
      price_per_mtok: {input: 0, output: 0}  # owner-entered from the provider's current page; no default
    - name: anthropic
      key_env: ANTHROPIC_API_KEY
      model: example-model-id
      monthly_cap_usd: 5
      per_call_cap_usd: 0.10
      price_per_mtok: {input: 0, output: 0}
  tasks: {pitch_draft: local_first, rate_card_notes: local_only}
```

Mechanics (all in the owner-side module, with tests):

1. **Ledger.** An append-only local file (one line per call) plus a monthly total per provider, written under a file lock so concurrent runs cannot both pass the check. Calendar month in UTC.
2. **Pre-flight reservation.** Before a hosted call the module estimates the worst-case cost: input tokens (counted or conservatively estimated from characters) times the owner-entered input price, plus `max_output_tokens` times the output price. If `spent_this_month + estimate > monthly_cap_usd`, or `estimate > per_call_cap_usd`, the call is refused before any network traffic. The estimate is reserved in the ledger first; the actual usage from the response replaces it afterward. A crash leaves the reservation in place, so a crash can only make the cap stricter.
3. **Hard `max_output_tokens`** on every call, no retries on a refusal by the cap, a bounded retry (at most one) on a network error, and a per-run call limit.
4. **Prices are config, not code.** The tool ships no hard-coded provider prices because they change; the owner enters the provider's current per-token prices, and the tool refuses to run a hosted call if they are zero or missing. Whether a provider also offers its own account-level spend limit was not verified for this spec (INFERRED that they do); the code cap is the one this design relies on, and the owner should also set the provider's own limit as a second fence.
5. **Visible.** `reach-around ai usage` prints per-provider spend this month, the cap, the remaining headroom and the call count. At 80% of a cap the owner gets an owner-channel alert (`ai_budget_near`, same style as `alert_budget_near`); at 100% hosted calls stop and say why.
6. **Kill switch.** `ai.enabled: false`, or an environment variable, stops every model call at once.

What the ledger cannot know is spend by other tools sharing the same key. The cap is a cap on this tool's spend only, and the docs say so. Use a dedicated key for this tool.

### 8.3 Data classes and what may reach a model

| Class | Examples | Local model | Hosted model |
|---|---|---|---|
| Owner public kit data | public metrics, public partners | allowed | allowed |
| Owner gated kit data | rates, packages, case-study text | allowed | allowed only if the owner set `ai.allow_gated_to_hosted: true` (default false) |
| Owner-typed lead fields | brand name, category, owner notes | allowed | brand name and category only |
| Visitor data | name, email, free text, quote request details, IP | never | never |
| Secrets, station or personal details | tokens, addresses, callsigns | never | never |

Visitor data is excluded structurally: the AI module's input type is built from the kit and the owner-typed fields, and the pipeline's contact fields are not part of it. A test feeds a pipeline containing a known hostile string and asserts it never appears in any request body.

Model output is untrusted text (draft copy only). It is displayed, never executed, never used as a config value, and never fed back as an instruction. A brand's inbound text is evidence, not instruction.

### 8.4 Logging

Each call records: time, provider, model, purpose, input and output token counts, estimated and actual cost, outcome (ok, refused by cap, error) and a hash of the prompt. The prompt and the response text are not logged unless the owner turns on `ai.log_text` (default false, local file only, never in a public repo). No key, no header and no contact data is ever written.

### 8.5 What the models are for

Pitch wording (7.3), a plain-language note beside a rate card line, and a rewrite of the owner's own draft. Not for: choosing a price, computing anything, reading visitor messages, sending anything, or deciding a stage.

## 9. Approaches and recommendation

### Approach A: Static sponsor page with a Worker-computed range (recommended)

The flow in section 4. Build-time price table, Worker arithmetic on ids, printable HTML quote, a stored request pulled to a local pipeline, thin alert.

- For: no script, no third-party call, fits the existing gate, Worker and alert design; the Worker holds no formulas and no creator metrics; the owner stays in control of the numbers; works with the self-hosted model.
- Against: new Worker routes and a new D1 table to review and threat-model; the "PDF" is the browser's print of an HTML page, not a generated file (acceptable for v1); the table can go stale, handled by the validity and max-age rules.

### Approach B: Client-side calculator with a small script

The gated page ships a small script that computes in the browser from embedded numbers, then posts the final selection.

- For: instant feedback, no Worker compute route.
- Against: it breaks the "no script on the gated page" property that the current CSP and tests rely on, exposes the creator's formula inputs in the page source, and the posted total can be forged unless the Worker recomputes anyway. Not recommended.

### Approach C: Owner-only quotes, no public flow

The brand uses the existing inquiry form; the owner builds a quote locally with the calculators and sends the PDF made by the existing offline renderer.

- For: smallest change, no new Worker surface, no new stored data, no stale table.
- Against: the brand gets no instant range, which is the thing that makes them take the next step; the owner still answers every inquiry by hand.
- It is the right **base**: it is the same code Approach A needs on the owner side (formulas, rate card, quote document), and it should land first.

### Recommendation

Build C's owner-side core first, then add A's public flow on top, with no B. Template-mode outreach and the pipeline come with C; the AI layer comes last and only after the caps, ledger and data-class tests exist.

## 10. Text sketches

### 10.1 The sponsor page (gated)

```
[ Example Creator   Work with me ]                         (gated, no script)

Build a package
  Pick what you want. Prices are ranges; the owner confirms the final number.

  LIVE                                   unit: per sponsored hour
  [ ] Twitch   integration   hours [2]   avg viewers 320 (API, read 2026-11-01)
  [ ] Kick     integration   hours [2]   avg viewers 140 (API, read 2026-11-01)

  VIDEO
  [ ] YouTube  dedicated video   qty [1]   median views 14,200 (API, 2026-11-01)
  [ ] Shorts / Reels / TikTok    qty [1]

  AUDIO AND TEXT
  [ ] Podcast host-read          qty [1]
  [ ] Newsletter placement       qty [1]
  [ ] Mastodon / Bluesky post    qty [1]   (creator-set price, no market benchmark)

Terms
  Usage rights      (o) none  ( ) 30 days  ( ) 60 days  ( ) 90 days
  Exclusivity       (o) none  ( ) category 30 / 60 / 90 days
  Whitelisting      [ ]      Clip rights   [ ]      Rush (< 7 days)   [ ]
  Flight dates      [start] [end]       Category [Gaming hardware v]

Your details
  Name [          ]  Email [          ]  Brand [          ]  Budget (optional) [   ]
  Notes [                                              ]            ( hidden honeypot )

  [ See my quote ]
```

### 10.2 The quote page and printable PDF

```
QUOTE  ref K7F3Q9X2        Issued 2026-11-20     Valid until 2026-12-20
From: Example Creator                 To: Example Brand, Pat Example
Currency: USD       (quote reference only, not an invoice number)

Line                         Basis                         Range
Twitch live integration x2h  320 avg viewers x 2h x        $512 to $960
                             $0.80 to $1.50 per viewer-hr
Kick live integration x2h    140 avg viewers x 2h x        $224 to $420
                             $0.80 to $1.50 per viewer-hr
YouTube dedicated video x1   14,200 views / 1,000 x        $426 to $1,136
                             $30 to $80 CPM
  Usage rights, 60 days      +20% to +50% of lines above    $232 to $1,258
  Multi-platform package     -15% low end, -10% high end   -$209 to -$377   (Invented)
TOTAL RANGE                                                $1,185 to $3,397

Where the numbers come from
  Average viewers: API, read 2026-11-01.  Median views: API, read 2026-11-01.
  Rates: Convention. Sources: streamscheme.com (2025-12-26), trykarat.com,
  later.com (2026-07-07). Modifiers marked Invented are the creator's judgment.
  Ranges, not offers. The owner confirms a final price in writing.

Audience (follows, not people)
  3 platforms, 18,400 follows. Largest single audience 9,100.
  Unique people are between 9,100 and 18,400.

Terms
  Deposit 50% on acceptance (creator-set)   Payment terms net 30
  Disclosure: both parties agree the sponsored content carries a clear
  disclosure (for example "ad" or "sponsored") in the content itself and the
  platform's paid-partnership setting where one exists.
  Tax, if any, is added on the invoice. Forms are exchanged outside this page.

Check the current rules before relying on this quote.        [ Print or save as PDF ]
```

(All figures above are made up for illustration.)

## 11. Risks and cautions

These are cautions to check with a qualified person, not advice. The tool prints a short "check the current rules" note beside each related feature.

- **FTC and disclosure (US).** Disclose a material connection to a brand; disclosures must be hard to miss and in the content itself, repeated in live streams so late joiners see it; use words like "ad" or "sponsored", not "sp", "collab" or a stand-alone "thanks". VERIFIED: https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers (dated November 2019, read 2026-11-19). A platform's built-in tool may not be enough; the FTC says add your own: https://www.ftc.gov/business-guidance/resources/ftcs-endorsement-guides-what-people-are-asking (VERIFIED, 2026-11-19). The quote carries a disclosure clause by default (wording Invented).
- **Fake indicators.** The FTC's 2024 rule bars buying or selling followers or views generated by bots for commercial use when the buyer knew or should have known (in effect 2024-10-21). VERIFIED: https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers. Implication (INFERRED): every number on the quote carries its trust label, and a typed or estimated number is never shown as platform-verified.
- **Tax.** The tool never computes tax. Awareness only: 1099-NEC threshold $2,000 or more for tax years beginning after 2025 (VERIFIED, https://www.irs.gov/instructions/i1099mec); 15.3% self-employment tax and an estimated-tax threshold (VERIFIED, https://www.irs.gov/taxtopics/tc554); EU cross-border B2C digital services threshold and UK registration threshold exist (VERIFIED: https://vat-one-stop-shop.ec.europa.eu/one-stop-shop_en and https://www.gov.uk/vat-registration-thresholds). Sponsorship income is the creator's to report; the invoice tool handles tax lines.
- **Platform terms.** YouTube requires the paid-promotion checkbox for sponsored videos (VERIFIED, https://support.google.com/youtube/answer/154235). TikTok, Twitch, Kick and Meta branded-content rules were not verified from the platforms' own pages (INFERRED or not found). Rules against steering viewers to off-platform payments were not found for any platform. The tool therefore must not claim compliance for any platform.
- **Freelancer payment laws.** New York's Freelance Isn't Free Act is statewide from 2024-08-28 (VERIFIED, https://dol.ny.gov/freelance-isnt-free-act); its dollar threshold and default payment term were not on that page (INFERRED). EU late-payment rules are VERIFIED under Directive 2011/7/EU. Other jurisdictions not researched.
- **Privacy.** The new stored row holds a brand contact's name and address. It has a retention, a pull-and-delete path, a delete command and a privacy-page entry. A chat service keeps alert copies under its own rules.
- **Abuse of the page.** A quote request is a free-text-bearing form on a gated page: covered by the inquiry form's controls plus the id-only recompute. The hostile-text and alert-formatting tests extend to the new event.
- **Stale or misleading ranges.** Source tables disagree by up to 10x; the page prints that. A number older than the max age is hidden.
- **AI cost and leakage.** Section 8: caps, kill switch, data classes, no visitor data, output untrusted.
- **Threat model.** The new Worker routes, the D1 table and the new alert events need an entry in `docs/threat-model` and an adversarial review before they ship (security work runs on the security review tier).

## 12. Owner decisions (recommended answer first)

1. **Quote and invoice boundary.** (a) Recommended: quotes in Reach Around, invoices stay in the accounting tool, hand-off by CSV or JSON export. (b) Build a simple invoice PDF here too (section 6.3 lists the cost). (c) No quote builder either.
2. **Where the sponsor page lives.** (a) Recommended: a section of the gated kit, public page links to the gate only. (b) Public page with starting-at prices. (c) No public sponsor page; owner-side quotes only (Approach C alone).
3. **Brand-visible output.** (a) Recommended: a low-to-high range per line with formulas and labels. (b) "Starting at" only. (c) A single number.
4. **Store quote requests on the Worker (30 days, pull and delete).** (a) Recommended: yes. (b) No storage; the alert carries everything (thin and lossy, caps details at the alert limit).
5. **Pull the pipeline to a local store.** (a) Recommended: yes, local only. (b) Keep it in D1.
6. **Bundle discount for multi-platform packages: 10% to 15%, Invented.** (a) Recommended: yes, shown as Invented and owner-editable. (b) No automatic discount; the owner prices bundles by hand.
7. **Niche premium multiplier x1.25 to x2, Invented.** (a) Recommended: yes, owner-set, labeled "Invented: your judgment". (b) No premium field.
8. **Fediverse and written-post lines are creator-set flat prices with a "no market benchmark" label.** (a) Recommended: yes. (b) Derive a price from engagements with an Invented formula.
9. **Outreach drafts are text only; nothing is ever sent by the tool.** (a) Recommended: yes, permanently. (b) Add a send function later behind a per-message confirmation.
10. **Reminders go to the owner's own alert channels only, daily summary.** (a) Recommended: yes. (b) Terminal listing only, no alerts.
11. **AI layer.** (a) Recommended: build it last, off by default, local first, with the cap and ledger before any hosted call. (b) Skip AI entirely. (c) Hosted-first.
12. **Cap defaults when AI is turned on.** (a) Recommended: a small default per provider (5 USD a month, 0.10 USD a call), owner raises it deliberately, no default higher than that. (b) No default; the owner must type a cap before the feature starts.
13. **May gated kit text (rates, packages) go to a hosted model?** (a) Recommended: no (default false); only public kit data and the brand name and category. (b) Yes, with the opt-in flag.
14. **Quote validity and reminder defaults** (30-day validity, follow-ups at 3 and 10 days, at most two). (a) Recommended: yes, all Invented and editable. (b) Different numbers (owner types them).
15. **Currency.** (a) Recommended: one currency per quote, chosen by the owner, no conversion. (b) Live conversion (needs a third-party call; not recommended).
16. **Transaction fees.** (a) Recommended: 0% always, written in the README. (b) Decide later.

## 13. Rough slice order

Each slice is its own issue with a card, tests first, and the usual review. Security-relevant slices (3, 4, 7) get the adversarial review before merge.

1. **Rate card schema and formulas (owner-side).** Config model, the section 5.3 formulas, ranges with sources and labels, the blended package rule, `reach-around rates draft`, and the offer check. Pure functions, heavy tests, no I/O.
2. **Quote document and owner PDF.** The quote model, the HTML and print stylesheet, and the PDF through the existing offline renderer. Local only (Approach C).
3. **Pipeline and reminders (local).** The store, stages, deadlines, exclusivity conflict check, the export to CSV or JSON, and `reach-around deals due`. Plus the `deal_reminder` alert event.
4. **Build-time price table and sponsor page (no Worker route yet).** The gated section renders, the table is emitted, stale-table behavior, and the page is checked for no script and no third-party request.
5. **Worker quote routes and D1 row.** Recompute from ids, `quote_requested` alert, retention, pull and delete command, privacy-page entry, threat-model entry. Adversarial review.
6. **Template-mode outreach.** Draft generator, the "numbers must come from the kit" check, the cautions note.
7. **AI foundation.** The ledger, cap, pre-flight reservation, kill switch, `ai usage`, data-class tests and the hostile-string test. No provider calls yet.
8. **AI providers.** Local endpoint first, then the two hosted providers behind the cap, model-mode drafts, and the output check. Adversarial review of the key handling and the prompt boundary.
9. **Docs.** A `docs/sponsor.md` guide, config reference entries, and the education page on why ranges and labels.

Slices 1 to 3 give the owner something usable with no new attack surface. The public flow arrives only after they are in use.

## 14. What this spec could not verify

- Current cold-outreach law (CAN-SPAM and EU or UK consent rules): not researched; INFERRED only.
- Whether OpenAI and Anthropic each offer an account-level spend limit and how it behaves: not verified here; the design relies only on the code cap.
- Provider per-token prices: deliberately not stated; the owner enters them from the provider's own page.
- Twitch, TikTok, Kick and Meta branded-content rule text, and platform rules on off-platform payments: not found on the platforms' own pages.
- Twitch's default 50/50 subscription split and clip-rights and in-stream-mention fractions: secondary sources only.
- Collabstr's sample-based averages: a search snippet only; its report returned a 403, so it is not used for any default here.
- Which Chromium print behavior the brand's browser will have for the printable quote: not tested; a visual check is part of slice 4.
- Per-tenant hosting cost and anything about a hosted plan: out of scope here.
