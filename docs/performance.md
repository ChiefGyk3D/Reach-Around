<!-- SPDX-License-Identifier: Apache-2.0 -->
# Performance and Lighthouse

Issue #358. Measured 2026-11-08 on the built example site (`examples/instance`, plus a copy with a `kit:` section for the media kit page). Nothing here comes from the live site.

## Method

| Item | Value |
| --- | --- |
| Lighthouse | 13.6.0 (npm, pinned), categories performance, accessibility, best-practices, seo |
| Browser | Chromium 156.0.8078.47, headless, `--no-sandbox` |
| Strategies | mobile (Lighthouse default throttling) and `--preset=desktop` |
| Build | `reach-around build --config site.yaml --out OUT --gated-out GATED` |
| Server | a 60-line local static server that applies the build's own `_headers` (CSP, cache rules, `! Header` removals) and gzip. Cloudflare Pages does the same on the real host, so compression scores here come from the emulation, not from the host |
| Command | `lighthouse http://127.0.0.1:PORT/PATH [--preset=desktop] --output=json --only-categories=performance,accessibility,best-practices,seo` |

Single runs on a laptop that was doing other work. Total Blocking Time moves by 100 to 600 ms between runs of the same page; two extra reruns of the second tab and privacy page on mobile read 100 with 0 ms TBT, so scores of 98 and 99 in the "after" pass are noise, not the page. Treat a one or two point difference as noise.

PageSpeed Insights: not run. The deploy docs name no public staging address (`docs/deploy.md` uses the placeholder `links-test.example.invalid`), and the public API only reaches a public URL. Re-run against the staging hostname when one exists.

## Scores (performance / accessibility / best practices / SEO)

| Page | Mobile before | Mobile after | Desktop before | Desktop after | Desktop CLS |
| --- | --- | --- | --- | --- | --- |
| Home (first tab) | 100/100/100/91 | 100/100/100/100 | 100/100/100/91 | 100/100/100/100 | 0 -> 0 |
| Second tab (events) | 100/100/100/91 | 98/100/100/100 (reruns: 100) | 100/100/100/91 | 100/100/100/100 | 0 -> 0 |
| Privacy | 100/100/100/91 | 99/100/100/100 (reruns: 100) | 100/100/100/91 | 100/100/100/100 | 0 -> 0 |
| Media kit (public) | 83/100/100/91 | 100/100/100/100 | 93/100/100/91 | 100/100/100/100 | 0.162 -> 0 |
| 404 | 100/100/100/54 | 100/100/100/63 | 100/100/100/54 | 100/100/100/63 | 0 -> 0 |

The example site has no post pages or search page; its page types are the tab pages, privacy, the media kit and 404.

## What changed

* SEO 91 to 100: no page had a meta description. Every page now has one (`creator.description`, default `Links and socials from <name>.`).
* Media kit CLS 0.162 to 0 on desktop (mobile performance 83 to 100, partly noise): the body and heading fonts were found only after the stylesheets were parsed, so the swap moved the gate box. Pages now preload the font files they certainly use (bundled body font, plus theme font files). A system font stack preloads nothing.

## Not at 100 and why

| Item | Evidence | Reason | Owner action |
| --- | --- | --- | --- |
| 404 SEO 63 (`is-crawlable`) | audit fails on every run | The 404 page is `noindex` on purpose. On the real host it answers with status 404, which Lighthouse and PageSpeed report as an error for that URL, not a score | None. Do not remove `noindex` |
| Render-blocking CSS (`render-blocking-insight`, est. 50 to 280 ms) | 2 to 3 small stylesheets (`site`, `theme`, plus `kit` on the kit page) | The usual fix is inlining critical CSS, which needs `style-src 'unsafe-inline'` or hashes. The CSP stays `style-src 'self'`. The performance score is already 100 with it | None; revisit only with a measured regression. Merging `site.css` and `theme.css` into one file would save one request and is left out as a build change with no score gain |
| Unminified CSS (`unminified-css`, est. 2 KiB) | `site.css` is 33 KB source, 9.5 KB transferred with gzip | The audit is informational and weightless; savings after compression are small. Minifying would add a CSS parser to the build | None |
| Compression, HTTP version, TLS, HSTS preload | Emulated locally; not measurable here | Set by the host. `_headers` already sends HSTS, a one-year `immutable` cache for hashed files and `max-age=0, must-revalidate` for pages | Run PageSpeed Insights against the staging hostname once it exists and compare |
| Media kit, Turnstile | The built kit page has a `cf-turnstile` container but no script tag, and its CSP allows `https://challenges.cloudflare.com` | If a Turnstile script is added later it is third-party code outside the repo, and the measured scores above do not include it | Re-measure on staging when the widget is wired in |
