# ADR 0013: Limits fail closed

- Status: accepted
- Date: 2026-11-19
- Source: `docs/deploy.md` (section "Kit limits"); `docs/failure-modes.md`

## Context

On a pay-as-you-go platform, an abusive burst or a bad setting can turn into a bill, or into a blind spot where data is silently lost. The project has to choose which way to fail.

## Decision

When a limit is reached, the feature stops and says so. A request over a route budget, a lead or event over its cap, an alert or email over its daily budget, and any invalid setting all get a refusal, never a pass. No paid feature switches on through a limit setting. Counting can stop for the rest of an hour or day while the pages and redirects keep working, and the owner is told through a health alert.

## Consequences

- The bill is capped by the limits, at the cost of a day with missing counts when traffic exceeds them.
- A refused request looks the same to a visitor as an accepted one. The docs do not claim the two are indistinguishable to a patient observer.
- The numeric defaults live in the deploy guide and are re-checked against Cloudflare's current limits before use. They are not repeated here.
