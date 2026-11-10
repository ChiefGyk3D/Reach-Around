# SPDX-License-Identifier: Apache-2.0
"""Rendering the kit ``testimonials`` (#420): gated page and owner PDF only.

Synthetic names, example.com and a canary string.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import (
    GATED_FILE,
    KitLeakError,
    build,
    gated_doc,
    kit_doc,
    make_config,
    tree_bytes,
)

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import extract_pages

TST = "CANARY-TESTIMONIAL-8b2f"


def quotes() -> list[dict[str, Any]]:
    return [
        {
            "quote": f"A fake quote about a fake stream {TST}",
            "name": "Example Person",
            "brand": "Example Brand",
        },
        {"quote": "Another fake quote here", "name": "Second Person"},
    ]


def test_testimonials_render_on_the_gated_page(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc(testimonials=quotes())))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    for text in (TST, "Example Person", "Example Brand", "Second Person"):
        assert text in page
    assert "kit-quote" in page and 'class="kit-testimonials"' in page


def test_testimonials_never_reach_the_public_tree(tmp_path: Path) -> None:
    out, _ = build(tmp_path, make_config(gated_doc(testimonials=quotes())))
    public = tree_bytes(out)
    for text in (
        TST,
        "Example Person",
        "Example Brand",
        "Second Person",
        "Another fake quote",
    ):
        assert text.encode() not in public
    assert b"kit-quote" not in public


def test_every_field_is_guarded_by_the_leak_search(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    for field, leaked in (
        ("quote", f"A fake quote about a fake stream {TST}"),
        ("name", "Example Person"),
        ("brand", "Example Brand"),
    ):
        (overrides / "templates" / "kit.html").write_text(
            '{% extends "base.html" %}{% block content %}'
            f"<p>{leaked}</p>{{% endblock %}}",
            encoding="utf-8",
        )
        with pytest.raises(KitLeakError, match=r"testimonials"):
            build(
                tmp_path / field,
                make_config(gated_doc(testimonials=quotes())),
                overrides=overrides,
            )


def test_values_are_escaped(tmp_path: Path) -> None:
    kit = gated_doc(
        testimonials=[
            {
                "quote": 'Fake "quote" & {{ 7*7 }} {% if 1 %}x{% endif %}',
                "name": "Tom & Jerry's",
                "brand": "Fake &amp; Co",
            }
        ]
    )
    _, gated = build(tmp_path, make_config(kit))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "Fake &#34;quote&#34; &amp; {{ 7*7 }} {% if 1 %}x{% endif %}" in page
    assert "Tom &amp; Jerry&#39;s" in page
    assert "Fake &amp;amp; Co" in page


def test_absent_or_empty_renders_nothing(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc()))
    assert "kit-testimonials" not in (gated / GATED_FILE).read_text(encoding="utf-8")
    _, gated = build(tmp_path / "e", make_config(gated_doc(testimonials=[])))
    assert "kit-quote" not in (gated / GATED_FILE).read_text(encoding="utf-8")


def test_a_quote_without_a_brand_prints_only_the_name(tmp_path: Path) -> None:
    _, gated = build(
        tmp_path,
        make_config(
            gated_doc(testimonials=[{"quote": "Solo fake quote", "name": "Solo Name"}])
        ),
    )
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    block = page[page.index("kit-testimonials") :]
    assert "Solo Name" in block and "kit-brand" not in block


def test_testimonials_alone_make_the_kit_gated(tmp_path: Path) -> None:
    out, gated = build(tmp_path, make_config(kit_doc(testimonials=quotes())))
    assert (gated / GATED_FILE).is_file()
    assert (
        "testimonials"
        in (out / "kit" / "index.html").read_text(encoding="utf-8").lower()
    )


def _pdf(tmp_path: Path, tamper: bool = False) -> Path:
    kit = pdf_kit_doc()
    kit["testimonials"] = [
        {"quote": "Pdf fake quote text", "name": "Pdf Person", "brand": "Pdf Brand"}
    ]
    config, site, gated = build_instance(tmp_path, pdf_config(kit))
    if tamper:
        page = gated / GATED_FILE
        page.write_text(
            page.read_text(encoding="utf-8").replace("Pdf fake quote text", "Changed"),
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


def test_testimonials_are_in_the_owner_pdf(tmp_path: Path) -> None:
    flat = "".join(" ".join(extract_pages(_pdf(tmp_path).read_bytes())).split())
    for text in ("Pdf fake quote text", "Pdf Person", "Pdf Brand"):
        assert "".join(text.split()) in flat


def test_the_pdf_check_fails_when_a_quote_is_missing(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match=r"testimonials"):
        _pdf(tmp_path, tamper=True)
