# SPDX-License-Identifier: Apache-2.0
"""deploy-test.yml refreshes follower counts before every build (issue #288):
a daily schedule, a keys-only step in a job that cannot write, the numbers file
handed to the build as data, and the script that does the work."""

from __future__ import annotations

import ast
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


def _truth(node: ast.expr) -> bool:
    """Evaluate a parsed boolean expression made only of and/or/==/constants."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.BoolOp):
        parts = [_truth(v) for v in node.values]
        return all(parts) if isinstance(node.op, ast.And) else any(parts)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = node.left, node.comparators[0]
        assert isinstance(left, ast.Constant) and isinstance(right, ast.Constant)
        same = left.value == right.value
        if isinstance(node.ops[0], ast.Eq):
            return same
        assert isinstance(node.ops[0], ast.NotEq)
        return not same
    raise AssertionError(f"unsupported expression: {ast.dump(node)}")


BUILD_IF = (
    "${{ !cancelled() && github.ref == 'refs/heads/main'"
    " && (inputs.skip_counts || (needs.wheels.result == 'success'"
    " && (needs.counts.result == 'success' || needs.counts.result == 'failure'))) }}"
)
USE_COUNTS = "!inputs.skip_counts && needs.counts.result == 'success'"


def build_runs(
    cond: str,
    *,
    cancelled: bool = False,
    ref: str = "refs/heads/main",
    skip: bool = False,
    wheels: str = "success",
    counts: str = "success",
) -> bool:
    """Evaluate the build's `if` for one combination (the expression is small
    and fixed, so it is rewritten into Python rather than parsed)."""
    expr = cond.strip().removeprefix("${{").removesuffix("}}").strip()
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = expr.replace("!cancelled()", str(not cancelled))
    expr = expr.replace("!inputs.skip_counts", str(not skip))
    expr = expr.replace("inputs.skip_counts", str(skip))
    expr = expr.replace("github.ref", repr(ref))
    expr = expr.replace("needs.wheels.result", repr(wheels))
    expr = expr.replace("needs.counts.result", repr(counts))
    assert not re.search(r"[A-Za-z_]+\(", expr), expr  # no unevaluated calls
    return _truth(ast.parse(expr, mode="eval").body)


def test_the_build_condition_is_exactly_the_reviewed_one() -> None:
    assert " ".join(jobs()["build"]["if"].split()) == " ".join(BUILD_IF.split())
    assert jobs()["build"]["needs"] == ["wheels", "counts"]


def test_the_build_runs_after_a_counts_success_failure_or_skip() -> None:
    cond = jobs()["build"]["if"]
    assert build_runs(cond)
    # Owner decision (#317): a failed counts job does not block the deploy.
    assert build_runs(cond, counts="failure")
    # Skipped on purpose: wheels and counts are both skipped.
    assert build_runs(cond, skip=True, wheels="skipped", counts="skipped")


def test_the_build_never_runs_when_a_guard_fails() -> None:
    cond = jobs()["build"]["if"]
    assert not build_runs(cond, cancelled=True)
    assert not build_runs(cond, cancelled=True, counts="failure")
    assert not build_runs(cond, ref="refs/heads/feature")
    assert not build_runs(cond, ref="refs/heads/feature", counts="failure")
    assert not build_runs(cond, ref="refs/heads/feature", skip=True)
    assert not build_runs(cond, wheels="failure", counts="skipped")
    assert not build_runs(cond, wheels="cancelled", counts="skipped")
    assert not build_runs(cond, counts="cancelled")
    assert not build_runs(cond, counts="skipped")
    # The guard bites: loosening it to a bare failure-tolerant form lets
    # a failed wheels job through, which the checks above would catch.
    loose = "${{ !cancelled() && github.ref == 'refs/heads/main' }}"
    assert build_runs(loose, wheels="failure", counts="skipped")


def test_the_build_uses_counts_artifacts_only_after_a_counts_success() -> None:
    build = jobs()["build"]
    steps = build["steps"]
    download = next(
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
        and s["with"]["name"] == "counts-numbers"
    )
    assert download["if"] == "${{ " + USE_COUNTS + " }}"
    step = next(s for s in steps if s.get("name") == "Build the site")
    assert step["env"]["USE_COUNTS"] == "${{ " + USE_COUNTS + " && 'true' || 'false' }}"
    run = step["run"]
    # Both forms exist: with the counts file, and without (the committed file).
    assert run.count("reach-around build") == 2
    assert "--numbers counts-in/numbers.json" in run
    assert 'if [ "$USE_COUNTS" = "true" ]' in run
    # The deploy waits for the build only; a skipped counts job is not in its path.
    assert jobs()["deploy"]["needs"] == "build"


NOTIFY_IF = (
    "${{ !cancelled() && github.event_name == 'schedule'"
    " && github.ref == 'refs/heads/main'"
    " && (needs.wheels.result == 'failure' || needs.counts.result == 'failure') }}"
)


def notify_runs(
    cond: str,
    *,
    event: str,
    counts: str = "success",
    wheels: str = "success",
    cancelled: bool = False,
) -> bool:
    expr = cond.strip().removeprefix("${{").removesuffix("}}").strip()
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = expr.replace("!cancelled()", str(not cancelled))
    expr = expr.replace("github.event_name", repr(event))
    expr = expr.replace("github.ref", repr("refs/heads/main"))
    expr = expr.replace("needs.counts.result", repr(counts))
    expr = expr.replace("needs.wheels.result", repr(wheels))
    return _truth(ast.parse(expr, mode="eval").body)


def test_one_alert_job_runs_only_for_a_failed_scheduled_refresh() -> None:
    notify = jobs()["notify"]
    assert " ".join(notify["if"].split()) == " ".join(NOTIFY_IF.split())
    cond = notify["if"]
    # A counts failure alerts.
    assert notify_runs(cond, event="schedule", counts="failure")
    # A wheels failure (counts is then skipped) alerts too.
    assert notify_runs(cond, event="schedule", wheels="failure", counts="skipped")
    # Manual runs never alert.
    assert not notify_runs(cond, event="workflow_dispatch", counts="failure")
    assert not notify_runs(
        cond, event="workflow_dispatch", wheels="failure", counts="skipped"
    )
    # Success, skips and cancellations never alert.
    assert not notify_runs(cond, event="schedule")
    assert not notify_runs(cond, event="schedule", counts="skipped")
    assert not notify_runs(cond, event="schedule", counts="cancelled")
    assert not notify_runs(cond, event="schedule", wheels="cancelled", counts="skipped")
    assert not notify_runs(cond, event="schedule", counts="failure", cancelled=True)
    assert not notify_runs(
        cond, event="schedule", wheels="failure", counts="skipped", cancelled=True
    )
    # Exactly wheels and counts. The deploy job waits for the approval-gated
    # `test` environment; with it in needs, a scheduled run pending approval
    # would hold the alert back forever. The condition decides on wheels and
    # counts alone, so a deploy still waiting does not matter.
    assert notify["needs"] == ["wheels", "counts"]
    assert "needs.deploy" not in cond and "needs.build" not in cond
    assert notify_runs(cond, event="schedule", counts="failure")  # deploy pending


def test_the_alert_job_can_only_write_issues_and_holds_no_secret() -> None:
    notify = jobs()["notify"]
    assert notify["permissions"] == {"issues": "write"}
    assert "id-token" not in yaml.safe_dump(notify)
    assert "environment" not in notify and "env" not in notify
    text = yaml.safe_dump(notify)
    assert "secrets." not in text
    assert "counts-numbers" not in text
    for s in notify["steps"]:
        if "uses" in s:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
        # Values reach the shell only through env, never spliced into run.
        assert "${{" not in s.get("run", ""), s.get("name")
    step = next(s for s in notify["steps"] if "run" in s)
    assert "github.token" in str(step["env"].get("GH_TOKEN"))
    assert "Counts refresh failed" in step["run"]
    assert "gh api" in step["run"]
    assert "pull_request" in step["run"]  # the issues list also holds PRs
    # One issue: it looks for an open one first, then comments or creates.
    assert step["run"].index("issues?state=open") < step["run"].index("-X POST")
    # Only what is known when the job starts goes into the body.
    assert set(step["env"]) == {
        "GH_TOKEN",
        "GH_REPO",
        "RUN_URL",
        "WHEELS_RESULT",
        "COUNTS_RESULT",
    }


def alert_script() -> str:
    notify = jobs()["notify"]
    step = next(s for s in notify["steps"] if "run" in s)
    run: str = step["run"]
    return run


def test_the_alert_step_fails_on_a_failed_listing_and_never_pipes_to_head() -> None:
    run = alert_script()
    lines = [ln.strip() for ln in run.splitlines()]
    assert "set -euo pipefail" in lines
    # Capture the whole listing into a variable, then select from it. A pipe
    # into head (or any early-closing reader) is what masked a failed gh.
    assert 'listing="$(' in run
    assert not re.search(r"\|\s*head\b", run)
    assert "sed -n 1p" in run
    assert run.index('listing="$(') < run.index('existing="$(')


def run_alert(tmp_path: Path, script: str, list_exit: int, list_out: str) -> list[str]:
    """Run the alert step's script with a stub `gh`; return the calls it made."""
    log = tmp_path / "calls.log"
    stub = tmp_path / "gh"
    stub.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{log}"\n'
        'case "$*" in\n'
        f'  *"issues?state=open"*) printf "%s" "{list_out}"; exit {list_exit};;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    # The workflow calls /usr/bin/gh by absolute path; point it at the stub.
    patched = script.replace("/usr/bin/gh", str(stub))
    assert str(stub) in patched
    env = {
        "GH_TOKEN": "not-a-token",
        "GH_REPO": "owner/repo",
        "RUN_URL": "https://example.invalid/run/1",
        "WHEELS_RESULT": "success",
        "COUNTS_RESULT": "failure",
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/bin/bash", "-c", patched],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    (tmp_path / "rc").write_text(str(result.returncode), encoding="utf-8")
    return calls


def alert_rc(tmp_path: Path) -> int:
    return int((tmp_path / "rc").read_text(encoding="utf-8"))


def test_a_failed_issue_listing_fails_the_step_and_creates_nothing(
    tmp_path: Path,
) -> None:
    calls = run_alert(tmp_path, alert_script(), 1, "")
    assert alert_rc(tmp_path) != 0
    assert len(calls) == 1 and "issues?state=open" in calls[0]
    assert not any("-X POST" in c for c in calls)


def test_the_alert_step_creates_once_or_comments_on_the_open_issue(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    calls = run_alert(fresh, alert_script(), 0, "")
    assert alert_rc(fresh) == 0
    posts = [c for c in calls if "-X POST" in c]
    assert len(posts) == 1 and posts[0].split()[3] == "repos/owner/repo/issues"
    assert "title=Counts refresh failed" in posts[0]
    known = tmp_path / "known"
    known.mkdir()
    calls = run_alert(known, alert_script(), 0, "42\n57\n")
    assert alert_rc(known) == 0
    posts = [c for c in calls if "-X POST" in c]
    assert len(posts) == 1 and "issues/42/comments" in posts[0]
    assert "title=" not in posts[0]


def test_the_failed_listing_check_bites_without_pipefail(tmp_path: Path) -> None:
    # The old shape (a pipe into head, no pipefail) swallows the failure and
    # goes on to create an issue: the behavior test above would catch that.
    script = alert_script()
    head, rest = script.split('listing="$(', 1)
    tail = rest[rest.index('if [ -n "$existing" ]') :]
    old = (
        head.replace("set -euo pipefail", "set -eu")
        + 'existing="$(/usr/bin/gh api --paginate '
        + '"repos/${GH_REPO}/issues?state=open&per_page=100" | head -n 1)"\n'
        + tail
    )
    assert "set -euo pipefail" not in old and "| head -n 1" in old
    calls = run_alert(tmp_path, old, 1, "")
    assert any("-X POST" in c for c in calls)


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
    " api_key_env: YOUTUBE_API_KEY}\n"
    "    - {platform: twitch, kind: twitch, login: abc,"
    " client_id_env: TWITCH_CLIENT_ID, client_secret_env: TWITCH_CLIENT_SECRET}\n"
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
            "YOUTUBE_API_KEY": "k",
            "TWITCH_CLIENT_ID": "i",
            "TWITCH_CLIENT_SECRET": "s",
            "UNRELATED_SECRET": "x",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "u",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t",
            "GITHUB_TOKEN": "g",
            "RUNNER_TEMP": "r",
        },
    )
    assert proc.returncode == 0, proc.stderr
    got = set(engine_env(tmp_path))
    assert {
        "YOUTUBE_API_KEY",
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "PATH",
        "HOME",
        "LANG",
    } <= got
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
    config = YT_CONFIG.replace("api_key_env: YOUTUBE_API_KEY", f"api_key_env: {name}")
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


def test_a_config_named_variable_is_never_forwarded(tmp_path: Path) -> None:
    # a well-formed but unrelated name: the engine runs (and refuses it itself),
    # but the named variable never reaches it
    config = YT_CONFIG.replace(
        "api_key_env: YOUTUBE_API_KEY", "api_key_env: UNRELATED_RUNNER_SECRET"
    )
    proc = run(
        tmp_path,
        config,
        fake_engine(tmp_path, 0, {}),
        extra_env={"UNRELATED_RUNNER_SECRET": "x", "YOUTUBE_API_KEY": "k"},
    )
    assert proc.returncode == 0, proc.stderr
    got = set(engine_env(tmp_path))
    assert "UNRELATED_RUNNER_SECRET" not in got
    assert "YOUTUBE_API_KEY" in got


def test_the_canonical_name_is_forwarded_when_the_field_is_omitted(
    tmp_path: Path,
) -> None:
    config = YT_CONFIG.replace(" api_key_env: YOUTUBE_API_KEY", "").replace(
        "UCxxxxxxxxxxxxxxxxxxxxxx,}", "UCxxxxxxxxxxxxxxxxxxxxxx}"
    )
    assert "api_key_env" not in config
    proc = run(
        tmp_path,
        config,
        fake_engine(tmp_path, 0, {}),
        extra_env={"YOUTUBE_API_KEY": "k", "AWS_SECRET_ACCESS_KEY": "x"},
    )
    assert proc.returncode == 0, proc.stderr
    got = set(engine_env(tmp_path))
    assert "YOUTUBE_API_KEY" in got and "AWS_SECRET_ACCESS_KEY" not in got


def test_the_wrapper_canonical_names_match_the_engine() -> None:
    import importlib.util

    from reach_around.config import numbers_sources as ns

    spec = importlib.util.spec_from_file_location("tc", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    expected = {
        "youtube": {ns.YOUTUBE_KEY_ENV},
        "twitch": {
            ns.CLIENT_ID_ENV,
            ns.CLIENT_SECRET_ENV,
            ns.USER_TOKEN_ENV,
            ns.REFRESH_TOKEN_ENV,
        },
        "instagram": {"INSTAGRAM_ACCESS_TOKEN"},
        "threads": {"THREADS_ACCESS_TOKEN"},
        "tiktok": {
            ns.TIKTOK_ACCESS_ENV,
            ns.TIKTOK_REFRESH_ENV,
            ns.TIKTOK_CLIENT_KEY_ENV,
            ns.TIKTOK_CLIENT_SECRET_ENV,
        },
    }
    assert {k: set(v) for k, v in mod.CANONICAL_ENV.items()} == expected


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
