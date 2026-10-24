<!-- SPDX-License-Identifier: Apache-2.0 -->
# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/);
while the project is alpha, every release is `0.x` and anything can change between them.

## [Unreleased]

- An `email` block for a Contact tab: the built page never holds the plain address or a `mailto:` link, only a spelled-out form (`name at example dot test`, words configurable with `spell`) and the address reversed and base64 encoded in a data attribute. `email.js` reveals it on a click (or at load with `reveal: always`) as selectable text with Write email and Copy buttons; `reveal: never_js` ships no script. Assets are published only on pages with the block. Reduces cheap scraping only. Browser tests use a `window.__raOpen` seam and a repo guard keeps tests from ever navigating to `mailto:` or `tel:`.
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
