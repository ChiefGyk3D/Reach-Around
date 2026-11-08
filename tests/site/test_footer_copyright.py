# SPDX-License-Identifier: Apache-2.0
"""The optional footer copyright line: absent by default, plain text, {year}."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "examples" / "instance"
PAGES = ("index.html", "more/index.html", "privacy/index.html", "404.html")
GOLDEN = Path(__file__).parent / "golden_footer"


def config(footer: dict[str, Any] | None = None) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "disclosure": {"text": "Some links are affiliate links.", "always": True},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [{"id": "b", "title": "B", "url": "https://e.com/b"}],
            },
        ],
    }
    if footer is not None:
        data["footer"] = footer
    return SiteConfig.model_validate(data)


def pages(cfg: SiteConfig, out: Path) -> dict[str, str]:
    build_site(cfg, out, today=TODAY, assets_dir=ASSETS)
    return {p: (out / p).read_text(encoding="utf-8") for p in PAGES}


def footer_of(html: str) -> str:
    return html[html.index("<footer>") : html.index("</footer>")]


def test_absent_by_default(tmp_path: Path) -> None:
    assert config().footer.copyright is None
    for name, html in pages(config(), tmp_path).items():
        assert "copyright" not in html.lower(), name


def test_shown_once_on_every_page_next_to_disclosure(tmp_path: Path) -> None:
    built = pages(config({"copyright": "(c) Example Creator"}), tmp_path)
    for name, html in built.items():
        assert html.count("(c) Example Creator") == 1, name
        foot = footer_of(html)
        assert foot.index("affiliate") < foot.index("(c) Example")
        assert foot.index("(c) Example") < foot.index('class="privacy-link"')


def test_year_token_replaced_with_build_year(tmp_path: Path) -> None:
    built = pages(config({"copyright": "© {year} Example LLC"}), tmp_path)
    for html in built.values():
        assert "© 2027 Example LLC" in html
        assert "{year}" not in html


def test_nothing_else_interpolated(tmp_path: Path) -> None:
    text = "{creator} {0} {year.x} {{year}}"
    built = pages(config({"copyright": text}), tmp_path)
    assert "{creator} {0} {year.x} {2027}" in built["index.html"]


def test_escaped_not_html(tmp_path: Path) -> None:
    built = pages(config({"copyright": "<b>A & B</b>"}), tmp_path)
    foot = footer_of(built["index.html"])
    assert "&lt;b&gt;A &amp; B&lt;/b&gt;" in foot
    assert "<b>" not in foot


def test_no_inline_style_or_script(tmp_path: Path) -> None:
    foot = footer_of(pages(config({"copyright": "x"}), tmp_path)["index.html"])
    assert "style=" not in foot and "<script" not in foot


def test_length_cap() -> None:
    assert config({"copyright": "x" * 120}).footer.copyright == "x" * 120
    with pytest.raises(ValidationError):
        config({"copyright": "x" * 121})


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        " lead",
        "trail ",
        "a\nb",
        "a\tb",
        "a\x00b",
        "a‮b",
        "a​b",
        "a b",
        "﻿a",
        5,
        True,
    ],
)
def test_bad_values_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config({"copyright": bad})


def test_golden_with_and_without(tmp_path: Path, update_golden: bool) -> None:
    for name, footer in (
        ("without", None),
        ("with", {"copyright": "© {year} Example LLC"}),
    ):
        text = footer_of(pages(config(footer), tmp_path / name)["index.html"])
        path = GOLDEN / f"{name}.html"
        if update_golden:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
        assert path.is_file(), "no golden file; run pytest --update-golden"
        assert text == path.read_text(encoding="utf-8")
