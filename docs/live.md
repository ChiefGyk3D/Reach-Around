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

## Deploying the Live Worker

The Live Worker (`GET /e/live` plus the 2-minute poller, issue #297) is built by
`deploy-test.yml` only when you turn it on. Everything below is a repository
**variable** (not a secret) next to the others in
[deploy.md](deploy.md); the platform credentials stay Worker secrets.

- **Enable condition.** The Live Worker is enabled when the repository variable
  `LIVE_TWITCH_HANDLE` or `LIVE_KICK_HANDLE` is set (they are the same handles
  the Worker already reads, validated by the config model's patterns). With
  neither set, the deploy is exactly the click-counter deploy it was before.
- **`LIVE_KV_ID`** is the id of the KV namespace you created for the
  `LIVE_KV` binding (`npx wrangler kv namespace create LIVE_KV` prints it). It
  must be 32 lowercase hex characters. When Live is enabled and `LIVE_KV_ID` is
  unset, empty, all zeros or malformed, the **Check configuration** step fails
  before any secret is fetched, and the **Write deploy config** step refuses
  again on its own. Nothing is deployed with a placeholder id.
- What the workflow writes into the throwaway `wrangler.deploy.toml` (never
  committed): the handle vars, a `[[kv_namespaces]]` block with `binding =
  "LIVE_KV"` and your id, and `crons = ["5 * * * *", "*/2 * * * *"]` (the
  hourly job stays; the Free plan allows five cron triggers per account).
- To switch Live off again, delete both handle variables and redeploy; to stop
  the poller without a deploy, use the `LIVE_KILL` var.

## Failure codes

Each platform in the `/e/live` JSON carries a `code`, so a `null` platform can be
diagnosed from the body without logs (the module logs nothing):

`{"kick":{"live":null,"since":null,"code":"auth_failed"}, ...}`

| `code` | Meaning |
| --- | --- |
| `ok` | The last lookup worked (`live` is `true` or `false`) |
| `not_configured` | No valid handle, or a client id or secret is missing, so the platform is never polled |
| `auth_failed` | The token endpoint (400, 401, 403) or the API (401, 403) refused the credentials |
| `rate_limited` | HTTP 429 from the platform |
| `upstream_error` | Any other HTTP error, or a network failure |
| `redirect` | The platform answered with a redirect (never followed, so no credential is forwarded) |
| `too_large` | The reply was over the 64 KB cap |
| `bad_record` | The reply was not the expected JSON shape |
| `timeout` | No answer within 5 seconds |
| `null` | No code: no state yet, the data is older than 30 minutes, the kill switch is on, or the state was written before codes existed |

The list is closed. A code is one of these fixed strings and never holds a
secret, an upstream body, a handle, a URL or any free text: the poller maps what
it saw to a value, and the Worker re-checks a stored value against the list
before serving it (anything else becomes `null`). The page **ignores** the field:
`static/live.js` accepts an entry with or without `code`, never reads it, and
drops an unknown value; it cannot affect what a visitor sees. The poll rate is
unchanged (every two minutes), and a change of code alone is written to KV once,
like any other change.

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
