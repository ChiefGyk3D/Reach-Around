# ADR 0005: No cookies for measurement, aggregate-only analytics

- Status: accepted
- Date: 2026-11-05
- Source: `docs/analytics-design.md` (section 1 and section 7); PR #222 (page-view collector design)

## Context

A link page and a blog need to know roughly how many people visit and where they come from. They do not need to know who those people are. Cookies and stored identifiers would add consent banners, legal duties and tracking risk for little gain.

## Decision

Measurement sets no cookie and writes nothing to `localStorage`, `sessionStorage`, IndexedDB or the Cache API, and uses no fingerprinting inputs. No raw IP address is stored or logged. The address and the raw user agent are read in memory to derive closed categories, then dropped. Unique visitors use a keyed hash that rotates daily, and the daily table is deleted at the end of the UTC day.

What the owner sees is aggregate: counts by day, source, device kind, language and geography, with small cells folded together. Bots are counted separately, not dropped. Raw rows are kept for a short window (7 days by default, 90 at most) and daily aggregates for 90 days.

## Consequences

- Measurement needs no cookie, so there is no cookie prompt to show. This is not legal advice. The privacy page still has to say what is collected and for how long.
- Unique counts are approximate, and returning visitors across days are not tracked. That is the point.
- The geography detail in the raw rows is narrowed by ADR 0006, and the raw-row shape is revisited by ADR 0008. Parts of the analytics design paper still describe the earlier shape.
- The media kit gate is a different case: a partner who submits an email is a lead, with its own retention. See ADR 0011.
