# SPDX-License-Identifier: Apache-2.0
"""The support page QR codes (#713): generated at build time from the address.

Every address here is synthetic.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from qrdecode import LEVEL_BITS, decode, parse_svg

from reach_around.config import SiteConfig
from reach_around.site.build import _chrome_urls, build_site
from reach_around.site.qr import qr_matrix, qr_svg
from reach_around.site.supportcheck import (
    SupportPageError,
    check_qr_svg,
    check_support_html,
)

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "examples" / "instance"
BTC = "bc1qexampleexampleexampleexampleexample"
ETH = "0xExampleExampleExampleExampleExample"


def config(qr: bool = True, coins: int = 2) -> dict[str, Any]:
    all_coins = [
        {"coin": "btc", "address": BTC},
        {"coin": "eth", "address": ETH},
    ][:coins]
    support: dict[str, Any] = {
        "title": "Support",
        "blocks": [{"id": "pay", "coins": all_coins}],
    }
    if qr:
        support["qr"] = True
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
            }
        ],
        "support": support,
    }


def build(raw: dict[str, Any], out: Path) -> Path:
    build_site(SiteConfig.model_validate(raw), out, today=TODAY, assets_dir=ASSETS)
    return out


# --- the encoder and its decode round trip


@pytest.mark.parametrize("size", [1, 11, 20, 32, 46, 60, 74, 86, 108, 130, 151])
def test_encoder_round_trips_through_an_independent_decoder(size: int) -> None:
    text = ("bc1q" + "x7Kp" * 40)[:size]
    matrix = qr_matrix(text)
    payload, level, version = decode(matrix)
    assert payload == text.encode()
    assert level == LEVEL_BITS["Q"]
    assert len(matrix) == 17 + 4 * version


def test_the_decoder_bites_on_a_flipped_module() -> None:
    matrix = qr_matrix(BTC)
    bad = [row[:] for row in matrix]
    # a module in the middle of the data area; the Reed-Solomon check or the
    # payload must change, never a silent pass
    mid = len(bad) // 2
    bad[mid + 3][mid + 1] = not bad[mid + 3][mid + 1]
    try:
        payload, _, _ = decode(bad)
    except AssertionError:
        return
    assert payload == BTC.encode()  # one flipped module is within the correction


def test_the_decoder_bites_on_the_wrong_text() -> None:
    payload, _, _ = decode(qr_matrix(ETH))
    assert payload != BTC.encode()


def test_encoder_refuses_what_does_not_fit_or_is_not_ascii() -> None:
    with pytest.raises(ValueError, match="too long"):
        qr_matrix("a" * 152)
    with pytest.raises(ValueError, match="ASCII"):
        qr_matrix("bç")
    with pytest.raises(ValueError, match="empty"):
        qr_matrix("")


# --- the SVG


def test_svg_has_quiet_zone_level_q_and_decodes_to_the_address() -> None:
    svg = qr_svg(BTC)
    matrix, border = parse_svg(svg)
    assert border >= 4
    payload, level, _ = decode(matrix)
    assert payload == BTC.encode()
    assert level == LEVEL_BITS["Q"]
    # no shortener and no wrapper: the payload is the address, byte for byte
    assert payload.decode() == BTC and "://" not in payload.decode()


def test_svg_is_inert_markup_only() -> None:
    svg = qr_svg(BTC)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ')
    assert re.fullmatch(
        r'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 \d+ \d+" '
        r'shape-rendering="crispEdges"><rect width="\d+" height="\d+" '
        r'fill="#[0-9a-f]{6}"/>'
        r'<path fill="#[0-9a-f]{6}" d="(M\d+ \d+h\d+v1h-\d+z)+"/></svg>\n?',
        svg,
    )
    for banned in ("<script", "onload", "href", "@import", "url(", "<style", "<image"):
        assert banned not in svg


def test_svg_tile_is_not_a_bright_fill() -> None:
    # dark modules on a soft gray tile: scanners need the contrast, the page
    # must not carry a white block
    tile = re.search(r'<rect [^>]*fill="#([0-9a-f]{6})"', qr_svg(BTC))
    assert tile
    r, g, b = (int(tile.group(1)[i : i + 2], 16) for i in (0, 2, 4))
    assert max(r, g, b) <= 0xD8


# --- the build


def test_qr_is_off_unless_asked(tmp_path: Path) -> None:
    out = build(config(qr=False), tmp_path)
    assert not list(out.rglob("*.svg")) or all(
        "qr" not in p.name for p in out.rglob("*.svg")
    )
    html = (out / "support" / "index.html").read_text(encoding="utf-8")
    assert "crypto-qr" not in html and "<img" not in html.split("<main")[1]


def test_build_writes_one_qr_file_per_coin_from_the_address(tmp_path: Path) -> None:
    out = build(config(), tmp_path)
    files = sorted(p.name for p in (out / "support").glob("*.svg"))
    assert files == ["qr-1.svg", "qr-2.svg"]
    for name, addr in (("qr-1.svg", BTC), ("qr-2.svg", ETH)):
        svg = (out / "support" / name).read_text(encoding="utf-8")
        assert svg == qr_svg(addr)
        assert decode(parse_svg(svg)[0])[0] == addr.encode()


def test_page_markup_is_the_exact_drawer(tmp_path: Path) -> None:
    html = (build(config(), tmp_path) / "support" / "index.html").read_text(
        encoding="utf-8"
    )
    found = re.findall(
        r'<details class="crypto-qr">\s*<summary>Show QR code</summary>\s*'
        r'<img class="crypto-qr-image" src="(qr-\d\.svg)" alt="[^"<>]*" '
        r'width="(\d+)" height="(\d+)">\s*</details>',
        html,
    )
    assert [f[0] for f in found] == ["qr-1.svg", "qr-2.svg"]
    assert all(w == h for _, w, h in found)


def test_csp_for_support_paths_stays_self_only(tmp_path: Path) -> None:
    headers = (build(config(), tmp_path) / "_headers").read_text(encoding="utf-8")
    block = headers.split("/support/\n", 1)[1].split("\n/", 1)[0]
    assert "img-src 'self';" in block
    assert "data:" not in block and "https:" not in block.split("img-src")[1][:20]


def test_page_with_qr_stays_inside_the_support_page_weight(tmp_path: Path) -> None:
    out = build(config(coins=2), tmp_path)
    for p in (out / "support").glob("*.svg"):
        assert p.stat().st_size < 4500, p.name


def test_support_qr_flag_is_a_strict_boolean() -> None:
    raw = config()
    raw["support"]["qr"] = "yes"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(raw)


def test_a_supplied_qr_image_is_still_refused_on_the_support_page() -> None:
    raw = config()
    raw["support"]["blocks"][0]["coins"][0]["qr"] = "media/qr.png"
    with pytest.raises(ValidationError, match="no QR image"):
        SiteConfig.model_validate(raw)


# --- the strict check covers the QR


def built_html(tmp_path: Path) -> str:
    return (build(config(), tmp_path) / "support" / "index.html").read_text(
        encoding="utf-8"
    )


NAMES: list[str | None] = ["qr-1.svg", "qr-2.svg"]


def check(html: str, names: list[str | None] | None = None) -> None:
    urls = _chrome_urls(SiteConfig.model_validate(config()))
    check_support_html(html, [BTC, ETH], urls, names)


def test_check_passes_the_built_page(tmp_path: Path) -> None:
    check(built_html(tmp_path), NAMES)


def test_check_refuses_an_image_when_no_qr_is_expected(tmp_path: Path) -> None:
    with pytest.raises(SupportPageError):
        check(built_html(tmp_path), None)


@pytest.mark.parametrize(
    "old,new",
    [
        ('src="qr-1.svg"', 'src="https://example.test/qr-1.svg"'),
        ('src="qr-1.svg"', 'src="qr-2.svg"'),
        ('src="qr-1.svg"', 'src="../qr-1.svg"'),
        ('src="qr-1.svg"', 'src="data:image/svg+xml,x"'),
        ('class="crypto-qr-image"', 'class="crypto-qr-image" onerror="x()"'),
        ('class="crypto-qr-image"', 'class="crypto-qr-image" srcset="a.svg"'),
        ('class="crypto-qr-image"', 'class="crypto-qr-image" style="x:y"'),
        ("<summary>Show QR code</summary>", "<summary>Pay here</summary>"),
        ('<details class="crypto-qr">', '<details class="crypto-qr" open>'),
        ("<summary>", "<summary><b>"),
    ],
)
def test_check_refuses_a_tampered_qr_drawer(tmp_path: Path, old: str, new: str) -> None:
    html = built_html(tmp_path)
    assert old in html
    with pytest.raises(SupportPageError):
        check(html.replace(old, new, 1), NAMES)


def test_check_refuses_a_second_image_in_a_card(tmp_path: Path) -> None:
    html = built_html(tmp_path)
    img = re.search(r"<img class=\"crypto-qr-image\"[^>]*>", html)
    assert img
    with pytest.raises(SupportPageError):
        check(html.replace(img.group(0), img.group(0) * 2, 1), NAMES)


def test_check_refuses_a_drawer_that_is_not_expected(tmp_path: Path) -> None:
    html = built_html(tmp_path)
    check(html, NAMES)  # control: both drawers expected
    with pytest.raises(SupportPageError):
        check(html, ["qr-1.svg", None])


def test_check_refuses_an_image_outside_a_drawer(tmp_path: Path) -> None:
    html = built_html(tmp_path)
    bad = html.replace(
        '<p class="crypto-note">',
        '<p class="crypto-note"><img src="qr-1.svg" alt="x" width="1" height="1">',
        1,
    )
    with pytest.raises(SupportPageError):
        check(bad, NAMES)


def test_qr_file_check_accepts_only_the_exact_svg_for_the_address() -> None:
    good = qr_svg(BTC)
    check_qr_svg(good, BTC)
    for bad in (
        qr_svg(ETH),
        good.replace("</svg>", "<script></script></svg>"),
        good.replace("<path ", '<path onload="x()" '),
        good.replace('fill="#', 'fill="url(#', 1),
        good + "<!-- x -->",
        good.replace("M", "M0 0h1v1h-1zM", 1),
        "",
    ):
        with pytest.raises(SupportPageError):
            check_qr_svg(bad, BTC)
