# Layouts and block types

This page extends the [config reference](config.md) with the block types and
layout options added in Plan 1, Task 6C. Every example uses synthetic data.

## Block types

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

### Markdown

Only these are supported: `**bold**`, `*italic*`, `[text](https://...)` links,
`-` bullet lists, and line breaks. Everything else is shown as plain text. Raw
HTML is escaped, never passed through. A link must be `https`; `http:`,
`javascript:`, `data:` and relative links stay as plain text.

### Images

`src` is either an `https://` URL or a relative path such as `img/photo.png`
(letters, digits, `.`, `_`, `-` and `/` only; no `..`, no leading slash, no
scheme). `alt` is required so the page works for people who cannot see the
image. `link`, if set, must be `https`.

## Layouts

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

## Header variants

`creator.header` is `compact` (the default), `centered` or `hero`.

```yaml
creator:
  name: Example Creator
  header: hero
```

An unknown `layout` or `header` is refused when the config loads.
