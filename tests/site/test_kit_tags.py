# SPDX-License-Identifier: Apache-2.0
"""Rendering the kit ``tags`` (#422): public header, gated page and owner PDF.

Synthetic names only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from test_kit_build import GATED_FILE, build, gated_doc, kit_doc, make_config

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import extract_pages


def test_tags_render_in_the_public_header_before_the_numbers(tmp_path: Path) -> None:
    out, _ = build(tmp_path, make_config(gated_doc(tags=["Gaming", "Hardware"])))
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert 'class="profile-tag">Gaming</li>' in html
    assert 'class="profile-tag">Hardware</li>' in html
    assert html.index("profile-tags") < html.index("Audience numbers")
    assert html.index("kit-title") < html.index("profile-tags")


def test_tags_render_on_the_gated_page_too(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc(tags=["Gaming"])))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert 'class="profile-tag">Gaming</li>' in page


def test_tags_alone_do_not_create_a_gated_page(tmp_path: Path) -> None:
    out, gated = build(
        tmp_path,
        make_config(kit_doc(tags=["Gaming"]), features={"kit_gate": False}),
        gated=False,
    )
    assert "Gaming" in (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert not (gated / GATED_FILE).exists()


def test_tags_are_escaped(tmp_path: Path) -> None:
    out, _ = build(
        tmp_path, make_config(gated_doc(tags=['Q&A "live"', "{{ 7*7 }}", "R&amp;D"]))
    )
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert ">Q&amp;A &#34;live&#34;</li>" in html
    assert ">{{ 7*7 }}</li>" in html
    assert ">R&amp;amp;D</li>" in html


def test_absent_or_empty_renders_nothing(tmp_path: Path) -> None:
    out, gated = build(tmp_path, make_config(gated_doc()))
    assert "profile-tag" not in (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "profile-tag" not in (gated / GATED_FILE).read_text(encoding="utf-8")
    out, _ = build(tmp_path / "e", make_config(gated_doc(tags=[])))
    assert 'class="profile-tags"' not in (out / "kit" / "index.html").read_text(
        encoding="utf-8"
    )


def test_a_gated_value_equal_to_a_tag_is_not_a_leak(tmp_path: Path) -> None:
    kit = gated_doc(tags=["Hardware reviews"])
    kit["rates"] = [{"item": "Hardware reviews", "price_text": "Ask me!"}]
    out, gated = build(tmp_path, make_config(kit))
    assert "Hardware reviews" in (gated / GATED_FILE).read_text(encoding="utf-8")
    assert (out / "kit" / "index.html").is_file()


def _pdf(tmp_path: Path, tamper: bool = False) -> Path:
    kit = pdf_kit_doc()
    kit["tags"] = ["Pdftagone", "Pdftagtwo"]
    config, site, gated = build_instance(tmp_path, pdf_config(kit))
    if tamper:
        page = gated / GATED_FILE
        page.write_text(
            page.read_text(encoding="utf-8").replace("Pdftagtwo", "Changed"),
            encoding="utf-8",
        )
    return build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
    ).path


def test_tags_are_in_the_owner_pdf(tmp_path: Path) -> None:
    flat = "".join(" ".join(extract_pages(_pdf(tmp_path).read_bytes())).split())
    assert "Pdftagone" in flat and "Pdftagtwo" in flat


def test_the_pdf_check_fails_when_a_tag_is_missing(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match=r"tags"):
        _pdf(tmp_path, tamper=True)
