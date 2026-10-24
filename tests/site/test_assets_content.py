# SPDX-License-Identifier: Apache-2.0
"""Instance files must be what their extension says, and SVG is screened."""

from __future__ import annotations

import re
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

IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def riff_webp(payload: bytes = b"VP8 \x00\x00\x00\x00") -> bytes:
    body = b"WEBP" + payload
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def woff(tag: bytes, extra: int = 8) -> bytes:
    total = 12 + extra
    return tag + b"\x00\x01\x00\x00" + total.to_bytes(4, "big") + b"\x00" * extra


def sfnt(tag: bytes, tables: int = 1) -> bytes:
    return tag + tables.to_bytes(2, "big") + b"\x00" * 6 + b"\x00" * (16 * tables)


# Tiny synthetic files that are structurally sane for each format.
GOOD: dict[str, bytes] = {
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + IEND,
    "jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 8 + b"\xff\xd9",
    "jpeg": b"\xff\xd8\xff\xdb" + b"\x00" * 8 + b"\xff\xd9",
    "gif": b"GIF89a" + b"\x00" * 8 + b";",
    "webp": riff_webp(),
    "avif": b"\x00\x00\x00\x18ftypavif" + b"\x00" * 8,
    "svg": f"<svg xmlns='{SVG_NS}' viewBox='0 0 1 1'><title>t</title></svg>".encode(),
}
FONTS: dict[str, bytes] = {
    "woff2": woff(b"wOF2"),
    "woff": woff(b"wOFF"),
    "ttf": sfnt(b"\x00\x01\x00\x00"),
    "otf": sfnt(b"OTTO"),
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
        build(tmp_path / "w", "a.webp", b"RIFF\x0c\x00\x00\x00WAVEfmt \x00\x00\x00\x00")
    out = build(tmp_path / "s", "a.avif", b"\x00\x00\x00\x18ftypavis" + b"\x00" * 8)
    assert (out / "a.avif").is_file()


def test_ttf_accepts_the_true_tag(tmp_path: Path) -> None:
    out = build(tmp_path, "f.ttf", sfnt(b"true"), slot="font")
    assert (out / "f.ttf").is_file()


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"creator\.avatar"):
        build(tmp_path, "a.png", b"")


# --- structural sanity: not just a prefix ------------------------------------


def bad_forms() -> list[tuple[str, bytes]]:
    return [
        ("png", GOOD["png"][:-12] + b"x" * 12),  # no IEND
        ("png", b"\x89PNG\r\n\x1a\n" + YAML),
        ("gif", b"GIF89a" + YAML),
        ("gif", GOOD["gif"][:-1]),
        ("jpg", b"\xff\xd8\xff" + YAML),
        ("jpeg", GOOD["jpeg"][:-2]),
        ("webp", riff_webp() + b"trailing"),
        ("webp", b"RIFF" + (5).to_bytes(4, "little") + b"WEBP" + YAML),
    ]


@pytest.mark.parametrize(("ext", "data"), bad_forms())
def test_a_valid_prefix_with_a_bad_structure_is_refused(
    tmp_path: Path, ext: str, data: bytes
) -> None:
    with pytest.raises(ValueError, match=rf"creator\.avatar.*{ext}"):
        build(tmp_path, f"a.{ext}", data)


@pytest.mark.parametrize(
    ("ext", "data"),
    [
        ("woff", b"wOFF<html>"),
        ("woff", woff(b"wOFF") + b"x"),
        ("woff", woff(b"wOFF")[:-1]),
        ("woff2", b"wOF2" + YAML),
        ("woff2", woff(b"wOF2") + b"x"),
        ("ttf", b"\x00\x01\x00\x00" + YAML),
        ("ttf", sfnt(b"true", tables=0)),
        ("ttf", sfnt(b"true", tables=65)),
        ("otf", sfnt(b"OTTO")[:-1]),
        ("otf", b"OTTO" + YAML),
    ],
)
def test_a_font_with_a_bad_header_is_refused(
    tmp_path: Path, ext: str, data: bytes
) -> None:
    with pytest.raises(ValueError, match=r"theme\.fonts\.body"):
        build(tmp_path, f"f.{ext}", data, slot="font")


def test_the_structural_checks_follow_the_extension_case(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"creator\.avatar"):
        build(tmp_path, "a.PNG", b"\x89PNG\r\n\x1a\n" + YAML)
    out = build(tmp_path / "ok", "a.GIF", GOOD["gif"])
    assert (out / "a.GIF").is_file()


def test_the_boundaries_of_the_table_directory_are_accepted(tmp_path: Path) -> None:
    out = build(tmp_path, "f.ttf", sfnt(b"true", tables=64), slot="font")
    assert (out / "f.ttf").is_file()


# --- SVG screening: an allowlist ---------------------------------------------

XML_NS = "http://www.w3.org/XML/1998/namespace"


def svg(body: str, attrs: str = "") -> bytes:
    return (
        f"<svg xmlns='{SVG_NS}' xmlns:xlink='{XLINK_NS}' {attrs}>{body}</svg>"
    ).encode()


def refused(tmp_path: Path, data: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=rf"theme\.mascot.*m\.svg.*{reason}"):
        build(tmp_path, "m.svg", data, slot="mascot")


def test_the_placeholder_mascot_passes_byte_for_byte(tmp_path: Path) -> None:
    assert b"<!--" in MASCOT.read_bytes()
    out = build(tmp_path, "m.svg", MASCOT.read_bytes(), slot="mascot")
    assert (out / "m.svg").read_bytes() == MASCOT.read_bytes()


@pytest.mark.parametrize(
    "body",
    [
        "<rect width='1' height='1' fill='red'/>",
        "<g transform='scale(2)'><circle cx='1' cy='1' r='1' fill='#fff'/></g>",
        "<defs><linearGradient id='g' x1='0' x2='1' gradientUnits='userSpaceOnUse'>"
        "<stop offset='0' stop-color='red' stop-opacity='1'/></linearGradient>"
        "<radialGradient id='r' fx='0' fy='0' spreadMethod='pad'><stop offset='1'/>"
        "</radialGradient></defs><rect fill='url(#g)' stroke='url(  #r )'/>",
        "<defs><clipPath id='c' clipPathUnits='userSpaceOnUse'><circle r='1'/>"
        "</clipPath><mask id='m' maskUnits='userSpaceOnUse'><rect/></mask></defs>"
        "<circle r='1' clip-path='url(#c)' mask='url(#m)'/>",
        "<defs><filter id='f' filterUnits='objectBoundingBox'>"
        "<feGaussianBlur in='SourceGraphic' stdDeviation='1' result='b'/>"
        "<feOffset in='b' dx='1' dy='1' result='o'/>"
        "<feFlood flood-color='red' flood-opacity='0.5' result='fl'/>"
        "<feComposite in='fl' in2='o' operator='in' k1='0' k2='1' k3='0' k4='0'/>"
        "<feColorMatrix type='saturate' values='0'/><feBlend mode='multiply'/>"
        "<feDropShadow dx='1' dy='1'/>"
        "<feMerge><feMergeNode in='b'/></feMerge></filter></defs>"
        "<rect filter='url(#f)'/>",
        "<defs><path id='p' d='M0 0L1 1'/></defs>"
        "<use href='#p'/><use xlink:href='#p' x='1' y='2'/>",
        "<title>a</title><desc>b</desc><metadata>c</metadata>",
        "<text x='1' y='2' font-family='serif' font-size='3' font-weight='bold'"
        " text-anchor='middle' xml:space='preserve' textLength='9' rotate='0'>hi"
        "<tspan dx='1'>there</tspan></text>",
        "<symbol id='s' viewBox='0 0 1 1' preserveAspectRatio='none'><line x1='0'"
        " x2='1' y1='0' y2='1'/><polyline points='0,0 1,1'/>"
        "<polygon points='0,0 1,1 0,1'/><ellipse rx='1' ry='2'/></symbol>",
    ],
)
def test_the_allowed_shapes_gradients_clips_masks_filters_and_text_pass(
    tmp_path: Path, body: str
) -> None:
    data = svg(body)
    out = build(tmp_path, "m.svg", data, slot="mascot")
    assert (out / "m.svg").read_bytes() == data


def test_the_xml_declaration_and_a_leading_comment_are_accepted(
    tmp_path: Path,
) -> None:
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n<!-- note -->\n' + svg("<rect/>")
    out = build(tmp_path, "m.svg", data, slot="mascot")
    assert (out / "m.svg").read_bytes() == data


def test_the_prefix_form_of_an_allowed_svg_element_passes(tmp_path: Path) -> None:
    data = f"<s:svg xmlns:s='{SVG_NS}'><s:rect/></s:svg>".encode()
    out = build(tmp_path, "m.svg", data, slot="mascot")
    assert (out / "m.svg").is_file()


# Every element outside the allowlist is refused by default.
@pytest.mark.parametrize(
    "element",
    [
        "script", "SCRIPT", "foreignObject", "FOREIGNOBJECT", "a", "image",
        "feImage", "style", "animate", "set", "animateTransform",
        "animateMotion", "switch", "pattern", "view", "cursor", "font-face",
        "iframe", "embed", "object", "audio", "video", "marker", "mpath",
        "textPath", "tref", "handler", "listener", "color-profile", "html",
    ],
)  # fmt: skip
def test_every_element_outside_the_allowlist_is_refused(
    tmp_path: Path, element: str
) -> None:
    refused(tmp_path, svg(f"<{element}/>"), rf"element <{element}> is not allowed")
    refused(tmp_path / "n", svg(f"<g><{element}>x</{element}></g>"), "element")


@pytest.mark.parametrize(
    "body",
    [
        "<svg:script xmlns:svg='" + SVG_NS + "'>x</svg:script>",
        "<q:script xmlns:q='http://www.w3.org/1999/xhtml'>x</q:script>",
        "<q:rect xmlns:q='urn:other'/>",
        "<rect xmlns='urn:other'/>",
        "<x:rect xmlns:x='" + XLINK_NS + "'/>",
        "<g xmlns:x='http://www.w3.org/1999/xhtml'><x:div/></g>",
    ],
)
def test_elements_in_any_other_namespace_are_refused(tmp_path: Path, body: str) -> None:
    refused(tmp_path, svg(body), "(element|namespace)")


def test_a_root_outside_the_svg_namespace_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, b"<svg><rect/></svg>", "element <svg>")
    refused(
        tmp_path / "h", b"<html xmlns='http://www.w3.org/1999/xhtml'/>", "namespace"
    )


@pytest.mark.parametrize("root", ["g", "rect", "defs", "text", "use"])
def test_an_allowed_element_is_not_a_valid_root(tmp_path: Path, root: str) -> None:
    refused(tmp_path, f"<{root} xmlns='{SVG_NS}'/>".encode(), "root element")


def test_an_unknown_namespace_declaration_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, svg("<rect/>", "xmlns:evil='urn:evil'"), "namespace")


@pytest.mark.parametrize(
    "attr",
    [
        "onclick='x()'", "onload='x()'", "ONCLICK='x()'", "style='fill:red'",
        "requiredExtensions='x'", "xml:base='https://evil.test/'", "xml:base=''",
        "data-x='1'", "target='_blank'", "role='img'", "aria-label='x'",
        "tabindex='0'", "xml:lang='en'", "systemLanguage='en'", "begin='0s'",
        "STYLE='fill:red'", "Fill='red'", "src='x'", "ping='x'", "lang='en'",
    ],
)  # fmt: skip
def test_every_attribute_outside_the_allowlist_is_refused(
    tmp_path: Path, attr: str
) -> None:
    name = attr.split("=")[0]
    refused(tmp_path, svg("<rect/>", attr), r"attribute .* on <svg> is not allowed")
    refused(tmp_path / "c", svg(f"<g><rect {attr}/></g>"), r"on <rect>")
    assert name  # the name is part of the message checked below
    with pytest.raises(ValueError, match=re.escape(name.split(":")[-1])):
        build(tmp_path / "d", "m.svg", svg("<rect/>", attr), slot="mascot")


def test_a_prefixed_attribute_in_an_unlisted_namespace_is_refused(
    tmp_path: Path,
) -> None:
    refused(tmp_path, svg("<rect xmlns:z='urn:z' z:onmouseover='x()'/>"), "namespace")
    refused(tmp_path / "x", svg("<rect xlink:title='x'/>"), "attribute")


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)", "JavaScript:alert(1)", "data:image/png;base64,AA",
        "expression(alert(1))", "@import 'x'", "a\\72 b", "a /* c */ b",
        "a\x7fb",
    ],
)  # fmt: skip
def test_hostile_values_are_refused_on_any_allowed_attribute(
    tmp_path: Path, value: str
) -> None:
    attr = value.replace("&", "&amp;").replace("'", "&apos;")
    refused(tmp_path, svg(f"<rect fill='{attr}'/>"), "value")


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)", "https://example.com/x", "//example.com/x",
        "data:image/png;base64,AAAA", "other.svg#a", "x", "", " #a",
    ],
)  # fmt: skip
@pytest.mark.parametrize("name", ["href", "xlink:href"])
def test_href_must_be_a_same_file_fragment(
    tmp_path: Path, href: str, name: str
) -> None:
    refused(tmp_path, svg(f"<use {name}='{href}'/>"), "href")


@pytest.mark.parametrize(
    "value",
    [
        "url(https://evil.test/a)", "url(//evil.test/a)", "url('#g')",
        'url("#g")', "url()", "url(#)", "URL(x)", "url( data:x )",
        "url(#a b)", "url(#a) url(https://evil.test/)", "url(#g",
    ],
)  # fmt: skip
def test_url_in_a_value_needs_a_fragment_id_and_a_closing_paren(
    tmp_path: Path, value: str
) -> None:
    attr = value.replace("'", "&apos;").replace('"', "&quot;")
    refused(tmp_path, svg(f"<rect fill='{attr}'/>"), "(url|data:)")


@pytest.mark.parametrize(
    "prefix",
    [
        b"<!DOCTYPE svg>", b"<!doctype svg>",
        b"<!DOCTYPE svg [<!ENTITY a 'b'>]>", b"<!ENTITY a 'b'>",
    ],
)  # fmt: skip
def test_doctype_and_entity_declarations_are_refused(
    tmp_path: Path, prefix: bytes
) -> None:
    refused(tmp_path, prefix + svg("<rect/>"), "DOCTYPE or ENTITY")


def test_a_billion_laughs_payload_is_refused_before_it_is_parsed(
    tmp_path: Path,
) -> None:
    lol = "<!DOCTYPE lolz [<!ENTITY a 'lol'>"
    for i in range(1, 9):
        prev = "&a;" if i == 1 else f"&e{i - 1};"
        lol += f"<!ENTITY e{i} '{prev * 10}'>"
    lol += "]>"
    refused(tmp_path, lol.encode() + svg("<text>&e8;</text>"), "DOCTYPE or ENTITY")


def test_a_utf16_svg_cannot_hide_a_doctype(tmp_path: Path) -> None:
    text = "<!DOCTYPE svg [<!ENTITY a 'b'>]><svg xmlns='" + SVG_NS + "'/>"
    refused(tmp_path, b"\xff\xfe" + text.encode("utf-16-le"), "UTF-8")


def test_not_xml_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, b"<svg xmlns='" + SVG_NS.encode() + b"'><g></svg>", "valid XML")
    refused(tmp_path / "t", YAML, "valid XML")


@pytest.mark.parametrize(
    "pi",
    [
        b'<?xml-stylesheet type="text/css" href="https://evil.test/a.css"?>',
        b"<?xml-stylesheet href='#s'?>",
        b"<?php echo 1 ?>",
        b"<?XML-STYLESHEET href='x'?>",
    ],
)
def test_processing_instructions_are_refused(tmp_path: Path, pi: bytes) -> None:
    refused(tmp_path, pi + svg("<rect/>"), "processing instruction")
    inside = svg("<rect/>").replace(b"</svg>", pi + b"</svg>")
    refused(tmp_path / "i", inside, "processing instruction")


def test_cdata_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, svg("<text><![CDATA[hi]]></text>"), "CDATA")


def test_a_hostile_svg_is_not_published(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build(tmp_path, "m.svg", svg("<script>x</script>"), slot="mascot")
    assert not (tmp_path / "out" / "m.svg").exists()
