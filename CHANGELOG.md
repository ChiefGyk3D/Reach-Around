<!-- SPDX-License-Identifier: Apache-2.0 -->
# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/);
while the project is alpha, every release is `0.x` and anything can change between them.

## [Unreleased]

- Worker: a daily page-view rollup. Each closed UTC day is aggregated into three marginal tables (page and source by country, one dimension at a time, geography) with capped rows, small-count folding and a per-day checkpoint, so a run is idempotent and a crash resumes. A heartbeat in `view_meta` records the last success, days and rows rolled and a closed error class. Raw view rows are now purged only after their day is rolled up (an unrolled day is kept up to the smaller of three times `RAW_KEEP_DAYS` and 90 days, then dropped and flagged; a failing day is recorded and skipped without blocking later days), and `visitor_seen` is deleted when the UTC day ends regardless of the rollup, and the hourly cron runs click purge, view purge, rollup in isolation. The rollup now runs hourly inside a hard per-invocation D1 query budget (`QUERY_BUDGET`, default 40 for the Free plan; a batch counts as one query), takes a lease so overlapping runs write nothing, waits `ROLLUP_GRACE_HOURS` (default 2) after UTC midnight, and folds every cell under `MIN_CELL` (default 3) views into `other`. The `rollup_overdue` flag now clears itself. Late rows (written after a day sealed) trigger a full re-roll of that day instead of an additive supplement, because folding is not additive; ranking and every fold now run in SQL so the Worker handles only capped rows (Free CPU is 10 ms), the purge runs before the rollup, an oversized day is rolled in a reduced mode (`ROLLUP_MAX_ROWS_PER_DAY`), lease expiry is enforced in every write guard, and `UTM_ALLOWLIST` hides unlisted `utm_source` labels. `MIN_CELL` is documented as label suppression, not k-anonymity. Migration `0007` invalidates old aggregates and adds `view_rollup_log.max_id` and `rerolls`. See `docs/deploy.md` for the write budget and the heartbeat check.
- An `email` block for a Contact tab: the built page never holds the plain address or a `mailto:` link, only a spelled-out form (`name at example dot test`, words configurable with `spell`) and the address reversed and base64 encoded in a data attribute. `email.js` reveals it on a click (or at load with `reveal: always`) as selectable text with Write email and Copy buttons; `reveal: never_js` ships no script. Assets are published only on pages with the block. Reduces cheap scraping only. Browser tests use a `window.__raOpen` seam and a repo guard keeps tests from ever navigating to `mailto:` or `tel:`.
- Every build writes `404.html` at the site root: same theme and tab bar, "That page does not exist.", a link home, `noindex`, no script and no click ping. Its links are root-relative so it works at any URL depth. Cloudflare Pages otherwise answers unknown paths with the home page and status 200.
- A `platforms` block: a laid-out list (or compact `chips` strip) of every
  platform with handle, link, abbreviated follower count and `as of` date, so a
  Socials tab can be the home page. Click-tracked per platform, `platforms.css`
  only on pages that have it. `reach-around build --numbers PATH` (default
  `numbers.json` beside the config) overlays counts from a strictly validated
  JSON file that is never published. See `examples/platforms/`.
- A "Made with Reach Around" credit link in the footer of every page, on by default. `footer.credit: false` removes it and `footer.credit_text` rewords it (plain text, 1-60 characters). Plain https link, no click tracking, no script. The built pages and goldens change by that one line.
- `reach-around counts refresh` pulls follower counts (Bluesky, Mastodon-compatible servers, GitHub, Discord invites, Matrix, YouTube, Twitch) into a validated `numbers.json`, driven by a new optional `numbers` config block. A failed source keeps its previous number and is recorded in `numbers.status.json`; keys come only from environment variables named in the config. Requests are https-only with pinned public addresses, no cross-host redirects, a hard total deadline and size caps.
- `reach-around counts refresh` pulls follower counts (Bluesky, Mastodon-compatible servers, GitHub, Discord invites, Matrix, YouTube, Twitch) into a validated `numbers.json`, driven by a new optional `numbers` config block. A failed source keeps its previous number and is recorded in `numbers.status.json`; keys come only from environment variables named in the config. Requests are https-only with pinned public addresses, no cross-host redirects, a hard total deadline and size caps. A zero or a fall of more than 90 percent over a previous good number is kept out (`zero_suspect`, `drop_suspect`) unless `--accept-drop PLATFORM` is given; runs hold a lock and write through a verified directory, and refuse output paths that could be the config.

- Optional page-view counting: `analytics.page_views` (off by default) and `analytics.route`. When on, the build ships `view.js`, which sends one same-origin beacon per page load (referrer host, `utm_source` label, window-width letter), honors Global Privacy Control and Do Not Track, and uses no cookie or browser storage. The privacy page lists what the counter records. The CSP is unchanged.
- Links and socials can point at another tab with `url: "tab:<id>"` or `"tab:<id>#fragment"`. Same-origin, no click ping, not in the manifest.

## [0.1.0] - 2026-10-24

> **Alpha.** This is the first container image. There is no stable configuration
> format, no upgrade path between `0.x` releases, and no promise that a config that
> builds today builds tomorrow. Do not depend on it yet.

The date is filled in when the `v0.1.0` tag is cut.

### Added

- Link thumbnails: `image` and `image_alt` on a link draw a lazy-loaded, fixed-size
  round picture (256 KiB cap, same-origin only) in place of the icon.
- Package scaffold, `reach-around --version`, and CI built on the shared reusable workflows.
- A hardened YAML loader and validated site config models (strict schema, size and
  nesting limits, no aliases or duplicate keys).
- `reach-around build`: renders a static link page from a config, with tabs,
  collapsible sections, several layouts and block types, a theme set in the config,
  and schedule-aware link visibility.
- A privacy page and cookie disclosure generated with the site, and a strict
  Content Security Policy.
- Instance template overrides, the customization guide and example variants.
- A `crypto` block for a Support tab: copyable public addresses with a Copy
  button, optional QR image, and `copy.js` and `crypto.css` published only on
  pages that use it.
- Optional `profile_card`: creator name, up to eight topic chips and a media-kit
  button after the content of every page.
- Click tracking through the `ping` attribute with a beacon fallback (no redirect
  route), and a write-only Cloudflare Worker with a click endpoint. The Worker is
  in the repository and tested; it is not part of the container image.
- `reach-around serve`: a live preview that binds to loopback only and checks the
  `Host` header.
- A config reference and a hardening page in `docs/`.
- A container image built from `Containerfile` for Docker and Podman: non-root user
  `10001:10001`, read-only root filesystem, hash-pinned dependencies, base image
  pinned by digest, no socket needed. `compose.yaml` and a Podman Quadlet unit are
  included.
- A release workflow that builds, scans, signs and publishes the image to GHCR and
  verifies it, plus `docs/releasing.md`.

### Not included

- Platform collectors, the stored numbers, the media kit and email gate, PDF and
  metric exports, and Grafana support. These are planned and are not in this release.
