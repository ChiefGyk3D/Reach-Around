<!-- SPDX-License-Identifier: Apache-2.0 -->
# Media kit

The media kit is two pages for two audiences. The public page shows your name,
a short description and your headline numbers to anyone. The gated page holds
the detail you only want to show people who pass a short check: past partners,
case studies, rates and audience details.

Everything here is synthetic: a fake creator, `example.com` and made-up brands.
Replace them with your own.

## What you write

Add a `kit` section to `site.yaml`. The full field list is in the
[config reference](config.md#kit); the short version:

```yaml
features:
  kit_gate: true
kit:
  title: Example Creator media kit
  about: A fake creator who streams games and talks about gear.
  turnstile_site_key: 0x4AAAAAAAexampleKey
  metrics:
    - {platform: twitch, label: Followers, value: 1200, as_of: 2027-03-01}
    - {platform: youtube, label: Subscribers, source: counts}
  partners:
    - {name: Acme Fake Co, note: 2026 sponsor, url: "https://example.com/acme"}
  case_studies:
    - title: Launch week
      summary: A fake product launch on stream.
      result: Ten thousand fake views.
  rates:
    - {item: Sponsored video, price_text: Ask me!}
  demographics:
    - {label: Top country, value: Exampleland 40%, as_of: 2027-03-01}
```

## What the build writes

| File | Where | Who sees it |
| --- | --- | --- |
| `kit/index.html` | `--out` (published) | everyone |
| `kit/retry/index.html` | `--out` (published) | everyone; only when the kit has gated content |
| `kit.html` | `--gated-out` (never published) | a visitor who passed the gate, served by the Worker |

```sh
reach-around build --config site.yaml --out site-out --gated-out gated-out
```

`--gated-out` is required when the kit has partners, case studies, rates or
demographics. It may not be the output directory, inside it, or a parent of it.
The build writes only `kit.html` there (and removes it if the kit no longer has
gated content); anything else in that directory is left alone. Upload the
gated file with the Worker deploy, never with the Pages deploy: the deploy
publishes only `--out`.

### Public page

Header (the kit title and description), the headline numbers each with the date
it was read, and the unified total. The total adds up the numbers that are not
marked `in_total: false`; it says how many numbers it adds up and the date of
the oldest one, and notes that people who follow on several platforms are
counted more than once. A number with no value is left out. With nothing to add
there is no total line.

If the kit has gated content, the page explains the gate and shows the form.

### The gate form

The form is plain HTML that works with no script on your side. It has:

- `email`: a required email field with a visible label;
- `website`: a honeypot, kept off screen and out of the tab order. A real visitor
  never fills it; the Worker should treat any value as a bot;
- an empty `div` with class `cf-turnstile` and `data-action="gate"`, the slot
  for the Turnstile widget. It carries `data-sitekey` when
  `kit.turnstile_site_key` is set. The Turnstile response is added to the form by
  Turnstile's own script as `cf-turnstile-response`.

There are no hidden fields. The form posts to a placeholder action
(`/kit/retry/`) until the Worker route exists (D1, #263); a post there lands on
the retry page, which is the fail-closed behavior. When the route ships, one
constant (`GATE_ACTION` in `reach_around/site/kit.py`) becomes `/k/gate`.

The page says plainly that the address is not checked and nothing is sent to it,
and links to the privacy page, which lists what the gate collects and for how
long.

**Turnstile is not loaded yet.** The widget needs Cloudflare's script and a
narrow Content-Security-Policy exception for the kit pages. That is a separate
task (Plan 3, Task 2A) so the policy is widened in one reviewed place. Until it
lands the build makes no third-party request, `_headers` is unchanged and the
widget slot stays empty.

### Retry page

`/kit/retry/` is fixed text and the same form. It never says why a try failed,
so it reveals nothing about the check. It is marked `noindex`. There is no
`/kit/sent/` page: it belongs to the emailed link, which is not part of v1
(`kit.unlock_link: true` is refused with an error until it exists).

### Gated page

A complete page (header, navigation, the same numbers, then partners, case
studies, a rates table and audience details), marked `noindex`, with root-based
asset links (`/static/...`) because the Worker serves it from any path. Partner
links open with `rel="noopener noreferrer"`. It contains no script and no form.

## The wall between the two

The rule for what is public is the display policy table in
`reach_around/config/kit.py`; the public page is rendered from
`public_projection` and the gated page from `gated_projection`, so a template
cannot reach a gated field. Three build-time checks back that up:

1. the gated directory cannot be, contain or sit inside the public output;
2. no public file may be named `kit.html` at the site root (the gated file name);
3. before anything is written, every gated string of at least 6 characters
   (partner name, note and URL; case study text; rate text; demographic text) is
   searched for in every public file, as written and HTML-escaped. A hit stops
   the build and names the field (`kit.partners[0].name`), not the text.

Consequence of check 3: if the same wording must appear on a public page (a
partner you also name on a link), reword one of them. Short strings under 6
characters are not searched, since a short word would match ordinary text; they
are still kept out by the structure, not by the search.

An override template for `kit.html` is still checked: if it prints a gated
string, the build fails.

## Old numbers

A number older than 30 days, a date in the future, and a `counts` metric the
numbers file does not hold each produce a build warning. `reach-around build`
prints them as `warning: ...` on standard error and the build still succeeds.
A kit with `features.kit_gate: true` but nothing gated warns that no form is
built, and a gated kit with no `turnstile_site_key` warns about the empty slot.

## Accessibility and privacy

- Landmarks: one `main`, a skip link, a `nav` when there are several tabs, and
  a footer. One `h1` (your name), then `h2` and `h3`; no levels are skipped.
- Every input has a visible label tied to it by `for`/`id`. Tables use
  `th scope`, a hidden caption, and `time` elements for dates.
- Text, borders and buttons read the same `--ra-*` color tokens as the rest of
  the site, so your theme's contrast choices apply. Targets are at least 44 px.
- No inline script, no inline style, no event attributes, no third-party
  request, no cookie set by the static pages. The privacy page (generated from
  `features.kit_gate`) covers what the Worker collects once the gate is live.
