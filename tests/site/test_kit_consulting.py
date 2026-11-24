# SPDX-License-Identifier: Apache-2.0
"""The optional "Consulting instead?" line on the kit (#686). Synthetic data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, kit_doc, make_config

from reach_around.config.kit import (
    DISPLAY_POLICY,
    KIT_FIELD_POLICY,
    KitConfig,
    gated_projection,
    public_projection,
)
from reach_around.kit.pdf import _expected_text

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
