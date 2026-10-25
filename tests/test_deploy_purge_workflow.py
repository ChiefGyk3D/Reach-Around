# SPDX-License-Identifier: Apache-2.0
"""deploy-test.yml: the purge wiring is least-privilege, and its list step runs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.cli import main

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-test.yml"
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
DOWNLOAD_STEP = "Download the previous assets inventory"
LIST_STEP = "List edge-cache paths to purge"
PURGE_STEP = "Purge changed paths from the edge cache (optional)"


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def step(job: str, name: str) -> dict[str, Any]:
    found = [s for s in load()["jobs"][job]["steps"] if s.get("name") == name]
    assert len(found) == 1, name
    result: dict[str, Any] = found[0]
    return result


def test_every_action_is_pinned_by_commit_sha() -> None:
    uses = [
        s["uses"]
        for job in load()["jobs"].values()
        for s in job["steps"]
        if "uses" in s
    ]
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref


def test_the_purge_token_is_in_exactly_one_step_and_no_other_secret_with_it() -> None:
    text_by_step = {
        (job, s.get("name") or s.get("uses")): yaml.safe_dump(s)
        for job, body in load()["jobs"].items()
        for s in body["steps"]
    }
    holders = [k for k, v in text_by_step.items() if "CLOUDFLARE_PURGE_TOKEN" in v]
    assert holders == [("deploy", PURGE_STEP)]
    purge = step("deploy", PURGE_STEP)
    # The purge token may come from Doppler (exported into the job) or from the
    # GitHub secret. The deploy token is exported into the job by the Doppler
    # step, so this step blanks it explicitly: it never carries a value.
    assert purge["env"] == {
        "CLOUDFLARE_API_TOKEN": "",
        "CLOUDFLARE_PURGE_TOKEN": (
            "${{ env.CLOUDFLARE_PURGE_TOKEN || secrets.CLOUDFLARE_PURGE_TOKEN }}"
        ),
        "ZONE_ID": "${{ vars.ZONE_ID }}",
        "SITE_HOST": "${{ vars.SITE_HOST }}",
    }
    assert "secrets.CLOUDFLARE_API_TOKEN" not in yaml.safe_dump(purge)
    assert "continue-on-error" not in yaml.safe_dump(purge)
    # the build job, where pip install runs, never sees any secret
    assert "secrets." not in yaml.safe_dump(load()["jobs"]["build"])


def test_the_purge_runs_last_after_the_publish_and_calls_the_script() -> None:
    steps = load()["jobs"]["deploy"]["steps"]
    assert steps[-1].get("name") == PURGE_STEP
    names = [s.get("name") for s in steps]
    assert names.index("Publish the site to Pages") < names.index(PURGE_STEP)
    assert steps[-1]["run"].strip() == (
        "bash .github/scripts/purge-cache.sh purge-in/site-purge-list.txt"
    )
    assert (ROOT / ".github" / "scripts" / "purge-cache.sh").is_file()


def test_permissions_stay_minimal() -> None:
    data = load()
    assert data["permissions"] == {"contents": "read"}
    assert data["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "actions": "read",
    }
    # id-token is for the Doppler OIDC exchange and only on the deploy job
    assert data["jobs"]["deploy"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }


def test_the_repo_token_reaches_only_the_download_step_never_the_dependency_code() -> (
    None
):
    steps = load()["jobs"]["build"]["steps"]
    holders = [s.get("name") for s in steps if "GH_TOKEN" in yaml.safe_dump(s)]
    assert holders == [DOWNLOAD_STEP]
    download = step("build", DOWNLOAD_STEP)
    assert set(download["env"]) == {"GH_TOKEN", "GH_REPO"}
    # the step that holds the token runs gh only, no installed entry point
    assert "reach-around" not in download["run"]
    assert "python" not in download["run"]
    listing = step("build", LIST_STEP)
    assert "env" not in listing
    assert "reach-around deploy-purge-list" in listing["run"]
    assert "gh " not in listing["run"]
    names = [s.get("name") for s in steps]
    assert names.index(DOWNLOAD_STEP) < names.index(LIST_STEP)
    # nothing exports the token to later steps
    text = yaml.safe_dump(load())
    assert "GITHUB_ENV" not in text and "GITHUB_PATH" not in text


def test_the_download_step_runs_before_anything_can_change_PATH() -> None:
    steps = load()["jobs"]["build"]["steps"]
    names = [s.get("name") for s in steps]
    position = names.index(DOWNLOAD_STEP)
    # only checkout may precede it: no setup-python, no pip, no venv, no npm
    for before in steps[:position]:
        assert str(before.get("uses", "")).startswith("actions/checkout@"), before
        assert "run" not in before
    for later in steps[position + 1 :]:
        # nothing after it can have taken the token with it
        assert "GH_TOKEN" not in yaml.safe_dump(later)


def test_the_download_step_pins_PATH_and_calls_gh_by_absolute_path() -> None:
    run = step("build", DOWNLOAD_STEP)["run"]
    assert re.search(r"^\s*PATH=/usr/bin:/bin\s*$", run, re.MULTILINE)
    assert "/usr/bin/gh" in run
    # no bare `gh` command anywhere: every use goes through the absolute path
    bare = [
        line
        for line in run.splitlines()
        if re.search(r"(^|[\s;(&|`$])gh\s+(run|api|auth|release)\b", line)
    ]
    assert bare == []
    for banned in ("reach-around", "python", "pip", "npm", "source ", "venv"):
        assert banned not in run, banned
    assert "GITHUB_ENV" not in run and "GITHUB_PATH" not in run


def test_the_inventory_artifact_is_uploaded_outside_the_site_and_kept() -> None:
    uploads = {
        s["with"]["name"]: s["with"]
        for s in load()["jobs"]["build"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    }
    inventory = uploads["site-assets-inventory"]
    assert inventory["path"] == "site-out.assets.json"
    assert int(inventory["retention-days"]) >= 30
    assert uploads["site-out"]["path"] == "site-out"
    assert "assets" not in str(uploads["site-out"]["path"])
    build = step("build", "Build the site")["run"]
    assert "test -z \"$(find site-out -iname '*assets.json')\"" in build


STUB_GH = r"""#!/usr/bin/env bash
# stub gh: STUB_PREVIOUS (run id or empty), STUB_PREVIOUS_FILE (inventory to hand out)
case "$1 $2" in
  "run list")
    [ -n "${STUB_LIST_FAIL:-}" ] && exit 1
    printf '%s' "${STUB_PREVIOUS:-}"
    ;;
  "run download")
    [ -n "${STUB_DOWNLOAD_FAIL:-}" ] && exit 1
    dir=""
    while [ $# -gt 0 ]; do [ "$1" = "--dir" ] && dir="$2"; shift; done
    mkdir -p "$dir"
    cp "$STUB_PREVIOUS_FILE" "$dir/site-out.assets.json"
    ;;
  *) exit 2 ;;
esac
"""


def run_list_step(
    tmp_path: Path, *, previous: str, previous_file: Path | None, **flags: str
) -> subprocess.CompletedProcess[str]:
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(STUB_GH)
    (bin_dir / "gh").chmod(0o755)
    # `reach-around` is the real command, run from this checkout
    (bin_dir / "reach-around").write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" -m reach_around.cli "$@"\n'
    )
    (bin_dir / "reach-around").chmod(0o755)
    assert (
        main(["build", "--config", str(EXAMPLE), "--out", str(work / "site-out")]) == 0
    )
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT / "src"),
        "STUB_PREVIOUS": previous,
        "GH_TOKEN": "synthetic",
        "GH_REPO": "example/example",
        **flags,
    }
    if previous_file is not None:
        env["STUB_PREVIOUS_FILE"] = str(previous_file)
    # two steps, as in the workflow: only the first one gets the token
    list_env = {k: v for k, v in env.items() if k not in ("GH_TOKEN", "GH_REPO")}
    # the script names gh by absolute path; point that literal at the stub
    download = step("build", DOWNLOAD_STEP)["run"]
    assert "/usr/bin/gh" in download
    download = download.replace("/usr/bin/gh", str(bin_dir / "gh"))
    first = subprocess.run(  # noqa: S603 - fixed argv, synthetic env and stubs
        ["/bin/bash", "-c", download],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert first.returncode == 0, first.stderr
    second = subprocess.run(  # noqa: S603 - fixed argv, synthetic env and stubs
        ["/bin/bash", "-c", step("build", LIST_STEP)["run"]],
        cwd=work,
        env=list_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    second.stdout = first.stdout + second.stdout
    return second


def previous_with_extra_file(tmp_path: Path) -> Path:
    scratch = tmp_path / "prev"
    scratch.mkdir()
    assert main(["build", "--config", str(EXAMPLE), "--out", str(scratch / "o")]) == 0
    data = json.loads((scratch / "o.assets.json").read_text())
    data["files"]["manifest.json"] = data["files"]["index.html"]
    path = scratch / "previous.json"
    path.write_text(json.dumps(data))
    return path


def test_the_list_step_writes_the_removed_paths(tmp_path: Path) -> None:
    prev = previous_with_extra_file(tmp_path)
    done = run_list_step(tmp_path, previous="123", previous_file=prev)
    assert done.returncode == 0, done.stderr
    lines = (tmp_path / "work" / "site-purge-list.txt").read_text().splitlines()
    assert "/manifest.json" in lines and "/" in lines
    assert not any(line.startswith("#") for line in lines)


@pytest.mark.parametrize(
    ("flags", "previous", "needle"),
    [
        ({}, "", "no earlier successful deployment"),
        ({"STUB_LIST_FAIL": "1"}, "123", "no earlier successful deployment"),
        ({"STUB_DOWNLOAD_FAIL": "1"}, "123", "has no assets inventory"),
    ],
)
def test_without_a_usable_previous_inventory_the_list_is_empty_and_the_step_passes(
    tmp_path: Path, flags: dict[str, str], previous: str, needle: str
) -> None:
    prev = previous_with_extra_file(tmp_path)
    done = run_list_step(tmp_path, previous=previous, previous_file=prev, **flags)
    assert done.returncode == 0, done.stderr
    assert needle in done.stdout
    text = (tmp_path / "work" / "site-purge-list.txt").read_text()
    assert [ln for ln in text.splitlines() if not ln.startswith("#")] == []


def test_a_corrupt_previous_inventory_is_a_warning_and_an_empty_list(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "files": {"../etc/passwd": {}}}')
    done = run_list_step(tmp_path, previous="123", previous_file=bad)
    assert done.returncode == 0, done.stderr
    assert "::warning::could not compare inventories" in done.stdout
    text = (tmp_path / "work" / "site-purge-list.txt").read_text()
    assert [ln for ln in text.splitlines() if not ln.startswith("#")] == []
