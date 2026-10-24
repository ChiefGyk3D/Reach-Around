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
