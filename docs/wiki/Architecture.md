<!-- SPDX-License-Identifier: Apache-2.0 -->
# Architecture overview

Reach Around is pre-release software. This page describes what the code does
today. Parts that are only decided or designed are labeled **planned** and are
not built. A claim that was not read from code or from the repo's own docs is
marked **INFERRED**.

Contents: [Parts](#the-parts) · [Diagrams](#diagrams) · [Why it is shaped this way](#why-it-is-shaped-this-way) ·
[Planned](#planned-not-built) · [Sources](#sources)

## The parts

| Part | What it is | Status |
|---|---|---|
| Static builder | Python, in the engine container image. Turns `site.yaml` and media into plain HTML and CSS | Built |
| Static site | The built files, served from Cloudflare Pages | Built |
| Worker | One Cloudflare Worker (`worker/src/index.ts`): click route, page-view route, beacon token mint route, live status route, media kit gate and inquiry routes, and scheduled jobs | Built |
| D1 | One SQLite database for the Worker: raw click and view rows (short retention), daily aggregates, kit leads, budget counters, coverage and health rows | Built |
| KV | Holds the gated kit page and the live status. Both bindings are optional | Built, optional |
| Rate limiting bindings | Per-client allowances in front of each route | Built |
| Turnstile | Cloudflare's bot check on the kit gate, and an optional invisible check for beacons through a hidden same-origin helper page | Built |
| Collectors | Python code that reads platform APIs into a local history store | Built |
| Owner tools | The report, the kit PDF and the Grafana export. They run on the owner's machine, never on Cloudflare | Built |
| Durable Objects | Buffering counters before they reach D1 | **Planned** |
| Analytics Engine | Daily floored counters | **Planned**, optional |
| KV pre-rendered stats | Public stats JSON served from KV | **Planned** |
| R2 | File storage | **Planned**, and not confirmed to be needed. No R2 binding exists in `worker/wrangler.toml` |
| Two D1 databases | A split of the data across two databases | **Planned**. The Worker binds one today |
| Separate frame domain | A dedicated origin for the Turnstile helper frame | **Planned** |

## Diagrams

Each diagram is Mermaid source with a plain-text description under it. Render
the source with a dark theme; none of them sets a color.

Diagram 1: [Architecture overview](diagrams/01-architecture-overview.md): what runs where.
Diagram 3: [Deployment](diagrams/03-deployment.md): how a change reaches the live site.
Diagram 4: [The wall](diagrams/04-the-wall.md): public, gated and owner-only kit content.

(The numbers follow the wiki plan. The data flow diagram, number 2, is held
until the storage work lands, so it does not describe a design that is still
moving.)

## Why it is shaped this way

**A static site first.** The link page is plain HTML and two stylesheets, with
no script needed to draw it and no font or image from another host. A visitor
can read it with nothing else running, and a strict Content Security Policy can
stay on. The Worker exists only for what a static file cannot do: count a click,
count a view, check a bot, and gate a page.

**The Worker is small and fails closed.** Every route has a switch that turns it
into an empty 204 without touching D1, and a missing secret or malformed binding
makes a route refuse rather than run without a limit. The reason is cost and
safety: on a paid platform a limit that fails open is a bill. Each limit fails
closed and is visible in the health alerts. See
[failure-modes.md](../failure-modes.md).

**Raw rows are short-lived.** Click and view rows are purged after a retention
window and rolled into daily aggregates first. The purge runs before the rollup
so a failing rollup can never block retention. A missing stretch of data is shown
as a gap, never as a zero.

**An engine and an instance, in two repositories.** The engine is public and
versioned. Your instance is a private repository with your config, media and
deploy workflow; it pins an engine release by digest and commit. A pinned digest
cannot be moved the way a tag can, and an update is a pull request you can read.
See [instance-repos.md](../instance-repos.md).

**The wall is structural.** The kit has a public half and a gated half. Each is
rendered from its own projection of the config and written to its own directory,
so a template cannot reach a field of the other half. A search of every public
file for each gated value is a second layer, and it fails the build on a hit.
See [kit.md](../kit.md#the-wall-between-the-two).

**One shared deploy.** The Worker deploy is a reusable workflow, so the engine's
own test deploy and an instance's deploys share one implementation. The
Cloudflare token comes from the secret manager over GitHub OIDC, not from a
GitHub secret.

**Secrets stay out of the repo.** The Worker secrets (such as the visitor hash
key and the kit signing key) are set with `wrangler secret`, never in
`wrangler.toml`, which holds placeholders only.

## Planned, not built

These are decisions or designs. No code exists for them in the Worker today, so
nothing above depends on them.

- **Durable Objects** for buffering counters, with a short loss window accepted.
- **Analytics Engine** for daily floored counters, optional.
- **KV pre-rendered stats** for the public stats JSON.
- **R2**: INFERRED to be unneeded until a feature requires file storage.
- **Two D1 databases** instead of one.
- **A separate frame domain** for the Turnstile helper. Today the helper is a
  sandboxed same-origin page at `/e-frame/`, linked from nowhere. The deploy
  guide names a dedicated origin as the fallback if the sandboxed helper cannot
  produce a token.

## Sources

Read for this page: `worker/wrangler.toml`, `worker/src/index.ts`,
`docs/design.md`, `docs/kit.md`, `docs/instance-repos.md`,
`docs/failure-modes.md`, `docs/deploy.md`, `.github/workflows/deploy-worker.yml`
and `src/reach_around/site/beaconframe.py`.

INFERRED, not read from code: that the static site deploy and the Worker deploy
run in parallel in an instance workflow (the template's `deploy.yml` was only
skimmed); that collectors can run from an instance workflow (the template has a
`refresh-counts.yml`, not read in full); and that R2 is not needed.
