# SPDX-License-Identifier: Apache-2.0
"""The support page QR codes (#713): generated at build time from the address.

Every address here is synthetic.
"""

from __future__ import annotations

import hashlib
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


def named(n: int, address: str) -> str:
    """The QR file name: position plus 16 hex of the SHA-256 of the address."""
    return f"qr-{n}-{hashlib.sha256(address.encode()).hexdigest()[:16]}.svg"


N1, N2 = named(1, BTC), named(2, ETH)


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
    assert files == sorted([N1, N2])
    for name, addr in ((N1, BTC), (N2, ETH)):
        svg = (out / "support" / name).read_text(encoding="utf-8")
        assert svg == qr_svg(addr)
        assert decode(parse_svg(svg)[0])[0] == addr.encode()


def test_page_markup_is_the_exact_drawer(tmp_path: Path) -> None:
    html = (build(config(), tmp_path) / "support" / "index.html").read_text(
        encoding="utf-8"
    )
    found = re.findall(
        r'<details class="crypto-qr">\s*<summary>Show QR code</summary>\s*'
        r'<img class="crypto-qr-image" src="(qr-\d-[0-9a-f]{16}\.svg)" alt="[^"<>]*" '
        r'width="(\d+)" height="(\d+)">\s*</details>',
        html,
    )
    assert [f[0] for f in found] == [N1, N2]
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


NAMES: list[str | None] = [N1, N2]


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
        (f'src="{N1}"', f'src="https://example.test/{N1}"'),
        (f'src="{N1}"', f'src="{N2}"'),
        (f'src="{N1}"', f'src="../{N1}"'),
        (f'src="{N1}"', 'src="qr-1.svg"'),
        (f'src="{N1}"', 'src="qr-1-0000000000000000.svg"'),
        (f'src="{N1}"', 'src="data:image/svg+xml,x"'),
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
        check(html, [N1, None])


def test_check_refuses_an_image_outside_a_drawer(tmp_path: Path) -> None:
    html = built_html(tmp_path)
    bad = html.replace(
        '<p class="crypto-note">',
        f'<p class="crypto-note"><img src="{N1}" alt="x" width="1" height="1">',
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


# --- cache safety: a file name changes whenever its address does (#713)


def test_file_names_follow_the_address(tmp_path: Path) -> None:
    first = build(config(), tmp_path / "a")
    raw = config()
    raw["support"]["blocks"][0]["coins"][0]["address"] = "bc1q" + "z9" * 18
    second = build(raw, tmp_path / "b")
    one = sorted(p.name for p in (first / "support").glob("qr-*.svg"))
    two = sorted(p.name for p in (second / "support").glob("qr-*.svg"))
    assert one != two
    # the unchanged second coin keeps its name, the changed first coin does not
    assert N2 in one and N2 in two
    assert N1 in one and N1 not in two


def test_a_rebuild_removes_the_stale_file(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build(config(), out)
    assert (out / "support" / N1).exists()
    raw = config()
    raw["support"]["blocks"][0]["coins"][0]["address"] = "bc1q" + "z9" * 18
    build(raw, out)
    left = sorted(p.name for p in (out / "support").glob("qr-*.svg"))
    assert N1 not in left and len(left) == 2


def test_qr_files_are_immutable_and_the_page_revalidates(tmp_path: Path) -> None:
    headers = (build(config(), tmp_path) / "_headers").read_text(encoding="utf-8")
    for name in (N1, N2):
        block = headers.split(f"/support/{name}\n", 1)[1].split("\n/", 1)[0]
        assert "immutable" in block
    page = headers.rsplit("\n/support/index.html\n", 1)[1]
    assert "must-revalidate" in page and "immutable" not in page


# --- known answers from the reference `qrcode` package (#713)
#
# Produced once in a throwaway virtualenv with `qrcode` 8.2 (error correction Q,
# no border) and stored here; the package is not a dependency. Each vector is
# (version, mask, text, rows) with each row as hex, most significant bit first.
# They cover versions 1, 5, 9 and 10 and all eight masks the encoder picks.
KNOWN: list[tuple[int, int, str, list[str]]] = [
    (
        1,
        0,
        "z9CPVNPkNa",
        [
            "1fd17f",
            "105f41",
            "175c5d",
            "17555d",
            "17545d",
            "104841",
            "1fd57f",
            "1900",
            "d615f",
            "1b3fd",
            "1b65bd",
            "1b923",
            "8eb54",
            "1a49",
            "1fd93b",
            "104c33",
            "175b2c",
            "174eaa",
            "1756f5",
            "1053b2",
            "1fc67f",
        ],
    ),
    (
        1,
        1,
        "E1Ki3ylOjt",
        [
            "1fc77f",
            "104241",
            "17475d",
            "175d5d",
            "17415d",
            "105c41",
            "1fd57f",
            "1100",
            "c4d68",
            "1728c3",
            "137217",
            "1bb38a",
            "15d38d",
            "1c54",
            "1fca91",
            "1047a9",
            "17482a",
            "174cb4",
            "175cd7",
            "105130",
            "1fc4f1",
        ],
    ),
    (
        1,
        2,
        "aQfDPrAJ7",
        [
            "1fdb7f",
            "104941",
            "174f5d",
            "174b5d",
            "17575d",
            "105c41",
            "1fd57f",
            "b00",
            "fef31",
            "179a66",
            "1b6c4e",
            "b8a0e",
            "7dce0",
            "19ad",
            "1fd4c2",
            "10563e",
            "175225",
            "1753dc",
            "175d44",
            "1053ec",
            "1fcd2e",
        ],
    ),
    (
        1,
        3,
        "ffOhq4",
        [
            "1fc77f",
            "105241",
            "175b5d",
            "174f5d",
            "17465d",
            "104c41",
            "1fd57f",
            "300",
            "ecd06",
            "eb22d",
            "15edff",
            "19928b",
            "196296",
            "1f84",
            "1fccdc",
            "105bed",
            "174a6f",
            "1750a6",
            "1756a4",
            "105361",
            "1fc71c",
        ],
    ),
    (
        1,
        4,
        "mlZGEONYlg",
        [
            "1fc17f",
            "104841",
            "17595d",
            "17465d",
            "175a5d",
            "105b41",
            "1fd57f",
            "e00",
            "95eb4",
            "2a0aa",
            "1f449a",
            "7baa2",
            "1afbbc",
            "1702",
            "1fc21e",
            "104781",
            "1754b6",
            "174d4f",
            "1749d4",
            "105710",
            "1fc6e5",
        ],
    ),
    (
        1,
        5,
        "A75Uyi",
        [
            "1fd17f",
            "105c41",
            "17475d",
            "17495d",
            "174a5d",
            "104841",
            "1fd57f",
            "e00",
            "87583",
            "1c9b5e",
            "13e998",
            "1e0f16",
            "194c43",
            "13cc",
            "1fd56e",
            "104fbc",
            "174f3e",
            "17491c",
            "174457",
            "10542c",
            "1fceba",
        ],
    ),
    (
        1,
        6,
        "tjfIZ4SOc",
        [
            "1fc37f",
            "105241",
            "17435d",
            "17575d",
            "175a5d",
            "104c41",
            "1fd57f",
            "1900",
            "bd5da",
            "121e90",
            "54a8b",
            "141fa4",
            "edab9",
            "13ec",
            "1fc350",
            "10535f",
            "17534c",
            "175de8",
            "1747b7",
            "10571f",
            "1fc9ac",
        ],
    ),
    (
        1,
        7,
        "76dEyTz",
        [
            "1fd57f",
            "104841",
            "17535d",
            "17595d",
            "17445d",
            "105241",
            "1fd57f",
            "1400",
            "af3ed",
            "31361",
            "1e6883",
            "159f38",
            "1bc543",
            "1603",
            "1fd4bc",
            "105ea1",
            "174d65",
            "175a0b",
            "17493d",
            "1050d0",
            "1fcf02",
        ],
    ),
    (
        5,
        0,
        "hivZOflJPgGflANktuKs5s9fWDPcEiXuQhkHH3AZGVT5SMK0YW0Cf0jVd3",
        [
            "1fdec93b7f",
            "105bf26b41",
            "175359475d",
            "175dc0725d",
            "17576f405d",
            "104d2bb641",
            "1fd555557f",
            "19cd9b00",
            "d6a7c8f5f",
            "3b866b504",
            "d66619c3f",
            "6a22db8f0",
            "f71b511c4",
            "1205fed78f",
            "148cfc537",
            "694c6de63",
            "1456825ad2",
            "1d2863194f",
            "116c051c3b",
            "63a1d9f83",
            "1fc98b11f8",
            "1074b9ce",
            "1ec6ecd5c7",
            "1919b9c941",
            "12d9863979",
            "8b651bb4f",
            "146cab059b",
            "e03069a68",
            "17c2fed9fb",
            "17997b1e",
            "1fd49b895f",
            "104b911910",
            "175b8a01f9",
            "1746b2bd01",
            "1751581435",
            "10595acd82",
            "1fc60c21bb",
        ],
    ),
    (
        5,
        1,
        "V9fUAdFWg65jxgp4fcInvw8eOiB6kCdfSvNZFlGpAq6LdToDYYuZ",
        [
            "1fc867697f",
            "104f9cad41",
            "174863cc5d",
            "175590fd5d",
            "174cde865d",
            "105c6c9c41",
            "1fd555557f",
            "1fd00b00",
            "c4b777b68",
            "786f850a9",
            "77c10e35d",
            "396fd469b",
            "ff09d514b",
            "938c7792b",
            "19e786ef8b",
            "1408355280",
            "13dfb5e073",
            "1128b1cb6a",
            "1d5a4eb4d",
            "ca8d70348",
            "1661d33b59",
            "122be0112a",
            "3c04a3fad",
            "11bd7705a8",
            "1461a23e59",
            "4298735ec",
            "1c4eba22f1",
            "2160e35ab",
            "1846fa87fe",
            "19dea717",
            "1fca303b55",
            "104491b71b",
            "174bd6bbf9",
            "17404491ab",
            "175202909b",
            "105ea437d8",
            "1fc53f0199",
        ],
    ),
    (
        5,
        2,
        "og4ewSE8mZs7BZrtUEbnOKZXuFmahAefYVMJapDDMFuucHACwqixN7W",
        [
            "1fd3a4d77f",
            "104d674341",
            "17424bd45d",
            "174881dd5d",
            "175e3d0c5d",
            "10570dcf41",
            "1fd555557f",
            "8f3c300",
            "fe9b27a31",
            "171bfcafac",
            "1fd35452fb",
            "a0a6da2e3",
            "1155454ce9",
            "60dec79a6",
            "cf16d4a67",
            "198bad3010",
            "a5aaad4dd",
            "d06158720",
            "11c47533eb",
            "188f5ea122",
            "4f692f4d3",
            "1c0124c361",
            "19e65d9f43",
            "8361ec4e2",
            "1ad34757fd",
            "1e04628d29",
            "154559c7f3",
            "151b71c681",
            "17e39645f8",
            "1ea3ef12",
            "1fd5759357",
            "1057aaf118",
            "1750dcbff4",
            "175b39d4e2",
            "175c8ac5e1",
            "105e6fe291",
            "1fcc3a542b",
        ],
    ),
    (
        5,
        3,
        "1HH026d1W0NIMJ64dMEiya8Lk5Krj5WWFhVOt04Bavo5Uy4SXXr9fATDU7",
        [
            "1fc5ff747f",
            "1056fb3141",
            "175d13195d",
            "174a09cb5d",
            "1749ce925d",
            "1041f1d541",
            "1fd555557f",
            "f8a6500",
            "ec87a8d06",
            "1d8347c1ef",
            "b5947f598",
            "203645d36",
            "12d6ef5557",
            "128ce3b71",
            "16eada88bc",
            "70bc5e1e0",
            "ddd131210",
            "17380fda4f",
            "ce123d329",
            "7ae8e74ab",
            "d6bd40391",
            "9b5c3a5c2",
            "13ec00a4dc",
            "128de46e3e",
            "1ece2147e3",
            "db5208279",
            "b41679514",
            "178ccff400",
            "75de08dfb",
            "19a89513",
            "1fc2365759",
            "105492a71b",
            "174e8505f5",
            "17563c8342",
            "1756e06272",
            "1052261a5c",
            "1fc015d4eb",
        ],
    ),
    (
        5,
        4,
        "ewQd2RJ6dQDMFZe9600DDXaMNMOk31NxxdaP1uNFFLm2cXMnD55OzTaJu6wd",
        [
            "1fcb62ff7f",
            "104c104c41",
            "175560b75d",
            "174f2bac5d",
            "175bde825d",
            "10583ad441",
            "1fd555557f",
            "caf9100",
            "957c0dbb4",
            "1718838bb5",
            "667dabe0c",
            "27a3f0a4",
            "95ee4f361",
            "11abe60e9a",
            "6cc6c2386",
            "1e8ed5f794",
            "195a25c9cf",
            "b2b0e6d74",
            "a774e2704",
            "f1e92c68e",
            "b55ce78ee",
            "1cad02cd1f",
            "1dc4a26728",
            "eb8109105",
            "75ac8907d",
            "1e1073cd9e",
            "d0b4a696",
            "72dbb92f5",
            "1962d4b9f5",
            "1b2b4318",
            "1fc4883750",
            "104fb5a517",
            "175c65d9f8",
            "17497f06ca",
            "1748007f2a",
            "105801e30e",
            "1fc583da6f",
        ],
    ),
    (
        5,
        5,
        "Ir55wt7t1KcxbtTFWXs3AtJa43475zdQeaG8ONfeS4xelAGu3M",
        [
            "1fdac1667f",
            "10517a3c41",
            "174f25a75d",
            "174560a05d",
            "174d1cb95d",
            "1041664d41",
            "1fd555557f",
            "27a3b00",
            "876270783",
            "ea8c93574",
            "145b4bc7f3",
            "1bb6d430c8",
            "1ce751276e",
            "4a3646da5",
            "dd5d78a63",
            "70abd35bd",
            "13f6488d77",
            "119c665ba9",
            "13db4462f9",
            "1c217910d8",
            "1d69fc4ff1",
            "af7c075d",
            "8eb9a42ab",
            "31bc445e9",
            "2573c6fc4",
            "1581a5f54a",
            "17690f90e7",
            "141dbb42bf",
            "11ed3b55f7",
            "1dbda313",
            "1fdc622d59",
            "104bc2b511",
            "174c6ffbf7",
            "17412d42ce",
            "174b1dfca5",
            "105b506069",
            "1fcb871135",
        ],
    ),
    (
        5,
        6,
        "6GkGrdIixMxRg9YUseDMGAZrz96RE7zRE2wfhcSzIf9R9jCjKBgMoWjCPst",
        [
            "1fc624827f",
            "105b7d9d41",
            "17483fdb5d",
            "1752c2995d",
            "1755f08d5d",
            "1043c2ac41",
            "1fd555557f",
            "1ac31800",
            "bd0f9adda",
            "172d4b3a72",
            "1d54c5676d",
            "1b89bb741c",
            "126034414d",
            "189bb743bb",
            "9cce253cb",
            "bbf57167f",
            "1dea054a5a",
            "8a32224ec",
            "9d81c26d7",
            "b27337b1a",
            "1a5ddd5fe0",
            "13170e00b0",
            "17f84d3aed",
            "11036549d",
            "16c8a78376",
            "1c1e61b512",
            "1edb01da71",
            "133b3c360f",
            "17f98cc3fd",
            "15ed011d",
            "1fcd04a551",
            "105c970f1b",
            "175c96f7f8",
            "17579c42d6",
            "174a3f3cb7",
            "105b3b551f",
            "1fcf984179",
        ],
    ),
    (
        5,
        7,
        "y46nUaa4kEjikih44VUeKRjnvClMds7dvTjBXegJcPvEz56YWn1",
        [
            "1fdb059e7f",
            "10401ef341",
            "175a6c335d",
            "1755ae775d",
            "174f6c8e5d",
            "105feedf41",
            "1fd555557f",
            "1b91c700",
            "af00231ed",
            "e20c79fc1",
            "d6a60a89",
            "1bbe7dad5b",
            "9c1689af9",
            "1ea3b01a29",
            "1fecc2b95f",
            "129bc8080a",
            "15621b71dc",
            "194e81f53",
            "147165f0c7",
            "23ab095ad",
            "bf1789f5b",
            "180e4f11c1",
            "acdb551ad",
            "50d8de651",
            "3e515b170",
            "b9abdfb65",
            "177de6b861",
            "88b099e32",
            "1c563b59fb",
            "17dab711",
            "1fd5124957",
            "105e68b11c",
            "174cf86dfa",
            "175463ef11",
            "1747a14111",
            "105e14cab0",
            "1fc40293ff",
        ],
    ),
    (
        9,
        0,
        "wxsU8TrXetDZWiFoN6CM4HGJbomBMga0dHHL5STPI68iUdNgKmB9yyuRPcKGRyTjyjBwhJKHIoazIFZBjhv63bmEHAJs1u6KYFn66j6zIMqwtKriLv",
        [
            "1fdbdafaffd47f",
            "105c2617b6ae41",
            "175f394295425d",
            "1753d3a8c4c55d",
            "175900ff86c45d",
            "104da611873c41",
            "1fd5555555557f",
            "159191c3f000",
            "d667f3f377f5f",
            "1fbcf7ce3220b3",
            "12cf0dc9744dbb",
            "b5f823cacf52",
            "947619ebf5d15",
            "4343da008017e",
            "ad87e39ce5873",
            "1618dfa46c6da1",
            "6c755dc73f6ba",
            "cb7317122023d",
            "e28868bf84c3",
            "e21ffbaba6ba3",
            "12d0951af230b1",
            "1b074a14934002",
            "1b7b928af67261",
            "189efac52d22e3",
            "13f3837ffff1f3",
            "1d126651811116",
            "195e47d50f8b53",
            "17187a31818b14",
            "5fedbff1b3ff4",
            "191e80dfc30983",
            "4f8e4d09c3cdb",
            "c2270643fc1b3",
            "1737e9df3f459",
            "1d8c938af1a2c7",
            "976b54456ba43",
            "bac2f95daac4a",
            "2cf9db11f756b",
            "129469d1c113c3",
            "4dc6b62f936d7",
            "506a5922dc873",
            "557bb3a79f8f6",
            "1d8a6613c333ed",
            "1bdc751e8b1e79",
            "c11589bd7ac60",
            "27ba3dfb3edf1",
            "16d151f5311f",
            "1fdddcb596695f",
            "1046afd132ad13",
            "1750ad3ffbf1f3",
            "17466acd42301d",
            "175d31d3f38517",
            "105c29dbb4a432",
            "1fc1a79efdfd2b",
        ],
    ),
    (
        9,
        1,
        "enQ0GHSmmZRQCaeNBVMYR62wcqgRIoqkXJ5Ksa8nb2O1Tl5bIDo2GARZTYqSuHjveGmv9sOL1TQjGdu2P49lWVkv2T3tsVL5jZznu0dmP5OUGyUvH9Cn13zkWU",
        [
            "1fc47ffc65647f",
            "104d4230247641",
            "17480b7505aa5d",
            "17534bc842655d",
            "174916bf35445d",
            "1052b0313cac41",
            "1fd5555555557f",
            "1d1ff1bbd000",
            "c44d9bf53d368",
            "14185d5a0b2a92",
            "1f5dcd29100809",
            "990b097920a1b",
            "125ba00255b25e",
            "436428d83aa28",
            "1a672e5da55e29",
            "1a52b6c72b519",
            "144ab577f57658",
            "15998300098a31",
            "1b5648160ff31d",
            "39d876430f8d1",
            "e60b3ae58b952",
            "1832b00d39da84",
            "1fd2ac904cd859",
            "119859c650caba",
            "bfadf7f155df2",
            "f1de0516abb12",
            "135ede35b54b51",
            "17136fd1e32519",
            "7f4f49ff113fa",
            "5810529ab9b8a",
            "ef2dbd7f33765",
            "b03f909361519",
            "127c05fb599f3b",
            "1a3bb9ace80ae3",
            "1ce868039d26e9",
            "33df2de03bbe9",
            "184026a6d1da16",
            "158ac44b1a92a5",
            "65e0cdbd6706d",
            "8b2973c1411fa",
            "10d5530255560d",
            "c8875c539b005",
            "1bdcd387577165",
            "c39d750cda1ba",
            "25c561f19d7ff",
            "1fe6d1ee3918",
            "1fc6e3351f1755",
            "1049593192e11a",
            "174b43df1113fd",
            "1746484e09ab9b",
            "175c83991a4d5d",
            "1059bc86163710",
            "1fc5a261d75321",
        ],
    ),
    (
        9,
        2,
        "llCwgpFVM7J9xsGXEQiSHnGUbneObEpUEwk20qEPUsUC32VwK1RmNm1fNo0GasttwiWtCOTnVdF5LxKAdED194KnGsgjjiOis3Jzlv5B0CjroDuC8inpOH5e6Xnk5",
        [
            "1fd570f778a47f",
            "1049b4f487a641",
            "1748220c5db25d",
            "17460446ef4d5d",
            "1755f03f718c5d",
            "105f8c51fd0441",
            "1fd5555555557f",
            "d6a5105e500",
            "fe27a3fa74731",
            "199af836c3be0a",
            "dd313845b8bcc",
            "12b0e4f835ba68",
            "166ceaceefc150",
            "c872148f7ae1a",
            "cee9138bf2b0",
            "f0133142e26ba",
            "ac5c21061cb7f",
            "f9b0ca3d0ac3b",
            "67f3df2046540",
            "133d182fb5eef8",
            "744bfa2254731",
            "139ec150a3bc33",
            "e63b70e6f561c",
            "1cbd4a32c8fdc8",
            "11f38a5f278df3",
            "31b99d1b82d1d",
            "b5dead585c756",
            "1b109db1003d19",
            "7f13d9ff74dfe",
            "1f859dc9d2857f",
            "12f953167f68a0",
            "109df531418578",
            "1dca6047e38586",
            "abf74db618df9",
            "473d444ebc738",
            "15a28fd40ecada",
            "27f357ced4018",
            "a05c669229e79",
            "1947cfcc1e4418",
            "1686b928f3856b",
            "64b9dd4eb4d9c",
            "101223d082be34",
            "1bf7849c75449c",
            "c376a8ef80b52",
            "2571a3fe3cdfe",
            "15d791e8b718",
            "1fd1e9d5824b5c",
            "1059ff31fd5b19",
            "1759557f8b69f4",
            "1750446472bf69",
            "17541926c7ecb8",
            "105367de0a700a",
            "1fce6c0f2dc940",
        ],
    ),
    (
        9,
        3,
        "oG7364Gw8FYrZN1b8JZwpI8EttiqCosGVuDkYr9RyNV5UxmDFlWUSY6Z7H0KPkPlTweY1tmRxXyXL6beAG6Tx0qr3uoEdgq5bQHkBGSRT5CxPmNSVUiUsan",
        [
            "1fc4e4a6f3b47f",
            "1054a5391b8e41",
            "17557eb71ce25d",
            "17465928c34d5d",
            "174bdd9fba6c5d",
            "10440e31a07441",
            "1fd5555555557f",
            "28191817d00",
            "ec1709f44d906",
            "168cd190d09c28",
            "1778c0cef4a436",
            "5bac5538b9062",
            "175444ecefd870",
            "1d054ff22cd1bb",
            "ee19502756b29",
            "1f940d30da970b",
            "868dea1f4c3cc",
            "83e3402ae1878",
            "11476c2cd00b78",
            "1d8cf73c607b4f",
            "1b6059d74c112b",
            "3e6618b33c13",
            "1a735c72a2c5de",
            "1401bda95ef3c3",
            "bfcca5f830bf0",
            "1b134e718ae312",
            "95a78d51dab53",
            "b15c8114d891b",
            "11fb3d9fec6ffc",
            "1b30bea67f0e65",
            "1cf175c2f2c73c",
            "b8f2802b5204e",
            "854e6f50633eb",
            "5131c3c518d77",
            "17e215e96358b6",
            "b9bba7f1f17d1",
            "17dbe0f869cd22",
            "b8fa9912acbac",
            "1efa81ccc18dff",
            "e96631eb75738",
            "1360d2f9d86c9b",
            "1d16af15ad2161",
            "1bc40705ba8934",
            "c375f9783efcf",
            "27c6d5f8e1bfd",
            "13d2f189871b",
            "1fc1e855180756",
            "1052a09192bb13",
            "174621ff2fc5fe",
            "17519d983bd03f",
            "175b881ec65427",
            "105bf63b99d612",
            "1fc1c4bf3d213a",
        ],
    ),
    (
        9,
        4,
        "lgoyo85rb2xf0S3DWYG3JPVVauSYGXQuNrT6sgDjG3dbnVSCuKwEfsPUExumcMI5JFRu4JU8GVROFVdPowZQKg5oM864AcyS9XoVPJ5CLL98UgWNf68OnM1gLxBjkUTb",
        [
            "1fc78e07e85c7f",
            "104eaa7b01d641",
            "175e275a67aa5d",
            "1749b819ec955d",
            "1759061f9b545d",
            "10508b9163ec41",
            "1fd5555555557f",
            "e0791176900",
            "952f15f5be8b4",
            "179a4ee0e95f8c",
            "1143f81c283f4d",
            "17bfaa5e84f309",
            "d701bb09b6272",
            "1100eb245acd86",
            "13caa60876e6f9",
            "11222cd627f00b",
            "f4c61b917e4fe",
            "d35319961cf81",
            "11c62a5463c90d",
            "18329d0da8315b",
            "1d4c6421dbaaf0",
            "1b12ef0e7a752b",
            "15fc42f41f4733",
            "1b3fb144bc014a",
            "5f9dcdfb78dfa",
            "f17855109ed1a",
            "13531c958bd951",
            "13154691c11119",
            "9f3525f7969fd",
            "11bc5551a84e31",
            "1e7f158d48f8f1",
            "16b5034780af2b",
            "beac3b65f2698",
            "1d95663fc8c6f7",
            "12cdb31e2cd54f",
            "d0184e43d9bf9",
            "5e6db4c5b6705",
            "12b026bd895c70",
            "feab46d0bd055",
            "7b206bc678108",
            "1f4e719f9f2a00",
            "4aa4b706a6db7",
            "1bcb16a1a69859",
            "c241876444bd9",
            "253c77f13ebfc",
            "1473914bcd18",
            "1fc8db15c8c155",
            "1049e8316d7d18",
            "175a4b1f1361fc",
            "174c3dc168fc34",
            "174160a1b6f0ce",
            "1053fa3e67625b",
            "1fcf18ee5fac26",
        ],
    ),
    (
        9,
        5,
        "C3V4nIuzH8UB4xEqUFfLTQWyGf0ZTGbaGTzWcFDrsyI7SHkGF9ZuEm0U4Itp59qUSeZOjfkoES3Qf4gCV5hUCk0knsGxeszm7sGxkloYYNIfgRZlGj11VQEhCDnkef",
        [
            "1fd6859f746c7f",
            "1056e2fd79c641",
            "174384c49ea25d",
            "1744269e37fd5d",
            "174f0c1f91045d",
            "1044ffb157dc41",
            "1fd5555555557f",
            "836d1dac600",
            "87f465fa5c883",
            "fb29f86dfcfdd",
            "ce9719f6035cc",
            "f9a9ae4bbdfba",
            "1d4f201559969f",
            "10038d26618e9a",
            "3474d4999c208",
            "94f7b5c593ff",
            "2673317af41fa",
            "1a151eb0c3ae38",
            "19cf2665a1eb19",
            "1619ee3954d3b1",
            "1f64dad9ef49fd",
            "80916e26fe7f6",
            "e640d71e37b8e",
            "1aa88e64c85869",
            "9f0035f51d3fe",
            "519d631483f13",
            "d5c29f593ff58",
            "319a011f6f116",
            "19fb119f4bc7fc",
            "602e671a28f72",
            "3634681b1bd71",
            "624b58e1231b3",
            "4c9001121cdce",
            "1821f13e4edc3f",
            "18f70985ddc808",
            "12335afbfab7fa",
            "f73086015d69f",
            "71a2a18bbbf78",
            "166ebbaa840d2c",
            "1b0d9da2a5ec5e",
            "1ad35b868f803b",
            "141c1a7b1badb9",
            "1bebe231965d59",
            "c28a1c77e0b11",
            "27666ff4dcdfe",
            "187031f8e516",
            "1fd98f552b295c",
            "104488118b6f1a",
            "17437b9f5d5bf9",
            "17427818b9ac6b",
            "174c6366847278",
            "1051143a1c07ed",
            "1fc587d12bc4f4",
        ],
    ),
    (
        9,
        6,
        "8jXdl4GwjMBcj7GbYgvjL6K4mLBR8u2AXXK4UwYw5HWbQaoqgpDRGCqEjleQmDDIexGhkvTmLTiHlFiEOWPWH6B0eJDUGftnc3Xrw3mOOv6IVI3",
        [
            "1fc1d53525cc7f",
            "1052b905402641",
            "174506d777225d",
            "17510f48fff55d",
            "1758789fd1e45d",
            "104d0831657441",
            "1fd5555555557f",
            "1f9471281c00",
            "bd65edfcb1dda",
            "14294f67ceedc7",
            "2c79cf7f9417c",
            "178516f509975b",
            "54a68c699415b",
            "3820577fc5f1a",
            "17c895120d8451",
            "9bbfad844e11c",
            "10cdb046750092",
            "120d69d6ee26fe",
            "13eda0a37b00a3",
            "189463e083df97",
            "14f7342f2911b0",
            "62afdd7b75dd5",
            "1cee3eaaf7edf9",
            "f38f4ec854138",
            "ff5cf7f1997f4",
            "5115ff1cb4d1f",
            "135f46b5849553",
            "1b14c731179916",
            "19f68b3f21c9fe",
            "7bf634f5fb095",
            "14e1ee5249fdfd",
            "2168e26747ec6",
            "f596d5daf166d",
            "1537d059adff71",
            "660f1665d7c0c",
            "58e8969be65d9",
            "2656ea791bb59",
            "5a1e0b2a97efb",
            "1758cf870ae649",
            "612365f13ac0e",
            "12c87020f1413d",
            "1320f73d47a689",
            "1bed3570425561",
            "c3c2d1231a425",
            "275263f47fbf7",
            "1f65b175fd1c",
            "1fc59415e0615c",
            "1052801153971a",
            "175065bfd99bf1",
            "1755d885537fa5",
            "1745e98ca28d35",
            "1051500d21a45d",
            "1fc60b43b82a6c",
        ],
    ),
    (
        9,
        7,
        "nVEel1si2JIzVItud7cRUdfaxOpEc8AKLCff8Tfsl9p2o1KlkJ8l6T5wwYNc2kV5ULHzYbooMuFrP7iwWwah5GB6TVRzwKUR4F4qYUvmViDIUtdRMC",
        [
            "1fd5538fcce47f",
            "104dabf2807641",
            "17594dbd22aa5d",
            "175c4442cc455d",
            "1746e19f986c5d",
            "105b9ad1172441",
            "1fd5555555557f",
            "19e811d5ce00",
            "af1667f6fdfed",
            "2cc0d2120206",
            "156e1e729ddf9e",
            "12315351184855",
            "3c856fa3f5130",
            "f1211b5be930a",
            "dfdc1f1f957b",
            "ca4197a6dfd20",
            "15c0145d76cb6",
            "1d161448927907",
            "1bff2b645c732b",
            "18b1cd0c86d2da",
            "3e583cf6c70bd",
            "c280425a37032",
            "1ce5ec4e6e588",
            "e3871b94af91d",
            "5fc8f1ff333f9",
            "131454f1e4811f",
            "195bc775922b57",
            "1114a3189db16",
            "3f2713fffeffe",
            "119ba3285941e0",
            "1358446a197ee3",
            "1223486e6ecaa8",
            "1eb400ee1342b",
            "153aad1be0b381",
            "d61d15b5b28ac",
            "190bfbca5079ef",
            "1b73809cfbb51b",
            "1a89aa3f34a3ce",
            "10cd8228d6b9e3",
            "14a5c638d17341",
            "1975999bb3677d",
            "6a0c4e6b2598a",
            "1bfdc0f750917b",
            "c21cf4b11b329",
            "24d79dfa16df1",
            "11ccd1353911",
            "1fdba1d5f27b56",
            "105bb911004116",
            "1744423f9371f7",
            "1750deebf79298",
            "174b6ee47628df",
            "105f9137589822",
            "1fc484a9bd43ba",
        ],
    ),
    (
        10,
        0,
        "j4f6Z0BhWQdQ5z1rhRrAJUH6yLVI4B5FEuCOytDwsolomxoiCY1vUbCxHBXJWeJmJ4AQl2Uta2s2y92UONDhJRC1zAIyXngK3DX06B4WlFDZQhs9yzdpsgdf1LdYvRWVHFyGKmYM0",
        [
            "1fd0064c2fcce7f",
            "105aa5e9b888241",
            "175e2674aee365d",
            "1754429a10e825d",
            "17508ec7ffeda5d",
            "104d61944e2fc41",
            "1fd55555555557f",
            "1bdd2c72bc400",
            "d6c9767d6f125f",
            "205ae9b738863a",
            "17d1f6d492d95cb",
            "db47e7d5e8ef3a",
            "18c9c605fff71b8",
            "2958b060390825",
            "1a72699d0b95bb3",
            "163b35636f110e0",
            "1c3a70fe7d5070",
            "505012ac03300d",
            "19579b2ff95da4f",
            "148083725ec74d3",
            "6e8e09807f74f9",
            "baaf43850a20ae",
            "e627bdc35b32b8",
            "122e1172f966192",
            "7d1be990f63e91",
            "2229847c8b421e",
            "1ff89477efa65ff",
            "f1cc4645891711",
            "115e965d73fb953",
            "71a38644300119",
            "bf37c97f38e7f3",
            "1f07cb3dc92cbb0",
            "6a8d565b7f564",
            "17be6834702a9a3",
            "bf4492e97ea9f7",
            "1a80aa41c9a7272",
            "87af30bf13b5b4",
            "12993971933320d",
            "962a5ad64d1147",
            "a2f4b0d9842963",
            "55cbe797d36ce5",
            "9b4768f38438c9",
            "1d4171a0956c53",
            "18358bbdec2db41",
            "14c60f73eff15d9",
            "a0a4909103301",
            "14fcdbb3df8f4fb",
            "1f056cf70822fa1",
            "6105afdb3bdf6",
            "1bb83c4b2331c",
            "1fd809555873753",
            "1045112c62b2712",
            "1750e06ff3f79f0",
            "174c13aa8303096",
            "17513935489e39d",
            "105d016ce48bf32",
            "1fcfebc3231f00f",
        ],
    ),
    (
        10,
        1,
        "rU0I670m5t6jkEwkwkyrNy9TvhQ9brlmjvaMlnaIgweQmboIh0PwIZ14lL3hZpmr7TliXZrPpfY1TMD6gYrW3526Hd78OxM34kQ0NVa9WSXfYXp2jCeD4BztZnR7kmUHYMJlUiD99uOShvW",
        [
            "1fc40e8a664d67f",
            "104663bd868c241",
            "1748d6aa3a8ce5d",
            "17528f8f5ac4a5d",
            "1748ba67e46525d",
            "1054c2347a47c41",
            "1fd55555555557f",
            "1a749c502b800",
            "c44055fdc51468",
            "178c67184928895",
            "86f55061066d05",
            "11bb6d0cca79870",
            "ecf55c7a1b1a71",
            "1a198f4570812a5",
            "ac28f5162a229d",
            "3aca8e5d6d1a38",
            "10d19eda8dd5c3c",
            "d1c11eb5a91a87",
            "13cfe77b02b1299",
            "da17c7f071e9cb",
            "c4585fad7d965e",
            "1f91b9805899aa8",
            "8e882876d574d6",
            "13a65eb524b2b8",
            "d58f2345cbdfb",
            "16a237563199abe",
            "7fca53fd7479f1",
            "1f179df44499918",
            "175dd12d5195350",
            "1110ffc7bbab15",
            "17f1e397e1d05f5",
            "c9373eb6366249",
            "11fe595195d5e73",
            "1e1b156ae9b882f",
            "ed75f569e9acf9",
            "41346c227fd7eb",
            "1d792a5ac971a88",
            "1a8e2f0ff2a888e",
            "1c72d9fe615f1fd",
            "199acd6a22d9df9",
            "13d167893f9d3fd",
            "149da324facb923",
            "116b3222bbcb657",
            "1c1e5101c45290a",
            "7aec27ad112b7",
            "2042f9a3aa02ad",
            "14c3aa8abde10e5",
            "1f3fdd2e22f6ac2",
            "4941c7fd55bf0",
            "18d6dc6199911",
            "1fcffe6571b4b51",
            "1048c6f45d3331b",
            "174c5507d5f97f9",
            "17444ff1d882aa4",
            "1750022e2e12917",
            "105a2277cf4bc98",
            "1fc760fbc9b163d",
        ],
    ),
    (
        10,
        2,
        "1pm2I13Q2VZs1RKAJKJbUvSvSipphTDiPlQwGiuC5dqwfUd9y6QgcKkSfIRawinrZaaSMKZuu4vQpx0JeuWrYgOIugxJzJ0BuayLSSHkuNNK7MnkQ7VAkvV4WYPxmzK819CJfTNiB65WkSPLN2d8W",
        [
            "1fd104ea71bc67f",
            "104ca061e218a41",
            "1749905229efe5d",
            "174c71b5a78c25d",
            "175e3767f29ea5d",
            "1055ef8458d0441",
            "1fd55555555557f",
            "8919443e2300",
            "fe6c117e342d31",
            "1d05ec56c11fb8d",
            "8ffa383b4e17c2",
            "149ee243fe16c3f",
            "e40cee99e20cc",
            "919ba29610fb21",
            "14d381caa8fe4e2",
            "1739efdcf5a0037",
            "1ad62e1db306c4a",
            "31316f3f2af3b4",
            "1e75ae0ccf4025e",
            "18be9c2c3a93b67",
            "16ed0c2e934e209",
            "14bdc6e8dabf33e",
            "ad8127557be5c1",
            "168a9b12e2e6f9c",
            "1c79949fe78a28c",
            "b33199df8ad3bd",
            "15f34eeff81cffe",
            "714cdec6876315",
            "1d5b4db56382159",
            "91315ac52ad112",
            "15f7ef0febfe5f2",
            "130c60edc8e746f",
            "4f834d6d346911",
            "30ce11bb18e380",
            "12f15c9225010ae",
            "f2ddea37d6e36d",
            "6c2cd937746d49",
            "15838b657097b8f",
            "361fe18ed08482",
            "a31c1a0c20523c",
            "3f103ecf14e0e6",
            "839e6ee498f386",
            "ff30a54076220e",
            "12a399db6e12acf",
            "14169f3ad8e089",
            "3bbfe3d128c944",
            "14d3c15d64b25b6",
            "1f09b0fa66428af",
            "454d67cd8a5f6",
            "12182c62af11c",
            "1fde8db54cb8d5a",
            "105978dc4c0611d",
            "175fa437e3865fd",
            "175e6d3833be29c",
            "175cc9350b4af84",
            "105240e3476778c",
            "1fc95e1e7fc6d66",
        ],
    ),
    (
        10,
        3,
        "FUsy0M16n2i9CW0OP3hm8XChGd0kI2L4eupDKpy3sszLTvZqhUDv1rXqGpthCNepuIq9mwDguvMRDJFLvXsjcx67TCYIt9qnrA0vXeLayaKTUiD2YAdSsO3HvT1dfoEWXjLq303T3fRrbVhS",
        [
            "1fc89110f38c67f",
            "105bff8e7c4da41",
            "17530e09142b65d",
            "17447f0a6362a5d",
            "17404a2fead0a5d",
            "1045a76458f8c41",
            "1fd55555555557f",
            "3739c4893900",
            "ec673ffd8f8306",
            "293d13243cd3a8",
            "1c7476228e59ad3",
            "182b3f1852f44a8",
            "9c6efb9f382acc",
            "91ef61cb9c08ee",
            "1fe45a7c684478",
            "13b50c430ce8e8f",
            "8d6dbd1986b264",
            "1c1aee47ce88e27",
            "14c2abcc6be2a6e",
            "5359b0aa4f0622",
            "f4280a656103f2",
            "1d1d277b12873a6",
            "56767645655164",
            "2857aa159598a2",
            "10fb4b878fa2c48",
            "1bbe6778bef18da",
            "1f9c8b7c6563f2",
            "111683bc76bb31e",
            "175aa3757eaf759",
            "f1122446d12d14",
            "ff19a17d38a3f6",
            "16bb1af2811e870",
            "a5452a2c67e196",
            "1515bc4b038c1c7",
            "d7f421c50db8c9",
            "39e6908a76577a",
            "1e2ddbe0de049a",
            "e128c40b9c3861",
            "10ceb1ed0e8e49c",
            "142f5ab451118ed",
            "cc499b339c3777",
            "1c88f5517dbbe51",
            "ee9b63436a8482",
            "1b808cd4f45ac91",
            "19e4736c5e7cde3",
            "d1babdfd38c924",
            "14f09acaacc4ee3",
            "1f1f97d56e23daa",
            "5abe97fb2e5f2",
            "11008c42e1b1a",
            "1fcf4e855acf35a",
            "1053a74c7db771c",
            "17435437f4633fd",
            "175ade746c0acb2",
            "17506e7bfbe13c4",
            "105e9a7ff2eead9",
            "1fc19242ae70d7c",
        ],
    ),
    (
        10,
        4,
        "qsXQqQJ5zUS3vC6O2jleWJfLd9xKbdNG1Ur4BKVEcSMvT1KmRGFje2VxLQ4WTxtjNyNCsGg48fGL5cNGRUd1ac2wfVhA3vGGC6WDyAwOE5ZSoJBdGzyS5cPC38T96Tfri1ygR8ckC3Mdocg",
        [
            "1fc7e6526b6fe7f",
            "1049f27eaf66a41",
            "17570993b68be5d",
            "174b7eac645225d",
            "1751da0ff05d25d",
            "105ef7c44f45c41",
            "1fd55555555557f",
            "db4dc7605e00",
            "95d6b77f261db4",
            "fb701a1a0fcb7f",
            "f7205f511b7d16",
            "f9169ae5b3bc90",
            "16cf6f3cb32fb66",
            "e9aea26285cb7f",
            "1472a4c4cefc57e",
            "1a31020e5af30a1",
            "172d5f8c7a92e7",
            "160a0c1bda4db7e",
            "12df677dfd18ab6",
            "2a292280b8f011",
            "d40911bb0792a",
            "32131a3f07fb67",
            "1d52b32f903212d",
            "537517dad38a38",
            "1f89162293912b",
            "602072c025ebf9",
            "1f0fd17f40d3f6",
            "7186c84538fd13",
            "55a127d5f03759",
            "1b147eac606f91b",
            "ffeab4fe3625fa",
            "159949f13c4f5d0",
            "14cb384ee7890d1",
            "c255e34f36d920",
            "960ef5b129d316",
            "16a2a8842d8fcd3",
            "15f154ed45cb0bf",
            "1839ed26ca7eb0b",
            "dd46002e67de9a",
            "223830cc8fd131",
            "a7fbc1621ad88d",
            "ad58ca6b1f8e8",
            "cc203bd3a0abaa",
            "11b9552dbd9bf99",
            "a4b667b1baf9a9",
            "58df84c206caaf",
            "14dda13a500ce96",
            "1f3ad88d2fa4750",
            "4a936ff7291f5",
            "19d7345add112",
            "1fcf65654af915e",
            "104bac0c5c91511",
            "175c7487dba57fb",
            "1746e71d1865bf3",
            "17469c2f90564d8",
            "1051573503b1590",
            "1fc5f2704709341",
        ],
    ),
    (
        10,
        5,
        "Cgiuxrwz5bQo2stWq1PG83iOnYfEcQpK1HvTgfcK08RdlDIO9vbD1Rv8MRG6WorY8VKhavVjsoiIXNAucdkBRU6BW3x3jA3zXQCnzSlWbQmGtwOqfhTndSOrvTLwyVKNgX4itk9Ct0H02TrWHdcssGF",
        [
            "1fd57256f75667f",
            "105b56ecfc46a41",
            "17440e4a1b6fe5d",
            "174d7b353ff5a5d",
            "1748d08fd1bda5d",
            "104675cc4ba5441",
            "1fd55555555557f",
            "34c0c4652100",
            "872456fcf06883",
            "1b9c2e624edcf51",
            "85f4316757cafe",
            "100cc3904f655c",
            "114f9e6b3d19e1e",
            "121e40a5702db35",
            "1d4654f576fe7ce",
            "e8c454dde68bd6",
            "10f10e9e47064ca",
            "33157e869afb2e",
            "c69497fadf7829",
            "1a32370cda9c3de",
            "17633e66d746f40",
            "1707e5949efefdf",
            "11da062e6165d05",
            "1f2024def00fc6c",
            "9c7f30bf15f3db",
            "192bf8ee8beb9f",
            "13fe08b7fbc97fa",
            "31146147e6d51f",
            "1d55da25554e555",
            "1112252459bcb15",
            "19fb0f87c1589fd",
            "14be65e969ede9e",
            "a6a96ee7f8ece4",
            "19038332ffcfc3d",
            "1b6453bb31214a2",
            "5b60bf0a2b3f5d",
            "44624ddc9b53fa",
            "1e969c24caaf8e9",
            "154b5c0535064be",
            "1ba694ac16ce09e",
            "a5f3a61c70096f",
            "f13b305fabc0a3",
            "1c59121cbaf347",
            "f2d845991370cd",
            "86aa25f0762c1e",
            "1e3aa8618eecd94",
            "14cc9f59fcdc49a",
            "1f07a2254e6ee6c",
            "6e5c97f111bf1",
            "1ec7445a3db13",
            "1fd20f0567e1154",
            "104c46a44278d1f",
            "174a9d17e9469fc",
            "17435a46429cb10",
            "174fc09951f19d3",
            "105a0f3c4ba6034",
            "1fcb70646742fea",
        ],
    ),
    (
        10,
        6,
        "8sPsZh1zNcXFR7aru7UoKIAEoXvZyYDxQeY8qzS31wxD18GMT7TK0pby7Sj9KzBNoMmlZ2X2F8GozI64Bm9Clfa92WMiiyr3ujGer4Uz5EE4Zq2rfGezEMq3QONtbVVLyCbgIlUYuu3U5g",
        [
            "1fc30ad09556e7f",
            "105b7bed9beda41",
            "1745dccdfe82e5d",
            "175bb9292717a5d",
            "175920b7f1c425d",
            "1040950c6b9dc41",
            "1fd55555555557f",
            "1eaf84732aa00",
            "bd7c81fc9d84da",
            "83158248f9f7ee",
            "1572bffafb5f471",
            "334d43d68ac136",
            "13c8f847f5b1b1a",
            "70358ab294dbf0",
            "1aeb92b25a1297c",
            "3a5946956dff3f",
            "7f5674cf147001",
            "910b10516b4d36",
            "8d2a47b1221819",
            "7021d8de54b4bc",
            "f5a679d5758e14",
            "832740ebedff71",
            "19426f7139fc884",
            "1e1bce1f58751a4",
            "1adda2ad6bb1b5b",
            "bbf4e9f1de6b7b",
            "13f3f9e7dc75ff0",
            "1d15ecac660a516",
            "1954d15d5fc9f53",
            "11f63ac6c16f15",
            "13fdec87f21b9f5",
            "a0e2c5bc44088e",
            "c45aeeae7104f2",
            "10199bad77df32",
            "1b636fe737806fb",
            "1d9b4b336687406",
            "f5ca30fcd916a6",
            "9a372665a5d06f",
            "cf961a6cb16108",
            "189f6680d4a49d",
            "1efa5ac3cc5654",
            "108297338636e13",
            "4455abe049741",
            "f2d1c97e5e940e",
            "136ff984a7d4dfa",
            "17bc06e62c7e7f6",
            "14f81a09ad994db",
            "1f220eb6057b92c",
            "7aed67c9db3f2",
            "1bc9bc696691c",
            "1fc9e71d532a75a",
            "105f5d8447faf1f",
            "175e1beff549bfb",
            "1757ccfadea6720",
            "174d46e73bf015f",
            "105e84d093cb34f",
            "1fc334aae7c086c",
        ],
    ),
    (
        10,
        7,
        "vcHCb5ArsDAnyjWGRGpekbjHoWZVD6RlLjGDUsRF3ewBTj22of3MDxhw6P2cnrQAodns6kDnE5XbzguI8MOMGMk3D484BjIOx01iUg82yyMVLpHsnZ8JGVwr4yodSvYAQgEje2ilKxzKP",
        [
            "1fd6907d3cfce7f",
            "104b52485f21a41",
            "17567a551f2b65d",
            "17588884b86c25d",
            "1747c527da76a5d",
            "1055943c563f441",
            "1fd55555555557f",
            "16a2944204800",
            "af3054fc0906ed",
            "aa37018d39201d",
            "14ff63ca4243f97",
            "13156d1ffe3c868",
            "187a2223773f233",
            "110cb6a25c80e0f",
            "166dfc7bebee9da",
            "1a8a24b8d98dfb0",
            "256b0b643a72a5",
            "1a2f3a36307024c",
            "10d1c984a63b003",
            "b340ac0fdac5b3",
            "194b191a1f768f6",
            "50339cce281022",
            "1a47cba4f70031e",
            "1c2195cc996ea93",
            "1050cbbcaf23179",
            "b38e292dea6405",
            "1f59cafed6cff6",
            "11166594728bd13",
            "1f56813d4f2915a",
            "31d80ec626b312",
            "9f58df7ce775f7",
            "1b1a19cb77c29c3",
            "19d66f9cc9bae48",
            "1591fd28c2010cb",
            "94ed3c18255859",
            "a0054527e703fa",
            "1954800577338ad",
            "1521405ff79a432",
            "6d5b9b9e2a089a",
            "133334e07050fa0",
            "175c925609a548d",
            "1f3653518a383c0",
            "9e7d579f9604c3",
            "eb4490e5663430",
            "14cefe53f5b0f01",
            "1e85694980332cc",
            "14f9d289a08716d",
            "1f2e3a395c1258b",
            "6b5027d7539f7",
            "1bdb1c67b251c",
            "1fd95ccd4932f5a",
            "105c3dec71a691b",
            "17440747dd295fb",
            "1752554510691f3",
            "174fa4f5b772fb1",
            "10512e7e8258da0",
            "1fc2233771d6f7a",
        ],
    ),
]


def _rows(matrix: list[list[bool]]) -> list[str]:
    return [
        format(sum(1 << (len(r) - 1 - i) for i, v in enumerate(r) if v), "x")
        for r in matrix
    ]


def test_known_answer_vectors_cover_the_versions_and_masks() -> None:
    assert {v for v, _, _, _ in KNOWN} == {1, 5, 9, 10}
    assert {m for _, m, _, _ in KNOWN} == set(range(8))


@pytest.mark.parametrize(("version", "mask", "text", "rows"), KNOWN)
def test_encoder_reproduces_the_reference_matrix_exactly(
    version: int, mask: int, text: str, rows: list[str]
) -> None:
    matrix = qr_matrix(text)
    assert len(matrix) == 17 + 4 * version
    assert _rows(matrix) == rows


def test_a_known_answer_comparison_bites() -> None:
    version, mask, text, rows = KNOWN[0]
    other = KNOWN[-1][2]
    assert _rows(qr_matrix(other)) != rows
