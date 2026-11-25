# SPDX-License-Identifier: Apache-2.0
"""The compact tile grid of the smart stack (#873): config contract."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from test_smart_stack_config import raw as stack_raw

from reach_around.config import SiteConfig


def compact() -> dict[str, Any]:
    data = stack_raw()
    data["page"]["density"] = "compact"
    return data


def messages(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        SiteConfig.model_validate(data)
    return [e["msg"] for e in caught.value.errors(include_url=False)]


def test_density_defaults_to_comfortable_and_accepts_compact() -> None:
    assert SiteConfig.model_validate(stack_raw()).page.density == "comfortable"
    assert SiteConfig.model_validate(compact()).page.density == "compact"


@pytest.mark.parametrize("bad", ["tight", "Compact", "", 1, None, ["compact"]])
def test_an_unknown_density_is_refused_without_echoing_it(bad: object) -> None:
    data = stack_raw()
    data["page"]["density"] = bad
    found = messages(data)
    assert len(found) == 1, found
    if isinstance(bad, str) and bad.strip():
        assert bad not in found[0]


def test_compact_density_needs_the_stack_layout() -> None:
    data = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "page": {"density": "compact"},
        "tabs": [{"id": "home", "title": "Home", "blocks": []}],
    }
    assert any("page.density needs page.layout: stack" in m for m in messages(data))


def test_compact_density_refuses_a_featured_link() -> None:
    data = compact()
    data["page"]["featured"] = {"link": "follow-one", "kicker": "New"}
    assert any("page.featured" in m and "compact" in m for m in messages(data))


@pytest.mark.parametrize("field", ["collapsed", "spoke", "style"])
def test_compact_density_refuses_group_modes_that_hide_links(field: str) -> None:
    data = compact()
    data["page"]["url"] = "https://example.com"
    data["tabs"][1][field] = "rows" if field == "style" else True
    found = messages(data)
    assert any("compact" in m for m in found), found


def test_a_group_may_set_its_column_count_only_in_compact_density() -> None:
    data = compact()
    data["tabs"][1]["columns"] = 2
    assert SiteConfig.model_validate(data).tabs[1].columns == 2
    plain = stack_raw()
    plain["tabs"][1]["columns"] = 2
    assert any("columns needs page.density: compact" in m for m in messages(plain))


def test_the_watch_group_refuses_columns_with_a_fixed_reason() -> None:
    data = compact()
    data["tabs"][0]["columns"] = 1
    found = messages(data)
    assert any(
        "the Watch group sets no columns (its column count is automatic)" in m
        for m in found
    ), found


@pytest.mark.parametrize("bad", [0, 7, -1, 2.5, "3", True])
def test_columns_is_a_whole_number_from_one_to_six(bad: object) -> None:
    data = compact()
    data["tabs"][1]["columns"] = bad
    assert messages(data)


def test_the_watch_group_may_hold_a_third_link_in_compact_density() -> None:
    data = compact()
    data["tabs"][0]["blocks"].append(
        {"id": "watch-three", "title": "Watch three", "url": "https://example.com/w3"}
    )
    config = SiteConfig.model_validate(data)
    assert config.page.watch == ["watch-one", "watch-two"]
    too_many = copy.deepcopy(data)
    for number in range(4, 8):
        too_many["tabs"][0]["blocks"].append(
            {
                "id": f"watch-{number}",
                "title": f"Watch {number}",
                "url": f"https://example.com/w{number}",
            }
        )
    assert any("2 to 6" in m for m in messages(too_many))


@pytest.mark.parametrize("intent", ["gear", "more", "hire"])
def test_the_new_group_intents_are_accepted_once_each(intent: str) -> None:
    data = compact()
    group = copy.deepcopy(data["tabs"][1])
    group.update(id=intent, title=intent.title(), intent=intent)
    for link in group["blocks"]:
        link["id"] = f"{intent}-{link['id']}"
    data["tabs"].append(group)
    assert SiteConfig.model_validate(data).tabs[-1].intent == intent
    twin = copy.deepcopy(group)
    twin["id"] = f"{intent}-again"
    for link in twin["blocks"]:
        link["id"] = f"again-{link['id']}"
    data["tabs"].append(twin)
    assert any("may appear only once" in m for m in messages(data))


def test_a_group_of_one_link_is_still_refused_in_compact_density() -> None:
    data = compact()
    data["tabs"][1]["blocks"] = data["tabs"][1]["blocks"][:1]
    assert any("2 to 6" in m for m in messages(data))
