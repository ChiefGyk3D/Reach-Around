# SPDX-License-Identifier: Apache-2.0
"""The published NPNC token bridge is the only CSS color and font source."""

from __future__ import annotations

import re
from importlib import resources

from reach_around.funnel.htmlout import CSS as REPORT_CSS

STATIC = resources.files("reach_around.site") / "static"
RAW_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\("
)
RAW_FONT = re.compile(
    r"font-family\s*:(?!\s*var\()|font\s*:[^;}]*\b(?:system-ui|sans-serif)\b"
)


def _without_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_token_layer_is_the_only_shipped_color_and_font_source() -> None:
    token_css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    assert "color-scheme: dark" in token_css
    assert "--ra-brand-amber: #FBB03B" in token_css
    assert "--ra-brand-ground: #0F0F0F" in token_css
    assert "--ra-brand-plate: #281808" in token_css
    assert "--ra-brand-carbon: #181818" in token_css
    assert '"Orbitron"' in token_css and '"Jost"' in token_css

    for path in STATIC.iterdir():
        if path.name == "tokens.css" or not path.name.endswith(".css"):
            continue
        css = _without_comments(path.read_text(encoding="utf-8"))
        assert not RAW_COLOR.search(css), path.name
        assert not RAW_FONT.search(css), path.name


def test_every_site_page_loads_tokens_before_page_css() -> None:
    template = (
        resources.files("reach_around.site") / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    tokens = 'asset("static/tokens.css")'
    site = 'asset("static/site.css")'
    assert tokens in template
    assert template.index(tokens) < template.index(site)


def test_owner_report_maps_its_palette_and_type_to_brand_tokens() -> None:
    css = _without_comments(REPORT_CSS)
    root = re.search(r":root\{.*?\}", css, flags=re.S)
    assert root
    assert "--ra-brand-amber:#FBB03B" in root.group(0)
    assert "--ra-brand-ground:#0F0F0F" in root.group(0)
    assert "--ra-font-body:" in root.group(0)
    outside = css.replace(root.group(0), "")
    assert not RAW_COLOR.search(outside)
    assert not RAW_FONT.search(outside)
