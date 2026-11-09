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
analytics:
  page_views: true
  route: /e/v/
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
| `disclosure` | text or Disclosure | none | the footer line for affiliate links; see [`disclosure`](#disclosure) |
| `timezone` | text | `America/New_York` | must be a known IANA zone name; decides which calendar day "today" is for scheduled links |
| `cutover_date` | date | absent | optional; the UTC calendar day the site went live (`YYYY-MM-DD`); see [`cutover_date`](#cutover_date) |
| `page_layout` | `tabs` or `sections` | `tabs` | `sections` puts every tab after the first on one page as native `<details>` sections; see [`page_layout`](#page_layout) |
| `title_template` | text | `{tab} - {creator}` | how page titles are built; see [Page titles](#page-titles) |
| `theme` | Theme | defaults | colors, fonts, background and shape; see [`theme`](#theme) |
| `features` | Features | click tracking on, kit gate off | switches for features that collect data; see [`features`](#features) |
| `privacy` | Privacy | defaults | wording and retention for the privacy page; see [`privacy`](#privacy) |
| `footer` | Footer | credit on | the "Made with Reach Around" link in the footer; see [`footer`](#footer) |
| `robots` | Robots | absent | optional; when present the build writes `robots.txt`; see [`robots`](#robots) |
| `redirects` | Redirects | absent | optional; `redirects.socials` writes a `_redirects` file that sends the old `/socials` path to the link page; see [`redirects`](#redirects) |
| `analytics` | Analytics | page views off | optional page-view counting; see [`analytics`](#analytics) |
| `store` | StoreConfig | SQLite at `data/reach-around.db` | where the snapshot store lives; see [`store`](#store) |
| `profile_card` | ProfileCard | none | name, topic chips and a media-kit button after the content of every page; see [`profile_card`](#profile_card) |
| `numbers` | NumbersConfig | none | where each platform's follower count comes from, for `reach-around counts refresh`; see [`numbers`](#numbers) |
| `kit` | KitConfig | absent | optional media kit content and its display policy; see [`kit`](#kit) |

Ids must be unique across the whole site: every tab id, every block id (links
and sections) and every link id inside a section share one pool. Reusing an
id anywhere is refused, with the message `duplicate id 'x': ids must be unique`.

```yaml
timezone: Europe/Berlin
disclosure: Some links are affiliate links.
```

### `disclosure`

A small line at the bottom of the page. Write it as plain text, or as a
mapping. Leave the key out and no disclosure is ever shown.

| Key | Type | Default | Constraints |
| --- | --- | --- | --- |
| `text` | text, 1-500 characters | a neutral built-in sentence | Plain text: no control, bidi or zero-width characters, no leading or trailing space, no links, `www.` hosts or `@` addresses. Escaped on the page. |
| `show` | `true` or `false` | `true` | `false` hides it without deleting the text. |
| `always` | `true` or `false` | `false` | `true` shows it on every page; otherwise only on a page with a visible affiliate link. |

The built-in sentence is: "Some links on this page may be affiliate links.
Purchases made through them may earn the creator a small commission at no extra
cost to the buyer, without affecting their recommendations." Your own wording
belongs in your config, for example:

```yaml
disclosure:
  text: Some links here are affiliate links. If you buy through them, I may earn a small commission at no extra cost to you, but it NEVER changes what I recommend.
```

### `store`

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `kind` | `sqlite` or `postgres` | `sqlite` | `postgres` is not built yet |
| `path` | text | `data/reach-around.db` | the SQLite file; created mode 0600 in a 0700 directory |
| `dsn_env` | text | none | the NAME of an environment variable holding a Postgres DSN; required for `postgres`; never a DSN or password |

Run `reach-around store status --config site.yaml` to see applied and pending
migrations and `reach-around store migrate --config site.yaml` to apply them.
A SQLite store is copied to `<file>.pre-<version>` first (three copies kept).

### `creator`

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Title | required | |
| `title` | page title text | none | The whole-site title; see [Page titles](#page-titles). |
| `description` | plain text, 1 to 160 characters | `Links and socials from <name>.` | The short text search engines show under the page title (`<meta name="description">`). Every page carries it. One line, no control characters, no leading or trailing space. |
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
| `open` | true or false | unset | Only with [`page_layout: sections`](#page_layout): whether this tab's section starts open. Unset opens the first section only. |
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
| `image` | local image path | none | A round thumbnail; wins over `icon`. Same path rules as a local `avatar`, file at most 256 KiB. See [Link thumbnails](customizing.md#link-thumbnails). |
| `image_alt` | text | empty | At most 120 characters; empty means decorative. Refused without `image`. |
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

## `page_layout`

`page_layout` is `tabs` (the default: one page per tab with a tab bar) or
`sections` (one page). It is a top-level key; a config without it builds
exactly what it built before.

With `sections` the build writes one `index.html` (plus `privacy/` and
`404.html`) and no page per tab:

- The **first tab** is the page itself, shown under the header.
- **Every other tab** becomes a native `<details>` section after it, with the
  tab title as a heading inside the `<summary>` and the tab id as the section's
  `id`. The first section is open; the rest are closed. Set `open: true` or
  `open: false` on a tab to choose (the key is ignored with `page_layout: tabs`).
  A tab whose blocks are all hidden by `show_from` or `show_until` is left out,
  and the next tab becomes the first section.
- There is no tab bar. A `tab:<id>` link points at `#<id>` (or at
  `#<fragment>` for `tab:<id>#<fragment>`); a browser opens a closed section
  that holds the fragment's target, with no script.
- No new script, style or inline attribute is added: the Content Security Policy
  is unchanged, `ping` click tracking and `rel="sponsored"` on affiliate links
  work as on a tab page, and the crypto, email and platforms assets load once
  for the page when any section uses them.

Zero-click rule for support pages: put the primary support actions (tip, Ko-fi,
Patreon, shop) as plain link blocks in the open section, so they are visible
buttons, and put only crypto and secondary groups in closed sections (a crypto
tab, or a `section` block with `collapsed: true`). Each summary is a full-width
row at least 44 px tall with the same focus ring as every other control.

```yaml
page_layout: sections
tabs:
  - id: socials          # the page
    title: Socials
    blocks: [...]
  - id: support          # open section: the buttons are visible
    title: Support
    blocks: [...]
  - id: crypto           # closed section
    title: Send crypto
    blocks: [...]
```

`examples/instance-variants/sections/site.yaml` is a complete synthetic example
with the `brand-dark` theme preset.

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
| `{tab}` | the tab's `title` (`Privacy` on the privacy page, `Not found` on the 404 page) |
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
   tabs, the privacy page and the 404 page use `{tab} - {site}`.
4. Otherwise `{tab} - {creator}`, the same as before this option existed.

The 404 page has no tab, so rule 1 does not apply to it: its title is
`Not found - <creator>` by default and follows `title_template` like the
privacy page.

### The 404 page

Every build writes `404.html` at the site root, with no config key. Hosts such
as Cloudflare Pages serve it, with status 404, for any path that does not
exist; without it they answer such paths with `index.html` and status 200. It
has the same theme, header and tab bar as the other pages, the line "That page
does not exist.", a link home and the privacy link. It carries
`<meta name="robots" content="noindex">`, loads no script and has no
click-counted link.

Because a host serves it at any depth (`/a/b/c`), its stylesheet, avatar, tab
and privacy links are root-relative (`/static/site.<hash>.css`, `/privacy/`), unlike
the other pages, whose paths are relative to their own folder. This assumes
the site lives at the root of its domain; a site under a path prefix is not
supported today. To restyle it see [customizing](customizing.md#the-404-page).

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
| email | `id`, `address`, optional `title`, `note`, `reveal`, `spell` | a contact address kept off the page; see [Email](#email) |
| platforms | `id`, `items`, optional `title`, `layout`, `note` | handles with counts; see [Platforms](#platforms) |
| live | `id`, `players`, optional `also_on`, `fediverse_note`, `vods` | Twitch and Kick as co-equal players, link-outs and VOD links; see [Live](#live) |
| divider | `id` only | a horizontal rule |

Text, image, crypto, email, platforms, live and divider blocks have no `show_from` or `show_until`; they always
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

#### Email

A contact button that keeps the plain address out of the built page. Without
JavaScript the page shows the address spelled out (`name at example dot test`)
as selectable text. With JavaScript a **Show email** button reveals the real
address as selectable text, a **Write email** button that opens the visitor's
mail program, and a **Copy** button.

```yaml
blocks:
  - id: email
    title: Email me
    address: name@example.test
    note: Business and press only. I read everything and answer within a week.
    reveal: click
    spell:
      at: at
      dot: dot
```

A runnable version is in `examples/email/`.

| Key | Rule |
| --- | --- |
| `id` | a slug, unique across the site |
| `title` | optional heading, plain text, up to 200 characters |
| `address` | required. One plain ASCII address, `local@domain`: no display name, quotes, comments, spaces, control characters or Unicode, at most 254 characters (64 before the `@`). The domain needs at least one dot and cannot be a bare IP number |
| `note` | optional, plain text, up to 300 characters, shown under the buttons. This is where you say what the address is for. It is refused if it contains `@`, `mailto:` or the address's domain, because it is plain text on the page |
| `reveal` | `click` (default): the visitor presses Show email. `always`: the script shows the address at load. `never_js`: no script is shipped for this block and the page only ever shows the spelled form |
| `spell.at`, `spell.dot` | optional words used for `@` and `.` in the spelled form, 1 to 20 characters with no spaces, `@` or `.`. Defaults `at` and `dot`. Dots in the part before the `@` are spelled out too |

**What the built page contains.** The HTML has the spelled text, and (for
`click` and `always`) the address reversed and base64 encoded in a `data-email`
attribute. It never contains the plain address, an `@`, or a `mailto:` link: the
script creates the link only after the reveal. `email.js` and `email.css` are
added only to pages with the block (`never_js` blocks get the stylesheet and no
script), the script is loaded with `<script src defer>` so the default
`script-src 'self'` still holds, and it makes no network request and stores
nothing.

**The honest limit.** This stops simple scrapers that read page source or run
a regular expression over the HTML. It does not stop a determined one: a
browser-driven or AI scraper that runs the script, or anything that decodes a
base64 attribute, can read the address, and so can a person who copies the
spelled form. If an address must never be found, do not publish it.

**Testing note.** The script opens the mail program through
`window.__raOpen(url)` when that function exists, and sets
`window.location.href` otherwise. Browser tests define `__raOpen` so no test
ever hands off to a real mail client.

#### Platforms

A laid-out list of the platforms you are on. Each item is a card or row with
the platform icon, its name, your handle, an abbreviated count (`3.8k`,
`406.8k`, `1.2M`) and a small `as of` date. The whole card is the link. The
exact number (`3,812 followers`) is in the page as hidden text for screen
readers, so the abbreviation costs no information.

```yaml
blocks:
  - id: socials
    title: Find me everywhere
    layout: list
    items:
      - platform: twitch
        handle: examplecreator
        url: https://example.com/twitch
        count: 3812
        as_of: 2026-01-01
      - platform: youtube
        handle: "@examplecreator"
        url: https://example.com/youtube
        label: subscribers
      - platform: other
        name: Example Net
        handle: examplecreator
        url: https://example.com/other
```

| Key | Rule |
| --- | --- |
| `id` | a slug, unique across the site |
| `title` | optional heading, plain text, up to 200 characters |
| `layout` | `list` (default: full rows, two columns from 896 px, `56rem`) or `chips` (a compact strip of icon and count) |
| `note` | optional, plain text, up to 300 characters, shown under the list |
| `items` | 1 to 40 entries |
| `items[].platform` | `twitch`, `kick`, `youtube`, `tiktok`, `instagram`, `threads`, `bluesky`, `mastodon`, `pixelfed`, `discord`, `matrix`, `github`, `patreon`, `x`, `linkedin`, `rss` or `other` (`pixelfed` and `other` use the generic link icon) |
| `items[].name` | required for `other`, refused for the others; up to 40 characters |
| `items[].handle` | the text shown, 1 to 80 characters, plain: no control, bidirectional or zero-width characters |
| `items[].url` | `https` only, or an internal `tab:<id>` link (same rules as a link) |
| `items[].label` | what the number counts: `followers` (default), `subscribers` or `members` |
| `items[].count` | optional whole number from 0 to 10,000,000,000. A float, a string or a boolean is refused. Needs an `as_of` |
| `items[].as_of` | `YYYY-MM-DD`, a real calendar day from 2000-01-01 to one day after the build day; required with a `count`, refused without one |

A platform with no count (typed or from the numbers file) shows no number.

**Typed (hand-kept) numbers.** Any platform can carry a typed `count` with its
`as_of` day, which is how you show Kick, TikTok, Instagram, X, Threads and
LinkedIn (the engine has no source for them). Both are checked when the config
is loaded; a count with no date, an impossible date such as `2027-02-30`, a
date before 2000 or a date more than a day in the future stops the build and is
reported by `reach-around check` (the future date is checked against the build
day in the config's time zone). The number on the button is the abbreviated
count; the date is the small `as of` line in the list layout and part of the
spoken text (`3,812 followers, as of 2026-01-01`) in the chips layout.

**Old numbers.** A number shown on a platform button whose date is more than 30
days before the build day gets a build warning, and `reach-around check` prints
a `Warning:` finding (exit status stays 0). Both name the block and the
platform, never the value. This covers typed numbers and numbers-file entries.
A stale value that loses to a fresh one is not reported.

**Stale marks.** A shown number older than its stale limit is marked on the
page: the list layout adds `(out of date)` after the `as of` date, and the chips
layout drops the visible count and says `out of date` in the spoken text, so an
old number is never presented as current. See [Hand-kept numbers](#hand-kept-numbers).

**Click ids.** With click tracking on, an item's link carries `ping` and
`data-link-id` set to `<block id>-<platform>`; a repeat of the same platform in
one block gets `-2`, `-3`. These ids are in the manifest, must not collide with
any other id on the site, and are at most 40 characters (the click endpoint's
limit), so a long block id with a long platform name is refused with the
length named. An item whose `url` is a `tab:` link is internal: no ping, no
manifest entry.

**Assets.** `static/platforms.css` is added only to pages that contain a
platforms block. It reads only theme tokens, keeps every target at least 44 px
high and wraps long handles.

##### The numbers file

Counts can come from a JSON file instead of (or on top of) the config, so a
script can refresh them without touching `site.yaml`:

```json
{"version": 1, "platforms": {"twitch": {"count": 3812, "as_of": "2026-10-24T12:00:00Z"}}}
```

`reach-around build --numbers PATH` reads it. Without the flag the build reads
`numbers.json` beside the config when that file exists, and quietly skips it
when it does not. A file named with `--numbers` that is missing or invalid is an
error naming the file, and so is an invalid default file. The file is read at
build time only: it is never copied into the output and never put in the
manifest, and a path inside `--out` is refused.

| Rule | Value |
| --- | --- |
| Top level | exactly `version` (1) and `platforms` |
| Key | a platform slug (applies to every item of that platform) or an item id such as `socials-twitch` (applies to that one item and wins over the slug) |
| Entry | exactly `count` and `as_of` |
| `count` | a whole JSON number, 0 to 10,000,000,000; `3.0`, strings, booleans and negatives are refused |
| `as_of` | a full timestamp with `Z` or an offset, from 2000-01-01 to one day after the build |
| Size and shape | up to 256 KiB, 1000 entries, no duplicate keys, no `NaN` or `Infinity`, deeply nested input is an error |
| Unknown keys | a key that is neither a platform slug nor an item id in the config is ignored; the build prints how many |

Which value wins: with no typed `count`, the file's value is used. The file's
value always beats the typed one while it is fresh (at most 30 days old),
whatever the two dates are: a typed value never replaces a fresh fetched one.
If the file's value is older than 30 days (for example its source has been
failing and refresh kept the last good number) and the typed value is not, the
typed value is shown instead. If both are old, the file's value is shown and the
build warns. A typed value with no file entry is always shown, with a warning
once it is old. With neither, the button shows no number. The date shown is the
date of the value that won.

#### Live

The data a Live tab shows: Twitch and Kick as two co-equal players, a line of
link-outs to the other platforms, a short fediverse note and, for when neither
channel is live, one VOD link per platform. The block renders a static Live
page: the offline layout (three equal VOD cards, the also-on line and the
fediverse note), a "status unavailable" pair of equal watch links, and the live
state as two identical click-to-load cards that are hidden until a script (a
later change) shows them. No player is loaded and no third-party request is
made until a visitor clicks. See [Live tab](live.md).

```yaml
blocks:
  - id: live
    players:
      - platform: kick
        channel: example_kick
      - platform: twitch
        channel: example_twitch
    also_on:
      - platform: youtube
        url: https://www.youtube.com/@example
      - platform: tiktok
        url: https://www.tiktok.com/@example
      - platform: instagram
        url: https://www.instagram.com/example
    fediverse_note: Fediverse coming soon
    vods:
      youtube: https://www.youtube.com/@example/streams
      kick: https://kick.com/example_kick/videos
      twitch: https://www.twitch.tv/example_twitch/videos
```

| Key | Rule |
| --- | --- |
| `id` | a slug, unique across the site |
| `players` | required: exactly one `twitch` and one `kick` entry. Both or the block is refused, so neither platform can be left out. The order you write is the order shown; nothing reorders it at run time |
| `players[].platform` | `twitch` or `kick` |
| `players[].channel` | a bare handle, never a URL. Twitch: 1 to 25 letters, digits or underscores. Kick: 1 to 40 letters, digits, dashes or underscores |
| `also_on` | optional, up to 3 plain link-outs, each platform once |
| `also_on[].platform` | `youtube`, `tiktok` or `instagram` |
| `also_on[].url` | `https` on that platform's own site only (YouTube: `youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`; TikTok and Instagram: the bare name or `www.`). No port, no user name, no look-alike host |
| `fediverse_note` | optional plain text, 1 to 80 characters, default `Fediverse coming soon`. No link, address, markup, colon, slash or domain name. `null` turns it off |
| `vods` | optional; `youtube`, `kick` and `twitch` together or not at all (equal weight). Each is an `https` URL on that platform's own site (`kick.com`, `twitch.tv` and the `www.` forms) |

Only one live block is allowed on a site, and it needs `page_layout: tabs`: a
live block with `page_layout: sections` is refused when the config loads. Any
other key (an embed address, autoplay, a title) is refused.

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
  mascot_opacity: 0.2
  mascot_size: contain
  mascot_position: center top
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
| `colors` | map | empty | Keys: `bg`, `text`, `accent`, `on_accent` (text on the accent fill), `surface`, `border`, `muted` (secondary text; derived from `text` and `bg` when unset). Any other key is refused. |
| `fonts.body` | string | Jost, shipped with the engine | A system font stack or a font file path. |
| `fonts.heading` | string | same as body | Same rules as `fonts.body`. |
| `fonts.license` | string | none | A relative path to a `.txt` license or notice (for example the font's `OFL.txt`) in the instance directory. It is published beside your site's files, so the license travels with the font. Needs a font file in `fonts.body` or `fonts.heading`. Must be non-empty UTF-8 text, at most 256 KiB. |
| `background` | string | none | The full-bleed page background, drawn under the carbon-fiber texture. A relative image path (preferred) or an `https://` URL. |
| `background_dim` | number 0 to 0.9 | `0` | A plain number (`0.6`, not `"0.6"` or `true`). Darkens the page background image with a black overlay, in CSS only. If your background image has a bright subject behind the header, raise this. The mascot is not affected. |
| `mascot` | string | none | The giant faded image behind the column. A relative image path only. |
| `mascot_opacity` | number 0 to 0.4 | `0.12` | A plain number. How visible the mascot is. Capped at 0.4 so text over it stays readable. Unset keeps the stylesheet's `0.12`. |
| `mascot_size` | `cover`, `contain` or a `rem` length | `cover` | A length is `1rem` to `200rem` (`40rem`). Anything else is refused. |
| `mascot_position` | one or two terms | `center` | Each term is `center`, `top`, `bottom`, `left`, `right` or `0%` to `100%`, such as `center top` or `20% 80%`. A horizontal term comes before a vertical one when you mix a percentage with a keyword. |
| `column_width` | length | `35rem` (560 px) | `px`, `rem`, `em`, `ch`, `%` or `vw`. |
| `radius` | length | `0.5rem` | Base corner radius for cards and sections. |
| `button_style` | `pill`, `rounded`, `square` | `rounded` | `rounded` is the 36 px pill radius, `pill` is fully round, `square` has no radius. |
| `frame` | `none`, `l1`, `l2`, `l3` | `none` | Optional border frames: the strongest level the build may use. `none` writes no frame markup and does not load the frame stylesheet. See [Border frames](customizing.md#border-frames). |
| `frame_motion` | `none`, `live` | `none` | `live` lets the top rules and dashes of each L3 frame pulse (CSS only, only for visitors without a reduced-motion setting). `none` keeps every frame still. See [Border frames](customizing.md#border-frames). |
| `frame_l3_max` | integer 1 to 64 | `1` | The most L3 border frames (the full frame with the glow) one page may carry when `frame` is `l3`. The packaged templates give the first L3 slots on a page L3 and the rest L2; the build refuses a page over the limit. See [Border frames](customizing.md#border-frames). |
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

### Heading font: Orbitron

The `brand-dark` preset leaves `fonts.heading` commented out, because the engine
does not ship Orbitron. To use it on your site:

1. Download Orbitron from its upstream project
   (https://github.com/theleagueof/orbitron) and put its `OFL.txt` beside the font
   (see the license note below).
2. Put the font file in your instance folder, for example
   `fonts/orbitron.woff2` (`.woff`, `.ttf` and `.otf` also work). Use the file
   as it comes: do not subset, convert or rename the font itself.
3. Uncomment the line in your theme:

   ```yaml
   theme:
     fonts:
       heading: fonts/orbitron.woff2
       license: fonts/OFL.txt
   ```

4. Build. The file is copied to your site and served from your own domain; no
   font host is contacted, which the Content-Security-Policy (`font-src 'self'`)
   enforces. Body text stays Jost.

License: Orbitron is licensed under the SIL Open Font License 1.1 and has the
Reserved Font Name "Orbitron". You may use and redistribute the font, but
a modified version may not keep that name, so ship it unmodified (converting a
file to another format counts as a modification; take a ready-made `.woff2`
from upstream or a repackaging that says it changes no glyphs). The engine does
not bundle Orbitron.

The OFL asks that the license text and copyright line travel with redistributed
copies. Keep the font's `OFL.txt` in your instance folder (for example
`fonts/OFL.txt`) and name it with `theme.fonts.license`, as in step 3.
The build publishes the file under a content-hashed name at the same relative path it has in
your instance folder (see [File names and the assets inventory](#file-names-and-the-assets-inventory)).
Check that the license and copyright line meet the terms for your use.

### What is refused

Any value that contains `;`, `{`, `}`, `url(`, `expression(`, `@import`, a
backslash escape, a comment opener (`/*`) or a newline is refused with the
key path and the reason. The check runs before the allowlist for each kind of
value, so the message names the first problem it found. Only a fixed set of
custom property names (`--ra-*`) and `color-scheme` is ever written to
`theme.css`.

## `profile_card`

An optional card shown after the content of every tab page and the privacy
page, before the disclosure and footer: your name, up to eight topic chips and
a button to your media kit. Leave the key out and the output is byte-for-byte
what it was without the feature. Unknown keys are refused.

| Key | Type | Default | Constraints |
| --- | --- | --- | --- |
| `title` | page title text, 1-80 characters | `creator.name` | The heading of the card. Plain text. |
| `tags` | list of text | empty | At most 8 chips, each 1-30 characters of plain text: no control, newline, bidirectional or zero-width characters, no leading or trailing space. |
| `button.label` | text, 1-40 characters | required with `button` | Same plain-text rules as a tag. |
| `button.url` | HTTPS URL | required with `button` | Same rules as a link URL: `https` only, no user name or password, at most 2048 characters. An internal page is not accepted yet. |

A card needs at least `tags` or a `button`. All text is escaped; there is no
markup in any field.

```yaml
profile_card:
  title: Work with Example Creator
  tags: [Retro games, Security, Amateur radio]
  button:
    label: Download the media kit
    url: https://media.example.com/kit
```

The button is an ordinary external link: `rel="noopener noreferrer"`, no
`target`. It has the id `profile-media-kit`, so a tab, block or link may not
use that id when a button is set. With `features.click_tracking` on, the
button carries the same `ping` and `data-link-id` attributes as a link, and the
build adds `profile-media-kit` to the build manifest (the `links` map the
click Worker is configured from), so the Worker counts it like any link. With click tracking off it carries neither. The card adds no
JavaScript and no inline style; it is styled from the same tokens as the rest of
the page (see [design](design.md)), so a theme's colors and `mode` apply.

## `cutover_date`

Optional. The day the DNS switch to Reach Around was verified end to end, as a
**UTC calendar day**: `YYYY-MM-DD`, no time and no zone. Everything Reach Around
stores and reports is UTC, so the date is a UTC day and is always printed with
"(UTC)". Quote it or leave it bare; a timestamp, `2026-11-8` and `2026-02-30`
are refused.

```yaml
cutover_date: 2026-11-08
```

- Real calendar day, 2000-01-01 or later, and at most 366 days after today's UTC
  day. A day still in the future is accepted but is not shown anywhere until it
  arrives, and `reach-around check` warns about it.
- Shown on the media kit page, the gated kit page and the private kit PDF as a
  small line, "Live since 2026-11-08 (UTC)", above the audience numbers. Absent,
  no line is printed and the output is unchanged.
- `reach-around check --production` warns (exit status stays 0) when it is not
  set. Without `--production` (a staging site) an unset date is not mentioned.
- The date is public. It is set on the day the switch is verified: add the line
  and redeploy (see the runbook step in [Deploying](deploy.md#setting-the-cutover-date)).
- Code that needs the date calls `reach_around.config.cutover.cutover_date` (the
  day or `None`) or `live_since` (the day once it has arrived).

## `kit`

Media kit content. Optional: leave it out and nothing changes. With a `kit`
section the build writes the public kit page at `/kit/`, and, when the kit has
gated content, a retry page at `/kit/retry/` and the gated page into a separate
directory (`--gated-out`) that is never published. The pages and the build flag
are described in the [media kit guide](kit.md). Unknown keys are refused everywhere in it. All text is plain: 1 character up to
its limit, no `<` or `>`, no control or hidden characters, no space at either
end. Text must be quoted if YAML would read it as a number.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `title` | text | required | at most 120 characters |
| `about` | text | required | at most 1000 characters |
| `contact` | text | none | one plain-text line for brands, at most 200 characters; see the note below |
| `metrics` | list of metric | none | at most 30; each `platform` and `label` pair once |
| `partners` | list of partner | none | at most 50 |
| `case_studies` | list of case study | none | at most 20 |
| `rates` | list of rate | none | at most 30 |
| `demographics` | list of demographic | none | at most 30 |
| `turnstile_site_key` | text | none | public key of the Turnstile widget on the gate form; 1 to 64 letters, digits, `-` or `_`. Without it the form has an empty widget slot and the build warns |
| `unlock_link` | true or false | `false` | v1 has no emailed unlock link, so `true` is refused with a clear error until it exists |

A **metric** is one headline number: `platform` (a platform name such as
`twitch`, `youtube` or `other`), `label` (at most 60 characters, such as
`Followers`), `source`, `value`, `as_of` and `in_total`.

- `source: manual` (the default): you type `value` (a whole number, 0 up to
  1,000,000,000,000) and **must** give `as_of`, the day you read it
  (`YYYY-MM-DD`). Leave `value` out to keep a placeholder that is ignored.
- `source: counts`: the number comes from the [numbers file](#the-numbers-file)
  at build time, under the metric's `platform`. Do not write `value` or `as_of`.
  The file's date is used. If the file has no entry, the metric has no value.
- `in_total: false` keeps a metric out of the unified total (use it for a second
  number from the same platform, such as views, so one audience is not counted
  twice). Default `true`.

A **partner** has `name` (80), optional `note` (200) and optional `url` (https
only). A **case study** has `title` (100), `summary` (500) and `result` (300).
A **rate** has `item` (80) and `price_text` (80); the price is text, so
`Ask me!` or `From $200` are fine. A **demographic** has `label` (60), `value`
(80) and a required `as_of`.

**Display policy.** The table is fixed, not a setting:

| Field | Shown |
| --- | --- |
| `title`, `about`, `contact`, `metrics`, the unified total | public |
| `partners`, `case_studies`, `rates`, `demographics` | gated (only after the gate) |
| `turnstile_site_key` | public (it ships in the gate form; the key is public by design) |
| `unlock_link` | private (configuration, never shown) |

The public half therefore shows the header and the headline numbers and nothing
else. If `partners`, `case_studies`, `rates` or `demographics` is not empty,
`features.kit_gate` must be `true`; the config is refused otherwise, so gated
content can never be published with no gate.

**Contact line.** `contact` is optional and is yours to decide: write whatever
you are willing to publish, such as `Brand deals: partners at example.com`.
An email address written as plain text is fine. It is printed escaped as text on
the public page, the gated page and the private PDF; it is never turned into a
`mailto:` or `tel:` link, and Reach Around does not ask for, check or require a
postal address or phone number (some kits elsewhere publish a postal address;
here that is your choice, not a requirement). Control and hidden characters,
`<` and `>`, and surrounding whitespace are refused. Without it nothing is
printed.

**Old numbers.** A number whose `as_of` is older than its stale limit (30 days
unless [`numbers.stale_days`](#numbers) says otherwise for that platform) gets a
build warning (typed numbers, counts file numbers and demographics alike), and
the kit table marks it `(out of date)` next to its date. A date in the future
also warns. Warnings do not fail the build; the production check does (see
[Hand-kept numbers](#hand-kept-numbers)). Kit subscribers are labeled as
subscribers, never as followers: use the label the platform really reports.

**Unified total.** Add up the value of every metric that has one and is not
marked `in_total: false`; a metric without a value is ignored. With nothing to
add there is no total (not zero). The total is worked out at build time and is
never stored.

```yaml
features:
  kit_gate: true
kit:
  title: Example Creator media kit
  about: A fake creator used to show the settings.
  metrics:
    - {platform: twitch, label: Followers, value: 1200, as_of: 2027-03-01}
    - {platform: bluesky, label: Followers, source: counts}
  partners:
    - {name: Acme Fake Co, url: "https://example.com/acme"}
  rates:
    - {item: Sponsored video, price_text: Ask me!}
```

## `numbers`

Where each platform's follower or member count comes from. Optional: leave it
out and nothing changes. `reach-around counts refresh` reads this block, asks
each source for one number and writes a validated numbers file (see
[Daily follower counts](customizing.md#daily-follower-counts) for the command,
the exit codes and a scheduled workflow). Unknown keys are refused.

`numbers.sources` is a list of up to 50 sources (it may be left out when the
block only sets `stale_days`), one per `platform`. Every
source has a `platform` and a `kind` that decides which other keys it takes.
Keys that a kind does not list are refused.

`platform` is the key the count is written under in the numbers file, so it
must be what the [platforms block](#platforms) looks up: either the `platform`
of an item in a platforms block (such as `github`), which feeds every item of
that platform, or the click id of one item (`<block id>-<platform>`, with `-2`,
`-3` for repeats, such as `socials-github`), which feeds only that item and wins
over the platform key. It must be unique across sources. A `platform` that no
platforms item uses is refused when the config loads, with the key path, because
the number would never be shown. (`kind` is separate: `kind: mastodon` with
`platform: pixelfed` is how a Pixelfed server is read.)

| `kind` | Keys | Asks | Needs a key |
| --- | --- | --- | --- |
| `bluesky` | `actor`: a handle such as `name.bsky.social`, or a DID | `public.api.bsky.app` `getProfile`, field `followersCount` | no |
| `mastodon` | `host`: a public host name; `acct`: the local user name, no leading `@` | `https://<host>/api/v1/accounts/lookup`, field `followers_count`. Also for Pixelfed and other servers that speak the Mastodon API | no |
| `github` | `user`: a GitHub user name | `api.github.com/users/<user>`, field `followers` | no |
| `discord` | `invite`: the invite code (not the URL) | `discord.com/api/v10/invites/<code>?with_counts=true`, field `approximate_member_count` (label: members) | no |
| `matrix` | `room`: a public alias such as `#room:example.org` | resolves the alias on the alias server's client API (following its `.well-known`), then reads the room summary's `num_joined_members` (label: members) | no |
| `youtube` | `channel_id`: the `UC...` id; `api_key_env`: the NAME of an environment variable | YouTube Data API v3 `channels.list`, `statistics.subscriberCount` | yes |
| `twitch` | `login`; `client_id_env` and `client_secret_env`: environment variable NAMES that must differ | app token, then `helix/channels/followers` `total` | yes, and see the limit below |

`numbers.history_file` (optional) is a path to an append-only history file in
your private instance repo; relative paths are relative to the config file. Unset
means no history is written. See [Social-stats history](social-history.md).

Credentials are never in the config. A keyed source names an environment
variable (capital letters, digits and underscores, starting with a letter); the
value is read when the command runs, sent in a header or a POST body (never in
a URL), and never printed, logged, written or put in an error. Supply them
with `doppler run -- reach-around counts refresh ...` or the CI secret store.
A key written inline (`api_key: ...`) is refused as an unknown key.

Each `platform` below must have an item in a platforms block (or be an item id):

```yaml
numbers:
  sources:
    - {platform: bluesky, kind: bluesky, actor: example.bsky.social}
    - {platform: mastodon, kind: mastodon, host: example.social, acct: example}
    - {platform: github, kind: github, user: example}
    - {platform: discord, kind: discord, invite: abc-DEF123}
    - {platform: matrix, kind: matrix, room: "#example:example.org"}
    - platform: youtube
      kind: youtube
      channel_id: UCabcdefghijklmnopqrstuv
      api_key_env: YOUTUBE_API_KEY
```

Rules the command enforces whatever the config says:

- https only, port 443 only, no redirect to another host, no credentials in a
  URL.
- Fixed providers use fixed hosts. For the two configurable hosts (Mastodon
  `host`, and the server in a Matrix alias) the config loader refuses IP
  addresses in any notation, `localhost`, single-label names and
  `.local`/`.internal` style suffixes. At run time the command also resolves the
  name, requires EVERY answer to be a public address (loopback, private,
  link-local, multicast, reserved, carrier-grade NAT and cloud-metadata ranges
  are all refused), and connects to the checked address while keeping the host
  name for the Host header and the certificate check.
- 10 seconds in total for each request (resolve, connect, TLS, send, read), at
  most 256 KiB of response, `application/json` only, no compressed responses.
- A 429 or 5xx is retried at most twice, and only for a GET. `Retry-After` is
  honored up to 60 seconds with a little jitter; a longer wait is not slept
  through, the source simply fails this run.
- A count must be a whole number from 0 to 10,000,000,000. A float, a string, a
  negative number, a boolean or a nested value is a failure. (YouTube sends
  `subscriberCount` as a string of digits; only that source accepts one.)

Limits worth knowing:

- **Twitch.** The follower total is documented under the moderator scope, and
  Twitch has asked for a user access token on that route. App credentials cannot
  obtain one, so the command reports the source as skipped with
  `needs_user_token` (a skip is not a failure) when Twitch answers 401 or 403.
  It does not try another route. Keep the last value by hand (below).
- **YouTube.** Public counts are rounded by YouTube to three significant
  figures, and a channel can hide its count; then the source is skipped as
  `hidden_subscriber_count`, never written as zero.
- **Matrix.** The room summary is public only for rooms that are publicly
  joinable or world-readable (in practice, rooms published in the directory).
  For any other room the source fails as `room_not_public`.
- **Mastodon.** A server that requires login for the public API (limited
  federation mode) answers 401; the source fails with `http_401`.
- **GitHub.** Unauthenticated calls are limited to 60 an hour per address.
  Shared CI runners can hit that; a failure keeps the old number.
- **TikTok, Instagram, Threads, Kick** (and X and LinkedIn) have no source kind.
  Their approval paths do not exist yet. Put the number in the numbers file by
  hand (below), or type `count` and `as_of` on the platform item.

### The numbers file

`reach-around counts refresh` writes `numbers.json` (default beside the config;
`--out` changes it) as a version 1 document:

```json
{
  "version": 1,
  "platforms": {
    "bluesky": {"count": 1234, "as_of": "2026-10-24T09:00:00Z"}
  }
}
```

`count` is a whole number from 0 to 10,000,000,000 and `as_of` is a UTC time in
that exact form: the same file `reach-around build --numbers` reads (see
[the numbers file](#the-numbers-file) under the platforms block). The file is
written atomically (temp file in the same folder, fsync, rename) with mode 0600. A failed source leaves its previous entry
untouched, and the reason goes to a sibling `numbers.status.json` as an error
class and a time only (never a response body). Entries for platforms that have
no source are not touched, so a hand-written entry for TikTok survives a
refresh. A numbers file that does not validate stops the command (exit 2)
instead of being overwritten. There is one definition of the shape and one
reader, `load_numbers` in `reach_around/config/numbers.py`, which the build uses;
refresh reads with it and checks what it writes by parsing it with the same
parser. A manual or item-id entry in the file that no source touches is kept.

`numbers.stale_days` (optional) maps a platform name to the number of days
(1 to 365) after which a hand-kept number for it counts as stale; platforms not
listed use 30. Example: `stale_days: {kick: 14}`.

### Hand-kept numbers

A number is **hand-kept** when a person maintains it: a `count` typed on a
platform item or a kit metric with `source: manual`, or a numbers-file entry no
configured source writes (Kick, TikTok, a LinkedIn follower count). Every one
carries an `as_of` day. All dates are UTC days.

- **The gate.** `reach-around check --production` fails (exit 2, finding
  `stale_hand_kept`) when a hand-kept number is older than its limit. Plain
  `check` only warns. `--stale fail` or `--stale warn` overrides either default.
  Fetched numbers that go stale stay a warning, because the fix is the source,
  not a person. Findings name the block, platform and age, never the value.
- **The build.** It never fails on a stale number; it marks it on the page (see
  above) so a stale number is not shown as current.
- **The reminder.** `reach-around counts stale --config site.yaml [--numbers
  numbers.json] [--remind-days 21] [--json]` lists the hand-kept numbers that are
  stale or at least `--remind-days` (default 21, never later than the platform's
  limit) old, with days old and days over the limit, never the value. Exit 0
  means nothing is due, 4 that something is, 2 a usage or config problem. The
  `--json` form is `{date, remind_days, due: [{scope, platform, id, as_of, days,
  limit, status, days_over}], stale_count}` for a scheduler or notifier to read.
  Sending the alert is a separate job (#252).
- **The import.** `reach-around counts import --config site.yaml --csv FILE
  [--out numbers.json] [--dry-run]` updates the numbers file from a CSV with the
  header `platform,metric,value,as_of`: `platform` is a platform name or item id
  in the config, `metric` is `followers`, `subscribers` or `members` and must
  match the label the config gives that platform, `value` is digits only, and
  `as_of` is a UTC day (`YYYY-MM-DD`), not in the future and not older than the
  entry already on file. The file is capped at 64 KiB and 200 rows, must be UTF-8
  without control characters, and is checked whole: one bad row refuses the file
  and nothing is written. Errors give row and column, never the cell. A platform
  that a fetch source writes is refused. It writes through the same locked,
  atomic writer as `counts refresh`. When `numbers.history_file` is set, each
  imported row is also appended to the [history store](social-history.md) as
  `source: hand_kept` (history first; one value per platform, metric and UTC day,
  a repeat the same day keeps the first).

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
| `lead_retention_days` | integer, 1-365 | `30` | How long a kit email address is kept. This is the one lead retention setting: the privacy page prints it and the deploy gives the same number to the Worker as `LEAD_RETENTION_DAYS`. The range and default are the Worker's own. Quoted numbers and booleans are refused. A value above 365 (allowed before this change, up to 3650) is now an error, because the Worker deletes leads after at most 365 days. |

```yaml
features:
  click_tracking: true
  kit_gate: true
privacy:
  contact: privacy@example.com
  intro: Here is exactly what this site collects.
  retention_days: 90
  lead_retention_days: 30
```

The privacy page at `/privacy/` and the cookie notice are generated from the
enabled features, so they cannot claim less than the site does. See
[privacy by construction](education/privacy-by-construction.md).

## `footer`

The footer of every generated page (each tab, the privacy page and any other
page the build writes) carries a small link to the Reach Around project. It is
on by default and you can turn it off or reword it. Unknown keys are refused.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `credit` | boolean | `true` | Show the credit link. When `false`, nothing is rendered for it. |
| `credit_text` | text, 1-60 characters | `Made with Reach Around` | The link label. Plain text only: no control, bidi or zero-width characters, no leading or trailing space. Escaped, never treated as HTML. Ignored when `credit` is `false`. |
| `copyright` | text, 1-120 characters | none | An optional copyright line, shown once in the footer after the disclosure. Absent by default, and then nothing is rendered. Plain text only (no HTML, no control, bidi or zero-width characters, no leading or trailing space), escaped on the page, no inline style. The literal token `{year}` is replaced with the build year; nothing else is interpolated. |

```yaml
footer:
  credit: true
  credit_text: Built with Reach Around
  copyright: "© {year} Example Creator"
```

The link goes to `https://github.com/ChiefGyk3D/Reach-Around` and is an
ordinary link with `rel="noopener noreferrer"`. It has no `ping` and no
`data-link-id`, so clicks on it are never counted, it adds no script, and the
page makes no request to that address until a visitor follows the link.
Because of that, the privacy page needs no extra statement for it.

## `analytics`

Optional page-view counting. **Off by default**: with the section absent, or
`page_views: false`, the build is byte-for-byte what it was before this key
existed. Unknown keys are refused; there is deliberately nothing else to set.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `page_views` | boolean | `false` | Count one view per page load. Must be a real boolean. When on, the build ships `static/view.js`, links it from every tab page (not the privacy page), puts `data-page-id` and `data-view-route` on `<body>`, and adds the page-view item to the privacy page, which lists the referring site, the campaign label (`utm_source`, at most 30 characters), the source label (`s`, same form) and the window width among the rest. |
| `raw_keep_days` | integer 1 to 90 | `7` | What the privacy page says about how long individual view records are kept. It does not change the Worker: keep it equal to the Worker's `RAW_KEEP_DAYS`. |
| `other_sites` | list of host names | none | Other sites counted by the same collector (the Worker's `SITES`), named on the privacy page. Lower-case host names only, at most 7, no scheme, port, path or wildcard. |
| `route` | path | `/e/v/` | The same-origin path the Worker serves the page-view route on. Plain segments (`A-Z a-z 0-9 . _ ~ -`, no segment starting with a dot), starting and ending with `/`, at most 64 characters. No scheme, host, query, fragment, `.` or `..` segment, escape or space. The tab id is appended to it. |

```yaml
analytics:
  page_views: true
  route: /e/v/
```

What `view.js` does, and nothing more:

- It sends one `navigator.sendBeacon` per page load, a small form-encoded body
  to `<route><tab id>`: `r` (the referrer's host name only, when there is
  one), `u` (a `utm_source` label of 1-30 characters from `a-z0-9-`, when
  there is one), `s` (a source label from the page address's own `s`
  parameter, same form, when there is one) and `w` (one letter for the window width: `s` under 480,
  `m` 480-767, `l` 768-1199, `x` 1200 and up). These are the fields the Worker
  route reads; see `worker/src/view.ts`.
- It sends nothing when `navigator.globalPrivacyControl` is true or Do Not Track
  is on: any of `navigator.doNotTrack`, `window.doNotTrack` and
  `navigator.msDoNotTrack` set to `1`, `yes` or `true` (any case, spaces ignored).
  This is checked at start and again at send time, so a signal that arrives while
  the page is prerendering or hidden still stops the beacon. The Worker checks
  `Sec-GPC` and `DNT` again.
- The referrer host is left out when it is not a real name: IPv4 literals in any
  spelling, IPv6 literals, single-label hosts and hosts ending in a number (the
  Worker would refuse the whole body). `u` and `w` are still sent.
- It waits while the page is prerendering or hidden and sends on the first
  visible moment, once.
- Without `navigator.sendBeacon` it does nothing. There is no fallback request.
- It sets no cookie, uses no browser storage and reads no identifier. The
  Content Security Policy is unchanged (`script-src 'self'`, no inline script).

**The Worker route is not detectable from this config.** The site config does
not say where the Worker runs or which hostname it answers on, so the build
cannot warn that `analytics.page_views` is on without a Worker. The route must
be served on the same hostname as the pages (the Worker also requires the
request `Origin` to match its `SITE_HOST`), the tab ids must be in the Worker's
`PAGE_IDS` (the build manifest lists them), and its limiter, hash secret and
kill switch must be set. The merged Worker records nothing without
`VIEW_HASH_SECRET` (it fails closed) and serves only `/e/v/:pageId` today, so
change `route` only when a proxy or route rule maps your path to that Worker
path. See [deploy](deploy.md). If nothing answers the route,
the beacon fails silently and the pages work normally; you just get no counts.
The privacy page still discloses the counting, which is the safe direction.

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

## `redirects`

Optional. When the `redirects` key is **absent** (or has no `socials`), the build
writes no `_redirects` file and the output is byte-for-byte what it was before
this key existed. Unknown keys are refused.

`redirects.socials` is for the cutover: the link page replaces an old page at
`/socials` on the same host, and shared links to it must keep working. Turning
it on writes a `_redirects` file with one rule for `/socials` and one for
`/socials/`, both to `target`. Every other path is untouched.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `status` | `301` or `302` | `301` | `301` for production. Use `302` while testing, so a browser or cache does not keep the redirect after you turn it off. It must be the number, not text (`"301"`) or a boolean. |
| `target` | root-relative path | `/` | Where the old path goes. Starts with a single `/`, at most 200 characters, plain characters only (`A-Z a-z 0-9 . _ ~ - /`). No scheme or host, no `//`, no `\`, no query, fragment, whitespace, wildcard or `.` / `..` segment. |

```yaml
redirects:
  socials:
    status: 302   # switch to 301 (or delete this line) for production
    target: /
```

The build refuses a site that also has a page under `/socials/` (a tab whose id
is `socials`): the redirect would hide it. Rename the tab or leave the redirect
off. The file is host configuration like `_headers`: it is not listed in the
assets inventory and gets no cache rule. See
[deploy.md](deploy.md#redirecting-the-old-socials-path) for the cutover step and
what has not been verified against the host.

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
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; require-trusted-types-for 'script'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |

The gate pages (`/kit/`, `/kit/retry/`) add the Turnstile origin and leave out
`require-trusted-types-for`: a path that allows a third-party script origin does not get
the directive (see [performance](performance.md)).

When `robots.noindex_paths` is set, each prefix also gets its own block with
`X-Robots-Tag: noindex` (see [`robots`](#robots)).

After those blocks the file lists the cache rules. Files with a content hash in
their name (everything under `static/`, and the images and fonts your config
names) get `Cache-Control: public, max-age=31536000, immutable`; pages,
`404.html`, `robots.txt` and root-level files a browser asks for by name get
`Cache-Control: public, max-age=0, must-revalidate`. The patterns never
overlap, so no URL gets both. See
[deploy.md](deploy.md#caching-and-stale-files).

### File names and the assets inventory

Every file under `static/` and every image or font your config names is
published as `name.<20 hex>.ext`, the hash being the SHA-256 of the file's
final bytes. A config value such as `creator.avatar: media/me.png` still names
the file in your instance folder; only the published name carries the hash, and
the page links to it. `apple-touch-icon.png` (and its `-precomposed` form) at
the root keep their names. Because the hash adds 21 bytes to a file name and
Linux limits one name to 255 bytes, every image or font path in the config may
have a file name of at most 234 bytes (counted in encoded bytes, so a multibyte
character counts for each of its bytes) and a whole path of at most 1024 bytes;
the config check names the field that is too long.
`reach-around build --assets-inventory PATH` (default
`<out>.assets.json` beside `--out`, never inside it) writes every published
path with its SHA-256, size and class (`hashed` or `stable`).
`reach-around deploy-purge-list --previous OLD --current NEW` prints the URL
paths a cache may still hold in an old state.

The policy has no `unsafe-inline` and no `unsafe-eval`. Pages carry no inline
style, no inline script and no inline event handler, and a test checks every
built page. A template override that adds one will not run in the browser.

For code that needs a narrow exception later (the gate page and its Turnstile
script), `reach_around.site.overrides.build_csp(extra)` and `render_headers(
path_extras)` accept extra sources for a fixed set of directives. A source
must be `'self'` or an exact `https://host[:port]` origin. `*`, `data:`,
`https:`, nonces and the `unsafe-*` keywords are refused.
