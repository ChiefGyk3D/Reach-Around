# ADR 0008: Storage direction (Durable Object counters, hour-rounded raw events, two D1 databases)

- Status: planned (decided 2026-11-24; the storage slices are not merged)
- Date: 2026-11-24
- Source: maintainer decision of 2026-11-24 (storage decisions and privacy decisions); issue #772 (leads database); `docs/analytics-design.md` (describes the earlier, single-database shape)

## Context

Every counted click and view was a D1 write, and D1 writes were the constraint that forced the move to the Paid plan (ADR 0007). Raw events and leads also sat close together, although they have very different sensitivity and retention.

## Decision

- Durable Objects hold the hot counters. The accepted trade is a loss window of up to 60 seconds if an object fails before it flushes. Counts are documented as approximate.
- Raw events stay in D1, rounded to the hour and kept for a short window. Analytics Engine is used only for daily floored counters, and is optional. The older 90 day split for marginal points stays.
- There are two D1 databases, one for analytics and one for leads, plus an ingest path protected by Cloudflare Access. Leads are kept apart from everything else.
- The public stats JSON is totals plus a top 5 list, floored.
- Both migration baselines are reset, because nothing is in production yet.

## Consequences

- Fewer D1 writes per event, and a smaller blast radius if the analytics database is exposed.
- A 60 second loss window is a real cost. It is acceptable for counts and would not be for leads, which is why leads do not go through the counters.
- More moving parts: Durable Objects, two databases, an Access policy. Each needs its own page in the architecture section and its own backup and restore note.
- Resetting the baselines is only safe because there is no deployed data. After a first production deploy it stops being an option.
- INFERRED: whether R2 is used at all, and the exact Cloudflare Access plan terms, are not settled by this decision. Check the Worker bindings before the architecture pages say either way.
