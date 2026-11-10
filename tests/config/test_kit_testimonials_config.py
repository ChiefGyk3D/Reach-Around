# SPDX-License-Identifier: Apache-2.0
"""The kit ``testimonials`` list: partner quotes on the gated half (#420).

Every name here is synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.kit import (
    DISPLAY_POLICY,
    KitConfig,
    Testimonial,
    gated_projection,
    policy_gaps,
    public_projection,
    visibility,
)

HOSTILE = [
    "<b>hi</b>",
    "a < b",
    "x>y",
    "bad" + chr(0) + "byte",
    "line" + chr(10) + "break",
    "zero" + chr(0x200B) + "width",
    "bidi" + chr(0x202E) + "flip",
    " leading",
    "trailing ",
    "",
]


def kit_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests.",
    }
    doc.update(kw)
    return doc


def quote(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "quote": "An example quote.",
        "name": "Example Person",
        "brand": "Example Brand",
    }
    doc.update(kw)
    return doc


def test_a_testimonial_validates_and_the_default_is_none() -> None:
    kit = KitConfig.model_validate(kit_doc(testimonials=[quote()]))
    assert kit.testimonials[0].quote == "An example quote."
    assert kit.testimonials[0].brand == "Example Brand"
    assert KitConfig.model_validate(kit_doc()).testimonials == []


def test_the_brand_is_optional() -> None:
    assert Testimonial.model_validate({"quote": "q", "name": "n"}).brand is None


def test_testimonials_are_gated_and_count_as_gated_content() -> None:
    assert visibility("testimonials") == "gated"
    assert "testimonials" in DISPLAY_POLICY
    assert policy_gaps(KitConfig.model_fields) == []
    kit = KitConfig.model_validate(kit_doc(testimonials=[quote()]))
    assert kit.has_gated_content
    assert "testimonials" not in public_projection(kit, None)
    assert gated_projection(kit, None)["testimonials"][0].name == "Example Person"


def test_gated_testimonials_need_the_gate_switch() -> None:
    doc = {
        "version": 1,
        "creator": {"name": "Example"},
        "tabs": [
            {
                "id": "links",
                "title": "Links",
                "blocks": [{"id": "h", "title": "H", "url": "https://example.com/"}],
            }
        ],
        "kit": kit_doc(testimonials=[quote()]),
    }
    with pytest.raises(ValidationError, match="kit_gate"):
        SiteConfig.model_validate(doc)
    SiteConfig.model_validate({**doc, "features": {"kit_gate": True}})


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_is_refused_everywhere(text: str) -> None:
    for field in ("quote", "name", "brand"):
        with pytest.raises(ValidationError):
            Testimonial.model_validate(quote(**{field: text}))


@pytest.mark.parametrize(
    ("field", "limit"), [("quote", 300), ("name", 80), ("brand", 80)]
)
def test_length_caps(field: str, limit: int) -> None:
    assert Testimonial.model_validate(quote(**{field: "a" * limit}))
    with pytest.raises(ValidationError, match="string_too_long"):
        Testimonial.model_validate(quote(**{field: "a" * (limit + 1)}))


def test_count_cap() -> None:
    many = [quote(name=f"Person {n}") for n in range(10)]
    assert KitConfig.model_validate(kit_doc(testimonials=many))
    with pytest.raises(ValidationError, match="too_long"):
        KitConfig.model_validate(kit_doc(testimonials=[*many, quote()]))


@pytest.mark.parametrize(
    "bad",
    [
        {"quote": 5},
        {"quote": None},
        {"name": ["a"]},
        {"brand": 4},
        {"surprise": 1},
    ],
)
def test_wrong_types_and_extra_keys_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Testimonial.model_validate(quote(**bad))


def test_name_and_quote_are_required() -> None:
    for missing in ("quote", "name"):
        doc = quote()
        del doc[missing]
        with pytest.raises(ValidationError, match="missing"):
            Testimonial.model_validate(doc)
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(testimonials={"quote": "q", "name": "n"}))
