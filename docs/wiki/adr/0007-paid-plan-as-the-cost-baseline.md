# ADR 0007: The Paid plan is the cost baseline

- Status: accepted (supersedes the Free-first profile decision of 2026-11-08)
- Date: 2026-11-19
- Source: `docs/deploy.md` (section "Kit limits", the Free-plan rollout paragraph and the D1 write quota); issue #650; maintainer decision of 2026-11-24 (storage decisions: "Paid is the baseline")

## Context

The project first started D1 on the Free plan with lower caps and planned to measure real row counts for a week before paying. The Free plan allows 100,000 D1 rows written a day. The click write ceiling no longer fit under that figure, and a second site with the funnel tables pushed the modeled worst case over it. When a Free daily limit is reached, queries stop until the next UTC day.

## Decision

The Workers Paid plan (a $5 a month account floor, checked 2026-11-24 against Cloudflare's pricing pages) is the baseline for sizing and defaults. The default D1 daily write quota is 1,600,000, which is the 50 million monthly allowance spread over a 31 day month. A Free deployment is still possible by setting lower values, but it is the exception, not the design target.

The rule that goes with it: when a limit is reached the kit goes unavailable, so the limits cap the bill. Every limit fails closed. No paid feature switches on by itself.

## Consequences

- Creators who want to stay free must lower the click ceiling and keep one site, and the docs have to say that honestly.
- The cost page and calculator must show prices with the date they were read, because Cloudflare changes them.
- Moving a second scheduled trigger onto the Paid plan is a billing step that belongs to the account owner.
- Older text in the deploy guide still describes the Free-first profiles. It is kept for deployments that choose Free, and it is superseded as the default.
