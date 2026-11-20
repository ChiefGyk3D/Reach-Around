# SPDX-License-Identifier: Apache-2.0
"""The collapsed flag on a smart stack group."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_smart_stack_config import raw

from reach_around.config import SiteConfig


def test_collapsed_defaults_to_false_and_validates() -> None:
    assert SiteConfig.model_validate(raw()).tabs[1].collapsed is False
    data = raw()
    data["tabs"][1]["collapsed"] = True
    assert SiteConfig.model_validate(data).tabs[1].collapsed is True


def test_collapsed_needs_the_stack_layout() -> None:
    data = raw()
    data["page"] = {}
    data["tabs"][1]["collapsed"] = True
    with pytest.raises(ValidationError, match=r"collapsed needs page\.layout: stack"):
        SiteConfig.model_validate(data)


def test_the_watch_group_cannot_be_collapsed() -> None:
    data = raw()
    data["tabs"][0]["collapsed"] = True
    with pytest.raises(ValidationError, match="the Watch group cannot be collapsed"):
        SiteConfig.model_validate(data)
