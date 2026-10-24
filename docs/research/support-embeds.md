# Support page embeds: StreamElements, Patreon, Ko-fi, Fourthwall, YouTube and Twitch memberships (issue #137)

> **Status: draft.** First-pass research by an AI agent on 2026-10-24, written for issue #137. It has not been checked by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open. Nothing here was run in a real browser against the site's CSP; the CSP conclusions are reasoned from the headers and scripts that were read.

All fetches are dated 2026-10-24. "Header probe" means a single `curl -I` (or `curl` of one page into a scratch directory under `$HOME/.local/share/reach-around-dev/tmp/`, deleted afterwards) with a plain research User-Agent, no cookies sent, nothing executed. Page bodies were only searched for static `src`/`href` hosts, so hosts that scripts add at run time are UNVERIFIED.

## Summary

- Only **Ko-fi** offers an embed that works in an iframe: the Ko-fi page with `?hidefeed=true&widget=true&embed=true` is served without `X-Frame-Options`, while the plain creator page and the home page send `X-Frame-Options: SAMEORIGIN` and `frame-ancestors 'self'`. Its two script widgets (floating overlay, "Support me" button script) are a bad fit for this site: they inject inline styles, write into iframes with `document.write`, and load a third-party script, so they need `'unsafe-inline'` for styles, which `build_csp` refuses by design.
- **StreamElements** documents a tip page link and nothing else for a website. The tip page sends `X-Frame-Options: SAMEORIGIN`, so it cannot be framed. No embed was found.
- **Patreon**: creator pages send `X-Frame-Options: sameorigin`, so they cannot be framed. The current developer docs describe no button or widget (a script button existed historically; its current state is UNVERIFIED). The official API gives `patron_count` for the creator's own campaign (see `patreon.md`).
- **Fourthwall** documents one embed: an iframe of a single product page that the creator builds by hand. The whole shop cannot be embedded, and cart and checkout leave the frame. A Storefront API exists for building your own frontend.
- **YouTube and Twitch memberships** have no embeddable join or subscribe button. YouTube documents a `/join` link. Twitch's subscribe function exists only inside its "Everything" player-plus-chat embed.
- **Recommendation:** keep the native link-out card for all of them. If the maintainer wants one real embed, allow Ko-fi only, as a click-to-load facade on the Support page, with `frame-src https://ko-fi.com` on that one path. Add API-fed numbers (Patreon patrons, Twitch subscribers) later through the collector, never from the visitor's browser.

## Method and what could not be read

| Source | Result |
|---|---|
| help.ko-fi.com (widget article 360018381678), ko-fi.com/manage/* | HTTP 403 to the fetch tool; plain `curl` got a Cloudflare "Just a moment" challenge. Not read. I did not try to get around the challenge. |
| support.streamelements.com (tipping articles) | Same 403 and challenge. Not read directly; titles and snippets only came through search. |
| docs.streamelements.com `!tip` page | Read (2026-10-24). |
| docs.patreon.com | Read the first 100,000 characters and the remainder from offset 100,000 via fetch (2026-10-24); no button or widget text found. |
| patreon.com/brand | Read; no button assets or code, contact brand@patreon.com. |
| help.fourthwall.com embedding article; docs.fourthwall.com storefront overview, architecture, getting started | Read. |
| dev.twitch.tv/docs/embed | Read. |
| developers.google.com members.list, player_parameters; support.google.com channel memberships (via search snippet) | Read; the YouTube Help page was seen only in a search result, not fetched. |
| storage.ko-fi.com/cdn/scripts/overlay-widget.js and /cdn/widget/Widget_2.js | Fetched through the fetch tool, which returns a model-written summary of the code, not the raw file. Treat the line-level claims as UNVERIFIED until someone reads the raw files in a browser. |

Because the vendors' own help articles for Ko-fi and StreamElements were blocked, the "official documentation" for those two is the vendor's shipped scripts, live response headers and the vendor-hosted pages themselves. Third-party how-to articles (Medium, forums) were used only to find what to probe, never as the claim.

## Per-platform findings

### Ko-fi

**Embed methods found**

1. Link out: `https://ko-fi.com/<username>`. Plain anchor.
2. Iframe of the creator page. The overlay script itself builds `https://ko-fi.com/<page>/?hidefeed=true&widget=true&embed=true` (read from the script, UNVERIFIED line text). A Medium guide shows the same query string with `preview=true` for previews (secondary, not relied on).
3. Floating overlay: `<script src="https://storage.ko-fi.com/cdn/scripts/overlay-widget.js">` then `kofiWidgetOverlay.draw(...)`. The help article was not readable; the call shape is from a GitHub issue quoted in search (secondary) and the script.
4. Button script: `https://storage.ko-fi.com/cdn/widget/Widget_2.js` with `kofiwidget2.init(text, color, id)` and `kofiwidget2.draw()`. The output is a plain anchor to `https://ko-fi.com/<id>` with `target="_blank"` (script summary, UNVERIFIED).

**What the browser loads** (header probe of `https://ko-fi.com/<creator>/?hidefeed=true&widget=true&embed=true` for two real public creator pages, static references in the HTML only)

| Type | Hosts |
|---|---|
| frame | `ko-fi.com` (the page itself) |
| script | `storage.ko-fi.com`, `js.stripe.com`, `www.googletagmanager.com`, `challenges.cloudflare.com` (Cloudflare Turnstile). The page's code also references PayPal (`www.paypal.com`, `www.paypalobjects.com`) and Apple Pay and Google Pay flows. |
| style | `storage.ko-fi.com`, `fonts.googleapis.com` |
| font | `fonts.gstatic.com` |
| img | `storage.ko-fi.com`, `media.ko-fi.com`, `more.ko-fi.com` |
| connect | UNVERIFIED (needs a browser network capture) |

The overlay script adds `storage.ko-fi.com` (CSS, `logomarkLogo.png`) and `fonts.googleapis.com` to the host page, preconnects to `ko-fi.com` and `storage.ko-fi.com`, and uses no cookies, `localStorage` or `sessionStorage` (script summary, UNVERIFIED). `Widget_2.js` loads a Quicksand font from `fonts.googleapis.com` and images from `storage.ko-fi.com`.

**Cookies and trackers.** The framed page response set no cookie in the probe, but the same site set `kofiweb.session` (HttpOnly, SameSite=Lax) on a redirect, and the page references Google Tag Manager, Stripe and PayPal, which set their own cookies and storage inside the frame once they run. Which of those load for an anonymous visitor in an embed: UNVERIFIED.

**`default-src 'self'`.** The plain iframe works with one addition: `frame-src https://ko-fi.com`. The framed page enforces its own CSP and cookie rules, not ours; our `script-src`, `img-src` and so on do not apply inside a cross-origin frame. The two script widgets do not work under the strict policy:
- Overlay needs `script-src https://storage.ko-fi.com` plus `style-src https://storage.ko-fi.com https://fonts.googleapis.com`, `font-src https://fonts.gstatic.com`, and it sets inline `style` attributes and writes `<style>`/HTML into about:blank iframes via `document.write`. Those frames inherit our `style-src 'self'`, so the inline styles are blocked unless `'unsafe-inline'` is allowed, which `_check_source` refuses. Expected to render broken; UNVERIFIED until tested in a browser.
- `Widget_2.js` uses `document.writeln` and an inline `<style>` and `style=""` (same problem) and also fails if called after load.
Both would add a third-party script that runs with the page's full privileges. Not recommended.

**`sandbox` and `allow`.** An iframe needs at least `allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox` for the Ko-fi page to work and to open PayPal or Stripe windows; `allow-top-navigation-by-user-activation` may be needed if Ko-fi navigates the top window (UNVERIFIED). `allow-same-origin` is safe here only because the frame is cross-origin to our site, so it grants Ko-fi its own origin, not ours. Never add `allow-same-origin` to a frame whose src could be ours. `allow="payment"` cannot help: the site's `Permissions-Policy: payment=()` denies the Payment Request API (Apple Pay, Google Pay) in all frames. Card and PayPal-window payment may still work; UNVERIFIED. Delegating would need a per-path `Permissions-Policy: payment=(self "https://ko-fi.com")`, which `render_headers` cannot emit today (it only handles CSP).

**Framing behavior of the target.** Plain creator page and home page: `X-Frame-Options: SAMEORIGIN` and `Content-Security-Policy: frame-ancestors 'self'`. With `widget=true&embed=true` the response carried neither header for two public creators (2026-10-24). A username that does not exist redirects to `/`, which is not framable. Ko-fi can change this at any time; the test strategy below includes a live header check.

**Accessibility and mobile.** An iframe needs a `title`, a sensible fixed height and a visible fallback link. The overlay is a fixed-position chat bubble with two button iframes (desktop and mobile) and a 328 px popup (script summary, UNVERIFIED); it overlays page content and its focus handling was not checked. A framed payment form scrolls inside the frame on small screens.

**What Ko-fi sees.** The visitor's IP address, user agent, language and the referring origin (our `Referrer-Policy` sends the origin only). With the framed page it can also see that this visitor reached the creator's page from this site. If the visitor has a Ko-fi session cookie and the browser allows third-party cookies, they may be recognized (browser behavior varies; UNVERIFIED). Google (Tag Manager, Fonts), Stripe, PayPal and Cloudflare get the same class of data if their resources load.

**Load cost.** Large: a full payment page with several payment SDKs in references, versus one anchor. UNVERIFIED in bytes; measure before deciding.

**Failure mode when blocked.** A framed page blocked by CSP shows a browser error placeholder; blocked scripts inside the frame break only the frame. With a facade and a visible link, the visitor still gets a working link.

**API.** No public Ko-fi API for supporter counts or goal progress was found. Webhooks exist for payments, with a `verification_token` in the payload (secondary sources only), and shop sales do not count toward goals (Ko-fi help snippet seen in search). Numbers would come from the creator's own webhook receiver, which the static site and the daily collector do not have today.

### StreamElements

**Embed methods found.** None for a website. The documented items are the tip page link (`https://streamelements.com/<channel>/tip`, from the `!tip` docs page, which says it must be set up under Revenue, Tipping settings) and overlays, which are meant as OBS browser sources, not website embeds. A tips goal widget exists in the widget gallery for stream overlays (search result only).

**Framing.** Header probe: `https://streamelements.com/streamelements/tip` returns `X-Frame-Options: SAMEORIGIN`. It cannot be framed from another origin. Nothing was found that works around this officially, and workarounds are not an option here.

**CSP, cookies, trackers, sandbox, accessibility, mobile, load cost.** Not applicable to an embed that does not exist. The tip page itself is a client-rendered app; its static HTML referenced `cdn.streamelements.com` only. What it loads at run time: UNVERIFIED.

**What it sees when the visitor follows a link.** Whatever the tip page collects at checkout; nothing about visitors before the click, apart from the referrer origin if the link does not use `rel="noreferrer"`.

**API.** There is an API for the creator's own tips (see `streamelements.md`, itself rated UNRELIABLE; endpoints, scopes and terms for showing tip totals are UNVERIFIED). No public endpoint exposes a creator's tip totals. Tip data includes donor names and messages, so only aggregates should ever be stored or shown.

### Patreon

**Embed methods found.** None in the current developer docs. Reading the whole of docs.patreon.com found no button, widget or iframe text. The docs do define a "Log in with Patreon" OAuth button flow (`GET www.patreon.com/oauth2/authorize`), which is a login flow, not a pledge button, and the campaign field `pledge_url` (sample `"/bePatron?c=1234560"`, relative to patreon.com), whose use is not described. Third-party posts describe a historic script button: `<a data-patreon-widget-type="become-patron-button" href="https://www.patreon.com/...">` followed by a `<script>` that loads a bundle; one forum thread says a `becomePatronButton.bundle.js` snippet no longer worked for a user, and a Patreon staff reply on that forum says the widget has no localization. Whether the current dashboard still offers it, and the script's host: UNVERIFIED (the dashboard needs a sign-in).

**Framing.** Header probe: `https://www.patreon.com/cw/patreon` returns `X-Frame-Options: sameorigin` (and the plain `/patreon` redirects there). A creator page cannot be framed. `https://www.patreon.com/oauth2/become-patron` returned 401 without parameters (not a framable page).

**What the creator page loads** (from the `Content-Security-Policy-Report-Only` header of that page, which lists what Patreon's own app is allowed to use, not necessarily what it loads): scripts from `c13.patreon.com`, Stripe, Braintree, Apple, Google, Sprig, Transcend, Facebook, PayPal, YouTube, TokenEx, Siftscience and gstatic; connect to Sentry, Mux, Facebook, Braintree and more. It sets `patreon_device_id`, `patreon_location_country_code`, `patreon_locale_code` and a Cloudflare `__cf_bm` cookie on a first request (names only). This is the page a visitor would land on after a link, not something our page would load.

**CSP, sandbox, accessibility, load cost.** A script button would need `script-src` for Patreon's host plus inline style handling (UNVERIFIED), and it can be replaced by a styled anchor that we control. The brand page restricts use of Patreon marks to digital use and points to brand@patreon.com for anything else, so the official logo should be used per those rules, not copied from a how-to.

**What Patreon sees.** On a link click: the visitor reaches a heavy, tracker-bearing app. We cannot reduce that.

**API.** Official and the best of the group: `patron_count` on the campaign (scope `campaigns`) and members for a derived MRR, with a Creator Access Token. Details, limits and risks are in `patreon.md`. The docs say "You may only fetch your own list of pledges or public pledges", so a self-hosted, own-campaign collector fits. Showing aggregates publicly: terms UNVERIFIED (open question in `patreon.md`).

### Fourthwall

**Embed methods found.** One, in the help article "Embedding your store on an external website" (read 2026-10-24; available on all plans per that page): the creator builds an iframe by hand from a product page URL.

```html
<iframe src="https://yourshop.fourthwall.com/products/your-product"
        width="100%" height="800" style="border: none;"
        title="Product from my Fourthwall shop"></iframe>
```

Stated limits on that page: "You cannot embed your entire shop"; "Cart and checkout redirect to Fourthwall"; "Navigation stays inside the iframe"; "Mobile responsiveness varies". The sample uses an inline `style` attribute, which `style-src 'self'` blocks; use a CSS class instead.

**Storefront API** (docs.fourthwall.com, read 2026-10-24): host `https://storefront-api.fourthwall.com/v1/`, token created under developer settings and sent as a `storefront_token` query parameter, "available on all Fourthwall plans at no additional cost". The docs say you can "make fetch requests directly to the Fourthwall API" from a static site. Whether the token is meant to be public, whether CORS permits browsers, rate limits and terms: not stated, UNVERIFIED. Header probe: a request without a token returns 401 and `Content-Security-Policy: frame-ancestors 'self' https://*.fourthwall.com` with `Vary: origin`, which hints at CORS handling but proves nothing.

**What the browser loads, cookies, framing of the shop pages.** Not probed: no real shop URL was confirmed to be public and permitted for a probe, so the shop page's `X-Frame-Options`/`frame-ancestors`, hosts and cookies are UNVERIFIED. The embed article implies framing works for product pages.

**`default-src 'self'`.** The iframe needs `frame-src` for the exact shop host (either `<shop>.fourthwall.com` or the creator's custom domain), never `https://*.fourthwall.com`: `_ORIGIN` refuses wildcards, which is correct, since any shop would otherwise load.

**sandbox and allow.** Probably the same set as Ko-fi (`allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox`), UNVERIFIED.

**What Fourthwall sees, accessibility, load cost, failure mode.** Same class as Ko-fi (IP, user agent, referrer origin, shop session); a product page is a full shop page. Blocked: empty frame; keep a link.

**Build-time alternative.** The collector or build step can call the Storefront API itself and bake product names, prices and images into static cards with a link to the product. The visitor's browser then loads nothing from Fourthwall except images (and `img-src` already allows `https:`), and the token never reaches a page. Image hotlinking still tells Fourthwall that the visitor viewed the page; copying images into the site at build time would avoid that (terms for copying: UNVERIFIED).

### YouTube channel memberships

**Embed methods found.** None for a join button. YouTube Help documents a link: the channel URL plus `/join`, for example `https://www.youtube.com/channel/<id>/join`, and an opt-in link `.../allow_gifts` (search snippets of YouTube Help, 2026-10-24; the pages themselves were not fetched). The Join button appears on eligible watch pages only, not on made-for-kids content. The embedded player parameter page (read 2026-10-24) mentions no membership or Join button and no `youtube-nocookie.com`.

**Framing and privacy.** Header probe: `https://www.youtube.com/@YouTube/join` returns `X-Frame-Options: SAMEORIGIN` and sets cookies such as `YSC`, `VISITOR_INFO1_LIVE`, `GPS` and `__Secure-YNID` on a first, cookie-less request (names only). It is not framable. Linking to it sends the visitor to a page that sets those cookies, which is the visitor's choice at that point.

**API.** `members.list` (read 2026-10-24) needs the scope `youtube.channel-memberships.creator`, can only be called by the creator for their own channel, and requires reaching out to a Google or YouTube representative for access. Quota cost 2 units. `pageInfo.totalResults` is "the total number of results in the result set"; whether that is the member count: UNVERIFIED. Access is gated, so treat this as unavailable until the maintainer's channel has been granted it.

### Twitch subscriptions

**Embed methods found.** Twitch's embed page (read 2026-10-24) lists three embeds: "Everything" (player and chat, which "includes follow, subscribe, and login functionality"), "Chat" and "Video and Clips". There is no standalone subscribe button. Every embed needs a `parent` parameter naming the embedding domain, and embeds must be served over TLS and not obscured by other elements. The page says nothing about cookies or framing headers. Header probe: `https://www.twitch.tv/twitch` returns `X-Frame-Options: SAMEORIGIN` and sets a `twitch.lohp.countryCode` cookie. The player and chat embed come from a different host (`player.twitch.tv`, `embed.twitch.tv`, UNVERIFIED). A live player plus chat is a heavy third-party load and is out of scope for a support card.

**CSP.** Would need `frame-src` for the player host, and Twitch's own code runs inside the frame; the specific hosts are UNVERIFIED.

**API.** Get Broadcaster Subscriptions (`channel:read:subscriptions`, broadcaster's token): per `twitch.md`, endpoint details are UNVERIFIED (the API reference page is too large for the fetch tool; a second attempt on 2026-10-24 also could not reach that section).

## Comparison with the native options

| Option | Third-party hosts loaded by the visitor | CSP change | Visitor data sent | Maintenance |
|---|---|---|---|---|
| Link-out card (today) | none until the visitor clicks | none | the click itself; referrer origin only | none |
| Link-out card with API numbers (patrons, subscribers) | none | none | none | collector job, token handling, a daily refresh; numbers are public aggregates only |
| Ko-fi iframe behind a facade | after click: `ko-fi.com`, plus whatever it loads (Stripe, PayPal, GTM, Cloudflare, Google Fonts) | `frame-src https://ko-fi.com` on one path | full page-view class data to Ko-fi and its processors, only after the click | Ko-fi can change framing headers; needs a live check |
| Ko-fi script widgets | `storage.ko-fi.com`, Google Fonts, and the Ko-fi frame | `script-src` and `style-src` additions; needs `'unsafe-inline'` styles (refused by the builder) | loaded on page view | not recommended |
| Fourthwall product iframe behind a facade | after click: the shop host | `frame-src https://<shop host>` | same class as Ko-fi | one embed per product; custom domains change the allowlist |
| Fourthwall build-time cards | images only (or none if copied) | none | image fetch only | build needs the token as a secret |

Cards fed by numbers need a stable source. Patreon (official API) and Twitch (official API) have one. Ko-fi, StreamElements public totals and Fourthwall counts do not (or are UNVERIFIED), and YouTube is gated.

## Recommendation per platform

| Platform | Recommendation | Security and privacy cost |
|---|---|---|
| StreamElements | Link-out only. No embed exists; the tip page cannot be framed. | None added. Optional later: tip total from the API, aggregate only, UNVERIFIED terms. |
| Patreon | Link-out card. Add a patron count from the API in the collector once the maintainer confirms the display terms. No iframe (blocked) and no script button (not documented now). | None added for the link. API: one Creator Access Token on the maintainer's laptop, no visitor data. |
| Ko-fi | Link-out is the default. If the maintainer wants an in-page tip form, use the iframe behind a facade, only on the Support page. Do not use the overlay or button scripts. | CSP: `frame-src https://ko-fi.com` on one path. After the click, Ko-fi, Stripe, PayPal, Google (Tag Manager, Fonts) and Cloudflare each see the visitor and may set cookies. Payment Request (Apple Pay, Google Pay) is blocked by `Permissions-Policy payment=()` unless a per-path header is added. The privacy page must say so. |
| Fourthwall | Link-out, or build-time product cards from the Storefront API. A product iframe only if one product must be shown live, behind the same facade. | Iframe: `frame-src` for one shop host. Build-time cards: a secret token in the build environment and an image-hotlink signal. |
| YouTube / Twitch memberships | Link-out only (`/join` for YouTube, the channel page for Twitch). No embed fits the purpose. | None added. YouTube and Twitch set cookies only after the visitor leaves the site. |

## Design: a per-page opt-in `embed` block

Goal: nothing third-party loads unless the page author asked for it, the CSP is widened only on that page, and nothing loads until the visitor clicks.

**Config.** A new block type `embed` (next to `link`, `text`, `crypto`):

```yaml
- type: embed
  kind: kofi            # an explicit enum: kofi, fourthwall_product
  id: "creator-name"    # validated per kind (Ko-fi username pattern)
  title: "Tip on Ko-fi" # required: the iframe title and the facade label
  height: 600           # clamped to a range
```

- The embed type is an enum. Each kind is defined in code with its **allowlist of hostnames** (Ko-fi: `ko-fi.com`; Fourthwall product: the one shop host, set once in the site config as `fourthwall.shop_host`, an exact https hostname, never a wildcard). Config never contains a free-form URL; the iframe `src` is built from the kind's template and the validated id.
- Validation reuses the existing rule that a CSP source is `'self'` or an exact https origin (`_check_source`).

**CSP generation.** The build collects the embed blocks per rendered page and emits `path_extras` for that page's path only: `{"frame-src": ["https://ko-fi.com"]}`. `render_headers` already supports `path_extras` and `frame-src` is already in `_EXTENDABLE`; build.py does not call it with extras yet, so this is new wiring, not a policy change. Two details to handle:
- `frame-src` is not in `_BASE_CSP`, so it falls back to `default-src 'self'` everywhere else; the extra produces `frame-src https://ko-fi.com` with no `'self'`, which is correct (no same-origin frames are used).
- `script-src` stays `'self'`; the facade script is a static first-party file. No hash or nonce is needed, and `_check_source` refuses them anyway.
- A page that uses no embed gets no extra and keeps the site-wide policy; a test asserts this.

**Facade.** The block renders a normal card: title, one sentence saying what will load, the platform link as an `<a>` (so the page works with no script), and a button "Load the Ko-fi form". A small first-party script (`embed.js`, `defer`, `'self'`) swaps the button for the iframe on click, with `src` set only then, plus `loading="lazy"`, a `title`, `referrerpolicy="strict-origin"` (origin only, no path), and `sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"` (the exact tokens are UNVERIFIED and must be tested against a live frame). The script reads no storage, sets no cookie and does nothing on Global Privacy Control that it does not do for everyone: the iframe is only created after a click, which is consent in the plain sense. If the script is missing, the link works. Because a no-script iframe would load on page view, there is no `<noscript>` iframe.
- Existing tests that say the only scripts are the enabled features and that no third-party script appears outside the gate page must be extended: `embed.js` counts as a feature script, and a page with an embed block is the only page allowed a `frame-src` extra.

**Privacy page.** When any embed block is present the generated privacy text gets one item per embed kind: What: "When you press the load button, your browser opens a page from Ko-fi inside this page. Ko-fi, and the payment and analytics services it uses, see your address and browser details and may set cookies, under their own privacy policies." Why: "To let you tip without leaving the page." Kept: "Nothing is stored by this site. Ko-fi keeps what it keeps." Before the click, the text says that nothing is loaded. The existing cookie notice logic (`sets_cookies`) stays true only for the kit gate, so the build must decide whether a page that can load a cookie-setting frame needs the banner line; the maintainer decides (open question 3). The line "This site sets no cookies for counting" stays accurate because it refers to this site's counting.

**Header for payments.** Decide whether to accept degraded Apple/Google Pay (default) or extend `render_headers` with a per-path `Permissions-Policy` override. The first is simpler and fits least privilege.

**Test strategy.**
- Unit: `build_csp` and `render_headers` for an embed page contain `frame-src https://ko-fi.com` for that path only; refuse a wildcard, `http:`, a path, a query, a user-info or a trailing newline host; pages without embeds have no `frame-src`; the site-wide `/*` policy is byte-identical to today.
- Config: unknown `kind`, a bad Ko-fi id (slashes, `..`, uppercase tricks, 100 characters), a Fourthwall host that is not an exact hostname, and a missing `title` are rejected.
- Build: the facade page has the anchor, the button, no `<iframe>` element and no third-party host in any attribute before the click (strict scan of the built HTML); `embed.js` is the only added script; no inline script or style; the iframe is created with the expected `sandbox`, `title`, `referrerpolicy` and `loading` in a `vm` run with stubs.
- Script: a `vm` test that the iframe is created exactly once, only after the click, with `src` equal to the template output, and that the script references no `cookie`, `localStorage`, `sessionStorage`, `fetch`, `XMLHttpRequest` or `sendBeacon`.
- Privacy: the generated text names every kind in use, is absent without embeds, and a new kind cannot ship without a privacy sentence (same pattern as the analytics dimensions test).
- Live, opt-in (not CI): a script that fetches the embed URL for a public test account and asserts no `X-Frame-Options` and no `frame-ancestors` that excludes us, so a vendor header change is noticed; and a Playwright run against the built site to confirm the frame loads under the real CSP and to record the actual hosts it contacts (this would settle most UNVERIFIED items here).

## Open questions for the maintainer

1. Do you want any in-page embed at all, or is the link-out card plus API numbers enough? The research suggests only Ko-fi can be embedded, and it costs a lot of third parties for a tip form.
2. If Ko-fi: accept that Apple Pay and Google Pay may not work inside the frame because of `Permissions-Policy payment=()`, or allow a per-path Permissions-Policy override (a new capability in `render_headers`)?
3. Does a click-loaded cookie-setting frame need a notice line or banner, given the site says it sets no cookies? Who reviews the privacy wording?
4. Can you read the Ko-fi widget help article and the StreamElements tipping articles in a browser and confirm there is no documented iframe or button (the fetch tool and curl hit a Cloudflare challenge)?
5. Do you have a Fourthwall shop, and is it on a custom domain? That fixes the allowlist host. Is the Storefront token meant to be public, and may product images be copied at build time?
6. Will you read Patreon's developer policy and StreamElements' terms on showing aggregate supporter or tip numbers publicly?
7. Is a YouTube members count worth a request to Google for `youtube.channel-memberships.creator` access?
8. Should embeds be allowed on tabs other than Support, or only one page, so the CSP exception stays on one path?

## Sources (fetched 2026-10-24)

- https://storage.ko-fi.com/cdn/scripts/overlay-widget.js and https://storage.ko-fi.com/cdn/widget/Widget_2.js (script behavior via fetch summary)
- Header probes and one-page static scans: ko-fi.com (home, two public creator pages, plain and `widget=true&embed=true`), streamelements.com (home and `/streamelements/tip`), www.patreon.com (`/patreon`, `/cw/patreon`, `/oauth2/become-patron`), storefront-api.fourthwall.com, www.youtube.com (`/@YouTube/join`), www.twitch.tv (`/twitch`), fourthwall.com
- https://docs.patreon.com/ and https://www.patreon.com/brand
- https://docs.streamelements.com/chatbot/commands/default/tip
- https://help.fourthwall.com/manage-my-shop/shop-settings/embedding-your-store-on-an-external-website.md
- https://docs.fourthwall.com/storefront/overview, /storefront/architecture, /storefront/getting-started
- https://dev.twitch.tv/docs/embed/ and https://dev.twitch.tv/docs/api/reference/ (subscriptions section not reached)
- https://developers.google.com/youtube/v3/docs/members/list and https://developers.google.com/youtube/player_parameters
- YouTube Help "Promote your YouTube channel's memberships program" (https://support.google.com/youtube/answer/14915302), seen in a search result only
- Secondary, used for leads only: Medium Ko-fi guide, Patreon Developers forum threads, ComicFury forum, Ko-fi webhook summaries from search
- Related notes: `patreon.md`, `streamelements.md`, `twitch.md`, `youtube.md`
