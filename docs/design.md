# Design and theme tokens

The link page is plain HTML and one stylesheet. No script runs to draw it, no
font or image comes from another host, and nothing is styled inline, so a strict
Content Security Policy can stay on. Every color, size, radius, space and
shadow reads a `--ra-*` custom property declared once in the `:root` block of
`static/site.css`. A creator restyles the page by changing tokens, never by
editing the stylesheet.

## How to restyle

1. Set the `theme` section of the config (see [config.md](config.md#theme)).
   The build writes only the values you change to `static/theme.css`, which
   loads after `site.css`.
2. Start from a preset in `examples/themes/` and paste it under `theme:`:
   - `dark-hero.yaml`: charcoal, warm orange, pill buttons. Pair it with
     `creator.header: hero`.
   - `light-clean.yaml`: soft gray page, white cards, deep blue accent.
3. Keep text contrast at 4.5:1 or better against both `bg` and `surface`, and
   keep `accent` at 4.5:1 or better against `bg` (it is used for links, the
   focus ring and the active tab).

## Settable from the config `theme`

| Config key | Token | Default |
| --- | --- | --- |
| `colors.bg` | `--ra-color-bg` | warm light gray, charcoal in dark |
| `colors.surface` | `--ra-color-surface` | white, dark brown-gray in dark |
| `colors.text` | `--ra-color-text` | near black, warm white in dark |
| `colors.accent` | `--ra-color-accent` | burnt orange, bright orange in dark |
| `colors.border` | `--ra-color-border` | light tan, dark brown-gray in dark |
| `fonts.body` | `--ra-font-body` | `system-ui, sans-serif` |
| `fonts.heading` | `--ra-font-heading` | an old-style serif system stack |
| `background` | `--ra-bg-image` | none (artwork drawn faintly behind the column) |
| `column_width` | `--ra-column-width` | `36rem` |
| `radius` | `--ra-radius` | `0.5rem` |
| `button_style` | `--ra-button-radius` | follows `--ra-radius`; `pill` and `square` set it |
| `mode` | `color-scheme` | `auto` follows the visitor; `light` or `dark` forces it |

The default colors are `light-dark()` pairs: the first value is used in light
mode, the second in dark mode. A color you set in `theme.colors` applies to
both, so pair it with `mode:` when you want one look.

## Derived tokens

These follow the five colors, so a retheme needs no extra work. They can still
be overridden by a creator who adds a stylesheet of their own.

| Token | Meaning |
| --- | --- |
| `--ra-color-on-accent` | Text on an accent fill (active tab, hovered icon). The page background color. |
| `--ra-color-muted` | Secondary text: 68% text over the background. |
| `--ra-color-raised` | A card on hover, and links inside a section. |
| `--ra-color-accent-soft` | The tint behind a link's icon. |
| `--ra-color-highlight` | The faint top edge on cards. |
| `--ra-color-glow` | The warm glow behind the header and the hero card. |
| `--ra-color-grain` | The fine diagonal texture on the page. |
| `--ra-color-edge` | The cue at a scrolling tab bar's edge when more tabs hide. |
| `--ra-shadow-color` | The shadow tint. |

## Type, space, shape and motion

| Token | Meaning |
| --- | --- |
| `--ra-text-xs`, `--ra-text-sm`, `--ra-text-base`, `--ra-text-md`, `--ra-text-lg`, `--ra-text-xl`, `--ra-text-2xl` | The type scale, a 1.25 ratio from 1rem. `--ra-text-2xl` is the fluid hero name. |
| `--ra-line-height` | Body line height. |
| `--ra-space-1`, `--ra-space-2`, `--ra-space-3`, `--ra-space-4`, `--ra-space-5`, `--ra-space-6` | The spacing scale on a 4-point rhythm (0.25rem to 2.5rem). |
| `--ra-tap` | Minimum touch target, `2.75rem` (44 px). |
| `--ra-card-radius` | Corner radius of cards and sections. |
| `--ra-tab-radius` | Corner radius of tabs. |
| `--ra-shadow-card`, `--ra-shadow-lift` | Resting and hover depth. |
| `--ra-bg-image-opacity`, `--ra-bg-image-size`, `--ra-bg-image-position` | How the background artwork is drawn. It is faint by default so text stays readable. |
| `--ra-duration`, `--ra-ease` | Timing of hover, focus and open/close motion. |

## Behavior the stylesheet guarantees

- **Mobile first.** One column, no horizontal page scroll at 360 px, long titles
  wrap, every control is at least 44 px tall.
- **Tab bar.** It scrolls sideways when the tabs do not fit, and a soft edge
  shadow shows on the side that has more.
- **Collapsible sections** use the native `<details>` element. The chevron
  rotates and the content eases open (in browsers that support
  `::details-content`; elsewhere it fades in). No script is involved.
- **Focus.** Every link, summary and button shows a 3 px `:focus-visible` ring in
  the accent color. The skip link is the first stop.
- **Reduced motion.** With `prefers-reduced-motion: reduce`, all animation and
  transition stops.
- **Light and dark.** Both default palettes pass WCAG AA; the tests compute the
  ratios.

## Icons

Social icons come from a small local set in `static/icons/`, from
[Simple Icons](https://simpleicons.org) (CC0 1.0) plus two generic glyphs. See
`static/icons/NOTICE`. They are drawn with CSS `mask-image`, so they take the text
color and no request leaves the site.

The `icon` field of a social or a link is a slug from this list: `twitch`,
`kick`, `mastodon`, `bluesky`, `tiktok`, `discord`, `youtube`, `github`,
`instagram`, `threads`, `matrix`, `x`, `linkedin`, `patreon`, `email`, `rss`,
`link`. An unknown slug shows the text label instead. Brand marks remain
trademarks of their owners.

## Known limits

- The tab bar cannot scroll the current tab into view without script. On a very
  narrow screen with many tabs, the active tab may sit off to the side.
- `light-dark()` and `color-mix()` need a browser from 2024 or newer (Chrome 123,
  Firefox 120, Safari 17.5). Older browsers drop the colors and show the
  browser defaults, which stay readable.
