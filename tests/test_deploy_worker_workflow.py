# SPDX-License-Identifier: Apache-2.0
"""The reusable Worker deploy (.github/workflows/deploy-worker.yml, issue #596).

Two kinds of test. The shell in the real steps is run with sample values: the
deploy config it renders must stay byte-for-byte what deploy-test.yml wrote
before the extraction (golden files made from the unchanged workflow), and the
quiet switches and the Worker name override are checked. The YAML itself is
guarded: pins, least-privilege permissions, no GitHub secret anywhere, no
placeholder id, id-token only on the job that fetches credentials, and a caller
(deploy-test.yml) that grants what the callee declares."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CALLEE = WORKFLOWS / "deploy-worker.yml"
CALLER = WORKFLOWS / "deploy-test.yml"
GOLDEN = ROOT / "tests" / "fixtures" / "worker_deploy"

PIN = re.compile(r"[\w./-]+@[0-9a-f]{40}")
COMMENT_PIN = re.compile(r"uses:\s+\S+@[0-9a-f]{40}\s+#\s+v\d+(\.\d+)*\s*$")

# The inputs deploy-test.yml passes for the test deploy. The golden file
# tests/fixtures/worker_deploy/test.active.toml was rendered by the step of the
# UNCHANGED deploy-test.yml (before issue #596) with exactly these values.
TEST_ENV = {
    "D1_DATABASE_ID": "11111111-2222-3333-4444-555555555555",
    "RATELIMIT_NAMESPACE_ID": "2001",
    "SITE_HOST": "links-test.example.test",
    "ZONE_NAME": "example.test",
    "LINK_IDS": '["example-one","example-two"]',
    "DESTINATION_KINDS": '{"example-one":"link","example-two":"link"}',
    "LEAD_RETENTION_DAYS": "30",
    "ALERTS_CONFIG": '{"analytics_health":true}',
    "LIVE_TWITCH_HANDLE": "n0tst",
    "LIVE_KICK_HANDLE": "n0tst-kick",
    "LIVE_KV_ID": "0123456789abcdef0123456789abcdef",
    "PAGE_VIEWS": "true",
    "PAGE_IDS": '["home","kit"]',
    "VIEW_LIMITER_NAMESPACE_ID": "2002",
    "VIEWS_PER_HOUR": "120",
    "BOT_VIEWS_PER_HOUR": "30",
    "ACCOUNT_VIEWS_PER_HOUR": "480",
    "ACCOUNT_BOT_VIEWS_PER_HOUR": "120",
    "ROUTE_BUDGET_GATE": "100",
    "MAX_LEADS": "500",
    "D1_DAILY_WRITE_QUOTA": "100000",
    "CLICK_DAILY_WRITE_CEILING": "20000",
    "BEACON_MODE": "observe",
    "MINT_LIMITER_NAMESPACE_ID": "2003",
}
# What an instance repository's staging deploy might pass.
STAGING_ENV = {
    "D1_DATABASE_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "RATELIMIT_NAMESPACE_ID": "3001",
    "SITE_HOST": "staging.example.test",
    "ZONE_NAME": "example.test",
    "LINK_IDS": '["alpha","beta"]',
    "DESTINATION_KINDS": '{"alpha":"link","beta":"link"}',
    "LEAD_RETENTION_DAYS": "90",
    "D1_DAILY_WRITE_QUOTA": "100000",
    "CLICK_DAILY_WRITE_CEILING": "20000",
}


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def callee_steps() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load(CALLEE)["jobs"]["deploy"]["steps"]
    return result


def step(name: str) -> dict[str, Any]:
    found = [s for s in callee_steps() if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def render(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the real 'Write deploy config' script against worker/wrangler.toml."""
    (tmp_path / "wrangler.toml").write_text(
        (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", step("Write deploy config")["run"]],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        check=False,
    )


def active_lines(path: Path) -> list[str]:
    """The lines that do something: comments and blank lines dropped, so a
    comment edit in worker/wrangler.toml never moves the golden."""
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


# ---- the rendered config is what deploy-test.yml wrote before -----------------


@pytest.mark.parametrize(
    ("name", "env"), [("test", TEST_ENV), ("staging", STAGING_ENV)]
)
def test_the_rendered_config_matches_the_golden(
    tmp_path: Path, name: str, env: dict[str, str]
) -> None:
    done = render(tmp_path, env)
    assert done.returncode == 0, done.stdout + done.stderr
    golden = active_lines(GOLDEN / f"{name}.active.toml")
    assert golden, "the golden is empty; the comparison would be vacuous"
    assert active_lines(tmp_path / "wrangler.deploy.toml") == golden


def test_the_golden_bites(tmp_path: Path) -> None:
    """A changed input changes the render, so the comparison above is not blind."""
    done = render(tmp_path, {**STAGING_ENV, "SITE_HOST": "other.example.test"})
    assert done.returncode == 0
    assert active_lines(tmp_path / "wrangler.deploy.toml") != active_lines(
        GOLDEN / "staging.active.toml"
    )


def test_the_golden_names_no_kill_switch_and_the_default_worker_name() -> None:
    # Kill switches are unset unless asked for, and the Worker name is not in
    # the file: it is passed with --name (see below).
    for name in ("test", "staging"):
        text = (GOLDEN / f"{name}.active.toml").read_text(encoding="utf-8")
        assert "KILL" not in text
        assert 'name = "reach-around-clicks"\n' in text


# ---- the kill switches and quiet ----------------------------------------------


def _kills(path: Path) -> set[str]:
    return {
        ln.split(" = ")[0]
        for ln in active_lines(path)
        if ln.split(" = ")[0] in {"CLICKS_KILL", "VIEWS_KILL", "SCHEDULED_KILL"}
    }


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, set()),
        ({"CLICKS_KILL": "false", "VIEWS_KILL": "false"}, set()),
        ({"CLICKS_KILL": "true"}, {"CLICKS_KILL"}),
        (
            {"SCHEDULED_KILL": "true", "VIEWS_KILL": "true"},
            {"SCHEDULED_KILL", "VIEWS_KILL"},
        ),
        (
            {"CLICKS_KILL": "true", "VIEWS_KILL": "true", "SCHEDULED_KILL": "true"},
            {"CLICKS_KILL", "VIEWS_KILL", "SCHEDULED_KILL"},
        ),
        # only the exact text true switches one on
        ({"CLICKS_KILL": "1", "VIEWS_KILL": "True", "SCHEDULED_KILL": "yes"}, set()),
    ],
)
def test_kill_switches_are_written_only_for_the_exact_text_true(
    tmp_path: Path, env: dict[str, str], expected: set[str]
) -> None:
    done = render(tmp_path, {**STAGING_ENV, **env})
    assert done.returncode == 0, done.stdout + done.stderr
    out = tmp_path / "wrangler.deploy.toml"
    assert _kills(out) == expected
    for name in expected:
        assert f'{name} = "1"' in active_lines(out)


def test_quiet_turns_all_three_switches_on_and_each_switch_can_be_set_alone() -> None:
    env = step("Write deploy config")["env"]
    for name, own in (
        ("CLICKS_KILL", "clicks_kill"),
        ("VIEWS_KILL", "views_kill"),
        ("SCHEDULED_KILL", "scheduled_kill"),
    ):
        assert env[name] == "${{ inputs.quiet || inputs." + own + " }}"


def test_a_quiet_deploy_does_not_migrate_and_a_normal_one_does() -> None:
    migrate = step("Apply D1 migrations")
    assert migrate["if"] == "${{ !inputs.quiet }}"
    assert "migrations apply DB --remote" in migrate["run"]
    # the Worker deploy itself always runs
    assert "if" not in step("Deploy the Worker")
    names = [s.get("name") for s in callee_steps()]
    # normal order: migrations, then the Worker (as deploy-test.yml always did)
    assert names.index("Apply D1 migrations") < names.index("Deploy the Worker")


# ---- the Worker name override ---------------------------------------------------


def test_the_worker_is_always_deployed_under_the_name_input() -> None:
    deploy = step("Deploy the Worker")
    assert deploy["env"]["WORKER_NAME"] == "${{ inputs.worker_name }}"
    assert '--name "$WORKER_NAME"' in deploy["run"]
    assert "--config wrangler.deploy.toml" in deploy["run"]
    spec = load(CALLEE)[True]["workflow_call"]["inputs"]["worker_name"]
    # required, with no default: omitting it can never fall back to the test Worker
    assert spec.get("required") is True and "default" not in spec


@pytest.mark.parametrize("bad", ["", "UPPER", "-lead", "a b", "a;b", "a" * 64, "x\ny"])
def test_a_bad_worker_name_is_refused(bad: str) -> None:
    done = _check({"WORKER_NAME": bad})
    assert done.returncode != 0, bad
    assert "WORKER_NAME" in done.stdout or "worker_name" in done.stdout


def test_a_good_worker_name_passes_the_same_check() -> None:
    assert _check({"WORKER_NAME": "reach-around-clicks-staging"}).returncode == 0


# ---- the plain-configuration check ----------------------------------------------

CHECK_BASE = {
    "CLOUDFLARE_ACCOUNT_ID": "acct",
    "WORKER_NAME": "reach-around-clicks-staging",
    "D1_DATABASE_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "RATELIMIT_NAMESPACE_ID": "3001",
    "SITE_HOST": "staging.example.test",
}


def _check(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", step("Check configuration")["run"]],
        env={"PATH": "/usr/bin:/bin", **CHECK_BASE, **extra},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ({"D1_DATABASE_ID": "00000000-0000-0000-0000-000000000000"}, "placeholder"),
        ({"RATELIMIT_NAMESPACE_ID": "1001"}, "placeholder"),
        (
            {
                "PAGE_VIEWS": "true",
                "PAGE_IDS": '["home"]',
                "ZONE_NAME": "example.test",
                "VIEW_LIMITER_NAMESPACE_ID": "1002",
            },
            "placeholder",
        ),
        (
            {
                "BEACON_MODE": "observe",
                "MINT_LIMITER_NAMESPACE_ID": "1004",
            },
            "MINT_LIMITER_NAMESPACE_ID",
        ),
        (
            {
                "LIVE_TWITCH_HANDLE": "n0tst",
                "LIVE_KV_ID": "0" * 32,
            },
            "placeholder",
        ),
    ],
)
def test_placeholder_ids_from_wrangler_toml_are_refused(
    extra: dict[str, str], needle: str
) -> None:
    done = _check(extra)
    assert done.returncode != 0
    assert needle in done.stdout


def test_the_check_is_not_vacuous_a_good_set_passes() -> None:
    assert _check({}).returncode == 0
    assert (
        _check(
            {
                "PAGE_VIEWS": "true",
                "PAGE_IDS": '["home"]',
                "ZONE_NAME": "example.test",
                "VIEW_LIMITER_NAMESPACE_ID": "3002",
            }
        ).returncode
        == 0
    )


@pytest.mark.parametrize(
    "bad",
    ["[]", '"x"', "{", "{'a': 1}", '{"a": "b;c"}', '{"a":"$(x)"}', "x" * 7000],
)
def test_sites_must_be_one_json_object_of_plain_characters(bad: str) -> None:
    assert _check({"SITES": bad}).returncode != 0


def test_a_good_sites_value_passes_and_is_written(tmp_path: Path) -> None:
    sites = '{"links":{"hosts":["staging.example.test"],"mode":"beacon"}}'
    assert _check({"SITES": sites}).returncode == 0
    done = render(tmp_path, {**STAGING_ENV, "SITES": sites})
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"SITES = '{sites}'" in active_lines(tmp_path / "wrangler.deploy.toml")


@pytest.mark.parametrize("bad", ["a b/e/*", "x/e/*;rm", "Host/e/*", "nopath", "/e/*"])
def test_a_bad_route_pattern_is_refused(bad: str) -> None:
    assert _check({"ROUTE_PATTERN": bad}).returncode != 0


def test_a_route_pattern_overrides_the_default_route(tmp_path: Path) -> None:
    done = render(
        tmp_path, {**STAGING_ENV, "ROUTE_PATTERN": "staging.example.test/x/*"}
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = active_lines(tmp_path / "wrangler.deploy.toml")
    assert 'pattern = "staging.example.test/x/*"' in lines
    assert 'pattern = "staging.example.test/e/*"' not in lines


# ---- the engine pin -------------------------------------------------------------


def _pin(repo: str, ref: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", step("Check the engine pin")["run"]],
        env={"PATH": "/usr/bin:/bin", "ENGINE_REPO": repo, "ENGINE_REF": ref},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("ref", ["main", "v0.2.0", "a" * 39, "A" * 40, "a" * 41, ""])
def test_the_engine_ref_must_be_a_full_commit(ref: str) -> None:
    assert _pin("ChiefGyk3D/Reach-Around", ref).returncode != 0


@pytest.mark.parametrize("repo", ["", "owner", "a/b/c", "o/n;x", "o/n n"])
def test_the_engine_repo_must_be_owner_and_name(repo: str) -> None:
    assert _pin(repo, "a" * 40).returncode != 0


def test_a_good_engine_pin_passes() -> None:
    assert (
        _pin("ChiefGyk3D/Reach-Around", "0123456789abcdef" * 2 + "01234567").returncode
        == 0
    )


# ---- supply chain, permissions, secrets -----------------------------------------


def all_workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def unpinned(text: str) -> list[str]:
    """Every `uses:` that is not a full commit with a version comment, except a
    same-repository workflow call (checked by test_the_caller_...)."""
    bad = []
    for line in text.splitlines():
        m = re.match(r"\s*(?:-\s+)?uses:\s+(\S+)", line)
        if not m or m.group(1).startswith("./"):
            continue
        if not PIN.fullmatch(m.group(1)) or not COMMENT_PIN.search(line):
            bad.append(line.strip())
    return bad


def test_every_action_in_every_workflow_is_pinned_with_a_version_comment() -> None:
    files = all_workflows()
    assert CALLEE in files and CALLER in files, "the guard must see both files"
    total = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        total += len(re.findall(r"^\s*(?:-\s+)?uses:", text, flags=re.M))
        assert unpinned(text) == [], path.name
    assert total > 10


def test_the_pin_guard_bites() -> None:
    assert unpinned("      - uses: actions/checkout@main\n")
    assert unpinned("      - uses: actions/checkout@v4 # v4\n")
    assert unpinned(f"      - uses: actions/checkout@{'a' * 40}\n")  # no version
    assert unpinned(f"      - uses: actions/checkout@{'a' * 39}\n")
    assert not unpinned(f"      - uses: actions/checkout@{'a' * 40} # v7.0.1\n")
    assert not unpinned("    uses: ./.github/workflows/deploy-worker.yml\n")


def test_the_callee_pins_are_the_ones_deploy_test_already_used() -> None:
    callee = {s["uses"] for s in callee_steps() if "uses" in s}
    caller = {
        s["uses"]
        for job in load(CALLER)["jobs"].values()
        for s in job.get("steps", [])
        if "uses" in s
    }
    assert callee, "no action found; the comparison would be vacuous"
    assert callee <= caller, sorted(callee - caller)


def test_the_callee_has_one_job_with_least_privilege() -> None:
    data = load(CALLEE)
    assert data["permissions"] == {"contents": "read"}
    assert list(data["jobs"]) == ["deploy"]
    job = data["jobs"]["deploy"]
    # id-token only on the one job that fetches the credentials
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert any(s.get("id") == "cf_doppler" for s in job["steps"])
    assert job["if"] == "github.ref == 'refs/heads/main'"  # refuse any other ref
    assert job["environment"] == "${{ inputs.environment }}"  # the caller's gate
    assert "env" not in job  # no job-level env: the token is never job-wide
    assert "continue-on-error" not in CALLEE.read_text(encoding="utf-8")


def test_the_callee_reads_no_github_secret_and_takes_none() -> None:
    # comments aside: they may talk about Worker secrets
    text = "\n".join(
        ln
        for ln in CALLEE.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "secrets." not in text  # the Doppler output is `steps.<id>.outputs.secrets)`
    assert text.count("outputs.secrets") == 3, "the three token mappings"
    assert "secrets:" not in text
    assert "inherit" not in text
    spec = load(CALLEE)[True]["workflow_call"]
    assert "secrets" not in spec and "outputs" not in spec
    for name in spec["inputs"]:
        assert not re.search(r"token|secret|key|password", name), name
    # the one credential is the masked Doppler output, mapped into exactly the
    # steps that need it, never into GITHUB_ENV
    assert "GITHUB_ENV" not in text and "GITHUB_OUTPUT" not in text
    carriers = [
        s["name"] for s in callee_steps() if "CLOUDFLARE_API_TOKEN" in s.get("env", {})
    ]
    assert carriers == [
        "Require the Cloudflare token",
        "Apply D1 migrations",
        "Deploy the Worker",
    ]


def test_no_input_reaches_a_run_block_unquoted_through_the_expression_syntax() -> None:
    # Expression injection: nothing from ${{ }} may sit inside a run: script.
    runs = [str(s["run"]) for s in callee_steps() if "run" in s]
    assert len(runs) >= 8
    for run in runs:
        assert "${{" not in run, run[:80]


def test_the_credential_steps_come_after_every_install_and_check() -> None:
    names = [s.get("name") for s in callee_steps()]
    fetch = names.index("Fetch Cloudflare credentials from Doppler")
    for before in (
        "Check configuration",
        "Verify the build manifest",
        "Install Worker dependencies",
        "Check the page-view write budget",
        "Check the click write budget",
        "Write deploy config",
    ):
        assert names.index(before) < fetch, before
    fetch_step = step("Fetch Cloudflare credentials from Doppler")
    assert fetch_step["with"]["export"] == "outputs"
    assert fetch_step["with"]["names"] == "CLOUDFLARE_KEY_VALUE"
    assert fetch_step["with"]["required"] == "true"
    assert "token" not in fetch_step["with"]


def test_no_literal_placeholder_id_is_in_either_workflow() -> None:
    # Placeholders live only in worker/wrangler.toml; the workflows may name them
    # in a refusal, never as a value passed on.
    for path in (CALLEE, CALLER):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#") or "::error::" in line:
                continue
            assert "00000000-0000-0000-0000-000000000000" not in line.replace(
                '!= "00000000-0000-0000-0000-000000000000"', ""
            ), (path.name, line)
    inputs = load(CALLER)["jobs"]["worker"]["with"]
    for key, value in inputs.items():
        if key.endswith("_id") or key.endswith("_ids"):
            assert str(value).startswith("${{ vars."), (key, value)


# ---- the caller ------------------------------------------------------------------


def test_the_caller_grants_what_the_callee_declares_and_passes_known_inputs() -> None:
    data = load(CALLER)
    job = data["jobs"]["worker"]
    assert job["uses"] == "./.github/workflows/deploy-worker.yml"
    # permissions are checked when the file is parsed, so the grant must match
    callee_job = load(CALLEE)["jobs"]["deploy"]
    assert job["permissions"] == callee_job["permissions"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert "secrets" not in job  # nothing is handed over, not even `inherit`
    spec = load(CALLEE)[True]["workflow_call"]["inputs"]
    passed = job["with"]
    assert set(passed) <= set(spec), sorted(set(passed) - set(spec))
    required = {k for k, v in spec.items() if v.get("required")}
    assert required <= set(passed), sorted(required - set(passed))
    # the engine runs its own Worker at the commit being deployed
    assert passed["environment"] == "test"
    assert passed["engine_ref"] == "${{ github.sha }}"
    assert passed["engine_repo"] == "${{ github.repository }}"
    # and keeps the test Worker's name, so nothing about the test deploy moved
    assert passed["worker_name"] == "reach-around-clicks"


def test_the_test_worker_name_is_the_one_in_wrangler_toml() -> None:
    name = tomllib.loads((ROOT / "worker" / "wrangler.toml").read_text())["name"]
    assert load(CALLER)["jobs"]["worker"]["with"]["worker_name"] == name


def test_the_pages_job_waits_for_the_worker_and_keeps_its_own_gate() -> None:
    data = load(CALLER)
    deploy = data["jobs"]["deploy"]
    assert deploy["needs"] == ["build", "worker"]
    assert deploy["environment"] == "test"
    assert deploy["permissions"] == {"contents": "read", "id-token": "write"}
    assert deploy["if"] == "github.ref == 'refs/heads/main'"
    # the Worker steps are gone from the caller: no copy
    names = [s.get("name") for s in deploy["steps"]]
    for moved in (
        "Write deploy config",
        "Apply D1 migrations",
        "Deploy the Worker",
        "Verify the build manifest",
    ):
        assert moved not in names
    live = [
        ln
        for ln in CALLER.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    assert not any("wrangler.deploy.toml" in ln for ln in live)
    assert not any("wrangler deploy" in ln for ln in live)
