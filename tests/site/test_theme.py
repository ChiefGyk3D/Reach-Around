# SPDX-License-Identifier: Apache-2.0
"""Theme tests: values are validated and only allowlisted tokens are emitted."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config import ConfigError, SiteConfig, load_site_config
from reach_around.site.build import build_site
from reach_around.site.theme import ALLOWED_PROPERTIES, theme_css

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]


def site_data(theme: object = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/"}
                ],
            }
        ],
    }
    if theme is not None:
        data["theme"] = theme
    return data


def load(tmp_path: Path, theme: object = None) -> SiteConfig:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(site_data(theme)), encoding="utf-8")
    return load_site_config(path)


def refused(tmp_path: Path, theme: object) -> str:
    with pytest.raises(ConfigError) as info:
        load(tmp_path, theme)
    return str(info.value)


def color(tmp_path: Path, value: str) -> str:
    return refused(tmp_path, {"colors": {"bg": value}})


def css_for(tmp_path: Path, theme: object) -> str:
    return theme_css(load(tmp_path, theme).theme)


def built_css(tmp_path: Path, theme: object) -> str:
    out = tmp_path / "out"
    build_site(load(tmp_path, theme), out, today=TODAY)
    return (out / "static" / "theme.css").read_text(encoding="utf-8")


# --- colors -----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "#fff",
        "#FFFF",
        "#1a2b3c",
        "#1a2b3c4d",
        "rgb(10, 20, 30)",
        "rgba(10 20 30 / 50%)",
        "hsl(210, 40%, 50%)",
        "hsla(210deg 40% 50% / 0.5)",
        "rebeccapurple",
        "Tomato",
        "transparent",
    ],
)
def test_color_values_are_validated_good(tmp_path: Path, value: str) -> None:
    css = css_for(tmp_path, {"colors": {"bg": value}})
    assert f"--ra-color-bg: {value};" in css


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("red; background:url(x)", "';'"),
        ("red;", "';'"),
        ("red}", "'}'"),
        ("red {", "'{'"),
        ("url(https://example.com/x.png)", "'url('"),
        ("expression(alert(1))", "'expression('"),
        ("red\nbackground:blue", "newline"),
        ("@import 'x'", "'@import'"),
    ],
)
def test_color_values_are_validated_bad(
    tmp_path: Path, value: str, reason: str
) -> None:
    assert reason in color(tmp_path, value)


@pytest.mark.parametrize(
    "value",
    ["", "#12", "#ggg", "rgb(1,2", "rgb(url)", "notacolor", "var(--x)", "red blue"],
)
def test_color_values_outside_the_allowlist_are_refused(
    tmp_path: Path, value: str
) -> None:
    assert "theme.colors: bg:" in color(tmp_path, value)


@pytest.mark.parametrize(
    "value",
    ["red/* x */", "red/*", "#fff */", "re\\64", "\\72ed", "rgb(1\\2c 2 3)"],
)
def test_a_value_with_a_comment_or_backslash_escape_is_refused(
    tmp_path: Path, value: str
) -> None:
    message = color(tmp_path, value)
    assert "comment" in message or "backslash" in message


def test_only_known_token_names_are_emitted(tmp_path: Path) -> None:
    for key in ("--ra-evil", "bg;x", "BG", "bg-image", ""):
        assert "unknown color" in refused(tmp_path, {"colors": {key: "red"}})
    css = css_for(
        tmp_path,
        {
            "colors": {
                "bg": "#101010",
                "text": "#eee",
                "accent": "hotpink",
                "surface": "#222",
                "border": "#444",
            }
        },
    )
    for name in ("bg", "text", "accent", "surface", "border"):
        assert f"--ra-color-{name}:" in css


def test_theme_css_contains_only_allowlisted_property_names(tmp_path: Path) -> None:
    css = css_for(
        tmp_path,
        {
            "colors": {
                "bg": "#101010",
                "text": "#eee",
                "accent": "hotpink",
                "surface": "#222",
                "border": "#444",
            },
            "fonts": {"body": "fonts/body.woff2", "heading": "Georgia, serif"},
            "background": "https://example.com/bg.png",
            "column_width": "40rem",
            "radius": "1rem",
            "button_style": "pill",
            "mode": "dark",
        },
    )
    names = set(re.findall(r"([A-Za-z-]+)\s*:", re.sub(r'url\("[^"]*"\)', "", css)))
    assert names
    assert names <= ALLOWED_PROPERTIES, names - ALLOWED_PROPERTIES
    # The generated CSS has no comments, escapes, imports or inline script hooks.
    for bad in ("/*", "\\", "@import", "expression(", "javascript:", "<"):
        assert bad not in css


# --- fonts ------------------------------------------------------------------


def test_fonts_are_system_stacks_or_local_files(tmp_path: Path) -> None:
    css = css_for(
        tmp_path,
        {
            "fonts": {
                "body": "Georgia, 'Times New Roman', serif",
                "heading": "fonts/h.woff2",
            }
        },
    )
    assert '--ra-font-body: "Georgia", "Times New Roman", serif;' in css
    assert 'src: url("../fonts/h.woff2")' in css
    for bad in (
        "https://fonts.example.com/f.woff2",
        "http://fonts.example.com/f.woff2",
        "//fonts.example.com/f.woff2",
        "url(f.woff2)",
        "/etc/passwd.woff2",
        "../outside.woff2",
        "fonts/../../outside.woff2",
        "fonts\\x.woff2",
        "fonts/x.exe",
        "C:/x.woff2",
        "Georgia; color:red",
        "Georgia, }",
    ):
        refused(tmp_path, {"fonts": {"body": bad}})
    assert "third-party" in refused(
        tmp_path, {"fonts": {"body": "https://fonts.example.com/f.woff2"}}
    )


# --- background -------------------------------------------------------------


def test_background_is_https_or_relative_asset(tmp_path: Path) -> None:
    assert 'url("https://example.com/bg.png")' in css_for(
        tmp_path, {"background": "https://example.com/bg.png"}
    )
    assert 'url("../media/bg.webp")' in css_for(
        tmp_path, {"background": "media/bg.webp"}
    )
    for bad in (
        "http://example.com/bg.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "//example.com/bg.png",
        "/abs/bg.png",
        "../bg.png",
        "media/../../bg.png",
        'https://example.com/a".png',
        "https://example.com/a).png",
        "https://example.com/a;b.png",
        "https://example.com/a'b.png",
        "https://example.com/a\\b.png",
        "https://user:pw@example.com/a.png",
        "media/bg.png;x",
        "media/bg.html",
    ):
        refused(tmp_path, {"background": bad})


# --- lengths, button style, mode --------------------------------------------


def test_column_width_accepts_only_a_length(tmp_path: Path) -> None:
    for good in ("560px", "40em", "60ch", "90%", "100vw", "20.5rem"):
        assert f"--ra-column-width: {good};" in css_for(
            tmp_path, {"column_width": good}
        )
    for bad in (
        "auto",
        "560",
        "calc(100% - 2rem)",
        "560px; color:red",
        "560px}",
        "-5px",
        "1e3px",
        "url(x)",
        "560 px",
        "",
        "560px\n",
        "var(--x)",
    ):
        refused(tmp_path, {"column_width": bad})
        refused(tmp_path, {"radius": bad})


def test_button_style_and_mode_are_closed_sets(tmp_path: Path) -> None:
    refused(tmp_path, {"button_style": "blob"})
    refused(tmp_path, {"mode": "sepia"})
    refused(tmp_path, {"unknown": 1})
    assert "--ra-button-radius: 999px;" in css_for(tmp_path, {"button_style": "pill"})
    assert "--ra-button-radius: 0;" in css_for(tmp_path, {"button_style": "square"})


def test_mode_dark_forces_dark_tokens(tmp_path: Path) -> None:
    assert "color-scheme: dark;" in css_for(tmp_path, {"mode": "dark"})
    assert "color-scheme: light;" in css_for(tmp_path, {"mode": "light"})
    assert "color-scheme" not in css_for(tmp_path, {"mode": "auto"})


# --- the built page ---------------------------------------------------------


def test_theme_css_overrides_defaults_in_the_built_page(tmp_path: Path) -> None:
    out = tmp_path / "out"
    config = load(tmp_path, {"colors": {"bg": "#101010"}, "column_width": "40rem"})
    build_site(config, out, today=TODAY)
    css = (out / "static" / "theme.css").read_text()
    assert ":root" in css
    assert "--ra-color-bg: #101010;" in css
    assert "--ra-column-width: 40rem;" in css
    site_css = (out / "static" / "site.css").read_text()
    # site.css declares every default and reads each through var(--ra-*).
    for token in ("color-bg", "column-width", "radius", "button-radius", "font-body"):
        assert f"--ra-{token}:" in site_css
        assert f"var(--ra-{token})" in site_css
    html = (out / "index.html").read_text()
    assert html.index("static/site.css") < html.index("static/theme.css")
    assert "<style" not in html
    assert "style=" not in html


def test_theme_stylesheet_path_resolves_from_every_page(tmp_path: Path) -> None:
    data = site_data()
    data["tabs"].append(
        {
            "id": "more",
            "title": "More",
            "blocks": [{"id": "two", "title": "Two", "url": "https://example.com/2"}],
        }
    )
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    out = tmp_path / "out"
    build_site(load_site_config(path), out, today=TODAY)
    for page in sorted(out.rglob("*.html")):
        href = re.search(
            r'<link rel="stylesheet" href="([^"]*theme\.css)">', page.read_text()
        )
        assert href, page
        assert (page.parent / href.group(1)).resolve().is_file()


def test_default_theme_builds_the_same_page_as_before(tmp_path: Path) -> None:
    css = built_css(tmp_path, None)
    # Nothing is overridden: no declarations, so site.css defaults decide the look.
    assert css == ":root {\n}\n"
    out = tmp_path / "out"
    html = (out / "index.html").read_text()
    stripped = html.replace('<link rel="stylesheet" href="static/theme.css">\n', "")
    assert stripped.count("<link") == 1
    assert html.count("theme.css") == 1
    # The example instance still matches its golden output byte for byte.
    example = tmp_path / "example"
    build_site(
        load_site_config(ROOT / "examples" / "instance" / "site.yaml"),
        example,
        today=TODAY,
        assets_dir=ROOT / "examples" / "instance",
    )
    golden = ROOT / "tests" / "site" / "golden"
    assert (example / "static" / "theme.css").read_bytes() == (
        golden / "static" / "theme.css"
    ).read_bytes()
    assert (example / "index.html").read_bytes() == (golden / "index.html").read_bytes()


def test_theme_doc_example_validates(tmp_path: Path) -> None:
    text = (ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, flags=re.S)
    themed = [b for b in blocks if re.search(r"^theme:", b, flags=re.M)]
    assert themed
    for block in themed:
        path = tmp_path / "doc.yaml"
        path.write_text(block, encoding="utf-8")
        config = load_site_config(path)
        assert theme_css(config.theme)
