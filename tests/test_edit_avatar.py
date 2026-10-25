# SPDX-License-Identifier: Apache-2.0
"""``set creator.avatar``: the asset-path rules, and nothing else."""

from __future__ import annotations

from pathlib import Path

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
from reach_around.cli import main


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return make_cfg(tmp_path, monkeypatch)


def set_avatar(path: Path, value: str) -> editing.Result:
    return edit(path, editing.set_value, key="creator.avatar", value=value)


def test_avatar_is_a_settable_key() -> None:
    assert "creator.avatar" in editing.SET_KEYS


@pytest.mark.parametrize(
    "value",
    [
        "img/new.png",
        "me.webp",
        "a/b/c.JPG",
        "https://cdn.example.com/me.png",
        "https://example.com/me.png?v=2",
    ],
)
def test_avatar_accepts_a_local_image_or_an_https_url(cfg: Path, value: str) -> None:
    before = lines(cfg)
    set_avatar(cfg, value)
    gone, new = changed_lines(before, lines(cfg))
    assert gone == ["  avatar: img/me.png   # the avatar\n"]
    assert len(new) == 1 and new[0].endswith("   # the avatar\n")
    assert raw_of(cfg)["creator"]["avatar"] == value
    assert no_anchors_or_tags(cfg.read_text(encoding="utf-8"))


def test_avatar_is_added_under_creator_when_absent(cfg: Path) -> None:
    cfg.write_text(CONFIG.replace("  avatar: img/me.png   # the avatar\n", ""))
    before = lines(cfg)
    set_avatar(cfg, "img/me.png")
    gone, new = changed_lines(before, lines(cfg))
    assert gone == [] and new == ["  avatar: img/me.png\n"]
    after = lines(cfg)
    assert after.index("  avatar: img/me.png\n") == after.index("tabs:\n") - 1


@pytest.mark.parametrize(
    "value",
    [
        "",
        "../me.png",
        "img/../me.png",
        "/etc/me.png",
        "./me.png",
        "static/me.png",
        "privacy/me.png",
        "me.txt",
        "me",
        "img//me.png",
        "img\\me.png",
        "a b.png",
        "me.png?x=1",
        "me.png#frag",
        "file:///me.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "http://example.com/me.png",
        "//example.com/me.png",
        "ftp://example.com/me.png",
        "https://",
        "https://user:pw@example.com/me.png",
        "&a me.png",
        "!tag me.png",
        "*a",
        "me.png\n",
        "x" * 600 + ".png",
    ],
)
def test_avatar_refuses_what_the_asset_pipeline_refuses(cfg: Path, value: str) -> None:
    message = refused(cfg, editing.set_value, key="creator.avatar", value=value)
    # refused by the editor's own check, before the config loader sees it
    assert "must be an https URL or a local image path" in message or (
        "non-empty printable text" in message
    )


def test_avatar_command_matches_the_function(cfg: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    twin = other / "site.yaml"
    twin.write_text(CONFIG, encoding="utf-8")
    set_avatar(cfg, "img/new.png")
    code = main(
        [
            "set", "--config", str(twin), "--force-path", "--no-backup",
            "creator.avatar", "img/new.png",
        ]
    )  # fmt: skip
    assert code == 0
    assert twin.read_bytes() == cfg.read_bytes()


def test_other_creator_keys_are_still_refused(cfg: Path) -> None:
    for key in ("creator.favicon", "creator.header", "creator.socials"):
        refused(cfg, editing.set_value, key=key, value="img/x.png")
