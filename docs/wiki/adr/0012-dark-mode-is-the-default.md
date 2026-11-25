# ADR 0012: Dark mode is the default

- Status: accepted
- Date: 2026-10-24
- Source: `docs/design.md` (sections on tokens and "Dark by default"); the maintainer's standing rule of 2026-11-09 (INFERRED as the origin of the "no light mode unless asked" part; not a repo file)

## Context

The page should look good on a stage-lit stream and in a dark room, and large light fills are hard on light-sensitive readers.

## Decision

Pages are dark unless the config says `mode: light`. The shared token layer is dark by default, and the default colors are `light-dark()` pairs. Both palettes are held to WCAG AA contrast, and the tests compute the ratios. Brand components are dark only. The project's own documentation follows the same rule: dark, no third-party script.

## Consequences

- A creator who wants a light page opts in, and owns the contrast of any colors they override.
- Every new component needs a dark design first. A light variant is extra work, not a given.
- Screenshots and docs show the dark theme.
