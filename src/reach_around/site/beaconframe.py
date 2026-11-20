# SPDX-License-Identifier: Apache-2.0
"""The hidden same-origin helper page for the invisible bot check (#610).

Off unless ``analytics.turnstile_beacon`` is on. Cloudflare Turnstile cannot be
self-hosted, so its script is loaded from exactly one page that no visitor opens:
``/e-frame/``. Ordinary pages keep ``script-src 'self'`` and never name the
Turnstile origin; they embed the helper in a hidden frame (``static/beacon.js``),
which works under the strict policy because ``frame-src`` falls back to
``default-src 'self'``. Only this one path gets the Turnstile origin in
``script-src`` and ``frame-src`` and ``frame-ancestors 'self'`` (so the site can
embed it and nobody else can), through the same per-path ``_headers`` mechanism
the kit gate uses. The path is not linked and carries ``noindex`` as a meta tag and
as a header.

The page loads two scripts, both ``defer`` so they run in order: Turnstile's, then
``static/beacon-frame.js``, which renders the widget and passes the result to the
parent with ``postMessage``. No inline script, so the page needs no
``unsafe-inline`` or nonce.

What this does and does not protect: Cloudflare's script is mutable third-party
code and it RUNS INSIDE THIS HELPER FRAME. So the frame is sandboxed with
``allow-scripts`` and without ``allow-same-origin``: its origin is opaque, and
code in it cannot read or change the parent page, same-origin storage or cookies.
The page policy also gives it only the vendor origin for script, frame and image
(``img-src``), and ``connect-src`` stays ``'self'``. It can still post any
message to its parent, including a forged "verified" one with the nonce the parent
put in the frame address, since the vendor script can read its own address. The
parent therefore treats the browser-side result as a hint, and the server must
verify the token (siteverify, hostname and action). If Turnstile does not work
in an opaque origin, the fallback is a separate origin for the helper; see
``docs/deploy.md``.
"""

from __future__ import annotations

from pathlib import Path

from reach_around.config.models import SiteConfig
from reach_around.site.kit import TURNSTILE_ORIGIN

FRAME_PATH = "/e-frame/"
FRAME_PAGE = Path("e-frame") / "index.html"
# The Turnstile script, explicit rendering (the helper script renders the widget).
TURNSTILE_SCRIPT = f"{TURNSTILE_ORIGIN}/turnstile/v0/api.js?render=explicit"
FRAME_SCRIPT = Path("static") / "beacon-frame.js"
LOADER_SCRIPT = Path("static") / "beacon.js"


def enabled(config: SiteConfig) -> bool:
    return config.analytics.turnstile_beacon


def frame_paths(config: SiteConfig) -> set[Path]:
    """The HTML file the helper adds to the public output (none when off)."""
    return {FRAME_PAGE} if enabled(config) else set()


def frame_path_extras(config: SiteConfig) -> dict[str, dict[str, list[str]]]:
    """The CSP extra for the helper path, and for no other path."""
    if not enabled(config):
        return {}
    return {
        FRAME_PATH: {
            "script-src": [TURNSTILE_ORIGIN],
            "frame-src": [TURNSTILE_ORIGIN],
            "frame-ancestors": ["'self'"],
            # The site-wide rule allows any https image; this page runs vendor
            # script and needs none, so it may load images from the vendor only.
            "replace:img-src": [TURNSTILE_ORIGIN],
        }
    }


def frame_noindex_paths(config: SiteConfig) -> tuple[str, ...]:
    return (FRAME_PATH,) if enabled(config) else ()
