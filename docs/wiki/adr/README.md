# Architecture decision records

These records say what Reach Around decided, why, and what it costs. Each one has a status, a date and a source line. The source is a file in this repository, an issue or PR number, or a dated maintainer decision.

Status words:

- accepted: in force and, where it touches code, shipped.
- planned: decided, but the code or docs that carry it are not merged yet. Do not describe it as current behavior.
- superseded: replaced by a later record.

A claim marked INFERRED has no direct source. It is the author's reading and needs the maintainer's confirmation.

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-cloudflare-workers-and-d1-as-the-platform.md) | Cloudflare Workers plus D1 as the platform | accepted | 2026-10-23 |
| [0002](0002-static-site-built-by-a-python-engine.md) | Static site built by a Python engine | accepted | 2026-10-23 |
| [0003](0003-instance-repo-pins-the-engine.md) | The instance repo pins the engine | accepted | 2026-10-24 |
| [0004](0004-the-wall-between-public-gated-and-private-kit-data.md) | The wall between public, gated and private kit data | accepted | 2026-11-07 |
| [0005](0005-no-cookies-for-measurement-aggregate-only-analytics.md) | No cookies for measurement, aggregate-only analytics | accepted | 2026-11-05 |
| [0006](0006-region-only-in-raw-rows.md) | Region only in raw rows | planned | 2026-11-24 |
| [0007](0007-paid-plan-as-the-cost-baseline.md) | The Paid plan is the cost baseline | accepted | 2026-11-19 |
| [0008](0008-storage-direction-durable-object-counters-and-two-d1-databases.md) | Storage direction: Durable Object counters, hour-rounded raw events, two D1 databases | planned | 2026-11-24 |
| [0009](0009-bot-check-turnstile-plus-proof-of-work-switchable.md) | Bot check is Turnstile plus proof-of-work, switchable | planned | 2026-11-24 |
| [0010](0010-turnstile-frame-on-a-separate-registrable-domain.md) | Third-party frames on a separate registrable domain | planned | 2026-11-24 |
| [0011](0011-no-marketing-email-to-leads.md) | No marketing email to leads | accepted | 2026-11-24 |
| [0012](0012-dark-mode-is-the-default.md) | Dark mode is the default | accepted | 2026-10-24 |
| [0013](0013-limits-fail-closed.md) | Limits fail closed | accepted | 2026-11-19 |

Records with INFERRED parts: 0001 (static hosting form), 0002 (Python for the site build), 0008 (R2 and Access terms), 0011 (alert masking format), 0012 (origin of the no-light-mode rule).

Records are drafts until the maintainer approves the wording. A change of mind gets a new record that supersedes the old one, and the old one stays.
