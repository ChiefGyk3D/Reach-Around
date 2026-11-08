# SPDX-License-Identifier: Apache-2.0
"""static/live.js: how the build ships it, and what its source may contain.

The behavior is covered by live_logic.mjs (pure logic) and test_live_client_visual.py
(a real browser). This file holds the wiring and the source guards. They collect
loosely (every page of the build) and assert strictly (at least one was seen).
"""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path

import pytest

from assetpaths import asset, asset_file
from reach_around.config import SiteConfig
from reach_around.config.blocks import LIVE_PLAYER_ORIGINS
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2027, 3, 10)

LIVE_BLOCK = {
    "id": "live",
    "players": [
        {"platform": "kick", "channel": "example_kick"},
        {"platform": "twitch", "channel": "example_twitch"},
    ],
}
TABLE = re.compile(r"// BEGIN PLATFORM TABLE\n(.*?)// END PLATFORM TABLE\n", re.DOTALL)


def source() -> str:
    return (resources.files("reach_around.site") / "static" / "live.js").read_text(
        encoding="utf-8"
    )


def config(*, live: bool) -> SiteConfig:
    tabs: list[dict[str, object]] = [
        {
            "id": "main",
            "title": "Main",
            "blocks": [{"id": "one", "title": "One", "url": "https://e.com/"}],
        }
    ]
    if live:
        tabs.append({"id": "watch", "title": "Live", "blocks": [LIVE_BLOCK]})
    raw: dict[str, object] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": tabs,
    }
    return SiteConfig.model_validate(raw)


def scripts(html: str) -> list[str]:
    return [
        Path(m).name.split(".")[0] for m in re.findall(r'<script src="([^"]*)"', html)
    ]


def test_live_js_ships_with_a_live_block_and_is_linked_from_every_tab_page(
    tmp_path: Path,
) -> None:
    out = tmp_path / "o"
    build_site(config(live=True), out, today=TODAY)
    assert asset_file(out, "static/live.js").is_file()
    seen = []
    for name in ("index.html", "watch/index.html", "privacy/index.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert "live" in scripts(html), name
        tag = re.search(r'<script src="([^"]*live\.[0-9a-f]{20}\.js)"([^>]*)>', html)
        assert tag, name
        assert "defer" in tag.group(2)
        assert tag.group(1).endswith(asset(out, "static/live.js"))
        seen.append(name)
    assert len(seen) == 3
    assert not re.search(r"<script>", (out / "watch/index.html").read_text("utf-8"))


def test_live_js_is_absent_without_a_live_block(tmp_path: Path) -> None:
    out = tmp_path / "o"
    build_site(config(live=False), out, today=TODAY)
    assert not list(out.rglob("live.*.js"))
    pages = sorted(out.rglob("*.html"))
    assert pages
    for page in pages:
        assert "live" not in scripts(page.read_text("utf-8"))


def test_no_script_on_the_not_found_page_even_with_live(tmp_path: Path) -> None:
    out = tmp_path / "o"
    build_site(config(live=True), out, today=TODAY)
    assert "<script" not in (out / "404.html").read_text("utf-8").lower()


# --- the source ------------------------------------------------------------------


def test_the_source_is_collected() -> None:
    assert len(source()) > 2000


@pytest.mark.parametrize(
    "banned",
    [
        "eval(",
        "new Function",
        "XMLHttpRequest",
        "sendBeacon",
        "WebSocket",
        "EventSource",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "import(",
        "postMessage",
        "contentWindow",
        "contentDocument",
        "location.href",
        "location.assign",
        "location.replace",
        "window.open",
        "setAttribute('on",
        'setAttribute("on',
        "srcdoc",
        "allow-top-navigation",
        "allow-popups",
    ],
)
def test_the_source_never_uses(banned: str) -> None:
    assert banned not in source()


def test_exactly_one_fetch_and_it_is_the_same_origin_status_route() -> None:
    text = source()
    assert text.count("fetch(") == 1
    assert 'var ROUTE = "/e/live";' in text
    assert "fetch(ROUTE," in text
    urls = re.findall(r"""["'](https?:)?//[^"']*["']""", text)
    assert urls  # the two player origins in the table are the only absolute URLs
    for match in re.finditer(r"""["'](https?://[^"']*)["']""", text):
        origin = re.match(r"https://[^/?]+", match.group(1))
        assert origin
        assert origin.group(0) in LIVE_PLAYER_ORIGINS.values(), match.group(1)


def test_the_player_origins_are_exactly_the_config_models() -> None:
    table = TABLE.search(source())
    assert table
    found = set(re.findall(r"https://[a-z.]+", table.group(1)))
    assert found == set(LIVE_PLAYER_ORIGINS.values())


def test_the_handle_patterns_are_the_config_models() -> None:
    from reach_around.config import blocks

    table = TABLE.search(source())
    assert table
    text = table.group(1)
    for platform, rule in (
        ("twitch", blocks._TWITCH_CHANNEL),
        ("kick", blocks._KICK_CHANNEL),
    ):
        assert f"/^{rule.pattern}$/" in text, platform


def test_equal_treatment_the_platform_names_appear_only_in_the_table() -> None:
    text = source()
    outside = TABLE.sub("", text)
    comment_free = "\n".join(
        line for line in outside.splitlines() if not line.lstrip().startswith("//")
    )
    assert not re.search(r"kick|twitch", comment_free, re.IGNORECASE)
    inside = TABLE.search(text)
    assert inside
    assert re.search(r"kick", inside.group(1), re.IGNORECASE)


def test_the_equal_treatment_guard_bites() -> None:
    bad = source().replace("var ROUTE", 'if (p.id === "kick") { x(); }\nvar ROUTE', 1)
    outside = TABLE.sub("", bad)
    assert re.search(r"kick|twitch", outside, re.IGNORECASE)


def test_the_iframe_is_sandboxed_lazy_titled_and_gets_a_minimal_allow_list() -> None:
    text = source()
    assert (
        'setAttribute("sandbox", "allow-scripts allow-same-origin allow-presentation")'
        in text
    )
    assert 'setAttribute("loading", "lazy")' in text
    # autoplay is delegated so the visitor's one click starts the player; the URL
    # never asks for autoplay itself.
    assert 'setAttribute("allow", "autoplay; fullscreen; picture-in-picture")' in text
    assert 'setAttribute("referrerpolicy"' in text
    assert 'setAttribute("title"' in text
    assert "autoplay=" not in text.lower()
    assert "muted" not in text.lower()


def test_staleness_matches_the_worker_when_the_worker_is_present() -> None:
    worker = ROOT / "worker" / "src" / "live.ts"
    if not worker.is_file():
        pytest.skip("worker/src/live.ts is not on this branch yet (slice 4)")
    ts = worker.read_text("utf-8")
    found = re.search(r"HEARTBEAT_SECONDS = (\d+)", ts)
    assert found
    heartbeat = int(found.group(1))
    assert f"var STALE_AFTER_SECONDS = {3 * heartbeat};" in source()
    assert 'ROUTE = "/e/live"' in ts or '"/e/live"' in ts
