# SPDX-License-Identifier: Apache-2.0
"""The kit ``services`` list: named packages on the gated half (#419).

Every name here is synthetic.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.kit import (
    DISPLAY_POLICY,
    KitConfig,
    ServicePackage,
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


def package(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example package",
        "included": ["One example item", "Another example item"],
        "price_text": "Ask me!",
    }
    doc.update(kw)
    return doc


def test_a_package_validates_and_the_default_is_no_services() -> None:
    kit = KitConfig.model_validate(kit_doc(services=[package()]))
    assert kit.services[0].title == "Example package"
    assert kit.services[0].included == ["One example item", "Another example item"]
    assert kit.services[0].price_text == "Ask me!"
    assert KitConfig.model_validate(kit_doc()).services == []


def test_included_and_price_are_optional() -> None:
    one = ServicePackage.model_validate({"title": "Bare package"})
    assert one.included == [] and one.price_text is None


def test_services_are_gated_and_count_as_gated_content() -> None:
    assert visibility("services") == "gated"
    assert policy_gaps(KitConfig.model_fields) == []
    assert "services" in DISPLAY_POLICY
    kit = KitConfig.model_validate(kit_doc(services=[package()]))
    assert kit.has_gated_content
    assert "services" not in public_projection(kit, None)
    assert gated_projection(kit, None)["services"][0].title == "Example package"
    assert not KitConfig.model_validate(kit_doc()).has_gated_content


def test_gated_services_need_the_gate_switch() -> None:
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
        "kit": kit_doc(services=[package()]),
    }
    with pytest.raises(ValidationError, match="kit_gate"):
        SiteConfig.model_validate(doc)
    SiteConfig.model_validate({**doc, "features": {"kit_gate": True}})


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_is_refused_everywhere(text: str) -> None:
    for doc in (
        package(title=text),
        package(included=[text]),
        package(price_text=text),
    ):
        with pytest.raises(ValidationError):
            ServicePackage.model_validate(doc)


@pytest.mark.parametrize(
    ("make", "limit"),
    [
        (lambda t: package(title=t), 80),
        (lambda t: package(included=[t]), 120),
        (lambda t: package(price_text=t), 80),
    ],
)
def test_length_caps(make: Any, limit: int) -> None:
    assert ServicePackage.model_validate(make("a" * limit))
    with pytest.raises(ValidationError, match="string_too_long"):
        ServicePackage.model_validate(make("a" * (limit + 1)))


def test_count_caps() -> None:
    items = [f"item {n}" for n in range(12)]
    assert ServicePackage.model_validate(package(included=items))
    with pytest.raises(ValidationError, match="too_long"):
        ServicePackage.model_validate(package(included=[*items, "one more"]))
    many = [package(title=f"Package {n}") for n in range(20)]
    assert KitConfig.model_validate(kit_doc(services=many))
    with pytest.raises(ValidationError, match="too_long"):
        KitConfig.model_validate(kit_doc(services=[*many, package(title="x")]))


@pytest.mark.parametrize(
    "bad",
    [
        {"title": 5},
        {"title": None},
        {"included": "one string, not a list"},
        {"included": [5]},
        {"included": [["nested"]]},
        {"price_text": 40},
        {"price_text": date(2027, 3, 1)},
        {"surprise": 1},
    ],
)
def test_wrong_types_and_extra_keys_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ServicePackage.model_validate(package(**bad))


def test_services_must_be_a_list() -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(services={"title": "x"}))
