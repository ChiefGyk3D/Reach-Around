# SPDX-License-Identifier: Apache-2.0
"""Support and Contact screens of the compact link page (#873): the build."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from test_smart_stack_compact import raw as compact_raw

from reach_around.config import SiteConfig
from reach_around.config.models import Link
from reach_around.site.build import _page_paths, build_site

TODAY = date(2027, 3, 10)
HERE = Path(__file__).parent
GOLDEN = HERE / "golden_smart_stack_screens"
EXAMPLE = HERE.parents[1] / "examples" / "instance" / "site-link-screens.yaml"
PAGES = ["index.html", "support/index.html", "contact/index.html"]


def block(ident: str, title: str, **extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": ident,
        "title": title,
        "url": f"https://example.invalid/{ident}",
    }
    fields.update(extra)
    return fields


def raw() -> dict[str, Any]:
    """The compact grid plus a Support screen (free, paid, crypto) and Contact."""
    data = compact_raw()
    data["tabs"] = [t for t in data["tabs"] if t["intent"] != "support"]
    data["tabs"] += [
        {
            "id": "help-free",
            "title": "Free ways to help",
            "intent": "free",
            "screen": "support",
            "columns": 3,
            "note": "Gear I Use is free for you. I may earn a commission.",
            "blocks": [
                block("free-twitch", "Follow on Twitch", icon="twitch"),
                block("free-kick", "Follow on Kick", icon="kick"),
                block("free-youtube", "Subscribe on YouTube", icon="youtube"),
                block("free-radio", "Radio", icon="radio", affiliate=True),
                block("free-security", "Security", icon="shield", affiliate=True),
                block("free-creator", "Creator", icon="tools", affiliate=True),
            ],
        },
        {
            "id": "help-paid",
            "title": "Paid ways to help",
            "intent": "paid",
            "screen": "support",
            "blocks": [
                block("paid-patreon", "Patreon", icon="patreon"),
                block("paid-kofi", "Ko-fi", icon="kofi"),
                block("paid-tip", "Stream tip", icon="streamelements"),
                block("paid-merch", "Merch", icon="merch"),
            ],
        },
        {
            "id": "help-crypto",
            "title": "Crypto tips",
            "intent": "crypto",
            "screen": "support",
            "blocks": [
                block("crypto-btc", "Bitcoin", icon="bitcoin"),
                block("crypto-xmr", "Monero", icon="monero"),
            ],
        },
        {
            "id": "say",
            "title": "Get in touch",
            "intent": "contact",
            "screen": "contact",
            "blocks": [
                block("say-email", "Email", icon="email"),
                block("say-discord", "Discord", icon="discord"),
                block("say-matrix", "Matrix", icon="matrix"),
            ],
        },
        {
            "id": "hire",
            "title": "Hire me",
            "intent": "hire",
            "screen": "contact",
            "note": "Work requests go through the company, not the socials.",
            "blocks": [
                block("hire-company", "example-company.invalid", icon="website")
            ],
        },
    ]
    return data


def build(tmp_path: Path, data: dict[str, Any] | None = None) -> Path:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data or raw()), out, today=TODAY)
    return out


def page(out: Path, name: str) -> str:
    return (out / name).read_text(encoding="utf-8")


def test_the_build_writes_the_home_page_and_one_page_per_used_screen(
    tmp_path: Path,
) -> None:
    out = build(tmp_path)
    written = {p.relative_to(out).as_posix() for p in out.rglob("*.html")}
    assert set(PAGES) <= written
    data = raw()
    data["tabs"] = [t for t in data["tabs"] if t.get("screen") != "contact"]
    out = build(tmp_path / "one", data)
    assert (out / "support" / "index.html").is_file()
    assert not (out / "contact").exists()


def test_a_site_with_no_screen_keeps_its_one_page_and_has_no_tab_bar(
    tmp_path: Path,
) -> None:
    out = build(tmp_path, compact_raw())
    assert not (out / "support").exists() and not (out / "contact").exists()
    html = page(out, "index.html")
    assert "smart-screens" not in html and "smart-has-tabs" not in html


def test_every_link_is_on_exactly_one_page_and_the_watch_group_stays_home(
    tmp_path: Path,
) -> None:
    out = build(tmp_path)
    config = SiteConfig.model_validate(raw())
    by_page = {
        name: re.findall(r'data-link-id="([^"]+)"', page(out, name)) for name in PAGES
    }
    everything = [i for ids in by_page.values() for i in ids]
    assert len(everything) == len(set(everything))
    expected = {b.id for t in config.tabs for b in t.blocks if isinstance(b, Link)}
    assert set(everything) == expected
    for tab in config.tabs:
        name = f"{tab.screen}/index.html" if tab.screen else "index.html"
        for link in (b for b in tab.blocks if isinstance(b, Link)):
            assert link.id in by_page[name], (tab.id, link.id)
    assert "group-watch" in page(out, "index.html")
    for name in ("support/index.html", "contact/index.html"):
        assert "group-watch" not in page(out, name)
        assert "smart-status" not in page(out, name)


def test_each_page_names_its_groups_in_config_order(tmp_path: Path) -> None:
    out = build(tmp_path)

    def headings(name: str) -> list[str]:
        return re.findall(r'<h2 id="group-[a-z]+-title">([^<]+)</h2>', page(out, name))

    assert headings("support/index.html") == [
        "Free ways to help",
        "Paid ways to help",
        "Crypto tips",
    ]
    assert headings("contact/index.html") == ["Get in touch", "Hire me"]
    assert "Free ways to help" not in page(out, "index.html")


def test_the_tab_bar_links_every_screen_and_marks_the_current_one(
    tmp_path: Path,
) -> None:
    out = build(tmp_path)

    def bar(name: str) -> list[tuple[str, str, bool]]:
        html = page(out, name)
        nav = html[html.index('<nav class="smart-screens"') :]
        nav = nav[: nav.index("</nav>")]
        return [
            (m[0], m[2], "aria-current" in m[1])
            for m in re.findall(
                r'<a class="smart-screen-tab" href="([^"]+)"([^>]*)>([^<]+)</a>', nav
            )
        ]

    assert bar("index.html") == [
        ("./", "Links", True),
        ("support/", "Support", False),
        ("contact/", "Contact", False),
    ]
    assert bar("support/index.html") == [
        ("../", "Links", False),
        ("../support/", "Support", True),
        ("../contact/", "Contact", False),
    ]
    assert bar("contact/index.html")[2] == ("../contact/", "Contact", True)
    assert 'aria-label="Pages"' in page(out, "index.html")


def test_a_screen_page_uses_root_relative_assets_and_its_own_title_and_id(
    tmp_path: Path,
) -> None:
    html = page(build(tmp_path), "support/index.html")
    assert 'href="../static/smart-compact.' in html
    assert 'data-landing="support"' in html
    assert "<title>Support" in html
    assert 'src="../static/click.' in html


def test_twitch_and_kick_in_the_free_group_get_identical_markup(tmp_path: Path) -> None:
    html = page(build(tmp_path), "support/index.html")
    twitch = re.search(r"<a [^>]*free-twitch.*?</a>", html, re.S)
    kick = re.search(r"<a [^>]*free-kick.*?</a>", html, re.S)
    assert twitch and kick

    def norm(text: str) -> str:
        return re.sub(r"twitch|kick|Twitch|Kick", "X", text)

    assert norm(twitch.group(0)) == norm(kick.group(0))


def test_the_hire_group_is_one_marked_block_with_one_link_and_a_note(
    tmp_path: Path,
) -> None:
    out = build(tmp_path)
    html = page(out, "contact/index.html")
    start = html.index('id="group-hire"')
    section = html[start : html.index("</section>", start)]
    assert "smart-hire" in html[start - 80 : start]
    assert section.count("smart-tile") == 2  # the link's class and its span class
    assert section.count("<a ") == 1
    assert '<p class="smart-note">Work requests go through' in section
    assert "smart-hire" not in page(out, "support/index.html")


def test_a_note_is_text_never_markup(tmp_path: Path) -> None:
    data = raw()
    hostile = '<img src=x onerror="boom()"> & "quoted" \'single\''
    for tab in data["tabs"]:
        if tab.get("note"):
            tab["note"] = hostile
    out = build(tmp_path, data)
    for name in ("support/index.html", "contact/index.html"):
        html = page(out, name)
        assert "<img src=x" not in html
        assert "&lt;img src=x onerror=&#34;boom()&#34;&gt; &amp;" in html


HOSTILE_LABELS = [
    "<script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "' onmouseover='boom()",
    "W" * 60,
]


@pytest.mark.parametrize("label", HOSTILE_LABELS)
def test_hostile_labels_on_a_screen_are_escaped(tmp_path: Path, label: str) -> None:
    data = raw()
    for tab in data["tabs"]:
        if tab.get("screen"):
            tab["title"] = label
            tab["blocks"][0]["title"] = label
    out = build(tmp_path, data)
    for name in ("support/index.html", "contact/index.html"):
        html = page(out, name)
        assert "<script>alert" not in html
        assert "<svg onload" not in html
        assert "' onmouseover='" not in html
        assert '" onmouseover=' not in html


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "http://example.invalid/plain",
        "//example.invalid/relative",
        "https://example.invalid/a b",
    ],
)
def test_a_hostile_url_on_a_screen_is_refused_by_the_link_model(url: str) -> None:
    data = raw()
    data["tabs"][-1]["blocks"][0]["url"] = url
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


def test_quotes_and_markup_in_a_valid_url_are_escaped_on_a_screen(
    tmp_path: Path,
) -> None:
    data = raw()
    data["tabs"][-1]["blocks"][0]["url"] = (
        "https://example.invalid/?q=%22onmouseover%3D"
    )
    out = build(tmp_path, data)
    html = page(out, "contact/index.html")
    assert " onmouseover=" not in html
    assert 'href="https://example.invalid/?q=%22onmouseover%3D"' in html


def test_the_footer_disclosure_shows_only_where_a_screen_has_an_affiliate_link(
    tmp_path: Path,
) -> None:
    data = raw()
    data["disclosure"] = "This page uses affiliate links."
    out = build(tmp_path, data)
    assert "This page uses affiliate links." in page(out, "support/index.html")
    assert "This page uses affiliate links." not in page(out, "contact/index.html")
    assert "This page uses affiliate links." not in page(out, "index.html")
    support = page(out, "support/index.html")
    assert support.count('rel="sponsored noopener noreferrer"') == 3


def test_a_tab_link_to_a_group_on_a_screen_resolves_to_that_screen(
    tmp_path: Path,
) -> None:
    data = raw()
    data["tabs"][1]["blocks"].append(
        block("to-free", "Free stuff", url="tab:help-free", icon="link")
    )
    home = page(build(tmp_path, data), "index.html")
    assert 'href="support/#group-free"' in home
    data = raw()
    data["tabs"][-2]["blocks"].append(
        block("to-home", "Home", url="tab:videos", icon="link")
    )
    out = build(tmp_path / "back", data)
    assert 'href="../#group-videos"' in page(out, "contact/index.html")


def test_screen_pages_are_in_the_public_path_set() -> None:
    paths = {p.as_posix() for p in _page_paths(SiteConfig.model_validate(raw()))}
    assert {"support/index.html", "contact/index.html"} <= paths
    plain = {
        p.as_posix() for p in _page_paths(SiteConfig.model_validate(compact_raw()))
    }
    assert not {"support/index.html", "contact/index.html"} & plain


def test_the_example_builds_all_three_screens_with_example_hosts_only(
    tmp_path: Path,
) -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "—" not in text
    assert not re.search(r"https?://(?!example\.(?:com|invalid|org)\b)", text)
    config = SiteConfig.model_validate(yaml.safe_load(text))
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert "Gear I Use" in page(out, "index.html")
    assert "Gear I Use is free for you" in page(out, "support/index.html")
    assert "Hire me" in page(out, "contact/index.html")


@pytest.mark.parametrize("name", PAGES)
def test_screen_html_matches_golden(
    tmp_path: Path, update_golden: bool, name: str
) -> None:
    html = page(build(tmp_path), name)
    target = GOLDEN / name
    if update_golden:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    assert target.is_file(), "no golden file; run pytest --update-golden"
    assert html == target.read_text(encoding="utf-8")
