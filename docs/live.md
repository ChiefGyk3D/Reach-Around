<!-- SPDX-License-Identifier: Apache-2.0 -->
# Live tab

The `live` block (see [config](config.md#live)) builds a Live page with Twitch and
Kick as co-equal players. This page says what the build produces today and what
must be checked before a release. Issue: #297.

## What the static page holds

One list item with three panels. The page ships in the **degraded** state, so it
is complete with no script:

| Panel | Shown by default | Holds |
| --- | --- | --- |
| `degraded` | yes | two equal links, "Watch live on Kick" and "Watch live on Twitch" (config order), and a small "Live status unavailable." note |
| `offline` | yes (only if `vods` is set) | three equal VOD cards, YouTube, Kick, Twitch, in that order |
| `live` | no (`hidden`) | two identical click-to-load facades in one grid, config order |

Under the panels, shown in every state: the "Also on" line (YouTube, TikTok,
Instagram, as configured) and the fediverse note.

Equal treatment is structural. Both facades are the same component with the same
CSS class and the same fields, in one grid with two identical tracks. There are no
titles and no viewer counts. A facade is a `<button>` with local art; it carries
only `data-platform` and `data-channel`. Its privacy line reads "Loads the player
from Kick. Kick will see your IP address." (and the same for Twitch). The page
makes no third-party request before a click: no iframe, no remote image, no
script from another site.

The Live tab's nav link carries a hidden text badge (`LIVE`, with a dot, so it
never depends on color). It is a plain `hidden` element, not an `aria-live`
region.

## Layout

The Live page uses a wide column (up to 112rem). From 56rem the two players sit
side by side, each at most 55rem wide (1100 px at 2560 px); below that they stack
at the column width. Measured in the browser tests with a compact header and no
avatar or profile card: 1920 x 930 shows two players about 915 px wide with no
vertical scroll, 2560 x 1290 caps each at 1100 px with no vertical scroll, and
393 x 852 stacks them at full width minus two 16 px gutters.

## Content-Security-Policy

Only the Live page's path (`/<tab id>/`, or `/` if it is the first tab) gets a
`frame-src` extra, through the per-path extra in `render_headers`:

```
frame-src https://player.kick.com https://player.twitch.tv
```

Every other path keeps the site-wide policy, which has no `frame-src` (so it falls
back to `default-src 'self'`). `connect-src` stays `'self'`. The extra is checked
by `_check_source`: exact `https` origins only.

## Before a release

- [ ] **Confirm the Kick player host with a plain header probe.** The Twitch host
  (`https://player.twitch.tv`) is Twitch's documented embed host. The Kick host
  (`https://player.kick.com`) is **inferred** from common practice and is not
  confirmed. Before a release, fetch a Kick player URL's response headers (a plain
  `HEAD` or `GET` of the headers only, by the owner's say-so) and check that the
  host answers and that its `Content-Security-Policy: frame-ancestors` or
  `X-Frame-Options` allow framing from this site. If the host differs, change
  `LIVE_PLAYER_ORIGINS` in `src/reach_around/config/blocks.py`; the tests name the
  exact origins. No probe is run by the build or the tests.
- [ ] Confirm Twitch's `parent` requirement for the embed (slice 3 builds the
  player URL) against Twitch's docs.
- [ ] Open the built Live page with the network blocked and confirm no request
  leaves the site before a click.
