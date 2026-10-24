<!-- SPDX-License-Identifier: Apache-2.0 -->
# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/);
while the project is alpha, every release is `0.x` and anything can change between them.

## [0.1.0] - Unreleased

> **Alpha.** This is the first container image. There is no stable configuration
> format, no upgrade path between `0.x` releases, and no promise that a config that
> builds today builds tomorrow. Do not depend on it yet.

The date is filled in when the `v0.1.0` tag is cut.

### Added

- Package scaffold, `reach-around --version`, and CI built on the shared reusable workflows.
- A hardened YAML loader and validated site config models (strict schema, size and
  nesting limits, no aliases or duplicate keys).
- `reach-around build`: renders a static link page from a config, with tabs,
  collapsible sections, several layouts and block types, a theme set in the config,
  and schedule-aware link visibility.
- A privacy page and cookie disclosure generated with the site, and a strict
  Content Security Policy.
- Instance template overrides, the customization guide and example variants.
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
