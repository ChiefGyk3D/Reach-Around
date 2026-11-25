# ADR 0009: Bot check is Turnstile plus proof-of-work, switchable

- Status: planned (decided 2026-11-24; the proof-of-work half is not merged)
- Date: 2026-11-24
- Source: maintainer decision of 2026-11-24 (privacy decisions); `docs/turnstile-storage-check.md`; `docs/turnstile-accessibility-check.md`; issue #610 (invisible bot check)

## Context

Unauthenticated write endpoints, such as the click beacon, get abusive traffic. A challenge on the visible link page would hurt real visitors. The invisible Turnstile check runs in a hidden helper frame and is off by default.

## Decision

The beacon bot check combines Cloudflare Turnstile and a proof-of-work step, and each part can be switched on or off by configuration. Turnstile is never loaded for a visitor who sends the Global Privacy Control or Do Not Track signal. The privacy page names Turnstile as a third-party service when it is on.

This record states the policy only. How the checks are tuned, and what limits sit behind them, is deliberately not published.

## Consequences

- A site can run with either check, both, or neither, and the choice is visible in its config.
- Visitors who opt out of tracking are not sent to a third party.
- Proof-of-work costs the visitor's device a little CPU time, and it can fail on very old devices. The accessibility check page lists what to test.
- Cloudflare's own pages disagree about what Turnstile stores in the browser. The storage check script exists to measure it, and its results, not the vendor's wording, go on the privacy page.
