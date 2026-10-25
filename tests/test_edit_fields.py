# SPDX-License-Identifier: Apache-2.0
"""Field-level edits: set_link_field, the platform item operations, avatar.

These run through ``editing.apply`` (the same entry point the commands use), so
the double check (real loader, then data equals original plus the one change)
is part of every test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from editkit import (
    CONFIG,
    changed_lines,
    edit,
    lines,
    link,
    make_cfg,
    no_anchors_or_tags,
    raw_of,
    refused,
)
from reach_around import editing
from reach_around.cli import main


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_cfg(tmp_path, monkeypatch)


# ---- set_link_field ----


def test_set_link_field_replaces_only_that_line(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.set_link_field, link_id="first", field="title", value="Renamed")
    gone, new = changed_lines(before, lines(cfg))
    assert gone == ["        title: First\n"]
    assert new == ["        title: Renamed\n"]
    assert link(raw_of(cfg), "first")["title"] == "Renamed"


def test_set_link_field_keeps_the_trailing_comment(cfg: Path) -> None:
    before = lines(cfg)
    edit(
        cfg,
        editing.set_link_field,
        link_id="first",
        field="url",
        value="https://example.com/new",
    )
    gone, new = changed_lines(before, lines(cfg))
    assert gone == ["        url: https://example.com/1   # keep me\n"]
    assert new == ["        url: https://example.com/new   # keep me\n"]


def test_set_link_field_inside_a_section(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.set_link_field, link_id="two", field="title", value="Second")
    gone, new = changed_lines(before, lines(cfg))
    assert (gone, new) == (
        ["            title: Two\n"],
        ["            title: Second\n"],
    )
    assert link(raw_of(cfg), "two")["title"] == "Second"


def test_set_link_field_adds_a_missing_key_in_the_usual_order(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.set_link_field, link_id="two", field="subtitle", value="Hello")
    edit(cfg, editing.set_link_field, link_id="two", field="icon", value="link")
    edit(cfg, editing.set_link_field, link_id="two", field="affiliate", value=True)
    after = lines(cfg)
    gone, new = changed_lines(before, after)
    assert gone == []
    assert new == [
        "            subtitle: Hello\n",
        "            icon: link\n",
        "            affiliate: true\n",
    ]
    at = after.index("            url: https://example.com/two\n")
    assert after[at + 1 : at + 4] == new
    two = link(raw_of(cfg), "two")
    assert two["subtitle"] == "Hello" and two["affiliate"] is True


def test_set_link_field_clears_an_optional_key(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.set_link_field, link_id="first", field="subtitle", value="")
    gone, new = changed_lines(before, lines(cfg))
    assert (gone, new) == (["        subtitle: Old subtitle\n"], [])
    assert "subtitle" not in link(raw_of(cfg), "first")


def test_set_link_field_booleans(cfg: Path) -> None:
    before = lines(cfg)
    edit(cfg, editing.set_link_field, link_id="one", field="featured", value="false")
    gone, new = changed_lines(before, lines(cfg))
    assert (gone, new) == (
        ["            featured: true\n"],
        ["            featured: false\n"],
    )
    assert link(raw_of(cfg), "one")["featured"] is False
    # false on a key that is not there writes nothing at all
    now = cfg.read_bytes()
    edit(cfg, editing.set_link_field, link_id="two", field="featured", value=False)
    assert cfg.read_bytes() == now


@pytest.mark.parametrize(
    "value",
    ["&a x", "*b", "!tag", "!!str x", "{a: b}", "[x]", "- x", "# c", "a: b", 'say "q"'],
)
def test_set_link_field_hostile_text_stays_text(cfg: Path, value: str) -> None:
    for field in ("title", "subtitle"):
        edit(cfg, editing.set_link_field, link_id="first", field=field, value=value)
        text = cfg.read_text(encoding="utf-8")
        assert no_anchors_or_tags(text)
        assert link(raw_of(cfg), "first")[field] == value


@pytest.mark.parametrize(
    ("link_id", "field", "value", "message"),
    [
        ("first", "image", "x.png", "cannot be set"),
        ("first", "id", "other", "cannot be set"),
        ("nope", "title", "X", "no link"),
        ("links", "title", "X", "is a tab"),
        ("group", "title", "X", "is a section"),
        ("first", "title", "", "non-empty"),
        ("first", "title", "a\nb", "one line"),
        ("first", "title", " padded", "whitespace"),
        ("first", "url", "http://example.com/", "invalid"),
        ("first", "url", "javascript:alert(1)", "invalid"),
        ("first", "featured", "yes", "true or false"),
        ("first", "affiliate", "maybe", "true or false"),
        ("first", "title", "x" * 500, "invalid"),
    ],
)
def test_set_link_field_refuses_bad_input(
    cfg: Path, link_id: str, field: str, value: str, message: str
) -> None:
    assert message in refused(
        cfg, editing.set_link_field, link_id=link_id, field=field, value=value
    )


def test_set_link_field_refuses_a_multi_line_value(cfg: Path) -> None:
    cfg.write_text(
        CONFIG.replace(
            "        title: First\n",
            "        title: |\n          First\n          Line\n",
        ),
        encoding="utf-8",
    )
    assert "multi-line" in refused(
        cfg, editing.set_link_field, link_id="first", field="title", value="X"
    )


def test_set_link_field_refuses_flow_style(cfg: Path) -> None:
    cfg.write_text(
        CONFIG.replace(
            "          - id: two\n"
            "            title: Two\n"
            "            url: https://example.com/two\n",
            "          - {id: two, title: Two, url: 'https://example.com/two'}\n",
        ),
        encoding="utf-8",
    )
    assert "flow" in refused(
        cfg, editing.set_link_field, link_id="one", field="title", value="X"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("\n", "\r\n"),
        lambda t: t.replace("      - id: first", "      - &a id: first", 1),
        lambda t: t.replace("      - id: first\n", "      -\n        id: first\n"),
        lambda t: "version: 1\n" + t,
    ],
    ids=["crlf", "anchor", "dash-alone", "duplicate-key"],
)
def test_set_link_field_keeps_every_refusal(
    cfg: Path, mutate: Callable[[str], str]
) -> None:
    cfg.write_bytes(mutate(CONFIG).encode())
    refused(cfg, editing.set_link_field, link_id="first", field="title", value="X")


def test_set_link_field_drops_a_tag_on_the_replaced_value(cfg: Path) -> None:
    cfg.write_text(
        CONFIG.replace("        title: First\n", "        title: !!str First\n"),
        encoding="utf-8",
    )
    edit(cfg, editing.set_link_field, link_id="first", field="title", value="Next")
    assert no_anchors_or_tags(cfg.read_text(encoding="utf-8"))
    assert link(raw_of(cfg), "first")["title"] == "Next"


def test_set_link_field_matches_the_command(cfg: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    twin = other / "site.yaml"
    twin.write_text(CONFIG, encoding="utf-8")
    edit(cfg, editing.set_link_field, link_id="first", field="subtitle", value="New")
    code = main(
        [
            "set-link-field", "--config", str(twin), "--force-path", "--no-backup",
            "first", "subtitle", "New",
        ]
    )  # fmt: skip
    assert code == 0
    assert twin.read_bytes() == cfg.read_bytes()


def test_the_command_refuses_like_the_function(
    cfg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = cfg.read_bytes()
    assert main(["set-link-field", "--config", str(cfg), "first", "nope", "x"]) == 1
    assert cfg.read_bytes() == before
    assert capsys.readouterr().err.startswith("error:")
