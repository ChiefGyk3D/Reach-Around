# SPDX-License-Identifier: Apache-2.0
"""Kit direction tokens (#685, owner decision 2026-11-24, option A).

The kit adopts the link page's deck tokens from #677: the tile (surface, border,
tile radius) and the small mono kicker. smart-stack.css is the single source;
kit.css reads the same tokens and may not redefine them, so the two cannot drift.
Dark only: no amber or light fill on a tile.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_kit_build import build, gated_doc, make_config

STATIC = (
    Path(__file__).resolve().parents[2] / "src" / "reach_around" / "site" / "static"
)
KIT = (STATIC / "kit.css").read_text(encoding="utf-8")
STACK = (STATIC / "smart-stack.css").read_text(encoding="utf-8")

KIT_TILE = ".kit > section:not(.ra-c-plate)"
KIT_KICKER = ".kit > section > h3"
KIT_KICKER_RULE = ".kit > section > h3::after"


def _strip(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def decls(css: str, selector: str) -> dict[str, str]:
    """Merged declarations of every rule whose selector list has `selector`."""
    out: dict[str, str] = {}
    found = False
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _strip(css)):
        if selector not in [s.strip() for s in match.group(1).split(",")]:
            continue
        found = True
        for item in match.group(2).split(";"):
            if ":" in item:
                name, _, value = item.partition(":")
                out[name.strip()] = " ".join(value.split())
    assert found, f"no rule for {selector!r}"
    return out


def test_kit_tile_is_the_deck_tile() -> None:
    deck = decls(STACK, ".smart-group")
    tile = decls(KIT, KIT_TILE)
    for prop in ("padding", "border", "border-radius", "background-color"):
        assert tile[prop] == deck[prop], prop


def test_kit_kicker_is_the_deck_kicker() -> None:
    deck = decls(STACK, ".smart-group h2")
    kicker = decls(KIT, KIT_KICKER)
    assert kicker == deck
    assert decls(KIT, KIT_KICKER_RULE) == decls(STACK, ".smart-group h2::after")


def test_kit_reads_tokens_and_defines_none() -> None:
    assert not re.search(r"(^|[;{\s])--ra-[a-z0-9-]+\s*:", _strip(KIT))


def test_kit_tile_and_kicker_hold_no_color_of_their_own() -> None:
    for selector in (KIT_TILE, KIT_KICKER, KIT_KICKER_RULE):
        for prop, value in decls(KIT, selector).items():
            assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb|hsl", value), (
                selector,
                prop,
            )


def test_kit_tiles_are_dark_with_no_amber_or_light_fill() -> None:
    fill = decls(KIT, KIT_TILE)["background-color"]
    assert fill == "var(--ra-color-surface)"
    assert "amber" not in fill and "white" not in fill and "cream" not in fill


def test_built_kit_page_links_the_stylesheet_with_the_direction_rules(
    tmp_path: Path,
) -> None:
    out, _ = build(tmp_path, make_config(gated_doc()))
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    href = re.search(r'href="([^"]*kit[^"]*\.css)"', html)
    assert href, "the kit page does not link kit.css"
    css = (out / "kit" / href.group(1)).resolve().read_text(encoding="utf-8")
    assert decls(css, KIT_TILE) and decls(css, KIT_KICKER)
