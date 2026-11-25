# ADR 0010: Third-party frames on a separate registrable domain

- Status: planned (decided 2026-11-24; no DNS or Cloudflare change has been applied)
- Date: 2026-11-24
- Source: maintainer decision of 2026-11-24 (frame domain and the later correction); issue #610; `docs/turnstile-storage-check.md`

## Context

The hidden helper frame that runs the Turnstile check was tried in a sandboxed form. In headless tests that sandboxed frame never received a token. The earlier docs named a dedicated origin as the fallback.

A subdomain of the site's own registrable domain is not enough on its own. The same domain can also serve links, embeds and other pages, and the frame should not share a registrable domain with them.

## Decision

The frame is served from a hostname on a registrable domain that serves nothing else of the site. The real domain stays out of public repositories and docs. Public examples and tests use `example.invalid`, and the real value lives in private deployment notes.

## Consequences

- The frame has its own origin, so the page's content security policy and cookies are not shared with it.
- One more domain to register, renew and put under DNS control, and one more hostname for Turnstile to allow.
- Any step that touches DNS or the Cloudflare account needs the account owner's approval first.
- Operators who deploy this need a spare registrable domain. The deploy guide has to explain why, in plain terms.
