# SPDX-License-Identifier: Apache-2.0
"""The shipped editor logic against a real editor server: the ops it builds are
accepted, the YAML changes only where it should, and every error path reads
back the way the forms expect."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_forms_logic import node_or_none, stage

from editorharness import tree_snapshot
from reach_around.editor.server import EditorSession

CONFIG = """\
# a comment that must survive
version: 1
creator:
  name: Example Creator   # keep me
tabs:
  - id: links
    title: Links
    blocks:
      - id: first
        title: First
        url: https://example.com/1
      - id: second
        title: Second
        url: https://example.com/2
      - id: socials
        layout: chips
        items:
          - platform: twitch
            handle: examplecreator
            url: https://example.com/twitch
            count: 10
            as_of: 2027-03-01
          - platform: kick
            handle: examplecreator
            url: https://example.com/kick
"""


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[EditorSession, Path, Path]]:
    work = tmp_path / "site"
    work.mkdir()
    config = work / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    session = EditorSession(config)
    session.start()
    try:
        yield session, config, tmp_path
    finally:
        session.shutdown()


def drive(
    served: tuple[EditorSession, Path, Path], scenario: str
) -> dict[str, Any]:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    session, config, tmp_path = served
    tools = tmp_path / "tools"
    tools.mkdir()
    stage(tools)
    shutil.copyfile(Path(__file__).with_name("forms_contract.mjs"), tools / "run.mjs")
    done = subprocess.run(
        [node, "run.mjs", str(session.port), session.token, str(config), scenario],
        cwd=tools,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    data = json.loads(done.stdout.strip().splitlines()[-1])
    assert isinstance(data, dict)
    return data


def test_every_form_operation_is_accepted_and_only_the_edit_changes(
    served: tuple[EditorSession, Path, Path],
) -> None:
    _, config, _ = served
    out = drive(served, "edit")
    for name in ("settings", "link", "platform", "moveLink", "movePlatform", "clear"):
        assert out[name]["ok"] is True, (name, out[name])
    text = config.read_text(encoding="utf-8")
    assert "# a comment that must survive" in text
    assert "# keep me" in text
    assert "name: New Name" in text
    assert "mode: dark" in text
    assert "background_dim: 0.4" in text
    assert "bg: '#112233'" in text or 'bg: "#112233"' in text
    assert "Second!" in text
    assert "featured: true" in text
    assert "subtitle: hello" not in text  # cleared by the last save
    assert "count: 11" in text
    assert "label: followers" in text
    assert text.index("id: second") < text.index("id: first")
    assert text.index("platform: kick") < text.index("platform: twitch")
    assert out["settings"]["saved"] == 4


def test_a_refused_value_comes_back_as_a_field_error_and_writes_nothing(
    served: tuple[EditorSession, Path, Path],
) -> None:
    _, config, _ = served
    before = tree_snapshot(config.parent)
    out = drive(served, "refused")
    for name in ("result", "empty"):
        assert out[name]["ok"] is False
        assert out[name]["problem"]["kind"] == "field"
        assert out[name]["problem"]["message"]
    assert out["result"]["field"] == "url"
    assert out["empty"]["field"] == "creator.name"
    assert tree_snapshot(config.parent) == before


def test_a_change_to_the_same_field_is_stale_and_shows_the_fresh_state(
    served: tuple[EditorSession, Path, Path],
) -> None:
    _, config, _ = served
    out = drive(served, "stale")
    assert out["result"]["ok"] is False
    assert out["result"]["problem"]["kind"] == "stale"
    assert out["title"] == "Changed elsewhere"
    assert out["message"] == "status.stale"
    text = config.read_text(encoding="utf-8")
    assert "title: Changed elsewhere" in text
    assert "Mine" not in text


def test_an_outside_change_to_another_field_merges(
    served: tuple[EditorSession, Path, Path],
) -> None:
    _, config, _ = served
    out = drive(served, "merge")
    assert out["result"]["ok"] is True, out["result"]
    assert out["name"] == "Someone Else"
    text = config.read_text(encoding="utf-8")
    assert "name: Someone Else" in text
    assert "title: Mine" in text


def test_the_rate_limit_reads_back_with_its_wait(
    served: tuple[EditorSession, Path, Path],
) -> None:
    out = drive(served, "limit")
    assert out["okCount"] == 20
    problem = out["last"]["problem"]
    assert problem["kind"] == "limit"
    assert problem["seconds"] >= 1
    assert str(problem["seconds"]) in problem["message"]
