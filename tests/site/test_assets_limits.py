# SPDX-License-Identifier: Apache-2.0
"""SVG checks: namespace removal never crashes, size caps, function-style values."""

from __future__ import annotations

from pathlib import Path

import pytest

from reach_around.site.assetcheck import ALLOWED_ATTRIBUTES, check_content

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
MASCOT = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "instance"
    / "media"
    / "mascot.svg"
)
KEY = "theme.mascot"


def svg(body: str, attrs: str = "") -> bytes:
    return f"<svg xmlns='{SVG_NS}' {attrs}>{body}</svg>".encode()


def accepted(data: bytes) -> None:
    check_content(KEY, "m.svg", data)


def refused(data: bytes, reason: str) -> None:
    with pytest.raises(ValueError, match=rf"{KEY}: 'm\.svg' is refused.*{reason}"):
        check_content(KEY, "m.svg", data)


# --- 1. namespace removal is a refusal, never a crash -------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"<svg xmlns=''><rect/></svg>",
        f"<svg xmlns='{SVG_NS}'><g xmlns=''><script/></g></svg>".encode(),
        f"<svg xmlns='{SVG_NS}'><g xmlns=''><rect/></g></svg>".encode(),
        f"<svg xmlns='{SVG_NS}' xmlns=''/>".encode(),
        f"<s:svg xmlns:s='{SVG_NS}'><s:g xmlns=''><s:rect/></s:g></s:svg>".encode(),
        f"<s:svg xmlns:s='{SVG_NS}'><rect xmlns='' s:fill='red'/></s:svg>".encode(),
        f"<svg xmlns='{SVG_NS}'><g xmlns=''/></svg>".encode(),
        f"<svg xmlns='{SVG_NS}' xmlns:x=''/>".encode(),
    ],
)
def test_namespace_removal_is_refused_with_the_documented_error(data: bytes) -> None:
    with pytest.raises(ValueError) as caught:
        check_content(KEY, "m.svg", data)
    assert type(caught.value) is ValueError
    assert str(caught.value).startswith(f"{KEY}: 'm.svg' is refused as an svg: ")


# --- 2. complexity caps -------------------------------------------------------


def test_element_count_cap() -> None:
    accepted(svg("<g/>" * 19999))  # the root plus 19999 is exactly 20000
    refused(svg("<g/>" * 20000), "20000 elements")


def test_the_parse_stops_early_once_a_cap_is_exceeded() -> None:
    # Garbage after the limit would make the parser fail with "not valid XML" if
    # it kept going; the cap error proves it stopped at the 20001st element.
    data = svg("<g/>" * 20000) + b"<<<not xml"
    refused(data[: -len(b"</svg><<<not xml")] + b"<<<not xml", "20000 elements")


def test_nesting_depth_cap() -> None:
    accepted(svg("<g>" * 63 + "</g>" * 63))  # the root plus 63 is depth 64
    refused(svg("<g>" * 64 + "</g>" * 64), "nested deeper")


def test_attributes_per_element_cap() -> None:
    names = sorted(n for n in ALLOWED_ATTRIBUTES if n != "href")
    assert len(names) >= 65
    ok = " ".join(f"{n}='1'" for n in names[:64])
    over = " ".join(f"{n}='1'" for n in names[:65])
    accepted(svg(f"<rect {ok}/>"))
    refused(svg(f"<rect {over}/>"), "64 attributes")


def test_namespace_declaration_cap() -> None:
    # The root's own xmlns is one declaration, so seven prefixes make eight.
    def with_prefixes(count: int) -> bytes:
        extra = " ".join(f"xmlns:p{i}='{SVG_NS}'" for i in range(count))
        return svg("<rect/>", extra)

    accepted(with_prefixes(7))
    refused(with_prefixes(8), "namespace declarations")


def test_namespace_declarations_are_counted_across_the_whole_file() -> None:
    inner = "".join(f"<g xmlns:q{i}='{SVG_NS}'/>" for i in range(7))
    accepted(svg(inner))  # 1 + 7
    refused(svg(inner + f"<g xmlns:z='{SVG_NS}'/>"), "namespace declarations")


def test_total_attribute_value_cap() -> None:
    cap = 1024 * 1024
    accepted(svg("<rect/>", f"width='{'1' * cap}'"))
    refused(svg("<rect/>", f"width='{'1' * (cap + 1)}'"), "attribute text")


def test_attribute_value_characters_add_up_across_elements() -> None:
    half = 512 * 1024
    accepted(svg(f"<rect width='{'1' * half}'/><rect width='{'1' * half}'/>"))
    refused(
        svg(f"<rect width='{'1' * half}'/><rect width='{'1' * (half + 1)}'/>"),
        "attribute text",
    )


# --- 3. function-style values -------------------------------------------------

FUNCTIONS = [
    "image(x)", "image-set(x 1x)", "-webkit-image-set(x 1x)", "src(x)",
    "cross-fade(x)", "element(#a)", "paint(x)", "calc(1+2)", "var(--x)",
    "url(#a) image(x)", "url(#a) calc(1)", "(1)", "a(b(c))",
]  # fmt: skip


@pytest.mark.parametrize("attr", ["fill", "stroke", "filter", "mask", "clip-path"])
@pytest.mark.parametrize("value", FUNCTIONS)
def test_function_style_values_are_refused_on_paint_and_reference_attributes(
    attr: str, value: str
) -> None:
    refused(svg(f"<rect {attr}='{value}'/>"), r"(function|url|\()")


@pytest.mark.parametrize("attr", ["font-family", "values", "d", "points", "class"])
def test_function_style_values_are_refused_on_every_other_attribute(attr: str) -> None:
    for value in ("image(x)", "calc(1)", "var(--x)", "a(b(c))"):
        refused(svg(f"<rect {attr}='{value}'/>"), r"(function|\()")


def test_nested_url_forms_are_refused() -> None:
    refused(svg("<rect fill='url(url(#a))'/>"), "url")


@pytest.mark.parametrize(
    "value",
    [
        "url(#grad)", "url( #grad )", "red", "url(#a) url(#b)", "#fff",
        "url(#a-b.c_d:e)",
    ],
)  # fmt: skip
def test_plain_urls_and_colors_still_pass(value: str) -> None:
    accepted(svg(f"<rect fill='{value}' stroke='{value}'/>"))


@pytest.mark.parametrize("attr", ["transform", "gradientTransform"])
@pytest.mark.parametrize(
    "value",
    [
        "translate(1 2) rotate(45)", "matrix(1,0,0,1,5,5)", "scale(2)",
        "scale(1.5e-1, -2)", "skewX(10) skewY(-10)", "rotate(45 1 2)",
        "translate(1)rotate(2)", "  scale(2)  ", "scale(2),translate(1)",
    ],
)  # fmt: skip
def test_listed_transform_functions_with_numbers_pass(attr: str, value: str) -> None:
    accepted(svg(f"<g {attr}='{value}'/>"))


@pytest.mark.parametrize("attr", ["transform", "gradientTransform"])
@pytest.mark.parametrize(
    "value",
    [
        "Scale(2)", "TRANSLATE(1)", "translate(a b)", "translate(1px)",
        "translate(url(#a))", "translate(scale(1))", "calc(1)", "scale(2) foo(1)",
        "scale(2", "translate(1) image(x)", "perspective(1)",
        "scale(1)x", "translate(var(--a))",
    ],
)  # fmt: skip
def test_other_functions_and_non_numeric_arguments_are_refused_in_transforms(
    attr: str, value: str
) -> None:
    refused(svg(f"<g {attr}='{value}'/>"), r"(numeric|url)")


def test_the_placeholder_mascot_still_passes_byte_for_byte() -> None:
    accepted(MASCOT.read_bytes())
