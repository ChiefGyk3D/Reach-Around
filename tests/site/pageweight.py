# SPDX-License-Identifier: Apache-2.0
"""Page-weight budget and the measurement the budget is judged by (#670).

``BUDGET`` is the single source: ``docs/performance.md`` carries the same
table and ``tests/site/test_page_weight.py`` fails when the two differ.

Method (also written in ``docs/performance.md``): a page costs its own HTML
plus every distinct same-origin file its HTML links as a stylesheet, script,
image, icon or preload (fonts included). Files only reached from inside a CSS
file (border art that loads when a frame is on) are not counted. "Compressed"
is gzip level 9 of the text files (HTML, CSS, JS, SVG, TTF) and the raw size
of everything else (WOFF2 is already compressed).
"""

from __future__ import annotations

import gzip
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

PAGE_TYPES = (
    "hub",
    "hub-stack",
    "hub-live",
    "spoke",
    "tab",
    "kit",
    "support",
    "support-page",
)
FIELDS = ("html", "css", "js", "images", "fonts", "total", "compressed", "requests")

# Bytes, except ``requests``. Measured on the example instance and a kit with
# no Turnstile key on 2026-11-20, then raised by about a quarter.
BUDGET: dict[str, dict[str, int]] = {
    "hub": {
        "html": 5200,
        "css": 59000,
        "js": 13000,
        "images": 1300,
        "fonts": 82000,
        "total": 160000,
        "compressed": 82000,
        "requests": 11,
    },
    # The smart-stack home page (#673) is not the example hub: it lists every
    # group's links on one page. Measured 2026-11-20 on a stack built from the
    # example instance's creator and theme; about a quarter of headroom.
    "hub-stack": {
        "html": 11300,
        "css": 65300,
        "js": 13000,
        "images": 1300,
        "fonts": 81500,
        "total": 172200,
        "compressed": 83700,
        "requests": 13,
    },
    # The same page with page.live_badges on: live.js (an optional enhancement,
    # deferred, only on pages that turn badges on) is added to click.js.
    "hub-live": {
        "html": 11700,
        "css": 65300,
        "js": 29000,
        "images": 1300,
        "fonts": 81500,
        "total": 191300,
        "compressed": 89300,
        "requests": 14,
    },
    # A spoke page (#676): a stack tab drawn as its own page. Measured on a
    # stack built from the example instance's creator and theme with one Gear
    # spoke of three sections and twelve links; about a quarter of headroom.
    "spoke": {
        "html": 7400,
        "css": 60000,
        "js": 13000,
        "images": 1300,
        "fonts": 81500,
        "total": 163000,
        "compressed": 81500,
        "requests": 11,
    },
    "tab": {
        "html": 3600,
        "css": 59000,
        "js": 13000,
        "images": 1300,
        "fonts": 82000,
        "total": 158000,
        "compressed": 81000,
        "requests": 11,
    },
    "kit": {
        "html": 11800,
        "css": 72000,
        "js": 3300,
        "images": 500,
        "fonts": 33300,
        "total": 123000,
        "compressed": 57000,
        "requests": 11,
    },
    "support": {
        "html": 4600,
        "css": 72000,
        "js": 13000,
        "images": 1300,
        "fonts": 82000,
        "total": 148000,
        "compressed": 78000,
        "requests": 10,
    },
    # The support page (#683): a crypto block with two coins and the copy
    # script. Measured 2026-11-23 on the example instance's creator and theme,
    # then raised by about a quarter. A QR image, if set, adds its own size.
    "support-page": {
        "html": 5100,
        "css": 65300,
        "js": 16700,
        "images": 1300,
        "fonts": 81500,
        "total": 169700,
        "compressed": 84800,
        "requests": 14,
    },
}

# The one third-party origin a build may reference, and only on the two gate
# pages and only when ``kit.turnstile_site_key`` is set.
GATE_ORIGINS = frozenset({"challenges.cloudflare.com"})

_TEXT = {".html", ".css", ".js", ".mjs", ".svg", ".ttf", ".otf", ".txt"}
_LINK_ATTRS = {
    "link": ("href",),
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "iframe": ("src",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "form": ("action",),
}
_FETCHED_RELS = {
    "stylesheet",
    "icon",
    "apple-touch-icon",
    "preload",
    "modulepreload",
    "prefetch",
    "preconnect",
    "dns-prefetch",
    "manifest",
}
_CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^'\")\s]+)|@import\s+['\"]([^'\"]+)")


# The fixture root that carries the support page (#683); only that page is measured.
SUPPORT_SITE = "supportpage"

# Stack fixture roots: only their home page is measured, as these types.
STACK_SITES = {"stack": "hub-stack", "stack-live": "hub-live"}
# The spoke fixture root: only its spoke page is measured, as the "spoke" type.
SPOKE_SITE = "spoke"
SPOKE_PAGE = "gear/index.html"


def page_type(rel: str, site: str = "") -> str:
    """Classify a built page by its path under the output root.

    ``site`` is the name of the output root; the two stack fixtures (see
    ``STACK_SITES``) classify their home page as their own type.
    """
    if site == SPOKE_SITE and rel == SPOKE_PAGE:
        return "spoke"
    if site in STACK_SITES and rel == "index.html":
        return STACK_SITES[site]
    if rel == "index.html":
        return "hub"
    if rel == "kit/index.html":
        return "kit"
    if rel == "support/index.html":
        return "support-page"
    if rel in ("privacy/index.html", "kit/retry/index.html", "404.html"):
        return "support"
    return "tab"


class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.local: list[str] = []
        self.remote: list[str] = []
        self.stylesheets_inline: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "style":
            self._in_style = True
        rel = dict(attrs).get("rel") or ""
        if tag == "link" and not set(rel.lower().split()) & _FETCHED_RELS:
            return  # canonical, alternate and the like are not fetched
        for name, value in attrs:
            if name == "style" and value:
                self.stylesheets_inline.append(value)
            if value and name in _LINK_ATTRS.get(tag, ()):
                for part in value.split(",") if name == "srcset" else [value]:
                    self._add(part.strip().split(" ")[0], tag == "form")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.stylesheets_inline.append(data)

    def _add(self, ref: str, navigation: bool = False) -> None:
        if not ref or ref.startswith(("data:", "#")):
            return
        if ref.startswith("//") or urlsplit(ref).scheme:
            self.remote.append(ref)
        elif not navigation:  # a local form action is a page, not a file
            self.local.append(ref.split("#")[0].split("?")[0])


def _parse(page: Path) -> _Refs:
    refs = _Refs()
    refs.feed(page.read_text(encoding="utf-8"))
    return refs


def _file(root: Path, page: Path, ref: str) -> Path:
    if ref.startswith("/"):
        return root / ref.lstrip("/")
    return (page.parent / ref).resolve()


def _css_refs(text: str) -> list[str]:
    return [m.group(1) or m.group(2) for m in _CSS_URL.finditer(text)]


def _is_remote(ref: str) -> bool:
    return ref.startswith("//") or urlsplit(ref).scheme in ("http", "https")


def _css_closure(root: Path, seeds: list[tuple[Path, str]]) -> list[Path]:
    """Same-origin stylesheets reached by ``@import`` or ``url()`` from CSS.

    ``seeds`` are (directory, CSS text) pairs; each stylesheet found is read
    in turn. A visited set ends import cycles.
    """
    found: dict[Path, None] = {}
    queue = list(seeds)
    while queue:
        where, text = queue.pop(0)
        for ref in _css_refs(text):
            if _is_remote(ref) or ref.startswith("data:"):
                continue
            clean = ref.split("#")[0].split("?")[0]
            if not clean.endswith(".css"):
                continue
            f = _file(root, where / "x", clean)
            if f not in found:
                found[f] = None
                queue.append((f.parent, f.read_text(encoding="utf-8")))
    return list(found)


def resources(root: Path, page: Path) -> list[Path]:
    """The distinct same-origin files the page loads, in order.

    Files its HTML links, then stylesheets those (and inline styles) import.
    """
    refs = _parse(page)
    seen: dict[Path, None] = {}
    for ref in refs.local:
        seen[_file(root, page, ref)] = None
    seeds = [(page.parent, t) for t in refs.stylesheets_inline]
    seeds += [
        (f.parent, f.read_text(encoding="utf-8")) for f in seen if f.suffix == ".css"
    ]
    for f in _css_closure(root, seeds):
        seen[f] = None
    return list(seen)


_KINDS = {
    **dict.fromkeys((".css",), "css"),
    **dict.fromkeys((".js", ".mjs"), "js"),
    **dict.fromkeys((".woff2", ".woff", ".ttf", ".otf"), "fonts"),
    **dict.fromkeys(
        (".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg", ".ico"), "images"
    ),
}


def measure(root: Path, page: Path) -> dict[str, int]:
    """Weight of one built page, with the keys of ``FIELDS``."""
    html = page.read_bytes()
    out = dict.fromkeys(FIELDS, 0)
    out["html"] = len(html)
    out["compressed"] = len(gzip.compress(html, 9))
    files = resources(root, page)
    for f in files:
        data = f.read_bytes()
        kind = _KINDS.get(f.suffix.lower())
        if kind is None:
            raise ValueError(f"unclassified file type {f.suffix!r}: {f.name}")
        out[kind] += len(data)
        out["compressed"] += (
            len(gzip.compress(data, 9)) if f.suffix in _TEXT else len(data)
        )
    out["total"] = out["html"] + out["css"] + out["js"] + out["images"] + out["fonts"]
    out["requests"] = 1 + len(files)
    return out


def third_party(root: Path, page: Path) -> list[str]:
    """Absolute or protocol-relative URLs the page or its CSS would fetch.

    Plain ``<a href>`` links are navigation, not requests, and are ignored.
    """
    refs = _parse(page)
    found = list(refs.remote)
    css_texts = list(refs.stylesheets_inline)
    for f in resources(root, page):
        if f.suffix == ".css":
            css_texts.append(f.read_text(encoding="utf-8"))
    for text in css_texts:
        found += [r for r in _css_refs(text) if _is_remote(r)]
    return found


def over_budget(kind: str, weight: dict[str, int]) -> list[str]:
    """One message per field of ``weight`` above the budget for ``kind``."""
    return [
        f"{f}: {weight[f]} > {BUDGET[kind][f]}"
        for f in FIELDS
        if weight[f] > BUDGET[kind][f]
    ]


DOCS_HEADING = "## Page-weight budget (#670)"
_ROW = re.compile(r"^\|\s*([\w-]+)\s*\|((?:\s*\d+\s*\|){8})\s*0\s*\|\s*$")


def docs_table(text: str) -> dict[str, dict[str, int]]:
    """Parse the budget table under ``DOCS_HEADING`` in docs/performance.md."""
    start = text.index(DOCS_HEADING)
    rest = text[start + len(DOCS_HEADING) :]
    nxt = re.search(r"^## ", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest
    table: dict[str, dict[str, int]] = {}
    for line in section.splitlines():
        m = _ROW.match(line)
        if m:
            cells = [int(c) for c in m.group(2).strip().strip("|").split("|")]
            table[m.group(1)] = dict(zip(FIELDS, cells, strict=True))
    return table


def docs_differences(text: str) -> list[str]:
    """Differences between the docs table and ``BUDGET``; empty when equal."""
    table = docs_table(text)
    diffs = [f"missing in docs: {k}" for k in BUDGET if k not in table]
    diffs += [f"not in BUDGET: {k}" for k in table if k not in BUDGET]
    for kind in BUDGET:
        for f in FIELDS:
            if kind in table and table[kind].get(f) != BUDGET[kind][f]:
                diffs.append(
                    f"{kind}.{f}: docs {table[kind].get(f)} != {BUDGET[kind][f]}"
                )
    return diffs
