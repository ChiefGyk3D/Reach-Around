# SPDX-License-Identifier: Apache-2.0
"""The production smart stack configuration contract."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig


def raw() -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "page": {
            "layout": "stack",
            "status": {
                "text": "Live Friday at 8 PM Eastern",
                "schedule": "Next stream: November 13",
            },
            "watch": ["watch-one", "watch-two"],
        },
        "tabs": [
            {
                "id": "watch",
                "title": "Watch",
                "intent": "watch",
                "blocks": [
                    {
                        "id": "watch-one",
                        "title": "Watch one",
                        "url": "https://example.com/watch-one",
                        "icon": "twitch",
                    },
                    {
                        "id": "watch-two",
                        "title": "Watch two",
                        "url": "https://example.com/watch-two",
                        "icon": "kick",
                    },
                ],
            },
            {
                "id": "follow",
                "title": "Follow",
                "intent": "follow",
                "blocks": [
                    {
                        "id": "follow-one",
                        "title": "Follow one",
                        "url": "https://example.com/follow-one",
                    },
                    {
                        "id": "follow-two",
                        "title": "Follow two",
                        "url": "https://example.com/follow-two",
                    },
                ],
            },
        ],
    }


def test_stack_is_the_only_promoted_variant() -> None:
    config = SiteConfig.model_validate(raw())
    assert config.page.layout == "stack"
    for prototype in ("chips", "bento", "tabs"):
        data = raw()
        data["page"]["layout"] = prototype
        with pytest.raises(ValidationError):
            SiteConfig.model_validate(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["page"].pop("status"), "page.status"),
        (lambda data: data["page"].update(watch=["watch-one"]), "exactly two"),
        (lambda data: data["tabs"][1].pop("intent"), "needs intent"),
        (lambda data: data["tabs"][1]["blocks"].pop(), "2 to 6 links"),
        (
            lambda data: data["tabs"][1]["blocks"].append(
                {"id": "copy", "markdown": "Synthetic copy."}
            ),
            "support only links",
        ),
        (lambda data: data.update(theme={"mode": "light"}), "dark theme"),
    ],
)
def test_stack_refuses_incomplete_or_unsupported_content(
    change: Any, message: str
) -> None:
    data = raw()
    change(data)
    with pytest.raises(ValidationError, match=message):
        SiteConfig.model_validate(data)
