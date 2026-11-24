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


def test_scrub_step_is_fail_closed_when_the_file_is_missing() -> None:
    cmd = load()["jobs"]["wiki"]["with"]["generate-command"]
    assert "[ ! -f scripts/check-wiki-scrub.py ]" in cmd
    missing_branch = cmd.split("[ ! -f scripts/check-wiki-scrub.py ]")[1].split("fi")[0]
    assert "::error" in missing_branch
    assert "exit 1" in missing_branch
    assert "::notice" not in cmd
    assert cmd.index("python scripts/check-wiki-scrub.py") < cmd.index(
        "scripts/wiki-flatten.py"
    )


DEFAULT_BRANCH_ONLY = (
    "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
)


def test_jobs_run_only_on_the_default_branch_and_never_on_forks() -> None:
    jobs = load()["jobs"]
    for name in ("precheck", "wiki"):
        cond = jobs[name]["if"]
        assert DEFAULT_BRANCH_ONLY in cond, name
        assert "github.event.repository.fork == false" in cond, name


def test_stale_run_is_refused_before_publishing() -> None:
    steps = load()["jobs"]["precheck"]["steps"]
    names = [s.get("name", "") for s in steps]
    idx = next(i for i, n in enumerate(names) if "default branch head" in n)
    step = steps[idx]
    assert step["env"]["RUN_SHA"] == "${{ github.sha }}"
    assert "git/ref/heads/$BRANCH" in step["run"]
    assert '[ "$head" != "$RUN_SHA" ]' in step["run"]
    assert "exit 1" in step["run"]
    # It runs before the step that reports the wiki as enabled.
    assert idx < names.index("Read the wiki setting")
    # Read-only: the job token only has contents: read.
    assert load()["jobs"]["precheck"]["permissions"] == {"contents": "read"}
