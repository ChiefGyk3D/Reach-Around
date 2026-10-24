# Config reference

A Reach-Around site is described by one YAML file, usually `site.yaml`. The
loader reads it with strict rules (see "What the loader refuses"), then the
models check every field (see "Fields"). Any failure is reported as one error
that names the file, and, where it is known, the line or the dotted key path.

This is a complete, valid config. The test
`tests/test_docs_examples.py::test_config_doc_example_validates` loads this
first block, so it cannot drift from the code. All names and URLs are made up.

```yaml
version: 1
timezone: America/New_York
creator:
  name: Example Creator
  avatar: https://example.com/avatar.png
  socials:
    - name: Example Video
      url: https://video.example.com/@example
      icon: video
disclosure: This page uses affiliate links. Example Creator may earn a commission.
tabs:
  - id: start-here
    title: Start Here
    blocks:
      - id: home-page
        title: Example Home Page
        subtitle: The main site
        url: https://example.com/
      - id: gear-picks
        title: Gear Picks
        subtitle: Things used on the show
        collapsed: true
        links:
          - id: example-mic
            title: Example Microphone
            url: https://shop.example.com/mic?ref=example
            affiliate: true
          - id: example-stand
            title: Example Boom Stand
            url: https://shop.example.com/stand
  - id: events
    title: Events
    blocks:
      - id: spring-meetup
        title: Spring Meetup Tickets
        url: https://tickets.example.invalid/spring
        show_from: 2027-03-01
        show_until: 2027-04-15
```

Another full file lives at `examples/instance/site.yaml`.

Every model rejects keys it does not know and is read-only once loaded. A typo
such as `titel:` is an error, not a silently ignored line.

## Shared types

| Name | Rule |
| --- | --- |
| Title | text, 1 to 200 characters |
| Subtitle | text, 1 to 500 characters |
| Slug | `^[a-z0-9][a-z0-9-]{0,39}$`: lowercase letters, digits and hyphens, starts with a letter or digit, 1 to 40 characters |
| HTTPS URL | at most 2048 characters; scheme must be `https`; must have a host; no spaces or control characters; no user name or password; valid port if one is given |
| Tab link | `tab:<tab-id>` or `tab:<tab-id>#<fragment>`, lowercase scheme; the tab must exist; fragment 1 to 40 characters of `a-z0-9-`. Allowed instead of an HTTPS URL on a link `url` and a social `url` (not with `affiliate`). See [customizing](customizing.md#linking-to-another-tab) |
| Date | `YYYY-MM-DD`, written either as a plain YAML date or a quoted string. Timestamps such as `2027-02-01 10:00:00` are refused |

Text is stored as written. HTML in a title is not stripped; the page builder
escapes it when it renders.

## Fields

### Top level (`SiteConfig`)

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `version` | integer | required | must be exactly `1` |
| `creator` | Creator | required | see below |
| `tabs` | list of Tab | required | at least one tab |
| `disclosure` | text | none | at most 2000 characters; shown when affiliate links exist |
| `timezone` | text | `America/New_York` | must be a known IANA zone name; decides which calendar day "today" is for scheduled links |
| `title_template` | text | `{tab} - {creator}` | how page titles are built; see [Page titles](#page-titles) |
| `theme` | Theme | defaults | colors, fonts, background and shape; see [`theme`](#theme) |
| `features` | Features | click tracking on, kit gate off | switches for features that collect data; see [`features`](#features) |
| `privacy` | Privacy | defaults | wording and retention for the privacy page; see [`privacy`](#privacy) |
| `robots` | Robots | absent | optional; when present the build writes `robots.txt`; see [`robots`](#robots) |

Ids must be unique across the whole site: every tab id, every block id (links
and sections) and every link id inside a section share one pool. Reusing an
id anywhere is refused, with the message `duplicate id 'x': ids must be unique`.

```yaml
timezone: Europe/Berlin
disclosure: Some links are affiliate links.
```

### `creator`

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Title | required | |
| `title` | page title text | none | The whole-site title; see [Page titles](#page-titles). |
| `avatar` | HTTPS URL or local image path | none | A relative path such as `media/avatar.png` is copied from the instance directory to the same path in the site. |
| `favicon` | local image path | none | The browser tab icon. Same rules as a local `avatar`; an https URL is refused. |
| `header` | `compact`, `centered`, `hero` | `compact` | See [layouts and headers](#blocks-layouts-and-headers). |
| `socials` | list of Social | empty list | |

```yaml
creator:
  name: Example Creator
  avatar: media/avatar.png
  favicon: media/favicon.png
```

### `creator.socials[]` (`Social`)

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Title | required | |
| `url` | HTTPS URL or Tab link | required | |
| `icon` | Slug | required | |

```yaml
socials:
  - name: Example Chat
    url: https://chat.example.invalid/example
    icon: chat
```

### `tabs[]` (`Tab`)

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `id` | Slug | required | unique across the site |
| `title` | Title | required | the label in the navigation |
| `page_title` | page title text | none | Replaces the whole browser title of this tab's page; see [Page titles](#page-titles). It is not called `title` because that key is the navigation label. |
| `blocks` | list of Link or Section | required | may be empty |

A block is a section if it has a `links` key, otherwise a link.

```yaml
tabs:
  - id: socials
    title: Socials
    blocks: []
```

### Link (a block, or inside a section)

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `id` | Slug | required | unique across the site |
| `title` | Title | required | |
| `url` | HTTPS URL or Tab link | required | A Tab link is same-site: no `rel`, `target` or click ping, and it is left out of `manifest.json` |
| `subtitle` | Subtitle | none | |
| `icon` | Slug | none | |
| `affiliate` | boolean | `false` | |
| `show_from` | Date | none | first day the link shows |
| `show_until` | Date | none | last day the link shows; must not be before `show_from` (the same day is fine) |

```yaml
- id: spring-meetup
  title: Spring Meetup Tickets
  url: https://tickets.example.invalid/spring
  show_from: 2027-03-01
  show_until: 2027-04-15
```

### Section (a block that groups links)

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `id` | Slug | required | unique across the site |
| `title` | Title | required | |
| `subtitle` | Subtitle | none | |
| `collapsed` | boolean | `true` | start folded |
| `links` | list of Link | required | sections do not nest |

```yaml
- id: gear-picks
  title: Gear Picks
  collapsed: false
  links:
    - id: example-stand
      title: Example Boom Stand
      url: https://shop.example.com/stand
```

## Page titles

The text in the browser tab (the HTML `<title>`) is built per page.

Page title text (`creator.title`, `tabs[].page_title`) is plain text, 1 to 80
characters. It must be a real string (a number or `true` is refused), must not
be empty or only spaces, must not start or end with a space, and must not
contain control characters, line breaks or invisible format characters.
`title_template` follows the same rules with a 200 character cap.

`title_template` may use exactly three placeholders and nothing else:

| Placeholder | Value |
| --- | --- |
| `{tab}` | the tab's `title` (`Privacy` on the privacy page) |
| `{creator}` | `creator.name` |
| `{site}` | `creator.title`, or `creator.name` when `creator.title` is not set |

Any other brace, such as `{nope}`, `{tab.x}`, `{0}` or `{{tab}}`, is refused
when the file loads, with an error that names `title_template`. The
substitution is a plain token replace, not Python formatting, and a value that
itself contains `{tab}` is left as written.

Which text wins, first match first:

1. The tab's `page_title`, used as is.
2. `title_template`, when set, for every page.
3. `creator.title` alone, when set, on the first tab (the home page). Other
   tabs and the privacy page use `{tab} - {site}`.
4. Otherwise `{tab} - {creator}`, the same as before this option existed.

```yaml
creator:
  name: Example Creator
  title: Example Creator Links
title_template: "{tab} | {site}"
tabs:
  - id: start-here
    title: Start Here
    page_title: Welcome to Example Creator Links
    blocks: []
```

Titles are escaped when the page is built, so `<script>` shows as text. They
are never marked safe and are never written into the CSS or JavaScript.

## Blocks, layouts and headers

The block types, layout options and header variants added in Plan 1, Task 6C.
Every example uses synthetic data.

### Block types

A tab's `blocks` list can mix these. The type is picked by the keys the block
has, and every `id` must be unique across the whole site.

| Block | Keys | Notes |
| --- | --- | --- |
| link | `id`, `title`, `url` | as before |
| section | `id`, `title`, `links`, optional `layout` | as before |
| text | `id`, `markdown` | up to 5000 characters |
| image | `id`, `src`, `alt`, optional `link` | `alt` is required |
| crypto | `id`, `coins`, optional `title`, `note` | addresses to copy; see [Crypto](#crypto) |
| divider | `id` only | a horizontal rule |

Text, image, crypto and divider blocks have no `show_from` or `show_until`; they always
show.

```yaml
blocks:
  - id: welcome
    markdown: |
      Welcome. **Bold**, *italic* and a [link](https://example.com/).

      - first
      - second
  - id: banner
    src: img/banner.png
    alt: A banner with the show logo
    link: https://example.com/
  - id: rule
```

#### Crypto

A list of public addresses people can copy, for a Support tab. Each entry is a
card with the coin icon, a name, the address in selectable monospace text, a
`Copy` button and, if you give one, a QR image inside a native
`<details>` that stays closed until opened.

```yaml
blocks:
  - id: crypto
    title: Send crypto
    note: Check the first and last characters of the address before you send anything.
    coins:
      - coin: btc
        network: Bitcoin mainnet
        address: bc1qexampleexampleexampleexampleexample
        qr: media/qr-example.png
      - coin: other
        name: Litecoin
        address: ltc1qexampleexampleexampleexampleexample
```

A runnable version with synthetic addresses is in `examples/crypto/`.

| Key | Rule |
| --- | --- |
| `id` | a slug, unique across the site |
| `title` | optional heading, plain text, up to 200 characters |
| `coins` | 1 to 12 entries |
| `note` | optional, plain text, up to 300 characters. It replaces the default "Check the first and last characters of the address before you send anything." The note always shows once, under the list |
| `coins[].coin` | `btc`, `xmr`, `eth`, `sol` or `other` |
| `coins[].name` | required for `other`, refused for the others; up to 40 characters |
| `coins[].label` | optional display name, up to 60 characters; defaults to the coin name |
| `coins[].network` | optional, plain text, up to 40 characters, such as `ERC-20 on Ethereum` |
| `coins[].address` | required string of 20 to 128 characters from `A-Z a-z 0-9 : . _ -` only |
| `coins[].qr` | optional relative image path, checked like the avatar |

Text fields refuse control characters and hidden or bidirectional formatting
characters. An address with whitespace, quotes, angle brackets, backticks,
slashes or anything outside the list above is refused with the key named, for
example `tabs[0].blocks[1].coins[0].address: address must be 20 to 128
characters...`. The address is **not** checked against a coin's checksum or
format: the page cannot know which network a string belongs to, so the note
tells visitors to check it.

`qr` goes through the same pipeline as `creator.avatar` and `theme.background`:
a case-insensitive extension allowlist, a signature and structure check
(a text file renamed `.png` is refused), the 8 MiB cap, no symlinks, and a copy
to the same relative path in the output. The key in an error reads
`tabs[0].blocks[1].coins[0].qr`.

**Security notes.** The block is static. There is no wallet code, no wallet
connection, no price lookup and no call to any outside service. Addresses are
public data. The `Copy` button needs `static/copy.js`, a small same-origin
script with no `eval`, no inline handler and no network call; it reads the
address from the page and writes it to the clipboard, nothing else. The build
adds `copy.js` and `crypto.css` only to pages that contain a crypto block, and
leaves them out of the output otherwise, so other pages are unchanged. The
script is loaded with `<script src defer>`, which the default
`script-src 'self'` allows. Without JavaScript the button stays hidden and the
address can still be selected and copied by hand. Never put a private key or a
seed phrase in the config.

#### Markdown

Only these are supported: `**bold**`, `*italic*`, `[text](https://...)` links,
`-` bullet lists, and line breaks. Everything else is shown as plain text. Raw
HTML is escaped, never passed through. A link must be `https`; `http:`,
`javascript:`, `data:` and relative links stay as plain text.

#### Images

`src` is either an `https://` URL or a relative path such as `img/photo.png`
(letters, digits, `.`, `_`, `-` and `/` only; no `..`, no leading slash, no
scheme). `alt` is required so the page works for people who cannot see the
image. `link`, if set, must be `https`.

### Layouts

`layout` is `list` (the default), `grid` or `cards`. Set it on a tab to lay out
that page's top-level blocks, or on a section to lay out its links. The same
links show in every layout; only the container class changes.

```yaml
tabs:
  - id: start
    title: Start
    layout: grid
    blocks:
      - id: gear
        title: Gear
        layout: cards
        links:
          - id: example-stand
            title: Example Boom Stand
            url: https://shop.example.com/stand
```

### Header variants

`creator.header` is `compact` (the default), `centered` or `hero`.

```yaml
creator:
  name: Example Creator
  header: hero
```

An unknown `layout` or `header` is refused when the config loads.

## `theme`

The theme changes how the page looks without letting config text reach the
stylesheet unchecked. Every value is validated when the config loads, and the
build writes the result to `static/theme.css` (an external file, so the page
needs no inline style). `static/site.css` declares a default for every token;
`theme.css` overrides only the ones you change. A config with no `theme`
section builds the same page as before. The tokens, the two ready-made presets in
`examples/themes/` and the icon slugs are listed in [design.md](design.md).

```yaml
version: 1
creator:
  name: Example Creator
theme:
  mode: dark
  colors:
    bg: "#101418"
    text: "#e8edf2"
    accent: hotpink
    surface: rgb(24 30 36)
    border: hsl(210 20% 30%)
  fonts:
    body: Georgia, "Times New Roman", serif
    heading: fonts/heading.woff2
  background: media/background.webp
  mascot: media/mascot.svg
  background_dim: 0.6
  column_width: 40rem
  radius: 1rem
  button_style: pill
tabs:
  - id: start
    title: Start
    blocks:
      - id: home
        title: Example Home
        url: https://example.com/
```

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `colors` | map | empty | Keys: `bg`, `text`, `accent`, `on_accent` (text on the accent fill), `surface`, `border`. Any other key is refused. |
| `fonts.body` | string | Jost, shipped with the engine | A system font stack or a font file path. |
| `fonts.heading` | string | same as body | Same rules as `fonts.body`. |
| `background` | string | none | The full-bleed page background, drawn under the carbon-fiber texture. A relative image path (preferred) or an `https://` URL. |
| `background_dim` | number 0 to 0.9 | `0` | A plain number (`0.6`, not `"0.6"` or `true`). Darkens the page background image with a black overlay, in CSS only. If your background image has a bright subject behind the header, raise this. The mascot is not affected. |
| `mascot` | string | none | The giant faded image behind the column. A relative image path only. |
| `column_width` | length | `35rem` (560 px) | `px`, `rem`, `em`, `ch`, `%` or `vw`. |
| `radius` | length | `0.5rem` | Base corner radius for cards and sections. |
| `button_style` | `pill`, `rounded`, `square` | `rounded` | `rounded` is the 36 px pill radius, `pill` is fully round, `square` has no radius. |
| `mode` | `auto`, `light`, `dark` | `auto` | `auto` is the stylesheet's own choice, which is dark. `light` forces the light palette. |

### What is accepted

- **Colors:** a hex color (`#fff`, `#1a2b3c`, with or without alpha), `rgb()`,
  `rgba()`, `hsl()`, `hsla()`, or a standard named color.
- **Lengths:** a number with `px`, `rem`, `em`, `ch`, `%` or `vw`, or `0`.
  `calc()`, `var()` and bare numbers are refused.
- **Fonts:** letters, digits, spaces and hyphens in a comma separated stack
  (`Georgia, "Times New Roman", serif`), or a font file (`.woff2`, `.woff`,
  `.ttf`, `.otf`) as a relative path inside the instance directory. A font
  on `https://` is refused on purpose: loading one tells that host about every
  visitor.
- **Background:** an `https://` image URL, or a relative path to a `.png`,
  `.jpg`, `.jpeg`, `.webp`, `.avif`, `.gif` or `.svg` file. A relative path
  has no `..` segment and does not start with `/`.

### What is refused

Any value that contains `;`, `{`, `}`, `url(`, `expression(`, `@import`, a
backslash escape, a comment opener (`/*`) or a newline is refused with the
key path and the reason. The check runs before the allowlist for each kind of
value, so the message names the first problem it found. Only a fixed set of
custom property names (`--ra-*`) and `color-scheme` is ever written to
`theme.css`.

## `features`

Switches for the features that collect data. Unknown keys are refused.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `click_tracking` | boolean | `true` | Count link clicks anonymously (link and country only, no IP address). When off, links carry no `ping` attribute and the page ships no `click.js`. |
| `kit_gate` | boolean | `false` | Ask for an email address to unlock a kit. This sets a cookie, so the site shows a cookie notice. |

## `privacy`

Wording and retention for the generated privacy page. Unknown keys are refused.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `contact` | text, 1-200 characters | none | How visitors can reach you about privacy. Shown as plain text. |
| `intro` | text, 1-2000 characters | neutral default | Replaces the opening text of the privacy page and the cookie notice. Escaped, never treated as HTML. |
| `retention_days` | integer, 1-3650 | `90` | How long click counts are kept. |
| `lead_retention_days` | integer, 1-3650 | `365` | How long a kit email address is kept. |

```yaml
features:
  click_tracking: true
  kit_gate: true
privacy:
  contact: privacy@example.com
  intro: Here is exactly what this site collects.
  retention_days: 90
  lead_retention_days: 365
```

The privacy page at `/privacy/` and the cookie notice are generated from the
enabled features, so they cannot claim less than the site does. See
[privacy by construction](education/privacy-by-construction.md).

## `robots`

Optional. When the `robots` key is **absent**, the build writes no `robots.txt`
and the output is byte-for-byte what it was before this key existed. When it is
present (even as `robots: {}`), the build writes `robots.txt` at the site root.
Unknown keys are refused; there is deliberately no free-form `extra_rules`.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `allow_all` | boolean | `true` | `true` writes `User-agent: *` with `Allow: /`; `false` writes `Disallow: /` for everyone. Must be a real boolean. |
| `block` | list of tokens | empty | User-agent tokens to disallow entirely. Each is 1-60 characters from `A-Z a-z 0-9 . _ * -`, at most 100 tokens, no duplicates (case-insensitive). A bare `*` is refused: use `allow_all: false`. |
| `ai_crawlers` | `allow` or `block` | `allow` | `block` also disallows every token in the packaged list `src/reach_around/site/data/ai_crawlers.txt`. |
| `sitemap` | boolean | `false` | Sitemaps are not generated; only `false` is accepted. |
| `noindex_paths` | list of paths | empty | Path prefixes that get `X-Robots-Tag: noindex` in `_headers`. At most 50. Each starts with `/` and uses plain segments (`A-Z a-z 0-9 . _ ~ -`), at most 200 characters; no wildcards, query, `.` or `..` segments, and not `/` alone. `/private` covers `/private` and everything under it. |

```yaml
robots:
  ai_crawlers: block
  block: [ExampleBot]
  noindex_paths: [/thanks]
```

The output is sorted, ASCII and deterministic, and starts with a comment saying
it is advisory. A path in `noindex_paths` is deliberately not also disallowed in
`robots.txt`: a crawler must be allowed to fetch a page to see its `noindex`.

**Honest limits.** `robots.txt` is advisory. It asks well-behaved crawlers to
stay away; it is not access control and stops nobody who ignores it. The AI
crawler list is data copied from each operator's public documentation on the
date noted in the file, marked "verify before relying", and it will go stale.
It leaves out user-initiated fetchers, which some operators say may bypass
`robots.txt`. Blocking bots at Cloudflare is a separate step in the Cloudflare
dashboard that this tool neither performs nor checks: see Cloudflare's current
documentation for what is available on your plan.

## What the loader refuses

`load_yaml` and `load_site_config` raise `ConfigError` (it carries `path`,
`line` and `message`) for all of these. Nothing is partly loaded.

| Input | Message contains |
| --- | --- |
| symlink as the file path | `refusing to follow a symlink` |
| not a regular file (directory, pipe) | `not a regular file` |
| missing or unreadable file | `cannot read file` |
| file over 1,048,576 bytes (1 MiB) | `file too large` |
| invalid UTF-8 | `not valid UTF-8` |
| empty or comment-only file | `file is empty` |
| more than one YAML document | a YAML error naming the line |
| duplicate key in one mapping | `duplicate key` |
| anchor (`&name`) | `anchors are not allowed` |
| alias (`*name`) | `aliases are not allowed` |
| merge key (`<<`) | `merge keys (<<) are not allowed` |
| any tag such as `!!python/object` or `!custom` | `tag not allowed` |
| nesting deeper than 64 levels | `nesting is deeper than 64 levels` |
| a mapping key that is not hashable | `mapping key is not hashable` |
| bad YAML syntax | the parser's message, with the line |

After the file loads, the models refuse a root that is not a mapping, unknown
keys, wrong types, non-HTTPS or credentialed URLs, bad slugs, duplicate ids,
reversed date windows, timestamps in date fields, unknown time zones, an
empty `tabs` list and bad page titles or title templates. Messages from the models name the dotted path, for example
`tabs[0].blocks[0].url: url must start with https://`.

The reasons behind these rules, and the test that proves each one, are in
[docs/education/config-hardening.md](education/config-hardening.md).

## Instance overrides and security headers

`reach-around build` and `reach-around serve` take `--overrides DIR`. The
directory is optional and holds only these two things:

| Path | What it does |
| --- | --- |
| `DIR/templates/NAME.html` | Replaces the packaged template of the same name. A name you do not override falls back to the packaged one. |
| `DIR/custom.css` | Written to `static/custom.css` and linked after `theme.css`, so it wins ties. |

A missing or non-directory `DIR` stops the build with a clear error. `DIR`
itself, `DIR/templates` and `DIR/custom.css` must not be symlinks; a symlink
there is refused.

Template lookups are confined to `DIR/templates`. A name with `..`, an
absolute path, a backslash, or a file or directory symlink that points outside
that folder is refused with an error, not skipped. Templates run in a Jinja
sandbox: reaching into Python internals (`__class__`, `__globals__`, `open`,
`os`) is refused and the build stops with a one-line error.

`custom.css` is refused (the build stops and names the reason) when:

- it is larger than 256 KiB or is not UTF-8;
- it contains a backslash anywhere (CSS escapes such as `\75rl(` can hide
  keywords from the check, so none are allowed; write the characters
  directly);
- it has a `/*` comment that is never closed. Real `/* ... */` comments are
  removed first, so a forbidden word inside a comment is fine and the same
  word outside one is not;
- it contains `@import`, `expression(`, `behavior:`, `-moz-binding`,
  `image-set(`, `src(` or the text `javascript:`;
- any `url(...)` is not a relative same-site path (no scheme, no `//`, no
  whitespace or control characters). For remote art use an Image block or the
  theme `background`. This is a privacy choice: a remote `url()` in CSS would
  tell a third party every visitor's IP address. The page policy still allows
  `img-src 'self' https:` because an Image block or avatar may use an https
  URL by your explicit choice.

Custom JavaScript is not supported. Any `.js`, `.mjs` or `.cjs` file under
`DIR` is ignored and the build logs a warning naming it. See
[docs/education/csp-and-overrides.md](education/csp-and-overrides.md) for why.

### `_headers`

Every build writes a Cloudflare Pages `_headers` file for `/*`:

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |

When `robots.noindex_paths` is set, each prefix also gets its own block with
`X-Robots-Tag: noindex` (see [`robots`](#robots)).

The policy has no `unsafe-inline` and no `unsafe-eval`. Pages carry no inline
style, no inline script and no inline event handler, and a test checks every
built page. A template override that adds one will not run in the browser.

For code that needs a narrow exception later (the gate page and its Turnstile
script), `reach_around.site.overrides.build_csp(extra)` and `render_headers(
path_extras)` accept extra sources for a fixed set of directives. A source
must be `'self'` or an exact `https://host[:port]` origin. `*`, `data:`,
`https:`, nonces and the `unsafe-*` keywords are refused.
