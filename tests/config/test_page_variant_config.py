# SPDX-License-Identifier: Apache-2.0
"""Page-level review layout config is strict and self-contained."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig


def data(*, layout: str = "stack") -> dict[str, Any]:
    def links(intent: str, count: int = 2) -> list[dict[str, str]]:
        return [
            {
                "id": f"{intent}-{number}",
                "title": f"{intent.title()} {number}",
                "url": f"https://example.com/{intent}/{number}",
                "icon": "link",
            }
            for number in range(1, count + 1)
        ]

    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "page": {
            "layout": layout,
            "status": {
                "text": "Live Friday at 8 PM Eastern",
                "schedule": "Next stream: November 13",
            },
            "watch": ["watch-twitch", "watch-kick"],
        },
        "tabs": [
            {
                "id": "watch",
                "title": "Watch",
                "intent": "watch",
                "blocks": [
                    {
                        "id": "watch-twitch",
                        "title": "Watch on Twitch",
                        "url": "https://example.com/twitch",
                        "icon": "twitch",
                    },
                    {
                        "id": "watch-kick",
                        "title": "Watch on Kick",
                        "url": "https://example.com/kick",
                        "icon": "kick",
                    },
                ],
            },
            *[
                {
                    "id": intent,
                    "title": title,
                    "intent": intent,
                    "blocks": links(intent),
                }
                for intent, title in (
                    ("videos", "Videos"),
                    ("follow", "Follow"),
                    ("community", "Community"),
                    ("support", "Support"),
                    ("work", "Work with me"),
                    ("shop", "Shop"),
                    ("blog", "Blog"),
                )
            ],
        ],
    }


def test_classic_is_the_page_layout_default() -> None:
    raw = data()
    raw.pop("page")
    config = SiteConfig.model_validate(raw)
    assert config.page.layout == "classic"
    assert config.page.status is None
    assert config.page.watch == []


@pytest.mark.parametrize("layout", ["classic", "stack", "chips", "bento", "tabs"])
def test_every_page_layout_value_validates(layout: str) -> None:
    raw = data(layout=layout)
    if layout == "classic":
        raw["page"] = {"layout": "classic"}
    assert SiteConfig.model_validate(raw).page.layout == layout


def refused(raw: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        SiteConfig.model_validate(raw)
    return str(caught.value)


def test_unknown_page_layout_is_refused() -> None:
    raw = data()
    raw["page"]["layout"] = "magazine"
    assert "page.layout" in refused(raw)


def test_review_layout_requires_status_text() -> None:
    raw = data()
    raw["page"].pop("status")
    assert "status" in refused(raw)


def test_review_layout_refuses_light_theme() -> None:
    raw = data()
    raw["theme"] = {"mode": "light"}
    assert "dark theme" in refused(raw)


@pytest.mark.parametrize(
    ("watch", "message"),
    [
        (["watch-twitch"], "exactly two"),
        (["watch-twitch", "missing"], "missing"),
        (["watch-twitch", "videos-1"], "one Twitch and one Kick"),
    ],
)
def test_watch_pair_is_exact_resolved_and_platform_neutral(
    watch: list[str], message: str
) -> None:
    raw = data()
    raw["page"]["watch"] = watch
    assert message in refused(raw)


@pytest.mark.parametrize("count", [1, 7])
def test_each_intent_group_has_two_to_six_links(count: int) -> None:
    raw = data()
    first = raw["tabs"][1]["blocks"][0]
    raw["tabs"][1]["blocks"] = [
        {**first, "id": f"videos-x-{number}"} for number in range(1, count + 1)
    ]
    assert "2 to 6" in refused(raw)


def test_group_order_and_titles_are_kept_from_tabs() -> None:
    config = SiteConfig.model_validate(data())
    groups = [(tab.intent, tab.title) for tab in config.tabs if tab.intent != "watch"]
    assert groups == [
        ("videos", "Videos"),
        ("follow", "Follow"),
        ("community", "Community"),
        ("support", "Support"),
        ("work", "Work with me"),
        ("shop", "Shop"),
        ("blog", "Blog"),
    ]


def test_watch_order_is_configured_not_platform_fixed() -> None:
    raw = data()
    raw["page"]["watch"] = ["watch-kick", "watch-twitch"]
    assert SiteConfig.model_validate(raw).page.watch == [
        "watch-kick",
        "watch-twitch",
    ]
