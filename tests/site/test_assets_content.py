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
        "<style>.a { fill: url(#g); } circle { stroke: url(  #g); }</style>"
        "<style>b { fill: url(#h); }</style>",
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


# --- SVG: document-level tricks ---------------------------------------------

XML_NS = "http://www.w3.org/XML/1998/namespace"


@pytest.mark.parametrize(
    "attrs",
    [
        "xml:base='https://evil.test/a.svg'",
        "xml:base='#frag'",
        "xml:base=''",
    ],
)
def test_xml_base_is_refused_on_the_root_and_on_children(
    tmp_path: Path, attrs: str
) -> None:
    refused(tmp_path, svg("<use href='#x'/>", attrs), "base")
    refused(tmp_path / "c", svg(f"<g {attrs}><use href='#x'/></g>"), "base")


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
    refused(
        tmp_path / "i", svg("<rect/>").replace(b"</svg>", pi + b"</svg>"), "processing"
    )


def test_the_xml_declaration_is_not_a_processing_instruction(tmp_path: Path) -> None:
    data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + svg("<rect/>")
    out = build(tmp_path, "m.svg", data, slot="mascot")
    assert (out / "m.svg").is_file()


def test_a_plain_comment_outside_style_is_accepted_and_kept(tmp_path: Path) -> None:
    data = svg("<!-- a note --><rect/>")
    out = build(tmp_path, "m.svg", data, slot="mascot")
    assert (out / "m.svg").read_bytes() == data


def test_the_placeholder_mascot_keeps_its_comment_byte_for_byte(
    tmp_path: Path,
) -> None:
    assert b"<!--" in MASCOT.read_bytes()
    out = build(tmp_path, "m.svg", MASCOT.read_bytes(), slot="mascot")
    assert (out / "m.svg").read_bytes() == MASCOT.read_bytes()


@pytest.mark.parametrize(
    "body",
    [
        "<style>a { fill: red } <!-- x --> b { fill: blue }</style>",
        "<style>a { fill: url<!-- x -->(https://evil.test/a) }</style>",
    ],
)
def test_a_comment_inside_style_is_refused(tmp_path: Path, body: str) -> None:
    refused(tmp_path, svg(body), "comment")


def test_cdata_is_refused(tmp_path: Path) -> None:
    refused(tmp_path, svg("<style><![CDATA[a { fill: red }]]></style>"), "CDATA")
    refused(tmp_path / "t", svg("<text><![CDATA[hi]]></text>"), "CDATA")


@pytest.mark.parametrize(
    "body",
    [
        "<style><x:style xmlns:x='urn:x'/>@import url(https://evil.test/a.css);</style>",
        "<style><b/>a { fill: url(https://evil.test/a) }</style>",
        "<style>a { fill: red }<style>@import url(x);</style></style>",
        "<style><svg:desc xmlns:svg='" + SVG_NS + "'/></style>",
    ],
)
def test_any_element_inside_style_is_refused(tmp_path: Path, body: str) -> None:
    refused(tmp_path, svg(body), "inside a style")


def test_each_of_several_sibling_styles_is_screened(tmp_path: Path) -> None:
    ok = "<style>a { fill: url(#g) }</style><style>b { fill: url(#h) }</style>"
    out = build(tmp_path, "m.svg", svg(ok), slot="mascot")
    assert (out / "m.svg").is_file()
    bad = "<style>a { fill: red }</style><style>@import url(x);</style>"
    refused(tmp_path / "b", svg(bad), "@import")
    bad2 = "<style>a { fill: red }</style><style>b {}</style><g/><style>x{fill:url(https://e.test/)}</style>"
    refused(tmp_path / "c", svg(bad2), "url")


@pytest.mark.parametrize(
    "css",
    [
        "a { fill: red } /* note */",
        "a { fill: url(#g) /* x */ }",
        "a { fill: u/**/rl(https://evil.test/a) }",
        "/*",
    ],
)
def test_css_comment_openers_are_refused_everywhere(tmp_path: Path, css: str) -> None:
    refused(tmp_path, svg(f"<style>{css}</style>"), "comment")
    refused(tmp_path / "s", svg(f"<rect style='{css}'/>"), "comment")
    refused(tmp_path / "a", svg(f"<rect data-x='{css}'/>"), "comment")


@pytest.mark.parametrize(
    "css",
    [
        "fill: url('#g')",
        'fill: url("#g")',
        "fill: url( 'https://e.test/x' )",
        "fill: URL(x)",
        "fill: url()",
    ],
)
def test_quotes_and_non_fragment_targets_in_url_are_refused(
    tmp_path: Path, css: str
) -> None:
    attr = css.replace('"', "&quot;")
    refused(tmp_path, svg(f'<rect style="{attr}"/>'), "url")
    refused(tmp_path / "e", svg(f"<style>a {{ {css} }}</style>"), "url")
