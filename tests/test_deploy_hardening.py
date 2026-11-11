# SPDX-License-Identifier: Apache-2.0
"""deploy-test.yml hardening (issue #206): egress control, hashed Python
dependencies, a safe manifest check, and the limit of what YAML tests can see."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-test.yml"
SCRIPT = ROOT / ".github" / "scripts" / "verify-manifest.py"
HARDEN = "step-security/harden-runner@46b06cea07f9e64621fdf066ba77ac6915163d43"
DOPPLER_STEP = "Fetch Cloudflare credentials from Doppler"


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def steps(job: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load()["jobs"][job]["steps"]
    return result


# ---- 1. egress control ----------------------------------------------------


@pytest.mark.parametrize("job", ["build", "deploy"])
def test_harden_runner_is_the_first_step_sha_pinned(job: str) -> None:
    first = steps(job)[0]
    assert re.fullmatch(
        r"step-security/harden-runner@[0-9a-f]{40}", first.get("uses", "")
    ), first
    assert first["uses"] == HARDEN


def test_the_deploy_job_blocks_egress_with_an_explicit_list() -> None:
    # The deploy job holds the Cloudflare token: it must stay in block mode.
    # Weakening it to audit must fail this test.
    first = steps("deploy")[0]
    assert first["with"]["egress-policy"] == "block"
    endpoints = str(first["with"]["allowed-endpoints"]).split()
    assert endpoints
    for endpoint in endpoints:
        # explicit host:port only, no wildcards
        assert re.fullmatch(r"[a-z0-9.-]+:443", endpoint), endpoint
    assert {
        "github.com:443",
        "api.github.com:443",
        "registry.npmjs.org:443",
        "api.doppler.com:443",
        "api.cloudflare.com:443",
    } <= set(endpoints)


def test_the_build_job_audits_egress_and_says_why() -> None:
    # Artifact upload goes to a blob host whose name varies per run and cannot
    # be listed without a wildcard, so block mode would stop every build. The
    # build job holds no secrets and installs from hashed lockfiles.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert steps("build")[0]["with"]["egress-policy"] == "audit"
    assert "*" not in str(steps("build")[0]["with"].get("allowed-endpoints", ""))
    head = text[: text.index("  deploy:")]
    flat = " ".join(head.replace("#", " ").split())
    assert "varies per run" in flat and "without a wildcard" in flat
    assert "no secrets" in flat and "hashed lockfiles" in flat


def test_the_allow_lists_are_documented_as_unmeasured() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "UNMEASURED" in text  # the deploy job's block-mode list
    docs = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")
    flat = " ".join(docs.split())
    assert "unmeasured" in flat.lower()
    assert "a blocked host is named in the log" in flat


@pytest.mark.parametrize("job", ["build", "deploy"])
def test_an_egress_canary_follows_the_harden_step(job: str) -> None:
    canary = steps(job)[1]
    assert canary.get("name") == "Egress canary"
    assert "secrets." not in yaml.safe_dump(canary)


# ---- 2. hashed Python dependencies ----------------------------------------


def test_every_pip_install_is_hashed_except_the_project_itself() -> None:
    installs: list[str] = []
    for job in ("build", "deploy"):
        for step in steps(job):
            for line in str(step.get("run", "")).splitlines():
                if re.search(r"\bpip3?\s+install\b", line):
                    installs.append(line.strip())
    assert installs, "no pip install found; the rule would be vacuous"
    own = [ln for ln in installs if "--no-deps" in ln]
    assert len(own) == 1 and re.search(r"--no-deps\b.*\s\.$", own[0]), own
    for line in installs:
        if line in own:
            continue
        assert "--require-hashes" in line, line
        assert re.search(r"-r\s+requirements(-build)?\.txt", line), line
    joined = "\n".join(installs)
    assert "requirements.txt" in joined and "requirements-build.txt" in joined
    # no bare `pip install .` that would resolve mutable ranges
    assert not re.search(r"pip3?\s+install\s+\.\s*$", joined, re.MULTILINE)


# ---- 3. manifest link ids -------------------------------------------------


def run_script(
    tmp_path: Path,
    links: Any,
    link_ids: str,
    retention: Any = 30,
    retention_var: str | None = None,
    output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    manifest = tmp_path / "site-out.manifest.json"
    body: dict[str, Any] = {"links": links}
    if retention is not MISSING:
        body["lead_retention_days"] = retention
    manifest.write_text(json.dumps(body), encoding="utf-8")
    env = {"LINK_IDS": link_ids, "PATH": "/usr/bin:/bin"}
    if retention_var is not None:
        env["LEAD_RETENTION_DAYS"] = retention_var
    if output is not None:
        env["GITHUB_OUTPUT"] = str(output)
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), str(manifest)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


MISSING = object()


def test_the_manifest_retention_is_written_as_a_step_output(tmp_path: Path) -> None:
    out = tmp_path / "gh-output"
    done = run_script(tmp_path, {"alpha": {}}, "", retention=45, output=out)
    assert done.returncode == 0, done.stderr
    assert "lead_retention_days=45\n" in out.read_text()


@pytest.mark.parametrize("var", ["45", "045"])
def test_a_variable_equal_to_the_config_passes(tmp_path: Path, var: str) -> None:
    done = run_script(tmp_path, {"alpha": {}}, "", retention=45, retention_var=var)
    if var == "45":
        assert done.returncode == 0, done.stdout
    else:  # not plain canonical digits: refused, never reinterpreted
        assert done.returncode == 1


def test_a_variable_that_differs_from_the_config_fails_naming_both(
    tmp_path: Path,
) -> None:
    done = run_script(tmp_path, {"alpha": {}}, "", retention=45, retention_var="90")
    assert done.returncode == 1
    out = done.stdout + done.stderr
    assert out.startswith("::error::") and len(out.splitlines()) == 1
    assert "LEAD_RETENTION_DAYS" in out and "privacy.lead_retention_days" in out
    assert "90" in out and "45" in out


def test_an_unset_or_empty_variable_is_not_a_mismatch(tmp_path: Path) -> None:
    assert run_script(tmp_path, {"alpha": {}}, "", retention=45).returncode == 0
    assert (
        run_script(tmp_path, {"alpha": {}}, "", retention=45, retention_var="")
    ).returncode == 0


@pytest.mark.parametrize("var", ["0", "366", "abc", "4 5", "-1", "45\n::error::x"])
def test_a_malformed_variable_is_refused_without_echo(tmp_path: Path, var: str) -> None:
    done = run_script(tmp_path, {"alpha": {}}, "", retention=45, retention_var=var)
    assert done.returncode == 1
    out = done.stdout + done.stderr
    assert out.startswith("::error::") and len(out.splitlines()) == 1
    assert "abc" not in out and "4 5" not in out


@pytest.mark.parametrize(
    "bad", [MISSING, 0, 366, -1, "30", True, False, 1.5, None, [30], 10**30]
)
def test_a_bad_manifest_retention_is_refused(tmp_path: Path, bad: Any) -> None:
    done = run_script(tmp_path, {"alpha": {}}, "", retention=bad)
    assert done.returncode == 1
    out = done.stdout + done.stderr
    assert out.startswith("::error::") and len(out.splitlines()) == 1
    assert "Traceback" not in done.stderr


def test_matching_ids_print_nothing(tmp_path: Path) -> None:
    done = run_script(tmp_path, {"alpha": {}, "beta": {}}, '["alpha","beta"]')
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


def test_mismatches_are_warnings(tmp_path: Path) -> None:
    done = run_script(tmp_path, {"alpha": {}, "gamma": {}}, '["alpha","beta"]')
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [
        "::warning::link id gamma is in the build but not in LINK_IDS",
        "::warning::LINK_IDS lists beta, which is not in the build",
    ]


HOSTILE = [
    "ok\n::set-output name=x::y",
    "ok\r::error::forged",
    "bad\x1b[31m",
    "tab\tid",
    "UPPER",
    "a" * 41,
    "",
    "trailing-newline\n",
    "unicode-é",
    "has space",
]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_manifest_id_is_refused_without_echo(
    tmp_path: Path, hostile: str
) -> None:
    done = run_script(tmp_path, {"alpha": {}, hostile: {}}, '["alpha"]')
    assert done.returncode == 1
    out = done.stdout + done.stderr
    assert out.count("::") == 2  # exactly one "::error::" command
    assert out.startswith("::error::")
    assert len(out.splitlines()) == 1
    for fragment in ("forged", "set-output", "x1b", "UPPER", "aaaa", "trailing"):
        assert fragment not in out
    assert hostile.strip() == "" or hostile.strip() not in out


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_link_ids_value_is_refused_without_echo(
    tmp_path: Path, hostile: str
) -> None:
    done = run_script(tmp_path, {"alpha": {}}, json.dumps(["alpha", hostile]))
    assert done.returncode == 1
    out = done.stdout + done.stderr
    assert out.startswith("::error::") and len(out.splitlines()) == 1
    assert "forged" not in out and "set-output" not in out


@pytest.mark.parametrize("links", [[1, 2], 2, None, "abc"])
def test_non_string_or_malformed_links_are_refused(tmp_path: Path, links: Any) -> None:
    done = run_script(tmp_path, links, '["alpha"]')
    assert done.returncode == 1
    assert (done.stdout + done.stderr).startswith("::error::")
    assert "Traceback" not in done.stderr


def test_the_workflow_step_calls_the_script_isolated() -> None:
    step = next(
        s for s in steps("deploy") if s.get("name") == "Verify the build manifest"
    )
    assert "python3 -I .github/scripts/verify-manifest.py" in step["run"]
    assert "<<" not in step["run"]  # no inline python any more
    assert SCRIPT.is_file()


# ---- 4. what these tests cannot see ---------------------------------------

ALLOWED_AFTER_DOPPLER = {
    "ChiefGyk3D/git-your-ship-together/.github/actions/doppler-secrets"
}
INSTALLERS = re.compile(
    r"\b(npm\s+(ci|install|i|add|update)|pnpm|yarn|pip3?\s+install|pipx|uv\s+(pip|sync|add)"
    r"|apt(-get)?\s+install|brew\s+install|gem\s+install|go\s+install|cargo\s+install)\b"
)


def violations_after_doppler(job_steps: list[dict[str, Any]]) -> list[str]:
    names = [s.get("name") for s in job_steps]
    start = names.index(DOPPLER_STEP) + 1
    bad: list[str] = []
    for step in job_steps[start:]:
        if (
            "uses" in step
            and str(step["uses"]).split("@")[0] not in ALLOWED_AFTER_DOPPLER
        ):
            bad.append(f"action {step['uses']}")
        if INSTALLERS.search(str(step.get("run", ""))):
            bad.append(f"install in {step.get('name')}")
    return bad


def test_nothing_installs_or_runs_another_action_after_the_doppler_step() -> None:
    """LIMIT OF THESE TESTS. The YAML `env:` blocks that the other deploy tests
    read are not the whole story: the Doppler action exports its whole config
    into the job environment (GITHUB_ENV), so the Cloudflare token reaches
    every later step and every action post-run hook whether or not a step
    lists it. A YAML test cannot prove that inherited-environment boundary.
    Closing it waits on ChiefGyk3D/git-your-ship-together#207. Until then this
    test only guards the order: after the Doppler step no step may install
    packages or run an action outside a short allow-list."""
    assert violations_after_doppler(steps("deploy")) == []


def test_the_after_doppler_guard_bites() -> None:
    names = [DOPPLER_STEP, "x", "y", "z"]
    hostile = [
        {
            "name": names[0],
            "uses": next(iter(ALLOWED_AFTER_DOPPLER)) + "@abc",
        },
        {"name": "x", "run": "npm ci --ignore-scripts"},
        {"name": "y", "uses": "actions/cache@abc"},
        {"name": "z", "run": "python -m pip install ."},
    ]
    assert violations_after_doppler(hostile) == [
        "install in x",
        "action actions/cache@abc",
        "install in z",
    ]
    clean = [hostile[0], {"name": "w", "run": "npx --no-install wrangler deploy"}]
    assert violations_after_doppler(clean) == []


# ---- 4. the deployed LINK_IDS is derived from the build (issue #319) --------


def run_derive(
    tmp_path: Path,
    links: Any,
    link_ids: str | None,
    destination_kinds: Any | None = None,
    keep_destination_kinds: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the script with a GITHUB_OUTPUT file; return the result and its text."""
    manifest = tmp_path / "site-out.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "links": links,
                "destination_kinds": destination_kinds
                if destination_kinds is not None
                else {name: "link" for name in links},
                "lead_retention_days": 30,
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "github-output"
    out.write_text("", encoding="utf-8")
    env = {"GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"}
    if link_ids is not None:
        env["LINK_IDS"] = link_ids
    done = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), str(manifest)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    text = out.read_text(encoding="utf-8")
    # The retention line is covered by its own tests; these look at link_ids.
    text = text.replace("lead_retention_days=30\n", "")
    if not keep_destination_kinds:
        text = re.sub(r"^destination_kinds=.*\n", "", text, flags=re.M)
    return done, text


def test_destination_kinds_are_validated_and_emitted(tmp_path: Path) -> None:
    links: dict[str, Any] = {"example-one": {}, "socials-example": {}}
    done, out = run_derive(
        tmp_path,
        links,
        None,
        {"example-one": "link", "socials-example": "social_icon"},
        True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        'destination_kinds={"example-one":"link","socials-example":"social_icon"}\n'
        in out
    )

    done, out = run_derive(
        tmp_path,
        links,
        None,
        {"example-one": "product", "socials-example": "link"},
        True,
    )
    assert done.returncode == 1
    assert "invalid destination_kinds" in done.stdout and out == ""


def test_the_derived_list_is_the_builds_ids_sorted_and_compact(tmp_path: Path) -> None:
    built: dict[str, Any] = {
        "example-stand": {},
        "home-page": {},
        "example-cable": {},
        "example-mic": {},
    }
    done, out = run_derive(tmp_path, built, None)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == ""  # an unset LINK_IDS is not a mismatch
    assert (
        out == 'link_ids=["example-cable","example-mic","example-stand","home-page"]\n'
    )
    assert json.loads(out.removeprefix("link_ids=")) == sorted(built)


def test_a_stale_link_ids_variable_warns_but_the_build_wins(tmp_path: Path) -> None:
    built: dict[str, Any] = {"example-mic": {}, "example-cable": {}}
    done, out = run_derive(tmp_path, built, '["example-mic","gone"]')
    assert done.returncode == 0
    assert done.stdout.splitlines() == [
        "::warning::link id example-cable is in the build but not in LINK_IDS",
        "::warning::LINK_IDS lists gone, which is not in the build",
    ]
    assert out == 'link_ids=["example-cable","example-mic"]\n'


def test_the_worker_cap_is_honored_and_in_step_with_the_worker(tmp_path: Path) -> None:
    source = (ROOT / "worker" / "src" / "sites.ts").read_text(encoding="utf-8")
    match = re.search(r"export const MAX_LINK_IDS = (\d+);", source)
    assert match, "the Worker cap was not found; the guard would be vacuous"
    cap = int(match.group(1))
    at_cap: dict[str, Any] = {f"link-{n}": {} for n in range(cap)}
    done, out = run_derive(tmp_path, at_cap, None)
    assert done.returncode == 0, done.stdout
    assert len(json.loads(out.removeprefix("link_ids="))) == cap
    over: dict[str, Any] = {f"link-{n}": {} for n in range(cap + 1)}
    done, out = run_derive(tmp_path, over, None)
    assert done.returncode == 1
    assert done.stdout.startswith("::error::") and out == ""


@pytest.mark.parametrize("links", [{}, []])
def test_an_empty_build_derives_nothing_and_fails(tmp_path: Path, links: Any) -> None:
    done, out = run_derive(tmp_path, links, None)
    assert done.returncode == 1
    assert done.stdout.startswith("::error::") and out == ""


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_id_never_reaches_the_derived_output(
    tmp_path: Path, hostile: str
) -> None:
    done, out = run_derive(tmp_path, {"alpha": {}, hostile: {}}, None)
    assert done.returncode == 1
    assert out == ""  # fail closed: nothing derived, nothing exported


def test_the_deploy_config_gets_the_derived_list_not_the_variable() -> None:
    manifest = next(
        s for s in steps("deploy") if s.get("name") == "Verify the build manifest"
    )
    assert manifest.get("id") == "manifest"
    write = next(s for s in steps("deploy") if s.get("name") == "Write deploy config")
    assert write["env"]["LINK_IDS"] == "${{ steps.manifest.outputs.link_ids }}"
    # and the variable is no longer required (it is a comparison only)
    check = next(s for s in steps("deploy") if s.get("name") == "Check configuration")
    assert "SITE_HOST; do" in check["run"] and "SITE_HOST LINK_IDS" not in check["run"]


# ---- owner alerts (#252): the manifest value is checked and emitted ---------


def _verify(tmp_path: Path, extra: dict[str, Any]) -> tuple[int, str, str]:
    manifest = tmp_path / "m.json"
    body = {"links": {"alpha": "https://example.com/a"}, "lead_retention_days": 30}
    body.update(extra)
    manifest.write_text(json.dumps(body), encoding="utf-8")
    out = tmp_path / "out.txt"
    done = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-I", str(SCRIPT), str(manifest)],
        env={"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(out)},
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        done.returncode,
        done.stderr + done.stdout,
        (out.read_text(encoding="utf-8") if out.exists() else ""),
    )


GOOD_ALERTS = json.dumps(
    {
        "channels": ["discord", "matrix"],
        "events": ["kit_unlocked"],
        "visitor_email": "full",
        "max_per_minute": 5,
        "matrix": {"allowed_hosts": ["matrix.example.org"]},
    }
)


def test_manifest_without_alerts_emits_none(tmp_path: Path) -> None:
    code, _, out = _verify(tmp_path, {})
    assert code == 0 and "alerts_config" not in out


def test_manifest_alerts_config_is_emitted_canonically(tmp_path: Path) -> None:
    code, _, out = _verify(tmp_path, {"alerts_config": GOOD_ALERTS})
    assert code == 0
    line = next(x for x in out.splitlines() if x.startswith("alerts_config="))
    assert json.loads(line.split("=", 1)[1]) == json.loads(GOOD_ALERTS)
    assert "\n" not in line.split("=", 1)[1] and " " not in line


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[]",
        json.dumps({"channels": ["slack"], "events": ["kit_unlocked"]}),
        json.dumps({"channels": ["discord"], "events": []}),
        json.dumps({"channels": ["discord"], "events": ["kit_unlocked"], "x": 1}),
        json.dumps(
            {
                "channels": ["matrix"],
                "events": ["kit_unlocked"],
                "visitor_email": "full",
                "max_per_minute": 5,
            }
        ),
        json.dumps(
            {
                "channels": ["discord"],
                "events": ["kit_unlocked"],
                "visitor_email": "full",
                "max_per_minute": 99,
            }
        ),
        "x" * 2000,
        5,
    ],
)
def test_manifest_bad_alerts_config_is_refused(tmp_path: Path, bad: Any) -> None:
    code, text, out = _verify(tmp_path, {"alerts_config": bad})
    assert code != 0 and "alerts_config" in text and "alerts_config=" not in out
