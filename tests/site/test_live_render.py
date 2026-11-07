# SPDX-License-Identifier: Apache-2.0
"""The static render of the live block (#297, slice 2): no script, no player.

Three states are rendered in the page itself: the no-script / status-unknown
state (the offline layout plus two equal watch links), the offline layout and
the hidden live state, two identical click-to-load facades. All data here is
synthetic.
"""

from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from assetpaths import asset
from reach_around.config import SiteConfig
from reach_around.config.blocks import LIVE_PLAYER_ORIGINS
from reach_around.site.build import build_site
from reach_around.site.overrides import build_csp

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_blocks"

TWITCH_ORIGIN = "https://player.twitch.tv"
KICK_ORIGIN = "https://player.kick.com"


def live_block(**changes: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "id": "live",
        "players": [
            {"platform": "kick", "channel": "example_kick"},
            {"platform": "twitch", "channel": "example_twitch"},
        ],
        "also_on": [
            {"platform": "youtube", "url": "https://www.youtube.com/@example"},
            {"platform": "tiktok", "url": "https://www.tiktok.com/@example"},
            {"platform": "instagram", "url": "https://www.instagram.com/example"},
        ],
        "vods": {
            "youtube": "https://www.youtube.com/@example/streams",
            "kick": "https://kick.com/example_kick/videos",
            "twitch": "https://www.twitch.tv/example_twitch/videos",
        },
    }
    block.update(changes)
    return block


def site(
    block: dict[str, Any] | None = None, *, live_first: bool = False
) -> SiteConfig:
    main = {
        "id": "main",
        "title": "Main",
        "blocks": [{"id": "one", "title": "One", "url": "https://example.com/1"}],
    }
    live = {
        "id": "watch",
        "title": "Live",
        "blocks": [block if block is not None else live_block()],
    }
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [live, main] if live_first else [main, live],
        }
    )


def build(cfg: SiteConfig, out: Path) -> Path:
    build_site(cfg, out, today=TODAY)
    return out


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return build(site(), tmp_path / "out")


def page(out: Path, name: str = "watch/index.html") -> str:
    return (out / name).read_text(encoding="utf-8")


class Tree(HTMLParser):
    """A tiny element list: (tag, attrs, text-inside) in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        self._stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        el = {
            "tag": tag,
            "attrs": dict(attrs),
            "text": "",
            "parents": [e["tag"] for e in self._stack],
            "open": True,
        }
        self.elements.append(el)
        if tag not in {"meta", "link", "br", "img", "input", "hr"}:
            self._stack.append(el)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        for el in self._stack:
            el["text"] += data


def parse(html: str) -> Tree:
    tree = Tree()
    tree.feed(html)
    return tree


def with_class(tree: Tree, name: str) -> list[dict[str, Any]]:
    return [e for e in tree.elements if name in e["attrs"].get("class", "").split()]


def panel(tree: Tree, name: str) -> dict[str, Any]:
    found = [e for e in tree.elements if e["attrs"].get("data-live-panel") == name]
    assert len(found) == 1, name
    return found[0]


# --- the build and the files ----------------------------------------------------


def test_build_renders_the_live_block_instead_of_refusing(out: Path) -> None:
    assert 'class="block block-live"' in page(out)


def test_live_stylesheet_ships_only_with_a_live_block(tmp_path: Path) -> None:
    with_live = build(site(), tmp_path / "a")
    name = asset(with_live, "static/live.css")
    assert f'href="../{name}"' in page(with_live)
    assert "static/live." not in page(with_live, "index.html")
    plain = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "main",
                    "title": "Main",
                    "blocks": [{"id": "o", "title": "O", "url": "https://e.com/"}],
                }
            ],
        }
    )
    without = build(plain, tmp_path / "b")
    assert not list((without / "static").glob("live.*.css"))


def test_the_live_page_has_the_wide_column_and_no_other_page_does(out: Path) -> None:
    assert '<main id="content" class="main-wide">' in page(out)
    for name in ("index.html", "privacy/index.html", "404.html"):
        assert "main-wide" not in page(out, name)


# --- the degraded / no-script state (the default markup) -----------------------


def test_default_state_is_degraded_with_the_offline_layout_shown(out: Path) -> None:
    tree = parse(page(out))
    root = [e for e in tree.elements if "data-live" in e["attrs"]]
    assert len(root) == 1
    assert root[0]["attrs"]["data-live-state"] == "degraded"
    assert "hidden" not in panel(tree, "degraded")["attrs"]
    assert "hidden" not in panel(tree, "offline")["attrs"]
    assert "hidden" in panel(tree, "live")["attrs"]


def test_degraded_has_two_equal_watch_links_and_a_small_note(out: Path) -> None:
    tree = parse(page(out))
    links = [
        e
        for e in tree.elements
        if e["tag"] == "a" and "live-watch-link" in e["attrs"].get("class", "").split()
    ]
    assert [(e["text"].strip(), e["attrs"]["href"]) for e in links] == [
        ("Watch live on Kick", "https://kick.com/example_kick"),
        ("Watch live on Twitch", "https://www.twitch.tv/example_twitch"),
    ]
    assert len({e["attrs"]["class"] for e in links}) == 1
    assert all(e["attrs"]["rel"] == "noopener noreferrer" for e in links)
    notes = with_class(tree, "live-status-note")
    assert [n["text"].strip() for n in notes] == ["Live status unavailable."]


def test_degraded_links_follow_the_configured_order(tmp_path: Path) -> None:
    block = live_block(
        players=[
            {"platform": "twitch", "channel": "example_twitch"},
            {"platform": "kick", "channel": "example_kick"},
        ]
    )
    html = page(build(site(block), tmp_path / "o"))
    assert html.index("Watch live on Twitch") < html.index("Watch live on Kick")


# --- the offline layout --------------------------------------------------------


def test_offline_has_three_equal_vod_cards_in_a_fixed_order(out: Path) -> None:
    tree = parse(page(out))
    cards = with_class(tree, "live-vod-link")
    assert [(e["attrs"]["href"]) for e in cards] == [
        "https://www.youtube.com/@example/streams",
        "https://kick.com/example_kick/videos",
        "https://www.twitch.tv/example_twitch/videos",
    ]
    assert len({e["attrs"]["class"] for e in cards}) == 1
    assert all(e["attrs"]["rel"] == "noopener noreferrer" for e in cards)
    labels = [re.sub(r"\s+", " ", e["text"]).strip() for e in cards]
    assert labels == [
        "YouTube Past streams",
        "Kick Past streams",
        "Twitch Past streams",
    ]


def test_offline_has_the_also_on_line_and_the_fediverse_note(out: Path) -> None:
    tree = parse(page(out))
    also = with_class(tree, "live-also-link")
    assert [a["text"].strip() for a in also] == ["YouTube", "TikTok", "Instagram"]
    assert [a["attrs"]["href"] for a in also] == [
        "https://www.youtube.com/@example",
        "https://www.tiktok.com/@example",
        "https://www.instagram.com/example",
    ]
    assert [n["text"].strip() for n in with_class(tree, "live-note")] == [
        "Fediverse coming soon"
    ]


def test_optional_parts_are_left_out_not_emptied(tmp_path: Path) -> None:
    block = live_block(also_on=[], fediverse_note=None)
    del block["vods"]
    tree = parse(page(build(site(block), tmp_path / "o")))
    assert not with_class(tree, "live-vod-link")
    assert not with_class(tree, "live-also-link")
    assert not with_class(tree, "live-note")
    assert not with_class(tree, "live-also")
    # the watch links still exist
    assert len(with_class(tree, "live-watch-link")) == 2


def test_the_fediverse_note_is_escaped_plain_text(tmp_path: Path) -> None:
    block = live_block(fediverse_note="Soon & more")
    html = page(build(site(block), tmp_path / "o"))
    assert "Soon &amp; more" in html


# --- the live state: two identical facades -------------------------------------


def facades(tree: Tree) -> list[dict[str, Any]]:
    return with_class(tree, "live-card")


def test_two_identical_cards_in_one_grid_in_config_order(out: Path) -> None:
    tree = parse(page(out))
    cards = facades(tree)
    assert len(cards) == 2
    assert len({c["attrs"]["class"] for c in cards}) == 1
    grids = with_class(tree, "live-grid")
    assert len(grids) == 1
    buttons = with_class(tree, "live-facade")
    assert [
        (b["attrs"]["data-platform"], b["attrs"]["data-channel"]) for b in buttons
    ] == [
        ("kick", "example_kick"),
        ("twitch", "example_twitch"),
    ]
    assert all(b["tag"] == "button" and b["attrs"]["type"] == "button" for b in buttons)
    assert len({b["attrs"]["class"] for b in buttons}) == 1


def test_card_order_is_the_config_order_not_an_alphabetical_one(tmp_path: Path) -> None:
    block = live_block(
        players=[
            {"platform": "twitch", "channel": "example_twitch"},
            {"platform": "kick", "channel": "example_kick"},
        ]
    )
    tree = parse(page(build(site(block), tmp_path / "o")))
    assert [b["attrs"]["data-platform"] for b in with_class(tree, "live-facade")] == [
        "twitch",
        "kick",
    ]


def test_each_card_has_the_privacy_note_and_equal_structure(out: Path) -> None:
    tree = parse(page(out))
    notes = [n["text"].strip() for n in with_class(tree, "live-privacy")]
    assert notes == [
        "Loads the player from Kick. Kick will see your IP address.",
        "Loads the player from Twitch. Twitch will see your IP address.",
    ]
    html = page(out)
    # The two cards differ only in the platform words and the channel: replace
    # them and the markup must be byte-identical (same fields, same classes).
    cards = re.findall(r'<li class="live-card">.*?</li>', html, flags=re.S)
    assert len(cards) == 2

    def normal(card: str, platform: str, channel: str) -> str:
        card = card.replace(channel, "CHANNEL")
        card = card.replace(platform.capitalize(), "PLATFORM")
        return card.replace(platform, "platform")

    assert normal(cards[0], "kick", "example_kick") == normal(
        cards[1], "twitch", "example_twitch"
    )


def test_no_titles_and_no_viewer_counts_anywhere(out: Path) -> None:
    html = page(out).lower()
    for word in ("viewer", "watching now", '<h1 class="live-title'):
        assert word not in html


def test_the_live_panel_is_hidden_and_not_an_aria_live_region(out: Path) -> None:
    html = page(out)
    assert "aria-live" not in html
    assert 'role="status"' not in html
    assert 'role="alert"' not in html


# --- the nav badge -------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["index.html", "watch/index.html", "privacy/index.html", "404.html"]
)
def test_nav_has_a_hidden_text_badge_in_the_live_tab_link_only(
    out: Path, name: str
) -> None:
    tree = parse(page(out, name))
    badges = [e for e in tree.elements if "data-live-badge" in e["attrs"]]
    assert len(badges) == 1
    badge = badges[0]
    assert "hidden" in badge["attrs"]
    assert badge["text"].strip() == "LIVE"
    assert "aria-live" not in badge["attrs"]
    assert "a" in badge["parents"] and "nav" in badge["parents"]
    nav_links = [e for e in tree.elements if e["tag"] == "a" and "nav" in e["parents"]]
    inside = [e for e in nav_links if "LIVE" in e["text"]]
    assert len(inside) == 1
    assert inside[0]["text"].strip().startswith("Live")


def test_other_tabs_have_no_badge(out: Path) -> None:
    tree = parse(page(out))
    links = [e for e in tree.elements if e["tag"] == "a" and "nav" in e["parents"]]
    main = [e for e in links if e["text"].strip() == "Main"]
    assert len(main) == 1


# --- no third-party URL before a click -----------------------------------------

_URL_ATTRS = ("src", "srcset", "poster", "data", "action", "content")


def test_no_third_party_resource_or_frame_before_any_click(out: Path) -> None:
    html = page(out)
    tree = parse(html)
    assert not [e for e in tree.elements if e["tag"] in {"iframe", "embed", "object"}]
    assert not [e for e in tree.elements if e["tag"] == "img"]
    for el in tree.elements:
        for name in _URL_ATTRS:
            value = el["attrs"].get(name) or ""
            if name == "content":
                continue
            assert not re.search(r"(?:https?:)?//", value), (el["tag"], name, value)
    for origin in (TWITCH_ORIGIN, KICK_ORIGIN, "player.twitch.tv", "player.kick.com"):
        assert origin not in html
    # the only absolute URLs are plain anchors the visitor must click
    for el in tree.elements:
        for name, value in el["attrs"].items():
            if value and re.match(r"https?://", value):
                assert el["tag"] == "a" and name == "href", (el["tag"], name)


def test_facade_data_attributes_carry_no_url(out: Path) -> None:
    tree = parse(page(out))
    for b in with_class(tree, "live-facade"):
        data = {k: v for k, v in b["attrs"].items() if k.startswith("data-")}
        assert set(data) == {"data-platform", "data-channel"}
        assert all("/" not in v and ":" not in v for v in data.values())


def test_live_css_has_no_remote_reference(out: Path) -> None:
    css = (out / asset(out, "static/live.css")).read_text(encoding="utf-8")
    assert "http" not in css
    assert "//" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert "@import" not in css
    for match in re.finditer(r"url\(([^)]*)\)", css):
        assert not re.match(r"""["']?(?:[a-z]+:|//)""", match.group(1))


def test_no_script_is_added_by_the_live_block(out: Path) -> None:
    scripts = re.findall(r'<script src="([^"]*)"', page(out))
    assert [Path(s).name.split(".")[0] for s in scripts] == ["click"]
    assert "<script>" not in page(out)


# --- the content-security-policy ----------------------------------------------


def header_blocks(out: Path) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in (out / "_headers").read_text(encoding="utf-8").splitlines():
        if not line:
            current = None
        elif not line.startswith(" "):
            current = blocks.setdefault(line, {})
        elif current is not None and ":" in line:
            name, _, value = line.strip().partition(":")
            if not name.startswith("!"):
                current[name] = value.strip()
    return blocks


def test_player_origins_are_exactly_the_two_player_hosts() -> None:
    assert LIVE_PLAYER_ORIGINS == {"kick": KICK_ORIGIN, "twitch": TWITCH_ORIGIN}


def test_the_live_path_has_exactly_the_two_frame_origins(out: Path) -> None:
    blocks = header_blocks(out)
    csp = blocks["/watch/"]["Content-Security-Policy"]
    assert csp == build_csp({"frame-src": [KICK_ORIGIN, TWITCH_ORIGIN]})
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["frame-src"] == f"{KICK_ORIGIN} {TWITCH_ORIGIN}"
    assert directives["connect-src"] == "'self'"
    assert directives["script-src"] == "'self'"
    assert directives["default-src"] == "'self'"


def test_no_other_path_has_a_frame_src(out: Path) -> None:
    blocks = header_blocks(out)
    with_frames = [
        path
        for path, headers in blocks.items()
        if "frame-src" in headers.get("Content-Security-Policy", "")
    ]
    assert with_frames == ["/watch/"]
    assert "/*" in blocks
    assert "frame-src" not in blocks["/*"]["Content-Security-Policy"]


def test_a_site_without_a_live_block_has_no_frame_src(tmp_path: Path) -> None:
    plain = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "main",
                    "title": "Main",
                    "blocks": [{"id": "o", "title": "O", "url": "https://e.com/"}],
                }
            ],
        }
    )
    out = build(plain, tmp_path / "o")
    assert "frame-src" not in (out / "_headers").read_text(encoding="utf-8")


def test_a_live_first_tab_gets_the_extra_on_the_root_path_only(
    tmp_path: Path,
) -> None:
    out = build(site(live_first=True), tmp_path / "o")
    blocks = header_blocks(out)
    with_frames = [
        p
        for p, h in blocks.items()
        if "frame-src" in h.get("Content-Security-Policy", "")
    ]
    assert with_frames == ["/"]


def test_the_extra_is_refused_for_a_wildcard_origin() -> None:
    from reach_around.site.overrides import OverridesError

    with pytest.raises(OverridesError):
        build_csp({"frame-src": ["https://*.twitch.tv"]})


# --- the golden page -----------------------------------------------------------


def test_golden_live_page(out: Path, update_golden: bool) -> None:
    path = GOLDEN / "live.html"
    actual = page(out)
    if update_golden:
        path.write_text(actual, encoding="utf-8")
    assert path.is_file(), "no golden file; run pytest --update-golden"
    assert actual == path.read_text(encoding="utf-8")


def test_golden_comparison_bites(out: Path, tmp_path: Path) -> None:
    golden = tmp_path / "live.html"
    golden.write_text(page(out).replace("Watch live on Kick", "x"), encoding="utf-8")
    assert page(out) != golden.read_text(encoding="utf-8")


# --- the badge colors ----------------------------------------------------------


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def test_the_badge_uses_the_tab_bar_pair_inverted_and_it_is_readable(out: Path) -> None:
    css = (out / asset(out, "static/site.css")).read_text(encoding="utf-8")
    rule = re.search(r"\.live-badge \{([^}]*)\}", css)
    assert rule is not None
    assert "background-color: var(--ra-color-on-accent)" in rule.group(1)
    assert re.search(r"[^-]color: var\(--ra-color-accent\)", rule.group(1))
    accent = re.search(r"--ra-color-accent: (#[0-9a-f]{6})", css)
    on_accent = re.search(r"--ra-color-on-accent: (#[0-9a-f]{6})", css)
    assert accent and on_accent
    hi, lo = sorted((_luminance(accent.group(1)), _luminance(on_accent.group(1))))[::-1]
    assert (hi + 0.05) / (lo + 0.05) >= 4.5
    assert ".live-badge[hidden] { display: none; }" in css
