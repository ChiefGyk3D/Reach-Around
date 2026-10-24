# SPDX-License-Identifier: Apache-2.0
"""Design pass guards: tokens only, accessibility rules, presets and icons."""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path

import pytest
import yaml

from reach_around.config import load_site_config
from reach_around.config.theme import Theme
from reach_around.site.build import build_site
from reach_around.site.icons import ICON_SLUGS

RGB = tuple[float, float, float]

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
THEMES = ROOT / "examples" / "themes"
TODAY = date(2027, 1, 15)
STATIC = resources.files("reach_around.site") / "static"
CSS = (STATIC / "site.css").read_text()
ICON_DIR = Path(str(STATIC / "icons"))

REQUIRED_SLUGS = (
    "twitch kick mastodon bluesky tiktok discord youtube github instagram "
    "threads matrix x linkedin patreon email rss link"
).split()

RAW_HEX = r"#[0-9a-fA-F]{3,8}\b"
COLOR_FUNCTION = r"\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\("


def _root_blocks(css: str) -> list[str]:
    return re.findall(r":root\s*\{.*?\n\}", css, flags=re.S)


def _outside_root(css: str) -> str:
    for block in _root_blocks(css):
        css = css.replace(block, "")
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# --- tokens -----------------------------------------------------------------


def test_site_css_reads_only_ra_tokens_for_colors() -> None:
    assert _root_blocks(CSS), "no :root token block found"
    outside = _outside_root(CSS)
    assert outside.count("{") > 20, "the guard collected too few rules"
    assert not re.findall(RAW_HEX, outside), "raw hex color outside :root"
    assert not re.findall(COLOR_FUNCTION, outside), "raw color function outside :root"


def test_color_guard_bites_on_a_raw_literal() -> None:
    sample = ":root {\n  --ra-color-bg: #fff;\n}\na { color: #abc; }\n"
    assert re.findall(RAW_HEX, _outside_root(sample)) == ["#abc"]
    sample = ":root {\n  --ra-x: 1;\n}\na { color: rgb(1 2 3); }\n"
    assert re.findall(COLOR_FUNCTION, _outside_root(sample)) == ["rgb("]


def test_css_has_focus_visible_and_reduced_motion_rules() -> None:
    assert ":focus-visible" in CSS
    assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)", CSS)
    reduced = CSS[CSS.index("prefers-reduced-motion") :]
    assert "animation" in reduced and "transition" in reduced
    assert "@import" not in CSS and "http://" not in CSS and "https://" not in CSS


def test_css_tokens_are_declared_used_and_documented() -> None:
    root = "".join(_root_blocks(CSS))
    declared = set(re.findall(r"(--ra-[a-z0-9-]+):", root))
    doc = (ROOT / "docs" / "design.md").read_text()
    documented = set(re.findall(r"`(--ra-[a-z0-9-]+)`", doc))
    assert declared, "no tokens declared"
    assert declared <= documented, sorted(declared - documented)
    used = set(re.findall(r"var\((--ra-[a-z0-9-]+)", CSS))
    assert used <= declared, sorted(used - declared)


# --- contrast ---------------------------------------------------------------


def _hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (r, g, b)


def _luminance(rgb: RGB) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _mix(fg: RGB, bg: RGB, share: float) -> RGB:
    return (
        fg[0] * share + bg[0] * (1 - share),
        fg[1] * share + bg[1] * (1 - share),
        fg[2] * share + bg[2] * (1 - share),
    )


def _token_sets() -> dict[str, dict[str, RGB]]:
    root = "".join(_root_blocks(CSS))
    sets: dict[str, dict[str, RGB]] = {"light": {}, "dark": {}}
    pairs = re.findall(
        r"--ra-color-([a-z]+):\s*light-dark\((#[0-9a-fA-F]{6}),\s*(#[0-9a-fA-F]{6})\)",
        root,
    )
    for name, light, dark in pairs:
        sets["light"][name] = _hex_to_rgb(light)
        sets["dark"][name] = _hex_to_rgb(dark)
    # Brand colors are one value for both modes: --ra-color-name: #rrggbb;
    for name, value in re.findall(r"--ra-color-([a-z-]+):\s*(#[0-9a-fA-F]{6});", root):
        for mode in sets:
            sets[mode][name] = _hex_to_rgb(value)
    return sets


def test_contrast_helper_matches_known_values() -> None:
    black, white = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    assert contrast(black, white) == pytest.approx(21.0)
    assert contrast(white, white) == pytest.approx(1.0)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_contrast_of_default_token_pairs_meets_aa(mode: str) -> None:
    tokens = _token_sets()[mode]
    needed = {"bg", "surface", "text", "accent", "on-accent", "border"}
    assert needed <= set(tokens), tokens
    bg, surface = tokens["bg"], tokens["surface"]
    text, accent, on_accent = tokens["text"], tokens["accent"], tokens["on-accent"]
    muted = _mix(text, bg, 0.72)  # --ra-color-muted: 72% text over bg
    # --ra-color-link: the accent in dark mode, 45% accent over black in light.
    link = accent if mode == "dark" else _mix(accent, (0.0, 0.0, 0.0), 0.45)
    for ground in (bg, surface):
        assert contrast(text, ground) >= 4.5, "text"
        assert contrast(muted, ground) >= 4.5, "muted text"
        assert contrast(link, ground) >= 4.5, "link text"
    # Text on the accent fill (tab bar, link buttons) is the on-accent color.
    assert contrast(on_accent, accent) >= 4.5, "text on the accent fill"
    subtitle = _mix(on_accent, accent, 0.85)  # --ra-color-on-accent-muted
    assert contrast(subtitle, accent) >= 4.5, "subtitle on the accent fill"
    # UI components: the focus ring against the page (large text).
    assert contrast(link, bg) >= 3, "focus ring"
    assert contrast(tokens["border"], bg) >= 1.2, "border is at least visible"


def test_contrast_guard_bites_on_a_bad_pair() -> None:
    assert contrast((0.5, 0.5, 0.5), (0.55, 0.55, 0.55)) < 3


# --- presets ----------------------------------------------------------------


def _preset_config(preset: Path, tmp_path: Path) -> Path:
    raw = yaml.safe_load(EXAMPLE.read_text())
    raw["theme"] = yaml.safe_load(preset.read_text())
    target = tmp_path / "site.yaml"
    target.write_text(yaml.safe_dump(raw))
    return target


@pytest.mark.parametrize("name", ["dark-hero", "light-clean"])
def test_both_presets_validate_and_build(name: str, tmp_path: Path) -> None:
    preset = THEMES / f"{name}.yaml"
    assert preset.is_file()
    theme = Theme.model_validate(yaml.safe_load(preset.read_text()))
    assert theme.colors, "a preset must set colors"
    config = load_site_config(_preset_config(preset, tmp_path))
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    css = (out / "static" / "theme.css").read_text()
    assert "--ra-color-bg" in css and "--ra-color-accent" in css
    assert (out / "index.html").is_file()


@pytest.mark.parametrize("name", ["dark-hero", "light-clean"])
def test_presets_meet_text_contrast(name: str) -> None:
    theme = Theme.model_validate(yaml.safe_load((THEMES / f"{name}.yaml").read_text()))
    colors = {key: _hex_to_rgb(value) for key, value in theme.colors.items()}
    for ground in ("bg", "surface"):
        assert contrast(colors["text"], colors[ground]) >= 4.5, ground
    assert contrast(colors["accent"], colors["bg"]) >= 4.5
    assert "on_accent" in colors, "a preset must set the text color on its accent"
    assert contrast(colors["on_accent"], colors["accent"]) >= 4.5


# --- icons ------------------------------------------------------------------


def test_icon_set_is_complete() -> None:
    assert set(REQUIRED_SLUGS) <= set(ICON_SLUGS)
    files = {p.stem for p in ICON_DIR.glob("*.svg")}
    assert files == set(ICON_SLUGS)


def test_every_referenced_icon_file_exists() -> None:
    urls = re.findall(r'url\("(icons/[^"]+)"\)', CSS)
    assert len(urls) >= len(ICON_SLUGS)
    for rel in urls:
        assert (ICON_DIR.parent / rel).is_file(), rel
    for slug in ICON_SLUGS:
        assert f"icon-{slug}" in CSS, slug


def _hostile(text: str) -> list[str]:
    lowered = text.lower()
    found = [
        needle
        for needle in ("<script", "onload=", "onclick=", "<image", "<style")
        if needle in lowered
    ]
    if "<foreignobject" in lowered:
        found.append("<foreignObject")
    if re.search(r"href\s*=\s*[\"'](?:https?:)?//", lowered):
        found.append("remote href")
    return found


def test_icon_files_carry_nothing_active() -> None:
    files = sorted(ICON_DIR.glob("*.svg"))
    assert len(files) >= len(REQUIRED_SLUGS)
    for path in files:
        assert _hostile(path.read_text()) == [], path.name


def test_icon_guard_bites_on_a_hostile_svg() -> None:
    bad = (
        '<svg onload="x()"><script>1</script><image href="https://e.test/x.png"/></svg>'
    )
    assert _hostile(bad) == ["<script", "onload=", "<image", "remote href"]


def test_icon_notice_names_source_and_license() -> None:
    notice = (ICON_DIR / "NOTICE").read_text()
    assert "Simple Icons" in notice and "CC0" in notice
    assert "trademarks" in notice


def _build_example(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    build_site(load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent)
    return out


def test_built_example_references_only_existing_icons(tmp_path: Path) -> None:
    out = _build_example(tmp_path)
    used: set[str] = set()
    for page in out.rglob("*.html"):
        used |= set(re.findall(r"\bicon-([a-z0-9]+)\b", page.read_text()))
    assert used, "the example config should show at least one icon"
    for slug in used:
        assert (out / "static" / "icons" / f"{slug}.svg").is_file(), slug


def test_unknown_icon_slug_falls_back_to_the_text_label(tmp_path: Path) -> None:
    out = _build_example(tmp_path)
    html = (out / "index.html").read_text()
    assert "icon-chat" not in html
    assert "Example Chat" in html
