# ADR 0002: Static site built by a Python engine

- Status: accepted
- Date: 2026-10-23
- Source: `docs/superpowers/specs/2026-10-23-reach-around-design.md` (sections 4.3, 4.7, 10); `docs/instance-repos.md`

## Context

The link page and media kit should work without client JavaScript, load fast and be easy to review. The design left the generator and its language to the builder.

## Decision

A Python engine (3.11 or newer) reads a validated configuration file and builds the static site. Tabs have their own URLs, collapsible sections use native disclosure elements, and the build refuses ambiguous or forbidden input. The same engine ships as a container image, so a build runs the same way on a laptop and in CI.

## Consequences

- Config is the source of truth. A bad config fails the build instead of producing a wrong page.
- The build can enforce rules a template cannot, such as refusing to render a field a platform's terms forbid on a public page.
- Python was chosen because it matches the maintainer's CI. The design spec marks the language as the builder's call, so the choice is a default, not a promise to every contributor. INFERRED: the spec's decisions table lists "Python 3.11+ default for collectors" as the author's call; its use for the site build follows the same reasoning but is not stated there in so many words.
- The Worker is TypeScript and is not built by this engine. It deploys from the engine's `worker/` folder.
