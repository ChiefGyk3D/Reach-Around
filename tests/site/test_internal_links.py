# SPDX-License-Identifier: Apache-2.0
"""Internal ``tab:<id>[#fragment]`` links on links and socials."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config import ConfigError, SiteConfig, load_site_config
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ANCHOR = re.compile(r"<a\b[^>]*>")
ATTR = re.compile(r'([a-zA-Z-]+)="([^"]*)"')


def data(
    social_url: str = "tab:contact", link_url: str = "tab:contact#form"
) -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {
            "name": "Example Creator",
            "socials": [{"name": "Mail", "url": social_url, "icon": "email"}],
        },
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "go", "title": "Go", "url": link_url},
                    {"id": "out", "title": "Out", "url": "https://example.com/"},
                    {
                        "id": "sec",
                        "title": "Sec",
                        "links": [{"id": "in-sec", "title": "S", "url": "tab:start"}],
                    },
                ],
            },
            {"id": "about", "title": "About", "blocks": []},
            {"id": "contact", "title": "Contact", "blocks": []},
        ],
    }


def load(tmp_path: Path, raw: dict[str, Any]) -> SiteConfig:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_site_config(path)


def refused(tmp_path: Path, raw: dict[str, Any]) -> str:
    with pytest.raises(ConfigError) as info:
        load(tmp_path, raw)
    return str(info.value)


def anchors(page: Path) -> list[dict[str, str]]:
    html = page.read_text(encoding="utf-8")
    return [dict(ATTR.findall(tag)) for tag in ANCHOR.findall(html)]


def built(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    build_site(load(tmp_path, data()), out, today=TODAY)
    return out


@pytest.mark.parametrize(
    "url",
    ["tab:contact", "tab:contact#form", "tab:about#a-1", "tab:start#" + "a" * 40],
)
def test_valid_forms(tmp_path: Path, url: str) -> None:
    config = load(tmp_path, data(social_url=url, link_url=url))
    assert config.creator.socials[0].url == url


@pytest.mark.parametrize("url", ["tab:nope", "tab:nope#x"])
def test_unknown_tab_names_key_and_lists_ids(tmp_path: Path, url: str) -> None:
    message = refused(tmp_path, data(link_url=url))
    assert "tabs[0].blocks[0].url" in message
    assert "'nope'" in message
    assert "start, about, contact" in message


def test_unknown_tab_on_social_names_key(tmp_path: Path) -> None:
    message = refused(tmp_path, data(social_url="tab:nope"))
    assert "creator.socials[0].url" in message
    assert "start, about, contact" in message


def test_unknown_tab_in_section_names_key(tmp_path: Path) -> None:
    raw = data()
    raw["tabs"][0]["blocks"][2]["links"][0]["url"] = "tab:nope"
    assert "tabs[0].blocks[2].links[0].url" in refused(tmp_path, raw)


@pytest.mark.parametrize(
    "url",
    [
        "tab:contact#",
        "tab:contact#Form",
        "tab:contact#a_b",
        "tab:contact#a b",
        "tab:contact#" + "a" * 41,
        "tab:contact#a#b",
        "tab:contact?x=1",
        "tab:",
        "tab:#form",
        "tab:Contact",
        "tab: contact",
        "tab:contact/",
        "tab:../contact",
    ],
)
def test_malformed_refs_refused(tmp_path: Path, url: str) -> None:
    refused(tmp_path, data(link_url=url))
    refused(tmp_path, data(social_url=url))


@pytest.mark.parametrize("url", ["TAB:contact", "Tab:contact", "TAB:CONTACT"])
def test_scheme_is_case_strict(tmp_path: Path, url: str) -> None:
    assert "lowercase" in refused(tmp_path, data(link_url=url))
    refused(tmp_path, data(social_url=url))


@pytest.mark.parametrize(
    "url",
    [
        "mailto:me@example.com",
        "javascript:alert(1)",
        "data:text/html,x",
        "//example.com/x",
        "http://example.com/",
        "/contact/",
        "contact",
    ],
)
def test_other_schemes_still_refused(tmp_path: Path, url: str) -> None:
    refused(tmp_path, data(link_url=url))
    refused(tmp_path, data(social_url=url))


def test_internal_link_cannot_be_affiliate(tmp_path: Path) -> None:
    raw = data()
    raw["tabs"][0]["blocks"][0]["affiliate"] = True
    assert "affiliate" in refused(tmp_path, raw)


def test_hrefs_equal_the_nav_path_of_every_tab(tmp_path: Path) -> None:
    out = built(tmp_path)
    # (page, root prefix seen from that page); the nav href of each tab.
    pages = {
        "start": (out / "index.html", "./", ""),
        "about": (out / "about" / "index.html", "../", "../"),
        "contact": (out / "contact" / "index.html", "../", "../"),
        "privacy": (out / "privacy" / "index.html", "../", "../"),
    }
    for name, (page, home, prefix) in pages.items():
        hrefs = [a["href"] for a in anchors(page)]
        nav_contact = f"{prefix}contact/"
        assert prefix in ("", "../")
        social = [a for a in anchors(page) if a.get("class") == "social"][0]
        assert social["href"] == nav_contact, name
        # The same path the navigation uses for the Contact tab.
        assert nav_contact in hrefs, name
        assert home in hrefs, name  # home tab path


def test_fragment_and_first_tab_resolve(tmp_path: Path) -> None:
    out = built(tmp_path)
    hrefs = {a["href"] for a in anchors(out / "index.html")}
    assert "contact/#form" in hrefs
    assert "./" in hrefs  # tab:start from the first tab itself
    other = {a["href"] for a in anchors(out / "about" / "index.html")}
    assert "../" in other and "../contact/" in other


def test_no_external_attributes_on_internal_links(tmp_path: Path) -> None:
    out = built(tmp_path)
    checked = 0
    for page in (out / "index.html", out / "about" / "index.html"):
        for attrs in anchors(page):
            kind = attrs.get("class", "").split()[:1]
            if kind not in (["link"], ["social"]):
                continue
            if attrs["href"].startswith("https://"):
                continue
            checked += 1
            for forbidden in ("target", "rel", "ping", "data-link-id"):
                assert forbidden not in attrs, attrs
    assert checked >= 4
    external = [
        a for a in anchors(out / "index.html") if a["href"] == "https://example.com/"
    ]
    assert external[0]["rel"] == "noopener noreferrer"
    assert external[0]["ping"] == "/e/c/out"
    assert "_blank" not in (out / "index.html").read_text(encoding="utf-8")


def test_manifest_lists_only_external_links(tmp_path: Path) -> None:
    out = tmp_path / "out"
    manifest = build_site(load(tmp_path, data()), out, today=TODAY)
    assert manifest.links == {"out": "https://example.com/"}
    written = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert written["links"] == manifest.links
