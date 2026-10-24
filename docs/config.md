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
| `theme` | Theme | defaults | colors, fonts, background and shape; see [`theme`](#theme) |

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
| `avatar` | HTTPS URL | none | |
| `socials` | list of Social | empty list | |

```yaml
creator:
  name: Example Creator
  avatar: https://example.com/avatar.png
```

### `creator.socials[]` (`Social`)

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `name` | Title | required | |
| `url` | HTTPS URL | required | |
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
| `title` | Title | required | |
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
| `url` | HTTPS URL | required | |
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
| divider | `id` only | a horizontal rule |

Text, image and divider blocks have no `show_from` or `show_until`; they always
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
section builds the same page as before.

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
| `colors` | map | empty | Keys: `bg`, `text`, `accent`, `surface`, `border`. Any other key is refused. |
| `fonts.body` | string | `system-ui, sans-serif` | A system font stack or a font file path. |
| `fonts.heading` | string | same as body | Same rules as `fonts.body`. |
| `background` | string | none | An `https://` URL or a relative image path. |
| `column_width` | length | `36rem` | `px`, `rem`, `em`, `ch`, `%` or `vw`. |
| `radius` | length | `0.5rem` | Corner radius for link buttons. |
| `button_style` | `pill`, `rounded`, `square` | `rounded` | `rounded` uses `radius`. |
| `mode` | `auto`, `light`, `dark` | `auto` | `auto` follows the visitor's setting. |

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
reversed date windows, timestamps in date fields, unknown time zones and an
empty `tabs` list. Messages from the models name the dotted path, for example
`tabs[0].blocks[0].url: url must start with https://`.

The reasons behind these rules, and the test that proves each one, are in
[docs/education/config-hardening.md](education/config-hardening.md).
