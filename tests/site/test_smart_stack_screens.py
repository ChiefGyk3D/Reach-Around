# SPDX-License-Identifier: Apache-2.0
"""Support and Contact screens of the compact link page (#873): the build."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from markupsafe import escape
from pydantic import ValidationError
from test_smart_stack_compact import raw as compact_raw

from reach_around.config import SiteConfig
from reach_around.config.models import Link
from reach_around.site import build as build_module
from reach_around.site.build import _page_paths, build_site
from reach_around.site.supportcheck import (
    SupportPageError,
    check_crypto_group_html,
    check_support_html,
)

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
                block("paid-tip", "StreamElements", icon="streamelements"),
                block("paid-merch", "Merch", icon="merch"),
            ],
        },
        {
            "id": "help-crypto",
            "title": "Crypto tips",
            "intent": "crypto",
            "screen": "support",
            "blocks": [
                block("crypto-btc", "Bitcoin", icon="bitcoin", url="tab:help-crypto"),
                block("crypto-xmr", "Monero", icon="monero", url="tab:help-crypto"),
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
    # Crypto tiles are inside links (no click path), so they carry no link id.
    expected = {
        b.id
        for t in config.tabs
        if t.intent != "crypto"
        for b in t.blocks
        if isinstance(b, Link)
    }
    assert set(everything) == expected
    for tab in (t for t in config.tabs if t.intent != "crypto"):
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


# ---- Review round 1 (#873): empty screens, the crypto boundary, hostile text ----

HOSTILE_TEXT = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "' onmouseover='boom()",
    "a & b < c > d",
    "שלום السلام",  # RTL letters
    "‮evil‬",  # RTL override and pop
    "zero​width",
    "bom﻿here",
    "L" * 400,
]
NEW_GROUPS = ["help-free", "help-paid", "help-crypto", "say", "hire"]
SCREEN_OF = {
    "help-free": "support",
    "help-paid": "support",
    "help-crypto": "support",
    "say": "contact",
    "hire": "contact",
}
ADDRESS = "bc1qexampleexampleexampleexampleexample"


def group(data: dict[str, Any], ident: str) -> dict[str, Any]:
    return next(t for t in data["tabs"] if t["id"] == ident)


def expire(data: dict[str, Any], ident: str) -> None:
    for item in group(data, ident)["blocks"]:
        item["show_until"] = "2027-03-01"


def written(out: Path) -> set[str]:
    return {p.relative_to(out).as_posix() for p in out.rglob("*.html")}


def tab_bar(html: str) -> list[str]:
    nav = re.search(r'<nav class="smart-screens".*?</nav>', html, re.S)
    return re.findall(r">([^<]+)</a>", nav.group(0)) if nav else []


def support_page_config() -> dict[str, Any]:
    return {
        "title": "Support",
        "blocks": [
            {"id": "pay", "coins": [{"coin": "btc", "address": ADDRESS}]},
        ],
    }


def test_a_screen_whose_links_are_all_expired_is_not_built_or_advertised(
    tmp_path: Path,
) -> None:
    data = raw()
    for ident in ("help-free", "help-paid", "help-crypto"):
        expire(data, ident)
    out = build(tmp_path, data)
    assert "support/index.html" not in written(out)
    assert not (out / "support").exists()
    for name in ("index.html", "contact/index.html"):
        assert tab_bar(page(out, name)) == ["Links", "Contact"]


def test_every_screen_expired_leaves_no_tab_bar_and_no_screen_page(
    tmp_path: Path,
) -> None:
    data = raw()
    for ident in NEW_GROUPS:
        expire(data, ident)
    out = build(tmp_path, data)
    assert "index.html" in written(out)
    assert not {"support/index.html", "contact/index.html"} & written(out)
    assert 'class="smart-screens"' not in page(out, "index.html")
    assert "smart-has-tabs" not in page(out, "index.html")


def test_a_screen_with_one_expired_group_keeps_its_page_and_other_groups(
    tmp_path: Path,
) -> None:
    data = raw()
    expire(data, "help-paid")
    out = build(tmp_path, data)
    html = page(out, "support/index.html")
    assert "Paid ways to help" not in html
    assert "Free ways to help" in html and "Crypto tips" in html
    assert tab_bar(html) == ["Links", "Support", "Contact"]


def test_an_expired_hire_link_drops_the_panel_and_keeps_contact(
    tmp_path: Path,
) -> None:
    data = raw()
    expire(data, "hire")
    out = build(tmp_path, data)
    html = page(out, "contact/index.html")
    assert "smart-hire" not in html and "Hire me" not in html
    assert "Get in touch" in html


def test_a_hire_only_contact_screen_that_is_expired_is_not_built(
    tmp_path: Path,
) -> None:
    data = raw()
    data["tabs"] = [t for t in data["tabs"] if t["id"] != "say"]
    expire(data, "hire")
    out = build(tmp_path, data)
    assert "contact/index.html" not in written(out)
    assert tab_bar(page(out, "index.html")) == ["Links", "Support"]


def test_a_future_window_hides_a_screen_until_it_opens(tmp_path: Path) -> None:
    data = raw()
    for item in group(data, "say")["blocks"]:
        item["show_from"] = "2027-04-01"
    group(data, "hire")["blocks"][0]["show_from"] = "2027-04-01"
    out = build(tmp_path, data)
    assert "contact/index.html" not in written(out)


# ---- The crypto group is inside links only, checked on the built page ----


def crypto_html(extra: str = "", href: str = "../support/#group-crypto") -> str:
    return (
        "<!doctype html><html><body><main>"
        '<section class="smart-group smart-group-crypto" id="group-crypto" '
        'aria-labelledby="group-crypto-title"><h2 id="group-crypto-title">Crypto</h2>'
        f'<div class="smart-tiles"><a class="link" href="{href}">'
        '<span class="link-chip"><span class="icon icon-bitcoin"></span></span>'
        '<span class="link-text"><span class="link-title">Bitcoin</span></span></a>'
        f"{extra}</div></section></main></body></html>"
    )


def test_the_built_crypto_group_holds_only_inside_links(tmp_path: Path) -> None:
    html = page(build(tmp_path), "support/index.html")
    check_crypto_group_html(html)
    section = re.search(r'<section[^>]*id="group-crypto".*?</section>', html, re.S)
    assert section is not None
    hrefs = re.findall(r'href="([^"]*)"', section.group(0))
    assert hrefs
    assert all(h == "../support/#group-crypto" for h in hrefs)
    assert "ping=" not in section.group(0)
    assert "https:" not in section.group(0)


@pytest.mark.parametrize(
    "href", ["../support/#group-crypto", "#x", "./", "../", "a/b/", "../#group-videos"]
)
def test_the_crypto_check_accepts_the_inside_shapes(href: str) -> None:
    check_crypto_group_html(crypto_html(href=href))


@pytest.mark.parametrize(
    "href",
    [
        "https://attacker.example/collect",
        "HTTPS://attacker.example/",
        "http://attacker.example/",
        "//attacker.example/x",
        "javascript:alert(1)",
        "data:text/html,x",
        "mailto:a@example.invalid",
        "../x?next=https://attacker.example",
        " https://attacker.example",
        "..\\attacker.example",
    ],
)
def test_the_crypto_check_refuses_a_link_that_leaves_the_site(href: str) -> None:
    with pytest.raises(SupportPageError):
        check_crypto_group_html(crypto_html(href=href))


@pytest.mark.parametrize(
    "extra",
    [
        '<form action="/x"><input name="k"></form>',
        '<iframe src="../x"></iframe>',
        '<img src="../x.png" alt="">',
        "<script>1</script>",
        "<object data='../x'></object>",
        "<embed src='../x'>",
        "<svg><a href='../x'></a></svg>",
        "<style>a{}</style>",
        '<span onclick="x()">t</span>',
        '<span style="color:red">t</span>',
        "<!-- a comment -->",
        '<button type="button">Copy</button>',
        '<a class="link" href="../x" ping="https://attacker.example/p">t</a>',
        "<code class='crypto-address'>bc1qaddr</code>",
    ],
)
def test_the_crypto_check_refuses_other_content_in_the_group(extra: str) -> None:
    with pytest.raises(SupportPageError):
        check_crypto_group_html(crypto_html(extra))


def test_an_unclosed_crypto_group_is_refused() -> None:
    with pytest.raises(SupportPageError):
        check_crypto_group_html(crypto_html().replace("</section>", ""))


def test_a_link_inside_crypto_content_fails_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a tile ever carries an outbound URL the real build refuses the page."""
    original = build_module._tile

    def outbound(link: Link) -> Link:
        shown = original(link)
        if shown.id.startswith("crypto-"):
            return shown.model_copy(update={"url": "https://attacker.example/pay"})
        return shown

    monkeypatch.setattr(build_module, "_tile", outbound)
    with pytest.raises(SupportPageError):
        build(tmp_path)


def test_the_old_support_page_check_still_runs_and_still_refuses_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = compact_raw()
    for t in data["tabs"]:
        if t["id"] == "support":
            t["id"] = "help"
    data["support"] = support_page_config()
    calls: list[str] = []
    real = check_support_html

    def spy(html: str, *args: Any, **kwargs: Any) -> None:
        calls.append(html)
        real(html, *args, **kwargs)

    monkeypatch.setattr("reach_around.site.build.check_support_html", spy)
    built = page(build(tmp_path, data), "support/index.html")
    assert len(calls) == 1
    assert ADDRESS in built
    start = built.index('<section class="support"')
    end = built.index(">", start) + 1
    linked = built[:end] + '<a href="https://attacker.example/pay">x</a>' + built[end:]
    with pytest.raises(SupportPageError):
        check_support_html(linked, [ADDRESS], frozenset(), None)


def test_a_support_screen_and_the_support_page_cannot_coexist() -> None:
    data = raw()
    data["support"] = support_page_config()
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


# ---- The script claim is exact ----


def scripts(html: str) -> list[str]:
    return re.findall(r'<script src="([^"]+)"', html)


def test_a_screen_page_has_no_script_when_every_script_feature_is_off(
    tmp_path: Path,
) -> None:
    data = raw()
    data["features"] = {"click_tracking": False}
    data["page"]["live_badges"] = True
    out = build(tmp_path, data)
    for name in ("support/index.html", "contact/index.html"):
        assert "<script" not in page(out, name)
    assert any("live" in src for src in scripts(page(out, "index.html")))


def test_a_screen_page_loads_only_the_click_script_when_click_tracking_is_on(
    tmp_path: Path,
) -> None:
    data = raw()
    data["features"] = {"click_tracking": True}
    data["page"]["live_badges"] = True
    out = build(tmp_path, data)
    for name in ("support/index.html", "contact/index.html"):
        found = scripts(page(out, name))
        assert len(found) == 1 and "click" in found[0]


def test_the_docs_say_exactly_when_a_screen_has_script() -> None:
    text = (HERE.parents[1] / "docs" / "config.md").read_text(encoding="utf-8")
    section = text.split("### Screens", 1)[1].split("### Collapsed groups", 1)[0]
    assert "script-free" not in section
    assert "features.click_tracking" in section
    assert "no `<script>` at all" in section


# ---- Hostile text on every new screen, through the real builder ----


def refused_without_echo(data: dict[str, Any], value: str) -> bool:
    try:
        SiteConfig.model_validate(data)
    except ValidationError as exc:
        assert value not in str(exc)
        return True
    return False


@pytest.mark.parametrize("ident", NEW_GROUPS)
@pytest.mark.parametrize("value", HOSTILE_TEXT)
def test_hostile_text_is_refused_or_exactly_escaped_on_every_new_screen(
    tmp_path: Path, ident: str, value: str
) -> None:
    data = raw()
    target = group(data, ident)
    target["title"] = value
    target["blocks"][0]["title"] = value
    if ident != "help-crypto":
        target["note"] = value
    if refused_without_echo(data, value):
        return
    html = page(build(tmp_path, data), SCREEN_OF[ident] + "/index.html")
    assert f">{escape(value)}</" in html
    for fragment in ("<script>alert", "<img src=x", "' onmouseover='", '"><img'):
        assert fragment not in html


@pytest.mark.parametrize("ident", NEW_GROUPS)
@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "file:///etc/passwd",
        "http://example.invalid/plain",
        "//example.invalid/x",
        "https://example.invalid/a b",
        "https://user:pass@example.invalid/",
        "https://example.invalid/‮",
    ],
)
def test_a_hostile_url_is_refused_on_every_new_screen(ident: str, url: str) -> None:
    data = raw()
    group(data, ident)["blocks"][0]["url"] = url
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


@pytest.mark.parametrize("ident", ["help-free", "help-paid", "say", "hire"])
def test_quotes_in_a_valid_url_stay_escaped_on_every_new_screen(
    tmp_path: Path, ident: str
) -> None:
    data = raw()
    group(data, ident)["blocks"][0]["url"] = (
        "https://example.invalid/?q=%22onmouseover%3D&x=a%26b"
    )
    html = page(build(tmp_path, data), SCREEN_OF[ident] + "/index.html")
    assert " onmouseover=" not in html
    assert 'href="https://example.invalid/?q=%22onmouseover%3D&amp;x=a%26b"' in html
