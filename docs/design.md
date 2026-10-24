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
   keep `accent` at 4.5:1 or better against `bg` (it is used for links and the
   focus ring) and `on_accent` at 4.5:1 or better against `accent` (the tab bar
   and link buttons put `on_accent` text on an `accent` fill).

## Settable from the config `theme`

| Config key | Token | Default |
| --- | --- | --- |
| `colors.bg` | `--ra-color-bg` | warm light gray, charcoal in dark |
| `colors.surface` | `--ra-color-surface` | white, dark brown-gray in dark |
| `colors.text` | `--ra-color-text` | near black, warm white in dark |
| `colors.accent` | `--ra-color-accent` | brand orange `#FBB03B` in both modes |
| `colors.on_accent` | `--ra-color-on-accent` | `#1A1A1A`, the text on the accent fill |
| `colors.border` | `--ra-color-border` | light tan, dark brown-gray in dark |
| `fonts.body` | `--ra-font-body` | Jost (shipped in `static/fonts/`, OFL license), then `system-ui` |
| `fonts.heading` | `--ra-font-heading` | the body font |
| `background` | `--ra-bg-image` | none (the page background image, fixed, under the texture) |
| `background_dim` | `--ra-bg-dim` | `0` (0 to 0.9; darkens the page background image) |
| `mascot` | `--ra-mascot-image` | none (the giant faded mascot, a fixed layer behind the column) |
| `column_width` | `--ra-column-width` | `35rem` (560 px) |
| `radius` | `--ra-radius` | `0.5rem` |
| `button_style` | `--ra-button-radius` | `2.25rem` (36 px); `pill` and `square` set it |
| `mode` | `color-scheme` | `auto` is the stylesheet default (dark); `light` forces light |

The default colors are `light-dark()` pairs: the first value is used in light
mode, the second in dark mode. A color you set in `theme.colors` applies to
both, so pair it with `mode:` when you want one look.

## Derived tokens

These follow the five colors, so a retheme needs no extra work. They can still
be overridden by a creator who adds a stylesheet of their own.

| Token | Meaning |
| --- | --- |
| `--ra-color-on-accent-muted` | The link subtitle on the accent fill: 85% on-accent over the accent. |
| `--ra-color-accent-edge` | The darker 1 px border of a link button and the active tab fill. |
| `--ra-color-chip` | The round thumbnail background on a link button. |
| `--ra-color-link` | The accent as text or focus ring on the page: the accent in dark mode, a darker mix in light mode. |
| `--ra-color-muted` | Secondary text: 72% text over the background. |
| `--ra-color-raised` | A card on hover, and links inside a section. |
| `--ra-color-accent-soft` | The tint behind a link's icon. |
| `--ra-color-highlight` | The faint top edge on cards. |
| `--ra-color-dim` | The black overlay laid above the background image: the dim amount as a percentage of black. The mascot is not dimmed by it. |
| `--ra-color-glow` | The soft center light behind the column and the hero card. |
| `--ra-color-grain`, `--ra-color-grain-deep` | The light and dark hatches of the carbon-fiber texture. |
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
| `--ra-tab-radius` | Corner radius of the tab bar, `0` (flush). |
| `--ra-avatar-radius` | Corner radius of the avatar, `50%` (a circle). |
| `--ra-thumb` | Size of the round thumbnail slot and the three-dot slot on a link button. |
| `--ra-thumb-radius` | Corner radius of a link's own thumbnail picture (`image`), `50%` (a circle). |
| `--ra-gutter` | Side padding inside the column on a phone and a desktop. |
| `--ra-shadow-pill` | The soft shadow under the tab bar and link buttons. |
| `--ra-shadow-card`, `--ra-shadow-lift` | Resting and hover depth. |
| `--ra-mascot-opacity`, `--ra-mascot-size`, `--ra-mascot-position` | How the mascot is drawn: faint (0.12), covering the screen, centered. Tune them in `custom.css` for a photo that is already faded. |
| `--ra-duration`, `--ra-ease` | Timing of hover, focus and open/close motion. |

## Behavior the stylesheet guarantees

- **Mobile first.** One column, no horizontal page scroll at 360 px, long titles
  wrap, every control is at least 44 px tall.
- **Tab bar.** An accent-colored bar flush to the top of the column with uppercase
  labels, smaller below 430 px. It scrolls sideways when the tabs do not fit, and a soft edge
  shadow shows on the side that has more.
- **Link buttons.** Full-width accent pills with dark text, a 1 px darker
  border and a soft shadow. A link's `image` (or else its `icon`) shows as a round thumbnail at the
  left; a three-dot glyph at the right is a visual cue only. `featured: true`
  makes a taller pill with no border or dots.
- **Collapsible sections** use the native `<details>` element. The chevron
  rotates and the content eases open (in browsers that support
  `::details-content`; elsewhere it fades in). No script is involved.
- **Focus.** Every link, summary and button shows a 3 px `:focus-visible` ring in
  the accent color. The skip link is the first stop.
- **Reduced motion.** With `prefers-reduced-motion: reduce`, all animation and
  transition stops.
- **Dark by default.** The page is dark unless `mode: light`. Both palettes pass
  WCAG AA; the tests compute the ratios (dark `#1A1A1A` on `#FBB03B` is 9.4:1,
  white on `#0E0E0E` is 19.3:1).

## Icons

Social icons come from a small local set in `static/icons/`, from
[Simple Icons](https://simpleicons.org) (CC0 1.0) plus two generic glyphs. See
`static/icons/NOTICE`. They are drawn with CSS `mask-image`, so they take the text
color and no request leaves the site.

The `icon` field of a social or a link is a slug from this list: `twitch`,
`kick`, `mastodon`, `bluesky`, `tiktok`, `discord`, `youtube`, `github`,
`instagram`, `threads`, `matrix`, `x`, `linkedin`, `patreon`, `email`, `rss`,
`link`, `bitcoin`, `ethereum`, `merch`, `monero`, `solana`. An unknown slug shows the text label instead. Brand marks remain
trademarks of their owners.

## Known limits

- Below 430 px the tab labels and padding shrink so about four short tabs fit in
  one row. With more tabs or longer labels the bar scrolls, and the current tab
  starts in view through CSS `scroll-initial-target` (Chrome 133 and
  newer; other browsers may start with the current tab off to the side).
- `light-dark()` and `color-mix()` need a browser from 2024 or newer (Chrome 123,
  Firefox 120, Safari 17.5). Older browsers drop the colors and show the
  browser defaults, which stay readable.
