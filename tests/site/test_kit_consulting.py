# SPDX-License-Identifier: Apache-2.0
"""The "Consulting instead?" line on the kit web pages only (#686). Synthetic data."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, kit_doc, make_config

from kitpdfharness import build_instance, fake_renderer
from reach_around.config.kit import (
    DISPLAY_POLICY,
    KIT_FIELD_POLICY,
    KitConfig,
    gated_projection,
    public_projection,
)
from reach_around.kit.pdf import (
    _expected_text,
    build_kit_pdf,
    build_public_kit_pdf,
)
from reach_around.kit.pdftext import extract_pages

URL = "https://consulting.example.com/"
LINE = 'Consulting instead? Visit my <a href="https://consulting.example.com/"'


def doc(url: str | None, **kw: Any) -> dict[str, Any]:
    out = kit_doc(partners=[{"name": "Acme"}], **kw)
    if url is not None:
        out["consulting_url"] = url
    return out


def pages(tmp_path: Path, url: str | None, **kw: Any) -> tuple[str, str]:
    out, gated = build(tmp_path, make_config(doc(url, **kw)))
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / "kit.html").read_text(encoding="utf-8"),
    )


def test_the_line_is_verbatim_with_the_link_on_both_pages(tmp_path: Path) -> None:
    for html in pages(tmp_path, URL):
        assert LINE in html
        assert "consulting site</a>.</p>" in html
        assert 'rel="noopener noreferrer"' in html.split(LINE)[1].split("</p>")[0]


def test_the_line_sits_right_after_the_contact_block(tmp_path: Path) -> None:
    for html in pages(tmp_path, URL, contact="Brand deals: partners at example.com"):
        contact = html.index("Brand deals: partners at example.com")
        line = html.index("Consulting instead?")
        assert contact < line
        assert html[contact:line].count("<p") <= 2


def test_the_line_prints_without_a_contact_line(tmp_path: Path) -> None:
    for html in pages(tmp_path, URL):
        assert "Consulting instead?" in html and "kit-contact" not in html


def test_no_url_means_no_line_at_all(tmp_path: Path) -> None:
    for html in pages(tmp_path, None):
        assert "Consulting instead" not in html
        assert "consulting site" not in html
        assert "kit-consulting" not in html


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "http://consulting.example.com/",
        "javascript:alert(1)",
        "//consulting.example.com/",
        "consulting.example.com",
        " https://consulting.example.com/",
        "https://user:pw@consulting.example.com/",
        "https://consulting.example.com/" + "a" * 2100,
        "https://consulting.example.com/\nx",
    ],
)
def test_a_bad_url_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(doc(bad))


def test_the_field_is_optional_and_public_in_the_policy() -> None:
    assert KitConfig.model_validate(doc(None)).consulting_url is None
    assert KIT_FIELD_POLICY["consulting_url"].visibility == "public"
    assert DISPLAY_POLICY["consulting_url"] == "public"
    kit = KitConfig.model_validate(doc(URL))
    assert public_projection(kit, None)["consulting_url"] == URL
    assert gated_projection(kit, None)["consulting_url"] == URL


def test_the_pdfs_do_not_print_the_line() -> None:
    kit = KitConfig.model_validate(doc(URL))
    for where, text in _expected_text(kit, None):
        assert "consulting" not in where and "Consulting" not in text


def test_the_built_pdfs_do_not_contain_the_line(tmp_path: Path) -> None:
    """Both real PDF builds (public and gated) run on a page that has the line,
    through the stand-in renderer, and the text that comes out has no trace of it."""
    config = make_config(doc(URL))
    config, site, gated = build_instance(tmp_path, config)
    # The web pages do carry it, so the PDFs must strip it.
    assert "Consulting instead?" in (site / "kit" / "index.html").read_text("utf-8")
    assert "Consulting instead?" in (gated / "kit.html").read_text("utf-8")
    common: dict[str, Any] = {
        "site_dir": site,
        "out_dir": tmp_path / "private",
        "created": date(2027, 3, 10),
        "renderer": fake_renderer,
    }
    results = [
        build_public_kit_pdf(config, name="public.pdf", **common),
        build_kit_pdf(config, gated_dir=gated, name="gated.pdf", **common),
    ]
    for result in results:
        flat = "".join(" ".join(extract_pages(result.path.read_bytes())).split())
        assert "Consulting" not in flat and "consulting" not in flat
