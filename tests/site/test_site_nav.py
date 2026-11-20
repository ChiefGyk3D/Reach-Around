# SPDX-License-Identifier: Apache-2.0
"""The property bar ``site_nav`` (#671): header bar and footer family line."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "examples" / "instance"
GOLDEN = Path(__file__).parent / "golden_site_nav"

ITEMS = [
    {"title": "Links", "url": "https://links.example.com/", "current": True},
    {"title": "Media kit", "url": "https://links.example.com/kit/"},
    {"title": "Main site", "url": "https://www.example.com/"},
]


def config(site_nav: dict[str, Any] | None, kit: bool = False) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [{"id": "b", "title": "B", "url": "https://e.com/b"}],
            },
        ],
    }
    if site_nav is not None:
        data["site_nav"] = site_nav
    if kit:
        data["kit"] = {
            "title": "Kit",
            "about": "About the creator.",
            "tags": ["one"],
        }
    return SiteConfig.model_validate(data)


def build(cfg: SiteConfig, out: Path) -> dict[str, str]:
    build_site(cfg, out, today=TODAY, assets_dir=ASSETS)
    names = ["index.html", "more/index.html", "privacy/index.html", "404.html"]
    return {n: (out / n).read_text(encoding="utf-8") for n in names}


def bars(html: str) -> list[str]:
    return re.findall(r'<nav class="site-nav[^"]*"[^>]*>.*?</nav>', html, re.S)


# --- off by default -----------------------------------------------------------


def test_no_site_nav_renders_nothing(tmp_path: Path) -> None:
    for name, html in build(config(None), tmp_path).items():
        assert "site-nav" not in html, name


# --- golden HTML ---------------------------------------------------------------


def test_golden_header_and_footer(tmp_path: Path, update_golden: bool) -> None:
    html = build(config({"items": ITEMS}), tmp_path)["index.html"]
    header = html[html.index("<main") : html.index("</header>")]
    footer = html[html.index("<footer") : html.index("</footer>")]
    got = {"header.html": header + "\n", "footer.html": footer + "\n"}
    for name, text in got.items():
        path = GOLDEN / name
        if update_golden:
            GOLDEN.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
        assert path.is_file(), "no golden file; run pytest --update-golden"
        assert text == path.read_text(encoding="utf-8"), name


def test_bar_comes_before_the_header_and_family_line_in_the_footer(
    tmp_path: Path,
) -> None:
    html = build(config({"items": ITEMS}), tmp_path)["index.html"]
    found = bars(html)
    assert len(found) == 2
    assert html.index(found[0]) < html.index("<header")
    assert html.index("<footer") < html.index(found[1]) < html.index("</footer>")


# --- the current item, aria-current, the landmark label ------------------------


def test_current_item_has_aria_current_and_others_do_not(tmp_path: Path) -> None:
    html = build(config({"items": ITEMS}), tmp_path)["index.html"]
    for bar in bars(html):
        assert bar.count("aria-current") == 1
        assert re.search(r'<a [^>]*aria-current="page"[^>]*>Links</a>', bar)
        assert not re.search(r"aria-current[^>]*>(Media kit|Main site)<", bar)


def test_no_current_item_means_no_aria_current(tmp_path: Path) -> None:
    items = [{k: v for k, v in i.items() if k != "current"} for i in ITEMS]
    html = build(config({"items": items}), tmp_path)["index.html"]
    assert "aria-current" not in "".join(bars(html))


def test_every_bar_is_a_labelled_nav_landmark(tmp_path: Path) -> None:
    html = build(config({"items": ITEMS}), tmp_path)["index.html"]
    header_bar, footer_line = bars(html)
    assert 'aria-label="Sites"' in header_bar
    assert 'aria-label="More from this creator"' in footer_line
    assert "role=" not in header_bar + footer_line


def test_items_are_plain_anchors_in_order(tmp_path: Path) -> None:
    html = build(config({"items": ITEMS}), tmp_path)["index.html"]
    for bar in bars(html):
        hrefs = re.findall(r'<a [^>]*href="([^"]+)"', bar)
        assert hrefs == [i["url"] for i in ITEMS]
        for tag in re.findall(r"<a [^>]*>", bar):
            assert 'rel="noopener noreferrer"' in tag
            for banned in ("ping", "data-link-id", "onclick", "target", "tabindex"):
                assert banned not in tag, banned


def test_no_script_is_added(tmp_path: Path) -> None:
    plain = build(config(None), tmp_path / "a")
    with_nav = build(config({"items": ITEMS}), tmp_path / "b")
    for name, html in with_nav.items():
        assert html.count("<script") == plain[name].count("<script"), name
        assert "<script" not in "".join(bars(html))
        assert "javascript:" not in html


# --- switches -----------------------------------------------------------------


def test_header_and_footer_are_switchable(tmp_path: Path) -> None:
    only_footer = build(config({"items": ITEMS, "header": False}), tmp_path / "f")
    only_header = build(config({"items": ITEMS, "footer": False}), tmp_path / "h")
    for html in only_footer.values():
        (line,) = bars(html)
        assert html.index("<footer") < html.index(line)
    for html in only_header.values():
        (bar,) = bars(html)
        assert html.index(bar) < html.index("<header")


def test_bar_is_on_every_page_and_the_kit(tmp_path: Path) -> None:
    cfg = config({"items": ITEMS}, kit=True)
    build_site(cfg, tmp_path, today=TODAY, assets_dir=ASSETS)
    for name in ("index.html", "more/index.html", "privacy/index.html", "404.html"):
        html = (tmp_path / name).read_text(encoding="utf-8")
        assert len(bars(html)) == 2, name
    kit = (tmp_path / "kit" / "index.html").read_text(encoding="utf-8")
    assert len(bars(kit)) == 2


# --- validation ---------------------------------------------------------------


def bad(site_nav: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as info:
        config(site_nav)
    return str(info.value)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://links.example.com/",
        "//links.example.com/",
        "/kit/",
        "tab:more",
        "https://user:pw@links.example.com/",
        "https://links.example.com/a b",
        "data:text/html,x",
    ],
)
def test_urls_must_be_plain_https(url: str) -> None:
    bad({"items": [{"title": "Links", "url": url}]})


def test_at_most_six_items() -> None:
    six = [
        {"title": f"Site {n}", "url": f"https://s{n}.example.com/"} for n in range(6)
    ]
    config({"items": six})
    seven = [*six, {"title": "Site 7", "url": "https://s7.example.com/"}]
    assert "at most 6" in bad({"items": seven})


def test_at_least_one_item() -> None:
    bad({"items": []})


@pytest.mark.parametrize(
    "title",
    ["", "x" * 25, " Links", "Links ", "Li​nks", "Li\nnks", "Li‮nks"],
)
def test_titles_are_one_to_24_plain_characters(title: str) -> None:
    bad({"items": [{"title": title, "url": "https://links.example.com/"}]})


def test_24_characters_is_fine() -> None:
    config({"items": [{"title": "x" * 24, "url": "https://links.example.com/"}]})


def test_title_is_escaped(tmp_path: Path) -> None:
    items = [{"title": "A <b> & B", "url": "https://links.example.com/?a=1&b=2"}]
    html = build(config({"items": items}), tmp_path)["index.html"]
    assert "A &lt;b&gt; &amp; B" in html
    assert "<b>" not in "".join(bars(html))
    assert "a=1&amp;b=2" in html


def test_only_one_current_item() -> None:
    items = [
        {"title": "A", "url": "https://a.example.com/", "current": True},
        {"title": "B", "url": "https://b.example.com/", "current": True},
    ]
    assert "only one item can be current" in bad({"items": items})


def test_header_and_footer_cannot_both_be_off() -> None:
    assert "header or footer" in bad({"items": ITEMS, "header": False, "footer": False})


def test_unknown_setting_is_refused() -> None:
    bad({"items": ITEMS, "script": True})
    bad({"items": [{"title": "A", "url": "https://a.example.com/", "id": "x"}]})


# --- CSS ----------------------------------------------------------------------


def site_css(tmp_path: Path) -> str:
    build(config({"items": ITEMS}), tmp_path)
    (path,) = (tmp_path / "static").glob("site*.css")
    return path.read_text(encoding="utf-8")


def rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_tap_target_css_uses_the_token_and_no_px(tmp_path: Path) -> None:
    css = site_css(tmp_path)
    block = css[css.index("/* Property bar") : css.index("/* end property bar */")]
    assert "min-height: var(--ra-tap)" in rule(block, ".site-nav a")
    assert "px" not in block
    assert ":focus-visible" in block
    assert '[aria-current="page"]' in block
    assert "text-decoration" in rule(block, '.site-nav a[aria-current="page"]')


def test_print_hides_the_bar_and_keeps_the_footer_line(tmp_path: Path) -> None:
    css = site_css(tmp_path)
    printed = css[css.index("@media print") :]
    assert ".site-nav-bar" in printed.split("}")[0] or re.search(
        r"\.site-nav-bar[^{}]*\{[^}]*display: none", printed
    )
    assert ".site-nav-foot" not in printed


def test_reflow_wraps_instead_of_clipping(tmp_path: Path) -> None:
    css = site_css(tmp_path)
    block = css[css.index("/* Property bar") : css.index("/* end property bar */")]
    assert "flex-wrap: wrap" in rule(block, ".site-nav ul")
    assert "overflow-x" not in block and "overflow:" not in block
