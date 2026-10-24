# Customizing your page

This guide is for a creator who hosts their own Reach-Around page and wants to
change the links, blocks, theme and layout without touching engine code. Work
through the layers in order. Each one takes more effort than the one before,
and most pages never need the last.

Reach-Around is alpha. The config format, template names and CSS custom
property names can change before a release. Pin the version you build with and
re-read this page when you upgrade. Every command and key below was run or
read from the code at the time of writing; the field-by-field rules live in
the [config reference](config.md).

## Where things live

An instance is a folder you own, outside the engine:

```
my-page/
  site.yaml            # the config: creator, tabs, blocks, theme, features
  my-overrides/        # optional
    templates/         # replace packaged templates by name
    custom.css         # extra CSS, linked after the theme
```

The folder names are yours. The engine only needs the paths you pass on the
command line. To keep this folder in its own private repository that pins an
engine release and deploys on merge, start from
[`examples/instance-repo/`](../examples/instance-repo/) and read
[instance repositories](instance-repos.md). `site.yaml` is the only required file. This first example is a
complete config and is checked by `tests/test_docs_examples.py`.

```yaml
version: 1
creator:
  name: Example Creator
  socials:
    - name: Example Video
      url: https://video.example.com/@example
      icon: video
tabs:
  - id: start
    title: Start
    blocks:
      - id: home-page
        title: Example Home Page
        subtitle: The main site
        url: https://example.com/
      - id: gear
        title: Gear
        links:
          - id: example-stand
            title: Example Boom Stand
            url: https://shop.example.com/stand
```

Three more complete files are in `examples/instance-variants/`: `minimal`,
`dark-hero` and `grid-cards`. A test builds each one, so they stay valid.

## Layer 1: links and blocks

Edit `tabs[].blocks`. Each tab becomes one page. A block is picked by its
keys:

| Block | Keys |
| --- | --- |
| link | `id`, `title`, `url` |
| section (a group of links) | `id`, `title`, `links` |
| text | `id`, `markdown` |
| image | `id`, `src`, `alt` |
| crypto (addresses to copy) | `id`, `coins` |
| divider | `id` |

Every `id` must be unique across the whole site. Links can carry `affiliate`,
`show_from` and `show_until`; the page shows the `disclosure` text when a
visible affiliate link exists. Full rules, limits and the Markdown subset are
in [the config reference](config.md#blocks-layouts-and-headers). An
unknown key is an error, not a silent no-op.

### A Support tab with crypto addresses

The `crypto` block lists public addresses with a Copy button. It is static: no
wallet code and no outside calls, and a small script (`copy.js`) loads only on
pages that have the block. Addresses are public data, but a wrong character
sends money to the wrong place, so the page shows a reminder to check the first
and last characters. `examples/crypto/site.yaml` is a working copy with fake
addresses and a QR image. Keys, limits and the security notes are in
[the config reference](config.md#crypto).

Review any `custom.css` you did not write yourself before publishing a page
with this block. A stylesheet can change how an address *looks* (for example
overlay a different one with a pseudo-element) without changing what the Copy
button writes, so someone reading the address off the screen and typing it in
could be misled. The build cannot detect that, so treat third-party CSS on a
payment page like any other code you run.

```yaml
- id: crypto
  title: Send crypto
  coins:
    - coin: btc
      address: bc1qexampleexampleexampleexampleexample
      qr: media/qr-example.png
```


### Linking to another tab

A link or a header social may point at another tab of the same site with
`url: "tab:<tab-id>"` or `url: "tab:<tab-id>#<fragment>"`. For example, a header
email icon that opens the Contact tab:

```yaml
creator:
  socials:
    - name: Email
      icon: email
      url: "tab:contact"
```

Rules: the scheme is exactly lowercase `tab:` (`TAB:contact` is refused); the
tab id must exist, and the error names the key and lists the valid ids; the
fragment is 1 to 40 characters of `a-z`, `0-9` and `-`. The build writes the
tab's relative path (`./` for the first tab, `../contact/` from another page).
Internal links open in the same tab, carry no `target`, `rel` or `ping`, are
not click-counted, and do not appear in `manifest.json`. They cannot be marked
`affiliate`. Any other scheme (`mailto:`, `javascript:`, `data:`, `//host`)
is still refused.

If you override `blocks/link.html`, `blocks/section.html` or `_macros.html`,
import the macro with context (`{% from "_macros.html" import link_anchor with
context %}`), because it calls the `tab_href` function the build provides. A
copy that imports without context fails on `tab:` links only.

## Layer 2: the theme

The `theme` section changes colors, fonts, background, column width, corner
radius, button shape and light or dark mode. Values are checked when the
config loads, and the build writes the result to `static/theme.css`. This
is the `dark-hero` variant's theme:

```yaml
theme:
  mode: dark
  colors:
    bg: "#101418"
    text: "#e8edf2"
    accent: "#ff69b4"
    surface: "#1a2128"
    border: "#33404c"
  fonts:
    body: Georgia, "Times New Roman", serif
  button_style: pill
```

and what the build wrote for it:

```
:root {
  --ra-color-bg: #101418;
  --ra-color-text: #e8edf2;
  --ra-color-accent: #ff69b4;
  --ra-color-surface: #1a2128;
  --ra-color-border: #33404c;
  --ra-font-body: "Georgia", "Times New Roman", serif;
  --ra-button-radius: 999px;
  color-scheme: dark;
}
```

Fonts are a system font stack or a font file inside your instance folder.
A font on someone else's server is refused. See
[`theme` in the config reference](config.md#theme) for every key.

## Your own images: avatar, favicon, background, mascot

The engine ships no artwork. Four image slots are config keys, and each takes
a file that lives in your instance folder (the folder that holds `site.yaml`):

| Config key | What it draws |
| --- | --- |
| `creator.avatar` | The small round picture beside your name. |
| `creator.favicon` | The browser tab icon. |
| `theme.background` | The full-bleed page background, fixed, under the texture. |
| `theme.mascot` | The giant faded image behind the column, fixed in place. |

```
my-page/
  site.yaml
  media/
    avatar.png
    favicon.svg
    background.jpg
    mascot.svg
```

```yaml
creator:
  avatar: media/avatar.png
  favicon: media/favicon.svg
theme:
  background: media/background.jpg
  mascot: media/mascot.svg
```

Rules: a relative path with no `..`, no leading `/` and no scheme; the file
ends in `.png`, `.jpg`, `.jpeg`, `.webp`, `.avif`, `.gif` or `.svg` (`.ico` is
not accepted); the path does not start with `static/` or `privacy/`. The build
copies only the files you name, to the same relative path in the output, so
the page loads them from its own origin and the security headers stay as they
are. The same extension list applies to image blocks (`src`), so a config cannot
publish a `.yaml` or `.env` file; font files must end in `.woff2`, `.woff`,
`.ttf` or `.otf`. A missing file, a symlink anywhere in the path (the instance
folder itself included), a non-regular file such as a FIFO, or a file over
8 MiB stops the build with an error naming the config key, for example
`tabs[0].blocks[1].src: 'site.yaml' must be an image file ending in .png, ...`. An https
URL is still accepted for `creator.avatar` and `theme.background`, but it
tells that host about every visitor; use a local file. `favicon` and `mascot`
refuse a URL.

**The bytes must match the extension.** After reading a file the build runs
cheap structural sanity checks, not a full decoder: the format's signature plus
a length or end marker that a renamed text file lacks. PNG must end with the
12-byte IEND chunk, GIF with `;`, JPEG with `FFD9`; WebP's RIFF size field plus
8 must equal the file length; WOFF and WOFF2's header length field must equal
the file length; TrueType and OpenType need 1 to 64 tables and room for the
table directory; AVIF needs an `avif` or `avis` brand. A `.yaml` renamed
`secret.png` is refused, and so is a real PNG header followed by a text body.
The extension is compared in lower case, so `.PNG` is checked the same way.
These checks are structural, not a decoder: a file wrapped in valid image or
font headers and trailers still passes.
The error is `theme.mascot: 'secret.png' is not a real png file (its first
bytes do not match the format)`. A file that passes can still be a damaged
image; the checks only stop other file types from riding along.

**SVG is checked against an allowlist.** A browser runs an SVG as a document
when someone opens its address, so the build accepts only what is listed and
refuses everything else, with the config key and the reason, for example
`theme.mascot: 'm.svg' is refused as an svg: element <script> is not allowed`
or `attribute 'onclick' on <rect> is not allowed`.

- Elements (SVG namespace, case-sensitive): `svg g defs symbol use path rect
  circle ellipse line polyline polygon text tspan title desc linearGradient
  radialGradient stop clipPath mask filter feGaussianBlur feOffset feBlend
  feColorMatrix feFlood feComposite feMerge feMergeNode feDropShadow metadata`.
  So `script`, `foreignObject`, `style`, `a`, `image`, `feImage`, `animate*`,
  `set`, `switch`, `pattern`, `view`, `cursor`, `font-face`, `iframe` and every
  element in another namespace are refused by default.
- Attributes (no `style` attribute, no `on*` handlers, no `data-*`, no `role`
  or `aria-*`): `id class transform viewBox preserveAspectRatio width height x
  y x1 x2 y1 y2 cx cy r rx ry d points fill fill-opacity fill-rule stroke
  stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit
  stroke-dasharray stroke-dashoffset opacity clip-path clip-rule mask filter
  offset stop-color stop-opacity gradientUnits gradientTransform spreadMethod
  fx fy dx dy stdDeviation in in2 result mode type values flood-color
  flood-opacity operator k1 k2 k3 k4 font-family font-size font-weight
  font-style text-anchor dominant-baseline letter-spacing version href
  maskUnits clipPathUnits filterUnits primitiveUnits rotate textLength
  lengthAdjust`, plus `xlink:href` and `xml:space`. `xml:base` is refused.
  Only the SVG and xlink namespaces may be declared.
- Values: no backslash, no `/*`, no control characters, no `javascript:`,
  `data:`, `expression(` or `@import`; `href` and `xlink:href` must start with
  `#`; any `url(` must be exactly `url(#id)` (optional spaces inside, no
  quotes).
- Document: no DOCTYPE or ENTITY declaration (checked before parsing, which
  also stops entity-expansion payloads), UTF-8 only, valid XML, an `svg` root,
  no processing instructions (the plain `<?xml ...?>` declaration is fine) and
  no CDATA sections. Comments are allowed, and an accepted file is copied
  byte for byte.

- Function-style values: after removing well-formed `url(#id)` tokens, any
  remaining `(` in an attribute value is refused (`image(`, `image-set(`,
  `src(`, `cross-fade(`, `element(`, `paint(`, `calc(`, `var(` and so on). The
  only exception is `transform` and `gradientTransform`, which may hold only
  `matrix`, `translate`, `scale`, `rotate`, `skewX` and `skewY` (case-sensitive)
  with numeric arguments (digits, sign, dot, `e`, comma, spaces). Error:
  `function-style values are not allowed here`.
- Size caps, so a small file cannot make a browser work hard: at most 20000
  elements, nesting 64 deep (the root counts as 1), 64 attributes on one
  element, 8 namespace declarations in the whole file, and 1 MiB of attribute
  text in total. A file over a cap stops the parse at that point and the build
  fails with, for example, `theme.mascot: 'm.svg' is refused as an svg: it has
  more than 20000 elements`. Removing a namespace with `xmlns=""` is refused
  the same way as any other namespace outside the SVG and xlink ones.

**To allow another SVG feature**, add its element or attribute name to
`ALLOWED_ELEMENTS` or `ALLOWED_ATTRIBUTES` in
`src/reach_around/site/assetcheck.py` and add a test for it in
`tests/site/test_assets_content.py`. The allowlist is the extension point;
nothing else needs to change.

**Dimming the background.** `theme.background_dim` (a number from 0 to 0.9,
default 0) lays a black overlay over `theme.background` only; the faded mascot
and the page content are not dimmed. If your background image has a bright
subject behind the header, raise this. For a square image with a full-strength
sticker behind the header, `background_dim: 0.6` keeps the white social icons
readable (about 5.7:1 against pure white behind them).

```yaml
theme:
  background: media/background.jpg
  background_dim: 0.6
```

The mascot is faint on purpose (`--ra-mascot-opacity: 0.12`). If your image
is already faded, raise it in `custom.css`:

```css
:root { --ra-mascot-opacity: 0.6; --ra-mascot-position: left center; }
```

`examples/instance/media/mascot.svg` is a synthetic placeholder drawn for this
repository. Build the example with `--config examples/instance/site.yaml` to
see every slot filled. Brand colors are tokens too: set `theme.colors.accent`
(default `#FBB03B`), `on_accent` (`#1A1A1A`, the text on the accent), `bg`
(`#0E0E0E`) and `text` (`#FFFFFF`). The font is Jost, shipped with the engine
under its OFL license; set `theme.fonts.body` to change it.

A link with `featured: true` becomes the taller highlighted pill; use it once,
for the top link.

### Page titles

By default a page is titled `<tab> - <creator.name>`. To set the name of the
whole site, add `creator.title`; the first tab then uses it alone and the other
tabs read `<tab> - <title>`. For full control, set `title_template` using only
`{tab}`, `{creator}` and `{site}`, and give any one tab its own `page_title`
to replace the result for that page:

```yaml
creator:
  name: Example Creator
  title: Example Creator Links
title_template: "{tab} | {site}"
```

Anything else in braces is refused when the config loads. The rules and limits
are in [Page titles](config.md#page-titles).

## Link thumbnails

A link can carry its own round picture, like the thumbnails on a Beacons page.
Add `image` (and, if the picture means something, `image_alt`) to the link:

```yaml
- id: spring-meetup
  title: Spring Meetup Tickets
  url: https://tickets.example.invalid/spring
  image: media/thumbs/meetup.jpg
  image_alt: Crowd at last year's meetup
```

- `image` is a path under your instance folder, with the same rules and the same
  checks as `creator.avatar`: `.png`, `.jpg`, `.jpeg`, `.webp`, `.avif` or `.gif`
  (`.svg` only if it passes the SVG allowlist), the bytes must match the
  extension, no symlinks, no `..`, never inside `static/` or `privacy/`. A URL is
  refused: thumbnails are always copied to your own site, so the page's
  `img-src` stays as it is. Errors name the key, for example
  `tabs[0].blocks[1].links[2].image: 'media/x.png' is not a real png file`.
- `image_alt` is up to 120 characters. Left empty, the picture is decorative and
  screen readers skip it. It is refused without `image`.
- When a link has both `image` and `icon`, the picture wins and the icon is not
  drawn.
- The page draws the picture at 44 by 44 pixels, cropped to a circle with
  `object-fit: cover`. The `<img>` has a fixed `width` and `height`, `loading="lazy"`
  and `decoding="async"`, so the layout does not jump and pictures below the fold
  wait. Change the crop with the `--ra-thumb-radius` token (`50%` is a circle).
- **Size cap: 256 KiB per thumbnail.** The build has no image-processing
  dependency and never resizes, so it refuses a bigger file and tells you to
  resize it. One line with ImageMagick makes a square 128 pixel JPEG (twice the
  display size, for sharp high-density screens):

  ```
  magick input.png -resize 128x128^ -gravity center -extent 128x128 -quality 82 media/thumbs/meetup.jpg
  ```

  (On ImageMagick 6 the command is `convert` instead of `magick`.)

### Getting the pictures from an existing Beacons page

There is no import tool; do it by hand, once:

1. Open your Beacons page in a browser and open the developer tools (F12), then
   the Network tab, filtered to Img. Reload the page.
2. For each link, right-click its thumbnail and choose "Save image as", or copy
   the image address from the Network tab and download it with `curl -L -o`.
   Save them in a folder such as `media/thumbs/` next to `site.yaml`.
3. Resize each with the ImageMagick line above.
4. Set `image:` on the matching link. You only need rights to pictures you
   made or are allowed to reuse.

## Layer 3: layouts and the header

`layout` is `list` (default), `grid` or `cards`. Put it on a tab to lay out
that page's blocks, or on a section to lay out its links. `creator.header` is
`compact` (default), `centered` or `hero`. The `grid-cards` variant uses all
three:

```yaml
creator:
  name: Example Creator
  header: centered
tabs:
  - id: start
    title: Start
    layout: grid
    blocks:
      - id: gear
        title: Gear
        layout: cards
        links: []
```

The same links show in every layout; only the container class changes.

## Layer 4: template and CSS overrides

When config is not enough, point `--overrides` at a folder.

**CSS.** `my-overrides/custom.css` is copied to `static/custom.css` and linked
after `theme.css`, so it wins ties. The theme tokens are CSS custom
properties; reading them keeps your CSS in step with the config. The names in
`static/site.css` today are `--ra-color-bg`, `--ra-color-text`,
`--ra-color-accent`, `--ra-color-surface`, `--ra-color-border`,
`--ra-font-body`, `--ra-font-heading`, `--ra-column-width`, `--ra-radius`,
`--ra-button-radius` and `--ra-bg-image`. Class names you can target include
`a.link`, `.link-title`, `.site-header`, `.header-hero`,
`.hero-card`, `.layout-grid` and `.layout-cards`. Read the packaged
templates and `site.css` for the rest; neither is a stable API yet.

```css
.hero-card { border-width: 2px; }
a.link { letter-spacing: 0.02em; }
```

**Templates.** A file at `my-overrides/templates/NAME.html` replaces the
packaged template of the same name; everything else falls back. The packaged
names are `base.html`, `tab.html`, `privacy.html`, `_header.html`,
`_header_variants.html`, `_nav.html`, `_footer.html`, `_cookie_banner.html`,
`_macros.html` and `blocks/{link,section,text,image,divider}.html`. A
subfolder such as `templates/blocks/divider.html` works. This footer
override was built and checked:

```
<footer><p class="privacy-link"><a href="{{ root }}privacy/">Privacy</a></p><p>Made by {{ creator.name }}</p></footer>
```

A replaced template replaces all of it. The footer above no longer shows the
affiliate disclosure or the cookie notice, because the packaged footer is what
printed them. If you override `_footer.html`, keep the
`show_disclosure` and `show_cookie_banner` blocks from the packaged file, or
the page can claim less than it does. Copy the packaged file from
`src/reach_around/site/templates/` as a starting point and change only what
you need. Template variables (`creator`, `tab`, `blocks`, `nav`, `root`,
`disclosure`, `show_disclosure`, `show_cookie_banner`, `click_tracking`) are
set in `site/build.py` and may change.

## What is deliberately refused

- **Custom JavaScript.** A `.js`, `.mjs` or `.cjs` file in the overrides
  folder is ignored and the build prints
  `ignored extra.js: custom JavaScript is not supported (it would weaken the Content-Security-Policy)`.
- **Third-party fonts, scripts and remote CSS `url()`.** Each one tells another
  host about every visitor. For remote art use an Image block or the theme
  `background`, which you choose explicitly.
- **Inline styles, scripts and event handlers.** The page's security headers
  have no `unsafe-inline`, so a template that adds one will not run in the
  browser.
- **Unsafe templates and CSS.** Templates run in a sandbox; `custom.css`,
  override paths and symlinks are checked and the build stops with a
  one-line error.

The reasons, the exact current rules and the test behind each are in
[the CSP and overrides page](education/csp-and-overrides.md) and in
[the config reference](config.md#instance-overrides-and-security-headers).
They change as the checks are hardened, so they are not repeated here. Real
refusals look like this:

```
error: custom.css is refused: it contains @import
error: custom.css is refused: url() in custom.css must be a relative same-site path (no scheme, no //); use an Image block or the theme background for remote art
error: overrides directory nope does not exist
```

## Preview, build, check

1. **Preview while you edit.**

   ```
   reach-around serve --config site.yaml --overrides my-overrides --port 8000
   ```

   Open `http://127.0.0.1:8000/` for the site or
   `http://127.0.0.1:8000/__preview` for desktop and 390 px phone width side
   by side. It rebuilds when the config, the overrides folder or the packaged
   files change. A broken config shows the error in the browser (HTTP 500)
   until you fix it. It binds to loopback only: `--host 0.0.0.0` stops with
   `error: refusing to bind '0.0.0.0': the preview serves only on a loopback
   address (127.0.0.1, ::1 or localhost)`. Details are in
   [the preview page](preview.md).

2. **Build.**

   ```
   reach-around build --config site.yaml --out out --overrides my-overrides
   ```

   The config is validated first, so a typo or bad value stops here with the
   file and the key path, for example
   `error: bad.yaml: theme.mode: Input should be 'auto', 'light' or 'dark'`.
   `--out` is emptied before each build; the build refuses an output folder
   that is, or contains, your config. A successful build writes `index.html`,
   one folder per extra tab, `privacy/index.html`, `_headers`
   and `static/` (`site.css`, `theme.css`, `click.js` when click tracking is on,
   and `custom.css` when you supplied one).

   The link manifest (`{"links": {id: url}}`, used to fill `LINK_IDS`) is not
   part of the site. `build` writes it next to `--out` as
   `<out>.manifest.json` (for `--out out`, that is `out.manifest.json`), or to
   the path you give with `--manifest`. It refuses a `--manifest` path inside
   `--out`, because everything in `--out` gets published.

3. **Check.** There is no separate check command yet; the build is the check.
   Then read the result:

   - open `out/index.html` or the preview and click through every tab;
   - `grep -n '<script' out/*.html out/*/*.html` should show only
     `static/click.js`, and no `style=` or `on...=` attributes should appear;
   - confirm `out/_headers` is present, because the host serves it.

   If you also run the repository tests, `tests/test_example_variants.py`
   applies these page checks to the example variants.

Keep the overrides folder out of reach of other users and build steps; the
build cannot defend against someone rewriting it while it runs (see the
residual risk in the CSP page).

## Alpha limits

- No release, stable config format or stable template API.
- No `check` or `validate` command; use `build`.
- Template variables and CSS class names are read from the code, not a
  contract.
- `dark-hero` here is an example config; the theme presets live in
  `examples/themes/`.
