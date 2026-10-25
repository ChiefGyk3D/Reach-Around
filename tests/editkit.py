# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures and helpers for the field-level edit tests."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from reach_around import editing
from reach_around.config import load_yaml

CONFIG = """\
# Synthetic config with comments in awkward places.
version: 1   # schema version
creator:
  name: Example Creator   # shown in the header
  avatar: img/me.png   # the avatar
tabs:
  - id: links
    title: Links
    blocks:
      # the first link
      - id: first
        title: First
        url: https://example.com/1   # keep me
        subtitle: Old subtitle
      # a section
      - id: group
        title: Group
        links:
          # grouped one
          - id: one
            title: One
            url: https://example.com/one
            featured: true
          - id: two
            title: Two
            url: https://example.com/two
  - id: socials
    title: Socials
    blocks:
      # the platforms
      - id: everywhere
        title: Everywhere
        layout: chips
        items:
          # twitch first
          - platform: twitch
            handle: examplecreator   # main account
            url: https://example.com/twitch
            count: 3812
            as_of: 2027-03-01

          # kick has no numbers
          - platform: kick
            handle: examplecreator
            url: https://example.com/kick
          - platform: other
            name: Example Net
            handle: examplecreator
            url: https://example.com/other
            label: members
# trailing comment
"""


def make_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def edit(path: Path, func: Callable[..., Any], **kwargs: Any) -> editing.Result:
    return editing.apply(
        path,
        lambda text, raw: func(text, raw, **kwargs),
        dry_run=False,
        backup=False,
        force_path=False,
    )


def refused(path: Path, func: Callable[..., Any], **kwargs: Any) -> str:
    """Run an edit that must be refused; the file must stay byte for byte."""
    before = path.read_bytes()
    with pytest.raises(editing.EditError) as caught:
        edit(path, func, **kwargs)
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]
    return str(caught.value)


def changed_lines(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    """The lines only in ``before`` and the lines only in ``after``."""
    gone: list[str] = []
    new: list[str] = []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        if tag != "equal":
            gone += before[a0:a1]
            new += after[b0:b1]
    return gone, new


def no_anchors_or_tags(text: str) -> bool:
    kinds = (yaml.AnchorToken, yaml.AliasToken, yaml.TagToken)
    return not any(
        isinstance(t, kinds) for t in yaml.scan(text, Loader=yaml.SafeLoader)
    )


def raw_of(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", load_yaml(path))


def link(raw: dict[str, Any], link_id: str) -> dict[str, Any]:
    for block in raw["tabs"][0]["blocks"]:
        if block["id"] == link_id:
            return cast("dict[str, Any]", block)
        for inner in block.get("links", []):
            if inner["id"] == link_id:
                return cast("dict[str, Any]", inner)
    raise AssertionError(link_id)
