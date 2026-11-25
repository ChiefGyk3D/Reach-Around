# SPDX-License-Identifier: Apache-2.0
"""The reusable Worker deploy (.github/workflows/deploy-worker.yml, issue #596).

Two kinds of test. The shell in the real steps is run with sample values: the
deploy config the reusable workflow renders must equal what the inline steps of
deploy-test.yml render (golden files, made from deploy-test.yml's own step), and
the quiet switches and the Worker name override are checked. The YAML itself is
guarded: pins, least-privilege permissions, no GitHub secret anywhere, no
placeholder id, id-token only on the job that fetches credentials.

deploy-test.yml does not call the reusable workflow yet: a same-repository
`uses: ./...` call fails zizmor 1.30.1 (self-repository) and `$/...` fails
actionlint 1.7.12, so a follow-up PR switches it to a commit-pinned call once
this one is merged. Until then both files carry the same Write deploy config
step and these tests hold them equal.

To regenerate the goldens after an intended change to the step, run
`UPDATE_WORKER_DEPLOY_GOLDEN=1 pytest tests/test_deploy_worker_workflow.py -k golden`
and review the diff: they are rewritten from deploy-test.yml's inline step."""

from __future__ import annotations

import json
import os
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


def call_spec() -> dict[str, Any]:
    """The `on.workflow_call` block. YAML 1.1 reads the key `on` as True."""
    data: dict[Any, Any] = yaml.safe_load(CALLEE.read_text(encoding="utf-8"))
    spec: dict[str, Any] = data[True]["workflow_call"]
    return spec


def callee_steps() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load(CALLEE)["jobs"]["deploy"]["steps"]
    return result


def step(name: str, path: Path = CALLEE) -> dict[str, Any]:
    jobs: dict[str, Any] = load(path)["jobs"]
    # `deploy` first; the callee's `validate` job holds the pin and compare steps
    steps: list[dict[str, Any]] = [
        s for job in ("deploy", "validate") if job in jobs for s in jobs[job]["steps"]
    ]
    found = [s for s in steps if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def render(
    tmp_path: Path, env: dict[str, str], path: Path = CALLEE
) -> subprocess.CompletedProcess[str]:
    """Run the real 'Write deploy config' script against worker/wrangler.toml."""
    (tmp_path / "wrangler.toml").write_text(
        (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", step("Write deploy config", path)["run"]],
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


# ---- the rendered config is what deploy-test.yml renders ----------------------


@pytest.mark.parametrize("path", [CALLER, CALLEE], ids=["inline", "reusable"])
@pytest.mark.parametrize(
    ("name", "env"), [("test", TEST_ENV), ("staging", STAGING_ENV)]
)
def test_the_rendered_config_matches_the_golden(
    tmp_path: Path, path: Path, name: str, env: dict[str, str]
) -> None:
    done = render(tmp_path, env, path)
    assert done.returncode == 0, done.stdout + done.stderr
    rendered = active_lines(tmp_path / "wrangler.deploy.toml")
    golden_file = GOLDEN / f"{name}.active.toml"
    if os.environ.get("UPDATE_WORKER_DEPLOY_GOLDEN") == "1" and path == CALLER:
        golden_file.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    golden = active_lines(golden_file)
    assert golden, "the golden is empty; the comparison would be vacuous"
    assert rendered == golden


def _destination_kinds(tmp_path: Path, env: dict[str, str], path: Path) -> str:
    done = render(tmp_path, env, path)
    assert done.returncode == 0, done.stdout + done.stderr
    data = tomllib.loads((tmp_path / "wrangler.deploy.toml").read_text("utf-8"))
    value: str = data["vars"]["DESTINATION_KINDS"]
    return value


@pytest.mark.parametrize("path", [CALLER, CALLEE], ids=["inline", "reusable"])
@pytest.mark.parametrize(
    "supplied",
    [
        '{"alpha":"link","beta":"social_icon"}',
        '{"only-one":"link"}',
    ],
)
def test_a_manifest_supplied_destination_kinds_is_deployed_as_valid_json(
    tmp_path: Path, path: Path, supplied: str
) -> None:
    # Issue #666: a default written inside the parameter expansion appended a
    # stray closing brace, so the Worker's JSON.parse refused the value.
    env = {**STAGING_ENV, "DESTINATION_KINDS": supplied}
    value = _destination_kinds(tmp_path, env, path)
    assert json.loads(value) == json.loads(supplied)
    assert value == supplied


@pytest.mark.parametrize("path", [CALLER, CALLEE], ids=["inline", "reusable"])
def test_an_unset_destination_kinds_falls_back_to_the_valid_example(
    tmp_path: Path, path: Path
) -> None:
    env = {k: v for k, v in STAGING_ENV.items() if k != "DESTINATION_KINDS"}
    value = _destination_kinds(tmp_path, env, path)
    assert json.loads(value) == {"example-one": "link", "example-two": "link"}


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
    spec = call_spec()["inputs"]["worker_name"]
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


ZONE = {"ZONE_NAME": "example.test"}
HOST = CHECK_BASE["SITE_HOST"]  # staging.example.test


@pytest.mark.parametrize("bad", ["a b/e/*", "x/e/*;rm", "Host/e/*", "nopath", "/e/*"])
def test_a_malformed_route_pattern_is_refused(bad: str) -> None:
    done = _check({**ZONE, "ROUTE_PATTERN": bad})
    assert done.returncode != 0
    assert "route_pattern" in done.stdout


@pytest.mark.parametrize(
    "route",
    [
        "*/*",
        "***/*",
        "*.*/*",
        "*/e/*",
        "*.example.test/e/*",
        "example.test/e/*",
        "victim.example.test/*",
        "victim.example.org/*",
        f"{HOST}.evil.test/e/*",
        f"x{HOST}/e/*",
    ],
)
def test_a_route_that_is_not_exactly_the_site_host_is_refused(route: str) -> None:
    done = _check({**ZONE, "ROUTE_PATTERN": route})
    assert done.returncode != 0, route
    assert "route_pattern" in done.stdout


def test_a_route_needs_the_zone_and_the_exact_host_passes() -> None:
    assert _check({"ROUTE_PATTERN": f"{HOST}/e/*"}).returncode != 0
    assert _check({**ZONE, "ROUTE_PATTERN": f"{HOST}/e/*"}).returncode == 0
    assert _check({**ZONE, "ROUTE_PATTERN": f"{HOST}/x/*"}).returncode == 0


@pytest.mark.parametrize(
    "zone", ["other.test", "ample.test", "test.example", "evil.com"]
)
def test_the_zone_must_be_the_site_host_or_a_parent_of_it(zone: str) -> None:
    done = _check({"ZONE_NAME": zone})
    assert done.returncode != 0
    assert "ZONE_NAME" in done.stdout


@pytest.mark.parametrize("zone", ["example.test", "staging.example.test", "test"])
def test_a_parent_zone_or_the_host_itself_passes(zone: str) -> None:
    # "test" is only 4 characters, still a plain hostname of 3 or more
    assert _check({"ZONE_NAME": zone}).returncode == 0


@pytest.mark.parametrize(
    "env",
    [
        {"ZONE_NAME": "other.test"},
        {"ZONE_NAME": "example.test", "ROUTE_PATTERN": "*.example.test/e/*"},
        {"ZONE_NAME": "example.test", "ROUTE_PATTERN": "***/*"},
        {"ZONE_NAME": "example.test", "ROUTE_PATTERN": "victim.example.test/*"},
        {"ROUTE_PATTERN": "staging.example.test/e/*"},
    ],
)
def test_the_write_step_locks_the_zone_and_the_route_again(
    tmp_path: Path, env: dict[str, str]
) -> None:
    # The second lock: the write step refuses these on its own, with no
    # Check configuration step in front of it.
    done = render(tmp_path, {**STAGING_ENV, "ZONE_NAME": "", **env})
    assert done.returncode != 0
    assert (
        not (tmp_path / "wrangler.deploy.toml").exists() or "::error::" in done.stdout
    )


@pytest.mark.parametrize(
    "ns", ["01001", "001001", "01002", "01004", "02001", "0", "00", "1e3", " 1", "1\n2"]
)
def test_namespace_ids_have_no_leading_zero_and_so_compare_numerically(ns: str) -> None:
    # 01001 is the placeholder 1001 spelled with a zero: refused, as a shape.
    assert _check({"RATELIMIT_NAMESPACE_ID": ns}).returncode != 0
    assert (
        _check(
            {
                "PAGE_VIEWS": "true",
                "PAGE_IDS": '["home"]',
                "ZONE_NAME": "example.test",
                "VIEW_LIMITER_NAMESPACE_ID": ns,
            }
        ).returncode
        != 0
    )
    assert (
        _check({"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": ns}).returncode
        != 0
    )


def test_a_namespace_id_with_a_leading_zero_is_refused_by_the_write_step(
    tmp_path: Path,
) -> None:
    for name in ("RATELIMIT_NAMESPACE_ID",):
        done = render(tmp_path, {**STAGING_ENV, name: "03001"})
        assert done.returncode != 0
        assert "RATELIMIT_NAMESPACE_ID" in done.stdout


@pytest.mark.parametrize(
    "uuid",
    [
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee",  # one short
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeeee",  # one long
        "-" * 36,
        "aaaaaaaabbbbccccddddeeeeeeeeeeeeeeee",
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\nx",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeeg",
        "aaaa-aaaa-bbbb-cccc-dddd-eeeeeeeeeeee"[:36],
    ],
)
def test_the_d1_database_id_must_be_uuid_shaped(uuid: str) -> None:
    done = _check({"D1_DATABASE_ID": uuid})
    assert done.returncode != 0
    assert "D1_DATABASE_ID" in done.stdout


def test_the_write_step_locks_the_uuid_shape_again(tmp_path: Path) -> None:
    done = render(tmp_path, {**STAGING_ENV, "D1_DATABASE_ID": "-" * 36})
    assert done.returncode != 0
    assert "D1_DATABASE_ID" in done.stdout


def test_the_site_host_must_be_one_of_the_hosts_in_sites() -> None:
    other = '{"links":{"hosts":["other.example.test"],"mode":"beacon"}}'
    done = _check({"SITES": other})
    assert done.returncode != 0
    assert "SITES" in done.stdout
    assert _check({"SITES": '{"links":{"mode":"beacon"}}'}).returncode != 0
    both = f'{{"a":{{"hosts":["other.example.test"]}},"b":{{"hosts":["{HOST}"]}}}}'
    assert _check({"SITES": both}).returncode == 0


@pytest.mark.parametrize("value", ['["a"]\nzzz', 'zzz\n["a"]', '["A"]', "[]", '["a"] '])
def test_link_ids_is_tested_as_a_whole_value(value: str) -> None:
    done = _check({"LINK_IDS": value})
    assert done.returncode != 0
    assert "LINK_IDS" in done.stdout


@pytest.mark.parametrize("value", ['["a"]', '["a","b-1"]', '["a", "b"]'])
def test_a_good_link_ids_passes(value: str) -> None:
    assert _check({"LINK_IDS": value}).returncode == 0


def test_kit_kill_is_its_own_input_and_quiet_does_not_set_it(tmp_path: Path) -> None:
    env = step("Write deploy config")["env"]
    assert env["KIT_KILL"] == "${{ inputs.kit_kill }}"
    assert "quiet" not in env["KIT_KILL"]
    spec = call_spec()["inputs"]["kit_kill"]
    assert spec["type"] == "boolean" and spec["default"] is False
    done = render(tmp_path, {**STAGING_ENV, "KIT_KILL": "true"})
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'KIT_KILL = "1"' in active_lines(tmp_path / "wrangler.deploy.toml")
    done = render(tmp_path, {**STAGING_ENV, "KIT_KILL": "false"})
    assert 'KIT_KILL = "1"' not in active_lines(tmp_path / "wrangler.deploy.toml")


def test_a_route_pattern_overrides_the_default_route(tmp_path: Path) -> None:
    done = render(
        tmp_path, {**STAGING_ENV, "ROUTE_PATTERN": "staging.example.test/x/*"}
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = active_lines(tmp_path / "wrangler.deploy.toml")
    assert 'pattern = "staging.example.test/x/*"' in lines
    assert 'pattern = "staging.example.test/e/*"' not in lines


# ---- the engine pin -------------------------------------------------------------


ENGINE = "ChiefGyk3D/Reach-Around"
SHA = "0123456789abcdef" * 2 + "01234567"


def _pin(
    repo: str, ref: str, environment: str = "staging"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", step("Check the engine pin")["run"]],
        env={
            "PATH": "/usr/bin:/bin",
            "ENVIRONMENT": environment,
            "ENGINE_REPO": repo,
            "ENGINE_REF": ref,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "ref",
    ["main", "v0.2", "v0.2.0-rc1", "V0.2.0", "a" * 39, "A" * 40, "a" * 41, ""],
)
def test_the_engine_ref_must_be_a_commit_or_a_release_tag(ref: str) -> None:
    assert _pin(ENGINE, ref).returncode != 0


@pytest.mark.parametrize("ref", [SHA, "v0.2.0", "v10.20.30"])
def test_a_commit_or_release_tag_passes_the_shape_check(ref: str) -> None:
    assert _pin(ENGINE, ref).returncode == 0


@pytest.mark.parametrize(
    "repo",
    ["", "owner", "a/b/c", "o/n;x", "o/n n", "someone/Reach-Around", "ChiefGyk3D/fork"],
)
def test_only_the_engine_repository_is_accepted(repo: str) -> None:
    done = _pin(repo, SHA)
    assert done.returncode != 0
    assert "engine_repo" in done.stdout


@pytest.mark.parametrize(
    "environment",
    ["", "a", "Staging", "1abc", "-x", "a_b", "a" * 32, "a b", "ok\nbad", "a/b"],
)
def test_a_bad_environment_name_is_refused(environment: str) -> None:
    done = _pin(ENGINE, SHA, environment)
    assert done.returncode != 0
    assert "environment" in done.stdout


@pytest.mark.parametrize(
    "environment", ["test", "staging", "production", "a1", "a" * 31]
)
def test_a_plain_environment_name_passes(environment: str) -> None:
    assert _pin(ENGINE, SHA, environment).returncode == 0


def _compare(
    tmp_path: Path, reply: str, exit_code: int = 0, ref: str = SHA
) -> subprocess.CompletedProcess[str]:
    """Run the compare step with a fake `gh` that answers with `reply`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(
        '#!/bin/bash\necho "$@" > "$FAKE_GH_ARGS"\n'
        f"printf '%s\\n' '{reply}'\nexit {exit_code}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    out = tmp_path / "github-output"
    out.write_text("", encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", step("Check the engine ref is on main")["run"]],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_GH_ARGS": str(tmp_path / "args"),
            "GITHUB_OUTPUT": str(out),
            "GH_TOKEN": "fake",
            "ENGINE_REPO": ENGINE,
            "ENGINE_REF": ref,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("status", ["identical", "ahead"])
def test_a_ref_that_is_main_or_behind_main_passes_the_compare(
    tmp_path: Path, status: str
) -> None:
    done = _compare(tmp_path, f"{status} {SHA}")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "github-output").read_text() == f"engine_sha={SHA}\n"
    # base is the ref, head is main: `ahead` then means the ref is an ancestor
    args = (tmp_path / "args").read_text().split()
    assert args[:2] == ["api", f"repos/{ENGINE}/compare/{SHA}...main"]


@pytest.mark.parametrize("status", ["diverged", "behind", "", "Ahead", "ahead!"])
def test_a_ref_that_is_not_on_main_fails_the_compare(
    tmp_path: Path, status: str
) -> None:
    done = _compare(tmp_path, f"{status} {SHA}")
    assert done.returncode != 0
    assert "not on the engine's main" in done.stdout
    assert (tmp_path / "github-output").read_text() == ""


def test_a_failed_api_call_fails_closed(tmp_path: Path) -> None:
    # a ref that does not exist makes `gh api` exit non-zero
    done = _compare(tmp_path, "", exit_code=1)
    assert done.returncode != 0
    assert (tmp_path / "github-output").read_text() == ""


@pytest.mark.parametrize("sha", ["", "xyz", "A" * 40, "a" * 39])
def test_an_unresolvable_commit_fails_the_compare(tmp_path: Path, sha: str) -> None:
    done = _compare(tmp_path, f"ahead {sha}")
    assert done.returncode != 0
    assert (tmp_path / "github-output").read_text() == ""


def test_the_deploy_job_checks_out_the_commit_the_compare_resolved() -> None:
    data = load(CALLEE)
    checkout = next(
        s for s in callee_steps() if "actions/checkout@" in s.get("uses", "")
    )
    assert checkout["with"]["ref"] == "${{ needs.validate.outputs.engine_sha }}"
    # the repository is fixed too, not taken from an input
    assert checkout["with"]["repository"] == ENGINE
    assert data["jobs"]["deploy"]["needs"] == "validate"
    assert (
        data["jobs"]["validate"]["outputs"]["engine_sha"]
        == "${{ steps.compare.outputs.engine_sha }}"
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
    assert list(data["jobs"]) == ["validate", "deploy"]
    job = data["jobs"]["deploy"]
    # id-token only on the one job that fetches the credentials
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert any(s.get("id") == "cf_doppler" for s in job["steps"])
    assert job["if"] == "github.ref == 'refs/heads/main'"  # refuse any other ref
    assert job["environment"] == "${{ inputs.environment }}"  # the caller's gate
    assert "env" not in job  # no job-level env: the token is never job-wide
    assert "continue-on-error" not in CALLEE.read_text(encoding="utf-8")
    # the validate job: no environment, no id-token, nothing but a read-only token
    check = data["jobs"]["validate"]
    assert check["permissions"] == {"contents": "read"}
    assert "environment" not in check and "env" not in check
    assert check["if"] == "github.ref == 'refs/heads/main'"
    assert not any(s.get("id") == "cf_doppler" for s in check["steps"])
    assert load(CALLEE)["jobs"]["validate"]["steps"][0]["with"]["egress-policy"] == (
        "block"
    )


def test_one_deploy_per_worker_name_at_a_time() -> None:
    job = load(CALLEE)["jobs"]["deploy"]
    assert job["concurrency"] == {
        "group": "deploy-worker-${{ inputs.worker_name }}",
        "cancel-in-progress": False,
    }


def test_the_callee_reads_no_github_secret_and_takes_none() -> None:
    # comments aside: they may talk about Worker secrets
    text = "\n".join(
        ln
        for ln in CALLEE.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "secrets." not in text  # the Doppler output is `steps.<id>.outputs.secrets)`
    # require-token, read-live-version, migrate, deploy and rollback (issue #768)
    assert text.count("outputs.secrets") == 5, "the five token mappings"
    assert "secrets:" not in text
    assert "inherit" not in text
    spec = call_spec()
    assert "secrets" not in spec and "outputs" not in spec
    for name in spec["inputs"]:
        assert not re.search(r"token|secret|key|password", name), name
    # the one credential is the masked Doppler output, mapped into exactly the
    # steps that need it, never into GITHUB_ENV
    assert "GITHUB_ENV" not in text
    # GITHUB_OUTPUT is used once, by the validate job, for the engine commit
    assert text.count("GITHUB_OUTPUT") == 1
    deploy_text = yaml.safe_dump(load(CALLEE)["jobs"]["deploy"])
    assert "GITHUB_OUTPUT" not in deploy_text
    carriers = [
        s["name"] for s in callee_steps() if "CLOUDFLARE_API_TOKEN" in s.get("env", {})
    ]
    assert carriers == [
        "Require the Cloudflare token",
        "Read the live Worker version and deployment owners",
        "Apply D1 migrations",
        "Deploy the Worker",
        "Roll back the Worker after a failed probe",
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


# ---- no local call until the follow-up -------------------------------------------


def test_no_workflow_calls_a_reusable_workflow_by_a_local_path_yet() -> None:
    # `uses: ./...` fails zizmor 1.30.1 and `uses: $/...` fails actionlint
    # 1.7.12 (CI pins). The call is added in a follow-up as a commit-pinned
    # `uses: ChiefGyk3D/Reach-Around/.github/workflows/deploy-worker.yml@<sha>`.
    files = all_workflows()
    assert CALLER in files
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*uses:\s+(\S+)", line)
            if m:
                assert not m.group(1).startswith(("./", "$/")), (path.name, line)


def test_the_test_worker_name_is_the_one_in_wrangler_toml() -> None:
    # What deploy-test.yml will pass as worker_name once it calls the workflow.
    name = tomllib.loads((ROOT / "worker" / "wrangler.toml").read_text())["name"]
    assert name == "reach-around-clicks"


def test_the_inline_and_reusable_write_steps_have_the_same_script() -> None:
    inline = step("Write deploy config", CALLER)["run"]
    reusable = step("Write deploy config")["run"]
    # the reusable step only adds SITES, the route pattern and the kill switches
    for line in inline.splitlines():
        if "DESTINATION_KINDS" in line or "destination_kinds" in line:
            assert line in reusable.splitlines(), line


def test_the_docs_state_the_gating_and_kit_caveats_plainly() -> None:
    text = " ".join((ROOT / "docs" / "deploy.md").read_text(encoding="utf-8").split())
    for needle in (
        "must not run this from a `pull_request_target`",
        "CALLER's ref",
        "UNVERIFIED",
        "deployment branches limited to `main` and a required reviewer",
        ":environment:<exact environment>",
        "job_workflow_ref",
        "one identity per tier",
        "pass `kit_kill: true` on every call",
        "a fork keeps and calls its own copy",
        "cannot prove the environment exists",
    ):
        assert needle in text, needle


# ---- the deploy job keeps its hardening (reviewer reproduction: a sed on any of
# these used to leave every test green) ----------------------------------------


def test_the_deploy_job_keeps_its_hardening() -> None:
    jobs = load(CALLEE)["jobs"]
    for name in ("validate", "deploy"):
        first = jobs[name]["steps"][0]
        assert first["uses"].startswith("step-security/harden-runner@"), name
        assert first["with"]["egress-policy"] == "block", name
        hosts = str(first["with"]["allowed-endpoints"]).split()
        # The caller's own site host, for the post-deploy probe, is the one
        # expression allowed in the list (issue #768).
        probe_host = "${{inputs.site_host}}:443" if name == "deploy" else None
        assert hosts and all(
            re.fullmatch(r"[a-z0-9.-]+:443", h) or h == probe_host for h in hosts
        ), name
    install = step("Install Worker dependencies")
    assert install["run"].strip() == "npm ci --ignore-scripts"
    checkouts = [
        s
        for job in jobs.values()
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts, "no checkout found; the rule would be vacuous"
    for checkout in checkouts:
        assert checkout["with"]["persist-credentials"] is False


# ---- the second locks of the write step, reached with no check in front --------

PV = {
    "PAGE_VIEWS": "true",
    "PAGE_IDS": '["home"]',
    "ZONE_NAME": "example.test",
    "VIEW_LIMITER_NAMESPACE_ID": "3002",
}
MINT = {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": "3003"}


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ({**PV, "VIEW_LIMITER_NAMESPACE_ID": "03002"}, "VIEW_LIMITER_NAMESPACE_ID"),
        ({**PV, "VIEW_LIMITER_NAMESPACE_ID": "01002"}, "VIEW_LIMITER_NAMESPACE_ID"),
        ({**PV, "VIEW_LIMITER_NAMESPACE_ID": "3001"}, "VIEW_LIMITER_NAMESPACE_ID"),
        (MINT | {"MINT_LIMITER_NAMESPACE_ID": "03003"}, "MINT_LIMITER_NAMESPACE_ID"),
        (MINT | {"MINT_LIMITER_NAMESPACE_ID": "01004"}, "MINT_LIMITER_NAMESPACE_ID"),
        (MINT | {"MINT_LIMITER_NAMESPACE_ID": "3001"}, "MINT_LIMITER_NAMESPACE_ID"),
        (
            {**PV, **MINT, "MINT_LIMITER_NAMESPACE_ID": "3002"},
            "MINT_LIMITER_NAMESPACE_ID",
        ),
        ({"ZONE_NAME": "ample.test"}, "ZONE_NAME"),
        ({"ZONE_NAME": "evil-example.test"}, "ZONE_NAME"),
    ],
)
def test_the_write_step_refuses_these_on_its_own(
    tmp_path: Path, extra: dict[str, str], needle: str
) -> None:
    done = render(tmp_path, {**STAGING_ENV, **extra})
    assert done.returncode != 0
    assert needle in done.stdout


def test_the_write_step_second_locks_are_not_vacuous(tmp_path: Path) -> None:
    done = render(tmp_path, {**STAGING_ENV, **PV, **MINT})
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    "sites",
    [
        '{"links":{"hosts":{"k":"staging.example.test"}}}',
        '{"links":{"hosts":"staging.example.test"}}',
        '{"links":{"hosts":[["staging.example.test"]]}}',
    ],
)
def test_sites_hosts_must_be_an_array_of_the_site_host(sites: str) -> None:
    done = _check({"SITES": sites})
    assert done.returncode != 0
    assert "SITE_HOST" in done.stdout
    ok = '{"links":{"hosts":["staging.example.test"]}}'
    assert _check({"SITES": ok}).returncode == 0


def test_the_docs_say_how_concurrency_really_behaves() -> None:
    text = " ".join((ROOT / "docs" / "deploy.md").read_text(encoding="utf-8").split())
    assert "at most one pending run per group" in text
    assert "cancels an older pending one" in text
    assert "serialize their own calls" in text
    forks = " ".join(
        (ROOT / "docs" / "instance-repos.md").read_text(encoding="utf-8").split()
    )
    assert "calls its own copy of `deploy-worker.yml`" in forks


# ---- the docs example matches the instance template it is written for ----------

TEMPLATE = ROOT / "examples" / "instance-repo" / ".github" / "workflows" / "deploy.yml"


def _docs_example_with() -> dict[str, Any]:
    text = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")
    section = text[text.index("## Deploying the Worker from an instance repo") :]
    block = section[section.index("```yaml\n") + 8 :]
    block = block[: block.index("```")]
    data = yaml.safe_load(block)
    with_: dict[str, Any] = data["worker"]["with"]
    return with_


def _template_uploads() -> dict[str, str]:
    """upload-artifact name -> path, for every upload step of the template."""
    jobs: dict[str, Any] = load(TEMPLATE)["jobs"]
    return {
        str(s["with"]["name"]): str(s["with"]["path"])
        for job in jobs.values()
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    }


def test_the_docs_example_uses_the_names_the_template_uploads() -> None:
    example = _docs_example_with()
    uploads = _template_uploads()
    assert uploads, "no upload step found in the template; the check would be vacuous"
    # the artifacts exist in the template ...
    assert example["site_artifact"] in uploads
    assert example["manifest_artifact"] in uploads
    # ... and the manifest file is the one the template uploads into it
    assert uploads[example["manifest_artifact"]].endswith(
        "/" + example["manifest_file"]
    )
    # the example only names inputs the workflow has
    assert set(example) <= set(call_spec()["inputs"])


def test_the_docs_say_the_template_is_not_migrated_yet() -> None:
    text = " ".join((ROOT / "docs" / "deploy.md").read_text(encoding="utf-8").split())
    assert "#694" in text
    assert "does NOT get the OIDC-only reusable deploy" in text
