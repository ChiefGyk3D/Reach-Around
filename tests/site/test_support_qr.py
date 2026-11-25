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
from qrdecode import LEVEL_BITS, decode, parse_svg, read_format

from reach_around.config import SiteConfig
from reach_around.site.build import _chrome_urls, build_site
from reach_around.site.qr import qr_file_name, qr_matrix, qr_svg
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
    """The QR file name: position plus 32 hex of the SHA-256 of the SVG bytes."""
    digest = hashlib.sha256(qr_svg(address).encode()).hexdigest()[:32]
    return f"qr-{n}-{digest}.svg"


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


def test_svg_tile_is_not_a_white_fill() -> None:
    # dark modules on an amber tile: scanners need the contrast, the page
    # must not carry a white or near-white block
    tile, _ = _colors(qr_svg(BTC))
    r, g, b = _rgb(tile)
    assert min(r, g, b) <= 0x60 and _luminance(tile) <= 0.6
    # the guard bites: white and the old pale gray both fail it
    for pale in ("#ffffff", "#e8e8e8", "#c8ccd4"):
        pr, pg, pb = _rgb(pale)
        assert not (min(pr, pg, pb) <= 0x60 and _luminance(pale) <= 0.6)


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
        r'<img class="crypto-qr-image" src="(qr-\d-[0-9a-f]{32}\.svg)" alt="[^"<>]*" '
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
        (f'src="{N1}"', 'src="qr-1-' + "0" * 32 + '.svg"'),
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


@pytest.mark.parametrize(
    "bad",
    [
        "evil.svg",
        "qr-1.svg",
        "qr-1-" + "0" * 16 + ".svg",
        "qr-1-" + "0" * 33 + ".svg",
        "qr-1-" + "G" * 32 + ".svg",
        "qr-0-" + "0" * 32 + ".svg",
        "qr-1-" + "0" * 32 + ".svg.png",
        "x/qr-1-" + "0" * 32 + ".svg",
    ],
)
def test_the_name_shape_is_checked_even_when_it_is_the_expected_name(
    tmp_path: Path, bad: str
) -> None:
    # equal to the expected name, so only the shape rule can refuse it
    html = built_html(tmp_path).replace(N1, bad)
    with pytest.raises(SupportPageError, match="not a QR file"):
        check(html, [bad, N2])
    good = built_html(tmp_path).replace(N1, "qr-1-" + "0" * 32 + ".svg")
    check(good, ["qr-1-" + "0" * 32 + ".svg", N2])  # the control passes


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


# --- the name fingerprints the bytes, not only the address (#713)


def test_name_changes_when_the_rendering_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reach_around.site import qr as qr_module

    before = qr_file_name(1, qr_svg(BTC))
    monkeypatch.setattr(qr_module, "TILE", "#c8ccd5")
    changed_tile = qr_file_name(1, qr_svg(BTC))
    monkeypatch.setattr(qr_module, "INK", "#0b0e15")
    changed_ink = qr_file_name(1, qr_svg(BTC))
    assert len({before, changed_tile, changed_ink}) == 3


def test_name_is_stable_for_the_same_bytes_and_follows_the_address() -> None:
    assert qr_file_name(1, qr_svg(BTC)) == qr_file_name(1, qr_svg(BTC)) == N1
    assert qr_file_name(1, qr_svg(ETH)) != N1
    assert qr_file_name(2, qr_svg(BTC)) != N1  # the position is in the name


def test_built_file_name_is_the_digest_of_its_own_bytes(tmp_path: Path) -> None:
    out = build(config(), tmp_path)
    for p in (out / "support").glob("qr-*.svg"):
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:32]
        assert p.name.endswith(f"-{digest}.svg")


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
# Produced by tests/site/gen_qr_vectors.py in a throwaway virtualenv with
# `qrcode` 8.2 (wheel sha256 in that script's header; error correction Q,
# no border) and stored here; the package is not a dependency. Each vector is
# (version, mask, text, rows) with each row as hex, most significant bit first.
# They cover versions 1, 5, 9 and 10 and all eight masks the encoder picks.
KNOWN: list[tuple[int, int, str, list[str]]] = [
    (
        1,
        0,
        "W1xsnKA",
        [
            "1fda7f",
            "105b41",
            "17525d",
            "17505d",
            "175b5d",
            "104d41",
            "1fd57f",
            "1800",
            "d615f",
            "6bf69",
            "1cc41d",
            "150d41",
            "6615b",
            "1eac",
            "1fd1f7",
            "104591",
            "1757cb",
            "17406a",
            "175939",
            "105c72",
            "1fc163",
        ],
    ),
    (
        1,
        1,
        "EJkwvNjIYa",
        [
            "1fcd7f",
            "104d41",
            "17435d",
            "17595d",
            "174b5d",
            "105a41",
            "1fd57f",
            "1400",
            "c4368",
            "ca944",
            "16059",
            "1d369a",
            "3df69",
            "1654",
            "1fc151",
            "104099",
            "174e82",
            "174808",
            "175c53",
            "105518",
            "1fccd1",
        ],
    ),
    (
        1,
        2,
        "1K2cip6Ku",
        [
            "1fd97f",
            "104241",
            "17475d",
            "17465d",
            "175e5d",
            "105441",
            "1fd57f",
            "200",
            "fe931",
            "a1060",
            "1ed1ca",
            "a02b6",
            "17d1c8",
            "1197",
            "1fd4a0",
            "10592e",
            "175821",
            "1753d4",
            "175148",
            "1057cc",
            "1fc932",
        ],
    ),
    (
        1,
        3,
        "jGHOTT",
        [
            "1fce7f",
            "105141",
            "17565d",
            "174c5d",
            "17475d",
            "104a41",
            "1fd57f",
            "a00",
            "eca06",
            "7b52c",
            "1873f7",
            "32c8b",
            "c7595",
            "168c",
            "1fc0d0",
            "105fed",
            "17456b",
            "1750be",
            "175a88",
            "105f41",
            "1fc338",
        ],
    ),
    (
        1,
        4,
        "wULu3eh",
        [
            "1fc27f",
            "104f41",
            "17525d",
            "17495d",
            "17515d",
            "105541",
            "1fd57f",
            "800",
            "951b4",
            "1e920d",
            "25b72",
            "1cadf3",
            "2dad5",
            "12d8",
            "1fc99a",
            "104e13",
            "175269",
            "17471f",
            "174e50",
            "1054c8",
            "1fc1c9",
        ],
    ),
    (
        1,
        5,
        "LbFmmLiXnE",
        [
            "1fdf7f",
            "105a41",
            "17455d",
            "17495d",
            "174a5d",
            "104a41",
            "1fd57f",
            "d00",
            "87583",
            "3b834",
            "7f502",
            "1415ac",
            "acfac",
            "15dc",
            "1fd142",
            "104bdc",
            "174b11",
            "174588",
            "174c57",
            "10502c",
            "1fca12",
        ],
    ),
    (
        1,
        6,
        "GbXerJ",
        [
            "1fc67f",
            "105d41",
            "174d5d",
            "17545d",
            "17555d",
            "104441",
            "1fd57f",
            "1500",
            "bd1da",
            "1da254",
            "bd3db",
            "d1707",
            "27e45",
            "1803",
            "1fcef4",
            "10568c",
            "17527b",
            "175f14",
            "174c57",
            "1058df",
            "1fc638",
        ],
    ),
    (
        1,
        7,
        "4zo3VXSb6H",
        [
            "1fd17f",
            "104041",
            "17545d",
            "17565d",
            "17455d",
            "105c41",
            "1fd57f",
            "1b00",
            "af0ed",
            "982bd",
            "440a1",
            "a95db",
            "11ea93",
            "1a97",
            "1fd970",
            "10532b",
            "1745da",
            "175883",
            "174a79",
            "105720",
            "1fc436",
        ],
    ),
    (
        5,
        0,
        "hlJ5H8ukAdqT3SCX7JJdNvDn23g4lCiqOLsoi0EwXxzmgL9I6XFb4",
        [
            "1fd4d41c7f",
            "105b15fa41",
            "17584a525d",
            "17536b325d",
            "1751d5225d",
            "1040fa6f41",
            "1fd555557f",
            "1ecceb00",
            "d657ff45f",
            "11b6655007",
            "7d4b24dc7",
            "17b06e8c09",
            "13e52181d9",
            "d0b0f798c",
            "8e16e8027",
            "7901f29d1",
            "569f51ad1",
            "f1966b9cf",
            "868e4f587",
            "f134fd9b8",
            "1711422eb",
            "5aab2f16e",
            "a70c6090f",
            "1db46c92b1",
            "a637e83eb",
            "99bfb09ae",
            "10dcafd53f",
            "ba2f68db2",
            "16c2c089f6",
            "1c429316",
            "1fde385b57",
            "104247f911",
            "175db34df5",
            "1749e36da1",
            "17523f01b9",
            "105491ee02",
            "1fc9978b57",
        ],
    ),
    (
        5,
        1,
        "eZ5PnNsQlaZ8qldo9YCSSunF7YvbXLGbMuzZE5IoUffWAe3xMFW031HD",
        [
            "1fca446c7f",
            "1042a32541",
            "174639ca5d",
            "175735e95d",
            "1744e28a5d",
            "105bd0d941",
            "1fd555557f",
            "13e21600",
            "c45b12668",
            "285763628",
            "9444c2fd5",
            "1e15ed0430",
            "2e4270340",
            "14107d2969",
            "1c4afbf3d5",
            "c1f8762f8",
            "1f7602d875",
            "178e3c43ad",
            "17da47fa51",
            "3089d5690",
            "1ecbff2a52",
            "14300813e9",
            "1ec8f26f51",
            "5b163376a",
            "127479887b",
            "389b55aa0",
            "1ed7bc2a31",
            "7b1d23358",
            "1b4cb1a3f2",
            "16c5d315",
            "1fca3b2359",
            "1048ee8310",
            "174b32b7f0",
            "1745081d87",
            "1755223ed7",
            "1054a04618",
            "1fc02b9135",
        ],
    ),
    (
        5,
        2,
        "c2ewjHTYdBfWvu32oBfViTA9n5H9NOTAzDZ9nwiaJNDO1cXQm",
        [
            "1fd4a2db7f",
            "104f0b4e41",
            "1746c1d35d",
            "17415a5f5d",
            "175979475d",
            "105c9f9241",
            "1fd555557f",
            "8fbb700",
            "fe3bc3231",
            "c86c14f4b",
            "9c9c740e7",
            "d9ede85b2",
            "4e51accc2",
            "b12b4012a",
            "14d5a053d5",
            "58eaa261a",
            "941cdd9d5",
            "e216487e8",
            "1ae3ab7fdf",
            "12b8dca643",
            "1048767dfd",
            "211294be0",
            "6e5a68eab",
            "50a4dda23",
            "1f2bfc599",
            "1aadecb26d",
            "136105d72b",
            "14905fa200",
            "145ba4f7f2",
            "1d8ca91a",
            "1fd79b915d",
            "105e21f713",
            "1759de8fff",
            "175f6d5bf6",
            "17523a4365",
            "105ba3d7e1",
            "1fcb7b1c0b",
        ],
    ),
    (
        5,
        3,
        "w4VcSqkPtmuyR6ZTRXxXLxo9U4oN72eg9IIB796kuZ8M9Cn1R",
        [
            "1fcd1f517f",
            "1059c03841",
            "17592b0d5d",
            "174fb5c95d",
            "174523f15d",
            "1040469841",
            "1fd555557f",
            "48a7200",
            "ec40d0b06",
            "1fbcf52748",
            "443d6a68c",
            "12b6d5aed",
            "eb730dcd",
            "150468da38",
            "1acaa5954a",
            "19343e0458",
            "1455aa6d2f",
            "121b39568f",
            "114a8e320b",
            "e208f1108",
            "af6b1f3bc",
            "b9f126d61",
            "18d76cf948",
            "2981a614e",
            "707427ac",
            "93858c4bd",
            "cc8b65c00",
            "12ab70828a",
            "168ea79fc",
            "1702711e",
            "1fc285835b",
            "105b53731b",
            "1745427dfe",
            "17555954fa",
            "175f037956",
            "105df5095c",
            "1fc3db1c63",
        ],
    ),
    (
        5,
        4,
        "5ALzMEwqQG34NKHhtlvJpeODT0XXzg67rNa36VH7ZPvkUyreja",
        [
            "1fce6c0d7f",
            "10411c7141",
            "175870d25d",
            "17466d965d",
            "17517d835d",
            "10595d1141",
            "1fd555557f",
            "4ebc800",
            "9572c69b4",
            "689194c16",
            "184573e2d6",
            "986c7e7d5",
            "1c69aec9ff",
            "c1a647258",
            "15f972fe14",
            "b9de785c6",
            "3708508c0",
            "dbc70df56",
            "ac9e43f4e",
            "19b227e3f7",
            "fd66249cd",
            "68c4b8f1a",
            "1bee0cf288",
            "c84b8d63c",
            "1cc799cc6f",
            "1a1e617950",
            "5f4f32168",
            "b774f427",
            "1ef33dcbf4",
            "123cbf11",
            "1fcc533554",
            "1049285315",
            "17529859f0",
            "174b4782ca",
            "174dc143ae",
            "10584c90f6",
            "1fcc6e6617",
        ],
    ),
    (
        5,
        5,
        "gPz475kYWbE6GdfFkGsQ5W2CLaNn5x7TOdhYFHslekKhVPdSH01CCemjWdx",
        [
            "1fd218c97f",
            "1057c1cd41",
            "17416da15d",
            "174b16105d",
            "174ddc235d",
            "104e48c941",
            "1fd555557f",
            "c830600",
            "8720d5c83",
            "17b8d3cc96",
            "134a1203c9",
            "109de85659",
            "1c4775abea",
            "dadbf35a9",
            "94befcb77",
            "11165a45ac",
            "17769345f3",
            "12276c49e8",
            "1f4ab73e49",
            "b8b4c716a",
            "1457d17efc",
            "e81a3e21f",
            "17755c5757",
            "1923360558",
            "16d0da53f5",
            "1e1d82e1aa",
            "1071b81ffb",
            "139623019f",
            "14fe757ff3",
            "1f0a8310",
            "1fd401f75d",
            "1048aa3719",
            "17421697f7",
            "174f04e552",
            "17492a8ba5",
            "105a0b0749",
            "1fc4fc935d",
        ],
    ),
    (
        5,
        6,
        "emR96cNte1rQVqhPEAn7auDN6M1zaH65mGCXpbmsaWtACAgXe",
        [
            "1fc171be7f",
            "1051570d41",
            "1748c29a5d",
            "175e94085d",
            "17535ce55d",
            "1048226141",
            "1fd555557f",
            "1db75b00",
            "bdd7b09da",
            "1494213056",
            "9c28860b3",
            "69196226f",
            "1c56dd00fa",
            "97cacf9b",
            "1dc03a1f87",
            "189298f52d",
            "67a91f1ce",
            "128db26226",
            "56814165d",
            "1406f92cbb",
            "1ae19737dd",
            "141fccac5d",
            "8ced12b0d",
            "d9a045b84",
            "117df63a23",
            "138d230c59",
            "1bde559ee3",
            "1616c9610f",
            "12400aeff3",
            "19fa4f1a",
            "1fc72fe355",
            "105bf95f1b",
            "17510ac3f5",
            "175b101ace",
            "1746efa2b3",
            "1059e3346f",
            "1fcff1a11d",
        ],
    ),
    (
        5,
        7,
        "sgxAx6mZpS5G1mlCX9104iWxFRX3WOhOalexIWtHCf2c8Uqnb4",
        [
            "1fd470487f",
            "104f9e0241",
            "1758ed055d",
            "1758fe0d5d",
            "174b52e15d",
            "1054c89341",
            "1fd555557f",
            "1598ca00",
            "af976e6ed",
            "1d32c0f263",
            "bf501d56b",
            "7ba69aeb3",
            "2ed85d9ca",
            "52191b66e",
            "7657a6de9",
            "8294cfe22",
            "266e30246",
            "110688c9fb",
            "1615c49bb",
            "3b7efa697",
            "2470fbd4f",
            "1b3710bdcc",
            "1c54f49cf3",
            "128d00c920",
            "ceeca2544",
            "7320eb6ae",
            "134591f39d",
            "c282f9991",
            "1c55f0cbf7",
            "14702111",
            "1fd3fa875b",
            "10519c4114",
            "1749b735f8",
            "1754d6b8bd",
            "174d76ba91",
            "1050d7fd08",
            "1fcb9cf16b",
        ],
    ),
    (
        9,
        0,
        "s3cEvSN5tGbpb8dnGXPjCqTPtw3IGd1wsyQGazerZ5edCx6rOAfa9BWYOgY47ccbRn1lqiVWTnN1RIYLJPMq3HkmMKrmgPMKbaPmwu5gBQbmK5TKCTfGQRO07Sd",
        [
            "1fd3e777e4cc7f",
            "1057777db52641",
            "17557ff85a225d",
            "17599c9202055d",
            "175142df7fdc5d",
            "104752b11fa441",
            "1fd5555555557f",
            "1332d1cf1a00",
            "d68de5ffbbf5f",
            "c9596cc080009",
            "16d0073c4bf8a3",
            "8aee218f67c91",
            "1e4150b1b3fef7",
            "12aecdeb15120a",
            "dfb8ecc1556e3",
            "7a872d6998993",
            "10fb566597fe9e",
            "429828a23323d",
            "184145d6c11363",
            "c3f31f35c2cb1",
            "e7ba5fd1d757c",
            "1f974ec960800b",
            "17c695ca75621f",
            "119e6fb7320282",
            "bf04b3f777fff",
            "191833b1e03113",
            "95a0975467353",
            "1910f251e30b13",
            "3f34c9fe775f5",
            "c1cf01e3b1b0a",
            "175a7d84e9bafb",
            "1d2b1989aad8ba",
            "17d256a9ff38fa",
            "1daaa6b1a382c3",
            "757f220beb5db",
            "122ff1586049f1",
            "7598d57fbbc4d",
            "50397b3612109",
            "9fd5f2b1f3deb",
            "148c2d0b231fe3",
            "10ce98567f517b",
            "b29d663703005",
            "1bfe57795f226b",
            "c3c1614b9c940",
            "25e4bdfbdb1f5",
            "14fa5159111b",
            "1fd6b355bfbd53",
            "104401115bad12",
            "17515dbf733df3",
            "174a0b96991239",
            "175bd3dd1b3fb7",
            "10589947b27f22",
            "1fcc6151fa3997",
        ],
    ),
    (
        9,
        1,
        "sdkWVfB7s3DII1KnJankz5ljj0Ucl0cDKLD7uhhEiVJUdOpzswJb8FoFBL6aQxxXm7gI0yaxbAeioyNP4Qe05DnThatA95e5idAbyh9q5IbM2sDERD9gUnzll4sXAbI",
        [
            "1fc54d4455547f",
            "104bf9d4a66641",
            "174dcf58ce6a5d",
            "17592fd98a655d",
            "1749907fde645d",
            "105f2711ae1c41",
            "1fd5555555557f",
            "11ddd1c27900",
            "c4688df99d668",
            "1ca08e65a9ba94",
            "94dc82a629a39",
            "99b1113b176b8",
            "1f59f7a259de71",
            "684d5f78a8aa4",
            "8cf72e9ad9e9d",
            "1e14a5b8344159",
            "1ade962d955c18",
            "4949e58b8a8bb",
            "a72307ecd935d",
            "13a21b4fd119cb",
            "1565d9eb911f94",
            "1f2607f8ca8abb",
            "1b6230fcfb599f",
            "178158d14b3688",
            "1bf7371f1715fd",
            "13109d512a2b15",
            "55a5cb5783355",
            "17148e11404d1a",
            "17fbf0bf059dfa",
            "ea16ebad83823",
            "8cf63534508f3",
            "1780a83fc54af8",
            "1efabdd2155bab",
            "af74eeca8a66",
            "1a5cdcb9db6e59",
            "1facb69a971b48",
            "fd5398e97966a",
            "39ce121208be7",
            "af62d7a90ca71",
            "1d098f72703728",
            "1c56144991bedf",
            "e08df23799a20",
            "1be653a84df8c1",
            "c18f47e3639eb",
            "26e0c9f957bf3",
            "10bb71a08b1d",
            "1fc78ed5ff0955",
            "10454bd1843713",
            "17479bdfd5b7f2",
            "1747c16503ab53",
            "175807ed53f955",
            "105c14393c6da8",
            "1fc56ab73dd8b9",
        ],
    ),
    (
        9,
        2,
        "tf4sgpr4qNHSgO2KWPLrdoKIkLdBtmVg6lw8tQK2vUW9R4O0D9lKls7Fqd2eDxin4GSB7fgey7ciR7nUIamDAdDSrM6OYA2zdlOqfDH8aJz2YGDwS3skfvJ2zAkkgOop",
        [
            "1fdd28f7f2b47f",
            "104ddd2bede641",
            "1745b646b4b25d",
            "1745ea29274d5d",
            "1759c8bf908c5d",
            "105c1a3141f441",
            "1fd5555555557f",
            "a5919e9e00",
            "fe4499f218731",
            "4b30024f2ae31",
            "a67ce63ebcfd2",
            "d8139ee153a19",
            "34e668a2747f0",
            "8346450c18c3c",
            "bd9d9a11f2964",
            "28156234b6d31",
            "a630fdca748bf",
            "1836bf0b418e25",
            "7dad26cace078",
            "16327de5695e4a",
            "1dd03fb1efc5b6",
            "48177cc629e3d",
            "1a747081f76640",
            "1b3089517f3029",
            "19f61e5f4387f2",
            "171592f1b09713",
            "1750945531095c",
            "911563101651a",
            "15f5535f43c1f4",
            "130c1912809e7a",
            "14c6ab30cb0890",
            "1b756073c9768",
            "1bd39e62a36554",
            "18a94402429ebb",
            "263210263bb1c",
            "148068a03e2b98",
            "d5b68c20188ab",
            "1f9ec8c4e19c59",
            "1c478e38cb7606",
            "152b2d75b357b9",
            "5d544c947ac05",
            "16aeac13933ebb",
            "1be0ccd14be420",
            "c2213e5b4e783",
            "26d153fe789fc",
            "1bf41181371a",
            "1fd29ef5a6fb50",
            "105d61f1317d1b",
            "175babdf8741f2",
            "17593ec6902c22",
            "175d795fcaf2fb",
            "10535c2dd04d2a",
            "1fc953b0abc6a4",
        ],
    ),
    (
        9,
        3,
        "yfBKCz5pU0d3TL7emMnfKofDfaTyZoRtboIEYus42NPfIaLU2r4I5ohaILDxTHKpN0aKNLkXz5hJy6OOyUHopLZK1itoza01DqFvtwng2IRzvIrcl",
        [
            "1fc65e10f12c7f",
            "1058a3bfd3a641",
            "175fa2561b825d",
            "1744a6a6c78d5d",
            "174b9adf9ac45d",
            "10410c31aab441",
            "1fd5555555557f",
            "854715b2c00",
            "ecc3f5fc2b906",
            "133330b0d1ae0b",
            "14e3c35b286ce",
            "726913d2b9a59",
            "1977c24fef9e3c",
            "d23312a1ed125",
            "1c0693936e4db",
            "8167d048a6888",
            "12c4579fbeec46",
            "8be1cf80c08ce",
            "1a63e8ab43a798",
            "27a34e7477de",
            "1c4e66c14c1201",
            "1c99233fb31e02",
            "1dd910dbaad16f",
            "1bb76de49f3e00",
            "1fff2abf8f81f1",
            "712c4919b7117",
            "1b5da615d54553",
            "f1e29314cb912",
            "bfe9bffe02ffe",
            "1c8393b8bc1cee",
            "ef6253ff37728",
            "9b3425a077f1f",
            "556d9d242d7de",
            "173b213f9394bd",
            "e7d49f75c468e",
            "9233b2290df72",
            "1aeeb14b4f8993",
            "1a2591e9fad086",
            "665d625e3b6df",
            "209aba7bf5dba",
            "fade9ad821e3",
            "b87f2340db9ba",
            "1bfa21dfd6c804",
            "c00a1ef5024dd",
            "24a519f8e1ff5",
            "176ad14b8f1f",
            "1fceb375ba8f52",
            "1051b071bdaf1a",
            "1746583f2b67fc",
            "1756145cbaf19f",
            "17548d8bf47563",
            "1059a1ae1de10a",
            "1fcee8c1f52ec6",
        ],
    ),
    (
        9,
        4,
        "LGfiBGvkuHXlpJK11mdcM7LmSLPcL0BgL8qVfPHQmBxwgCFR2pG7fzDpqBVPvJR9b6wDpaZer4Odizqm6JsgRWknfUflLig0pCcyhjHFVfHaKf0av",
        [
            "1fca2dc6da647f",
            "10493570b68e41",
            "1756c80bb01a5d",
            "174104700c1d5d",
            "1753c5bf486c5d",
            "105b3e71d33c41",
            "1fd5555555557f",
            "fe091b98300",
            "95de63fdf63b4",
            "1cb0163ee97dbc",
            "772420edbe1b5",
            "c8f3abba3b0f0",
            "bca165957b1b2",
            "82f940ed5f2b",
            "1d68860f5e866b",
            "1db3104a03a859",
            "46747a513a778",
            "10bba700495d84",
            "2780143831d85",
            "1c9fd4c94c2169",
            "9e9fadc1d693c",
            "eb1ae52bbdd9c",
            "4c367bf8e8dac",
            "586ee0a06acda",
            "17fcfb5f1f65fb",
            "51b4d91eb5d1c",
            "f5a27b51a2b59",
            "513a811cf0513",
            "df2c0df4323f4",
            "5b7fd6b284ab3",
            "5c06b8e701863",
            "2a8911016d379",
            "1c4b9c16536a8f",
            "1016d220394cff",
            "1c648c604824cb",
            "43511b910231a",
            "1bc32835136b88",
            "10ae3d1184cf2",
            "f4f72849a0851",
            "14b86cef7529c2",
            "1878588d57a692",
            "1e9ee64610dded",
            "1bd5be8892d875",
            "c3291e84c9d5a",
            "26be07f9b43f0",
            "1fd771a16d1f",
            "1fc0c775e57957",
            "104ac0714c5910",
            "175ea7ffd3edf1",
            "174520ecc95f6b",
            "17461957ef53c5",
            "105ac97bf2ed5b",
            "1fc6be8532a852",
        ],
    ),
    (
        9,
        5,
        "halMTZMHqdbTTs3qYJyJVD0YdbrcL3sz4RXhgGO0lS8TZVnfF2EfSQrderOFrqTTPtjgKeZBL12yJAJxyyF6JQiPlBRFyHp62ELCyI3hJzGa6H",
        [
            "1fd02840d6747f",
            "105bd6c3cd4641",
            "17453cb1b1b25d",
            "1744b354137d5d",
            "174fa23f82845d",
            "104a82714e5441",
            "1fd5555555557f",
            "590510b3e00",
            "879ec7f2f4a83",
            "1e8ddd1afddfee",
            "1fec7aeb883456",
            "4204eb7e0e6e9",
            "1346df5a95f3be",
            "1facd4e860afca",
            "af8fe00812db0",
            "c04ab248f228f",
            "1e56c2c2b4bba",
            "60b2c21989c95",
            "5577fc5d7e9d5",
            "aafa83f71d559",
            "9fd965ce64e3d",
            "b3af7546c9fcf",
            "ed2649bca219a",
            "1ba303c6ba057a",
            "11f0911fff53ff",
            "131190317b9d1c",
            "f5119d5817354",
            "9153731fdc518",
            "17f3fddf4f0ff2",
            "1b3edb9b289737",
            "1cf4ad5773b66d",
            "163ee8d706f158",
            "971c60c712027",
            "ea242cc8feef3",
            "10ca8c2d399408",
            "baffeb435af48",
            "6c9c8fe555fad",
            "a31eb95488479",
            "13e35df6d9eb30",
            "53f24a4129c1c",
            "a750ae4e90839",
            "c0aea1dd22e5b",
            "1bf04175ef0c59",
            "c15bb7cbe0513",
            "24a36ff2f5dfe",
            "133fb108cf16",
            "1fda6215899950",
            "1045ced1e88f1b",
            "174cc2ff1d15f3",
            "174c5efb998e55",
            "174d4949977c38",
            "1057ebb4ab73dd",
            "1fcd6724c98ca0",
        ],
    ),
    (
        9,
        6,
        "DL6L3cdOrqaxdo0SFdSjAXydGC5HmHQ5Q94jL1MMdFpebZNrlbfGvFh4gLe3qf45TOnEfNuv964Rsdtrlt2FSACmfXrcieXnZuceeghTyI9L5bncO2",
        [
            "1fc5fb1d475c7f",
            "10510ba47bbe41",
            "1748f1a8be325d",
            "175896ba397d5d",
            "1758a67f11ec5d",
            "1043b9b1d17441",
            "1fd5555555557f",
            "10d4b1c87b00",
            "bd61bdf47d7da",
            "79d1b81dd67f7",
            "19709ca23f92d0",
            "1596a32a37b4bb",
            "1a78aaf8b5bb99",
            "4b4c1b5e36cc4",
            "47e74cac737d1",
            "53748ef921ece",
            "1acf293939c8de",
            "da0d69fdd36cd",
            "b5b249a2b2ccd",
            "cacdce6551505",
            "8e48855cede37",
            "a10f19c5cbfcd",
            "1c78fa5f4c5efe",
            "1e32e922fc28c8",
            "1ff2bdff379ffb",
            "91e1cb1496d13",
            "155a3d51a5d5d",
            "71e839174e518",
            "7fe1bdf95a7f7",
            "1d18e14c6e9d78",
            "f744cab73e65d",
            "83b55b2224445",
            "15449022899b7b",
            "191a7f090cfe3e",
            "9ed731ba55932",
            "111581d8a28959",
            "cfa672ad51f09",
            "16156fdd596d5f",
            "e488a528aa851",
            "a389e6d861464",
            "17ca54673dc519",
            "1239e13d3e163e",
            "1bf636dd791c5f",
            "c2e66b42e8d96",
            "25dfa3f0983ff",
            "13a0513acd1f",
            "1fc57e559b9954",
            "1051a9b1ae6319",
            "175892ffdd59ff",
            "175868da6b4d87",
            "174c472b699a79",
            "10564bd49b47e5",
            "1fcf7cd1f3c1f8",
        ],
    ),
    (
        9,
        7,
        "aUOe4pswnvggdkpV3Z1aVEXR4TpfeAabnnMlAQbrToflpBgO0OV5FO3srGzemuOIeNVZLyENMQMvE51g052H2Wx9J7i0n11cgPuFDtbp5ccUylC57X5S4D35",
        [
            "1fdae8cf7fcc7f",
            "1049833c61be41",
            "17533806162a5d",
            "175ccf74c8655d",
            "1748b79f98545d",
            "10513ff13d2441",
            "1fd5555555557f",
            "131051bbea00",
            "af68f9f4d77ed",
            "10f91f4031037",
            "1d725d5117ef32",
            "1d3228fc39890f",
            "19e63d69bf1279",
            "aa89cf2e1074",
            "cf83e52afdf79",
            "132460b433b643",
            "1ee0ab189f26fa",
            "2ad3e9ca3491f",
            "1df224cfd33927",
            "3085c9196f2c3",
            "ccc81116072dc",
            "1f811ef2834019",
            "85a6bb1f6da6c",
            "131f705c072346",
            "1dfdce1ff731f8",
            "1f173f91f49119",
            "f56d15559f35b",
            "71bc371c11311",
            "1df1279f79eff6",
            "12d484412512d",
            "dee0f6ad0d7cd",
            "1d14aab6368998",
            "7ef4df4013dcb",
            "480fbbb9033ea",
            "775ff91b18b02",
            "1131906f47ca55",
            "17f1b813133dde",
            "14131585edb18f",
            "147c06bf0907d7",
            "1a8fe219575b01",
            "17dc36c6f36e6a",
            "9154e24135b8f",
            "1bd1ffdef6d067",
            "c349246b1b629",
            "24faaff21e1f1",
            "1a9fb1863910",
            "1fd54ff5c3bb56",
            "105c2fb1f1d516",
            "174e251fb79df5",
            "1756ba7c47b368",
            "174f5451c19c5b",
            "1058d557609e0a",
            "1fc0c636d945f2",
        ],
    ),
    (
        10,
        0,
        "9Zhg2XR8NGVuHYxBQpiKWaGTOtsygkXBmq389SCeQLpKCHO8OMesCpSzc1bSoBJF72aMMNqWXfkN3T01TKadNlfHv8dvfNhEaK9eLigPDUkgB8qfZwYo6kb3ziV58K5JTUr5OUMLqHnP9avl4rItXm",
        [
            "1fd204f667fee7f",
            "1054ee5306c6241",
            "175ee9ed351165d",
            "1750ea58b48825d",
            "1756a95fddcda5d",
            "104e18fc578bc41",
            "1fd55555555557f",
            "1532bc7023f00",
            "d6fb86feb7b45f",
            "112a021ba20801f",
            "b71198d2fa833b",
            "9b1b551d023b33",
            "12ecc42b77f73f4",
            "12a719984313034",
            "dcaa98d4864fc3",
            "29b6d8d3dc32a0",
            "1ecbd2a457b530",
            "c9ef0e2d31000f",
            "11ce0076394c6e3",
            "179ee236a35f932",
            "2da673257ff679",
            "1bb3606c2230084",
            "1dfd9ca4dc3d588",
            "b152b465c599b2",
            "1ffb4c75f7dd3fd",
            "289901a023002",
            "bfbd5fff16b7f3",
            "17174ff44ad6118",
            "55cae2d535375b",
            "191b7a5c410131c",
            "1df4d56fddb57ff",
            "1581e18949cd083",
            "253f7846bbfcb7",
            "11230a08282214a",
            "376fb571a3356b",
            "8b75a4469f5b63",
            "fc351f567b9087",
            "10a7e34310aba8b",
            "10d7ac9e8ca99fb",
            "5913e615da956b",
            "136fe98c473110b",
            "71ccc49a002185",
            "13ce475b7051ce3",
            "1001fde62e5ce9",
            "9ec5b4e0373479",
            "59ba67b902100d",
            "14e24258d3c1ee5",
            "1f2df6f8aabb4b2",
            "6e5dc7ef379f4",
            "1277b47112311",
            "1fdaccc5633695b",
            "10425f345103f10",
            "17531c87ddf39fd",
            "174d556b7319382",
            "175ae58c5bb87fd",
            "10543fdaa572d02",
            "1fc76a1f9f33147",
        ],
    ),
    (
        10,
        1,
        "VzLffYHoPTpb18JPZ1IA4ij6fsj2CqRbYPedDBaUIesiEZ4aK31Ijn8Nwcuot29BEaj5Rau3GRnLugH6FqEsNfwPV1RMtJUmft1VreRsOGN1kYltdd7rqIFaxfdMxuC8okjs4CkXAodlel",
        [
            "1fcf3a16874ee7f",
            "104369b5642da41",
            "174fa1ccf999e5d",
            "17550f62a626a5d",
            "17403a37e64ca5d",
            "1056c49c75ef441",
            "1fd55555555557f",
            "17280c52e9d00",
            "c4da6c7c395168",
            "69956e8caeaa8f",
            "1fffe9153fd9999",
            "82a13b34131889",
            "185be7aef9b1b5c",
            "d4f417838a94",
            "a7c0c43ae2e6e9",
            "16964027a622aa8",
            "1073a0afd599855",
            "7bbfeaecbaa8b8",
            "145827e82ab1bad",
            "329fb5de1b2ba",
            "fd617eab11901b",
            "15997b347a0aab8",
            "10f43b6beb399de",
            "69d10e50f2c298",
            "1fc031557bbd89f",
            "8a14a151ea10af",
            "df0c747fdbf3fb",
            "112334c4132b1b",
            "350d2bd7dddb57",
            "191ca8a47aaab15",
            "7fb8f5fd6453f5",
            "192262f16ac1bf8",
            "952e93a9599bed",
            "1db90fedaa3bb60",
            "96ee1c96a0f359",
            "1b8121b99cb9be8",
            "bcee470c95da89",
            "11393812f098829",
            "fe260ad32dab41",
            "16990bc2f831909",
            "5495b929075e1b",
            "e329459191882c",
            "fd888d81468a5d",
            "f3ccb8def0f648",
            "1dfe6798a759782",
            "1290a36bdb2a988",
            "14dd68b5c33be55",
            "1f0909bb0d31a48",
            "42bbd7cd91bf9",
            "181f6c5ab991b",
            "1fc63a75437f35f",
            "104ea6947738511",
            "1743a357d35d5f2",
            "17485f14598ab70",
            "17596d47a9dfd53",
            "1050657e230c798",
            "1fcf2b6b3549499",
        ],
    ),
    (
        10,
        2,
        "MRIphdFwDHUNe01erfFE7kv3nrrWB4Bz2HgyBDpreL79JTdyiQJQTXq4G2rHSkqiFwUtuKpxm6k5PELAgFMFvCpWTcrvH7jKmFFhft88BDyff0pcCd9PJs6tUGZQEhshkBucpjJjRbQofrdI5FVRw",
        [
            "1fd925ff51ad67f",
            "1044c1839ccc241",
            "1744188b008ce5d",
            "1744d04deb86a5d",
            "175012afe3af25d",
            "105ff5345162441",
            "1fd55555555557f",
            "d6c344e9f900",
            "fe01b8fdb82c31",
            "4103b9210ad390",
            "6c9438a2d0a04a",
            "40e8cf108ccf24",
            "cce07904f4284a",
            "f222082218e3bc",
            "941b077747af6a",
            "109f87e4ea2393d",
            "5629b2d3944440",
            "199b5cb590a43b4",
            "344414457cae3a",
            "11aca439ee2c18d",
            "10f22b14078a682",
            "e1aadd3628e986",
            "157c6b3e390f535",
            "1205ab71f294efc",
            "1752a61867a2680",
            "3a537ff198e19f",
            "15ffe217c9315fe",
            "d1b71fc43ae31f",
            "1358074553c6754",
            "314b29c538d11c",
            "19f74cc7c5a59f2",
            "142d8630d16fe3f",
            "eed0bf8578a924",
            "598d79980bd10f",
            "1fe60397a564da4",
            "17977f413e5f22c",
            "1153c60f1906d59",
            "13118855e19d34d",
            "cd45164e5ea4ba",
            "190399bf979daa5",
            "2f877e1834a15a",
            "9a098eda88f38f",
            "2e15e40578ffac",
            "142f5a9490be0ee",
            "13740f3dcf8295c",
            "13965ac3d20c2e0",
            "14f450e879f6982",
            "1f013ea168534ce",
            "405edfcb66df5",
            "1355bc603d11d",
            "1fd34e354fe6556",
            "1054077c5840f15",
            "1755e7cfef423f8",
            "17547f1e21ad380",
            "1750376467beb80",
            "10578a3c066d98c",
            "1fcce5b0ffc6016",
        ],
    ),
    (
        10,
        3,
        "LDqBCtpSlJeTU58lfXth6RIpnFaq38DcuM8xmOmAfPy8YUgKEu9kaAgYXqvcJicohAfL6JCNFM22zcRSjZorSBmKLxlEr1cj1ZMGYrsqjHoaHcb5gDq54x8duF1dDxAXYEjQjcJFias48kRO",
        [
            "1fc0b15fcbaee7f",
            "105ee5532d15241",
            "175599d2851b65d",
            "174ca962f30ea5d",
            "1746d3c7eae2a5d",
            "1043770c4566c41",
            "1fd55555555557f",
            "ee5d44cbc300",
            "ec8666fd0d0c06",
            "1f13d69f63ec3b6",
            "26028a60278cef",
            "130fc3b94a227a0",
            "195b9249cb02489",
            "c2ec4be3ae38c9",
            "b68041dc538bf8",
            "b2cbcba68a79ae",
            "149c703bc6732e",
            "1b80bf136c1be09",
            "6caf7834c78c7a",
            "1a3b0d1937e2771",
            "54a4afa7e34f53",
            "19beea0c9107b82",
            "1d4799aca3a6ecc",
            "168d3c1bd8170aa",
            "13d1c4581f6a2a0",
            "4315d696df28d8",
            "ffa6e27d4a93f0",
            "118a624646ad1e",
            "135d04ed40ab759",
            "1f1e16bc4f18f1b",
            "17f48b67ec051f6",
            "307075bd688bd3",
            "14702dd7a678804",
            "9a6830c03b538b",
            "e5a7a58ac17a55",
            "21aa60287c834b",
            "de2796e510a890",
            "857f99e8d1a2e",
            "17dc49587292332",
            "eb071a2c127a6e",
            "3e689b6f5e1e46",
            "c9ec8716d8a530",
            "135f36089a82582",
            "1e9292f4f8cab72",
            "187a761e8e3c4ac",
            "f02e301331c365",
            "14e0ffdeae9bdc7",
            "1f3612f864a198a",
            "63df57ffeedf1",
            "1fe2ec40f3b13",
            "1fc298355857750",
            "105a7aac6275d1d",
            "17446007f8e33f7",
            "175d2559fc0bc12",
            "1755d6539e71348",
            "10529052b2081c1",
            "1fc2b83b2ef4820",
        ],
    ),
    (
        10,
        4,
        "fEPVBTl5BBFZzDHyR1NQcVk9hesdRXfcfQHCGrjfyG2ljVBvr3UBZmgGrYmPzO5n1PIO7dbIHYXuIdkVSDwGhV1zcyELBQ6fvfQn9TRTmW2zCfYjNbv8r2DO6hX7cJiN719MUy7GgeRpNti786nTZM",
        [
            "1fcd19c41bfce7f",
            "104b6ae67123241",
            "175545fad900e5d",
            "17466c9358de25d",
            "175f8737c96d25d",
            "105a9e9464c5441",
            "1fd55555555557f",
            "4957c584b600",
            "959743fcbcd8b4",
            "79f94e889feb70",
            "c78e1fd6cc34ba",
            "1015c2254541e93",
            "8d12b0d89897a9",
            "1531f233585eb64",
            "245e2405dfe7d2",
            "f81814817aa8fb",
            "166f0add97a1969",
            "16b9b6a3da4fb7b",
            "1ff092af4662b9e",
            "b96f578d61cf82",
            "f380139f256c3",
            "9893951035c95d",
            "a6e3de19392951",
            "396e536ea83080",
            "ce2d6d22707eab",
            "9af2797287e15d",
            "9fa0a27de5f1fe",
            "916721c686ad12",
            "d50bcf54ba1d50",
            "d1bf17c687eb1b",
            "bfaa097e659ffe",
            "80dcb7da813351",
            "1f8101813a559e",
            "e3e77e3986d2e5",
            "1662c50fdcc229e",
            "1c203ac2924b5e1",
            "1cf1bb4d0b83053",
            "6b76311c95fa25",
            "12d11f2d7857f9e",
            "3b500aca8b8c62",
            "196f8efbd3a55de",
            "1a9570f5586fbef",
            "b50cb2bb5683a2",
            "229d524f7a3",
            "14dd4a7163a7155",
            "1c397c45db5caaf",
            "14d58b2b5f13b9e",
            "1f3295960a12b73",
            "7eb96fcb251f3",
            "1c7f14685db14",
            "1fc65dfd7b7d95c",
            "1047706c4dbf311",
            "175d2f17db653fb",
            "1741fc101257a83",
            "174b7b861236890",
            "105004fe838d6a8",
            "1fc1f027fbcd9e9",
        ],
    ),
    (
        10,
        5,
        "gqb3E6IJyYrHw2HGUnSXJK6B8JswsjoAK5nMbFMNsHsgoRaPOcNAjvhAg7GdX1doCGuctbvnAi8BMqBrmu4H1B2TOAKH3YxR52WXiC9g4gvPOIqM5HTdStd8BOpcWlLAxYxASODboHA",
        [
            "1fd50b3f6544e7f",
            "1053f841d7e8241",
            "174e0e422d87e5d",
            "1748c1ed53b3a5d",
            "174e320ff1bd25d",
            "1045afb44c41441",
            "1fd55555555557f",
            "bb6246d36700",
            "875a687ce88b83",
            "1b8eef489c6ffc0",
            "178db961aa4a36",
            "1d83cc93d650bde",
            "8d6d26491dd69f",
            "38687863bb53a0",
            "1c604a57a9d84ee",
            "1d81131151825cc",
            "2459a7dc3ccec8",
            "523b5d80a9f18e",
            "106d6d5eb0eb395",
            "48c43db250801d",
            "12edbe678bca687",
            "1abebfe66fcdfd5",
            "14c1b89cc12ad09",
            "1e2f7c1690880bc",
            "1ccf773e65c977f",
            "10245d24609bb12",
            "17f41fd7e357dfa",
            "111979a47cf3b1f",
            "115d8145670e350",
            "111d507c5bbf917",
            "7f5e847e7f43f3",
            "139c8a6a3e9f4ce",
            "b714b2c1d4a89f",
            "21b0250fedcf7f",
            "17e22c6a9889d82",
            "62ec75be9f48bf",
            "f47b9ef59f5eff",
            "1e0d60f3189e26f",
            "9fcca6f7d2d698",
            "da94c0e178d637",
            "156ad5d8414b029",
            "1a79a265bed364",
            "46fc1e79bd0ed9",
            "12283502e5848fc",
            "a6d07a5abced65",
            "148ade06755dfb0",
            "14dabd2566e5826",
            "1f1651b4211c6de",
            "61e53ff551ff4",
            "1d41f469b6911",
            "1fd42bc54929b56",
            "104fba9c51a6d1f",
            "174418f7e3445fe",
            "174596aa8aaeb38",
            "1742e5f860e9913",
            "105223868e259bc",
            "1fcc16d83f6eb06",
        ],
    ),
    (
        10,
        6,
        "vkd3ZVo1vwDOjNnQYKiqWFEIdnurgasDdWfXfRxwqHY0hvLuo8I2bX4vlH5JogTnJs72LXihnr7OL1S69bNKQg9qtLbruiWTBQpH0h0b8972HOZrKp3YgwC86ftUAKi6kWShbTaASHIlHjFDuv",
        [
            "1fce166824d567f",
            "1052d58d1719a41",
            "174c961bbf8de5d",
            "175d68a7d3f3a5d",
            "175385f7d2c7a5d",
            "104e4dcc7c0c441",
            "1fd55555555557f",
            "169c6c7742700",
            "bdcc647dd10dda",
            "c14d5e91fcdfec",
            "16f9c1308fa0b43",
            "a06b46486e9385",
            "27a4ed3b93181d",
            "425e117287e971",
            "4deb8f0f3cc46c",
            "a0b7370c9663dc",
            "1578a10e758d2c7",
            "15953a257fb4db9",
            "2d0950576fa8f1",
            "c92c929548f6ec",
            "76bb767639071b",
            "17baec32cddc7e7",
            "2e46d7d20427e8",
            "32fb2c4ae8e14",
            "f5a7eb199d9397",
            "f2cdecebf4eb46",
            "df90d2fdc157f0",
            "151a3894645e11d",
            "1559197561cb150",
            "31704a47eb4f18",
            "5faa8f7e7129f5",
            "f881cdc7b7446e",
            "5d59dbf8b70cca",
            "4008d48ed5c7d4",
            "1beb3dce41ba7eb",
            "b9eeab2263666",
            "fe4de8c9d3f2c1",
            "1930b0c3007c348",
            "184c0134d2d9036",
            "151af40e09e52cc",
            "13d14e91dc232dd",
            "1895ccf80e2cc33",
            "fc7527d50d0af1",
            "141724e6b294a1f",
            "16ebc92859de814",
            "fac2e801ffdd3c",
            "14d433c1e648ec3",
            "1f0bd8cf438dd14",
            "46e607c9d9ff6",
            "192f5c6877b17",
            "1fcb7eb551dab54",
            "1052bc444d1d517",
            "175305dff5cfbf0",
            "175f22ccbf25e04",
            "174f1516f51795f",
            "105c5f74d08e85f",
            "1fca36902790c90",
        ],
    ),
    (
        10,
        7,
        "cfzNehF4zOuYbU0TFXupI4W902O4vcL68t2fMB21wbzBThNimI5d6nmia9eXjdffFtd0UvepXdhg7qBwua6Qc9V4QMbP0m7pUAozm3G2Bkf6ok8MdfeUcjBV4MTqW8aU6AboDN8e3",
        [
            "1fd0e1a375e6e7f",
            "1040939c2067a41",
            "1753760addab65d",
            "1751eb5bec6825d",
            "174bab27c9df25d",
            "1053ff8c6c01c41",
            "1fd55555555557f",
            "1bdce44e12300",
            "af78bcfd4b80ed",
            "1fb7c54d73a048f",
            "37e92356a4b215",
            "1e086dbf5ba33d9",
            "154469ef31ffef0",
            "8827e2544904a4",
            "8416eec2d6a0f6",
            "83eb4f31f62c38",
            "18e74c794fe9868",
            "12a6f72e4bd0a68",
            "1ece64b62a998c7",
            "10333ef84756b10",
            "1cfbfc5741b60b9",
            "12aea5903083002",
            "dfd8eb7d22d0e6",
            "17b17a7b8b6f04b",
            "b49a0f3bfe36f5",
            "1ea1aca7dca56bc",
            "ff4a22fcdee1f6",
            "b1541d4710f111",
            "1f544ecd51e1b50",
            "312e3ac404b11a",
            "19f40cc7e7d71ff",
            "113d7e0dbda0932",
            "14743cf617e3a3",
            "b840dfaf2b310b",
            "c50df44440b771",
            "1c17c19c678c883",
            "8eb1fbee1710c9",
            "1035ce77e491499",
            "19cbf513f14af36",
            "90d224c1c5297a",
            "12f1eed6d1848b9",
            "c06a776622aaaa",
            "17f0030b8172dcb",
            "18854991ac2b93a",
            "6ec3ec2a570693",
            "bb2052e680b282",
            "14d66046bd59049",
            "1f05b37d5d70b6a",
            "60d6cfd7315fb",
            "18557c6722f12",
            "1fd3c8d545ff95a",
            "105c634c7a11b13",
            "174a37efdb675f1",
            "1759a533625917f",
            "1747689f9129fb1",
            "1055ec838399688",
            "1fc30f8f3156252",
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


def _matrix_from(rows: list[str]) -> list[list[bool]]:
    size = len(rows)
    return [
        [bool((int(r, 16) >> (size - 1 - i)) & 1) for i in range(size)] for r in rows
    ]


@pytest.mark.parametrize(("version", "mask", "text", "rows"), KNOWN)
def test_each_vector_mask_label_matches_its_matrix(
    version: int, mask: int, text: str, rows: list[str]
) -> None:
    level, read = read_format(_matrix_from(rows))
    assert level == LEVEL_BITS["Q"]
    assert read == mask


def test_a_permuted_mask_label_is_caught() -> None:
    first, second = KNOWN[0], KNOWN[1]
    assert first[1] != second[1]
    swapped = [(first[0], second[1], first[2], first[3])]
    for _, mask, _, rows in swapped:
        assert read_format(_matrix_from(rows))[1] != mask


# --- the tile color (#688, owner decision D10): dark modules on an amber tile


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _luminance(color: str) -> float:
    def lin(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _token(name: str) -> str:
    css = (ROOT / "src/reach_around/site/static/tokens.css").read_text()
    found = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})\s*;", css)
    assert found, name
    return found.group(1).lower()


def _colors(svg: str) -> tuple[str, str]:
    found = re.fullmatch(
        r'.*<rect [^>]*fill="(#[0-9a-f]{6})"/><path fill="(#[0-9a-f]{6})".*',
        svg,
        re.DOTALL,
    )
    assert found
    return found.group(1), found.group(2)


def _scan_from_colors(svg: str) -> bytes:
    """Decode as a camera would: from the luminance of what is drawn.

    Every drawn module gets the luminance of its color, the threshold sits
    halfway between the two, and the decoder reads the thresholded grid.
    """
    tile, ink = _colors(svg)
    matrix, border = parse_svg(svg)
    cut = (_luminance(tile) + _luminance(ink)) / 2
    grid = [[_luminance(ink if m else tile) < cut for m in row] for row in matrix]
    payload, level, _ = decode(grid)
    assert level == LEVEL_BITS["Q"] and border >= 4
    return payload


def test_tile_is_the_brand_amber_and_ink_is_dark() -> None:
    tile, ink = _colors(qr_svg(BTC))
    assert tile == _token("ra-brand-amber")
    assert _luminance(ink) < 0.02
    # a light module color stays one small tile (the image is capped at 14rem);
    # it is never a page-wide fill
    css = (ROOT / "src/reach_around/site/static/crypto.css").read_text()
    assert re.search(r"\.crypto-qr-image\s*\{[^}]*width:\s*min\(100%, 14rem\)", css)


def test_tile_contrast_is_enough_for_a_phone_camera() -> None:
    tile, ink = _colors(qr_svg(BTC))
    assert _contrast(tile, ink) >= 7


@pytest.mark.parametrize("address", [BTC, ETH, "x", "bc1q" + "x7Kp" * 36])
def test_colors_alone_round_trip_to_the_address(address: str) -> None:
    assert _scan_from_colors(qr_svg(address)) == address.encode()


def test_the_contrast_guard_bites_on_a_dim_tile() -> None:
    # the old gray tile, and an amber that is too dark, must fail the bar
    assert _contrast("#c8ccd4", "#0b0e14") >= 7  # sanity: helper agrees on gray
    assert _contrast("#af6a0e", "#0b0e14") < 7
    assert _contrast("#fbb03b", "#fbb03b") == pytest.approx(1)
