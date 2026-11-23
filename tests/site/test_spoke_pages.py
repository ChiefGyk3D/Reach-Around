# SPDX-License-Identifier: Apache-2.0
"""Spoke pages: a stack tab that renders as its own page (#676)."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_smart_stack import raw as stack_raw

from reach_around.config import SiteConfig
from reach_around.site.build import _internal_href, _page_paths, build_site

TODAY = date(2027, 3, 10)
ORIGIN = "https://links.example.com"


def raw(*, spoke_id: str = "gear") -> dict[str, Any]:
    data = copy.deepcopy(stack_raw())
    data["page"]["url"] = ORIGIN
    data["creator"]["title"] = "Example Creator Links"
    shop = next(t for t in data["tabs"] if t["intent"] == "shop")
    shop.update(
        id=spoke_id,
        title="Gear",
        spoke=True,
        page_title="Gear - Example Creator",
        blocks=[
            {
                "id": "picks",
                "title": "Desk",
                "collapsed": False,
                "links": [
                    {
                        "id": f"gear-{n}",
                        "title": f"Pick {n}",
                        "url": f"https://example.com/gear/{n}",
                        "affiliate": n == 1,
                    }
                    for n in range(1, 8)
                ],
            }
        ],
    )
    data["disclosure"] = "Some links earn me a commission."
    return data


def build(tmp_path: Path, data: dict[str, Any]) -> Path:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    return out


def text(out: Path, rel: str) -> str:
    return (out / rel).read_text(encoding="utf-8")


def test_spoke_renders_its_own_page(tmp_path: Path) -> None:
    out = build(tmp_path, raw())
    page = text(out, "gear/index.html")
    assert "<title>Gear - Example Creator</title>" in page
    assert f'<link rel="canonical" href="{ORIGIN}/gear/">' in page
    assert '<a href="../privacy/">Privacy</a>' in page
    assert "Some links earn me a commission." in page
    assert 'href="https://example.com/gear/3"' in page
    assert 'href="../static/' in page
    assert 'href="../">Back to the links</a>' in page


def test_spoke_clicks_are_counted(tmp_path: Path) -> None:
    data = raw()
    data["features"] = {"click_tracking": True}
    out = build(tmp_path, data)
    page = text(out, "gear/index.html")
    for n in range(1, 8):
        assert f'ping="/e/c/gear-{n}" data-link-id="gear-{n}"' in page
    assert 'data-landing="gear"' in page
    manifest = build_site(
        SiteConfig.model_validate(data), tmp_path / "again", today=TODAY
    )
    assert manifest.links["gear-5"] == "https://example.com/gear/5"


def test_hub_shows_one_card_not_the_picks(tmp_path: Path) -> None:
    out = build(tmp_path, raw())
    hub = text(out, "index.html")
    assert f'<link rel="canonical" href="{ORIGIN}/">' in hub
    assert 'href="gear/"' in hub
    assert "Pick 3" not in hub
    assert "/e/c/gear-" not in hub
    section = hub[hub.index('id="group-shop"') :]
    section = section[: section.index("</section>")]
    assert "Gear" in section and "ping=" not in section


def test_hub_disclosure_follows_only_the_hub_links(tmp_path: Path) -> None:
    out = build(tmp_path, raw())
    assert "commission" not in text(out, "index.html")
    assert "commission" in text(out, "gear/index.html")


def test_internal_links_reach_the_spoke(tmp_path: Path) -> None:
    data = raw()
    blog = next(t for t in data["tabs"] if t["intent"] == "blog")
    blog["blocks"][0]["url"] = "tab:gear"
    out = build(tmp_path, data)
    assert 'href="gear/"' in text(out, "index.html")
    config = SiteConfig.model_validate(data)
    for root, want in (("./", "gear/"), ("../", "../gear/"), ("/", "/gear/")):
        assert _internal_href(config, root)("tab:gear") == want
    assert _internal_href(config, "../")("tab:gear#picks") == "../gear/#picks"
    assert (
        _internal_href(config, "../")(
            "tab:shop-nothing".replace("shop-nothing", "blog")
        )
        == "../#group-blog"
    )


def test_no_spoke_changes_nothing(tmp_path: Path) -> None:
    data = stack_raw()
    out = build(tmp_path, data)
    assert "canonical" not in text(out, "index.html")
    assert not (out / "gear").exists()


def test_page_paths_name_the_spoke() -> None:
    paths = _page_paths(SiteConfig.model_validate(raw()))
    assert Path("gear/index.html") in paths


# --- validation --------------------------------------------------------------


def bad(mutate: Any) -> str:
    data = raw()
    mutate(data)
    with pytest.raises(ValueError) as caught:
        SiteConfig.model_validate(data)
    return str(caught.value)


def shop(data: dict[str, Any]) -> dict[str, Any]:
    return next(t for t in data["tabs"] if t["intent"] == "shop")


def test_spoke_needs_the_stack() -> None:
    data = raw()
    data["page"] = {"layout": "classic"}
    data["tabs"] = [t for t in data["tabs"] if t["intent"] != "watch"]
    with pytest.raises(ValueError, match="spoke needs page.layout: stack"):
        SiteConfig.model_validate(data)


def test_spoke_needs_the_canonical_origin() -> None:
    assert "page.url" in bad(lambda d: d["page"].pop("url"))


@pytest.mark.parametrize(
    "url",
    [
        "http://links.example.com",
        "https://links.example.com/path",
        "https://links.example.com/?q=1",
        "https://links.example.com/#x",
        "https://user@links.example.com",
        "links.example.com",
    ],
)
def test_page_url_is_a_bare_https_origin(url: str) -> None:
    assert "page.url" in bad(lambda d: d["page"].update(url=url))


@pytest.mark.parametrize("ident", ["privacy", "static", "e", "e-frame", "kit", "404"])
def test_spoke_id_cannot_take_a_reserved_path(ident: str) -> None:
    data = raw(spoke_id=ident)
    with pytest.raises(ValueError, match="reserved"):
        SiteConfig.model_validate(data)


def test_the_watch_group_cannot_be_a_spoke() -> None:
    def make(d: dict[str, Any]) -> None:
        d["tabs"][0]["spoke"] = True

    assert "Watch group" in bad(make)


def test_a_spoke_has_no_group_style_or_collapse() -> None:
    assert "spoke" in bad(lambda d: shop(d).update(style="rows"))
    assert "spoke" in bad(lambda d: shop(d).update(collapsed=True))


def test_a_spoke_has_no_link_count_limit_but_needs_one() -> None:
    SiteConfig.model_validate(raw())  # seven links is fine
    assert "at least one" in bad(lambda d: shop(d).update(blocks=[]))


# --- review fixes -------------------------------------------------------------


def test_hub_discloses_a_featured_affiliate_link_from_a_spoke(tmp_path: Path) -> None:
    data = raw()
    data["page"]["featured"] = {"link": "gear-1", "kicker": "New"}
    out = build(tmp_path, data)
    hub = text(out, "index.html")
    assert "sponsored" in hub
    assert "Some links earn me a commission." in hub


def test_hub_with_a_featured_plain_spoke_link_has_no_disclosure(
    tmp_path: Path,
) -> None:
    data = raw()
    data["page"]["featured"] = {"link": "gear-2", "kicker": "New"}
    out = build(tmp_path, data)
    assert "commission" not in text(out, "index.html")


@pytest.mark.parametrize(
    ("given", "want"),
    [
        ("https://x.com/", "https://x.com"),
        ("https://x.com", "https://x.com"),
        ("https://X.COM", "https://x.com"),
        ("https://x.com:443", "https://x.com"),
        ("https://x.com:8443", "https://x.com:8443"),
        ("https://Links.Example.com:443/", "https://links.example.com"),
    ],
)
def test_page_url_is_normalized(given: str, want: str) -> None:
    data = raw()
    data["page"]["url"] = given
    assert SiteConfig.model_validate(data).page.url == want


@pytest.mark.parametrize(
    "url",
    [
        "https://x .com",
        "https://x.com:abc",
        "https://x.com:99999",
        "https://x.com:-1",
        "https://x.com:0",
        "https://x.com\\evil.com",
        "https://<b>.com",
        "https://a,b.com",
        "https://x.com;a=b",
        "https://x.com%0a",
        "https://x.com?",
        "https://x.com#",
        " https://x.com",
        "https://x.com\n",
        "https://[::1]",
        "https://-x.com",
        "https://x..com",
    ],
)
def test_page_url_rejects_malformed_origins(url: str) -> None:
    data = raw()
    data["page"]["url"] = url
    with pytest.raises(ValueError):
        SiteConfig.model_validate(data)


def test_page_url_needs_the_stack() -> None:
    data = raw()
    data["page"] = {"layout": "classic", "url": ORIGIN}
    data["tabs"] = [t for t in data["tabs"] if t["intent"] != "watch"]
    for tab in data["tabs"]:
        tab.pop("spoke", None)
    with pytest.raises(ValueError, match="page.url needs page.layout: stack"):
        SiteConfig.model_validate(data)


def test_socials_is_reserved_for_a_spoke() -> None:
    with pytest.raises(ValueError, match="reserved"):
        SiteConfig.model_validate(raw(spoke_id="socials"))


def test_spoke_id_cannot_be_the_analytics_route_segment() -> None:
    data = raw()
    data["analytics"] = {"route": "/gear/"}
    with pytest.raises(ValueError, match="analytics"):
        SiteConfig.model_validate(data)
    data["analytics"] = {"route": "/e/v/"}
    SiteConfig.model_validate(data)


def test_a_tab_redirect_to_a_spoke_goes_to_its_page(tmp_path: Path) -> None:
    data = raw()
    data["redirects"] = {"tabs": {"old-gear": "gear"}}
    out = build(tmp_path, data)
    lines = text(out, "_redirects").splitlines()
    assert "/old-gear /gear/ 301" in lines
    assert "/old-gear/ /gear/ 301" in lines
