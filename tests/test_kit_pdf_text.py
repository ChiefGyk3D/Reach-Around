# SPDX-License-Identifier: Apache-2.0
"""The text extractor the PDF leak check relies on (no dependency, no browser)."""

from __future__ import annotations

import zlib

import pytest

from kitpdfharness import make_pdf
from reach_around.kit.pdftext import PdfTextError, extract_pages


def test_extracts_each_page_in_order_with_tj_tjs_digits_and_ligatures() -> None:
    pdf = make_pdf(
        [["Hello Partner 2027", "Rates: Ask me!", "Third line here"], ["Page two"]]
    )
    pages = extract_pages(pdf)
    assert len(pages) == 2
    joined = [" ".join(p.split()) for p in pages]
    assert "Hello Partner 2027" in joined[0]
    assert "Rates: Ask me!" in joined[0].replace("\n", " ")
    assert "Third line here" in joined[0]
    assert "fi" in joined[0]  # a ligature glyph maps to two characters
    assert joined[1].startswith("Page two")


def test_text_is_found_even_when_a_run_is_split_by_kerning() -> None:
    # the second line of make_pdf is written with a TJ array and a kern number
    pages = extract_pages(make_pdf([["aaaa", "BreakMeIntoTwoParts"]]))
    assert "BreakMeIntoTwoParts" in pages[0].replace("\n", "")


def test_a_pdf_with_no_pages_is_an_error() -> None:
    with pytest.raises(PdfTextError):
        extract_pages(make_pdf([]))


def test_garbage_is_an_error_not_an_empty_result() -> None:
    with pytest.raises(PdfTextError):
        extract_pages(b"not a pdf at all")


def test_a_form_xobject_is_refused_because_its_text_would_be_missed() -> None:
    with pytest.raises(PdfTextError, match="form"):
        extract_pages(make_pdf([["x y z"]], form_xobject=True))


def test_a_decompression_bomb_is_refused() -> None:
    pdf = make_pdf([["x y z"]])
    bomb = zlib.compress(b"\x00" * (40 * 1024 * 1024))
    marker = b"stream\n"
    start = pdf.index(marker) + len(marker)
    # replace the first stream (the CMap) with the bomb and fix its /Length
    end = pdf.index(b"\nendstream", start)
    head = pdf[:start]
    head = head[: head.rindex(b"/Length")] + f"/Length {len(bomb)}>> ".encode()
    head += b"stream\n"
    bad = head + bomb + pdf[end:]
    with pytest.raises(PdfTextError):
        extract_pages(bad)
