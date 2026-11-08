# SPDX-License-Identifier: Apache-2.0
"""Guard the scheduled base-image digest check (#231) and the GYST pins.

Collects loosely (every workflow file) and asserts strictly: the check exists, is
scheduled, is read-only, calls the GYST workflow by commit with a version comment,
and every GYST call in the repo uses one commit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CHECK = WORKFLOWS / "base-image.yml"
GYST_USE = re.compile(
    r"uses:\s*ChiefGyk3D/git-your-ship-together/\S+@(?P<ref>\S+)(?P<rest>[^\n]*)"
)
PINNED = re.compile(r"^[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+\.\d+\.\d+\s*$")


def gyst_calls(text: str) -> list[tuple[str, str]]:
    return [(m["ref"], m["rest"]) for m in GYST_USE.finditer(text)]


def violations(texts: dict[str, str]) -> list[str]:
    """Problems with GYST calls across workflow files (empty means fine)."""
    problems: list[str] = []
    refs: set[str] = set()
    total = 0
    for name, text in texts.items():
        for ref, rest in gyst_calls(text):
            total += 1
            refs.add(ref)
            if not PINNED.match(ref):
                problems.append(f"{name}: {ref} is not a commit")
            if not VERSION_COMMENT.match(rest):
                problems.append(f"{name}: missing version comment")
    if total == 0:
        problems.append("no GYST call collected")
    if len(refs) > 1:
        problems.append(f"GYST callers pin {len(refs)} different refs")
    return problems


def all_texts() -> dict[str, str]:
    files = sorted(WORKFLOWS.glob("*.yml"))
    template = REPO / "examples" / "instance-repo" / ".github" / "workflows"
    files += sorted(template.glob("*.yml"))
    return {str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in files}


def test_every_gyst_call_is_pinned_to_one_commit() -> None:
    texts = all_texts()
    assert violations(texts) == []


def test_guard_bites() -> None:
    good = "    uses: ChiefGyk3D/git-your-ship-together/.github/workflows/x.yml@" + (
        "a" * 40
    )
    assert violations({"a": good + " # v1.31.0\n"}) == []
    assert violations({"a": good.replace("a" * 40, "main") + " # v1.31.0\n"})
    assert violations({"a": good + "\n"})
    assert violations(
        {"a": good + " # v1.31.0\n", "b": good.replace("a", "b") + " # v1.31.0\n"}
    )
    assert violations({"a": "name: nothing\n"})


def load() -> dict[Any, Any]:
    data = yaml.safe_load(CHECK.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_base_image_check_is_scheduled_and_read_only() -> None:
    wf = load()
    triggers = wf[True] if True in wf else wf["on"]  # PyYAML reads `on` as True
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] and "cron" in triggers["schedule"][0]
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    assert list(jobs) == ["base-image"]
    job = jobs["base-image"]
    assert job["permissions"] == {"contents": "read"}
    assert job["uses"].startswith(
        "ChiefGyk3D/git-your-ship-together/.github/workflows/base-image-digest.yml@"
    )
    assert job["with"]["dockerfile"] == "Containerfile"
    assert "secrets" not in job
    assert "continue-on-error" not in job


def test_check_reads_the_containerfile_the_release_builds() -> None:
    containerfile = (REPO / "Containerfile").read_text(encoding="utf-8")
    assert re.search(r"@sha256:[0-9a-f]{64}", containerfile)
