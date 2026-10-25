# SPDX-License-Identifier: Apache-2.0
"""The instance template's scheduled counts refresh keeps its promises: a daily
schedule, minimal permissions, secrets only through ``env:``, a suspect-value
guard that is never overridden, and actions pinned by commit."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "refresh-counts.yml"
SCRIPT = TEMPLATE / "scripts" / "refresh-counts.sh"

SHA_USE = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S.*?)\s*$", re.MULTILINE)
ALLOWED_PERMISSIONS = {"contents": "write", "pull-requests": "write"}
KEY_NAMES = {"YOUTUBE_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"}


def problems(text: str, script: str = "") -> list[str]:
    """Guard problems in a refresh workflow's text. Empty means clean."""
    found: list[str] = []
    data: dict[Any, Any] = yaml.safe_load(text)
    # YAML 1.1 reads the key `on` as True.
    triggers = data.get("on", data.get(True)) or {}
    if not triggers.get("schedule"):
        found.append("no schedule")
    if "workflow_dispatch" not in triggers:
        found.append("no workflow_dispatch")
    for bad in ("pull_request_target", "pull_request", "push", "workflow_run"):
        if bad in triggers:
            found.append(f"trigger {bad}")
    if data.get("permissions") != {"contents": "read"}:
        found.append("workflow permissions are not exactly contents: read")
    for line in USES_LINE.findall(text):
        if not SHA_USE.match(line):
            found.append(f"unpinned action: {line}")
    for name, job in data["jobs"].items():
        perms = job.get("permissions")
        if perms != ALLOWED_PERMISSIONS:
            found.append(f"{name}: permissions are {perms}")
        if "env" in job:
            found.append(f"{name}: job-level env")
        if "environment" in job:
            found.append(f"{name}: environment")
        for step in job["steps"]:
            run = step.get("run", "")
            if "${{" in run:
                found.append(f"{name}: expression inside run")
            if "secrets." in run:
                found.append(f"{name}: secret inside run")
    everything = text + script
    if "--accept-drop" in everything or "accept_drop" in everything:
        found.append("accept-drop is used")
    if "continue-on-error" in text:
        found.append("continue-on-error")
    return found


def test_the_refresh_workflow_is_clean() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert problems(WORKFLOW.read_text(encoding="utf-8"), script) == []


def test_the_schedule_is_daily() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    crons = [item["cron"] for item in data[True]["schedule"]]
    assert len(crons) == 1
    minute, hour, dom, month, dow = crons[0].split()
    assert (dom, month, dow) == ("*", "*", "*")
    assert minute.isdigit() and hour.isdigit()
    # Off the top of the hour, where scheduled runs are most often delayed.
    assert minute not in ("0", "00")


def test_secrets_reach_only_the_refresh_step_through_env() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    holders = []
    for job in data["jobs"].values():
        for step in job["steps"]:
            if "secrets." in yaml.safe_dump(step):
                holders.append(step["name"])
                assert "secrets." not in str(step.get("with", {}))
                names = {k for k, v in step["env"].items() if "secrets." in str(v)}
                assert names == KEY_NAMES
    assert holders == ["Refresh the counts"]


def test_failure_of_one_source_does_not_hide_the_others() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    # Exit 3 (a source failed) still lets the good numbers through; the job
    # reports it red afterwards.
    assert re.search(r"\b3\)", script)
    assert "numbers.json" in script


def test_the_guard_bites() -> None:
    good = WORKFLOW.read_text(encoding="utf-8")
    sha = re.search(r"actions/checkout@[0-9a-f]{40} # v[\d.]+", good)
    assert sha
    mutations = {
        "no schedule": good.replace("  schedule:", "  other:", 1),
        "unpinned": good.replace(sha.group(0), "actions/checkout@main"),
        "pull_request_target": good.replace(
            "workflow_dispatch:", "pull_request_target:", 1
        ),
        "extra permission": good.replace(
            "      pull-requests: write",
            "      pull-requests: write\n      actions: write",
        ),
        "secret in run": good.replace(
            "run: scripts/check-repo.sh",
            "run: echo ${{ secrets.YOUTUBE_API_KEY }}",
        ),
        "accept-drop": good + "\n# --accept-drop youtube\n",
    }
    for label, mutated in mutations.items():
        assert mutated != good, label
        assert problems(mutated), label
