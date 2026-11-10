# SPDX-License-Identifier: Apache-2.0
"""The kit ``disclosure`` statement: gated, plain text, capped (#421).

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
TEXT = "An example statement. It has two example sentences."


def kit_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests.",
    }
    doc.update(kw)
    return doc


def test_the_statement_validates_and_defaults_to_none() -> None:
    assert KitConfig.model_validate(kit_doc(disclosure=TEXT)).disclosure == TEXT
    assert KitConfig.model_validate(kit_doc()).disclosure is None


def test_the_statement_is_gated_and_counts_as_gated_content() -> None:
    assert visibility("disclosure") == "gated"
    assert "disclosure" in DISPLAY_POLICY
    assert policy_gaps(KitConfig.model_fields) == []
    kit = KitConfig.model_validate(kit_doc(disclosure=TEXT))
    assert kit.has_gated_content
    assert "disclosure" not in public_projection(kit, None)
    assert gated_projection(kit, None)["disclosure"] == TEXT
    assert not KitConfig.model_validate(kit_doc()).has_gated_content


def test_a_gated_statement_needs_the_gate_switch() -> None:
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
        "kit": kit_doc(disclosure=TEXT),
    }
    with pytest.raises(ValidationError, match="kit_gate"):
        SiteConfig.model_validate(doc)
    SiteConfig.model_validate({**doc, "features": {"kit_gate": True}})


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(disclosure=text))


def test_length_cap() -> None:
    assert KitConfig.model_validate(kit_doc(disclosure="a" * 500))
    with pytest.raises(ValidationError, match="string_too_long"):
        KitConfig.model_validate(kit_doc(disclosure="a" * 501))


@pytest.mark.parametrize("bad", [5, ["a list"], {"text": "x"}, True])
def test_wrong_types_are_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(disclosure=bad))
