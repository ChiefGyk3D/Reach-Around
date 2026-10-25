# SPDX-License-Identifier: Apache-2.0
"""The Beacons-style baseline: structure, image slots, same-origin assets, tokens."""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from assetpaths import asset, asset_file, read
from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import build_site
from reach_around.site.theme import theme_css

ROOT = Path(__file__).resolve().parents[2]
INSTANCE = ROOT / "examples" / "instance"
EXAMPLE = INSTANCE / "site.yaml"
TODAY = date(2027, 3, 10)
STATIC = resources.files("reach_around.site") / "static"
CSS = (STATIC / "site.css").read_text()


def build_example(out: Path) -> Path:
    config = load_site_config(EXAMPLE)
    build_site(config, out, today=TODAY, protect=[EXAMPLE], assets_dir=INSTANCE)
    return out


def config_with(**creator_and_theme: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator", **creator_and_theme.pop("creator", {})},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/1"}
                ],
            }
        ],
    }
    if creator_and_theme:
        data["theme"] = creator_and_theme
    return SiteConfig.model_validate(data)


def root_block() -> str:
    match = re.search(r":root\s*\{.*?\n\}", CSS, flags=re.S)
    assert match, "no :root block"
    return match.group(0)


def token(name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*([^;]+);", root_block())
    assert match, f"{name} is not declared in :root"
    return match.group(1).strip()


def luminance(hex_color: str) -> float:
    digits = hex_color.lstrip("#")
    channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- image slots are config, never engine defaults ---------------------------


def test_engine_defaults_bake_in_no_image() -> None:
    assert token("--ra-mascot-image") == "none"
    assert token("--ra-bg-image") == "none"
    outside = CSS.replace(root_block(), "")
    urls = re.findall(r"url\(([^)]*)\)", outside)
    assert urls, "the guard collected no url() at all"
    for target in urls:
        cleaned = target.strip("\"' ")
        assert cleaned.startswith(("icons/", "fonts/")), cleaned


def test_the_example_instance_fills_every_slot_with_its_placeholder(
    tmp_path: Path,
) -> None:
    out = build_example(tmp_path / "out")
    mascot = asset(out, "media/mascot.svg")
    assert (out / mascot).is_file()
    html = (out / "index.html").read_text()
    assert f'<img class="avatar" src="{mascot}"' in html
    assert f'<link rel="icon" href="{mascot}"' in html
    theme = read(out, "static/theme.css")
    assert f'--ra-mascot-image: url("../{mascot}");' in theme


def test_nested_page_uses_a_root_relative_path_for_slots(tmp_path: Path) -> None:
    out = build_example(tmp_path / "out")
    html = (out / "events" / "index.html").read_text()
    mascot = asset(out, "media/mascot.svg")
    assert f'src="../{mascot}"' in html
    assert f'href="../{mascot}"' in html


def test_the_placeholder_is_a_synthetic_svg_with_no_script() -> None:
    svg = (INSTANCE / "media" / "mascot.svg").read_text()
    assert "<svg" in svg and "synthetic" in svg.lower()
    for bad in ("<script", "onload", "href=", "<image", "<foreignObject"):
        assert bad not in svg, bad


def test_unreferenced_instance_files_are_not_published(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    work.mkdir()
    (work / "secret.txt").write_text("private notes")
    (work / "a.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    config = config_with(creator={"avatar": "a.svg"})
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=work)
    assert asset_file(out, "a.svg").is_file()
    assert not (out / "secret.txt").exists()


def test_a_missing_asset_stops_the_build_with_its_path(tmp_path: Path) -> None:
    config = config_with(creator={"avatar": "media/nope.png"})
    with pytest.raises(ValueError, match=r"media/nope\.png"):
        build_site(config, tmp_path / "out", today=TODAY, assets_dir=tmp_path)


def test_local_assets_without_an_assets_dir_stop_the_build(tmp_path: Path) -> None:
    config = config_with(creator={"avatar": "media/a.png"})
    with pytest.raises(ValueError, match="assets_dir"):
        build_site(config, tmp_path / "out", today=TODAY)


def test_an_asset_symlink_is_refused(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    work.mkdir()
    outside = tmp_path / "outside.svg"
    outside.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (work / "a.svg").symlink_to(outside)
    config = config_with(creator={"avatar": "a.svg"})
    with pytest.raises(ValueError, match="symlink"):
        build_site(config, tmp_path / "out", today=TODAY, assets_dir=work)


def test_an_asset_cannot_overwrite_a_packaged_file(tmp_path: Path) -> None:
    # Only images are accepted, and the folders the build owns are reserved.
    with pytest.raises(ValidationError, match="static"):
        config_with(creator={"avatar": "static/site.css.png"})


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/m.png",
        "http://example.com/m.png",
        "//example.com/m.png",
        "data:image/png;base64,AAAA",
        "../m.png",
        "/etc/m.png",
        "media/../m.png",
        "media/m.exe",
        "static/m.png",
        "privacy/m.png",
        "media\\m.png",
    ],
)
def test_mascot_and_favicon_refuse_anything_not_a_local_image_path(
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        config_with(mascot=value)
    with pytest.raises(ValidationError):
        config_with(creator={"favicon": value})


def test_avatar_takes_a_local_path_or_an_https_url() -> None:
    assert config_with(creator={"avatar": "media/me.png"}).creator.avatar
    assert config_with(creator={"avatar": "https://example.com/a.png"}).creator.avatar
    for bad in ("http://example.com/a.png", "../a.png", "data:image/png;base64,AA"):
        with pytest.raises(ValidationError):
            config_with(creator={"avatar": bad})


# --- strict CSP stays on ------------------------------------------------------


def _all_text(out: Path) -> dict[Path, str]:
    return {
        p: p.read_text()
        for p in out.rglob("*")
        if p.is_file() and p.suffix in {".html", ".css", ".js", ".svg"}
    }


def test_built_example_has_no_inline_style_or_script(tmp_path: Path) -> None:
    out = build_example(tmp_path / "out")
    pages = list(out.rglob("*.html"))
    assert len(pages) >= 3
    for page in pages:
        html = page.read_text()
        assert not re.search(r"\sstyle\s*=", html, flags=re.I), page
        assert not re.search(r"<style\b", html, flags=re.I), page
        assert not re.search(r"\son[a-z]+\s*=", html, flags=re.I), page
        assert not re.search(r"<script\b(?![^>]*\bsrc=)", html, flags=re.I), page


def test_every_resource_the_page_loads_is_same_origin(tmp_path: Path) -> None:
    out = build_example(tmp_path / "out")
    checked = 0
    for page in out.rglob("*.html"):
        html = page.read_text()
        resources_ = re.findall(r"<(?:img|script|link|source)\b[^>]*>", html)
        for tag in resources_:
            for ref in re.findall(r'\b(?:src|href|srcset)="([^"]*)"', tag):
                checked += 1
                assert not re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I), (
                    page,
                    tag,
                )
    assert checked >= 6, "the guard collected too few resource references"
    for css in out.rglob("*.css"):
        text = css.read_text()
        for target in re.findall(r"url\(([^)]*)\)", text):
            assert not re.match(r"[\"']?(?:[a-z][a-z0-9+.-]*:|//)", target, re.I), css
        assert "@import" not in text


def test_resource_guard_bites_on_a_remote_font_and_image() -> None:
    sample = '<link rel="stylesheet" href="https://fonts.example/jost.css">'
    tag = re.findall(r"<(?:img|script|link|source)\b[^>]*>", sample)[0]
    ref = re.findall(r'\bhref="([^"]*)"', tag)[0]
    assert re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I)


# --- Jost, vendored ------------------------------------------------------------


def test_jost_is_vendored_with_its_license_and_served_same_origin(
    tmp_path: Path,
) -> None:
    fonts = Path(str(STATIC / "fonts"))
    woff = fonts / "jost-latin-wght-normal.woff2"
    assert woff.read_bytes()[:4] == b"wOF2"
    ofl = (fonts / "OFL.txt").read_text()
    assert "SIL OPEN FONT LICENSE Version 1.1" in ofl and "Jost" in ofl
    assert 'font-family: "Jost"' in CSS
    face = re.search(r"@font-face\s*\{[^}]*\}", CSS)
    assert face and 'url("fonts/jost-latin-wght-normal.woff2")' in face.group(0)
    assert token("--ra-font-body").startswith('"Jost"')
    out = build_example(tmp_path / "out")
    assert asset_file(out, "static/fonts/jost-latin-wght-normal.woff2").is_file()
    assert asset_file(out, "static/fonts/OFL.txt").is_file()


def test_a_system_font_stack_can_still_replace_jost(tmp_path: Path) -> None:
    config = config_with(fonts={"body": "system-ui, sans-serif"})
    out = tmp_path / "out"
    build_site(config, out, today=TODAY)
    assert "--ra-font-body: system-ui, sans-serif;" in read(out, "static/theme.css")


# --- brand tokens and contrast -------------------------------------------------


def test_brand_colors_are_tokens_with_the_brand_values() -> None:
    assert "#fbb03b" in token("--ra-color-accent").lower()
    assert "#0e0e0e" in token("--ra-color-bg").lower()
    assert "#ffffff" in token("--ra-color-text").lower()
    assert token("--ra-color-on-accent").lower() == "#1a1a1a"
    assert token("--ra-button-radius") == "2.25rem"
    assert token("--ra-column-width") == "35rem"


def test_on_accent_is_a_theme_color_key() -> None:
    theme = config_with(colors={"on_accent": "#111111"}).theme
    assert theme.colors["on_accent"] == "#111111"
    assert "--ra-color-on-accent: #111111;" in theme_css(theme)


def test_default_palette_meets_wcag_aa() -> None:
    accent, dark = "#FBB03B", "#1A1A1A"
    assert contrast(accent, dark) >= 4.5
    assert contrast("#FFFFFF", "#0E0E0E") >= 4.5
    # The subtitle is the on-accent color mixed 85% into the accent.
    assert "85%" in token("--ra-color-on-accent-muted")
    mixed = tuple(
        round(0.85 * d + 0.15 * a)
        for d, a in zip((0x1A,) * 3, (0xFB, 0xB0, 0x3B), strict=True)
    )
    mixed_hex = "#{:02x}{:02x}{:02x}".format(*mixed)
    assert contrast(accent, mixed_hex) >= 4.5


def test_contrast_helper_bites() -> None:
    assert contrast("#FBB03B", "#FFFFFF") < 4.5


# --- structure -----------------------------------------------------------------


def test_the_tab_bar_comes_first_and_labels_are_uppercase(tmp_path: Path) -> None:
    out = build_example(tmp_path / "out")
    html = (out / "index.html").read_text()
    assert html.index('class="tabs"') < html.index("site-header")
    assert re.search(r"\.tabs a\s*\{[^}]*text-transform:\s*uppercase", CSS)
    assert re.search(
        r"\.tabs\b[^{]*\{[^}]*background-color:\s*var\(--ra-color-accent\)", CSS
    )


def test_compact_header_has_avatar_then_name_then_socials(tmp_path: Path) -> None:
    out = build_example(tmp_path / "out")
    html = (out / "index.html").read_text()
    header = html[html.index("<header") : html.index("</header>")]
    assert "header-compact" in header
    assert header.index('class="avatar"') < header.index("<h1>")
    assert header.index("<h1>") < header.index('class="socials"')
    assert 'class="header-text"' in header


def test_no_subscribe_or_share_control_is_invented(tmp_path: Path) -> None:
    out = build_example(tmp_path / "out")
    html = (out / "index.html").read_text().lower()
    assert "subscribe" not in html and "share" not in html


def test_featured_link_gets_its_own_pill_class_and_others_do_not(
    tmp_path: Path,
) -> None:
    out = build_example(tmp_path / "out")
    html = (out / "index.html").read_text()
    featured = re.findall(r'<a class="link link-featured"', html)
    plain = re.findall(r'<a class="link"', html)
    assert len(featured) == 1 and len(plain) >= 2
    raw = yaml.safe_load(EXAMPLE.read_text())
    assert raw["tabs"][0]["blocks"][0]["featured"] is True


def test_link_pill_has_dots_thumbnail_slot_and_dark_text() -> None:
    assert re.search(r"a\.link::after\s*\{", CSS), "the three-dot glyph is missing"
    assert re.search(r"a\.link\s*\{[^}]*grid-template-columns", CSS)
    link_rule = re.search(r"\na\.link\s*\{[^}]*\}", CSS)
    assert link_rule
    assert "var(--ra-color-accent)" in link_rule.group(0)
    assert "var(--ra-color-on-accent)" in link_rule.group(0)
    assert "var(--ra-button-radius)" in link_rule.group(0)


def test_the_background_is_fixed_and_the_mascot_is_a_fixed_layer() -> None:
    assert re.search(r"body\s*\{[^}]*background-attachment:\s*fixed", CSS)
    mascot = re.search(r"body::before\s*\{[^}]*\}", CSS)
    assert mascot and "position: fixed" in mascot.group(0)
    assert "var(--ra-mascot-image)" in mascot.group(0)
    assert "var(--ra-mascot-opacity)" in mascot.group(0)
