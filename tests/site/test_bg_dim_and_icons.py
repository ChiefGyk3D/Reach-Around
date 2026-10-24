# SPDX-License-Identifier: Apache-2.0
"""``theme.background_dim`` and the shipped icon set."""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.theme import Theme
from reach_around.site.assetcheck import check_content
from reach_around.site.build import build_site
from reach_around.site.icons import ICON_SLUGS
from reach_around.site.theme import theme_css

ROOT = Path(__file__).resolve().parents[2]
MEDIA_ICONS = ROOT / "media" / "icons"
STATIC = resources.files("reach_around.site") / "static"
ICON_DIR = Path(str(STATIC / "icons"))
CSS = (STATIC / "site.css").read_text()
TODAY = date(2027, 3, 10)


def config(**theme: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {
            "name": "C",
            "socials": [
                {"name": "Shop", "url": "https://shop.example.com/", "icon": "merch"}
            ],
        },
        "tabs": [
            {
                "id": "a",
                "title": "A",
                "blocks": [
                    {
                        "id": "one",
                        "title": "One",
                        "url": "https://x.example/",
                        "icon": "bitcoin",
                    }
                ],
            }
        ],
    }
    if theme:
        data["theme"] = theme
    return SiteConfig.model_validate(data)


# --- background_dim -----------------------------------------------------------


def test_the_default_is_zero_and_adds_nothing_to_theme_css() -> None:
    assert Theme().background_dim == 0.0
    assert theme_css(Theme()) == ":root {\n}\n"
    assert theme_css(Theme(background_dim=0.0)) == ":root {\n}\n"


@pytest.mark.parametrize(
    ("value", "text"), [(0.6, "0.6"), (0.9, "0.9"), (0.05, "0.05")]
)
def test_a_value_is_written_as_the_dim_token(value: float, text: str) -> None:
    css = theme_css(Theme(background_dim=value))
    assert f"--ra-bg-dim: {text};" in css


@pytest.mark.parametrize("value", [0.0, 0.5, 0.9, 0, "0.25"])
def test_values_in_range_are_accepted(value: Any) -> None:
    assert config(background_dim=value).theme.background_dim == float(value)


@pytest.mark.parametrize(
    "value", [-0.01, 0.91, 1.0, 5, "abc", None, float("nan"), float("inf"), [0.5]]
)
def test_values_out_of_range_or_not_numbers_are_refused(value: Any) -> None:
    with pytest.raises(ValidationError):
        config(background_dim=value)


def test_theme_css_rechecks_a_value_set_past_validation() -> None:
    forged = Theme.model_construct(background_dim=3.0)
    with pytest.raises(ValueError, match="background_dim"):
        theme_css(forged)


def test_the_token_is_declared_and_the_overlay_sits_above_the_image() -> None:
    root = re.search(r":root\s*\{.*?\n\}", CSS, flags=re.S)
    assert root
    assert re.search(r"--ra-bg-dim:\s*0;", root.group(0))
    body = re.search(r"\nbody\s*\{[^}]*\}", CSS)
    assert body
    layers = re.search(r"background-image:\s*(.*?);", body.group(0), flags=re.S)
    assert layers
    first, second = layers.group(1).split("var(--ra-bg-image)")[0], "var(--ra-bg-image)"
    assert "var(--ra-color-dim)" in first, "the dim layer must come before the image"
    assert second in layers.group(1)
    # Per-layer lists stay in step with the five layers.
    for prop in ("background-size", "background-position", "background-repeat"):
        value = re.search(rf"{prop}:\s*(.*?);", body.group(0), flags=re.S)
        assert value and value.group(1).count(",") == 4, prop


def test_the_mascot_layer_does_not_read_the_dim_token() -> None:
    mascot = re.search(r"body::before\s*\{[^}]*\}", CSS)
    assert mascot
    assert "dim" not in mascot.group(0)


def test_dimming_adds_no_inline_style_and_changes_only_theme_css(
    tmp_path: Path,
) -> None:
    plain, dim = tmp_path / "plain", tmp_path / "dim"
    build_site(config(), plain, today=TODAY)
    build_site(config(background_dim=0.6), dim, today=TODAY)
    for page in ("index.html", "privacy/index.html"):
        assert (plain / page).read_bytes() == (dim / page).read_bytes()
        assert b"style=" not in (dim / page).read_bytes()
    assert (plain / "static" / "theme.css").read_text() == ":root {\n}\n"
    assert "--ra-bg-dim: 0.6;" in (dim / "static" / "theme.css").read_text()


# --- the icon set -------------------------------------------------------------


def test_every_file_in_media_icons_has_a_slug() -> None:
    files = sorted(MEDIA_ICONS.glob("*.svg"))
    assert len(files) >= 16, "the guard collected too few icons"
    assert {p.stem for p in files} <= ICON_SLUGS


def test_every_slug_has_a_shipped_file_and_a_css_rule() -> None:
    for slug in ICON_SLUGS:
        assert (ICON_DIR / f"{slug}.svg").is_file(), slug
        assert re.search(rf"\.icon-{re.escape(slug)}\s*\{{", CSS), slug


def test_no_shipped_icon_file_is_missing_from_the_slug_set() -> None:
    assert {p.stem for p in ICON_DIR.glob("*.svg")} == set(ICON_SLUGS)


@pytest.mark.parametrize("slug", ["bitcoin", "ethereum", "merch", "monero", "solana"])
def test_the_added_icons_are_wired(slug: str) -> None:
    assert slug in ICON_SLUGS
    assert (ICON_DIR / f"{slug}.svg").is_file()


def test_the_drift_guards_bite() -> None:
    assert {"nonexistent-slug"} - ICON_SLUGS
    slugs = set(ICON_SLUGS) | {"ghost"}
    assert not (ICON_DIR / "ghost.svg").is_file() and "ghost" in slugs


def test_every_shipped_icon_passes_the_svg_screen() -> None:
    for path in sorted(ICON_DIR.glob("*.svg")):
        check_content("icons", path.name, path.read_bytes())


def test_the_screen_rejects_the_unstripped_source_icons_only_for_role() -> None:
    # media/icons keeps role="img" and a <title>; the shipped copies drop role.
    with pytest.raises(ValueError, match="role"):
        check_content("k", "merch.svg", (MEDIA_ICONS / "merch.svg").read_bytes())


def test_a_link_with_a_new_icon_slug_renders_the_icon(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(config(), out, today=TODAY)
    html = (out / "index.html").read_text()
    assert "icon-merch" in html and "icon-bitcoin" in html
    assert (out / "static" / "icons" / "merch.svg").is_file()
    assert (out / "static" / "icons" / "bitcoin.svg").is_file()
