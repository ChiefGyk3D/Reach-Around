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
    assert "github.ref == 'refs/heads/main'" in counts["if"]
    assert "env" not in counts  # no job-level env, on purpose
    assert "counts" in jobs()["build"]["needs"]
    assert "id-token" not in yaml.safe_dump(jobs()["build"])


def dispatch_inputs() -> dict[str, Any]:
    data: dict[Any, Any] = load()
    triggers: dict[str, Any] = data.get("on") or data[True]
    inputs: dict[str, Any] = triggers["workflow_dispatch"]["inputs"]
    return inputs


def test_a_manual_run_can_skip_the_counts() -> None:
    skip = dispatch_inputs()["skip_counts"]
    assert skip["type"] == "boolean"
    assert skip["default"] is False
    assert skip.get("required", False) is False
    ref = "github.ref == 'refs/heads/main'"
    # wheels and counts do not run when skipped, and still only on main.
    for name in ("wheels", "counts"):
        cond = jobs()[name]["if"]
        assert ref in cond and "!inputs.skip_counts" in cond, name


def test_the_build_runs_when_counts_are_skipped_but_never_after_a_failure() -> None:
    build = jobs()["build"]
    cond = build["if"]
    assert "!cancelled()" in cond  # else a skipped dependency skips the build
    assert "github.ref == 'refs/heads/main'" in cond
    # Skipped on purpose, or the counts job itself succeeded. A bare
    # "skipped" result would also let a failed wheels job through.
    assert "inputs.skip_counts" in cond
    assert "needs.counts.result == 'success'" in cond
    assert "'skipped'" not in cond and "always()" not in cond
    steps = build["steps"]
    download = next(
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
        and s["with"]["name"] == "counts-numbers"
    )
    assert "inputs.skip_counts" in download["if"]
    run = next(s for s in steps if s.get("name") == "Build the site")["run"]
    # Both forms exist: with the counts file, and without (the committed file).
    assert run.count("reach-around build") == 2
    assert "--numbers counts-in/numbers.json" in run
    # The deploy waits for the build only; a skipped counts job is not in its path.
    assert jobs()["deploy"]["needs"] == "build"


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
        "DOPPLER_COUNTS_IDENTITY_ID",
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
    assert withs["identity-id"] == "${{ vars.DOPPLER_COUNTS_IDENTITY_ID }}"
    assert withs["project"] == "${{ vars.DOPPLER_COUNTS_PROJECT }}"
    assert withs["config"] == "${{ vars.DOPPLER_COUNTS_CONFIG }}"
    assert "secrets." not in yaml.safe_dump(doppler)
    assert steps.index(doppler) < [s.get("name") for s in steps].index(REFRESH_STEP)
    # No environment-wide export: no GITHUB_ENV write anywhere in the job.
    assert "GITHUB_ENV" not in yaml.safe_dump(jobs()["counts"])


def variables_used(job: dict[str, Any]) -> set[str]:
    return set(re.findall(r"vars\.(DOPPLER_\w+)", yaml.safe_dump(job)))


def test_counts_and_deploy_use_different_doppler_identities() -> None:
    counts = variables_used(jobs()["counts"])
    deploy = variables_used(jobs()["deploy"])
    assert "DOPPLER_COUNTS_IDENTITY_ID" in counts
    assert "DOPPLER_IDENTITY_ID" in deploy
    assert "DOPPLER_IDENTITY_ID" not in counts
    assert "DOPPLER_COUNTS_IDENTITY_ID" not in deploy
    # The same value in both would give the counts code the deploy token.
    assert not any(v.startswith("DOPPLER_IDENTITY") for v in counts)


def test_the_identity_guard_bites() -> None:
    shared = {"steps": [{"with": {"identity-id": "${{ vars.DOPPLER_IDENTITY_ID }}"}}]}
    assert "DOPPLER_IDENTITY_ID" in variables_used(shared)


def test_the_counts_egress_policy_states_why_it_is_audit() -> None:
    first = jobs()["counts"]["steps"][0]
    policy = first["with"]["egress-policy"]
    assert policy in ("audit", "block")
    if policy == "audit":
        # Only the unknowable reasons justify audit: the blob host for the
        # wheels artifact varies per run, and mastodon/matrix hosts come from
        # the config. The comment in the workflow must say so.
        text = WORKFLOW.read_text(encoding="utf-8")
        job = text.split("  counts:")[1].split("  build:")[0]
        assert "blob host" in job and "varies per run" in job
        assert "configurable" in job
    else:
        assert "allowed-endpoints" in first["with"]


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


NO_SOURCES = "error: the config has no numbers.sources to refresh"


def fake_engine(
    tmp_path: Path,
    exit_code: int,
    platforms: dict[str, Any],
    stderr: str = "",
) -> Path:
    """A stand-in `reach-around`: records its environment, writes --out, exits."""
    path = tmp_path / "fake-engine"
    body = (
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(tmp_path / 'engine-env.json')!r}, 'w').write("
        "json.dumps(sorted(os.environ)))\n"
        f"sys.stderr.write({stderr!r})\n"
        f"if {stderr!r}: sys.exit({exit_code})\n"
        "out = sys.argv[sys.argv.index('--out') + 1]\n"
        f"json.dump({{'version': 1, 'platforms': {platforms!r}}}, open(out, 'w'))\n"
        f"sys.exit({exit_code})\n"
    )
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def run(
    tmp_path: Path,
    config_text: str,
    engine: Path,
    seed: dict[str, Any] | None = None,
    extra_env: dict[str, str] | None = None,
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
        env={"PATH": "/usr/bin:/bin", **(extra_env or {})},
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
    engine = fake_engine(tmp_path, 2, {}, NO_SOURCES)
    proc = run(tmp_path, "version: 1\n", engine, seed)
    assert proc.returncode == 0, proc.stderr
    assert result(tmp_path) == seed  # the engine said there is nothing to refresh


def test_with_nothing_at_all_an_empty_valid_file_is_written(tmp_path: Path) -> None:
    engine = fake_engine(tmp_path, 2, {}, NO_SOURCES)
    proc = run(tmp_path, "version: 1\n", engine)
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
    proc = run(tmp_path, "version: 1\n", fake_engine(tmp_path, 2, {}, NO_SOURCES), seed)
    assert proc.returncode == 0, proc.stderr
    warned = [ln for ln in proc.stdout.splitlines() if ln.startswith("::warning")]
    assert len(warned) == 1 and "tiktok" in warned[0] and "instagram" not in warned[0]


def test_a_hostile_platform_key_is_not_echoed(tmp_path: Path) -> None:
    evil = "x\n::error::pwned"
    seed = {"version": 1, "platforms": {evil: {"count": 1, "as_of": stamp(90)}}}
    proc = run(tmp_path, "version: 1\n", fake_engine(tmp_path, 2, {}, NO_SOURCES), seed)
    assert "pwned" not in proc.stdout and "pwned" not in proc.stderr


def test_a_quoted_numbers_key_still_refreshes(tmp_path: Path) -> None:
    # The old regex looked for a bare `numbers:` at the start of a line; a
    # quoted key is the same YAML and must reach the engine.
    fresh = {"bluesky": {"count": 7, "as_of": stamp(0)}}
    config = 'version: 1\n"numbers":\n  sources: []\n'
    proc = run(tmp_path, config, fake_engine(tmp_path, 0, fresh))
    assert proc.returncode == 0, proc.stderr
    assert result(tmp_path)["platforms"] == fresh


def test_an_engine_config_error_is_not_mistaken_for_no_block(tmp_path: Path) -> None:
    engine = fake_engine(tmp_path, 2, {}, "error: version: unsupported")
    proc = run(tmp_path, "version: 1\n", engine)
    assert proc.returncode != 0
    assert "::error" in proc.stdout


YT_CONFIG = (
    "version: 1\nnumbers:\n  sources:\n"
    "    - {platform: youtube, kind: youtube, channel_id: UCxxxxxxxxxxxxxxxxxxxxxx,"
    " api_key_env: MY_YT_KEY}\n"
    "    - {platform: twitch, kind: twitch, login: abc,"
    " client_id_env: MY_TW_ID, client_secret_env: MY_TW_SECRET}\n"
)


def engine_env(tmp_path: Path) -> list[str]:
    names: list[str] = json.loads((tmp_path / "engine-env.json").read_text())
    return names


def test_the_engine_gets_only_an_allow_list_of_the_environment(
    tmp_path: Path,
) -> None:
    proc = run(
        tmp_path,
        YT_CONFIG,
        fake_engine(tmp_path, 0, {}),
        extra_env={
            "HOME": str(tmp_path),
            "LANG": "C.UTF-8",
            "MY_YT_KEY": "k",
            "MY_TW_ID": "i",
            "MY_TW_SECRET": "s",
            "UNRELATED_SECRET": "x",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "u",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t",
            "GITHUB_TOKEN": "g",
            "RUNNER_TEMP": "r",
        },
    )
    assert proc.returncode == 0, proc.stderr
    got = set(engine_env(tmp_path))
    assert {"MY_YT_KEY", "MY_TW_ID", "MY_TW_SECRET", "PATH", "HOME", "LANG"} <= got
    for bad in (
        "UNRELATED_SECRET",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "GITHUB_TOKEN",
        "RUNNER_TEMP",
    ):
        assert bad not in got


@pytest.mark.parametrize(
    "name",
    [
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "GITHUB_TOKEN",
        "RUNNER_TEMP",
        "lowercase",
        "BAD-NAME",
        "A",
    ],
)
def test_env_names_that_could_expose_runner_credentials_are_refused(
    tmp_path: Path, name: str
) -> None:
    config = YT_CONFIG.replace("MY_YT_KEY", name)
    proc = run(
        tmp_path,
        config,
        fake_engine(tmp_path, 0, {}),
        extra_env={name: "secret-value-123"},
    )
    assert proc.returncode != 0
    assert "::error" in proc.stdout
    assert "secret-value-123" not in proc.stdout + proc.stderr
    assert not (tmp_path / "engine-env.json").exists()  # the engine never ran


# ---- environment binding and the engine wheel hash ------------------------


def test_the_counts_job_has_its_own_protected_environment() -> None:
    counts = jobs()["counts"]
    assert counts["environment"] == "counts-secrets"
    # Every job that can ask for an OIDC token names an environment, and no
    # two share one, so the Doppler subject for one cannot be used by another.
    seen: dict[str, str] = {}
    for name, job in jobs().items():
        if "id-token" in job.get("permissions", {}):
            env = job.get("environment")
            assert isinstance(env, str) and env, name
            assert env not in seen, (name, seen.get(env))
            seen[env] = name
    assert seen == {"counts-secrets": "counts", "test": "deploy"}


def test_the_engine_wheel_is_hash_checked_before_it_is_installed() -> None:
    out = jobs()["wheels"]["outputs"]
    assert "engine_sha256" in out and "steps." in out["engine_sha256"]
    hash_step = next(s for s in jobs()["wheels"]["steps"] if s.get("id") == "hash")
    assert "sha256sum" in hash_step["run"] and "GITHUB_OUTPUT" in hash_step["run"]
    steps = jobs()["counts"]["steps"]
    names = [s.get("name") for s in steps]
    install = next(
        s for s in steps if s.get("name") == "Install the engine from wheels"
    )
    assert install["env"]["ENGINE_SHA256"] == (
        "${{ needs.wheels.outputs.engine_sha256 }}"
    )
    run = install["run"]
    assert "sha256sum" in run and "ENGINE_SHA256" in run
    # The check refuses on mismatch and comes before the engine is installed.
    assert run.index("sha256sum") < run.index("--no-deps")
    assert "exit 1" in run
    engine = [ln for ln in run.splitlines() if "--no-deps" in ln]
    assert len(engine) == 1
    assert "--no-index" in engine[0] and "--find-links" not in engine[0]
    assert names.index("Install the engine from wheels") < names.index(REFRESH_STEP)


def load_script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("test_counts_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("age", "warned"),
    [
        (timedelta(days=30), False),
        (timedelta(days=30, seconds=1), True),
        (timedelta(days=30, hours=1), True),
        (timedelta(days=29, hours=23), False),
    ],
)
def test_the_stale_threshold_is_over_30_days_not_31_whole_days(
    tmp_path: Path, age: timedelta, warned: bool
) -> None:
    script = load_script()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    when = (now - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = tmp_path / "numbers.json"
    path.write_text(
        json.dumps({"version": 1, "platforms": {"tiktok": {"as_of": when}}}),
        encoding="utf-8",
    )
    assert bool(script.stale_warnings(path, now)) is warned


def test_the_docs_name_the_counts_identity_variable_and_claim_no_more() -> None:
    readme = (ROOT / "examples" / "instance-repo" / "README.md").read_text("utf-8")
    assert "DOPPLER_COUNTS_IDENTITY_ID" in readme
    docs = (ROOT / "docs" / "deploy.md").read_text("utf-8")
    assert "does not name a workflow" in docs
    assert "no other job or workflow, on `main` or" not in docs
    assert "As shipped, this refreshes nothing" in docs
