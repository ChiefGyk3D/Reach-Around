# SPDX-License-Identifier: Apache-2.0
"""Every shipped example variant must load, build and stay inside the rules."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.loader import load_yaml
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ROOT / "examples" / "instance-variants"
TODAY = date(2027, 3, 10)
EXPECTED = {"minimal", "dark-hero", "grid-cards"}
HOSTILE = "<script>alert(1)</script>"

INLINE_STYLE = re.compile(r"<[^>]*\sstyle\s*=", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc\s*=)", re.IGNORECASE)
STYLE_ELEMENT = re.compile(r"<style\b", re.IGNORECASE)
# Resources the browser fetches on its own. Anchors are excluded: a link the
# creator chose is not something the page loads.
EXTERNAL_RESOURCE = re.compile(
    r"<(?:link|script|iframe|source|video|audio)\b[^>]*"
    r"\s(?:href|src)\s*=\s*[\"']?(?:https?:)?//",
    re.IGNORECASE,
)
CSS_REMOTE_URL = re.compile(r"url\(\s*[\"']?(?:https?:)?//", re.IGNORECASE)


def rule_violations(html: str) -> list[str]:
    checks = (
        ("style attribute", INLINE_STYLE),
        ("event handler", INLINE_HANDLER),
        ("inline script", INLINE_SCRIPT),
        ("style element", STYLE_ELEMENT),
        ("third-party resource", EXTERNAL_RESOURCE),
    )
    return [label for label, pattern in checks if pattern.search(html)]


def variant_files() -> list[Path]:
    return sorted(VARIANTS.glob("*/site.yaml"))


def test_the_expected_variants_exist() -> None:
    assert {p.parent.name for p in variant_files()} == EXPECTED


def test_rule_scanner_bites() -> None:
    assert rule_violations('<p style="x">') == ["style attribute"]
    assert rule_violations('<a onclick="x()">') == ["event handler"]
    assert rule_violations("<script>x</script>") == ["inline script"]
    assert rule_violations("<style>a{}</style>") == ["style element"]
    assert rule_violations('<script src="https://cdn.example.com/a.js">') == [
        "third-party resource"
    ]
    assert rule_violations('<link href="//cdn.example.com/a.css">') == [
        "third-party resource"
    ]
    assert rule_violations('<link href="static/site.css">') == []
    assert rule_violations('<a href="https://example.com/">x</a>') == []


@pytest.mark.parametrize("path", variant_files(), ids=lambda p: p.parent.name)
def test_every_example_variant_validates_and_builds(path: Path, tmp_path: Path) -> None:
    config = load_site_config(path)
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, protect=[path])
    pages = sorted(out.rglob("*.html"))
    assert pages, path
    for page in pages:
        assert rule_violations(page.read_text(encoding="utf-8")) == [], page
    for css in sorted(out.rglob("*.css")):
        assert not CSS_REMOTE_URL.search(css.read_text(encoding="utf-8")), css


@pytest.mark.parametrize("path", variant_files(), ids=lambda p: p.parent.name)
def test_every_example_variant_escapes_hostile_text(path: Path, tmp_path: Path) -> None:
    raw = load_yaml(path)
    assert isinstance(raw, dict)
    raw["creator"]["name"] = HOSTILE
    raw["tabs"][0]["title"] = HOSTILE
    first = raw["tabs"][0]["blocks"][0]
    if "title" in first:
        first["title"] = HOSTILE
    config = SiteConfig.model_validate(raw)
    out = tmp_path / "out"
    build_site(config, out, today=TODAY)
    for page in sorted(out.rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        assert HOSTILE not in html, page
        assert "&lt;script&gt;" in html, page
        assert rule_violations(html) == [], page
