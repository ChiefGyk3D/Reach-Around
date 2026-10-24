# SPDX-License-Identifier: Apache-2.0
"""The page-view beacon: build wiring, privacy wording, and a source scan."""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.site.build import build_site
from reach_around.site.overrides import build_csp
from reach_around.site.privacy import collected_items

TODAY = date(2027, 3, 10)
GOLDEN_ON = Path(__file__).parent / "golden_privacy" / "views_on" / "index.html"
GOLDEN_OFF = Path(__file__).parent / "golden_privacy" / "views_off" / "index.html"
SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)


def make_config(**extra: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/1"}
                ],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [
                    {"id": "two", "title": "Two", "url": "https://example.com/2"}
                ],
            },
        ],
    }
    data.update(extra)
    return SiteConfig.model_validate(data)


def build(tmp: Path, **extra: Any) -> Path:
    build_site(make_config(**extra), tmp, today=TODAY)
    return tmp


def script_text() -> str:
    return (resources.files("reach_around.site") / "static" / "view.js").read_text(
        encoding="utf-8"
    )


# --- served only when enabled, linked, no inline script -----------------------


def test_off_by_default_no_file_no_link_no_attribute(tmp_path: Path) -> None:
    build(tmp_path)
    assert not (tmp_path / "static" / "view.js").exists()
    pages = sorted(tmp_path.rglob("*.html"))
    assert pages
    for page in pages:
        html = page.read_text()
        assert "view.js" not in html
        assert "data-page-id" not in html and "data-view-route" not in html


def test_on_serves_the_file_and_links_it_from_tab_pages(tmp_path: Path) -> None:
    build(tmp_path, analytics={"page_views": True})
    assert (tmp_path / "static" / "view.js").read_text() == script_text()
    home = (tmp_path / "index.html").read_text()
    more = (tmp_path / "more" / "index.html").read_text()
    assert '<script src="static/view.js" defer></script>' in home
    assert '<script src="../static/view.js" defer></script>' in more


def test_page_id_and_route_are_data_attributes_on_tab_pages_only(
    tmp_path: Path,
) -> None:
    build(tmp_path, analytics={"page_views": True, "route": "/stats/pv/"})
    home = (tmp_path / "index.html").read_text()
    more = (tmp_path / "more" / "index.html").read_text()
    assert '<body data-page-id="start" data-view-route="/stats/pv/">' in home
    assert 'data-page-id="more"' in more
    privacy = (tmp_path / "privacy" / "index.html").read_text()
    assert "data-page-id" not in privacy
    assert "view.js" not in privacy


def test_no_inline_script_and_only_external_files(tmp_path: Path) -> None:
    build(tmp_path, analytics={"page_views": True})
    seen = 0
    for page in sorted(tmp_path.rglob("*.html")):
        html = page.read_text()
        for tag in SCRIPT_TAG.findall(html):
            seen += 1
            assert " src=" in tag and "defer" in tag
        assert "<script" not in re.sub(r"<script[^>]*></script>", "", html).lower()
        assert not re.search(r"\son[a-z]+=", html, re.IGNORECASE)
    assert seen >= 2


def test_strict_csp_is_unchanged_by_analytics(tmp_path: Path) -> None:
    off = build(tmp_path / "off")
    on = build(tmp_path / "on", analytics={"page_views": True})
    assert (off / "_headers").read_bytes() == (on / "_headers").read_bytes()
    headers = (on / "_headers").read_text()
    assert "script-src 'self';" in headers
    assert "unsafe" not in headers
    assert build_csp() in headers


def test_a_route_that_is_not_a_path_is_refused() -> None:
    with pytest.raises(ValidationError):
        make_config(analytics={"page_views": True, "route": "https://x.test/"})


# --- privacy wording ----------------------------------------------------------

CATEGORIES = (
    "browser",
    "operating system",
    "device",
    "language",
    "window width",
    "country",
    "region",
    "city",
    "referring site",
    "automated",
    "daily",
    "IP address is never stored",
)


def test_privacy_lists_every_derived_category_when_on() -> None:
    items = collected_items(make_config(analytics={"page_views": True}))
    views = [i for i in items if "page views" in i.what]
    assert len(views) == 1
    text = f"{views[0].what} {views[0].why} {views[0].kept}"
    for needle in CATEGORIES:
        assert needle in text, needle
    assert not any(i.sensitive for i in items)


def test_privacy_says_nothing_about_page_views_when_off() -> None:
    off = collected_items(make_config())
    text = " ".join(f"{i.what} {i.why} {i.kept}" for i in off).lower()
    for needle in ("page view", "browser", "referring", "window width", "city"):
        assert needle not in text


def test_privacy_when_only_page_views_are_on(tmp_path: Path) -> None:
    build(tmp_path, analytics={"page_views": True}, features={"click_tracking": False})
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "collects nothing" not in page
    assert "page view" in page.lower()


def test_golden_privacy_pages(tmp_path: Path, update_golden: bool) -> None:
    for golden, extra in (
        (GOLDEN_ON, {"analytics": {"page_views": True}}),
        (GOLDEN_OFF, {}),
    ):
        out = build(tmp_path / golden.parent.name, **extra)
        built = (out / "privacy" / "index.html").read_bytes()
        if update_golden:
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_bytes(built)
        assert golden.is_file(), "no golden file; run pytest --update-golden"
        assert built == golden.read_bytes()


def test_off_output_is_identical_to_the_default(tmp_path: Path) -> None:
    default = build(tmp_path / "a")
    explicit = build(tmp_path / "b", analytics={"page_views": False})
    files = [p for p in sorted(default.rglob("*")) if p.is_file()]
    assert files
    for path in files:
        other = explicit / path.relative_to(default)
        assert path.read_bytes() == other.read_bytes()


# --- source scan --------------------------------------------------------------

BANNED = (
    "fetch",
    "XMLHttpRequest",
    "cookie",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "caches",
    "canvas",
    "getContext",
    "WebGL",
    "AudioContext",
    "fonts",
    "getBattery",
    "plugins",
    "screen",
    "devicePixelRatio",
    "userAgent",
    "userAgentData",
    "hardwareConcurrency",
    "deviceMemory",
    "eval",
    "new Function",
    "document.write",
    "innerHTML",
    "WebSocket",
    "EventSource",
    "importScripts",
    "Image(",
)


def violations(source: str) -> list[str]:
    return [w for w in BANNED if re.search(re.escape(w), source, re.IGNORECASE)]


def test_scan_bites() -> None:
    bad = "localStorage.x = screen.width; fetch('/x'); document.cookie; eval(1)"
    assert {"localStorage", "screen", "fetch", "cookie", "eval"} <= set(violations(bad))
    assert violations("navigator.sendBeacon(u, b)") == []


def test_view_js_uses_no_fetch_xhr_storage_cookie_or_eval() -> None:
    src = script_text()
    assert "sendBeacon" in src and len(src) > 200  # loose: the file was read
    assert violations(src) == []
    assert len(re.findall(r"innerWidth", src)) == 1
    assert len(re.findall(r"sendBeacon\(", src)) == 1
