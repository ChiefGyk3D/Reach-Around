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

The boundary is structural. The rule for what is public is the display policy
table in `reach_around/config/kit.py`; the public page is rendered from
`public_projection` and the gated page from `gated_projection`, so a template
cannot reach a gated field, and the two pages go to two separate directories.

What the build enforces:

1. **Separate roots, checked by filesystem identity.** The gated directory may
   not be the public output, inside it or around it, however it is spelled:
   links and other aliases of one directory are compared by device and inode,
   not by name. The build also refuses a public output that already holds an
   entry named `kit.html`.
2. **Safe writes.** The gated directory is created owner-only. The page is
   written to a new temporary file there and renamed into place after the public
   tree is written, so a failure to write it leaves the public tree as it was. A
   symbolic link at `kit.html` in the gated directory is refused, never written
   through.
3. **A search as defense in depth.** Before anything is written, each gated
   value of at least 6 characters (partner text, case study text, rate text,
   demographic text and dates) is searched for in every public file: as written,
   HTML-escaped, and in a canonical form: entities, percent-encoding and JSON,
   JavaScript (`\x`, `\u`) and CSS escapes decoded until the text stops changing
   (at most 10 rounds), tags removed, Unicode NFKC, case and whitespace ignored.
   Known text types (html, css, js, json, xml, svg, txt, webmanifest, map,
   `_headers`, `_redirects`) are always canonicalized; one that holds a NUL byte
   or invalid UTF-8 fails the build, naming the file and the reason. Any other
   file is sniffed by content: valid UTF-8 with no NUL byte near the start is
   canonicalized; otherwise (an image, a font) it is searched for the raw bytes of
   each value in UTF-8 and UTF-16, which finds a value stored in metadata or a
   name table. The search has
   work limits (text size per file and in total, and number of gated values);
   over a limit the build stops with an error that names the limit. A
   hit stops the build and names the field, not the text. A value that is also
   public by policy (the kit title and description, a metric label or date) is
   not a leak and is skipped.
4. **No remote requests from the gated page.** The page and each local
   stylesheet it links are checked: a build fails, naming the file or element
   and never the address, if the page or a stylesheet imports anything or names
   an `http(s)` or protocol-relative target (a remote theme background image
   counts), or if the page has a request-bearing element pointing at another
   host. Use local files.

What is **not** guaranteed: the search cannot catch every transformation a
custom template could apply (base64 and other arbitrary transforms are out of
scope), and values under 6 characters are not searched. Those rest on the
structure in points 1 and 2. If you use an override template for the kit pages,
review it as code that handles gated-adjacent text. If the same wording must
appear on a public page, reword one of them.

### Trust assumptions

- The gated directory must be owner-only. An existing directory that group or
  others can enter is refused; the build removes only its own leftover staging
  files (`.kit.html.<16 hex>.tmp`) from it.
- The directory that holds `<out>.gated.json` is a trusted state directory. Someone
  who can remove the record can defeat the stale-page check and leave an old
  gated page in place.
- Replacing a parent directory of either root while a build runs is out of scope:
  this is a local build tool run by the owner, not a service.
- The public tree is rewritten in place, not atomically. If a build fails part
  way, the public tree may be partial: rebuild before publishing. If the final
  rename of the gated page fails, or recording the destination fails, the new
  public output can sit beside the previous gated page: rebuild.

### Removing a gated page

`reach-around build` records where it wrote the gated page, in
`<out>.gated.json` beside `--out` (never inside it, so never published). While
that record exists, every build must pass `--gated-out` with the same
directory; without it the build stops with an error and changes nothing. A build
whose kit no longer has gated content then removes the old `kit.html` and
deletes the record, after which plain builds work again. If you moved the gated
directory on purpose, delete the old page and the record first. Code that calls
`build_site` directly has no record and must pass the old directory itself.

### The gated page and requests

The gated page makes no third-party request. A remote (`https://`) creator
avatar is left out of it; the public pages keep the avatar, like every other
page. Use a local avatar file if you want it on the gated page too.

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
