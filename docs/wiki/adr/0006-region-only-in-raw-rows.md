# ADR 0006: Region only in raw rows

- Status: planned (decided 2026-11-24; the shipped code still stores city, so the change is not merged)
- Date: 2026-11-24
- Source: maintainer decision of 2026-11-24 (privacy decisions); `docs/analytics-design.md` (section 1 and 3, which describe the earlier shape)

## Context

The first analytics design stored country, region and city per raw view. City is more precise than the product needs, and raw rows are the most sensitive thing the system keeps.

## Decision

Raw rows carry the region and nothing finer. City appears only in daily counters that apply a privacy floor, so a city with too few visitors is folded away instead of shown. Coordinates and postal codes are never stored.

## Consequences

- A raw row is less identifying if the database is ever exposed.
- The owner still gets a city view, but only for cities with enough traffic to hide any one visitor.
- The shipped analytics design and the privacy page must be updated when the change lands. Until then they describe the old behavior. Do not quote them as current on this point.
- The `GEO_LEVEL` setting stays as a way to go coarser.
