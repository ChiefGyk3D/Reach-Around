# ADR 0001: Cloudflare Workers plus D1 as the platform

- Status: accepted
- Date: 2026-10-23
- Source: `docs/superpowers/specs/2026-10-23-reach-around-design.md` (sections 4.3, 4.4, 10); `docs/deploy.md`

## Context

Reach Around needs a place to serve a static page, accept a few small writes (clicks, page views, media kit gate submissions) and keep a little state. The design goal was a small attack surface and a setup one creator can run alone.

## Decision

The visitor gets a static site hosted on Cloudflare. One small Worker handles the writes, and D1 (SQLite on Cloudflare) holds leads, events and counters. The Worker is write-only: it never returns visitor data such as emails or click rows. The owner reads data from their own machine with a scoped token, never through a public route.

## Consequences

- Static output keeps the public surface small, and most pages need no script.
- One vendor carries hosting, the Worker and the database. Quotas and prices are Cloudflare's, and they change. See ADR 0007 for how that shaped the plan choice.
- Anything that needs more than a Worker and D1 can do (Durable Objects, Analytics Engine) is a separate decision. See ADR 0008.
- The design spec says "Pages". Cloudflare has been steering new projects toward Workers static assets. The deploy guide, not this record, names the current recommendation. INFERRED: this record does not pick between the two hosting forms for the static files.
