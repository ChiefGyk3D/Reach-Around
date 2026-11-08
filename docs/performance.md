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

## Sweep 2 (#363)

Measured 2026-11-08, Lighthouse 13.6.0, Chromium 156, three runs per page and strategy, median. Local pages were served with the build's own `_headers` and gzip. Pages: the default link page (first tab, second tab, privacy), the same instance with a `kit:` section (kit page and `/kit/retry/`), the 404 page (served as `/404.html`, because Lighthouse refuses a page that answers with status 404) and the `dark-hero` example (a themed link page with no favicon configured). The gated kit page is served by the Worker after the gate and was not measured.

| Page | Mobile before | Mobile after | Desktop before | Desktop after |
| --- | --- | --- | --- | --- |
| Home, events, privacy, kit | 100/100/100/100 | 100/100/100/100 | 100/100/100/100 | 100/100/100/100 |
| Themed link page (`dark-hero`, no favicon) | 100/100/96/100 | 100/100/100/100 | 100/100/96/100 | 100/100/100/100 |
| `/kit/retry/` | 100/100/100/63 | 100/100/100/63 | 100/100/100/63 | 100/100/100/63 |
| 404 | 100/100/100/63 | 100/100/100/63 | 100/100/100/63 | 100/100/100/63 |
| Test host, link page (not built from this branch) | 98-99/100/92/92 | n/a | 100/100/92/92 | n/a |

What changed: a page with no `creator.favicon` made the browser request `/favicon.ico`, the host answered 404 and Best Practices lost 4 points (`errors-in-console`). Every page now links a packaged default icon (`static/favicon.svg`, fingerprinted, same origin) when no favicon is configured. A configured favicon still wins. No CSP, budget or script change.

Remaining items:

| Item | Page | Reason | Who |
| --- | --- | --- | --- |
| `is-crawlable` (SEO 63) | 404, `/kit/retry/` | Both carry `noindex` on purpose; the audit fails for any `noindex` page | Nobody; keep `noindex` |
| Best Practices 92: `errors-in-console`, `inspector-issues` | test host | The host injects Cloudflare's analytics script (`static.cloudflareinsights.com`); the page CSP blocks it. Not in the built output | Owner, host setting (#405) |
| SEO 92: `meta-description` | test host | The deployed build predates #359; the current build has the description | Redeploy from main |
| Performance 98 to 99 on mobile | test host | LCP and FCP scores of 0.94 to 0.97 on the network path; run to run variance | Host and network |

## Browser security headers (#443)

Measured 2026-11-08 with Lighthouse 13.6.0 (Chromium 156) against a local production build served with the build's own `_headers`: the example instance with a `kit:` section, mobile, best practices category. The "Trust and Safety" items are the audits `csp-xss`, `has-hsts`, `origin-isolation`, `clickjacking-mitigation` and `trusted-types-xss`. They are informational and unscored.

| Item | Link page before | Link page after | Kit page (`/kit/`) before and after |
| --- | --- | --- | --- |
| CSP host allowlist (`csp-xss`) | not applicable (no allowlist) | same | note shown: the page CSP pre-allows `https://challenges.cloudflare.com` for the planned Turnstile widget (no script is loaded by the current build). It cannot be removed once the widget is wired in |
| HSTS (`has-hsts`) | "No `preload` directive found" | same | same |
| COOP (`origin-isolation`) | not applicable (`same-origin` is sent) | same | same |
| Clickjacking (`clickjacking-mitigation`) | not applicable (`frame-ancestors 'none'`) | same | same |
| Trusted Types (`trusted-types-xss`) | "No Content-Security-Policy header with Trusted Types directive found" | not applicable (directive present) | note stays: see below |

Best Practices score 100 before and after on both pages.

### Trusted Types

Every page CSP except the two gate paths (below) now carries `require-trusted-types-for 'script'`, generated in the one place the CSP is built (`build_csp`). The page scripts (`click.js`, `view.js`, `live.js`, `copy.js`, `email.js`) were read for DOM injection sinks: none use `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, a string `setTimeout`, or assign a script `src`. Text is written with `textContent`, elements are built with `createElement`, and the live player sets an iframe `src`, which is not a Trusted Types sink (only `srcdoc` is). No policy is needed, so no `trusted-types` directive is added.

Checked in headless Chromium 156 with the new header: the link page and its second tab, the copy buttons of the crypto example, the email reveal example, the public kit page and the retry page. No console warnings or errors and no `securitypolicyviolation` events.

Declined: the two gate paths (`/kit/`, `/kit/retry/`) do not get the directive. The build does not load the Turnstile script today (the pages hold only the widget placeholder and make no third-party request), but their CSP pre-allows its origin for the planned widget, which would be third-party code. No source was found that says whether it works under `require-trusted-types-for` (two searches, one on Turnstile and Trusted Types, one on the TrustedHTML error with `api.js`; neither found a statement), and a break would stop the kit unlock once the widget is wired in. The rule in `build_csp` is general: a path that adds a third-party origin to `script-src` does not get the directive. Re-test with the live widget on staging, then drop the exception. The repo does not build a CSP for the Worker-served gated kit page, so there is nothing to change there; the Worker sends `default-src 'none'` only on its fixed 503 page.

### HSTS preload (not enabled)

Requirements, read from hstspreload.org on 2026-11-08: a valid certificate; HTTP redirected to HTTPS on the same host; every subdomain served over HTTPS, including internal ones; and on the base domain, a header with `max-age` of at least 31536000, `includeSubDomains` and `preload` (a redirect on the HTTPS site must carry the header too). The requirements must be met at all times. The site says inclusion is hard to undo: removal can take months to reach users through a Chrome update and other browsers make no guarantee.

Consequences: the list is per registrable domain, not per host. Preloading `chiefgyk3d.com` makes browsers refuse plain HTTP for the apex and every subdomain, present and future, including hosts used only on a private network or lab. A staging host alone cannot be preloaded; the header on it does not matter to the list. The current header (`max-age=31536000; includeSubDomains`) already meets the max-age and subdomain parts, so the only change to opt in is adding `; preload` here and on the apex.

Owner decision, in order of recommendation:

1. Do not preload now (recommended). Keep the header as is. The Lighthouse note stays and costs no score.
2. First audit every `chiefgyk3d.com` subdomain (public DNS, internal names, anything that answers on port 80 only), and preload only when all serve valid HTTPS and the apex answers with the header. Then add `preload` and submit the apex.
3. Preload now. Accept that any HTTP-only subdomain breaks in browsers until removal propagates.

## Noindex shape (#443)

By design only three outputs carry `noindex`: `404.html`, `/kit/retry/` (both through the page template's `noindex` flag) and the Worker-served gated kit page. Indexable pages (tabs, privacy, the public kit page) do not. `tests/site/test_noindex_shape.py` builds a site with a kit and asserts exactly that list, and that no `robots.txt` or `X-Robots-Tag` rule appears unless the config has a `robots:` section. A staging host that blocks crawlers does so through that config section (`allow_all: false`, `noindex_paths`) or a host setting, never through a build default, so staging settings cannot reach a production build that does not ask for them. `check --production` does not inspect robots; the config is the single source.
