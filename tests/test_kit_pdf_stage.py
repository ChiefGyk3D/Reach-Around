# SPDX-License-Identifier: Apache-2.0
"""Staging: the gated page becomes a sealed, self-contained page for the printer."""

from __future__ import annotations

import base64
import os
import re
import struct
import zlib
from pathlib import Path

import pytest

from kitpdfharness import CANARY, SITE_KEY, build_instance
from reach_around.kit.stage import MAX_PAGE_BYTES, StageError, stage_page


def png(extra_chunk: bytes = b"") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + extra_chunk
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def png_text_chunk(text: bytes) -> bytes:
    body = b"tEXt" + b"Author\x00" + text
    return struct.pack(">I", len(body) - 4) + body + struct.pack(">I", zlib.crc32(body))


def jpeg_with_exif() -> bytes:
    exif = b"Exif\x00\x00" + b"SECRET-GPS-CANARY"
    return (
        b"\xff\xd8"
        + b"\xff\xe1"
        + struct.pack(">H", len(exif) + 2)
        + exif
        + b"\xff\xfe"
        + struct.pack(">H", 9)
        + b"comment"
        + b"\xff\xd9"
    )


def embedded(html: str, mime: str) -> bytes:
    match = re.search(rf"data:{mime};base64,([^\"]+)", html)
    assert match is not None
    return base64.b64decode(match.group(1))


def staged_html(tmp_path: Path) -> str:
    _, site, gated = build_instance(tmp_path)
    return stage_page(gated, site).html


def write_gated(gated: Path, html: str) -> None:
    (gated / "kit.html").write_text(html, encoding="utf-8")


def with_header_image(gated: Path, src: str) -> None:
    page = (gated / "kit.html").read_text(encoding="utf-8")
    write_gated(
        gated,
        page.replace(
            '<div class="header-text">',
            f'<img src="{src}" alt="Avatar"><div class="header-text">',
            1,
        ),
    )


def test_staged_page_is_complete_and_sealed(tmp_path: Path) -> None:
    html = staged_html(tmp_path)
    assert f"Acme Fake Co {CANARY}" in html
    assert "Exampleland 40%" in html and "Ask me!" in html
    assert 'href="https://example.com/acme"' in html
    # nothing that fetches, runs or submits
    for banned in (
        "<script",
        "<form",
        "<link",
        "<iframe",
        "<img",
        "<footer",
        "@import",
    ):
        assert banned not in html
    assert "url(" not in html
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    # the page's own stylesheets are inlined
    assert "<style>" in html and "--ra-" in html
    # navigation back to the live site is not part of a printed document
    assert "Skip to the kit" not in html and "Back to the public kit page" not in html
    assert "Cookie notice" not in html and "Preferences" not in html


@pytest.mark.parametrize(
    "fragment",
    [
        "<script>1</script>",
        "<iframe src='https://example.invalid/'></iframe>",
        "<form action='/x'><input name='a'></form>",
        "<object data='x'></object>",
        "<embed src='x'>",
        "<base href='https://example.invalid/'>",
        "<meta http-equiv='refresh' content='0;url=https://example.invalid/'>",
        "<svg><use href='x'/></svg>",
        "<link rel='stylesheet' href='https://example.invalid/a.css'>",
        "<img src='https://example.invalid/a.png'>",
        "<img src='//example.invalid/a.png'>",
        "<img src='file:///etc/hostname'>",
        "<video src='x'></video>",
    ],
)
def test_hostile_markup_is_refused(tmp_path: Path, fragment: str) -> None:
    _, site, gated = build_instance(tmp_path)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    marker = '<section class="kit"'
    write_gated(gated, page.replace(marker, fragment + marker, 1))
    with pytest.raises(StageError):
        stage_page(gated, site)


def test_event_handler_and_style_attributes_and_ids_are_dropped(
    tmp_path: Path,
) -> None:
    _, site, gated = build_instance(tmp_path)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    page = page.replace(
        '<p class="kit-about">',
        '<p class="kit-about" onclick="x()" '
        'style="background:url(https://x.invalid/)">',
    )
    write_gated(gated, page)
    html = stage_page(gated, site).html
    assert "onclick" not in html and "x.invalid" not in html
    assert "style=" not in html


def test_javascript_and_other_href_schemes_are_dropped(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    page = page.replace(
        '<p class="kit-about">',
        '<p><a href="javascript:alert(1)">j</a><a href="/kit/">r</a>'
        '<a href="http://example.com/p">plain</a></p><p class="kit-about">',
    )
    write_gated(gated, page)
    html = stage_page(gated, site).html
    assert "javascript:" not in html and 'href="/kit/"' not in html
    assert "http://example.com/p" not in html
    assert ">j<" in html and ">plain<" in html  # the text stays


def _gated_with_css(tmp_path: Path, href: str) -> tuple[Path, Path]:
    _, site, gated = build_instance(tmp_path)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    first = page.index('<link rel="stylesheet"')
    end = page.index(">", first) + 1
    link = f'<link rel="stylesheet" href="{href}">'
    write_gated(gated, page[:first] + link + page[end:])
    return gated, site


@pytest.mark.parametrize(
    "href",
    [
        "/static/../../../etc/hostname.css",
        "/static/missing.css",
        "https://example.invalid/a.css",
        "//example.invalid/a.css",
        "static/relative.css",
        "/static/a.txt",
        "/other/site.css",
    ],
)
def test_stylesheet_links_are_confined_to_the_site_static_dir(
    tmp_path: Path, href: str
) -> None:
    gated, site = _gated_with_css(tmp_path, href)
    with pytest.raises(StageError):
        stage_page(gated, site)


def test_a_symlinked_stylesheet_is_not_followed(tmp_path: Path) -> None:
    gated, site = _gated_with_css(tmp_path, "/static/evil.css")
    outside = tmp_path / "outside.css"
    outside.write_text("body{color:red}", encoding="utf-8")
    os.symlink(outside, site / "static" / "evil.css")
    with pytest.raises(StageError):
        stage_page(gated, site)


def test_css_remote_references_are_stripped(tmp_path: Path) -> None:
    gated, site = _gated_with_css(tmp_path, "/static/hostile.css")
    (site / "static" / "hostile.css").write_text(
        "@import url(https://x.invalid/a.css);\n"
        "@font-face{font-family:F;src:url(https://x.invalid/f.woff2)}\n"
        "p{background:url('https://x.invalid/b.png');color:#123456}\n",
        encoding="utf-8",
    )
    html = stage_page(gated, site).html
    assert "x.invalid" not in html and "@import" not in html and "url(" not in html
    assert "#123456" in html  # ordinary rules survive


def test_a_symlinked_or_oversized_gated_page_is_refused(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    real = gated / "kit.html"
    moved = tmp_path / "moved.html"
    real.rename(moved)
    os.symlink(moved, real)
    with pytest.raises(StageError, match="symbolic link|not a regular"):
        stage_page(gated, site)
    real.unlink()
    real.write_bytes(b"<p>" + b"a" * (MAX_PAGE_BYTES + 1))
    with pytest.raises(StageError, match="larger"):
        stage_page(gated, site)


def test_a_missing_gated_page_says_to_build_first(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    with pytest.raises(StageError, match="build"):
        stage_page(gated, tmp_path)


def test_a_gated_dir_others_can_enter_is_refused(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    gated.chmod(0o755)
    with pytest.raises(StageError, match="owner"):
        stage_page(gated, site)


def test_a_local_png_is_embedded_and_its_text_chunks_are_dropped(
    tmp_path: Path,
) -> None:
    _, site, gated = build_instance(tmp_path)
    (site / "static" / "avatar.png").write_bytes(
        png(png_text_chunk(b"SECRET-AUTHOR-CANARY"))
    )
    with_header_image(gated, "/static/avatar.png")
    data = embedded(stage_page(gated, site).html, "image/png")
    assert data.startswith(b"\x89PNG")
    assert b"SECRET-AUTHOR-CANARY" not in data and b"tEXt" not in data


def test_a_jpeg_loses_exif_and_comments(tmp_path: Path) -> None:
    _, site, gated = build_instance(tmp_path)
    (site / "static" / "photo.jpg").write_bytes(jpeg_with_exif())
    with_header_image(gated, "/static/photo.jpg")
    data = embedded(stage_page(gated, site).html, "image/jpeg")
    assert data.startswith(b"\xff\xd8")
    assert b"SECRET-GPS-CANARY" not in data and b"comment" not in data


@pytest.mark.parametrize("name", ["pic.svg", "pic.gif", "pic.webp", "pic.bin"])
def test_other_image_types_are_refused(tmp_path: Path, name: str) -> None:
    _, site, gated = build_instance(tmp_path)
    (site / "static" / name).write_bytes(b"GIF89a....")
    with_header_image(gated, f"/static/{name}")
    with pytest.raises(StageError):
        stage_page(gated, site)


def test_the_site_key_is_not_in_the_staged_page(tmp_path: Path) -> None:
    # the gated page has no form; this guards the staging against gaining one
    assert SITE_KEY not in staged_html(tmp_path)
