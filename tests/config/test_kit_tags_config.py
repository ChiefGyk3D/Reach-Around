# SPDX-License-Identifier: Apache-2.0
"""The kit ``tags`` list: content category chips in the public header (#422).

Every name here is synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config.kit import (
    DISPLAY_POLICY,
    KitConfig,
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


def test_tags_validate_and_default_to_none() -> None:
    kit = KitConfig.model_validate(kit_doc(tags=["Gaming", "Hardware"]))
    assert kit.tags == ["Gaming", "Hardware"]
    assert KitConfig.model_validate(kit_doc()).tags == []


def test_tags_are_public_in_both_projections_and_are_not_gated_content() -> None:
    assert visibility("tags") == "public"
    assert policy_gaps(KitConfig.model_fields) == []
    assert "tags" in DISPLAY_POLICY
    kit = KitConfig.model_validate(kit_doc(tags=["Gaming"]))
    assert public_projection(kit, None)["tags"] == ["Gaming"]
    assert gated_projection(kit, None)["tags"] == ["Gaming"]
    assert not kit.has_gated_content


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(tags=[text]))


def test_length_cap() -> None:
    assert KitConfig.model_validate(kit_doc(tags=["a" * 30]))
    with pytest.raises(ValidationError, match="string_too_long"):
        KitConfig.model_validate(kit_doc(tags=["a" * 31]))


def test_count_cap() -> None:
    names = [f"tag{n}" for n in range(8)]
    assert KitConfig.model_validate(kit_doc(tags=names))
    with pytest.raises(ValidationError, match="too_long"):
        KitConfig.model_validate(kit_doc(tags=[*names, "tag8"]))


def test_duplicates_are_refused_ignoring_case_and_width() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        KitConfig.model_validate(kit_doc(tags=["Gaming", "gaming"]))
    with pytest.raises(ValidationError, match="more than once"):
        KitConfig.model_validate(kit_doc(tags=["Tech", "Ｔech"]))


@pytest.mark.parametrize("bad", ["Gaming", 5, [5], [["nested"]], [None], {"a": 1}])
def test_wrong_types_are_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(tags=bad))
