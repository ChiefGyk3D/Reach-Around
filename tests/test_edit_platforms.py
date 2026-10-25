# SPDX-License-Identifier: Apache-2.0
"""Platform item edits: add, remove, move and set one field."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from editkit import (
    CONFIG,
    changed_lines,
    edit,
    lines,
    make_cfg,
    no_anchors_or_tags,
    raw_of,
    refused,
)
from reach_around import editing

BLOCK = "everywhere"
KICK = [
    "          - platform: kick\n",
    "            handle: examplecreator\n",
    "            url: https://example.com/kick\n",
]
DAY = datetime.date(2027, 3, 2)


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_cfg(tmp_path, monkeypatch)


def items_of(path: Path) -> list[dict[str, Any]]:
    block = raw_of(path)["tabs"][1]["blocks"][0]
    assert block["id"] == BLOCK
    return cast("list[dict[str, Any]]", block["items"])


def new_item(**extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "platform": "mastodon",
        "handle": "@examplecreator@mastodon.example.com",
        "url": "https://example.com/mastodon",
    }
    item.update(extra)
    return item


def set_field(path: Path, index: int, field: str, value: Any) -> editing.Result:
    return edit(
        path,
        editing.set_platform_item_field,
        block=BLOCK,
        index=index,
        field=field,
        value=value,
    )


# ---- add ----


def test_add_appends_in_the_layout_of_the_list(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.add_platform_item, block=BLOCK, item=new_item(), position=None)
    gone, new = changed_lines(before, lines(cfg))
    assert gone == []
    assert new == [
        "          - platform: mastodon\n",
        '            handle: "@examplecreator@mastodon.example.com"\n',
        "            url: https://example.com/mastodon\n",
    ]
    assert items_of(cfg)[-1] == new_item()


def test_add_at_the_front_goes_above_the_leading_comment(cfg: Path) -> None:
    edit(cfg, editing.add_platform_item, block=BLOCK, item=new_item(), position=1)
    after = lines(cfg)
    at = after.index("          - platform: mastodon\n")
    assert after[at + 3] == "          # twitch first\n"
    assert items_of(cfg)[0] == new_item()
    assert len(items_of(cfg)) == 4


def test_add_writes_numbers_only_when_given(cfg: Path) -> None:
    item = new_item(label="members", count=120, as_of=DAY)
    edit(cfg, editing.add_platform_item, block=BLOCK, item=item, position=2)
    text = "".join(lines(cfg))
    assert (
        "            label: members\n"
        "            count: 120\n"
        "            as_of: 2027-03-02\n"
    ) in text
    assert items_of(cfg)[1] == item


@pytest.mark.parametrize("blank", [None, ""])
def test_add_leaves_a_blank_count_out_instead_of_writing_zero(
    cfg: Path, blank: object
) -> None:
    edit(
        cfg,
        editing.add_platform_item,
        block=BLOCK,
        item=new_item(count=blank),
        position=None,
    )
    assert "count" not in items_of(cfg)[-1]
    assert cfg.read_text(encoding="utf-8").count("count:") == 1  # twitch's own


@pytest.mark.parametrize(
    "value",
    [
        "&a x",
        "*b",
        "!tag",
        "{a: b}",
        "[x]",
        "- x",
        "# c",
        "a: b",
        'say "q"',
        "yes",
        "123",
    ],
)
def test_add_hostile_text_stays_text(cfg: Path, value: str) -> None:
    edit(
        cfg,
        editing.add_platform_item,
        block=BLOCK,
        item=new_item(handle=value),
        position=None,
    )
    assert no_anchors_or_tags(cfg.read_text(encoding="utf-8"))
    assert items_of(cfg)[-1]["handle"] == value


@pytest.mark.parametrize(
    ("block", "item", "position", "message"),
    [
        ("nope", new_item(), None, "no block"),
        ("first", new_item(), None, "not a platforms block"),
        ("everywhere", new_item(), 0, "--position"),
        ("everywhere", new_item(), 5, "--position"),
        (
            "everywhere",
            {"platform": "mastodon", "url": "https://e.com/"},
            None,
            "handle",
        ),
        ("everywhere", new_item(platform="nope"), None, "invalid"),
        ("everywhere", new_item(name="X"), None, "invalid"),
        ("everywhere", new_item(platform="other"), None, "invalid"),
        ("everywhere", new_item(as_of=DAY), None, "invalid"),
        ("everywhere", new_item(count=-1), None, "count"),
        ("everywhere", new_item(count="abc"), None, "count"),
        ("everywhere", new_item(count="1e3"), None, "count"),
        ("everywhere", new_item(count=True), None, "count"),
        ("everywhere", new_item(color="red"), None, "cannot be set"),
        ("everywhere", new_item(url="http://example.com/"), None, "invalid"),
        ("everywhere", new_item(handle="a\nb"), None, "one line"),
    ],
)
def test_add_refuses_bad_input(
    cfg: Path, block: str, item: dict[str, Any], position: int | None, message: str
) -> None:
    assert message in refused(
        cfg, editing.add_platform_item, block=block, item=item, position=position
    )


# ---- remove ----


def test_remove_takes_its_comment_and_nothing_else(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.remove_platform_item, block=BLOCK, index=2)
    gone, new = changed_lines(before, lines(cfg))
    assert new == []
    assert gone == ["          # kick has no numbers\n", *KICK]
    assert [i["platform"] for i in items_of(cfg)] == ["twitch", "other"]


def test_remove_first_keeps_the_rest(cfg: Path) -> None:
    edit(cfg, editing.remove_platform_item, block=BLOCK, index=1)
    text = cfg.read_text(encoding="utf-8")
    assert "twitch" not in text and "# twitch first" not in text
    assert "# trailing comment" in text and "# kick has no numbers" in text
    assert [i["platform"] for i in items_of(cfg)] == ["kick", "other"]


def test_remove_refuses_to_empty_the_list(cfg: Path) -> None:
    edit(cfg, editing.remove_platform_item, block=BLOCK, index=1)
    edit(cfg, editing.remove_platform_item, block=BLOCK, index=1)
    message = refused(cfg, editing.remove_platform_item, block=BLOCK, index=1)
    assert "only one item" in message
    assert len(items_of(cfg)) == 1


@pytest.mark.parametrize("index", [0, 4, -1])
def test_remove_refuses_a_bad_index(cfg: Path, index: int) -> None:
    message = refused(cfg, editing.remove_platform_item, block=BLOCK, index=index)
    assert "between 1 and 3" in message


# ---- move ----


def test_move_reorders_whole_items(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.move_platform_item, block=BLOCK, index=3, position=1)
    assert sorted(lines(cfg)) == sorted(before)
    assert [i["platform"] for i in items_of(cfg)] == ["other", "twitch", "kick"]
    edit(cfg, editing.move_platform_item, block=BLOCK, index=1, position=3)
    assert [i["platform"] for i in items_of(cfg)] == ["twitch", "kick", "other"]
    assert sorted(lines(cfg)) == sorted(before)


def test_move_keeps_a_comment_with_its_item(cfg: Path) -> None:
    edit(cfg, editing.move_platform_item, block=BLOCK, index=2, position=1)
    after = lines(cfg)
    at = after.index("          # kick has no numbers\n")
    assert after[at + 1 : at + 4] == KICK
    assert items_of(cfg)[0]["platform"] == "kick"


@pytest.mark.parametrize(("index", "position"), [(0, 1), (4, 1), (1, 0), (1, 4)])
def test_move_refuses_bad_numbers(cfg: Path, index: int, position: int) -> None:
    refused(
        cfg, editing.move_platform_item, block=BLOCK, index=index, position=position
    )


# ---- set one field ----


def test_set_field_replaces_only_that_line_and_keeps_the_comment(cfg: Path) -> None:
    before = lines(cfg)
    set_field(cfg, 1, "handle", "newname")
    gone, new = changed_lines(before, lines(cfg))
    assert gone == ["            handle: examplecreator   # main account\n"]
    assert new == ["            handle: newname   # main account\n"]
    assert items_of(cfg)[0]["handle"] == "newname"


def test_set_field_count_and_as_of(cfg: Path) -> None:
    before = lines(cfg)
    set_field(cfg, 1, "count", 4000)
    set_field(cfg, 1, "as_of", "2027-04-01")
    gone, new = changed_lines(before, lines(cfg))
    assert gone == ["            count: 3812\n", "            as_of: 2027-03-01\n"]
    assert new == ["            count: 4000\n", "            as_of: 2027-04-01\n"]
    first = items_of(cfg)[0]
    assert first["count"] == 4000 and first["as_of"] == datetime.date(2027, 4, 1)


def test_set_field_adds_count_then_as_of_in_order(cfg: Path) -> None:
    set_field(cfg, 2, "count", "99")
    set_field(cfg, 2, "as_of", "2027-05-06")
    after = lines(cfg)
    at = after.index("            url: https://example.com/kick\n")
    assert after[at + 1 : at + 3] == [
        "            count: 99\n",
        "            as_of: 2027-05-06\n",
    ]
    assert items_of(cfg)[1]["count"] == 99


def test_set_field_adds_label_between_url_and_count(cfg: Path) -> None:
    set_field(cfg, 1, "label", "subscribers")
    after = lines(cfg)
    at = after.index("            url: https://example.com/twitch\n")
    assert after[at + 1] == "            label: subscribers\n"
    assert after[at + 2] == "            count: 3812\n"


@pytest.mark.parametrize("blank", [None, ""])
def test_set_field_blank_count_removes_the_line_never_writes_zero(
    cfg: Path, blank: object
) -> None:
    set_field(cfg, 1, "as_of", blank)
    set_field(cfg, 1, "count", blank)
    text = cfg.read_text(encoding="utf-8")
    assert "3812" not in text and "as_of" not in text
    assert text.count("count:") == 0
    assert "count" not in items_of(cfg)[0]
    # blank on an item that has no count writes nothing
    now = cfg.read_bytes()
    set_field(cfg, 2, "count", blank)
    assert cfg.read_bytes() == now


def test_set_field_blank_count_with_a_date_is_refused(cfg: Path) -> None:
    assert "as_of" in refused(
        cfg,
        editing.set_platform_item_field,
        block=BLOCK,
        index=1,
        field="count",
        value="",
    )


def test_set_field_as_of_without_a_count_is_refused(cfg: Path) -> None:
    assert "count" in refused(
        cfg,
        editing.set_platform_item_field,
        block=BLOCK,
        index=2,
        field="as_of",
        value="2027-01-01",
    )


def test_set_field_clears_label_and_name(cfg: Path) -> None:
    before = lines(cfg)
    set_field(cfg, 3, "label", "")
    gone, new = changed_lines(before, lines(cfg))
    assert (gone, new) == (["            label: members\n"], [])
    set_field(cfg, 3, "name", "Other Net")
    assert items_of(cfg)[2]["name"] == "Other Net"


@pytest.mark.parametrize(
    "value",
    ["&a x", "*b", "!tag", "{a: b}", "[x]", "- x", "# c", "a: b", 'say "q"', "yes"],
)
def test_set_field_hostile_text_stays_text(cfg: Path, value: str) -> None:
    for field in ("handle", "name"):
        set_field(cfg, 3, field, value)
        assert no_anchors_or_tags(cfg.read_text(encoding="utf-8"))
        assert items_of(cfg)[2][field] == value


@pytest.mark.parametrize(
    ("index", "field", "value", "message"),
    [
        (0, "handle", "x", "between 1 and 3"),
        (4, "handle", "x", "between 1 and 3"),
        (1, "platform", "kick", "cannot be set"),
        (1, "color", "red", "cannot be set"),
        (1, "handle", "", "non-empty"),
        (1, "url", "", "non-empty"),
        (1, "url", "http://example.com/", "invalid"),
        (1, "handle", "a\nb", "one line"),
        (1, "count", "abc", "count"),
        (1, "count", "-5", "count"),
        (1, "count", "1.5", "count"),
        (1, "count", "٣", "count"),
        (1, "count", True, "count"),
        (1, "count", 10**11, "invalid"),
        (1, "as_of", "2027-13-01", "as_of"),
        (1, "as_of", "March 1", "as_of"),
        (1, "label", "fans of fans", "invalid"),
        (1, "name", "Name", "invalid"),
    ],
)
def test_set_field_refuses_bad_input(
    cfg: Path, index: int, field: str, value: object, message: str
) -> None:
    assert message in refused(
        cfg,
        editing.set_platform_item_field,
        block=BLOCK,
        index=index,
        field=field,
        value=value,
    )


def test_set_field_refuses_a_multi_line_value(cfg: Path) -> None:
    cfg.write_text(
        CONFIG.replace(
            "            handle: examplecreator   # main account\n",
            "            handle: example\n              creator\n",
        ),
        encoding="utf-8",
    )
    assert "multi-line" in refused(
        cfg,
        editing.set_platform_item_field,
        block=BLOCK,
        index=1,
        field="handle",
        value="x",
    )


# ---- refusals that must survive ----


def test_ops_refuse_flow_style_items(cfg: Path) -> None:
    cfg.write_text(
        CONFIG.replace(
            "".join(KICK),
            "          - {platform: kick, handle: examplecreator, "
            "url: 'https://example.com/kick'}\n",
        ),
        encoding="utf-8",
    )
    assert "flow" in refused(cfg, editing.remove_platform_item, block=BLOCK, index=1)
    assert "flow" in refused(
        cfg, editing.move_platform_item, block=BLOCK, index=1, position=2
    )
    assert "flow" in refused(
        cfg, editing.add_platform_item, block=BLOCK, item=new_item(), position=None
    )
    assert "flow" in refused(
        cfg,
        editing.set_platform_item_field,
        block=BLOCK,
        index=1,
        field="handle",
        value="x",
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("\n", "\r\n"),
        lambda t: t.replace(
            "          - platform: kick", "          - &a platform: kick"
        ),
        lambda t: t.replace(
            "          - platform: kick\n", "          -\n            platform: kick\n"
        ),
        lambda t: "version: 1\n" + t,
    ],
    ids=["crlf", "anchor", "dash-alone", "duplicate-key"],
)
def test_ops_keep_every_refusal(cfg: Path, mutate: Callable[[str], str]) -> None:
    cfg.write_bytes(mutate(CONFIG).encode())
    refused(cfg, editing.remove_platform_item, block=BLOCK, index=1)
    refused(cfg, editing.move_platform_item, block=BLOCK, index=1, position=2)
    refused(cfg, editing.add_platform_item, block=BLOCK, item=new_item(), position=None)
    refused(
        cfg,
        editing.set_platform_item_field,
        block=BLOCK,
        index=1,
        field="handle",
        value="x",
    )
