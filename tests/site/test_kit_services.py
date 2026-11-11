# SPDX-License-Identifier: Apache-2.0
"""Rendering the kit ``services`` packages (#419): gated page and owner PDF only.

Synthetic names, example.com and a canary string.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import build, gated_doc, kit_doc, make_config, tree_bytes

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import extract_pages
from reach_around.site.kit import GATED_FILE, KitLeakError

SVC = "CANARY-SERVICE-5d1e"


def services(**kw: Any) -> list[dict[str, Any]]:
    one: dict[str, Any] = {
        "title": f"Starter package {SVC}",
        "included": [f"Two fake mentions {SVC}", "One fake video"],
        "price_text": "Ask me!",
    }
    one.update(kw)
    return [one, {"title": "Second package"}]


def test_services_render_on_the_gated_page_above_the_rates(tmp_path: Path) -> None:
    out, gated = build(tmp_path, make_config(gated_doc(services=services())))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    for text in (SVC, "Two fake mentions", "One fake video", "Second package"):
        assert text in page
    assert 'class="kit-services"' in page
    assert page.index("kit-services") < page.index("kit-rates")


def test_services_never_reach_the_public_tree(tmp_path: Path) -> None:
    out, _ = build(tmp_path, make_config(gated_doc(services=services())))
    public = tree_bytes(out)
    assert SVC.encode() not in public
    assert b"Starter package" not in public
    assert b"Second package" not in public
    assert b"kit-services" not in public.replace(b"/* kit-services */", b"")


def test_every_services_field_is_guarded_by_the_leak_search(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    for field, leaked in (
        ("title", f"Starter package {SVC}"),
        ("included", f"Two fake mentions {SVC}"),
        ("price_text", "From a fake amount"),
    ):
        kit = gated_doc(
            services=services(
                **({"price_text": leaked} if field == "price_text" else {})
            )
        )
        (overrides / "templates" / "kit.html").write_text(
            '{% extends "base.html" %}{% block content %}'
            f"<p>{leaked}</p>{{% endblock %}}",
            encoding="utf-8",
        )
        with pytest.raises(KitLeakError, match=r"services"):
            build(tmp_path / field, make_config(kit), overrides=overrides)


def test_values_are_escaped(tmp_path: Path) -> None:
    kit = gated_doc(
        services=[
            {
                "title": 'Tom & "Jerry" \'s {{ 7*7 }}',
                "included": ["a & b {% if 1 %}x{% endif %}"],
                "price_text": "Ask &amp; me",
            }
        ]
    )
    _, gated = build(tmp_path, make_config(kit))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "Tom &amp; &#34;Jerry&#34; &#39;s {{ 7*7 }}" in page
    assert "a &amp; b {% if 1 %}x{% endif %}" in page
    assert "Ask &amp;amp; me" in page


def test_zero_packages_renders_nothing(tmp_path: Path) -> None:
    _, gated = build(tmp_path)
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "kit-services" not in page


def test_a_package_with_no_items_or_price_renders_only_its_title(
    tmp_path: Path,
) -> None:
    _, gated = build(
        tmp_path, make_config(gated_doc(services=[{"title": "Bare package"}]))
    )
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "Bare package" in page
    block = page[page.index("kit-services") : page.index("kit-rates")]
    assert "<ul" not in block and "kit-price" not in block


def test_services_alone_make_the_kit_gated(tmp_path: Path) -> None:
    kit = kit_doc(services=services())
    out, gated = build(tmp_path, make_config(kit))
    assert (gated / GATED_FILE).is_file()
    form = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "kit-gate" in form
    assert "packages" in form.lower()  # named in the gate explanation


def test_services_are_in_the_owner_pdf(tmp_path: Path) -> None:
    kit = pdf_kit_doc()
    kit["services"] = [
        {
            "title": "Pdf package fake",
            "included": ["Three fake shout outs"],
            "price_text": "From a fake price",
        }
    ]
    config, site, gated = build_instance(tmp_path, pdf_config(kit))
    result = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
    )
    flat = "".join(" ".join(extract_pages(result.path.read_bytes())).split())
    for text in ("Pdf package fake", "Three fake shout outs", "From a fake price"):
        assert "".join(text.split()) in flat


def test_the_pdf_check_fails_when_a_package_text_is_missing(tmp_path: Path) -> None:
    kit = pdf_kit_doc()
    kit["services"] = [{"title": "Pdf package fake", "included": ["Item fake text"]}]
    config, site, gated = build_instance(tmp_path, pdf_config(kit))
    page = gated / GATED_FILE
    page.write_text(
        page.read_text(encoding="utf-8").replace("Item fake text", "Changed"),
        encoding="utf-8",
    )
    with pytest.raises(PdfError, match=r"services"):
        build_kit_pdf(
            config,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit.pdf",
            created=date(2027, 3, 10),
            renderer=fake_renderer,
        )
