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

## The script

`static/live.js` is one pinned file (`script-src 'self'`, no inline script). It is
published, and linked with `defer` on every page that has a tab bar, only on a
site with a live block (the 404 and kit pages carry no script). It does three
things:

- **Status.** It fetches the same-origin `GET /e/live` (no parameters) on load and
  every 60 s while the tab is visible; a hidden tab pauses and one request is made
  when it is visible again. Each request times out after 5 s. The document is
  checked strictly before anything in it is used: exact keys, `v` 1, booleans or
  null, whole numbers in a bounded range, `since` only while live. The body is
  read incrementally and cut off at 2048 bytes. A non-200, a timeout, a network error, malformed data, a
  document with a null `as_of` (the kill switch), one older than 30 minutes (the
  Worker's stale rule, 3 heartbeats) or more than 5 minutes ahead of the visitor's
  clock all count as **degraded**: the script never shows live on a guess.
- **Panels and badge.** The badge (`data-live-badge`) is shown when Kick or Twitch
  is live, on every page. On the Live page: at least one live shows the live
  panel only; both known not live shows the offline panel only; anything else is
  degraded and shows the page as it ships (the watch links and the past streams).
  Each card has two slots of the same size, the player facade and a "Not live right
  now" card with the platform's VOD link, for both platforms; a platform that is
  not live (or unknown, while the other is live) shows the second slot. Once a
  visitor has loaded a player the panels stop changing, so a later poll never
  removes a running stream; the badge keeps following the status.
- **Click to load.** A press on a facade builds the iframe in the browser: Twitch
  `https://player.twitch.tv/?channel=<handle>&parent=<this host>`, Kick
  `https://player.kick.com/<handle>`. The origins are a hard-coded table; the
  handle in `data-channel` is checked against the config model's pattern for that
  platform first. The iframe has `sandbox="allow-scripts allow-same-origin
  allow-presentation"`, `allow="autoplay; fullscreen; picture-in-picture"` (the
  click is the visitor's gesture, delegated to the player; the URL never asks for
  autoplay), `loading="lazy"`, a referrer policy and a title. Nothing from the
  platform's pages is read.

Both platforms go through one table and one code path; the platform names appear
nowhere else in the script (a test checks it).

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
  player URL as `?channel=<handle>&parent=<hostname>`) against Twitch's docs, and
  that `allow-popups` is not needed for the player to work (it is left out).
- [ ] Open the built Live page with the network blocked and confirm no request
  leaves the site before a click.
