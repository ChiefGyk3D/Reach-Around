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
| `disclosure` | text | none | at most 2000 characters; shown when affiliate links exist |
| `timezone` | text | `America/New_York` | must be a known IANA zone name; decides which calendar day "today" is for scheduled links |
| `title_template` | text | `{tab} - {creator}` | how page titles are built; see [Page titles](#page-titles) |
| `theme` | Theme | defaults | colors, fonts, background and shape; see [`theme`](#theme) |
| `features` | Features | click tracking on, kit gate off | switches for features that collect data; see [`features`](#features) |
| `privacy` | Privacy | defaults | wording and retention for the privacy page; see [`privacy`](#privacy) |
| `footer` | Footer | credit on | the "Made with Reach Around" link in the footer; see [`footer`](#footer) |
| `robots` | Robots | absent | optional; when present the build writes `robots.txt`; see [`robots`](#robots) |
| `analytics` | Analytics | page views off | optional page-view counting; see [`analytics`](#analytics) |
| `store` | StoreConfig | SQLite at `data/reach-around.db` | where the snapshot store lives; see [`store`](#store) |
| `profile_card` | ProfileCard | none | name, topic chips and a media-kit button after the content of every page; see [`profile_card`](#profile_card) |
| `numbers` | NumbersConfig | none | where each platform's follower count comes from, for `reach-around counts refresh`; see [`numbers`](#numbers) |

Ids must be unique across the whole site: every tab id, every block id (links
and sections) and every link id inside a section share one pool. Reusing an
id anywhere is refused, with the message `duplicate id 'x': ids must be unique`.

```yaml
timezone: Europe/Berlin
disclosure: Some links are affiliate links.
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
| divider | `id` only | a horizontal rule |

Text, image, crypto, email, platforms and divider blocks have no `show_from` or `show_until`; they always
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
        as_of: 2027-03-01
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
| `items[].count` | optional whole number from 0 to 10,000,000,000. A float, a string or a boolean is refused |
| `items[].as_of` | optional `YYYY-MM-DD`; needs a `count` |

A platform with no count (typed or from the numbers file) shows no number.

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

Which value wins: with no typed `count`, the file's value is used. With a typed
`count` and no `as_of`, the file wins. With both, the file wins when its date
(UTC) is the same day or later than the typed `as_of`, and the typed value
stays otherwise. The date shown is the date of the value that won.

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

## `numbers`

Where each platform's follower or member count comes from. Optional: leave it
out and nothing changes. `reach-around counts refresh` reads this block, asks
each source for one number and writes a validated numbers file (see
[Daily follower counts](customizing.md#daily-follower-counts) for the command,
the exit codes and a scheduled workflow). Unknown keys are refused.

`numbers.sources` is a list of 1 to 50 sources, one per `platform`. Every
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
- **TikTok, Instagram, Threads, Kick** have no source kind. Their approval paths
  do not exist yet. Put the number in the numbers file by hand (below).

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

## `footer`

The footer of every generated page (each tab, the privacy page and any other
page the build writes) carries a small link to the Reach Around project. It is
on by default and you can turn it off or reword it. Unknown keys are refused.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `credit` | boolean | `true` | Show the credit link. When `false`, nothing is rendered for it. |
| `credit_text` | text, 1-60 characters | `Made with Reach Around` | The link label. Plain text only: no control, bidi or zero-width characters, no leading or trailing space. Escaped, never treated as HTML. Ignored when `credit` is `false`. |

```yaml
footer:
  credit: true
  credit_text: Built with Reach Around
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
| `page_views` | boolean | `false` | Count one view per page load. Must be a real boolean. When on, the build ships `static/view.js`, links it from every tab page (not the privacy page), puts `data-page-id` and `data-view-route` on `<body>`, and adds the page-view item to the privacy page, which lists the referring site, the campaign label (`utm_source`, at most 30 characters) and the window width among the rest. |
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
  there is one) and `w` (one letter for the window width: `s` under 480,
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

After those blocks the file lists the cache rules. Files with a content hash in
their name (everything under `static/`, and the images and fonts your config
names) get `Cache-Control: public, max-age=31536000, immutable`; pages,
`404.html`, `robots.txt` and root-level files a browser asks for by name get
`Cache-Control: public, max-age=0, must-revalidate`. The patterns never
overlap, so no URL gets both. See
[deploy.md](deploy.md#caching-and-stale-files).

### File names and the assets inventory

Every file under `static/` and every image or font your config names is
published as `name.<10 hex>.ext`, the hash being the SHA-256 of the file's
final bytes. A config value such as `creator.avatar: media/me.png` still names
the file in your instance folder; only the published name carries the hash, and
the page links to it. `apple-touch-icon.png` (and its `-precomposed` form) at
the root keep their names. `reach-around build --assets-inventory PATH` (default
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
