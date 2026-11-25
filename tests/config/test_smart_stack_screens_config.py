# SPDX-License-Identifier: Apache-2.0
"""Support and Contact screens of the compact link page (#873): config contract."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from test_smart_stack_config import raw as stack_raw

from reach_around.config import SiteConfig


def links(prefix: str, count: int, **extra: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}-{n}",
            "title": f"{prefix} {n}",
            "url": f"https://example.invalid/{prefix}/{n}",
            **extra,
        }
        for n in range(1, count + 1)
    ]


def screens() -> dict[str, Any]:
    """A compact site with a Support screen (three groups) and a Contact screen."""
    data = stack_raw()
    data["page"]["density"] = "compact"
    data["tabs"] += [
        {
            "id": "help-free",
            "title": "Free ways to help",
            "intent": "free",
            "screen": "support",
            "note": "Gear I Use is free for you.",
            "blocks": links("free", 6),
        },
        {
            "id": "help-paid",
            "title": "Paid ways to help",
            "intent": "paid",
            "screen": "support",
            "blocks": links("paid", 4),
        },
        {
            "id": "help-crypto",
            "title": "Crypto tips",
            "intent": "crypto",
            "screen": "support",
            "blocks": links("crypto", 4),
        },
        {
            "id": "say",
            "title": "Get in touch",
            "intent": "contact",
            "screen": "contact",
            "blocks": links("say", 5),
        },
        {
            "id": "hire",
            "title": "Hire me",
            "intent": "hire",
            "screen": "contact",
            "note": "Work requests go through the company.",
            "blocks": links("hire", 1),
        },
    ]
    return data


def messages(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        SiteConfig.model_validate(data)
    return [e["msg"] for e in caught.value.errors(include_url=False)]


def tab(data: dict[str, Any], ident: str) -> dict[str, Any]:
    return next(t for t in data["tabs"] if t["id"] == ident)


def test_a_support_and_a_contact_screen_validate() -> None:
    config = SiteConfig.model_validate(screens())
    found = {t.id: (t.screen, t.intent) for t in config.tabs}
    assert found["help-free"] == ("support", "free")
    assert found["hire"] == ("contact", "hire")
    assert found["follow"] == (None, "follow")


def test_screen_and_note_default_to_none() -> None:
    config = SiteConfig.model_validate(stack_raw())
    assert all(t.screen is None and t.note is None for t in config.tabs)


@pytest.mark.parametrize("field", ["screen", "note"])
def test_screen_and_note_need_compact_density_on_the_stack(field: str) -> None:
    data = screens()
    data["page"]["density"] = "comfortable"
    for other in data["tabs"]:  # one refusal at a time: the first one raises
        if field == "note":
            other.pop("screen", None)
    found = messages(data)
    assert any(f"{field} needs page.density: compact" in m for m in found), found


@pytest.mark.parametrize("field", ["screen", "note"])
def test_screen_and_note_need_compact_density_on_the_classic_layout(
    field: str,
) -> None:
    data = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "home",
                "title": "Home",
                "blocks": [],
                field: "support" if field == "screen" else "A note",
            }
        ],
    }
    assert any(f"{field} needs page.density: compact" in m for m in messages(data))


@pytest.mark.parametrize("bad", ["Support", "links", "home", "", 1, None, ["support"]])
def test_an_unknown_screen_is_refused_without_echoing_it(bad: object) -> None:
    data = screens()
    tab(data, "say")["screen"] = bad
    if bad is None:
        # None means "no screen": the group moves home, which is valid.
        SiteConfig.model_validate(data)
        return
    found = messages(data)
    assert len(found) == 1, found
    if isinstance(bad, str) and bad.strip():
        assert bad not in found[0]


def test_the_watch_group_stays_home_and_takes_no_note() -> None:
    for field, value in (("screen", "support"), ("note", "A note")):
        data = screens()
        tab(data, "watch")[field] = value
        found = messages(data)
        assert any(
            "the Watch group stays on the home screen and takes no note" in m
            for m in found
        ), found


def test_an_intent_may_repeat_on_another_screen_but_not_on_the_same_one() -> None:
    data = screens()
    twin = copy.deepcopy(tab(data, "follow"))
    twin.update(id="follow-again", screen="support")
    twin["blocks"] = links("again", 2)
    data["tabs"].append(twin)
    SiteConfig.model_validate(data)  # follow on home and follow on support
    same = copy.deepcopy(twin)
    same["id"] = "follow-third"
    same["blocks"] = links("third", 2)
    data["tabs"].append(same)
    assert any("may appear only once" in m for m in messages(data))


def test_a_support_screen_refuses_the_support_page_path() -> None:
    data = screens()
    data["support"] = {
        "title": "Support",
        "blocks": [
            {
                "id": "pay",
                "coins": [
                    {
                        "coin": "btc",
                        "address": "bc1qexampleexampleexampleexampleexample",
                    }
                ],
            }
        ],
    }
    assert any(
        "a support screen takes the path /support/, which the support page uses; "
        "remove support: or the screen" in m
        for m in messages(data)
    )
    # A contact screen alone does not collide with the support page.
    data["tabs"] = [t for t in data["tabs"] if t.get("screen") != "support"]
    SiteConfig.model_validate(data)


@pytest.mark.parametrize("screen", ["support", "contact"])
def test_a_screen_refuses_the_path_of_the_analytics_route(screen: str) -> None:
    data = screens()
    data["analytics"] = {"route": f"/{screen}/"}
    found = messages(data)
    assert any(
        f"a {screen} screen cannot take the path of analytics.route; rename the route"
        in m
        for m in found
    ), found


def test_a_hire_group_is_exactly_one_link_and_sets_no_columns() -> None:
    data = screens()
    tab(data, "hire")["blocks"] = links("hire", 2)
    assert any(
        "a hire group takes exactly 1 link for page.density: compact" in m
        for m in messages(data)
    )
    data = screens()
    tab(data, "hire")["blocks"] = []
    assert any("a hire group takes exactly 1 link" in m for m in messages(data))
    data = screens()
    tab(data, "hire")["columns"] = 1
    assert any("a hire group sets no columns" in m for m in messages(data))


def test_the_other_groups_still_take_two_to_six_links_on_a_screen() -> None:
    data = screens()
    tab(data, "say")["blocks"] = links("say", 1)
    assert any("2 to 6" in m for m in messages(data))
    data = screens()
    tab(data, "say")["blocks"] = links("say", 7)
    assert any("2 to 6" in m for m in messages(data))


BAD_NOTES = [
    "",
    " leading",
    "trailing ",
    "line\nbreak",
    "tab\there",
    "nul" + chr(0) + "byte",
    "x" * 161,
    "bell" + chr(7),
    "sep" + chr(0x2028),
]


@pytest.mark.parametrize("bad", [*BAD_NOTES, 7, ["a"]])
def test_a_note_is_plain_text_of_one_to_160_characters(bad: object) -> None:
    data = screens()
    tab(data, "say")["note"] = bad
    found = messages(data)
    assert found, bad
    if isinstance(bad, str) and len(bad) > 8:
        assert all(bad not in m for m in found)


def test_markup_in_a_note_is_accepted_as_text() -> None:
    data = screens()
    tab(data, "say")["note"] = "x" * 160
    SiteConfig.model_validate(data)
    hostile = "<b>\"hi\"</b> & 'bye'"
    tab(data, "say")["note"] = hostile
    config = SiteConfig.model_validate(data)
    assert next(t for t in config.tabs if t.id == "say").note == hostile


def test_the_new_intents_are_accepted() -> None:
    config = SiteConfig.model_validate(screens())
    assert {t.intent for t in config.tabs} >= {"free", "paid", "crypto", "contact"}


def test_unknown_tab_fields_are_still_refused() -> None:
    data = screens()
    tab(data, "say")["screens"] = "support"
    assert messages(data)
