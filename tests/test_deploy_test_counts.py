# SPDX-License-Identifier: Apache-2.0
"""deploy-test.yml refreshes follower counts before every build (issue #288):
a daily schedule, a keys-only step in a job that cannot write, the numbers file
handed to the build as data, and the script that does the work."""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-test.yml"
SCRIPT = ROOT / ".github" / "scripts" / "test-counts.py"
KEYS = {
    "YOUTUBE_API_KEY",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
}
DOPPLER_STEP = "Fetch count-source keys from Doppler"
REFRESH_STEP = "Refresh the counts"


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def jobs() -> dict[str, Any]:
    result: dict[str, Any] = load()["jobs"]
    return result


def key_holders(all_jobs: dict[str, Any]) -> list[tuple[str, str]]:
    """(job, step name) of every step that mentions a counts key."""
    return [
        (job_name, s["name"])
        for job_name, job in all_jobs.items()
        for s in job["steps"]
        if any(key in yaml.safe_dump(s.get("env", {})) for key in KEYS)
    ]


# ---- the workflow ---------------------------------------------------------


def test_the_workflow_runs_daily_and_by_hand() -> None:
    data: dict[Any, Any] = load()
    triggers: dict[str, Any] = data.get("on") or data[True]
    assert "workflow_dispatch" in triggers
    crons = [item["cron"] for item in triggers["schedule"]]
    assert len(crons) == 1
    minute, hour, dom, month, dow = crons[0].split()
    assert (dom, month, dow) == ("*", "*", "*")
    assert minute.isdigit() and hour.isdigit() and minute not in ("0", "00")
    for bad in ("push", "pull_request", "pull_request_target", "workflow_run"):
        assert bad not in triggers


def test_a_counts_job_runs_before_the_build_and_cannot_write() -> None:
    assert "counts" in jobs()
    counts = jobs()["counts"]
    # id-token is for the optional Doppler OIDC login; nothing here can write.
    assert counts["permissions"] == {"contents": "read", "id-token": "write"}
    assert counts["if"] == "github.ref == 'refs/heads/main'"
    assert "environment" not in counts and "env" not in counts
    assert "counts" in jobs()["build"]["needs"]
    assert "id-token" not in yaml.safe_dump(jobs()["build"])


def test_the_keys_reach_exactly_one_step_through_env() -> None:
    assert key_holders(jobs()) == [("counts", REFRESH_STEP)]
    step = next(s for s in jobs()["counts"]["steps"] if s.get("name") == REFRESH_STEP)
    assert set(step["env"]) == KEYS
    for name in KEYS:
        # Both sources, Doppler first: the GitHub secret is the default path.
        value = step["env"][name]
        assert f"secrets.{name}" in value
        assert f".{name} ||" in value and "steps.doppler.outputs.secrets" in value
    assert "secrets." not in step["run"]


def test_the_github_secrets_path_needs_no_doppler_setup() -> None:
    # With no variables set the Doppler step is skipped and the secrets above
    # are the only source; keyless sources need neither.
    doppler = next(
        s for s in jobs()["counts"]["steps"] if s.get("name") == DOPPLER_STEP
    )
    condition = doppler["if"]
    for var in (
        "DOPPLER_IDENTITY_ID",
        "DOPPLER_COUNTS_PROJECT",
        "DOPPLER_COUNTS_CONFIG",
    ):
        assert f"vars.{var} != ''" in condition
    step = next(s for s in jobs()["counts"]["steps"] if s.get("name") == REFRESH_STEP)
    assert "if" not in step  # the refresh runs with or without keys


def test_the_doppler_path_is_oidc_outputs_only_and_has_no_default_project() -> None:
    steps = jobs()["counts"]["steps"]
    doppler = next(s for s in steps if s.get("name") == DOPPLER_STEP)
    assert doppler["id"] == "doppler"
    withs = doppler["with"]
    assert withs["export"] == "outputs"  # nothing goes to the job environment
    assert "token" not in withs  # no long-lived token, OIDC only
    assert withs["identity-id"] == "${{ vars.DOPPLER_IDENTITY_ID }}"
    assert withs["project"] == "${{ vars.DOPPLER_COUNTS_PROJECT }}"
    assert withs["config"] == "${{ vars.DOPPLER_COUNTS_CONFIG }}"
    assert "secrets." not in yaml.safe_dump(doppler)
    assert steps.index(doppler) < [s.get("name") for s in steps].index(REFRESH_STEP)
    # No environment-wide export: no GITHUB_ENV write anywhere in the job.
    assert "GITHUB_ENV" not in yaml.safe_dump(jobs()["counts"])


def test_the_key_guard_bites() -> None:
    hostile = {
        "build": {"steps": [{"name": "x", "env": {"YOUTUBE_API_KEY": "v"}}]},
    }
    assert key_holders(hostile) == [("build", "x")]


PIP_LINE = re.compile(r"\bpip3?\s+(install|download|wheel)\b")
# Anything that can run code from a package at install time.
SOURCE_BUILD = re.compile(r"\bpip3?\s+wheel\b|--no-build-isolation|\bnpm\b|\s\.\s*$")


def runs_source_code(job: dict[str, Any]) -> list[str]:
    """Run lines in a job that could build or install from source."""
    found: list[str] = []
    for s in job["steps"]:
        for line in str(s.get("run", "")).splitlines():
            if PIP_LINE.search(line) and (
                SOURCE_BUILD.search(line)
                or "--only-binary" not in line
                or "--no-index" not in line
            ):
                found.append(line.strip())
    return found


def test_the_job_that_can_ask_for_an_oidc_token_installs_no_source_code() -> None:
    # A package build script or setup.py run in a job with id-token: write
    # could request a token for the Doppler identity.
    for name, job in jobs().items():
        if "write" in str(job.get("permissions", {}).get("id-token", "")):
            assert runs_source_code(job) == [], name
    # The job that builds from source must not be able to ask for one.
    wheels = jobs()["wheels"]
    assert runs_source_code(wheels)  # it does build (the guard has something to see)
    assert "id-token" not in wheels["permissions"]
    assert wheels["permissions"] == {"contents": "read"}
    assert "secrets." not in yaml.safe_dump(wheels)
    assert "counts" not in jobs()["wheels"].get("needs", [])
    assert jobs()["counts"]["needs"] == "wheels"


def test_id_token_is_in_no_job_that_runs_pip_from_source_or_the_build() -> None:
    allowed = {"counts", "deploy"}
    for name, job in jobs().items():
        if "id-token" in job.get("permissions", {}):
            assert name in allowed, name
        if runs_source_code(job):
            assert "id-token" not in job.get("permissions", {}), name


def test_the_id_token_guard_bites() -> None:
    hostile = {
        "steps": [{"run": "python -m pip install --no-deps --no-build-isolation ."}]
    }
    assert runs_source_code(hostile)
    sloppy = {"steps": [{"run": "python -m pip install -r requirements.txt"}]}
    assert runs_source_code(sloppy)
    clean = {
        "steps": [
            {
                "run": "python -m pip install --no-index --only-binary :all: "
                "--require-hashes -r requirements.txt"
            }
        ]
    }
    assert runs_source_code(clean) == []


def test_no_install_runs_after_the_keys_and_installs_are_wheels_only() -> None:
    steps = jobs()["counts"]["steps"]
    at = [s.get("name") for s in steps].index(REFRESH_STEP)
    for later in steps[at + 1 :]:
        assert not re.search(r"\bpip3?\b|\bnpm\b", str(later.get("run", "")))
    installs = [
        line.strip()
        for s in steps
        for line in str(s.get("run", "")).splitlines()
        if re.search(r"\bpip3?\s+install\b", line)
    ]
    assert installs
    for line in installs:
        assert "--no-index" in line
    hashed = [ln for ln in installs if "-r requirements.txt" in ln]
    assert hashed and all("--require-hashes" in ln for ln in hashed)
    # The wheels job installs and downloads only from hashed requirements.
    builder = [
        line
        for s in jobs()["wheels"]["steps"]
        for line in str(s.get("run", "")).splitlines()
        if re.search(r"\bpip3?\s+(install|download)\b", line)
    ]
    assert builder and all("--require-hashes" in ln for ln in builder)


def test_the_counts_job_starts_hardened_and_every_action_is_pinned() -> None:
    first = jobs()["counts"]["steps"][0]
    assert first["uses"].startswith("step-security/harden-runner@")
    pinned = 0
    for job in jobs().values():
        for s in job["steps"]:
            if "uses" in s:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
                pinned += 1
    assert pinned
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "continue-on-error" not in text
    assert "--accept-drop" not in text


def test_the_numbers_cross_to_the_build_as_data_and_are_passed_explicitly() -> None:
    upload = [
        s
        for s in jobs()["counts"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert [u["with"]["name"] for u in upload] == ["counts-numbers"]
    download = [
        s
        for s in jobs()["build"]["steps"]
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    ]
    assert [d["with"]["name"] for d in download] == ["counts-numbers"]
    build = [s for s in jobs()["build"]["steps"] if s.get("name") == "Build the site"]
    assert len(build) == 1 and "--numbers" in build[0]["run"]
    assert "secrets." not in yaml.safe_dump(jobs()["build"])


# ---- the script -----------------------------------------------------------


def fake_engine(tmp_path: Path, exit_code: int, platforms: dict[str, Any]) -> Path:
    """A stand-in `reach-around`: writes --out and exits as told."""
    path = tmp_path / "fake-engine"
    body = (
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--out') + 1]\n"
        f"json.dump({{'version': 1, 'platforms': {platforms!r}}}, open(out, 'w'))\n"
        f"sys.exit({exit_code})\n"
    )
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run(
    tmp_path: Path, config_text: str, engine: Path, seed: dict[str, Any] | None = None
) -> subprocess.CompletedProcess[str]:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    (site / "site.yaml").write_text(config_text, encoding="utf-8")
    if seed is not None:
        (site / "numbers.json").write_text(json.dumps(seed), encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--config",
            str(site / "site.yaml"),
            "--out",
            str(tmp_path / "out"),
            "--engine",
            str(engine),
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def stamp(days_ago: int) -> str:
    when = datetime.now(UTC) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


NUMBERS_CONFIG = "version: 1\nnumbers:\n  sources: []\n"


def result(tmp_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((tmp_path / "out" / "numbers.json").read_text())
    return data


def test_with_no_numbers_block_the_hand_kept_file_passes_through(
    tmp_path: Path,
) -> None:
    seed = {"version": 1, "platforms": {"tiktok": {"count": 5, "as_of": stamp(1)}}}
    proc = run(tmp_path, "version: 1\n", fake_engine(tmp_path, 9, {}), seed)
    assert proc.returncode == 0, proc.stderr
    assert result(tmp_path) == seed  # the engine (exit 9) was never called


def test_with_nothing_at_all_an_empty_valid_file_is_written(tmp_path: Path) -> None:
    proc = run(tmp_path, "version: 1\n", fake_engine(tmp_path, 9, {}))
    assert proc.returncode == 0, proc.stderr
    assert result(tmp_path) == {"version": 1, "platforms": {}}


def test_a_refresh_replaces_what_the_engine_returns(tmp_path: Path) -> None:
    fresh = {"bluesky": {"count": 7, "as_of": stamp(0)}}
    proc = run(tmp_path, NUMBERS_CONFIG, fake_engine(tmp_path, 0, fresh))
    assert proc.returncode == 0, proc.stderr
    assert result(tmp_path)["platforms"] == fresh


def test_a_failed_source_warns_and_keeps_the_good_numbers(tmp_path: Path) -> None:
    fresh = {"github": {"count": 3, "as_of": stamp(0)}}
    proc = run(tmp_path, NUMBERS_CONFIG, fake_engine(tmp_path, 3, fresh))
    assert proc.returncode == 0, proc.stderr
    assert "::warning" in proc.stdout
    assert result(tmp_path)["platforms"] == fresh


@pytest.mark.parametrize("code", [1, 2])
def test_any_other_engine_failure_stops_the_run(tmp_path: Path, code: int) -> None:
    proc = run(tmp_path, NUMBERS_CONFIG, fake_engine(tmp_path, code, {}))
    assert proc.returncode != 0
    assert "::error" in proc.stdout


def test_hand_kept_numbers_older_than_30_days_get_a_warning(tmp_path: Path) -> None:
    seed = {
        "version": 1,
        "platforms": {
            "tiktok": {"count": 5, "as_of": stamp(45)},
            "instagram": {"count": 6, "as_of": stamp(29)},
        },
    }
    proc = run(tmp_path, "version: 1\n", fake_engine(tmp_path, 0, {}), seed)
    assert proc.returncode == 0, proc.stderr
    warned = [ln for ln in proc.stdout.splitlines() if ln.startswith("::warning")]
    assert len(warned) == 1 and "tiktok" in warned[0] and "instagram" not in warned[0]


def test_a_hostile_platform_key_is_not_echoed(tmp_path: Path) -> None:
    evil = "x\n::error::pwned"
    seed = {"version": 1, "platforms": {evil: {"count": 1, "as_of": stamp(90)}}}
    proc = run(tmp_path, "version: 1\n", fake_engine(tmp_path, 0, {}), seed)
    assert "pwned" not in proc.stdout and "pwned" not in proc.stderr
