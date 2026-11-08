# SPDX-License-Identifier: Apache-2.0
"""Fail-closed gaps in the kit PDF code found in review (PR #339)."""

from __future__ import annotations

import os
import shutil
import zlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from kitpdfharness import (
    build_instance,
    fake_renderer,
    kit_doc,
    make_config,
)
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import PdfTextError, _Budget, _CMap, _show
from reach_around.kit.stage import StageError, stage_page
from reach_around.site.build import build_site

CREATED = date(2027, 3, 10)
CMAP = (
    "begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    "1 beginbfchar\n<0003> <0041>\nendbfchar\n"
)


# --- 1: Flate streams must be complete ---


def test_a_truncated_flate_stream_is_refused() -> None:
    packed = zlib.compress(b"x" * 2000)
    with pytest.raises(PdfTextError, match="complete|truncated"):
        _Budget().inflate(packed[:-6], b"<</Filter /FlateDecode>>")


def test_trailing_bytes_after_a_flate_stream_are_refused() -> None:
    packed = zlib.compress(b"x" * 20)
    with pytest.raises(PdfTextError, match="trailing"):
        _Budget().inflate(packed + b"junk", b"<</Filter /FlateDecode>>")
    assert _Budget().inflate(packed, b"<</Filter /FlateDecode>>") == b"x" * 20


# --- 2 and 3: every code must map; a font needs a map ---


def test_an_unmapped_code_is_an_error_not_an_empty_string() -> None:
    cmap = _CMap(CMAP)
    assert cmap.decode(b"\x00\x03") == "A"
    with pytest.raises(PdfTextError, match="unmapped"):
        cmap.decode(b"\x00\x03\x00\x99")


def test_an_incomplete_code_is_an_error() -> None:
    with pytest.raises(PdfTextError, match="incomplete"):
        _CMap(CMAP).decode(b"\x00\x03\x00")


def test_a_font_with_no_unicode_map_is_refused_not_read_as_latin_1() -> None:
    with pytest.raises(PdfTextError, match="ToUnicode|map"):
        _show(("hex", b"0041"), None)


# --- 4: JPEG data after the end marker is dropped ---


def _jpeg_with_tail(tail: bytes, end: bool = True) -> bytes:
    sos = b"\xff\xda" + (4).to_bytes(2, "big") + b"\x00\x00"
    return (
        b"\xff\xd8"
        + sos
        + b"\x01\x02\xff\x00\x03"
        + (b"\xff\xd9" if end else b"")
        + tail
    )


def _embed_jpeg(tmp_path: Path, data: bytes) -> bytes:
    import base64
    import re

    _, site, gated = build_instance(tmp_path)
    (site / "static" / "p.jpg").write_bytes(data)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    marker = '<div class="header-text">'
    img = '<img src="/static/p.jpg" alt="p">'
    (gated / "kit.html").write_text(page.replace(marker, img + marker, 1), "utf-8")
    html = stage_page(gated, site).html
    found = re.search(r"data:image/jpeg;base64,([^\"]+)", html)
    assert found is not None
    return base64.b64decode(found.group(1))


def test_bytes_after_the_jpeg_end_marker_do_not_survive(tmp_path: Path) -> None:
    tail = b"\xff\xe1\x00\x10Exif\x00\x00SECRET-TAIL-CANARY"
    out = _embed_jpeg(tmp_path, _jpeg_with_tail(tail))
    assert b"SECRET-TAIL-CANARY" not in out
    assert out.endswith(b"\xff\xd9") and out.count(b"\xff\xd9") == 1


def test_a_jpeg_with_no_end_marker_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StageError, match="end"):
        _embed_jpeg(tmp_path, _jpeg_with_tail(b"", end=False))


# --- 5: hidden content stays hidden through malformed nesting ---


@pytest.mark.parametrize(
    "fragment",
    [
        "<div hidden><span>secret</span></bogus><p>LEAK</p></div>",
        "<div hidden><p>one</p></p></p><p>LEAK</p></div>",
        "<div hidden></span></em></b><p>LEAK</p></div>",
    ],
)
def test_malformed_end_tags_do_not_unhide_hidden_content(
    tmp_path: Path, fragment: str
) -> None:
    _, site, gated = build_instance(tmp_path)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    marker = '<section class="kit"'
    (gated / "kit.html").write_text(page.replace(marker, fragment + marker, 1), "utf-8")
    html = stage_page(gated, site).html
    assert "LEAK" not in html and "secret" not in html
    assert "Example Creator media kit" in html  # the content after it is back


# --- 6: the site root and static dir are never canonicalized away ---


def test_a_symlinked_site_root_is_refused(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    alias = tmp_path / "alias"
    os.symlink(site, alias)
    with pytest.raises(StageError, match="symbolic link"):
        stage_page(gated, alias)


def test_a_symlinked_static_dir_is_refused(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    real = tmp_path / "elsewhere"
    shutil.move(str(site / "static"), real)
    os.symlink(real, site / "static")
    with pytest.raises(StageError, match="symbolic link"):
        stage_page(gated, site)


# --- 7: cleanup failures are visible ---


def _failing_rmtree(path: object, *args: object, **kwargs: object) -> None:
    raise OSError("cannot remove")


def test_a_cleanup_failure_after_success_fails_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path)
    monkeypatch.setattr(shutil, "rmtree", _failing_rmtree)
    with pytest.raises(PdfError, match="work directory"):
        build_kit_pdf(
            config,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit.pdf",
            created=CREATED,
            renderer=fake_renderer,
        )
    assert not (tmp_path / "private" / "kit.pdf").exists()


def test_a_cleanup_failure_keeps_the_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path)
    monkeypatch.setattr(shutil, "rmtree", _failing_rmtree)

    def boom(html: str, work: Path) -> bytes:
        raise PdfError("render failed")

    with pytest.raises(PdfError, match="render failed") as caught:
        build_kit_pdf(
            config,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit.pdf",
            created=CREATED,
            renderer=boom,
        )
    assert any("work directory" in note for note in caught.value.__notes__)


# --- 8: the rendered metric rows match the resolved values ---


def _run_with_html_edit(tmp_path: Path, old: str, new: str) -> None:
    config, site, gated = build_instance(tmp_path)

    def renderer(html: str, work: Path) -> bytes:
        assert old in html
        return fake_renderer(html.replace(old, new), work)

    build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=CREATED,
        renderer=renderer,
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (">1,200<", ">9,999<"),  # a changed number
        ("2027-03-05", "2027-03-06"),  # a changed date
        (">4,600<", ">4,601<"),  # a changed total
        (">YouTube<", ">Twitch<"),  # a changed platform on that row
    ],
)
def test_a_metric_row_that_differs_from_the_resolved_values_fails(
    tmp_path: Path, old: str, new: str
) -> None:
    with pytest.raises(PdfError, match="metric|total"):
        _run_with_html_edit(tmp_path, old, new)


def test_counts_metrics_are_checked_against_the_numbers_file(tmp_path: Path) -> None:
    doc = kit_doc()
    doc["metrics"] = [{"platform": "twitch", "label": "Followers", "source": "counts"}]
    config = make_config(doc)
    stamp = datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
    numbers = Numbers(entries={"twitch": NumbersEntry(count=777, as_of=stamp)})
    site, gated = tmp_path / "site", tmp_path / "gated"
    build_site(config, site, today=CREATED, numbers=numbers, gated_out=gated)

    def run(renderer_edit: tuple[str, str] | None, out: str) -> None:
        def renderer(html: str, work: Path) -> bytes:
            if renderer_edit:
                html = html.replace(*renderer_edit)
            return fake_renderer(html, work)

        build_kit_pdf(
            config,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / out,
            name="kit.pdf",
            created=CREATED,
            renderer=renderer,
            numbers=numbers,
        )

    run(None, "ok")
    with pytest.raises(PdfError, match="metric|total"):
        run((">777<", ">778<"), "bad")
