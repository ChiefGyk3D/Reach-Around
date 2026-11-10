# SPDX-License-Identifier: Apache-2.0
"""Rendering the kit ``disclosure`` statement (#421): gated page and owner PDF only.

Synthetic names, example.com and a canary string.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from test_kit_build import build, gated_doc, kit_doc, make_config, tree_bytes

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import extract_pages
from reach_around.site.kit import GATED_FILE, KitLeakError

STATEMENT = "CANARY-DISCLOSURE-3c9a: an example statement, in two fake sentences."


def test_the_statement_renders_on_the_gated_page(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc(disclosure=STATEMENT)))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert STATEMENT in page and 'class="kit-disclosure"' in page


def test_the_statement_never_reaches_the_public_tree(tmp_path: Path) -> None:
    out, _ = build(tmp_path, make_config(gated_doc(disclosure=STATEMENT)))
    public = tree_bytes(out)
    assert b"CANARY-DISCLOSURE" not in public
    assert b"kit-disclosure" not in public


def test_the_leak_search_covers_the_statement(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "kit.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        f"<p>{STATEMENT}</p>{{% endblock %}}",
        encoding="utf-8",
    )
    with pytest.raises(KitLeakError, match=r"disclosure"):
        build(
            tmp_path, make_config(gated_doc(disclosure=STATEMENT)), overrides=overrides
        )


def test_the_statement_is_escaped(tmp_path: Path) -> None:
    text = 'Fake "quote" & {{ 7*7 }} {% if 1 %}x{% endif %} it\'s &amp; done'
    _, gated = build(tmp_path, make_config(gated_doc(disclosure=text)))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert (
        "Fake &#34;quote&#34; &amp; {{ 7*7 }} {% if 1 %}x{% endif %} "
        "it&#39;s &amp;amp; done"
    ) in page


def test_absent_renders_nothing(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc()))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "kit-disclosure" not in page and "Disclosure" not in page


def test_the_statement_alone_makes_the_kit_gated(tmp_path: Path) -> None:
    out, gated = build(tmp_path, make_config(kit_doc(disclosure=STATEMENT)))
    assert (gated / GATED_FILE).is_file()
    assert (
        "disclosure" in (out / "kit" / "index.html").read_text(encoding="utf-8").lower()
    )


def _pdf(tmp_path: Path, tamper: bool = False) -> Path:
    kit = pdf_kit_doc()
    kit["disclosure"] = "Pdf fake disclosure statement text."
    config, site, gated = build_instance(tmp_path, pdf_config(kit))
    if tamper:
        page = gated / GATED_FILE
        page.write_text(
            page.read_text(encoding="utf-8").replace("fake disclosure", "changed"),
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


def test_the_statement_is_in_the_owner_pdf(tmp_path: Path) -> None:
    flat = "".join(" ".join(extract_pages(_pdf(tmp_path).read_bytes())).split())
    assert "Pdffakedisclosurestatementtext." in flat


def test_the_pdf_check_fails_when_the_statement_is_missing(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match=r"disclosure"):
        _pdf(tmp_path, tamper=True)


def test_the_same_text_in_the_site_footer_stops_the_build(tmp_path: Path) -> None:
    same = "A fake disclosure line that is used twice by mistake."
    config = make_config(
        gated_doc(disclosure=same), disclosure={"text": same, "always": True}
    )
    with pytest.raises(KitLeakError, match=r"disclosure"):
        build(tmp_path, config)


@pytest.mark.parametrize("short", ["a", "Hi", "Fine.", "x&y"])
def test_a_short_statement_equal_to_the_site_footer_text_stops_the_build(
    tmp_path: Path, short: str
) -> None:
    config = make_config(
        gated_doc(disclosure=short), disclosure={"text": short, "always": True}
    )
    with pytest.raises(KitLeakError, match=r"disclosure"):
        build(tmp_path, config)


def test_a_short_statement_that_is_only_part_of_other_text_still_builds(
    tmp_path: Path,
) -> None:
    config = make_config(
        gated_doc(disclosure="a"), disclosure={"text": "A longer footer line."}
    )
    _, gated = build(tmp_path, config)
    assert 'class="kit-disclosure"' in (gated / GATED_FILE).read_text(encoding="utf-8")
