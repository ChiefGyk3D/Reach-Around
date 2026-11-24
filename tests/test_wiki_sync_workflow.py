# SPDX-License-Identifier: Apache-2.0
"""Guard the wiki-sync workflow (#799): main only, no pull requests, least privilege."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "wiki-sync.yml"
)


def load() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_triggers_are_main_push_and_dispatch_only() -> None:
    on = load()[True]  # YAML 1.1 reads the key `on` as True
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"]["branches"] == ["main"]
    assert on["push"]["paths"] == ["docs/wiki/**"]


def test_write_scope_only_on_the_publish_call() -> None:
    wf = load()
    assert wf["permissions"] == {"contents": "read"}
    assert wf["jobs"]["precheck"]["permissions"] == {"contents": "read"}
    assert wf["jobs"]["wiki"]["permissions"] == {"contents": "write"}
    writers = [
        n for n, j in wf["jobs"].items() if "write" in j.get("permissions", {}).values()
    ]
    assert writers == ["wiki"]


def test_forks_skipped_and_queued() -> None:
    wf = load()
    assert "fork" in wf["jobs"]["precheck"]["if"]
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert wf["concurrency"]["group"] != "wiki"
    assert wf["jobs"]["wiki"]["needs"] == "precheck"
    assert "enabled == 'true'" in wf["jobs"]["wiki"]["if"]


def test_no_secrets_and_every_use_pinned() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    for line in text.splitlines():
        m = re.search(r"uses:\s*(\S+)@(\S+)(.*)", line)
        if m:
            assert re.fullmatch(r"[0-9a-f]{40}", m.group(2)), line
            assert re.search(r"# v\d+\.\d+\.\d+", m.group(3)), line


def test_scrub_step_is_conditional_on_the_file() -> None:
    cmd = load()["jobs"]["wiki"]["with"]["generate-command"]
    assert "[ -f scripts/check-wiki-scrub.py ]" in cmd
    assert "scripts/wiki-flatten.py" in cmd
