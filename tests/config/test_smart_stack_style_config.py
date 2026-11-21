# SPDX-License-Identifier: Apache-2.0
"""The style of a smart stack group (#672): buttons, rows or icons."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_smart_stack_config import raw

from reach_around.config import ConfigError, SiteConfig, load_site_config

SOCIALS = [
    {"name": "Twitch", "url": "https://example.com/t", "icon": "twitch"},
    {"name": "Kick", "url": "https://example.com/k", "icon": "kick"},
]


def stack(**styles: str) -> dict[str, Any]:
    """The base stack (Watch, Follow) plus a Community group, styled by intent."""
    data = raw()
    group = copy.deepcopy(data["tabs"][1])
    group.update(id="community", title="Community", intent="community")
    for link in group["blocks"]:
        link["id"] = "c-" + link["id"]
    data["tabs"].append(group)
    for tab in data["tabs"]:
        if tab["intent"] in styles:
            tab["style"] = styles[tab["intent"]]
    return data


def icons_stack() -> dict[str, Any]:
    data = stack(follow="icons")
    data["creator"]["socials"] = SOCIALS
    follow = next(t for t in data["tabs"] if t["intent"] == "follow")
    follow["blocks"] = []
    return data


def test_style_defaults_to_buttons_and_accepts_the_three_words() -> None:
    assert SiteConfig.model_validate(raw()).tabs[1].style == "buttons"
    for word in ("buttons", "rows"):
        data = stack(community=word)
        assert SiteConfig.model_validate(data).tabs[2].style == word
    config = SiteConfig.model_validate(icons_stack())
    assert next(t for t in config.tabs if t.intent == "follow").style == "icons"


@pytest.mark.parametrize("bad", ["tiles", "Rows", "", " rows", 3, None, ["rows"]])
def test_an_unknown_style_names_the_closed_set_and_never_echoes_the_value(
    bad: object,
) -> None:
    data = raw()
    data["tabs"][1]["style"] = bad
    with pytest.raises(ValidationError) as caught:
        SiteConfig.model_validate(data)
    messages = [e["msg"] for e in caught.value.errors(include_url=False)]
    assert len(messages) == 1, messages
    assert "'buttons', 'rows' or 'icons'" in messages[0]
    if isinstance(bad, str) and bad.strip():
        assert bad not in messages[0]


def test_the_loader_never_prints_a_bad_style_value(tmp_path: Path) -> None:
    data = raw()
    data["tabs"][1]["style"] = "hunter2-do-not-print"
    path = tmp_path / "site.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_site_config(path)
    assert "hunter2" not in str(caught.value)
    assert "tabs[1].style" in str(caught.value)


def test_style_needs_the_stack_layout() -> None:
    data = raw()
    data["page"] = {}
    data["tabs"][1]["style"] = "rows"
    with pytest.raises(ValidationError, match=r"style needs page\.layout: stack"):
        SiteConfig.model_validate(data)
    data["tabs"][1]["style"] = "buttons"  # the default word is always fine
    SiteConfig.model_validate(data)


def test_the_watch_group_has_no_style() -> None:
    for word in ("rows", "icons"):
        data = raw()
        data["tabs"][0]["style"] = word
        with pytest.raises(ValidationError, match="the Watch group has no style"):
            SiteConfig.model_validate(data)


def test_rows_keep_the_two_to_six_links_rule() -> None:
    data = stack(community="rows")
    data["tabs"][2]["blocks"] = data["tabs"][2]["blocks"][:1]
    with pytest.raises(ValidationError, match="2 to 6 links"):
        SiteConfig.model_validate(data)


def test_icons_are_only_for_the_follow_group() -> None:
    data = stack(community="icons")
    data["creator"]["socials"] = SOCIALS
    data["tabs"][2]["blocks"] = []
    with pytest.raises(ValidationError, match="icons are only for the Follow group"):
        SiteConfig.model_validate(data)


def test_icons_need_creator_socials() -> None:
    data = icons_stack()
    data["creator"]["socials"] = []
    with pytest.raises(ValidationError, match=r"icons need creator\.socials"):
        SiteConfig.model_validate(data)


def test_an_icons_group_lists_no_links_of_its_own() -> None:
    data = icons_stack()
    follow = next(t for t in data["tabs"] if t["intent"] == "follow")
    follow["blocks"] = raw()["tabs"][1]["blocks"]
    with pytest.raises(ValidationError, match="icons group lists no links"):
        SiteConfig.model_validate(data)


def test_icons_are_exempt_from_the_two_to_six_rule() -> None:
    data = icons_stack()
    data["creator"]["socials"] = SOCIALS[:1]  # one icon is enough
    SiteConfig.model_validate(data)
    data["creator"]["socials"] = [
        {"name": f"Site {n}", "url": f"https://example.com/{n}", "icon": "link"}
        for n in range(9)
    ]
    SiteConfig.model_validate(data)
