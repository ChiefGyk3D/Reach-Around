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


def test_stack_requires_exactly_one_watch_group() -> None:
    data = raw()
    data["tabs"][0]["intent"] = "follow"

    with pytest.raises(ValidationError, match="exactly one.*watch"):
        SiteConfig.model_validate(data)


def test_stack_watch_links_must_belong_to_the_watch_group() -> None:
    data = raw()
    data["tabs"][0]["blocks"], data["tabs"][1]["blocks"] = (
        data["tabs"][1]["blocks"],
        data["tabs"][0]["blocks"],
    )

    with pytest.raises(ValidationError, match="page.watch.*Watch group"):
        SiteConfig.model_validate(data)


@pytest.mark.parametrize("schedule_key", ["show_from", "show_until"])
def test_stack_watch_links_cannot_be_scheduled(schedule_key: str) -> None:
    data = raw()
    data["tabs"][0]["blocks"][0][schedule_key] = "2027-04-01"

    with pytest.raises(ValidationError, match="page.watch.*scheduled"):
        SiteConfig.model_validate(data)


def featured(**changes: Any) -> dict[str, Any]:
    data = raw()
    data["page"]["featured"] = {"link": "follow-one", "kicker": "New this week"}
    data["page"]["featured"].update(changes)
    return data


def test_featured_is_optional_and_validates() -> None:
    assert SiteConfig.model_validate(raw()).page.featured is None
    config = SiteConfig.model_validate(
        featured(show_from="2027-03-01", show_until="2027-03-31")
    )
    assert config.page.featured is not None
    assert config.page.featured.link == "follow-one"
    assert config.page.featured.kicker == "New this week"


def test_featured_refuses_an_unknown_link_id() -> None:
    with pytest.raises(
        ValidationError, match=r"page\.featured link id 'nope' is missing"
    ):
        SiteConfig.model_validate(featured(link="nope"))


def test_featured_refuses_two_items() -> None:
    data = raw()
    data["page"]["featured"] = [
        {"link": "follow-one", "kicker": "One"},
        {"link": "follow-two", "kicker": "Two"},
    ]
    with pytest.raises(ValidationError, match=r"page\.featured accepts one link"):
        SiteConfig.model_validate(data)


def test_featured_refuses_a_backwards_window() -> None:
    with pytest.raises(ValidationError, match="show_until must not be before"):
        SiteConfig.model_validate(
            featured(show_from="2027-03-10", show_until="2027-03-01")
        )


def test_featured_dates_are_plain_days() -> None:
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        SiteConfig.model_validate(featured(show_from="2027-03-01T10:00:00"))


def test_featured_needs_the_stack_layout() -> None:
    data = featured()
    data["page"] = {"layout": "classic", "featured": data["page"]["featured"]}
    with pytest.raises(ValidationError, match=r"page\.featured needs page\.layout"):
        SiteConfig.model_validate(data)


@pytest.mark.parametrize("kicker", ["", " padded", "x" * 61, "two\nlines"])
def test_featured_kicker_is_short_plain_text(kicker: str) -> None:
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(featured(kicker=kicker))
