# SPDX-License-Identifier: Apache-2.0
"""Instance files must be what their extension says, and SVG is screened."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
MASCOT = ROOT / "examples" / "instance" / "media" / "mascot.svg"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Tiny synthetic files that carry each format's signature.
GOOD: dict[str, bytes] = {
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
    "jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 8,
    "jpeg": b"\xff\xd8\xff\xdb" + b"\x00" * 8,
    "gif": b"GIF89a" + b"\x00" * 8,
    "webp": b"RIFF\x04\x00\x00\x00WEBPVP8 ",
    "avif": b"\x00\x00\x00\x18ftypavif" + b"\x00" * 8,
    "svg": f"<svg xmlns='{SVG_NS}' viewBox='0 0 1 1'><title>t</title></svg>".encode(),
}
FONTS: dict[str, bytes] = {
    "woff2": b"wOF2" + b"\x00" * 8,
    "woff": b"wOFF" + b"\x00" * 8,
    "ttf": b"\x00\x01\x00\x00" + b"\x00" * 8,
    "otf": b"OTTO" + b"\x00" * 8,
}
YAML = b"secret: hunter2\nkey: value\n"


def config(**theme: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "C", **theme.pop("creator", {})},
        "tabs": [
            {
                "id": "a",
                "title": "A",
                "blocks": [{"id": "one", "title": "One", "url": "https://x.example/"}],
            }
        ],
    }
    if theme:
        data["theme"] = theme
    return SiteConfig.model_validate(data)


def build(tmp_path: Path, name: str, data: bytes, slot: str = "avatar") -> Path:
    work = tmp_path / "inst"
    work.mkdir(parents=True, exist_ok=True)
    (work / name).write_bytes(data)
    if slot == "avatar":
        cfg = config(creator={"avatar": name})
    elif slot == "mascot":
        cfg = config(mascot=name)
    else:
        cfg = config(fonts={"body": name})
    out = tmp_path / "out"
    build_site(cfg, out, today=TODAY, assets_dir=work)
    return out


# --- magic bytes ------------------------------------------------------------


@pytest.mark.parametrize("ext", sorted(GOOD))
def test_a_real_file_of_each_image_type_is_accepted(tmp_path: Path, ext: str) -> None:
    out = build(tmp_path, f"a.{ext}", GOOD[ext])
    assert (out / f"a.{ext}").read_bytes() == GOOD[ext]


@pytest.mark.parametrize("ext", sorted(FONTS))
def test_a_real_file_of_each_font_type_is_accepted(tmp_path: Path, ext: str) -> None:
    out = build(tmp_path, f"f.{ext}", FONTS[ext], slot="font")
    assert (out / f"f.{ext}").read_bytes() == FONTS[ext]


@pytest.mark.parametrize("ext", sorted(GOOD))
def test_a_yaml_renamed_to_any_image_extension_is_refused(
    tmp_path: Path, ext: str
) -> None:
    with pytest.raises(ValueError, match=rf"creator\.avatar.*{ext}"):
        build(tmp_path, f"secret.{ext}", YAML)
    assert not (tmp_path / "out" / f"secret.{ext}").exists()


@pytest.mark.parametrize("ext", sorted(FONTS))
def test_a_yaml_renamed_to_any_font_extension_is_refused(
    tmp_path: Path, ext: str
) -> None:
    with pytest.raises(ValueError, match=r"theme\.fonts\.body"):
        build(tmp_path, f"secret.{ext}", YAML, slot="font")


def test_extension_case_is_handled_the_same_way(tmp_path: Path) -> None:
    out = build(tmp_path, "a.PNG", GOOD["png"])
    assert (out / "a.PNG").is_file()
    with pytest.raises(ValueError, match=r"creator\.avatar"):
        build(tmp_path / "bad", "b.PNG", YAML)


def test_a_valid_png_under_a_jpg_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"creator\.avatar"):
        build(tmp_path, "a.jpg", GOOD["png"])


def test_avif_needs_an_avif_brand_and_webp_needs_the_webp_tag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="avif"):
        build(tmp_path, "a.avif", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8)
    with pytest.raises(ValueError, match="webp"):
        build(tmp_path / "w", "a.webp", b"RIFF\x04\x00\x00\x00WAVEfmt ")
    out = build(tmp_path / "s", "a.avif", b"\x00\x00\x00\x18ftypavis" + b"\x00" * 8)
    assert (out / "a.avif").is_file()


def test_ttf_accepts_the_true_tag(tmp_path: Path) -> None:
    out = build(tmp_path, "f.ttf", b"true" + b"\x00" * 8, slot="font")
    assert (out / "f.ttf").is_file()


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"creator\.avatar"):
        build(tmp_path, "a.png", b"")


# --- SVG screening ----------------------------------------------------------


def svg(body: str, attrs: str = "") -> bytes:
    return (
        f"<svg xmlns='{SVG_NS}' xmlns:xlink='{XLINK_NS}' {attrs}>{body}</svg>".encode()
    )


def refused(tmp_path: Path, data: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=rf"mascot.*{reason}"):
        build(tmp_path, "m.svg", data, slot="mascot")


def test_the_placeholder_mascot_passes(tmp_path: Path) -> None:
    out = build(tmp_path, "m.svg", MASCOT.read_bytes(), slot="mascot")
    assert (out / "m.svg").read_bytes() == MASCOT.read_bytes()


@pytest.mark.parametrize(
    "body",
    [
        "<rect width='1' height='1' fill='red'/>",
        "<defs><linearGradient id='g'><stop offset='0'/></linearGradient>"
        "<clipPath id='c'><circle r='1'/></clipPath>"
        "<filter id='f'><feGaussianBlur stdDeviation='1'/></filter></defs>"
        "<circle r='1' fill='url(#g)' clip-path='url(#c)' filter='url(#f)'/>",
        "<defs><path id='p' d='M0 0'/></defs><use href='#p'/><use xlink:href='#p'/>",
        "<title>a</title><desc>b</desc>",
        "<style>.a { fill: url(#g); } circle { stroke: url('#g'); }</style>",
        "<rect style='fill: url( #g )'/>",
        "<animate attributeName='opacity' from='0' to='1'/>",
    ],
)
def test_plain_shapes_gradients_clips_filters_and_fragment_refs_pass(
    tmp_path: Path, body: str
) -> None:
    out = build(tmp_path, "m.svg", svg(body), slot="mascot")
    assert (out / "m.svg").is_file()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<script>alert(1)</script>", "script"),
        ("<SCRIPT>alert(1)</SCRIPT>", "script"),
        ("<svg:script xmlns:svg='" + SVG_NS + "'>x</svg:script>", "script"),
        (
            "<g xmlns:q='http://www.w3.org/1999/xhtml'><q:script>x</q:script></g>",
            "script",
        ),
        ("<rect onload='x()'/>", "event handler"),
        ("<rect ONCLICK='x()'/>", "event handler"),
        ("<rect xmlns:z='urn:z' z:onmouseover='x()'/>", "event handler"),
        ("<foreignObject><div/></foreignObject>", "foreignObject"),
        ("<FOREIGNOBJECT/>", "foreignObject"),
        ("<a href='javascript:alert(1)'><rect/></a>", "href"),
        ("<a href='https://example.com/'><rect/></a>", "href"),
        ("<use href='other.svg#a'/>", "href"),
        ("<image href='https://example.com/x.png'/>", "href"),
        ("<image href='data:image/png;base64,AAAA'/>", "href"),
        ("<use xlink:href='https://example.com/x.svg#a'/>", "href"),
        ("<use xmlns:foo='" + XLINK_NS + "' foo:href='x.svg#a'/>", "href"),
        ("<use xmlns:XLINK='" + XLINK_NS + "' XLINK:HREF='x'/>", "href"),
        ("<a><animate attributeName='href' to='javascript:alert(1)'/></a>", "animat"),
        ("<a><set attributeName='xlink:href' to='x'/></a>", "animat"),
        ("<a><animateTransform attributeName='href'/></a>", "animat"),
        ("<a><animateMotion attributeName='HREF'/></a>", "animat"),
        ("<style>@import url(#a);</style>", "@import"),
        ("<style>a { fill: url(https://example.com/x) }</style>", "url"),
        ("<style>a { fill: url('//example.com/x') }</style>", "url"),
        ("<style>a { width: expression(alert(1)) }</style>", "expression"),
        ("<rect style='fill: url(https://example.com/x)'/>", "url"),
        ("<rect style='background: URL( data:text/html,x )'/>", "url"),
        ("<rect fill='url(https://example.com/x)'/>", "url"),
        ("<style>a { fill: u\\72l(https://example.com/x) }</style>", "escape"),
        (
            "<style>a { fill: image-set('https://example.com/x' 1x) }</style>",
            "image-set",
        ),
        ("<iframe src='x'/>", "iframe"),
        ("<embed src='x'/>", "embed"),
        ("<object data='x'/>", "object"),
        ("<audio src='x'/>", "audio"),
        ("<video src='x'/>", "video"),
    ],
)
def test_hostile_svg_is_refused_with_the_key_and_reason(
    tmp_path: Path, body: str, reason: str
) -> None:
    refused(tmp_path, svg(body), reason)


@pytest.mark.parametrize(
    "prefix",
    [
        b"<!DOCTYPE svg>",
        b"<!doctype svg>",
        b"<!DOCTYPE svg [<!ENTITY a 'b'>]>",
        b"<!ENTITY a 'b'>",
    ],
)
def test_doctype_and_entity_declarations_are_refused(
    tmp_path: Path, prefix: bytes
) -> None:
    data = prefix + svg("<rect/>")
    refused(tmp_path, data, "DOCTYPE or ENTITY")


def test_a_billion_laughs_payload_is_refused_before_it_is_parsed(
    tmp_path: Path,
) -> None:
    lol = "<!DOCTYPE lolz [<!ENTITY a 'lol'>"
    for i in range(1, 9):
        prev = "&a;" if i == 1 else f"&e{i - 1};"
        lol += f"<!ENTITY e{i} '{prev * 10}'>"
    lol += "]>"
    data = lol.encode() + svg("<text>&e8;</text>")
    refused(tmp_path, data, "DOCTYPE or ENTITY")


def test_a_utf16_svg_cannot_hide_a_doctype(tmp_path: Path) -> None:
    text = "<!DOCTYPE svg [<!ENTITY a 'b'>]><svg xmlns='" + SVG_NS + "'/>"
    refused(tmp_path, b"\xff\xfe" + text.encode("utf-16-le"), "UTF-8")


def test_not_xml_and_wrong_root_are_refused(tmp_path: Path) -> None:
    refused(tmp_path, b"<svg xmlns='" + SVG_NS.encode() + b"'><g></svg>", "valid XML")
    refused(tmp_path, b"<html xmlns='http://www.w3.org/1999/xhtml'/>", "svg")
    refused(tmp_path / "t", YAML, "valid XML")


def test_a_hostile_svg_is_not_published(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build(tmp_path, "m.svg", svg("<script>x</script>"), slot="mascot")
    assert not (tmp_path / "out" / "m.svg").exists()
