# SPDX-License-Identifier: Apache-2.0
"""The two recommendation groups and the affiliate flag (#873).

The link page shows "Gear I Use" and "Services I Use" as two groups. A link
the owner earns from carries ``affiliate: true``: it gets ``rel="sponsored"``
and turns the disclosure line on. A page with no flagged link shows no
disclosure line.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.models import Link
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "instance"
    / "site-link-grid.yaml"
)
LINE = "Some links here are affiliate links."
SPONSORED = 'rel="sponsored noopener noreferrer"'
PLAIN = 'rel="noopener noreferrer"'


def example() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    doc["disclosure"] = LINE
    return doc


def tab(doc: dict[str, Any], intent: str) -> dict[str, Any]:
    found: dict[str, Any] = next(t for t in doc["tabs"] if t["intent"] == intent)
    return found


def set_affiliate(doc: dict[str, Any], flag: bool) -> None:
    for t in doc["tabs"]:
        for block in t["blocks"]:
            block["affiliate"] = flag


def render(tmp_path: Path, doc: dict[str, Any]) -> str:
    out = tmp_path / "out"
    build_site(
        SiteConfig.model_validate(doc), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    return (out / "index.html").read_text(encoding="utf-8")


def group_html(html: str, intent: str) -> str:
    start = html.index(f'id="group-{intent}"')
    nxt = html.find('<section class="smart-group ', start)
    return html[start : nxt if nxt != -1 else len(html)]


def anchors(html: str) -> list[str]:
    return re.findall(r'<a class="link[^"]*smart-tile[^"]*"[^>]*>', html)


# ---- schema and validation --------------------------------------------------


def test_the_services_intent_is_accepted() -> None:
    config = SiteConfig.model_validate(example())
    assert [t.intent for t in config.tabs if t.intent in ("gear", "services")] == [
        "gear",
        "services",
    ]


def test_an_unknown_group_name_is_refused() -> None:
    doc = example()
    tab(doc, "services")["intent"] = "recommendations"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)


def test_each_group_appears_once() -> None:
    doc = example()
    twin = copy.deepcopy(tab(doc, "services"))
    twin["id"] = "services-again"
    for n, block in enumerate(twin["blocks"]):
        block["id"] = f"again-{n}"
    doc["tabs"].append(twin)
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)


@pytest.mark.parametrize("value", ["yes", "true", "True", 1, 0, "", None, []])
def test_affiliate_must_be_a_real_boolean(value: object) -> None:
    doc = example()
    tab(doc, "services")["blocks"][0]["affiliate"] = value
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)


@pytest.mark.parametrize("value", [True, False])
def test_affiliate_accepts_a_boolean(value: bool) -> None:
    doc = example()
    tab(doc, "services")["blocks"][0]["affiliate"] = value
    config = SiteConfig.model_validate(doc)
    link = next(t for t in config.tabs if t.intent == "services").blocks[0]
    assert isinstance(link, Link)
    assert link.affiliate is value


@pytest.mark.parametrize(
    "url",
    [
        "http://m.do.co/c/e3aaa65aed6b",
        "javascript:alert(1)",
        "ftp://example.invalid/x",
        "//example.invalid/x",
        "m.do.co/c/e3aaa65aed6b",
    ],
)
def test_a_non_https_item_url_is_refused(url: str) -> None:
    doc = example()
    tab(doc, "services")["blocks"][0]["url"] = url
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)


@pytest.mark.parametrize("title", ["", " ", "   "])
def test_a_group_and_an_item_need_a_real_title(title: str) -> None:
    doc = example()
    tab(doc, "services")["title"] = title
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)
    doc = example()
    tab(doc, "services")["blocks"][0]["title"] = title
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)


def test_an_affiliate_link_cannot_point_inside_the_site() -> None:
    doc = example()
    first = tab(doc, "services")["blocks"][0]
    first["url"] = "tab:gear"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(doc)


# ---- render -----------------------------------------------------------------


def test_the_two_groups_have_the_exact_titles_in_order(tmp_path: Path) -> None:
    html = render(tmp_path, example())
    titles = re.findall(r"<h2 id=\"group-[a-z]+-title\">([^<]+)</h2>", html)
    assert "Gear I Use" in titles and "Services I Use" in titles
    assert titles.index("Gear I Use") + 1 == titles.index("Services I Use")
    assert titles.count("Services I Use") == 1


def test_digitalocean_is_the_first_services_item_and_is_flagged(
    tmp_path: Path,
) -> None:
    html = render(tmp_path, example())
    services = group_html(html, "services")
    tiles = anchors(services)
    assert len(tiles) == 2
    assert 'href="https://m.do.co/c/e3aaa65aed6b"' in tiles[0]
    assert "DigitalOcean" in services.split("</a>")[0]
    assert SPONSORED in tiles[0]
    assert "example.invalid/services-example" in tiles[1] and PLAIN in tiles[1]


def test_gear_items_keep_config_order_and_flagged_ones_are_sponsored(
    tmp_path: Path,
) -> None:
    html = render(tmp_path, example())
    gear = group_html(html, "gear")
    hrefs = re.findall(r'href="([^"]+)"', gear)
    assert hrefs[:3] == [
        "https://example.invalid/gear-radio",
        "https://example.invalid/gear-security",
        "https://example.invalid/gear-example",
    ]
    tiles = anchors(gear)
    assert [SPONSORED in t for t in tiles] == [False, False, True]
    assert [PLAIN in t for t in tiles] == [True, True, False]


def test_every_outbound_link_is_noopener_and_none_set_a_target(
    tmp_path: Path,
) -> None:
    html = render(tmp_path, example())
    for tile in anchors(html):
        assert "noopener" in tile
        assert "target=" not in tile


# ---- the disclosure line: only when a flagged item is on the page -----------


def test_the_disclosure_shows_when_a_flagged_item_is_present(tmp_path: Path) -> None:
    html = render(tmp_path, example())
    assert f'<p class="disclosure">{LINE}</p>' in html


def test_the_disclosure_is_absent_when_no_item_is_flagged(tmp_path: Path) -> None:
    doc = example()
    set_affiliate(doc, False)
    html = render(tmp_path, doc)
    assert LINE not in html
    assert SPONSORED not in html
    assert 'class="disclosure"' not in html


@pytest.mark.parametrize(("intent", "index"), [("gear", 2), ("services", 0)])
def test_one_flagged_item_in_either_group_is_enough(
    tmp_path: Path, intent: str, index: int
) -> None:
    doc = example()
    set_affiliate(doc, False)
    tab(doc, intent)["blocks"][index]["affiliate"] = True
    html = render(tmp_path, doc)
    assert LINE in html
    assert html.count(SPONSORED) == 1


def test_a_hidden_flagged_item_does_not_turn_the_disclosure_on(
    tmp_path: Path,
) -> None:
    doc = example()
    set_affiliate(doc, False)
    flagged = tab(doc, "services")["blocks"][0]
    flagged["affiliate"] = True
    flagged["show_until"] = "2020-01-01"
    html = render(tmp_path, doc)
    assert "m.do.co" not in html
    assert LINE not in html


def test_the_example_keeps_the_real_url_only_for_digitalocean() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "—" not in text
    outside = re.findall(r"https?://(?!example\.(?:com|invalid|org)\b)\S+", text)
    assert outside == ["https://m.do.co/c/e3aaa65aed6b"]
