# SPDX-License-Identifier: Apache-2.0
"""The instance template's hand-kept freshness check (#631): the script, its
agreement with the engine's own rule, and the workflow's promises. Synthetic data."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config.handkept import make_entry, stale_entries, write_entries

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
SCRIPT = TEMPLATE / "scripts" / "check-freshness.py"
WORKFLOW = TEMPLATE / ".github" / "workflows" / "check-hand-kept-freshness.yml"
TODAY = date(2027, 3, 1)


def run(
    tmp_path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv: this interpreter and the shipped script
        [sys.executable, "-I", str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", ""), **(env or {})},
    )


def doc(entries: list[dict[str, Any]]) -> str:
    return json.dumps({"version": 1, "entries": entries})


def entry(key: str, as_of: date, platform: str = "bluesky", value: int = 7777) -> Any:
    return {
        "platform": platform,
        "key": key,
        "value": value,
        "as_of": as_of.isoformat(),
    }


def test_a_missing_file_is_fresh(tmp_path: Path) -> None:
    out = tmp_path / "out"
    r = run(tmp_path, "--today", "2027-03-01", env={"GITHUB_OUTPUT": str(out)})
    assert r.returncode == 0, r.stderr
    assert "0 entries, 0 past" in r.stdout
    assert out.read_text() == "stale=false\n"


def test_a_stale_measure_is_named_with_its_age_and_never_its_value(
    tmp_path: Path,
) -> None:
    (tmp_path / "hand-kept.json").write_text(
        doc(
            [
                entry("followers", TODAY - timedelta(days=31)),
                entry("analytics_stat", TODAY - timedelta(days=30), value=8888),
            ]
        )
    )
    out = tmp_path / "out"
    r = run(
        tmp_path,
        "--today",
        "2027-03-01",
        "--body",
        "body.md",
        env={"GITHUB_OUTPUT": str(out)},
    )
    assert r.returncode == 0, r.stderr
    assert "stale: bluesky followers 31 days" in r.stdout
    assert "analytics_stat" not in r.stdout
    assert out.read_text() == "stale=true\n"
    body = (tmp_path / "body.md").read_text()
    assert "bluesky `followers`: 31 days" in body
    assert "7777" not in r.stdout + body and "8888" not in r.stdout + body


def test_no_body_is_written_when_nothing_is_stale(tmp_path: Path) -> None:
    (tmp_path / "hand-kept.json").write_text(doc([entry("followers", TODAY)]))
    r = run(tmp_path, "--today", "2027-03-01", "--body", "body.md")
    assert r.returncode == 0
    assert not (tmp_path / "body.md").exists()


def test_the_script_agrees_with_the_engine_rule(tmp_path: Path) -> None:
    entries = [
        make_entry(
            platform=p,
            key="followers",
            value=1,
            as_of=TODAY - timedelta(days=d),
            today=TODAY,
        )
        for p, d in (
            ("bluesky", 5),
            ("bluesky", 90),
            ("mastodon", 40),
            ("pixelfed", 31),
        )
    ] + [
        make_entry(
            platform="kick",
            key="analytics_stat",
            value=1,
            as_of=TODAY - timedelta(days=200),
            today=TODAY,
        )
    ]
    path = tmp_path / "hand-kept.json"
    write_entries(path, entries)
    for limit in (0, 10, 30, 31, 100):
        want = [
            f"stale: {e.platform} {e.key} {days} days"
            for e, days in stale_entries(entries, TODAY, limit)
        ]
        r = run(tmp_path, "--today", "2027-03-01", "--limit-days", str(limit))
        got = [line for line in r.stdout.splitlines() if line.startswith("stale: ")]
        assert got == want, limit


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        '{"version": 1}',
        '{"version": 2, "entries": []}',
        '{"version": 1, "version": 1, "entries": []}',
        '{"version": 1, "entries": [NaN]}',
        doc([{"platform": "x"}]),
        doc([{**entry("followers", TODAY), "as_of": "2027-3-1"}]),
        doc([{**entry("followers", TODAY), "platform": "Bad Name"}]),
        doc([{**entry("followers", TODAY), "key": "bad key"}]),
        "[" * 100000,
    ],
)
def test_a_bad_file_exits_2_with_a_fixed_message(tmp_path: Path, raw: str) -> None:
    (tmp_path / "hand-kept.json").write_text(raw)
    r = run(tmp_path, "--today", "2027-03-01")
    assert r.returncode == 2
    assert r.stderr.startswith("error: ")
    assert "Traceback" not in r.stderr


def test_a_link_or_a_pipe_or_a_huge_file_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(doc([]))
    link = tmp_path / "hand-kept.json"
    link.symlink_to(real)
    assert run(tmp_path).returncode == 2
    link.unlink()
    os.mkfifo(link)
    assert run(tmp_path).returncode == 2
    link.unlink()
    link.write_bytes(b" " * (1024 * 1024 + 1))
    assert run(tmp_path).returncode == 2


def test_bad_arguments_exit_2(tmp_path: Path) -> None:
    assert run(tmp_path, "--limit-days", "-1").returncode == 2
    assert run(tmp_path, "--today", "yesterday").returncode == 2


# --- the workflow ------------------------------------------------------------


def workflow() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def test_the_workflow_is_scheduled_and_runs_only_on_main() -> None:
    data = workflow()
    triggers = data[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    job = data["jobs"]["freshness"]
    assert job["if"] == "github.ref == 'refs/heads/main'"


def test_the_workflow_has_least_privilege_and_no_secret() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = workflow()
    assert data["permissions"] == {"contents": "read"}
    assert data["jobs"]["freshness"]["permissions"] == {
        "contents": "read",
        "issues": "write",
    }
    assert "secrets." not in text
    assert "CLOUDFLARE" not in text
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "continue-on-error" not in code
    assert "pull_request" not in code
    for line in re.findall(r"^\s*-?\s*uses:\s*(\S.*)$", text, re.MULTILINE):
        assert re.match(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$", line), line
    for step in data["jobs"]["freshness"]["steps"]:
        assert "${{" not in step.get("run", "")


def test_the_workflow_runs_the_script_with_isolated_python() -> None:
    steps = workflow()["jobs"]["freshness"]["steps"]
    runs = [s["run"] for s in steps if "run" in s]
    assert any(r.startswith("python3 -I scripts/check-freshness.py") for r in runs)
