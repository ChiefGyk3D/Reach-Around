# SPDX-License-Identifier: Apache-2.0
"""Helpers for the kit PDF tests: a synthetic instance, a hand-built PDF in the
shape Chromium writes, and a fake renderer that needs no browser.

Everything is synthetic: a fake creator, example.com and canary strings.
"""

from __future__ import annotations

import getpass
import time
import zlib
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
CANARY = "CANARY-GATED-7f3a"
SITE_KEY = "0x4AAAAAAAexampleKey"


def kit_doc() -> dict[str, Any]:
    return {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests. Streams games & talks.",
        "metrics": [
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 1200,
                "as_of": "2027-03-01",
            },
            {
                "platform": "youtube",
                "label": "Subscribers",
                "value": 3400,
                "as_of": "2027-03-05",
            },
        ],
        "turnstile_site_key": SITE_KEY,
        "partners": [
            {
                "name": f"Acme Fake Co {CANARY}",
                "note": "2026 sponsor, fake",
                "url": "https://example.com/acme",
            }
        ],
        "case_studies": [
            {
                "title": "Launch week fake",
                "summary": "A fake product launch on stream.",
                "result": "Ten thousand fake views.",
            }
        ],
        "rates": [{"item": "Sponsored video fake", "price_text": "Ask me!"}],
        "demographics": [
            {"label": "Top country", "value": "Exampleland 40%", "as_of": "2027-03-01"}
        ],
    }


def make_config(kit: dict[str, Any] | None = None) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [
                        {"id": "one", "title": "One", "url": "https://example.com/1"}
                    ],
                }
            ],
            "features": {"kit_gate": True},
            "kit": kit if kit is not None else kit_doc(),
        }
    )


def build_instance(
    root: Path, config: SiteConfig | None = None
) -> tuple[SiteConfig, Path, Path]:
    """Build a synthetic site; return the config, the public dir, the gated dir."""
    config = config or make_config()
    site, gated = root / "site", root / "gated"
    build_site(config, site, today=TODAY, gated_out=gated)
    return config, site, gated


# --- a PDF in the shape Chromium (Skia) writes -------------------------------


def _hex16(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def make_pdf(
    pages: list[list[str]],
    *,
    title: str = "Kit",
    creator: bool = True,
    form_xobject: bool = False,
    stamp: str | None = None,
) -> bytes:
    """A classic-xref PDF. Each page is a list of text lines. One Type0 font with
    Identity-H codes and a ToUnicode CMap (bfchar for most characters, a bfrange
    for the digits, and one ligature). Text is drawn with ``Tj`` and ``TJ``."""
    stamp = stamp or time.strftime("%Y%m%d%H%M%S")
    chars = sorted({ch for page in pages for line in page for ch in line} | {" "})
    gid = {ch: index + 3 for index, ch in enumerate(chars)}
    digits = [ch for ch in chars if ch in "0123456789"]
    bfchar = [f"<{gid[ch]:04X}> <{_hex16(ch)}>" for ch in chars if ch not in digits]
    bfchar.append("<00FF> <00660069>")  # a ligature glyph for "fi"
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{len(bfchar)} beginbfchar\n" + "\n".join(bfchar) + "\nendbfchar\n"
    )
    for d in digits:
        cmap += (
            f"1 beginbfrange\n<{gid[d]:04X}> <{gid[d]:04X}> <{_hex16(d)}>\nendbfrange\n"
        )
    cmap += "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"

    objects: dict[int, bytes] = {}

    def stream(data: bytes) -> bytes:
        packed = zlib.compress(data)
        return (
            f"<</Filter /FlateDecode\n/Length {len(packed)}>> stream\n".encode()
            + packed
            + b"\nendstream"
        )

    n_pages = len(pages)
    page_ids = [10 + 2 * i for i in range(n_pages)]
    objects[1] = (
        f"<</Title <FEFF{_hex16(title)}>\n"
        + (
            "/Creator (Mozilla/5.0 \\(X11; Linux x86_64\\) HeadlessChrome/156.0.0.0 "
            f"Safari/537.36 {getpass.getuser()})\n"
            if creator
            else ""
        )
        + f"/Producer (Skia/PDF m156)\n/CreationDate (D:{stamp}+00'00')\n"
        f"/ModDate (D:{stamp}+00'00')>>"
    ).encode()
    objects[2] = (
        f"<</Type /Pages\n/Count {n_pages}\n/Kids ["
        + " ".join(f"{p} 0 R" for p in page_ids)
        + "]>>"
    ).encode()
    objects[3] = b"<</Type /Catalog\n/Pages 2 0 R>>"
    objects[4] = (
        b"<</Type /Font\n/Subtype /Type0\n/BaseFont /AAAAAA+Test\n"
        b"/Encoding /Identity-H\n/ToUnicode 5 0 R>>"
    )
    objects[5] = stream(cmap.encode())
    for index, lines in enumerate(pages):
        ops = ["BT", "/F4 12 Tf"]
        for line_no, line in enumerate(lines):
            codes = "".join(f"{gid[ch]:04X}" for ch in line)
            if line_no % 2:
                half = len(codes) // 8 * 4
                ops.append(f"[<{codes[:half]}> -20 <{codes[half:]}>] TJ")
            else:
                ops.append(f"<{codes}> Tj")
            ops.append("0 -14 Td")
        if index == 0:
            ops.append("<00FF> Tj")
        ops.append("ET")
        content_id = page_ids[index] + 1
        extra = " /XObject <</X1 6 0 R>>" if form_xobject else ""
        objects[page_ids[index]] = (
            f"<</Type /Page\n/Parent 2 0 R\n/Resources <</Font <</F4 4 0 R>>{extra}>>\n"
            f"/MediaBox [0 0 612 792]\n/Contents {content_id} 0 R>>"
        ).encode()
        objects[content_id] = stream("\n".join(ops).encode())
    if form_xobject:
        objects[6] = (
            b"<</Type /XObject\n/Subtype /Form\n/BBox [0 0 1 1]\n/Length 0>> "
            b"stream\n\nendstream"
        )

    body = bytearray(b"%PDF-1.4\n%\xd3\xeb\xe9\xe1\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(body)
        body += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    top = max(objects) + 1
    xref = len(body)
    body += f"xref\n0 {top}\n0000000000 65535 f \n".encode()
    for number in range(1, top):
        if number in offsets:
            body += f"{offsets[number]:010d} 00000 n \n".encode()
        else:
            body += b"0000000000 65535 f \n"
    body += (
        f"trailer\n<</Size {top}\n/Root 3 0 R\n/Info 1 0 R>>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(body)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "title"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "title"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.lines.append(" ".join(data.split()))


def fake_renderer(html: str, work: Path) -> bytes:
    """Stands in for the browser: the page's visible text becomes the PDF, with a
    creator string and the current time in the Info dictionary (as Chromium
    writes them). The page is also written to the work directory, as the real
    renderer does."""
    (work / "page.html").write_text(html, encoding="utf-8")
    parser = _Text()
    parser.feed(html)
    lines = parser.lines
    pages = [lines[i : i + 20] for i in range(0, len(lines), 20)] or [["empty"]]
    return make_pdf(pages, title="Chromium title")
