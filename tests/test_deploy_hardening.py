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
HARDEN = "step-security/harden-runner@ccd8616d44fd3846e67624a50d5aad6d37bf2d25"
DOPPLER_STEP = "Fetch Cloudflare credentials from Doppler"


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def steps(job: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = load()["jobs"][job]["steps"]
    return result


# ---- 1. egress control ----------------------------------------------------


@pytest.mark.parametrize("job", ["build", "deploy"])
def test_harden_runner_is_the_first_step_in_block_mode_sha_pinned(job: str) -> None:
    first = steps(job)[0]
    assert re.fullmatch(
        r"step-security/harden-runner@[0-9a-f]{40}", first.get("uses", "")
    ), first
    assert first["uses"] == HARDEN
    assert first["with"]["egress-policy"] == "block"
    endpoints = str(first["with"]["allowed-endpoints"]).split()
    assert endpoints
    for endpoint in endpoints:
        # explicit host:port only, no wildcards
        assert re.fullmatch(r"[a-z0-9.-]+:443", endpoint), endpoint


def test_the_allow_lists_cover_what_each_job_does() -> None:
    def hosts(job: str) -> set[str]:
        return set(str(steps(job)[0]["with"]["allowed-endpoints"]).split())

    assert {
        "pypi.org:443",
        "files.pythonhosted.org:443",
        "github.com:443",
        "api.github.com:443",
        "results-receiver.actions.githubusercontent.com:443",
    } <= hosts("build")
    assert {
        "github.com:443",
        "api.github.com:443",
        "registry.npmjs.org:443",
        "api.doppler.com:443",
        "api.cloudflare.com:443",
    } <= hosts("deploy")
    # the build job never talks to Cloudflare or Doppler
    assert not {h for h in hosts("build") if "cloudflare.com" in h or "doppler" in h}


def test_the_allow_lists_are_documented_as_unmeasured() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("UNMEASURED") >= 2  # one comment per job
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
    tmp_path: Path, links: Any, link_ids: str
) -> subprocess.CompletedProcess[str]:
    manifest = tmp_path / "site-out.manifest.json"
    manifest.write_text(json.dumps({"links": links}), encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), str(manifest)],
        env={"LINK_IDS": link_ids, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


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
