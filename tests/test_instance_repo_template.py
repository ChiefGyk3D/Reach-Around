# SPDX-License-Identifier: Apache-2.0
"""The private-instance template (examples/instance-repo): its config builds
and its workflows keep the hardening docs/instance-repos.md promises."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config import load_site_config
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "instance-repo"
WORKFLOWS = TEMPLATE / ".github" / "workflows"
BASH = "/bin/bash"

# A version comment: v1.2.3, or a pre-release v1.2.3-rc.4 (also -alpha, -beta,
# each with an optional .N). Anything else after the numbers is refused.
PINNED = re.compile(
    r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # "
    r"v\d+(\.\d+)*(-(rc\.\d+|alpha(\.\d+)?|beta(\.\d+)?))?$"
)
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S.*?)\s*$", re.MULTILINE)
CF_ENV_VAR = "CLOUDFLARE_API_TOKEN"
DIGEST_IMAGE = re.compile(r"^ENGINE_IMAGE=\S+:[\w.-]+@sha256:[0-9a-f]{64}$", re.M)


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


# The four deploy jobs of deploy.yml. Each worker job calls the engine's
# reusable workflow, each pages job publishes the site; each pair is one tier.
WORKER_JOBS = ("worker-staging", "worker-production")
PAGES_JOBS = ("pages-staging", "pages-production")
# In the order the file lists them: each tier's Worker, then its Pages job.
DEPLOY_JOBS = (
    "worker-staging",
    "pages-staging",
    "worker-production",
    "pages-production",
)
# The GitHub environment of a pages job. A worker job names its environment in
# `with:` (the called workflow holds it), so it carries none of its own.
ENVIRONMENT_OF = {"pages-staging": "staging", "pages-production": "production"}
CALLEE_PATH = "ChiefGyk3D/Reach-Around/.github/workflows/deploy-worker.yml"
CALLEE_FILE = ROOT / ".github" / "workflows" / "deploy-worker.yml"
DEPLOY_ID_TOKEN = {"contents": "read", "id-token": "write"}

# Every job holds contents: read, except these: the comment job is the only
# one allowed to write to a pull request, the gate needs nothing at all, and the
# four deploy jobs also hold id-token: write to exchange the OIDC token with the
# secret manager (nothing else in the template may).
JOB_PERMISSIONS: dict[str, dict[str, str]] = {
    **{name: DEPLOY_ID_TOKEN for name in DEPLOY_JOBS},
    "comment": {"contents": "read", "pull-requests": "write"},
    "ci-green": {},
    # Opens, updates and closes the one reminder issue; reads no key.
    "freshness": {"contents": "read", "issues": "write"},
    # Holds the API keys, so it can only read; it publishes nothing.
    "refresh": {"contents": "read"},
    # Pushes the counts/refresh branch and opens one pull request; no keys.
    "publish": {"contents": "write", "pull-requests": "write"},
    # Reads the pull request's labels and events and the workflow runs of its
    # head commit (the approval is bound to the head); never writes (issue #619).
    "history-guard": {
        "contents": "read",
        "pull-requests": "read",
        "actions": "read",
    },
}


def violations(text: str) -> list[str]:
    """Hardening problems in one workflow file's text. Empty means clean."""
    found: list[str] = []
    data: dict[Any, Any] = yaml.safe_load(text)
    # YAML 1.1 reads the key `on` as True.
    triggers = data.get("on", data.get(True))
    names = set(triggers) if isinstance(triggers, dict) else {triggers}
    for bad in ("pull_request_target", "workflow_run"):
        if bad in names:
            found.append(f"trigger {bad}")
    if data.get("permissions") != {"contents": "read"}:
        found.append("workflow permissions are not exactly contents: read")
    if "env" in data:
        found.append("workflow-level env")
    for line in USES_LINE.findall(text):
        if not PINNED.match(line):
            found.append(f"unpinned action: {line}")
    for job_name, job in data["jobs"].items():
        wanted = JOB_PERMISSIONS.get(job_name, {"contents": "read"})
        if job.get("permissions") != wanted:
            found.append(f"{job_name}: permissions are not exactly {wanted}")
        if "timeout-minutes" not in job and "uses" not in job:
            found.append(f"{job_name}: no timeout-minutes")
        if "continue-on-error" in yaml.safe_dump(job):
            found.append(f"{job_name}: continue-on-error")
        if "env" in job:
            found.append(f"{job_name}: job-level env")
        if job.get("environment") != ENVIRONMENT_OF.get(job_name):
            found.append(f"{job_name}: unexpected environment")
        for step in job.get("steps", []):
            label = f"{job_name}/{step.get('name', step.get('uses'))}"
            run = step.get("run", "")
            if "${{" in run:
                found.append(f"{label}: expression inside run")
            step_text = yaml.safe_dump(step)
            is_wrangler = "wrangler" in run and "--version" not in run
            token_step = (
                is_wrangler
                and job_name in PAGES_JOBS
                and step.get("name") == "Publish the site to Pages"
                and CF_ENV_VAR in step.get("env", {})
            )
            counts_step = (
                job_name == "refresh"
                and step.get("name") == "Refresh the counts"
                and CF_ENV_VAR not in step_text
            )
            # The Cloudflare token is never a GitHub secret now: it is the masked
            # output of the secret-manager step, mapped into the one publish step.
            if "secrets." in step_text and not counts_step:
                found.append(f"{label}: GitHub secret read")
            if CF_ENV_VAR in step_text and not token_step:
                found.append(f"{label}: token name outside a wrangler step")
    return found


def test_the_template_files_exist() -> None:
    for rel in (
        "README.md",
        "site.yaml",
        "engine.lock",
        "engine.release.json",
        "renovate.json",
        ".gitignore",
        "media/.gitkeep",
        "docs/EDITING.md",
        "scripts/deploy-local.sh",
        "scripts/validate.sh",
        "scripts/check-config.sh",
        "scripts/render-comment.py",
        "scripts/write-result.py",
        ".github/workflows/deploy.yml",
        ".github/workflows/preview.yml",
        ".github/workflows/refresh-counts.yml",
        "scripts/refresh-counts.sh",
        "scripts/validate-numbers.py",
        "scripts/verify-history.sh",
        "scripts/unmerged-history.sh",
        ".github/workflows/check-hand-kept-freshness.yml",
        "scripts/check-freshness.py",
        ".github/workflows/history-guard.yml",
        ".github/history-approvers",
        ".github/CODEOWNERS",  # example owners of the rewrite-deciding paths (#619)
        "scripts/history-guard.py",
        "scripts/verify-history-rewrite.sh",
    ):
        assert (TEMPLATE / rel).is_file(), rel


def test_the_template_config_builds_with_its_media_folder(tmp_path: Path) -> None:
    path = TEMPLATE / "site.yaml"
    config = load_site_config(path)
    out = tmp_path / "out"
    manifest = build_site(
        config,
        out,
        today=date(2027, 3, 10),
        protect=[path],
        assets_dir=path.parent,
    )
    assert (out / "index.html").is_file()
    assert manifest.links
    assert (TEMPLATE / "media").is_dir()


def test_the_template_config_names_the_schema() -> None:
    first = (TEMPLATE / "site.yaml").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# yaml-language-server: $schema=https://")


def test_all_workflows_are_collected() -> None:
    assert [p.name for p in workflow_files()] == [
        "check-hand-kept-freshness.yml",
        "deploy.yml",
        "history-guard.yml",
        "preview.yml",
        "refresh-counts.yml",
    ]


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_workflow_hardening(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8")) == []


TIER_DOPPLER_CONFIG = {"staging": "dev", "production": "prd"}
TIER_BRANCH = {"staging": "staging", "production": "main"}
# What a `with:` value of a worker call may be: a literal or an output of the
# validate job (the checked values). Never a repository variable read again,
# a secret, event data or an input of the run.
WITH_EXPRESSION = re.compile(r"^\$\{\{ needs\.validate\.outputs\.[a-z][a-z0-9_]* \}\}$")
# The values check-tiers.sh writes as step outputs; the deploy jobs read only these.
CHECKED_OUTPUTS = (
    "staging_worker_name",
    "production_worker_name",
    "staging_pages_project",
    "production_pages_project",
    "staging_d1_database_id",
    "production_d1_database_id",
    "staging_ratelimit_namespace_id",
    "production_ratelimit_namespace_id",
    "staging_site_host",
    "production_site_host",
    "staging_zone_name",
    "production_zone_name",
    "doppler_identity_id_staging",
    "doppler_identity_id_production",
    "cloudflare_account_id",
    "doppler_project",
    "allow_staging_deploy",
    "allow_production_deploy",
)
STAGING_SWITCH = "needs.validate.outputs.allow_staging_deploy"
PRODUCTION_SWITCH = "needs.validate.outputs.allow_production_deploy"


def _deploy_data() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(
        (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    )
    return data


UPSTREAM_REPO = "ChiefGyk3D/Reach-Around"


def test_deploy_workflow_shape() -> None:
    data = _deploy_data()
    dispatch = data[True]["workflow_dispatch"]
    assert set(data[True]) == {"push", "workflow_dispatch"}
    assert data[True]["push"] == {"branches": ["main"]}
    target = dispatch["inputs"]["target"]
    assert target["type"] == "choice"
    assert target["options"] == ["staging", "production"]
    assert target["default"] == "staging"
    assert set(dispatch["inputs"]) == {"target"}
    jobs = data["jobs"]
    assert list(jobs) == ["validate", *DEPLOY_JOBS]
    assert "environment" not in jobs["validate"]
    # the engine repository is a literal in the template
    assert set(jobs["validate"]["outputs"]) == {"engine_ref", *CHECKED_OUTPUTS}
    for tier in ("staging", "production"):
        worker = jobs[f"worker-{tier}"]
        pages = jobs[f"pages-{tier}"]
        # the worker call: the engine's reusable workflow, the tier's environment
        assert worker["uses"].startswith(CALLEE_PATH + "@")
        assert worker["needs"] == "validate"
        assert "environment" not in worker  # the called job holds it
        assert "secrets" not in worker
        call = worker["with"]
        assert call["environment"] == tier
        assert call["doppler_config"] == TIER_DOPPLER_CONFIG[tier]
        assert call["manifest_file"] == "manifest.json"
        assert call["manifest_artifact"] == "site-manifest"
        assert call["site_artifact"] == "site-out"
        assert call["engine_ref"] == "${{ needs.validate.outputs.engine_ref }}"
        assert call["engine_repo"] == UPSTREAM_REPO
        for key, value in call.items():
            if "${{" in str(value):
                assert WITH_EXPRESSION.match(str(value)), (tier, key, value)
        # the pages job: same environment, after the Worker
        assert pages["environment"] == tier
        assert pages["needs"] == ["validate", f"worker-{tier}"]
        steps = pages["steps"]
        with_token = [s["name"] for s in steps if CF_ENV_VAR in s.get("env", {})]
        assert with_token == ["Publish the site to Pages"]
        names = [s.get("name") for s in steps]
        fetch = next(i for i, s in enumerate(steps) if s.get("id") == "cf_doppler")
        publish = names.index("Publish the site to Pages")
        assert publish == len(steps) - 1 and fetch == publish - 1
        for before in ("Check configuration", "Install Worker dependencies"):
            assert names.index(before) < fetch
        smoke = steps[names.index("Confirm wrangler runs")]
        assert names.index("Confirm wrangler runs") < fetch
        assert "env" in smoke and CF_ENV_VAR not in smoke["env"]
        doppler = steps[fetch]
        assert doppler["uses"].startswith(
            "ChiefGyk3D/git-your-ship-together/.github/actions/doppler-secrets@"
        )
        assert doppler["with"]["export"] == "outputs"
        assert doppler["with"]["names"] == "CLOUDFLARE_KEY_VALUE"
        assert doppler["with"]["required"] == "true"
        assert doppler["with"]["config"] == TIER_DOPPLER_CONFIG[tier]
        identity = "DOPPLER_IDENTITY_ID_" + tier.upper()
        checked = f"${{{{ needs.validate.outputs.{identity.lower()} }}}}"
        assert doppler["with"]["identity-id"] == checked
        assert call["doppler_identity_id"] == checked
        publish_step = steps[publish]
        assert publish_step["env"][CF_ENV_VAR] == (
            "${{ fromJSON(steps.cf_doppler.outputs.secrets).CLOUDFLARE_KEY_VALUE }}"
        )
        assert f"--branch {TIER_BRANCH[tier]}" in publish_step["run"]
        assert "--commit-dirty=true" in publish_step["run"]


def test_preview_workflow_deploys_nothing() -> None:
    text = (WORKFLOWS / "preview.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert set(data[True]) == {"pull_request"}
    assert "wrangler" not in text
    assert data["jobs"]
    assert all("environment" not in job for job in data["jobs"].values())
    assert "secrets." not in text


PAGES_ENV = (
    "    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    environment: staging\n"
)


def test_the_guard_bites() -> None:
    good = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    assert PAGES_ENV in good
    assert violations(good) == []
    sha = re.search(r"actions/checkout@([0-9a-f]{40}) # v[\d.]+", good)
    assert sha
    install = "        run: npm ci --ignore-scripts"
    token_env = (
        f"        env:\n          {CF_ENV_VAR}: ${{{{ secrets.{CF_ENV_VAR} }}}}\n"
    )
    mutations = {
        "unpinned": good.replace(sha.group(0), "actions/checkout@main"),
        "no version comment": good.replace(
            f"@{sha.group(1)} # v7.0.1", f"@{sha.group(1)}"
        ),
        "pull_request_target": good.replace("push:", "pull_request_target:", 1),
        "write permission": good.replace(
            "    permissions:\n      contents: read\n    # No `environment`",
            "    permissions:\n      contents: write\n    # No `environment`",
        ),
        "token on install step": good.replace(install, token_env + install),
        "job env": good.replace(
            "    timeout-minutes: 15\n",
            "    timeout-minutes: 15\n    env:\n      X: y\n",
        ),
        "expression in run": good.replace(
            "run: scripts/check-repo.sh", "run: echo ${{ github.head_ref }}"
        ),
        "token from a GitHub secret": good.replace(
            "${{ fromJSON(steps.cf_doppler.outputs.secrets).CLOUDFLARE_KEY_VALUE }}",
            f"${{{{ secrets.{CF_ENV_VAR} }}}}",
        ),
        "token on the smoke test": good.replace(
            '          WRANGLER_SEND_METRICS: "false"\n'
            "        run: npx --no-install wrangler --version",
            f'          {CF_ENV_VAR}: x\n          WRANGLER_SEND_METRICS: "false"\n'
            "        run: npx --no-install wrangler --version",
        ),
        "wrong environment": good.replace(
            PAGES_ENV, PAGES_ENV.replace("staging", "production"), 1
        ),
        "environment dropped": good.replace(
            PAGES_ENV, PAGES_ENV.rsplit("    environment", 1)[0], 1
        ),
        "pre-release comment with a bad suffix": good.replace(
            "# v0.3.0-rc.6", "# v0.3.0-rc", 1
        ),
    }
    for label, mutated in mutations.items():
        assert mutated != good, label
        assert violations(mutated), label


# --- the deploy migration (issue #694): the pins, the permissions, the tiers ----


def deploy_text() -> str:
    return (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")


def lock_text() -> str:
    return (TEMPLATE / "engine.lock").read_text(encoding="utf-8")


def lock_pin(lock: str) -> tuple[str, str]:
    """The commit and the version comment of ENGINE_REF in engine.lock."""
    match = re.search(r"^ENGINE_REF=([0-9a-f]{40}) # (v\S+)$", lock, re.M)
    assert match, "engine.lock has no ENGINE_REF with a version comment"
    return match.group(1), match.group(2)


CALL_LINE = re.compile(
    r"^\s*uses:\s+" + re.escape(CALLEE_PATH) + r"@([0-9a-f]{40}) # (\S+)\s*$", re.M
)


def call_pin_problems(text: str, lock: str) -> list[str]:
    """The reusable call must be pinned to the commit engine.lock names."""
    ref, tag = lock_pin(lock)
    found: list[str] = []
    calls = CALL_LINE.findall(text)
    named = [ln for ln in text.splitlines() if CALLEE_PATH in ln and "uses:" in ln]
    if len(calls) != len(WORKER_JOBS) or len(named) != len(calls):
        found.append(f"expected {len(WORKER_JOBS)} pinned calls, found {len(calls)}")
    for sha, comment in calls:
        if sha != ref:
            found.append(f"call pinned to {sha}, engine.lock says {ref}")
        if comment != tag:
            found.append(f"call comment {comment}, engine.lock says {tag}")
    return found


def test_the_reusable_call_is_pinned_to_the_commit_in_engine_lock() -> None:
    assert call_pin_problems(deploy_text(), lock_text()) == []


def test_the_call_pin_guard_bites() -> None:
    good, lock = deploy_text(), lock_text()
    ref, tag = lock_pin(lock)
    assert call_pin_problems(good, lock) == []
    other = "0" * 40
    cases = {
        "one call moved": (good.replace(ref, other, 1), lock),
        "lock moved": (good, lock.replace(ref, other)),
        "branch instead of a commit": (
            good.replace(f"@{ref} # {tag}", "@main", 1),
            lock,
        ),
        "wrong comment": (
            good.replace(f"@{ref} # {tag}", f"@{ref} # v0.0.1", 1),
            lock,
        ),
        "no call at all": (good.replace(CALLEE_PATH, "ChiefGyk3D/x/y"), lock),
    }
    for label, (text, lock_used) in cases.items():
        assert (text, lock_used) != (good, lock), label
        assert call_pin_problems(text, lock_used), label


GYST_PIN = re.compile(r"ChiefGyk3D/git-your-ship-together/\S+@([0-9a-f]{40})\b")


def gyst_pin_problems(texts: dict[str, str]) -> list[str]:
    """Every pin of the shared workflows and actions must be one commit."""
    pins = {name: set(GYST_PIN.findall(text)) for name, text in texts.items()}
    found = {sha for shas in pins.values() for sha in shas}
    problems: list[str] = []
    if len(found) != 1:
        problems.append(f"expected one shared commit, found {sorted(found)}")
    if sum(1 for shas in pins.values() if shas) < 2:
        problems.append("fewer than two files use the shared repository")
    return problems


def workflow_texts() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in workflow_files()}


def test_all_shared_workflow_pins_are_one_commit() -> None:
    texts = workflow_texts()
    assert gyst_pin_problems(texts) == []
    # collected loosely: the doppler action in deploy.yml and the bash lint call
    # in preview.yml are both found, so the check is not vacuous
    assert GYST_PIN.search(texts["deploy.yml"])
    assert GYST_PIN.search(texts["preview.yml"])


def test_the_shared_pin_guard_bites() -> None:
    texts = workflow_texts()
    sha = GYST_PIN.search(texts["deploy.yml"])
    assert sha
    skewed = {
        **texts,
        "deploy.yml": texts["deploy.yml"].replace(sha.group(1), "1" * 40, 1),
    }
    assert skewed["deploy.yml"] != texts["deploy.yml"]
    assert gyst_pin_problems(skewed)
    assert gyst_pin_problems({"deploy.yml": texts["deploy.yml"]})  # one file only
    assert gyst_pin_problems({k: v.replace(sha.group(1), "") for k, v in texts.items()})


LEVEL = {"none": 0, "read": 1, "write": 2}


RECORDED = ROOT / "tests" / "fixtures" / "instance_template" / "callee_permissions.json"


def recorded_callee() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(RECORDED.read_text(encoding="utf-8"))
    return data


def permission_blocks(workflow: dict[Any, Any]) -> list[dict[str, str]]:
    """The top-level permissions of a workflow, then each job's."""
    blocks = [workflow.get("permissions", {})]
    blocks += [job.get("permissions", {}) for job in workflow["jobs"].values()]
    return blocks


def needs_of(workflow_text: str) -> dict[str, int]:
    """The most each permission is asked for anywhere in a workflow (its top level
    and every job)."""
    needs: dict[str, int] = {}
    for block in permission_blocks(yaml.safe_load(workflow_text)):
        for scope, level in block.items():
            needs[scope] = max(needs.get(scope, 0), LEVEL[level])
    return needs


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    assert git, "git is needed to read the pinned workflow"
    return subprocess.run(  # noqa: S603 - fixed argv, read-only except one fetch
        [git, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def pinned_callee_text(
    ref: str | None = None,
    env: Mapping[str, str] | None = None,
    fetch: bool = True,
) -> str:
    """The called workflow exactly as GitHub runs it for the template: the file at
    the commit in engine.lock, read from git (never the working-tree copy, never a
    recorded copy). If the commit is not in this clone, try to fetch it. If it is
    still missing, fail under CI (the check must not pass unchecked) and skip with
    a message elsewhere."""
    env = os.environ if env is None else env
    ref = ref or lock_pin(lock_text())[0]
    present = _git("cat-file", "-e", f"{ref}^{{commit}}").returncode == 0
    if not present and fetch:
        _git("fetch", "--depth=1", "origin", ref)
        present = _git("cat-file", "-e", f"{ref}^{{commit}}").returncode == 0
    if present:
        shown = _git("show", f"{ref}:.github/workflows/deploy-worker.yml")
        assert shown.returncode == 0, shown.stderr
        return shown.stdout
    message = (
        f"the engine commit {ref} from engine.lock is not in this checkout and "
        f"`git fetch --depth=1 origin {ref}` did not get it; fetch it (or use "
        "fetch-depth: 0) so the callee's permissions can be read from it"
    )
    if env.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def callee_needs() -> dict[str, int]:
    """What the called workflow, at the pinned commit, asks of its caller."""
    return needs_of(pinned_callee_text())


def permission_problems(text: str, needs: dict[str, int]) -> list[str]:
    """A caller job's permissions must cover what the called workflow asks for:
    GitHub checks it when the file is parsed, so a skipped job counts."""
    found: list[str] = []
    jobs = yaml.safe_load(text)["jobs"]
    calls = {
        n: j for n, j in jobs.items() if str(j.get("uses", "")).startswith(CALLEE_PATH)
    }
    if not calls:
        found.append("no call of the reusable workflow found")
    for name, job in calls.items():
        granted = job.get("permissions", {})
        for scope, level in needs.items():
            if LEVEL[granted.get(scope, "none")] < level:
                found.append(f"{name}: {scope} is below what the callee asks for")
    return found


def test_the_caller_permissions_cover_what_the_callee_asks_for() -> None:
    needs = callee_needs()
    # collected loosely, asserted strictly: the callee does ask for both
    assert needs.get("contents") == LEVEL["read"]
    assert needs.get("id-token") == LEVEL["write"]
    assert permission_problems(deploy_text(), needs) == []


def drop_first_id_token(text: str) -> str:
    """The text without its first `id-token: write` line (a comment may follow
    the permission on the same line)."""
    return re.sub(r"^ +id-token: write.*\n", "", text, count=1, flags=re.M)


def test_the_permission_guard_bites() -> None:
    good, needs = deploy_text(), callee_needs()
    no_id_token = drop_first_id_token(good)
    assert no_id_token != good
    assert permission_problems(no_id_token, needs)
    assert permission_problems(good, {**needs, "packages": LEVEL["write"]})
    assert permission_problems(good.replace(CALLEE_PATH, "x/y/z"), needs)


def secret_problems(text: str) -> list[str]:
    """No GitHub secret is read or passed in the deploy workflow."""
    code = code_only(text)
    found: list[str] = []
    if "secrets." in code:
        found.append("reads a secrets. value")
    if re.search(r"^\s*secrets:", code, re.M):
        found.append("passes secrets to a called workflow")
    if "inherit" in code:
        found.append("secrets: inherit")
    return found


def test_the_deploy_workflow_reads_no_github_secret() -> None:
    assert secret_problems(deploy_text()) == []


def test_the_secret_guard_bites() -> None:
    good = deploy_text()
    call = "    uses: " + CALLEE_PATH
    assert call in good
    mutations = {
        "inherit": good.replace(call, "    secrets: inherit\n" + call, 1),
        "named secret": good.replace(call, "    secrets:\n      X: y\n" + call, 1),
        "secrets context": good.replace(
            "node-version:",
            "x: ${{ secrets.CLOUDFLARE_API_TOKEN }}\n          node-version:",
            1,
        ),
    }
    for label, mutated in mutations.items():
        assert mutated != good, label
        assert secret_problems(mutated), label


def evaluate(expr: str, context: dict[str, str]) -> bool:
    """A small evaluator for the `if` expressions of deploy.yml: `==` and `!=`
    against a quoted text, joined by `&&` and `||`. Anything else (a function, a
    bracket) is refused, so a condition this cannot read fails the test."""

    def atom(text: str) -> bool:
        match = re.fullmatch(r"\s*([\w.]+)\s*(==|!=)\s*'([^']*)'\s*", text)
        if not match:
            raise ValueError(f"unsupported condition: {text!r}")
        return (context.get(match.group(1), "") == match.group(3)) == (
            match.group(2) == "=="
        )

    return any(all(atom(a) for a in part.split("&&")) for part in expr.split("||"))


def contexts(event: str) -> list[dict[str, str]]:
    """Every run the expression could see for one event name."""
    return [
        {
            "github.event_name": event,
            "github.ref": ref,
            "inputs.target": target,
            PRODUCTION_SWITCH: allow,
            STAGING_SWITCH: staging_allow,
        }
        for staging_allow in ("", "true", "false", "TRUE")
        for ref in ("refs/heads/main", "refs/heads/other", "refs/tags/v1")
        for target in ("", "staging", "production")
        for allow in ("", "true", "false", "TRUE")
    ]


def tier_problems(text: str) -> list[str]:
    jobs = yaml.safe_load(text)["jobs"]
    found: list[str] = []
    for name in ("worker-production", "pages-production"):
        condition = str(jobs[name].get("if", ""))
        if not condition:
            found.append(f"{name}: no condition")
            continue
        try:
            evaluate(condition, {})
        except ValueError:
            found.append(f"{name}: a condition this check cannot read")
            continue
        if any(evaluate(condition, c) for c in contexts("push")):
            found.append(f"{name}: can run on a push")
        on = {
            "github.event_name": "workflow_dispatch",
            "github.ref": "refs/heads/main",
            "inputs.target": "production",
            PRODUCTION_SWITCH: "true",
        }
        if not evaluate(condition, on):
            found.append(f"{name}: never runs, even when asked for")
        # each of the four conditions is needed on its own
        for key, other in (
            ("github.ref", "refs/heads/other"),
            ("github.event_name", "push"),
            ("inputs.target", "staging"),
            (PRODUCTION_SWITCH, ""),
        ):
            if evaluate(condition, {**on, key: other}):
                found.append(f"{name}: runs without {key}")
    for name in ("worker-staging", "pages-staging"):
        # The one place staging may name production: the Pages job compares its
        # project with the production project and refuses to publish into it.
        job_text = yaml.safe_dump(jobs[name]).replace("PRODUCTION_PAGES_PROJECT", "")
        job_text = job_text.replace("production_pages_project", "")
        if "production" in job_text.lower():
            found.append(f"{name}: names production")
        condition = str(jobs[name].get("if", ""))
        try:
            evaluate(condition, {})
        except ValueError:
            found.append(f"{name}: a condition this check cannot read")
            continue
        for ctx in contexts("push") + contexts("workflow_dispatch"):
            allowed = ctx[STAGING_SWITCH] == "true"
            if evaluate(condition, ctx) and not allowed:
                found.append(f"{name}: runs without ALLOW_STAGING_DEPLOY")
                break
        for ctx in contexts("pull_request"):
            if evaluate(condition, ctx):
                found.append(f"{name}: can run on a pull request")
                break
    return found


def test_production_never_runs_on_a_push_and_staging_never_names_it() -> None:
    assert tier_problems(deploy_text()) == []
    jobs = yaml.safe_load(deploy_text())["jobs"]
    # controls: staging does run on a push to main and on a staging run
    push = {
        "github.event_name": "push",
        "github.ref": "refs/heads/main",
        STAGING_SWITCH: "true",
    }
    assert evaluate(str(jobs["worker-staging"]["if"]), push)
    # but not until the repository variable says the environments are ready
    assert not evaluate(str(jobs["worker-staging"]["if"]), {**push, STAGING_SWITCH: ""})
    assert evaluate(
        str(jobs["pages-staging"]["if"]),
        {
            "github.event_name": "workflow_dispatch",
            "github.ref": "refs/heads/main",
            "inputs.target": "staging",
            STAGING_SWITCH: "true",
        },
    )


def test_the_tier_guard_bites() -> None:
    good = deploy_text()
    jobs = yaml.safe_load(good)["jobs"]
    cond = str(jobs["worker-production"]["if"])
    assert "github.event_name == 'workflow_dispatch'" in cond
    parts = [p.strip() for p in cond.split("&&")]
    assert len(parts) == 4, parts
    for index, part in enumerate(parts):
        rest = " && ".join(p for i, p in enumerate(parts) if i != index)
        mutated = good.replace(cond, rest)
        assert mutated != good, part
        assert tier_problems(mutated), part
    assert tier_problems(good.replace(cond, f"{cond} || github.event_name == 'push'"))
    assert tier_problems(good.replace(cond, "contains(github.ref, 'main')"))
    # the staging condition is folded over two lines in the file
    first = (
        "      github.ref == 'refs/heads/main'"
        " && needs.validate.outputs.allow_staging_deploy == 'true'"
        " && github.event_name == 'push'\n"
    )
    assert first in good
    assert tier_problems(good.replace(first, "      github.event_name != 'x'\n"))
    # without the staging switch a push would start the first, unprotected run
    no_switch = first.replace(
        " && needs.validate.outputs.allow_staging_deploy == 'true'", ""
    )
    assert tier_problems(good.replace(first, no_switch))
    # naming the production project anywhere but the one refusal is caught
    assert tier_problems(
        good.replace(
            "${{ needs.validate.outputs.staging_pages_project }}\n          # Only",
            "${{ needs.validate.outputs.production_worker_name }}\n          # Only",
            1,
        )
    )
    assert tier_problems(
        good.replace(
            "worker_name: ${{ needs.validate.outputs.staging_worker_name }}",
            "worker_name: ${{ needs.validate.outputs.production_worker_name }}",
            1,
        )
    )
    assert evaluate("a.b == 'x' && c.d != 'y'", {"a.b": "x", "c.d": "z"})
    with pytest.raises(ValueError):
        evaluate("contains(a, 'b')", {})


def id_token_problems(text: str) -> list[str]:
    """id-token: write only on the four deploy jobs, and on all four."""
    data = yaml.safe_load(text)
    found: list[str] = []
    if "id-token" in (data.get("permissions") or {}):
        found.append("workflow-level id-token")
    for name, job in data["jobs"].items():
        has = (job.get("permissions") or {}).get("id-token") == "write"
        if name in DEPLOY_JOBS and not has:
            found.append(f"{name}: has no id-token: write")
        if name not in DEPLOY_JOBS and "id-token" in (job.get("permissions") or {}):
            found.append(f"{name}: id-token outside the deploy jobs")
    return found


def test_id_token_write_is_only_on_the_four_deploy_jobs() -> None:
    texts = workflow_texts()
    assert id_token_problems(texts["deploy.yml"]) == []
    for name in ("preview.yml", "refresh-counts.yml"):
        assert "id-token" not in texts[name], name
    jobs = yaml.safe_load(texts["deploy.yml"])["jobs"]
    holders = [n for n, j in jobs.items() if "id-token" in (j.get("permissions") or {})]
    assert holders == list(DEPLOY_JOBS)


def test_the_id_token_guard_bites() -> None:
    good = deploy_text()
    on_validate = good.replace(
        "    permissions:\n      contents: read\n    # No `environment`",
        "    permissions:\n      contents: read\n      id-token: write\n"
        "    # No `environment`",
        1,
    )
    assert on_validate != good
    assert id_token_problems(on_validate)
    assert id_token_problems(
        good.replace(
            "permissions:\n  contents: read\n",
            "permissions:\n  contents: read\n  id-token: write\n",
            1,
        )
    )
    assert drop_first_id_token(good) != good
    assert id_token_problems(drop_first_id_token(good))


def engine_group_wins(renovate: str) -> bool:
    """The engine group rule must come after the Actions group rule, so the two
    reusable-call pins move in the same pull request as engine.lock."""
    rules = json.loads(renovate)["packageRules"]
    engine = [
        i for i, r in enumerate(rules) if r.get("groupName") == "reach-around engine"
    ]
    actions = [i for i, r in enumerate(rules) if r.get("groupName") == "github actions"]
    return bool(engine) and bool(actions) and min(engine) > max(actions)


def test_renovate_moves_the_call_pins_with_engine_lock() -> None:
    text = (TEMPLATE / "renovate.json").read_text(encoding="utf-8")
    assert engine_group_wins(text)
    swapped = json.loads(text)
    swapped["packageRules"].reverse()
    assert not engine_group_wins(json.dumps(swapped))


# --- Daybreak round 1 findings (issue #694) -----------------------------------


def _step(job: str, prefix: str, text: str | None = None) -> dict[str, Any]:
    jobs = yaml.safe_load(text or deploy_text())["jobs"]
    steps = [s for s in jobs[job]["steps"] if s.get("name", "").startswith(prefix)]
    assert len(steps) == 1, (job, prefix)
    found: dict[str, Any] = steps[0]
    return found


def _shell(
    step: dict[str, Any], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a step's own script with only the given values in its environment."""
    assert set(env) <= set(step.get("env", {})), "the step does not read those"
    return subprocess.run(  # noqa: S603 - the template's own step, fixed argv
        [BASH, "-c", step["run"]],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], **env},
    )


NAMES = {
    "STAGING_WORKER_NAME": "my-links-clicks-staging",
    "PRODUCTION_WORKER_NAME": "my-links-clicks",
    "DOPPLER_PROJECT": "my-links",
    "STAGING_PAGES_PROJECT": "my-links-staging",
    "PRODUCTION_PAGES_PROJECT": "my-links",
}
NAMES_STEP = "Check the plain configuration values"


def test_validate_checks_the_plain_names_and_keeps_the_pages_projects_apart() -> None:
    step = _step("validate", NAMES_STEP)
    assert _shell(step, NAMES).returncode == 0
    # staging only: production not set up yet
    staging_only = {
        **NAMES,
        "PRODUCTION_WORKER_NAME": "",
        "PRODUCTION_PAGES_PROJECT": "",
    }
    assert _shell(step, staging_only).returncode == 0
    # names are lowercased before they are checked and compared
    mixed = {**NAMES, "PRODUCTION_PAGES_PROJECT": "My-Links"}
    assert _shell(step, mixed).returncode == 0
    bad = {
        "same pages project": {"PRODUCTION_PAGES_PROJECT": "my-links-staging"},
        "same after lowercasing": {
            "STAGING_PAGES_PROJECT": "My-Links-Staging",
            "PRODUCTION_PAGES_PROJECT": "my-links-staging",
        },
        "second line": {"STAGING_PAGES_PROJECT": "my-links-staging\nmy-links"},
        "trailing newline": {"PRODUCTION_PAGES_PROJECT": "my-links\n"},
        "not plain": {"STAGING_PAGES_PROJECT": "my links"},
        "slash": {"PRODUCTION_PAGES_PROJECT": "a/b"},
        "staging pages unset": {"STAGING_PAGES_PROJECT": ""},
        "same worker": {"PRODUCTION_WORKER_NAME": "my-links-clicks-staging"},
        "test worker": {"STAGING_WORKER_NAME": "reach-around-clicks"},
    }
    for label, change in bad.items():
        result = _shell(step, {**NAMES, **change})
        assert result.returncode != 0, label
        assert "::error::" in result.stdout, label


def test_the_staging_pages_job_refuses_the_production_project() -> None:
    step = _step("pages-staging", "Check configuration")
    env = {
        "CLOUDFLARE_ACCOUNT_ID": "0" * 32,
        "PAGES_PROJECT": "my-links-staging",
        "PRODUCTION_PAGES_PROJECT": "my-links",
    }
    assert _shell(step, env).returncode == 0
    assert _shell(step, {**env, "PRODUCTION_PAGES_PROJECT": ""}).returncode == 0
    for label, change in {
        "same": {"PRODUCTION_PAGES_PROJECT": "my-links-staging"},
        "same, other case": {
            "PAGES_PROJECT": "My-Links",
            "PRODUCTION_PAGES_PROJECT": "my-links",
        },
        "newline": {"PAGES_PROJECT": "my-links-staging\nx"},
    }.items():
        assert _shell(step, {**env, **change}).returncode != 0, label


def test_the_production_pages_job_does_not_read_the_staging_project() -> None:
    assert "STAGING" not in yaml.safe_dump(
        yaml.safe_load(deploy_text())["jobs"]["pages-production"]
    )


def readme_text() -> str:
    return (TEMPLATE / "README.md").read_text(encoding="utf-8")


def setup_order_problems(text: str) -> list[str]:
    """The environments and their protection come before any variable that lets a
    run start, and nothing says to push first."""
    found: list[str] = []
    marks = [
        "**Settings > Environments > New environment**",
        "Required reviewers",
        "Deployment branches",
        "**repository variables**",
        "| `ALLOW_STAGING_DEPLOY` |",  # the table row that switches staging on
    ]
    at = [text.find(m) for m in marks]
    if min(at) < 0:
        return [f"missing: {m}" for m, i in zip(marks, at, strict=True) if i < 0]
    if at != sorted(at):
        found.append("the setup steps are out of order")
    # the enabling row is the last variable of the table
    rows = [ln for ln in text.splitlines() if ln.startswith("| `") and "|" in ln[3:]]
    names = [ln.split("`")[1] for ln in rows]
    for must in ("ALLOW_STAGING_DEPLOY", "ALLOW_PRODUCTION_DEPLOY"):
        if must not in names:
            found.append(f"{must} is not in the variable table")
    if "ALLOW_STAGING_DEPLOY" in names:
        idx = names.index("ALLOW_STAGING_DEPLOY")
        tail = names[idx + 1 :]
        if any(
            n.startswith("STAGING_") or n == "DOPPLER_IDENTITY_ID_STAGING" for n in tail
        ):
            found.append("a staging variable comes after the enabling one")
    if "Do not push" not in text:
        found.append("nothing tells the reader not to push before setup is done")
    return found


def test_the_readme_creates_and_protects_the_environments_before_anything_can_run() -> (
    None
):
    assert setup_order_problems(readme_text()) == []
    docs = " ".join(
        (ROOT / "docs" / "instance-repos.md").read_text(encoding="utf-8").split()
    )
    assert "ALLOW_STAGING_DEPLOY" in docs
    assert "before the first push" in docs


def test_the_setup_order_guard_bites() -> None:
    good = readme_text()
    assert setup_order_problems(good) == []
    env = "**Settings > Environments > New environment**"
    moved = good.replace(env, "X", 1) + "\n" + env
    assert setup_order_problems(moved)
    assert setup_order_problems(good.replace("Do not push", "Push"))
    assert setup_order_problems(good.replace("ALLOW_STAGING_DEPLOY", "ALLOW_X"))
    lines = good.splitlines()
    first_row = next(ln for ln in lines if ln.startswith("| `STAGING_WORKER_NAME`"))
    row = next(ln for ln in lines if ln.startswith("| `ALLOW_STAGING_DEPLOY`"))
    # the enabling variable set before the staging values it enables
    early = good.replace(row + "\n", "").replace(first_row, row + "\n" + first_row)
    assert early != good
    assert setup_order_problems(early)


PROTECTION_PHRASES = (
    "Prevent self-review",
    "sole owner",
    "second human",
    "do not establish that the approver did not author",
    "protected-branch pull-request review",
    "confirmation pause",
    "not an approval control",
)
# A second account is not a second person: never offered as independent control.
PROTECTION_FORBIDDEN = (
    "second account",
    "another account",
    "(not the author)",
    "Add yourself (or a second person)",
)


def protection_problems(text: str) -> list[str]:
    """What a document says about environment protection and the sole owner."""
    flat = " ".join(text.split())
    found = [f"missing: {n}" for n in PROTECTION_PHRASES if n not in flat]
    found += [f"must not say: {n}" for n in PROTECTION_FORBIDDEN if n in flat]
    return found


def test_the_readme_does_not_promise_more_than_environment_protection_gives() -> None:
    assert protection_problems(readme_text()) == []
    docs = (ROOT / "docs" / "instance-repos.md").read_text(encoding="utf-8")
    flat = " ".join(docs.split())
    # the docs page carries the same limits, in its own words
    for needed in (
        "Prevent self-review",
        "second human",
        "do not establish that the approver did not author",
        "protected-branch pull-request review",
    ):
        assert needed in flat, needed
    for banned in PROTECTION_FORBIDDEN:
        assert banned not in flat, banned


def test_the_protection_guard_bites() -> None:
    # the phrases may wrap across lines in the file, so mutate the flat text
    good = " ".join(readme_text().split())
    assert protection_problems(good) == []
    for phrase in PROTECTION_PHRASES:
        assert phrase in good
        assert protection_problems(good.replace(phrase, "x")), phrase
    for banned in PROTECTION_FORBIDDEN:
        assert protection_problems(good + " " + banned), banned


def review_aid_problems(recorded: dict[str, Any], lock: str, callee: str) -> list[str]:
    """The committed fixture is a review aid only: it must name the pinned commit
    and say what the pinned workflow says. It is never what the tests trust."""
    found: list[str] = []
    ref, _ = lock_pin(lock)
    if recorded["engine_ref"] != ref:
        found.append("the review aid is for another commit than engine.lock")
    if permission_blocks(yaml.safe_load(callee)) != recorded["permissions"]:
        found.append("the review aid differs from the pinned workflow")
    return found


def test_the_review_aid_equals_the_permissions_of_the_pinned_workflow() -> None:
    recorded = recorded_callee()
    assert recorded["permissions"], "nothing recorded; the check would be vacuous"
    assert review_aid_problems(recorded, lock_text(), pinned_callee_text()) == []


def test_the_review_aid_guard_bites() -> None:
    recorded, lock, callee = recorded_callee(), lock_text(), pinned_callee_text()
    ref, _ = lock_pin(lock)
    assert review_aid_problems({**recorded, "engine_ref": "0" * 40}, lock, callee)
    assert review_aid_problems(recorded, lock.replace(ref, "1" * 40), callee)
    fewer = {**recorded, "permissions": recorded["permissions"][:-1]}
    assert review_aid_problems(fewer, lock, callee)
    widened = callee.replace(
        "permissions:\n  contents: read\n",
        "permissions:\n  contents: read\n  packages: read\n",
        1,
    )
    assert widened != callee
    assert review_aid_problems(recorded, lock, widened)


SYNTHETIC_CALLEE = (
    "permissions:\n  contents: read\n"
    "jobs:\n  deploy:\n    permissions:\n      contents: read\n"
    "      id-token: write\n      packages: read\n"
)


def test_a_newly_required_callee_permission_fails_the_compatibility_check() -> None:
    # a callee that starts to ask for packages: read, which the caller does not give
    needs = needs_of(SYNTHETIC_CALLEE)
    assert needs["packages"] == LEVEL["read"]
    problems = permission_problems(deploy_text(), needs)
    assert any("packages" in p for p in problems)
    # and the real pinned callee asks for nothing the caller lacks
    assert permission_problems(deploy_text(), callee_needs()) == []
    # a caller that grants it passes
    granted = deploy_text().replace(
        "      id-token: write", "      packages: read\n      id-token: write"
    )
    assert permission_problems(granted, needs) == []


def test_a_missing_pinned_commit_fails_under_ci_and_skips_elsewhere() -> None:
    absent = "0" * 40
    with pytest.raises(pytest.fail.Exception, match="not in this checkout"):
        pinned_callee_text(absent, {"CI": "true"}, fetch=False)
    with pytest.raises(pytest.skip.Exception, match="fetch"):
        pinned_callee_text(absent, {}, fetch=False)
    # present: read from git, not from the working tree
    ref, _ = lock_pin(lock_text())
    text = pinned_callee_text(ref, {"CI": "true"}, fetch=False)
    assert yaml.safe_load(text)["name"] == "Deploy Worker"


def test_a_workflow_pin_comment_accepts_a_pre_release_and_refuses_a_bad_suffix() -> (
    None
):
    sha = "a" * 40
    for ok in ("v1.2.3", "v0.3.0-rc.6", "v1.0.0-alpha", "v1.0.0-beta.2", "v7"):
        assert PINNED.match(f"x/y@{sha} # {ok}"), ok
    for bad in (
        "v0.3.0-rc",
        "v0.3.0-rc.x",
        "v0.3.0-gamma",
        "v0.3.0-rc.6x",
        "0.3.0",
        "v1.2.3 x",
    ):
        assert not PINNED.match(f"x/y@{sha} # {bad}"), bad


def test_the_engine_is_pinned_by_digest() -> None:
    lock = (TEMPLATE / "engine.lock").read_text(encoding="utf-8")
    assert DIGEST_IMAGE.search(lock)
    assert re.search(r"^ENGINE_REF=[0-9a-f]{40} # v\d", lock, re.M)
    assert not DIGEST_IMAGE.search(lock.replace("sha256:", "sha1:"))


def _copy_template(tmp_path: Path) -> Path:
    dest = tmp_path / "inst"
    shutil.copytree(TEMPLATE, dest)
    return dest


def _script(root: Path, name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, a copy of the template
        [BASH, str(root / "scripts" / name), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_read_lock_accepts_the_shipped_pins(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    for key in ("ENGINE_IMAGE", "ENGINE_REPO", "ENGINE_REF"):
        result = _script(root, "read-lock.sh", key)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip()


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("@sha256:", "@sha1:", "ENGINE_IMAGE"),
        ("<current ENGINE_REF commit>", "main", "ENGINE_REF"),
        ("ENGINE_REPO=ChiefGyk3D/Reach-Around", "ENGINE_REPO=a;b", "ENGINE_REPO"),
    ],
)
def test_read_lock_refuses_bad_shapes(
    tmp_path: Path, old: str, new: str, key: str
) -> None:
    root = _copy_template(tmp_path)
    lock = root / "engine.lock"
    text = lock.read_text(encoding="utf-8")
    if old == "<current ENGINE_REF commit>":
        # Read the pinned commit from the lock itself so an engine bump never
        # breaks this test.
        match = re.search(r"^ENGINE_REF=([0-9a-f]{40})", text, re.MULTILINE)
        assert match is not None
        old = match.group(1)
    assert old in text
    lock.write_text(text.replace(old, new), encoding="utf-8")
    assert _script(root, "read-lock.sh", key).returncode != 0


def test_check_repo_flags_forbidden_files(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    clean = _script(root, "check-repo.sh")
    assert clean.returncode == 0, clean.stderr
    (root / ".env").write_text("X=1\n", encoding="utf-8")
    dirty = _script(root, "check-repo.sh")
    assert dirty.returncode == 1
    assert ".env" in dirty.stderr


# --- the manifest never sits in the published directory ----------------------


def _deploy_jobs() -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
    jobs: dict[str, Any] = data["jobs"]
    return jobs


def _template_texts() -> dict[str, str]:
    paths = [*workflow_files(), *sorted((TEMPLATE / "scripts").glob("*.sh"))]
    return {p.name: p.read_text(encoding="utf-8") for p in paths}


def manifest_violations(text: str) -> list[str]:
    """Problems with how deploy.yml handles the build manifest."""
    found: list[str] = []
    data = yaml.safe_load(text)
    for name, job in data["jobs"].items():
        uploads: dict[str, str] = {}
        # a job that calls a reusable workflow has no steps
        for step in job.get("steps", []):
            label = f"{name}/{step.get('name', step.get('uses'))}"
            run = step.get("run", "")
            if "site-out/manifest" in run or "site-out/manifest" in str(
                step.get("with", "")
            ):
                found.append(f"{label}: manifest path inside the output directory")
            if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                uploads[step["with"]["name"]] = str(step["with"]["path"])
        for art, path in uploads.items():
            if "manifest" in path and art != "site-manifest":
                found.append(f"{name}: manifest uploaded in artifact {art}")
    return found


def test_no_template_file_reads_a_manifest_inside_the_output() -> None:
    texts = _template_texts()
    assert {"deploy.yml", "preview.yml", "validate.sh", "deploy-local.sh"} <= set(texts)
    for name, text in texts.items():
        assert "site-out/manifest" not in text, name
        assert "$out/manifest" not in text, name
        assert "/out/manifest" not in text, name
    deploy = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    assert manifest_violations(deploy) == []


def test_validate_mounts_a_separate_manifest_directory() -> None:
    text = (TEMPLATE / "scripts" / "validate.sh").read_text(encoding="utf-8")
    assert '--manifest "/manifest/$manifest_name"' in text
    assert '-v "$manifest_dir":/manifest' in text
    assert '-v "$manifest_dir":/manifest:ro' not in text
    # one run command carries the hardening flags for both mounts
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "keep-id"):
        assert flag in text, flag


def test_manifest_is_its_own_artifact_and_not_in_the_pages_upload() -> None:
    jobs = _deploy_jobs()
    uploads = {
        s["with"]["name"]: str(s["with"]["path"])
        for s in jobs["validate"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    }
    assert set(uploads) == {"site-out", "site-manifest"}
    assert "manifest" not in uploads["site-out"]
    assert "site-manifest/manifest.json" in uploads["site-manifest"]
    for name in PAGES_JOBS:
        downloads = {
            s["with"]["name"]: s["with"]["path"]
            for s in jobs[name]["steps"]
            if str(s.get("uses", "")).startswith("actions/download-artifact@")
        }
        # the Pages job never sees the manifest: the Worker call reads it
        assert downloads == {"site-out": "site-out"}, name
        pages = [
            s
            for s in jobs[name]["steps"]
            if s.get("name") == "Publish the site to Pages"
        ]
        assert len(pages) == 1
        assert "manifest" not in pages[0]["run"]


def test_the_worker_call_reads_the_manifest_the_validate_job_uploads() -> None:
    # The reusable workflow downloads the artifact and reads one file from it
    # (its own tests cover the reading); this ties the names it is given to the
    # upload steps, so a rename on one side cannot slip past the other.
    jobs = _deploy_jobs()
    uploads = {
        s["with"]["name"]: str(s["with"]["path"])
        for s in jobs["validate"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    }
    for name in WORKER_JOBS:
        call = jobs[name]["with"]
        assert call["manifest_artifact"] in uploads, name
        assert call["site_artifact"] in uploads, name
        assert uploads[call["manifest_artifact"]].endswith("/" + call["manifest_file"])
    for job in jobs.values():
        assert all("manifest-in" not in s.get("run", "") for s in job.get("steps", []))


def test_the_manifest_guard_bites() -> None:
    good = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    assert manifest_violations(good) == []
    old_read = good.replace(
        "run: npm ci --ignore-scripts", "run: cat site-out/manifest.json", 1
    )
    assert old_read != good
    assert manifest_violations(old_read)
    bundled = good.replace(
        "path: ${{ runner.temp }}/site-out\n",
        "path: |\n            ${{ runner.temp }}/site-out\n"
        "            ${{ runner.temp }}/site-manifest/manifest.json\n",
        1,
    )
    assert bundled != good
    assert manifest_violations(bundled)


# --- pull request previews and the plain-language comment (issue #157) --------


def preview_text() -> str:
    return (WORKFLOWS / "preview.yml").read_text(encoding="utf-8")


def code_only(text: str) -> str:
    """The file without comment lines, for text searches."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


COMMENT_PERMISSIONS = {"contents": "read", "pull-requests": "write"}
# A script path is only ever allowed behind a trusted folder: the copy made from
# the base commit, the sparse checkout it is copied from, or the new folder.
UNTRUSTED_SCRIPT = re.compile(
    r"(?<!TRUSTED/)(?<!trusted/)(?<!trusted-src/)(?<!\$dir/)scripts/"
)
TRUSTED_FILES = {
    "scripts/read-lock.sh",
    "scripts/check-repo.sh",
    "scripts/check-config.sh",
    "scripts/validate.sh",
    "scripts/write-result.py",
    "engine.lock",
    "engine.release.json",
    "site.yaml",
}
FORK_GUARD = "head.repo.full_name == github.repository"


def preview_violations(text: str) -> list[str]:
    """Problems with preview.yml beyond the general hardening in violations()."""
    found = violations(text)
    data = yaml.safe_load(text)
    triggers = data.get("on", data.get(True))
    if set(triggers) != {"pull_request"}:
        found.append("trigger is not exactly pull_request")
    if "pull_request_target" in code_only(text):
        found.append("the file names the privileged pull request trigger")
    group = data.get("concurrency", {})
    if not group.get("group") or group.get("cancel-in-progress") is not True:
        found.append("no concurrency group that cancels a superseded preview")
    jobs = data["jobs"]
    preview = jobs["preview"]
    if preview.get("timeout-minutes") != 10:
        found.append("preview: timeout-minutes is not 10")
    if FORK_GUARD not in str(preview.get("if")):
        found.append("preview: no guard against pull requests from forks")
    writers = [
        name
        for name, job in jobs.items()
        if "pull-requests" in (job.get("permissions") or {})
    ]
    if writers != ["comment"]:
        found.append(f"pull-requests permission on {writers}, not only comment")
    for name, job in jobs.items():
        job_text = yaml.safe_dump(job)
        if name != "comment" and ("github.token" in job_text or "GH_TOKEN" in job_text):
            found.append(f"{name}: a token outside the comment job")
        if "secrets." in job_text:
            found.append(f"{name}: secrets")
    if "environment" in preview:
        found.append("preview: an environment")

    # --- the preview job runs only trusted copies, against the PR as data
    steps = preview["steps"]
    checkouts = [s for s in steps if "checkout" in str(s.get("uses", ""))]
    paths = sorted(str(c.get("with", {}).get("path")) for c in checkouts)
    if paths != ["pr", "trusted-src"]:
        found.append(f"preview: checkouts are at {paths}, not pr and trusted-src")
    for checkout in checkouts:
        with_ = checkout.get("with", {})
        if with_.get("persist-credentials") is not False:
            found.append("preview: a checkout keeps credentials")
        if with_.get("path") == "trusted-src":
            if "pull_request.base.sha" not in str(with_.get("ref")):
                found.append("preview: the trusted copy is not of the base commit")
            listed = set(str(with_.get("sparse-checkout", "")).split())
            if listed != TRUSTED_FILES:
                found.append("preview: the trusted checkout lists other files")
        elif "ref" in with_:
            found.append("preview: the data checkout is not the default ref")
    by_id = {s["id"]: s for s in steps if "id" in s}
    repo = by_id.get("repo")
    if repo is None:
        found.append("preview: no repository check step with an id")
    else:
        run = repo.get("run", "")
        if "set +e" not in run or 'echo "status=$?"' not in run:
            found.append("preview: the repository check does not record its status")
        if "check-repo.sh" not in run or "2>" not in run:
            found.append("preview: the repository check output is not kept")
    for step_id in ("check", "build"):
        wanted = (
            "steps.repo.outputs.status == '0'"
            if step_id == "check"
            else ("steps.check.outputs.status == '0'")
        )
        if step_id in by_id and by_id[step_id].get("if") != wanted:
            found.append(f"preview: {step_id} is not skipped after a failed step")
    results_ids = [s for s in steps if s.get("name", "").startswith("Make a fresh")]
    if results_ids:
        found.append("preview: the results folder is made after the trust step")
    last = steps[-1].get("if", "")
    for needle in (
        "steps.repo.outputs.status != '0'",
        "steps.check.outputs.status != '0'",
        "steps.build.outputs.status != '0'",
    ):
        if needle not in last:
            found.append(f"preview: the last step does not fail on {needle}")
    writes = [s for s in steps if "write-result.py" in s.get("run", "")]
    if len(writes) != 1:
        found.append("preview: not exactly one result writer")
    else:
        if "--repo-status" not in writes[0]["run"]:
            found.append("preview: the result lacks the repository status")
        if writes[0].get("if") != "${{ !cancelled() }}":
            found.append("preview: the result is not always written")
    upload_idx = [
        i
        for i, s in enumerate(steps)
        if s.get("with", {}).get("name") == "preview-result"
    ]
    if len(upload_idx) != 1 or steps[upload_idx[0]].get("if") != "${{ !cancelled() }}":
        found.append("preview: the result is not always uploaded")
    elif len(steps) - 1 <= upload_idx[0]:
        found.append("preview: the job fails before the result is uploaded")
    trust = [s for s in steps if s.get("id") == "trust"]
    if len(trust) != 1:
        found.append("preview: no trust step")
    else:
        run = trust[0]["run"]
        for needle in (
            "chmod -R a-w",
            "mktemp -d",
            '"$dir/scripts/read-lock.sh"',
            "preview-results.XXXXXX",
            "results=",
        ):
            if needle not in run:
                found.append(f"preview: the trust step lacks {needle}")
        if steps.index(trust[0]) > min(
            i for i, s in enumerate(steps) if "run" in s and s is not trust[0]
        ):
            found.append("preview: something runs before the trust step")
    for step in steps:
        run = step.get("run", "")
        if step.get("id") != "trust" and "read-lock" in run:
            found.append("preview: the image is chosen again after the trust step")
        if UNTRUSTED_SCRIPT.search(run):
            found.append(f"preview: runs a script that is not a trusted copy: {run!r}")
        if "working-directory" in step:
            found.append("preview: a step changes directory")
        for script in ("check-config.sh", "validate.sh"):
            if f"scripts/{script}" in run:
                for needle in ("--root pr", '--image "$IMAGE"'):
                    if needle not in run:
                        found.append(f"preview: {script} lacks {needle}")
                if "--lock" in run:
                    found.append(f"preview: {script} reads a lock from the checkout")
        if "check-repo.sh" in run and "--root pr" not in run:
            found.append("preview: check-repo.sh lacks --root pr")
        if "write-result.py" in run and '"$TRUSTED/scripts/write-result.py"' not in run:
            found.append("preview: write-result.py is not the trusted copy")

    # --- the comment job renders from the base commit and the real verdict
    comment = jobs["comment"]
    if comment.get("permissions") != COMMENT_PERMISSIONS:
        found.append("comment: permissions are not exactly contents + pull-requests")
    if FORK_GUARD not in str(comment.get("if")):
        found.append("comment: no guard against pull requests from forks")
    csteps = comment["steps"]
    ccheckouts = [s for s in csteps if "checkout" in str(s.get("uses", ""))]
    if len(ccheckouts) != 1:
        found.append("comment: not exactly one checkout")
    for checkout in ccheckouts:
        with_ = checkout.get("with", {})
        if "pull_request.base.sha" not in str(with_.get("ref")):
            found.append("comment: the checkout is not of the base commit")
        if with_.get("persist-credentials") is not False:
            found.append("comment: the checkout keeps credentials")
        if with_.get("sparse-checkout") != "scripts/render-comment.py":
            found.append("comment: the checkout is not only the renderer")
    for step in csteps:
        if UNTRUSTED_SCRIPT.search(step.get("run", "")):
            found.append("comment: runs a script that is not from the base commit")
    renders = [s for s in csteps if "render-comment.py" in s.get("run", "")]
    if len(renders) != 1:
        found.append("comment: not exactly one render step")
    else:
        run = renders[0]["run"]
        if "python3 -I trusted/scripts/render-comment.py" not in run:
            found.append("comment: the comment is not rendered by the trusted script")
        if '--preview-result "$PREVIEW_RESULT"' not in run:
            found.append("comment: the renderer is not told the job's real verdict")
        if renders[0].get("env", {}).get("PREVIEW_RESULT") != (
            "${{ needs.preview.result }}"
        ):
            found.append("comment: the verdict is not needs.preview.result")
    posting = [s for s in csteps if "gh api" in s.get("run", "")]
    if len(posting) != 1:
        found.append("comment: not exactly one posting step")
    else:
        run = posting[0]["run"]
        for needle in (
            "startswith",
            "github-actions[bot]",
            "--method PATCH",
            "repo=$GH_REPO workflow=$WORKFLOW",
            '"$count" -gt 1',
            "exit 1",
        ):
            if needle not in run:
                found.append(f"comment: the posting step lacks {needle}")
        for banned in ("result-in", "--edit-last", "pr comment", "tail -n"):
            if banned in run:
                found.append(f"comment: the posting step uses {banned}")
    gate = jobs.get("ci-green", {})
    if gate.get("name") != "CI green":
        found.append("the gate job is not named CI green")
    if set(gate.get("needs", [])) != set(jobs) - {"ci-green"}:
        found.append("the gate does not need every other job")
    if gate.get("if") != "${{ always() }}":
        found.append("the gate does not run when a job fails")
    return found


def test_the_preview_workflow_is_clean() -> None:
    assert preview_violations(preview_text()) == []


def test_the_preview_workflow_has_the_four_jobs() -> None:
    data = yaml.safe_load(preview_text())
    assert list(data["jobs"]) == ["shell", "preview", "comment", "ci-green"]
    shell = data["jobs"]["shell"]
    assert shell["uses"].startswith(
        "ChiefGyk3D/git-your-ship-together/.github/workflows/bash-ci.yml@"
    )
    assert shell["with"]["shfmt-args"] == "-i 2 -ci"


def test_every_workflow_job_has_a_timeout_and_no_opt_out() -> None:
    assert workflow_files()
    for path in workflow_files():
        text = path.read_text(encoding="utf-8")
        assert "continue-on-error" not in code_only(text), path.name
        for name, job in yaml.safe_load(text)["jobs"].items():
            assert "timeout-minutes" in job or "uses" in job, (path.name, name)


def test_the_preview_job_uploads_the_site_and_a_result_file() -> None:
    steps = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    uploads = {
        s["with"]["name"]: s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    }
    assert set(uploads) == {"preview", "preview-result"}
    assert "preview-site" in uploads["preview"]["with"]["path"]
    assert uploads["preview"]["if"] == "steps.build.outputs.status == '0'"
    assert uploads["preview-result"]["with"]["path"].endswith("/result.json")
    assert "manifest" not in uploads["preview"]["with"]["path"]
    assert "result" not in uploads["preview"]["with"]["path"]


def test_the_preview_checks_the_config_before_it_builds() -> None:
    steps = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    runs = [s.get("run", "") for s in steps]
    check = next(i for i, r in enumerate(runs) if "scripts/check-config.sh" in r)
    build = next(i for i, r in enumerate(runs) if "scripts/validate.sh" in r)
    assert check < build


def test_the_job_still_fails_when_the_config_is_bad() -> None:
    steps = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    last = steps[-1]
    assert "exit 1" in last["run"]
    assert "steps.check.outputs.status != '0'" in last["if"]
    assert "steps.build.outputs.status != '0'" in last["if"]


def test_deploy_runs_the_check_before_the_build() -> None:
    steps = _deploy_jobs()["validate"]["steps"]
    runs = [s.get("run", "") for s in steps]
    check = next(i for i, r in enumerate(runs) if "scripts/check-config.sh" in r)
    build = next(i for i, r in enumerate(runs) if "scripts/validate.sh" in r)
    assert check < build
    # a push to main uses main's own lock and files: no overrides
    assert all("--lock" not in r and "--root" not in r for r in runs)


def test_the_trust_step_copies_the_base_scripts_and_picks_the_image_once(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Run the workflow's own trust step against a base copy and a hostile PR."""
    steps = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "trust")
    work = tmp_path / "work"
    work.mkdir()
    base = work / "trusted-src"
    shutil.copytree(TEMPLATE, base)
    pr = work / "pr"
    shutil.copytree(TEMPLATE, pr)
    (pr / "scripts" / "read-lock.sh").write_text("#!/bin/sh\necho evil\n")
    (pr / "engine.lock").write_text("ENGINE_IMAGE=evil\n", encoding="utf-8")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output),
    }
    done = subprocess.run(  # noqa: S603 - the workflow's own step, bash
        [BASH, "-e", "-c", run],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    values = dict(
        line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
    )
    lock_image = _script(
        _copy_template(tmp_path / "ref"), "read-lock.sh", "ENGINE_IMAGE"
    )
    assert values["image"] == lock_image.stdout.strip()
    trusted = Path(values["dir"])
    request.addfinalizer(
        lambda: subprocess.run(["chmod", "-R", "u+w", str(trusted)], check=False)  # noqa: S603, S607 - cleanup
    )
    assert trusted.parent == runner_temp
    assert not str(trusted).startswith(str(work))
    # copies, not links, and read-only
    assert not (trusted / "scripts").is_symlink()
    for script in (trusted / "scripts").iterdir():
        assert not script.is_symlink()
        assert not os.access(script, os.W_OK) or os.geteuid() == 0
    for name in ("read-lock.sh", "check-config.sh", "validate.sh", "write-result.py"):
        assert (trusted / "scripts" / name).read_bytes() == (
            TEMPLATE / "scripts" / name
        ).read_bytes()
    assert (trusted / "engine.lock").read_bytes() == (
        TEMPLATE / "engine.lock"
    ).read_bytes()
    # nothing from the pull request's folder was used
    assert b"evil" not in (trusted / "engine.lock").read_bytes()
    # changing the source afterwards changes nothing: the image is a value
    (base / "engine.lock").write_text("changed\n", encoding="utf-8")
    assert (trusted / "engine.lock").read_bytes() == (
        TEMPLATE / "engine.lock"
    ).read_bytes()


def test_the_preview_guard_bites() -> None:
    good = preview_text()
    assert preview_violations(good) == []
    checkout = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
    trusted = '"$TRUSTED/scripts/check-config.sh"'
    mutations = {
        "privileged trigger": good.replace(
            "  pull_request:\n", "  pull_request_target:\n"
        ),
        "a second trigger": good.replace(
            "  pull_request:\n    branches: [main]\n",
            "  pull_request:\n    branches: [main]\n  push:\n",
        ),
        "secret in the preview job": good.replace(
            "          BUILD_STATUS:",
            "          TOKEN: ${{ secrets.TOKEN }}\n          BUILD_STATUS:",
        ),
        "write access on the preview job": good.replace(
            "    timeout-minutes: 10\n    permissions:\n      contents: read\n",
            "    timeout-minutes: 10\n    permissions:\n      contents: read\n"
            "      pull-requests: write\n",
        ),
        "token in the preview job": good.replace(
            "          BUILD_STATUS:",
            "          GH_TOKEN: ${{ github.token }}\n          BUILD_STATUS:",
        ),
        "no fork guard on the comment job": good.replace(
            " && github.event.pull_request.head.repo.full_name == github.repository",
            "",
        ),
        "no fork guard on the preview job": good.replace(
            "    if: ${{ github.event.pull_request.head.repo.full_name"
            " == github.repository }}\n"
            "    runs-on: ubuntu-latest\n    timeout-minutes: 10",
            "    runs-on: ubuntu-latest\n    timeout-minutes: 10",
        ),
        "no cancel": good.replace(
            "cancel-in-progress: true", "cancel-in-progress: false"
        ),
        "no timeout": good.replace("    timeout-minutes: 10\n", "", 1),
        # --- the preview job must run only trusted copies
        "check script from the pull request": good.replace(
            trusted, "pr/scripts/check-config.sh"
        ),
        "check script from the workspace": good.replace(
            'bash "$TRUSTED/scripts/check-config.sh"', "bash ./scripts/check-config.sh"
        ),
        "build script from the pull request": good.replace(
            '"$TRUSTED/scripts/validate.sh"', "pr/scripts/validate.sh"
        ),
        "repo check from the pull request": good.replace(
            'bash "$TRUSTED/scripts/check-repo.sh" --root pr', "scripts/check-repo.sh"
        ),
        "write-result from the pull request": good.replace(
            '"$TRUSTED/scripts/write-result.py"', "pr/scripts/write-result.py"
        ),
        "no image handed to the check": good.replace(
            '--root pr --image "$IMAGE" \\\n            --format json',
            "--root pr \\\n            --format json",
        ),
        "lock read from the pull request": good.replace(
            '--root pr --image "$IMAGE" \\\n            --format json',
            "--root pr --lock pr/engine.lock \\\n            --format json",
        ),
        "no data root": good.replace(
            'check-config.sh" --root pr --image', 'check-config.sh" --image'
        ),
        "image chosen again later": good.replace(
            '          bash "$TRUSTED/scripts/validate.sh"',
            '          IMAGE="$(bash "$TRUSTED/scripts/read-lock.sh" ENGINE_IMAGE)"\n'
            '          bash "$TRUSTED/scripts/validate.sh"',
        ),
        "head checkout at the workspace root": good.replace("          path: pr\n", ""),
        "trusted checkout of the head": good.replace(
            "          ref: ${{ github.event.pull_request.base.sha }}\n"
            "          path: trusted-src",
            "          path: trusted-src",
        ),
        "trusted checkout lists more": good.replace(
            "            scripts/write-result.py\n",
            "            scripts/write-result.py\n"
            "            scripts/deploy-local.sh\n",
        ),
        "trusted copy left writable": good.replace("chmod -R a-w", "chmod -R u+w"),
        # --- a forbidden file must still produce a comment
        "repo check aborts the job": good.replace(
            '          set +e\n          bash "$TRUSTED/scripts/check-repo.sh"',
            '          bash "$TRUSTED/scripts/check-repo.sh"',
        ),
        "repo output not kept": good.replace(' 2> "$RESULTS/repo-output.txt"', ""),
        "engine check runs after a failed repo check": good.replace(
            "        if: steps.repo.outputs.status == '0'\n", ""
        ),
        "build runs after a failed check": good.replace(
            "        if: steps.check.outputs.status == '0'\n        env:",
            "        env:",
        ),
        "repo status not in the result": good.replace(
            '            --repo-status "$REPO_STATUS" '
            '--repo-files "$RESULTS/repo-output.txt" \\\n',
            "",
        ),
        "result written only on success": good.replace(
            "      - name: Write the result file\n        if: ${{ !cancelled() }}",
            "      - name: Write the result file\n        if: success()",
        ),
        "result upload only on success": good.replace(
            "      - name: Keep the result for the comment job\n"
            "        if: ${{ !cancelled() }}",
            "      - name: Keep the result for the comment job\n        if: success()",
        ),
        "job not failed by a forbidden file": good.replace(
            "(steps.repo.outputs.status != '0' || ", "("
        ),
        "results folder made later": good.replace(
            "      - name: Check the config in plain language",
            "      - name: Make a fresh folder for the results\n"
            "        run: echo later\n\n"
            "      - name: Check the config in plain language",
        ),
        "step before the trust step": good.replace(
            "      - name: Fetch the trusted scripts",
            "      - run: echo early\n      - name: Fetch the trusted scripts",
        ),
        "checkout keeps credentials": good.replace(
            "          path: pr\n          persist-credentials: false",
            "          path: pr\n          persist-credentials: true",
        ),
        # --- the comment job
        "renderer from the head": good.replace(
            "ref: ${{ github.event.pull_request.base.sha }}\n          path: trusted\n",
            "path: trusted\n",
        ),
        "renderer checkout keeps credentials": good.replace(
            "sparse-checkout-cone-mode: false\n          persist-credentials: false\n"
            "\n      - uses: actions/download-artifact",
            "sparse-checkout-cone-mode: false\n          persist-credentials: true\n"
            "\n      - uses: actions/download-artifact",
        ),
        "whole repository checked out in comment": good.replace(
            "          sparse-checkout: scripts/render-comment.py\n", ""
        ),
        "comment runs the head renderer": good.replace(
            "python3 -I trusted/scripts/render-comment.py",
            "python3 -I scripts/render-comment.py",
        ),
        "verdict not passed": good.replace(' --preview-result "$PREVIEW_RESULT"', ""),
        "verdict from the artifact": good.replace(
            "PREVIEW_RESULT: ${{ needs.preview.result }}", "PREVIEW_RESULT: success"
        ),
        "comment posts the artifact": good.replace(
            '-F "body=@$COMMENT_FILE" > /dev/null\n          else',
            '-F "body=@$RUNNER_TEMP/result-in/result.json" > /dev/null\n          else',
        ),
        "comment takes the last of several": good.replace(
            'if [ "$count" -gt 1 ]; then', "if false; then"
        ),
        "marker without the repository": good.replace(
            "repo=$GH_REPO workflow=$WORKFLOW", "workflow=$WORKFLOW"
        ),
        "comment edits the last comment": good.replace(
            "--method PATCH", "--method PUT"
        ),
        "second checkout in comment": good.replace(
            "      - uses: actions/download-artifact@",
            f"      - uses: {checkout}\n        with:\n          path: more\n"
            "      - uses: actions/download-artifact@",
            1,
        ),
        "comment permission widened": good.replace(
            "      contents: read # to fetch",
            "      issues: write\n      contents: read # to fetch",
        ),
        "gate renamed": good.replace("name: CI green", "name: done"),
        "gate drops a job": good.replace(
            "needs: [shell, preview, comment]", "needs: [preview]"
        ),
        "unpinned shell call": good.replace(
            "bash-ci.yml@9e0d7b2e7e8530e2b1bb200ecb4fbf6de021881f # v1.38.0",
            "bash-ci.yml@main",
        ),
    }
    for label, mutated in mutations.items():
        assert mutated != good, label
        assert preview_violations(mutated), label


# --- running the scripts ---------------------------------------------------------


def _run(
    root: Path,
    name: str,
    *args: str,
    path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": path or "/usr/bin:/bin", "HOME": str(root)}
    return subprocess.run(  # noqa: S603 - fixed argv, a copy of the template
        [BASH, str(root / "scripts" / name), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _fake_docker(tmp_path: Path, body: str) -> str:
    """A `docker` that runs ``body``; returns a PATH that finds it first."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/bash\nif [ "$1" = "--version" ]; then'
        ' echo "Docker version 1"; exit 0; fi\n'
        # The image is already present: these tests are about the check run.
        'if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then exit 0; fi\n' + body,
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


def _fake_runtime(
    tmp_path: Path, name: str, run_body: str, *, cached: bool, pull_ok: bool = True
) -> tuple[str, Path]:
    """A `docker` or `podman` that models an image cache.

    `image inspect` succeeds only when ``cached`` or after a pull. `pull`
    writes progress to stderr (as a fresh runner does) and records itself.
    Any other call is the check run and executes ``run_body``; it fails
    with status 125 if the image was never pulled.
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    state = tmp_path / "pulled"
    calls = tmp_path / "calls.txt"
    if cached:
        state.write_text("", encoding="utf-8")
    pull = (
        f'echo "Trying to pull image..." >&2\ntouch {state}\nexit 0\n'
        if pull_ok
        else 'echo "pull failed: denied" >&2\nexit 1\n'
    )
    script = (
        "#!/bin/bash\n"
        f'echo "$*" >> {calls}\n'
        'if [ "$1" = "--version" ]; then'
        f' echo "{name} version 1"; exit 0; fi\n'
        f'if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then\n'
        f"  [ -e {state} ]; exit\n"
        "fi\n"
        f'if [ "$1" = "pull" ]; then {pull}fi\n'
        f'[ -e {state} ] || {{ echo "image not present" >&2; exit 125; }}\n' + run_body
    )
    exe = bin_dir / name
    exe.write_text(script, encoding="utf-8")
    exe.chmod(0o755)
    # Only this runtime may be found: link the system tools except any real
    # docker or podman, so the test is the same on a host that has either.
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    for tool in (
        "bash",
        "env",
        "mktemp",
        "id",
        "dirname",
        "basename",
        "grep",
        "cat",
        "rm",
        "sed",
        "tr",
        "printf",
        "touch",
        "echo",
        "head",
    ):
        found = shutil.which(tool, path="/usr/bin:/bin")
        if found and not (tools / tool).exists():
            (tools / tool).symlink_to(found)
    return f"{bin_dir}:{tools}", calls


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_check_config_pulls_a_missing_image_and_ignores_pull_progress(
    tmp_path: Path, runtime: str
) -> None:
    root = _copy_template(tmp_path)
    path, calls = _fake_runtime(
        tmp_path, runtime, 'echo "site.yaml is valid."\n', cached=False
    )
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "site.yaml is valid.\n"
    assert result.stderr == ""
    assert any(c.startswith("pull ") for c in _calls(calls))


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_check_config_skips_the_pull_when_the_image_is_present(
    tmp_path: Path, runtime: str
) -> None:
    root = _copy_template(tmp_path)
    path, calls = _fake_runtime(tmp_path, runtime, 'echo "ok"\n', cached=True)
    assert _run(root, "check-config.sh", path=path).returncode == 0
    assert not any(c.startswith("pull ") for c in _calls(calls))


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_check_config_invalid_config_on_a_fresh_runner_exits_2(
    tmp_path: Path, runtime: str
) -> None:
    root = _copy_template(tmp_path)
    path, _ = _fake_runtime(
        tmp_path, runtime, 'echo "site.yaml has 1 problem."\nexit 2\n', cached=False
    )
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 2
    assert result.stdout == "site.yaml has 1 problem.\n"


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_check_config_engine_crash_on_a_fresh_runner_still_fails_closed(
    tmp_path: Path, runtime: str
) -> None:
    root = _copy_template(tmp_path)
    path, _ = _fake_runtime(tmp_path, runtime, "echo boom >&2\nexit 1\n", cached=False)
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 3
    assert "could not run the check" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_check_config_stderr_from_the_run_itself_still_fails_closed(
    tmp_path: Path, runtime: str
) -> None:
    root = _copy_template(tmp_path)
    path, _ = _fake_runtime(
        tmp_path,
        runtime,
        'echo "warning" >&2\necho "site.yaml is valid."\n',
        cached=True,
    )
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 3
    assert "could not run the check" in result.stderr


@pytest.mark.parametrize("runtime", ["docker", "podman"])
def test_check_config_fails_clearly_when_the_pull_fails(
    tmp_path: Path, runtime: str
) -> None:
    root = _copy_template(tmp_path)
    path, _ = _fake_runtime(
        tmp_path, runtime, 'echo "valid"\n', cached=False, pull_ok=False
    )
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 3
    assert "could not pull the pinned engine image" in result.stderr
    assert result.stdout == ""


def _calls(calls: Path) -> list[str]:
    """Each runtime call as one line: its full argv, space-joined."""
    return calls.read_text(encoding="utf-8").splitlines()


def test_check_config_pull_and_run_use_the_same_pinned_digest(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    log = tmp_path / "argv.txt"
    path, calls = _fake_runtime(
        tmp_path, "podman", f'printf "%s\\n" "$@" > {log}\necho ok\n', cached=False
    )
    assert _run(root, "check-config.sh", path=path).returncode == 0
    pin = re.search(
        r"^ENGINE_IMAGE=(\S+@sha256:[0-9a-f]{64})$",
        (root / "engine.lock").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert pin is not None
    image = pin.group(1)
    seen = _calls(calls)
    pulls = [c.split() for c in seen if c.startswith("pull ")]
    inspects = [c.split() for c in seen if c.startswith("image inspect ")]
    assert len(pulls) == 1
    assert len(inspects) == 1
    assert pulls[0][-1] == image
    assert inspects[0][-1] == image
    assert image in _argv(log)  # the check run uses the exact same reference


def _recording_docker(tmp_path: Path, reply: str = "ok") -> tuple[str, Path]:
    log = tmp_path / "argv.txt"
    path = _fake_docker(tmp_path, f'printf "%s\\n" "$@" > {log}\necho "{reply}"\n')
    return path, log


def _argv(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines()


def _mounts(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]


HARDENING = [
    ("--network", "none"),
    ("--cap-drop", "ALL"),
    ("--security-opt", "no-new-privileges:true"),
    ("--pids-limit", None),
    ("--memory", None),
    ("--tmpfs", "/tmp"),  # noqa: S108 - a path inside the container
]


def _assert_hardened(argv: list[str]) -> None:
    assert "--read-only" in argv
    for flag, value in HARDENING:
        assert flag in argv, flag
        if value is not None:
            assert argv[argv.index(flag) + 1] == value, flag
    user = argv[argv.index("--user") + 1]
    assert re.fullmatch(r"[1-9][0-9]*:[0-9]+", user), user  # not root
    assert "-e" not in argv and "--env" not in argv
    assert not any(a.startswith("--env-file") for a in argv)
    assert "--privileged" not in argv


def test_check_config_runs_the_pinned_image_hardened(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    path, log = _recording_docker(tmp_path, "site.yaml is valid: 1 tab and 1 block.")
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "site.yaml is valid: 1 tab and 1 block.\n"
    argv = _argv(log)
    image = _script(root, "read-lock.sh", "ENGINE_IMAGE").stdout.strip()
    assert image in argv
    _assert_hardened(argv)
    assert argv[argv.index(image) + 1 :] == [
        "check",
        "--config",
        "/config/site.yaml",
        "--format",
        "plain",
    ]


def test_check_config_mounts_only_the_config_and_media(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    (root / "secret.txt").write_text("not for the engine", encoding="utf-8")
    path, log = _recording_docker(tmp_path)
    assert _run(root, "check-config.sh", path=path).returncode == 0
    mounts = _mounts(_argv(log))
    assert sorted(mounts) == sorted(
        [f"{root}/site.yaml:/config/site.yaml:ro", f"{root}/media:/config/media:ro"]
    )
    # never the checkout as a whole, never read-write
    assert not any(m.split(":")[0].rstrip("/") == str(root) for m in mounts)
    assert all(m.endswith(":ro") for m in mounts)


def test_check_config_mounts_the_base_file_alone_and_read_only(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    base = tmp_path / "basecopy"
    base.mkdir()
    (base / "site.yaml").write_text("version: 1\n", encoding="utf-8")
    (base / "other.txt").write_text("x", encoding="utf-8")
    path, log = _recording_docker(tmp_path)
    result = _run(
        root, "check-config.sh", "--against", str(base / "site.yaml"), path=path
    )
    assert result.returncode == 0, result.stderr
    argv = _argv(log)
    assert f"{base}/site.yaml:/base/site.yaml:ro" in _mounts(argv)
    assert not any(m.startswith(f"{base}:") for m in _mounts(argv))
    assert argv[-2:] == ["--against", "/base/site.yaml"]


def _lock_with_other_digest(root: Path, tmp_path: Path) -> tuple[Path, str, str]:
    """The base lock is the shipped one; the pull request's lock is changed."""
    base_lock = tmp_path / "base.lock"
    text = (root / "engine.lock").read_text(encoding="utf-8")
    base_lock.write_text(text, encoding="utf-8")
    head_digest = "sha256:" + "ab" * 32
    lines = []
    for line in text.splitlines():
        if line.startswith("ENGINE_IMAGE="):
            line = re.sub(r"@sha256:[0-9a-f]{64}", "@" + head_digest, line)
            line = line.replace("reach-around:0.1.0", "evil/image:9")
        lines.append(line)
    (root / "engine.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")
    base_image = _script(root, "read-lock.sh", "ENGINE_IMAGE", str(base_lock))
    head_image = _script(root, "read-lock.sh", "ENGINE_IMAGE")
    assert base_image.returncode == 0 and head_image.returncode == 0
    assert base_image.stdout != head_image.stdout
    return base_lock, base_image.stdout.strip(), head_image.stdout.strip()


@pytest.mark.parametrize("script", ["check-config.sh", "validate.sh"])
def test_the_base_lock_wins_when_the_pull_request_changes_engine_lock(
    tmp_path: Path, script: str
) -> None:
    root = _copy_template(tmp_path)
    base_lock, base_image, head_image = _lock_with_other_digest(root, tmp_path)
    path, log = _recording_docker(tmp_path)
    extra = ["--out", str(tmp_path / "site-out")] if script == "validate.sh" else []
    result = _run(root, script, "--lock", str(base_lock), *extra, path=path)
    assert result.returncode == 0, result.stderr
    argv = _argv(log)
    assert base_image in argv
    assert head_image not in argv
    # and without --lock the repository's own lock is used (a push to main)
    again = ["--out", str(tmp_path / "second-out")] if extra else []
    result = _run(root, script, *again, path=path)
    assert result.returncode == 0, result.stderr
    assert head_image in _argv(log)


def test_a_symlinked_lock_is_refused(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    real = tmp_path / "real.lock"
    real.write_text(
        (root / "engine.lock").read_text(encoding="utf-8"), encoding="utf-8"
    )
    link = tmp_path / "link.lock"
    link.symlink_to(real)
    assert _script(root, "read-lock.sh", "ENGINE_IMAGE", str(real)).returncode == 0
    assert _script(root, "read-lock.sh", "ENGINE_IMAGE", str(link)).returncode != 0


@pytest.mark.parametrize("script", ["check-config.sh", "validate.sh"])
def test_a_symlinked_config_or_media_folder_is_not_mounted(
    tmp_path: Path, script: str
) -> None:
    for victim in ("site.yaml", "media"):
        work = tmp_path / victim
        work.mkdir()
        root = _copy_template(work)
        target = work / "elsewhere"
        if victim == "site.yaml":
            target.write_text("version: 1\n", encoding="utf-8")
        else:
            target.mkdir()
        if victim == "site.yaml":
            (root / victim).unlink()
        else:
            shutil.rmtree(root / victim)
        (root / victim).symlink_to(target)
        path, log = _recording_docker(work)
        extra = ["--out", str(work / "o")] if script == "validate.sh" else []
        result = _run(root, script, *extra, path=path)
        assert result.returncode != 0, (script, victim)
        assert "symbolic link" in result.stderr
        assert not log.exists()  # the container never started


def test_validate_mounts_only_what_the_build_reads(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    (root / "overrides").mkdir()
    (root / "numbers.json").write_text("{}", encoding="utf-8")
    (root / "secret.txt").write_text("no", encoding="utf-8")
    path, log = _recording_docker(tmp_path)
    out = tmp_path / "site-out"
    result = _run(root, "validate.sh", "--out", str(out), path=path)
    assert result.returncode == 0, result.stderr
    argv = _argv(log)
    _assert_hardened(argv)
    mounts = _mounts(argv)
    read_only = sorted(m for m in mounts if m.endswith(":ro"))
    assert read_only == sorted(
        [
            f"{root}/site.yaml:/config/site.yaml:ro",
            f"{root}/media:/config/media:ro",
            f"{root}/overrides:/config/overrides:ro",
            f"{root}/numbers.json:/config/numbers.json:ro",
        ]
    )
    writable = [m for m in mounts if not m.endswith(":ro")]
    assert sorted(w.split(":")[1] for w in writable) == ["/manifest", "/out"]
    assert not any(m.split(":")[0].rstrip("/") == str(root) for m in mounts)


def test_check_config_passes_the_invalid_status_through(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    out = tmp_path / "result.txt"
    path = _fake_docker(tmp_path, 'echo "site.yaml has 1 problem."\nexit 2\n')
    result = _run(root, "check-config.sh", "--out", str(out), path=path)
    assert result.returncode == 2
    assert out.read_text(encoding="utf-8") == "site.yaml has 1 problem.\n"


def test_check_config_says_when_the_engine_is_too_old(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    path = _fake_docker(
        tmp_path,
        'echo "usage: reach-around [-h]" >&2\n'
        "echo \"reach-around: error: argument command: invalid choice: 'check'\" >&2\n"
        "exit 2\n",
    )
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 3
    assert "too old" in result.stderr
    assert result.stdout == ""


def test_check_config_treats_a_crash_as_not_run(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    path = _fake_docker(tmp_path, "echo boom >&2\nexit 1\n")
    result = _run(root, "check-config.sh", path=path)
    assert result.returncode == 3
    assert "could not run the check" in result.stderr


def test_check_config_needs_a_container_runtime(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run(root, "check-config.sh", path=str(empty))
    assert result.returncode == 3
    assert "Docker or Podman" in result.stderr


# --- --out never follows a link or overwrites ------------------------------------


def test_check_config_out_is_created_new(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    path, _ = _recording_docker(tmp_path, "fine")
    out = tmp_path / "check.txt"
    assert _run(root, "check-config.sh", "--out", str(out), path=path).returncode == 0
    assert out.read_text(encoding="utf-8") == "fine\n"
    assert stat.S_IMODE(out.stat().st_mode) & 0o002 == 0


def test_check_config_out_refuses_an_existing_file_or_a_link(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    path, log = _recording_docker(tmp_path)
    existing = tmp_path / "exists.txt"
    existing.write_text("keep", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    dangling = tmp_path / "dangling.txt"
    dangling.symlink_to(tmp_path / "nowhere.txt")
    for out in (existing, link, dangling):
        result = _run(root, "check-config.sh", "--out", str(out), path=path)
        assert result.returncode == 3, out
        assert "--out" in result.stderr
    assert existing.read_text(encoding="utf-8") == "keep"
    assert target.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "nowhere.txt").exists()
    assert not log.exists()  # refused before the container started


def test_check_config_out_refuses_a_linked_folder(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    path, log = _recording_docker(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linkdir").symlink_to(real)
    result = _run(
        root, "check-config.sh", "--out", str(tmp_path / "linkdir" / "c.txt"), path=path
    )
    assert result.returncode == 3
    assert not (real / "c.txt").exists()
    assert not log.exists()


# --- the result file --------------------------------------------------------------


def _write_result(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-I", str(root / "scripts" / "write-result.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_write_result_bundles_statuses_and_the_engine_json(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    check = tmp_path / "check.json"
    check.write_text('{"valid": true}', encoding="utf-8")
    out = tmp_path / "result.json"
    done = _write_result(
        root, "--repo-status", "0", "--check-status", "0", "--build-status", "0",
        "--check", str(check), "--out", str(out),
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "check_status": 0,
        "build_status": 0,
        "check": {"valid": True},
        "repo_status": 0,
        "repo_files": [],
    }


@pytest.mark.parametrize(
    ("status", "build", "expected"),
    [("", "", (3, None)), ("x", "999", (3, 3)), ("2", "", (2, None))],
)
def test_write_result_defaults_bad_statuses(
    tmp_path: Path, status: str, build: str, expected: tuple[int, int | None]
) -> None:
    root = _copy_template(tmp_path)
    out = tmp_path / "result.json"
    done = _write_result(
        root, "--check-status", status, "--build-status", build,
        "--check", str(tmp_path / "missing.json"), "--out", str(out),
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert (data["check_status"], data["build_status"]) == expected
    assert data["check"] is None


def test_write_result_refuses_links_and_overwrites(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    existing = tmp_path / "exists.json"
    existing.write_text("keep", encoding="utf-8")
    done = _write_result(root, "--check-status", "0", "--out", str(existing))
    assert done.returncode == 2
    assert existing.read_text(encoding="utf-8") == "keep"
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linkdir").symlink_to(real)
    done = _write_result(
        root, "--check-status", "0", "--out", str(tmp_path / "linkdir" / "r.json")
    )
    assert done.returncode == 2
    assert not (real / "r.json").exists()
    # a linked check file is not read
    secret = tmp_path / "secret.json"
    secret.write_text('{"leak": 1}', encoding="utf-8")
    (tmp_path / "check-link.json").symlink_to(secret)
    out = tmp_path / "ok.json"
    done = _write_result(
        root, "--check-status", "0", "--check", str(tmp_path / "check-link.json"),
        "--out", str(out),
    )  # fmt: skip
    assert json.loads(out.read_text(encoding="utf-8"))["check"] is None


def test_the_template_round_trip_from_the_engine_to_the_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Engine JSON, then write-result.py, then render-comment.py."""
    from reach_around.cli import main

    root = _copy_template(tmp_path)
    site = root / "site.yaml"
    text = site.read_text(encoding="utf-8")
    assert "url: https://news.example.com/" in text
    site.write_text(
        text.replace("url: https://news.example.com/", "url: http://news.example.com/"),
        encoding="utf-8",
    )
    assert main(["check", "--config", str(site), "--format", "json"]) == 2
    check = tmp_path / "check.json"
    check.write_text(capsys.readouterr().out, encoding="utf-8")
    result = tmp_path / "result.json"
    assert (
        _write_result(
            root,
            "--repo-status",
            "0",
            "--check-status",
            "2",
            "--check",
            str(check),
            "--out",
            str(result),
        ).returncode
        == 0
    )
    comment = tmp_path / "comment.md"
    done = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable, "-I", str(root / "scripts" / "render-comment.py"),
            "--in", str(result), "--out", str(comment),
            "--preview-result", "failure", "--repo", "example/site",
            "--workflow", "Preview",
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    text = comment.read_text(encoding="utf-8")
    assert "block `newsletter`, field `url`" in text
    assert "news.example.com" not in text


def test_the_troubleshooting_table_quotes_real_messages() -> None:
    """Every phrase the docs promise is one the engine can print."""
    from reach_around.checkvocab import VOCAB

    phrases = [
        "the web address must start with https://",
        "must not contain a user name or password",
        "is missing",
        "is not a setting Reach Around knows",
        "(unknown setting #",
        "must be lowercase letters, digits and hyphens only",
        "is used more than once",
        "links to a tab that does not exist",
        "must be one of:",
        "is too long (at most",
        "the date must be written YYYY-MM-DD",
        "must be a path to a picture file in this repository",
        "is not valid YAML",
        "has a setting written twice",
        "uses an alias",
    ]
    docs = (TEMPLATE / "docs" / "EDITING.md").read_text(encoding="utf-8")
    source = "\n".join(m for m, _ in VOCAB.values()) + (
        ROOT / "src" / "reach_around" / "commands" / "check.py"
    ).read_text(encoding="utf-8")
    assert phrases
    for phrase in phrases:
        assert phrase in docs, phrase
        assert phrase in source, phrase


def test_check_config_out_survives_a_link_planted_during_the_run(
    tmp_path: Path,
) -> None:
    """The exclusive create is the defense when the path appears after the
    early check (a race): a planted symlink is never written through."""
    root = _copy_template(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    out = tmp_path / "check.txt"
    path = _fake_docker(tmp_path, f'ln -s {target} {out}\necho "site.yaml is valid"\n')
    result = _run(root, "check-config.sh", "--out", str(out), path=path)
    assert result.returncode == 3
    assert target.read_text(encoding="utf-8") == "keep"


# --- a hostile pull request cannot reach the engine image or the scripts ----------

EVIL_IMAGE = "evil.example/engine:9@sha256:" + "ab" * 32
MARKER_FILE = "PWNED"


def _evil_script(name: str) -> str:
    if name.endswith(".py"):
        return f"open({MARKER_FILE!r}, 'w').close()\n"
    return f"#!/bin/bash\ntouch {MARKER_FILE}\necho {EVIL_IMAGE}\nexit 0\n"


def _hostile_pr(tmp_path: Path, how: str) -> tuple[Path, Path, str]:
    """A trusted copy (the base), and a pull request that attacks it.

    Returns (trusted root, pull request root, the trusted image).
    """
    trusted = tmp_path / "trusted"
    shutil.copytree(TEMPLATE, trusted)
    pr = tmp_path / "pr"
    shutil.copytree(TEMPLATE, pr)
    image = _script(trusted, "read-lock.sh", "ENGINE_IMAGE").stdout.strip()
    scripts = ("read-lock.sh", "check-repo.sh", "check-config.sh", "validate.sh")
    if how.startswith("script:"):
        name = how.split(":", 1)[1]
        (pr / "scripts" / name).write_text(_evil_script(name), encoding="utf-8")
    elif how == "lock":
        lock = (pr / "engine.lock").read_text(encoding="utf-8")
        lock = re.sub(
            r"^ENGINE_IMAGE=.*$", f"ENGINE_IMAGE={EVIL_IMAGE}", lock, flags=re.M
        )
        (pr / "engine.lock").write_text(lock, encoding="utf-8")
    elif how == "lock-link":
        evil = tmp_path / "evil.lock"
        evil.write_text(f"ENGINE_IMAGE={EVIL_IMAGE}\n", encoding="utf-8")
        (pr / "engine.lock").unlink()
        (pr / "engine.lock").symlink_to(evil)
    elif how == "scripts-link":
        evil_dir = tmp_path / "evil-scripts"
        evil_dir.mkdir()
        for name in scripts:
            (evil_dir / name).write_text(_evil_script(name), encoding="utf-8")
        shutil.rmtree(pr / "scripts")
        (pr / "scripts").symlink_to(evil_dir)
    elif how == "python-shadow":
        for module in ("json", "os", "stat", "sys"):
            (pr / f"{module}.py").write_text(_evil_script("x.py"), encoding="utf-8")
    else:
        raise AssertionError(how)
    return trusted, pr, image


HOSTILE_PRS = [
    "script:read-lock.sh",
    "script:check-repo.sh",
    "script:check-config.sh",
    "script:validate.sh",
    "script:write-result.py",
    "lock",
    "lock-link",
    "scripts-link",
    "python-shadow",
]


@pytest.mark.parametrize("how", HOSTILE_PRS)
@pytest.mark.parametrize("script", ["check-config.sh", "validate.sh"])
def test_a_hostile_pull_request_cannot_change_the_image_or_the_mounts(
    tmp_path: Path, how: str, script: str
) -> None:
    trusted, pr, image = _hostile_pr(tmp_path, how)
    path, log = _recording_docker(tmp_path)
    extra = ["--out", str(tmp_path / "site-out")] if script == "validate.sh" else []
    done = subprocess.run(  # noqa: S603 - fixed argv; a trusted copy of the template
        [
            BASH,
            str(trusted / "scripts" / script),
            "--root",
            str(pr),
            "--image",
            image,
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=pr,
        env={"PATH": path, "HOME": str(tmp_path)},
    )
    assert done.returncode == 0, done.stderr
    argv = _argv(log)
    assert image in argv
    assert EVIL_IMAGE not in argv
    assert not any("evil" in a for a in argv)
    mounts = _mounts(argv)
    read_only = sorted(m for m in mounts if m.endswith(":ro"))
    assert read_only == sorted(
        [f"{pr}/site.yaml:/config/site.yaml:ro", f"{pr}/media:/config/media:ro"]
    )
    writable = [m for m in mounts if not m.endswith(":ro")]
    expected = ["/manifest", "/out"] if script == "validate.sh" else []
    assert sorted(w.split(":")[1] for w in writable) == expected
    assert not any(m.split(":")[0] in {str(pr), str(trusted)} for m in mounts)
    assert not (pr / MARKER_FILE).exists()  # no script from the pull request ran
    assert not (tmp_path / MARKER_FILE).exists()
    _assert_hardened(argv)


@pytest.mark.parametrize("how", HOSTILE_PRS)
def test_a_hostile_pull_request_cannot_make_the_trusted_helpers_run_its_code(
    tmp_path: Path, how: str
) -> None:
    trusted, pr, _ = _hostile_pr(tmp_path, how)
    # the repository check reads the pull request as data
    done = subprocess.run(  # noqa: S603 - fixed argv
        [BASH, str(trusted / "scripts" / "check-repo.sh"), "--root", str(pr)],
        capture_output=True,
        text=True,
        check=False,
        cwd=pr,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    # the result writer must not import anything from the pull request's folder
    out = tmp_path / "result.json"
    done = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable, "-I", str(trusted / "scripts" / "write-result.py"),
            "--check-status", "0", "--out", str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=pr,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["check_status"] == 0
    assert not (pr / MARKER_FILE).exists()
    assert not (tmp_path / MARKER_FILE).exists()


def test_a_hostile_hostile_battery_is_collected() -> None:
    assert len(HOSTILE_PRS) >= 9


@pytest.mark.parametrize("script", ["check-config.sh", "validate.sh"])
@pytest.mark.parametrize(
    "bad",
    [
        "evil;touch PWNED",
        "ghcr.io/x/y:1",
        "ghcr.io/x/y@sha256:abc",
        "$(touch PWNED)",
        "",
    ],
)
def test_a_badly_shaped_image_is_refused_before_anything_starts(
    tmp_path: Path, script: str, bad: str
) -> None:
    root = _copy_template(tmp_path)
    path, log = _recording_docker(tmp_path)
    extra = ["--out", str(tmp_path / "o")] if script == "validate.sh" else []
    result = _run(root, script, "--image", bad, *extra, path=path)
    # an empty --image falls back to the lock; every other value is refused
    if bad:
        assert result.returncode != 0
        assert not log.exists()
    assert not (tmp_path / "PWNED").exists()


def test_read_lock_verify_checks_the_shape_only(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    good = _script(root, "read-lock.sh", "ENGINE_IMAGE").stdout.strip()
    ok = _script(root, "read-lock.sh", "--verify", "ENGINE_IMAGE", good)
    assert ok.returncode == 0 and ok.stdout.strip() == good
    assert (
        _script(root, "read-lock.sh", "--verify", "ENGINE_IMAGE", "x").returncode != 0
    )


def test_check_repo_checks_the_root_it_is_given_not_its_own_folder(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    shutil.copytree(TEMPLATE, trusted)
    pr = tmp_path / "pr"
    shutil.copytree(TEMPLATE, pr)
    (pr / ".env").write_text("X=1\n", encoding="utf-8")
    script = str(trusted / "scripts" / "check-repo.sh")
    env = {"PATH": "/usr/bin:/bin"}
    hostile = subprocess.run(  # noqa: S603 - fixed argv
        [BASH, script, "--root", str(pr)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert hostile.returncode == 1
    assert ".env" in hostile.stderr
    clean = subprocess.run(  # noqa: S603 - fixed argv
        [BASH, script], capture_output=True, text=True, check=False, env=env
    )
    assert clean.returncode == 0, clean.stderr


def test_write_result_records_the_forbidden_file_names_only(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    output = tmp_path / "repo-output.txt"
    output.write_text(
        "These files must not be in the repository:\n"
        "  config/.env\n"
        "  keys/id_rsa\n"
        "  evil\x00name/x.pem\n"
        "Remove them from Git (git rm --cached <file>) and keep them private.\n"
        "If one held a real token, revoke the token first.\n",
        encoding="utf-8",
    )
    out = tmp_path / "result.json"
    done = _write_result(
        root, "--repo-status", "1", "--repo-files", str(output), "--out", str(out)
    )
    assert done.returncode == 0, done.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["repo_status"] == 1
    assert data["repo_files"] == [".env", "id_rsa", "x.pem"]  # base names only
    assert data["check_status"] == 3 and data["check"] is None
    # names are not recorded when the check passed
    (tmp_path / "b").mkdir()
    out2 = tmp_path / "b" / "result.json"
    _write_result(
        root, "--repo-status", "0", "--repo-files", str(output), "--out", str(out2)
    )
    assert json.loads(out2.read_text(encoding="utf-8"))["repo_files"] == []
    # a missing status means the check did not run
    out3 = tmp_path / "b" / "r3.json"
    _write_result(root, "--out", str(out3))
    assert json.loads(out3.read_text(encoding="utf-8"))["repo_status"] == 3


# --- end to end: a forbidden file still produces a comment ------------------------


def _steps_by_id() -> dict[str, dict[str, Any]]:
    steps = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    return {s["id"]: s for s in steps if "id" in s}


def _named_step(name: str) -> dict[str, Any]:
    steps = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    return next(s for s in steps if s.get("name") == name)


class _Run:
    """Run the preview job's own shell steps in a scratch workspace."""

    def __init__(self, tmp_path: Path, forbidden: str | None) -> None:
        self.work = tmp_path / "work"
        self.work.mkdir()
        shutil.copytree(TEMPLATE, self.work / "trusted-src")
        shutil.copytree(TEMPLATE, self.work / "pr")
        if forbidden:
            target = self.work / "pr" / forbidden
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("SECRET=1\n", encoding="utf-8")
        self.runner_temp = tmp_path / "runner-temp"
        self.runner_temp.mkdir()
        self.output = tmp_path / "github-output"
        self.output.write_text("", encoding="utf-8")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        for tool in (
            "bash", "cp", "chmod", "mktemp", "git", "find", "sed", "cat", "grep",
            "dirname", "basename", "wc", "head", "tr", "id", "rm", "mkdir", "touch",
            "readlink", "cut", "sort", "uname", "tee", "jq",
        ):  # fmt: skip
            for folder in ("/usr/bin", "/bin"):
                if (Path(folder) / tool).exists():
                    (self.bin / tool).symlink_to(Path(folder) / tool)
                    break
        self.docker_log = tmp_path / "docker-called"
        docker = self.bin / "docker"
        docker.write_text(
            f"#!/bin/bash\ntouch {self.docker_log}\nexit 9\n", encoding="utf-8"
        )
        docker.chmod(0o755)
        self.outputs: dict[str, str] = {}

    def run(self, step: dict[str, Any], **env: str) -> subprocess.CompletedProcess[str]:
        self.output.write_text("", encoding="utf-8")
        full = {
            "PATH": str(self.bin),
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_OUTPUT": str(self.output),
            "HOME": str(self.work),
            **env,
        }
        done = subprocess.run(  # noqa: S603 - the workflow's own step text
            [str(self.bin / "bash"), "-e", "-c", step["run"]],
            cwd=self.work, env=full, capture_output=True, text=True, check=False,
        )  # fmt: skip
        for line in self.output.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            self.outputs[f"{step.get('id', '')}.{key}"] = value
        return done


@pytest.mark.parametrize(
    "forbidden", [".env", "keys/server.pem", "deep/er/id_rsa", "x/wrangler.deploy.toml"]
)
def test_a_forbidden_file_still_produces_a_comment_end_to_end(
    tmp_path: Path, request: pytest.FixtureRequest, forbidden: str
) -> None:
    run = _Run(tmp_path, forbidden)
    steps = _steps_by_id()
    done = run.run(steps["trust"])
    assert done.returncode == 0, done.stderr
    trusted = run.outputs["trust.dir"]
    results = run.outputs["trust.results"]
    request.addfinalizer(
        lambda: subprocess.run(["chmod", "-R", "u+w", trusted], check=False)  # noqa: S603, S607 - cleanup
    )
    assert Path(results).is_dir() and not results.startswith(str(run.work))

    # the repository check fails but the step records it and ends cleanly
    done = run.run(steps["repo"], TRUSTED=trusted, RESULTS=results)
    assert done.returncode == 0, done.stderr
    assert run.outputs["repo.status"] == "1"
    assert Path(forbidden).name in done.stderr

    # the engine steps are skipped by their conditions, so nothing starts
    assert steps["check"]["if"] == "steps.repo.outputs.status == '0'"
    assert not run.docker_log.exists()

    # the result is written anyway
    write = _named_step("Write the result file")
    done = run.run(
        write, TRUSTED=trusted, RESULTS=results, REPO_STATUS="1",
        CHECK_STATUS="", BUILD_STATUS="",
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    result = Path(results) / "result.json"
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["repo_status"] == 1
    assert data["repo_files"] == [Path(forbidden).name]
    assert data["check_status"] == 3 and data["check"] is None

    # and the trusted renderer turns it into the plain-language comment
    comment = tmp_path / "comment.md"
    rendered = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable, "-I", str(TEMPLATE / "scripts" / "render-comment.py"),
            "--in", str(result), "--out", str(comment),
            "--preview-result", "failure", "--repo", "example/site",
            "--workflow", "Preview",
        ],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert rendered.returncode == 0, rendered.stderr
    text = comment.read_text(encoding="utf-8")
    assert "## A file that must not be committed is in this change" in text
    assert f"`{Path(forbidden).name}`" in text
    assert "SECRET" not in text  # file contents are never read

    # the job fails only afterwards, and that step is the last one
    steps_all = yaml.safe_load(preview_text())["jobs"]["preview"]["steps"]
    assert steps_all[-1]["name"].startswith("Fail the job")
    assert "steps.repo.outputs.status != '0'" in steps_all[-1]["if"]


def test_a_clean_repository_check_records_status_zero(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    run = _Run(tmp_path, None)
    steps = _steps_by_id()
    assert run.run(steps["trust"]).returncode == 0
    trusted, results = run.outputs["trust.dir"], run.outputs["trust.results"]
    request.addfinalizer(
        lambda: subprocess.run(["chmod", "-R", "u+w", trusted], check=False)  # noqa: S603, S607 - cleanup
    )
    done = run.run(steps["repo"], TRUSTED=trusted, RESULTS=results)
    assert done.returncode == 0, done.stderr
    assert run.outputs["repo.status"] == "0"


# ---- the engine source is the upstream repository (#694) ---


def engine_source_problems(text: str) -> list[str]:
    """Every engine checkout and reusable call names the upstream literal."""
    found: list[str] = []
    jobs = yaml.safe_load(text)["jobs"]
    seen = 0
    for name, job in jobs.items():
        if "with" in job and "engine_repo" in job["with"]:
            seen += 1
            if job["with"]["engine_repo"] != UPSTREAM_REPO:
                found.append(f"{name}: engine_repo is not the upstream literal")
        for step in job.get("steps", []):
            args = step.get("with", {})
            if "repository" in args:
                seen += 1
                if args["repository"] != UPSTREAM_REPO:
                    found.append(f"{name}: checkout repository is not the literal")
    if seen != len(DEPLOY_JOBS):
        found.append(f"expected 4 engine sources, found {seen}")
    return found


def test_the_engine_source_is_the_upstream_literal_in_every_deploy_job() -> None:
    assert engine_source_problems(deploy_text()) == []


def test_the_engine_source_check_fails_on_a_changed_source() -> None:
    text = deploy_text()
    lock_value = "${{ needs.validate.outputs.engine_repo }}"
    one_call = text.replace(
        f"engine_repo: {UPSTREAM_REPO}", f"engine_repo: {lock_value}", 1
    )
    assert engine_source_problems(one_call)
    one_checkout = text.replace(
        f"repository: {UPSTREAM_REPO}", f"repository: {lock_value}", 1
    )
    assert engine_source_problems(one_checkout)
    other = text.replace(f"repository: {UPSTREAM_REPO}", "repository: other/engine", 1)
    assert engine_source_problems(other)


REPO_LINE = "ENGINE_REPO=ChiefGyk3D/Reach-Around"
IMAGE_HEAD = "ghcr.io/chiefgyk3d/reach-around:"


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        (REPO_LINE, "ENGINE_REPO=other/engine", "ENGINE_REPO"),
        (REPO_LINE, "ENGINE_REPO=chiefgyk3d/reach-around", "ENGINE_REPO"),
        (REPO_LINE, "ENGINE_REPO=ChiefGyk3D/Reach-Around-fork", "ENGINE_REPO"),
        (IMAGE_HEAD, "ghcr.io/other/reach-around:", "ENGINE_IMAGE"),
        (IMAGE_HEAD, "docker.io/chiefgyk3d/reach-around:", "ENGINE_IMAGE"),
        (IMAGE_HEAD, "ghcr.io/chiefgyk3d/reach-around-x:", "ENGINE_IMAGE"),
    ],
)
def test_read_lock_requires_the_upstream_source(
    tmp_path: Path, old: str, new: str, key: str
) -> None:
    root = _copy_template(tmp_path)
    lock = root / "engine.lock"
    lock.write_text(
        lock.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )
    assert _script(root, "read-lock.sh", key).returncode != 0
    # --verify applies the same literal to a value handed in by another script
    value = new.split("=", 1)[1] if "=" in new else new
    if key == "ENGINE_REPO":
        assert _script(root, "read-lock.sh", "--verify", key, value).returncode != 0


def test_read_lock_verify_pins_the_upstream_repository(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    ok = _script(root, "read-lock.sh", "--verify", "ENGINE_REPO", UPSTREAM_REPO)
    assert ok.returncode == 0, ok.stderr
    for bad in ("other/engine", "ChiefGyk3D/other", "chiefgyk3d/reach-around"):
        res = _script(root, "read-lock.sh", "--verify", "ENGINE_REPO", bad)
        assert res.returncode != 0, bad


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed (runs on CI)")
def test_a_release_naming_another_repository_is_refused_even_when_consistent(
    tmp_path: Path,
) -> None:
    """Lock and manifest agree on another repository: still refused."""
    root = _copy_template(tmp_path)
    manifest = root / "engine.release.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["repo"] = "other/engine"
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    lock = root / "engine.lock"
    lock.write_text(
        lock.read_text(encoding="utf-8").replace(
            "ENGINE_REPO=ChiefGyk3D/Reach-Around", "ENGINE_REPO=other/engine"
        ),
        encoding="utf-8",
    )
    res = _script(root, "read-lock.sh", "--release")
    assert res.returncode != 0
    assert "ENGINE_REPO" in res.stderr or "repo" in res.stderr


# ---- the staging and production settings are distinct (#694) ----------------

TIER_ENV = {
    "STAGING_WORKER_NAME": "my-links-clicks-staging",
    "PRODUCTION_WORKER_NAME": "my-links-clicks",
    "STAGING_PAGES_PROJECT": "my-links-staging",
    "PRODUCTION_PAGES_PROJECT": "my-links",
    "STAGING_D1_DATABASE_ID": "11111111-1111-1111-1111-111111111111",
    "PRODUCTION_D1_DATABASE_ID": "22222222-2222-2222-2222-222222222222",
    "STAGING_RATELIMIT_NAMESPACE_ID": "1001",
    "PRODUCTION_RATELIMIT_NAMESPACE_ID": "1002",
    "STAGING_SITE_HOST": "staging.links.example.invalid",
    "PRODUCTION_SITE_HOST": "links.example.invalid",
    "STAGING_ZONE_NAME": "example.invalid",
    "PRODUCTION_ZONE_NAME": "example.invalid",
    "DOPPLER_IDENTITY_ID_STAGING": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "DOPPLER_IDENTITY_ID_PRODUCTION": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "CLOUDFLARE_ACCOUNT_ID": "0" * 32,
    "DOPPLER_PROJECT": "my-links",
    "ALLOW_STAGING_DEPLOY": "true",
    "ALLOW_PRODUCTION_DEPLOY": "true",
}
TIER_STEP = "Check the tier settings are distinct"
PROD_D1 = TIER_ENV["PRODUCTION_D1_DATABASE_ID"]
PRODUCTION_SETTINGS = (
    "PRODUCTION_WORKER_NAME",
    "PRODUCTION_PAGES_PROJECT",
    "PRODUCTION_D1_DATABASE_ID",
    "PRODUCTION_RATELIMIT_NAMESPACE_ID",
    "PRODUCTION_SITE_HOST",
    "PRODUCTION_ZONE_NAME",
    "DOPPLER_IDENTITY_ID_PRODUCTION",
    "ALLOW_PRODUCTION_DEPLOY",
)
PRODUCTION_OFF = dict.fromkeys(PRODUCTION_SETTINGS, "")
STAGING_REQUIRED = (
    "STAGING_WORKER_NAME",
    "STAGING_PAGES_PROJECT",
    "STAGING_D1_DATABASE_ID",
    "STAGING_RATELIMIT_NAMESPACE_ID",
    "STAGING_SITE_HOST",
    "DOPPLER_IDENTITY_ID_STAGING",
    "CLOUDFLARE_ACCOUNT_ID",
    "DOPPLER_PROJECT",
)


def _tiers(
    root: Path, change: Mapping[str, str], out: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], **TIER_ENV, **change}
    if out is not None:
        env["GITHUB_OUTPUT"] = str(out)
    return subprocess.run(  # noqa: S603 - fixed argv, a copy of the template
        [BASH, str(root / "scripts" / "check-tiers.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _outputs(path: Path) -> dict[str, str]:
    pairs = (ln.split("=", 1) for ln in path.read_text(encoding="utf-8").splitlines())
    return {k: v for k, v in pairs}


def test_distinct_tier_settings_pass(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    assert _tiers(root, {}).returncode == 0
    # production off: none of its settings is set, nothing to compare
    assert _tiers(root, PRODUCTION_OFF).returncode == 0
    # the zone name is optional in both tiers
    assert (
        _tiers(root, {"STAGING_ZONE_NAME": "", "PRODUCTION_ZONE_NAME": ""}).returncode
        == 0
    )


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("d1", {"STAGING_D1_DATABASE_ID": TIER_ENV["PRODUCTION_D1_DATABASE_ID"]}),
        (
            "d1 case",
            {"STAGING_D1_DATABASE_ID": TIER_ENV["PRODUCTION_D1_DATABASE_ID"].upper()},
        ),
        ("ratelimit", {"STAGING_RATELIMIT_NAMESPACE_ID": "1002"}),
        ("worker (and its namespaces)", {"STAGING_WORKER_NAME": "my-links-clicks"}),
        ("pages", {"STAGING_PAGES_PROJECT": "my-links"}),
        ("pages case", {"STAGING_PAGES_PROJECT": "My-Links"}),
        ("host (route)", {"STAGING_SITE_HOST": "links.example.invalid"}),
        ("host case", {"STAGING_SITE_HOST": "LINKS.example.invalid"}),
        ("host equals the production zone", {"STAGING_SITE_HOST": "example.invalid"}),
        (
            "production host is the staging zone",
            {"STAGING_ZONE_NAME": "links.example.invalid"},
        ),
        (
            "identity",
            {"DOPPLER_IDENTITY_ID_STAGING": TIER_ENV["DOPPLER_IDENTITY_ID_PRODUCTION"]},
        ),
        ("second line", {"STAGING_D1_DATABASE_ID": "x\n" + PROD_D1}),
        ("padded", {"STAGING_SITE_HOST": " staging.links.example.invalid"}),
    ],
)
def test_each_tier_setting_pair_must_differ(
    tmp_path: Path, label: str, change: dict[str, str]
) -> None:
    root = _copy_template(tmp_path)
    result = _tiers(root, change)
    assert result.returncode != 0, label
    assert "::error::" in result.stdout, label
    # only the name of a setting is printed, never a value
    for value in change.values():
        assert value.strip() not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("trailing dot", {"STAGING_SITE_HOST": "links.example.invalid."}),
        (
            "upper case and trailing dot",
            {"STAGING_SITE_HOST": "LINKS.Example.invalid."},
        ),
        ("default https port", {"STAGING_SITE_HOST": "links.example.invalid:443"}),
        ("default http port", {"STAGING_SITE_HOST": "links.example.invalid:80"}),
        ("port and dot", {"STAGING_SITE_HOST": "links.example.invalid.:443"}),
        (
            "host is the other zone, with a dot",
            {"STAGING_SITE_HOST": "EXAMPLE.invalid."},
        ),
        (
            "production host equals the staging zone, with a dot",
            {"STAGING_ZONE_NAME": "LINKS.example.invalid."},
        ),
        ("leading zeros", {"STAGING_RATELIMIT_NAMESPACE_ID": "01002"}),
        ("many leading zeros", {"STAGING_RATELIMIT_NAMESPACE_ID": "0000001002"}),
    ],
)
def test_equivalent_forms_of_a_shared_value_are_refused(
    tmp_path: Path, label: str, change: dict[str, str]
) -> None:
    root = _copy_template(tmp_path)
    result = _tiers(root, change)
    assert result.returncode != 0, label
    assert "::error::" in result.stdout, label


def test_an_international_name_equals_its_punycode_form_only(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    puny = "xn--bcher-kva.example.invalid"
    both = {"STAGING_SITE_HOST": puny, "PRODUCTION_SITE_HOST": puny.upper() + "."}
    assert _tiers(root, both).returncode != 0
    # the Unicode spelling is refused, not guessed at
    unicode_host = {"STAGING_SITE_HOST": "bücher.example.invalid"}
    result = _tiers(root, unicode_host)
    assert result.returncode != 0
    assert "STAGING_SITE_HOST must be ASCII" in result.stdout
    # a different punycode name is a different host
    other = {"STAGING_SITE_HOST": puny, "PRODUCTION_SITE_HOST": "xn--fiq228c.invalid"}
    assert _tiers(root, other).returncode == 0


@pytest.mark.parametrize(
    "value",
    ["１００２", "100٢", "1e3", "+1002", "-1", "0x3ea", "1 2", "1" * 13],
)
def test_a_namespace_id_is_ascii_digits_only(tmp_path: Path, value: str) -> None:
    root = _copy_template(tmp_path)
    result = _tiers(root, {"PRODUCTION_RATELIMIT_NAMESPACE_ID": value})
    assert result.returncode != 0
    assert "PRODUCTION_RATELIMIT_NAMESPACE_ID must be ASCII digits" in result.stdout


@pytest.mark.parametrize(
    "value",
    [
        "links.example.invalid:8443",
        "links.example.invalid/",
        "http://links.example",
        "-links.example.invalid",
        "links..example.invalid",
        "links.example.invalid..",
        "*.example.invalid",
        "[::1]",
        "links_x.example.invalid",
        "a" * 64 + ".example",
    ],
)
def test_a_host_must_be_a_plain_name(tmp_path: Path, value: str) -> None:
    root = _copy_template(tmp_path)
    result = _tiers(root, {"PRODUCTION_SITE_HOST": value})
    assert result.returncode != 0
    assert "PRODUCTION_SITE_HOST" in result.stdout


# A tier whose required setting is missing fails; it is never "distinct".
@pytest.mark.parametrize("name", STAGING_REQUIRED)
def test_a_missing_staging_setting_fails(tmp_path: Path, name: str) -> None:
    root = _copy_template(tmp_path)
    for production in (TIER_ENV, PRODUCTION_OFF):
        result = _tiers(root, {**production, name: ""})
        assert result.returncode != 0, name
        assert f"{name} is" in result.stdout, name


@pytest.mark.parametrize("name", PRODUCTION_SETTINGS[:5] + PRODUCTION_SETTINGS[6:7])
def test_a_missing_production_setting_fails_once_production_is_set_up(
    tmp_path: Path, name: str
) -> None:
    root = _copy_template(tmp_path)
    # one other production setting is set, or the switch is on: production is
    # being set up, so every required production setting must be there
    assert _tiers(root, {name: ""}).returncode != 0, name
    only_one = {**PRODUCTION_OFF, "PRODUCTION_PAGES_PROJECT": "other-pages"}
    result = _tiers(root, only_one)
    assert result.returncode != 0
    assert "is required when production is set up" in result.stdout
    # the switch alone, with nothing else configured
    result = _tiers(root, {**PRODUCTION_OFF, "ALLOW_PRODUCTION_DEPLOY": "true"})
    assert result.returncode != 0
    assert "is required when production is set up" in result.stdout


def test_a_switch_is_on_only_when_exactly_true(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    for value in ("TRUE", "True", "1", "yes", " ", "false", ""):
        out = tmp_path / f"out-{abs(hash(value))}"
        change = {**PRODUCTION_OFF, "ALLOW_STAGING_DEPLOY": value.strip()}
        assert _tiers(root, change, out).returncode == 0, value
        assert _outputs(out)["allow_staging_deploy"] == "false", value
    out = tmp_path / "out-true"
    assert _tiers(root, {}, out).returncode == 0
    assert _outputs(out)["allow_staging_deploy"] == "true"
    assert _outputs(out)["allow_production_deploy"] == "true"


def test_the_outputs_are_the_normalized_values(tmp_path: Path) -> None:
    root = _copy_template(tmp_path)
    out = tmp_path / "github-output"
    change = {
        "STAGING_SITE_HOST": "Staging.Links.Example.invalid.:443",
        "STAGING_RATELIMIT_NAMESPACE_ID": "0001001",
        "STAGING_PAGES_PROJECT": "My-Links-Staging",
    }
    assert _tiers(root, change, out).returncode == 0
    got = _outputs(out)
    assert set(got) == set(CHECKED_OUTPUTS)
    assert got["staging_site_host"] == "staging.links.example.invalid"
    assert got["staging_ratelimit_namespace_id"] == "1001"
    assert got["staging_pages_project"] == "my-links-staging"
    assert got["production_ratelimit_namespace_id"] == "1002"
    assert got["doppler_project"] == "my-links"
    # a failing check writes nothing for the deploy jobs to read
    bad = tmp_path / "bad-output"
    assert _tiers(root, {"STAGING_D1_DATABASE_ID": PROD_D1}, bad).returncode != 0
    assert not bad.exists() or bad.read_text(encoding="utf-8") == ""


def test_the_tier_check_runs_in_validate_with_every_value_it_reads() -> None:
    step = _step("validate", TIER_STEP)
    assert step["run"] == "scripts/check-tiers.sh"
    assert step["id"] == "tiers"
    assert set(step["env"]) == set(TIER_ENV)
    for name, value in step["env"].items():
        assert value == "${{ vars." + name + " }}", name
    validate_steps = yaml.safe_load(deploy_text())["jobs"]["validate"]["steps"]
    names = [s.get("name") for s in validate_steps]
    # before the build, so a failure stops before any deploy job starts
    assert names.index(TIER_STEP) < names.index("Build the site with the pinned engine")
    # the plain-name checks read the checked values, not the raw variables
    plain = _step("validate", NAMES_STEP)
    for value in plain["env"].values():
        assert str(value).startswith("${{ steps.tiers.outputs."), value


def binding_problems(text: str) -> list[str]:
    """The deploy jobs read the values the validate job checked, and nothing
    else: no repository variable is read again after the check."""
    jobs = yaml.safe_load(text)["jobs"]
    found: list[str] = []
    outputs = jobs["validate"]["outputs"]
    for key in CHECKED_OUTPUTS:
        want = "${{ steps.tiers.outputs." + key + " }}"
        if outputs.get(key) != want:
            found.append(f"validate output {key} is not the checked value")
    allowed = {"engine_ref", *CHECKED_OUTPUTS}
    for name in DEPLOY_JOBS:
        job_text = yaml.safe_dump(jobs[name])
        if "vars." in job_text:
            found.append(f"{name}: reads a repository variable")
        if "github.event.repository" in job_text or "secrets." in job_text:
            found.append(f"{name}: reads something the check did not")
        used = set(re.findall(r"needs\.validate\.outputs\.([A-Za-z0-9_]+)", job_text))
        for key in used - allowed:
            found.append(f"{name}: reads an unchecked output {key}")
        if "validate" not in jobs[name]["needs"]:
            found.append(f"{name}: does not need validate")
    return found


def test_the_deploy_jobs_read_only_the_checked_outputs() -> None:
    assert binding_problems(deploy_text()) == []
    # each job names the values of its own tier from the check
    jobs = yaml.safe_load(deploy_text())["jobs"]
    for name in DEPLOY_JOBS:
        used = set(
            re.findall(
                r"needs\.validate\.outputs\.([A-Za-z0-9_]+)",
                yaml.safe_dump(jobs[name]),
            )
        )
        assert "cloudflare_account_id" in used or name.startswith("worker"), name
        assert used, name


def test_the_binding_guard_bites() -> None:
    good = deploy_text()
    worker = "worker_name: ${{ needs.validate.outputs.staging_worker_name }}"
    assert worker in good
    # a deploy job that reads the raw variable again
    assert binding_problems(
        good.replace(worker, "worker_name: ${{ vars.STAGING_WORKER_NAME }}")
    )
    page = "PAGES_PROJECT: ${{ needs.validate.outputs.production_pages_project }}"
    assert page in good
    assert binding_problems(
        good.replace(page, "PAGES_PROJECT: ${{ vars.PRODUCTION_PAGES_PROJECT }}")
    )
    # the switch re-read in a condition
    cond = "&& needs.validate.outputs.allow_production_deploy == 'true'"
    assert cond in good
    assert binding_problems(
        good.replace(cond, "&& vars.ALLOW_PRODUCTION_DEPLOY == 'true'")
    )
    # an output that is not the checked value
    out = (
        "      staging_d1_database_id:"
        " ${{ steps.tiers.outputs.staging_d1_database_id }}"
    )
    assert out in good
    assert binding_problems(
        good.replace(
            out, "      staging_d1_database_id: ${{ vars.STAGING_D1_DATABASE_ID }}"
        )
    )
    # an output the check never produced
    assert binding_problems(
        good.replace(worker, "worker_name: ${{ needs.validate.outputs.worker }}")
    )


def test_each_tier_uses_only_its_own_identity_and_environment() -> None:
    jobs = yaml.safe_load(deploy_text())["jobs"]
    for tier, other in (("staging", "PRODUCTION"), ("production", "STAGING")):
        for name in (f"worker-{tier}", f"pages-{tier}"):
            job_text = yaml.safe_dump(jobs[name])
            assert f"doppler_identity_id_{other.lower()}" not in job_text, name
            assert f"doppler_identity_id_{tier}" in job_text, name


ENGINE_TAG_GRAMMAR = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$")


def test_the_engine_call_comment_is_the_release_tag_of_the_lock() -> None:
    """The grammar read-lock.sh enforces (stable or -rc.N), not the broader
    workflow pin comment grammar."""
    lock = (TEMPLATE / "engine.lock").read_text(encoding="utf-8")
    found = re.search(r"^ENGINE_REF=[0-9a-f]{40} # (\S+)$", lock, re.M)
    assert found
    assert ENGINE_TAG_GRAMMAR.match(found.group(1))
    text = deploy_text()
    calls = re.findall(rf"uses: {re.escape(CALLEE_PATH)}@[0-9a-f]{{40}} # (\S+)", text)
    assert len(calls) == 2
    assert set(calls) == {found.group(1)}


# --- Daybreak round 3 findings (issue #694) -----------------------------------
# Exact per-job binding of every validated value, read from the parsed YAML.

OUTPUT_REF = re.compile(r"^\$\{\{ needs\.validate\.outputs\.([a-z0-9_]+) \}\}$")
Binding = tuple[str, str | None]
Key = tuple[str, str, str]

# (job, where, input) -> ("output", name) or ("literal", value). The staging
# Pages job reads the production Pages project only to refuse it.
STEP_LABELS = (
    "Fetch the Worker source",
    "Check configuration",
    "Fetch Cloudflare credentials",
    "Publish the site",
)


def expected_bindings() -> dict[Key, Binding]:
    out: dict[Key, Binding] = {}
    for tier, config in (("staging", "dev"), ("production", "prd")):
        w = f"worker-{tier}"
        for key, value in (
            ("environment", ("literal", tier)),
            ("engine_repo", ("literal", UPSTREAM_REPO)),
            ("engine_ref", ("output", "engine_ref")),
            ("manifest_artifact", ("literal", "site-manifest")),
            ("manifest_file", ("literal", "manifest.json")),
            ("site_artifact", ("literal", "site-out")),
            ("doppler_project", ("output", "doppler_project")),
            ("doppler_config", ("literal", config)),
            ("cloudflare_account_id", ("output", "cloudflare_account_id")),
            ("worker_name", ("output", f"{tier}_worker_name")),
            ("d1_database_id", ("output", f"{tier}_d1_database_id")),
            ("ratelimit_namespace_id", ("output", f"{tier}_ratelimit_namespace_id")),
            ("site_host", ("output", f"{tier}_site_host")),
            ("zone_name", ("output", f"{tier}_zone_name")),
            ("doppler_identity_id", ("output", f"doppler_identity_id_{tier}")),
        ):
            out[(w, "with", key)] = value
        p = f"pages-{tier}"
        for label, where, value in (
            ("Fetch the Worker source", "with.ref", ("output", "engine_ref")),
            ("Fetch the Worker source", "with.repository", ("literal", UPSTREAM_REPO)),
            ("Fetch the Worker source", "with.path", ("literal", "engine")),
            ("Fetch the Worker source", "with.sparse-checkout", ("literal", "worker")),
            (
                "Fetch the Worker source",
                "with.persist-credentials",
                ("literal", "False"),
            ),
            (
                "Publish the site",
                "env.CLOUDFLARE_API_TOKEN",
                (
                    "literal",
                    "${{ fromJSON(steps.cf_doppler.outputs.secrets)"
                    ".CLOUDFLARE_KEY_VALUE }}",
                ),
            ),
            (
                "Check configuration",
                "env.CLOUDFLARE_ACCOUNT_ID",
                ("output", "cloudflare_account_id"),
            ),
            (
                "Check configuration",
                "env.PAGES_PROJECT",
                ("output", f"{tier}_pages_project"),
            ),
            (
                "Fetch Cloudflare credentials",
                "with.project",
                ("output", "doppler_project"),
            ),
            ("Fetch Cloudflare credentials", "with.config", ("literal", config)),
            ("Fetch Cloudflare credentials", "with.export", ("literal", "outputs")),
            (
                "Fetch Cloudflare credentials",
                "with.names",
                ("literal", "CLOUDFLARE_KEY_VALUE"),
            ),
            ("Fetch Cloudflare credentials", "with.required", ("literal", "true")),
            (
                "Fetch Cloudflare credentials",
                "with.identity-id",
                ("output", f"doppler_identity_id_{tier}"),
            ),
            (
                "Publish the site",
                "env.CLOUDFLARE_ACCOUNT_ID",
                ("output", "cloudflare_account_id"),
            ),
            (
                "Publish the site",
                "env.PAGES_PROJECT",
                ("output", f"{tier}_pages_project"),
            ),
        ):
            out[(p, label, where)] = value
    out[("pages-staging", "Check configuration", "env.PRODUCTION_PAGES_PROJECT")] = (
        "output",
        "production_pages_project",
    )
    return out


def actual_bindings(text: str) -> dict[Key, Binding]:
    """Every input of the four deploy jobs, read from the parsed workflow.

    Worker jobs: every key of `with`. Pages jobs: every `with` or `env` key of
    the four named steps (a step that is not one of them is its own finding).
    """
    jobs = yaml.safe_load(text)["jobs"]
    found: dict[Key, Binding] = {}

    def classify(value: Any) -> Binding:
        match = OUTPUT_REF.match(str(value))
        return ("output", match.group(1)) if match else ("literal", str(value))

    for tier in ("staging", "production"):
        for key, value in jobs[f"worker-{tier}"]["with"].items():
            found[(f"worker-{tier}", "with", key)] = classify(value)
        for step in jobs[f"pages-{tier}"]["steps"]:
            name = str(step.get("name", ""))
            label = next((n for n in STEP_LABELS if name.startswith(n)), None)
            for where in ("with", "env"):
                for key, value in (step.get(where) or {}).items():
                    if label is None:
                        if "needs.validate" in str(value):
                            found[(f"pages-{tier}", name, f"{where}.{key}")] = classify(
                                value
                            )
                        continue
                    if where == "env" and key == "WRANGLER_SEND_METRICS":
                        continue
                    found[(f"pages-{tier}", label, f"{where}.{key}")] = classify(value)
    return found


def binding_map_problems(text: str) -> list[str]:
    expected = expected_bindings()
    actual = actual_bindings(text)
    found: list[str] = []
    for key in sorted(set(actual) - set(expected)):
        found.append(f"{key}: an input that is not mapped (add it to the map)")
    for key in sorted(set(expected) - set(actual)):
        found.append(f"{key}: a mapped input that is missing")
    for key in sorted(set(expected) & set(actual)):
        if expected[key] != actual[key]:
            found.append(f"{key}: reads {actual[key]}, must read {expected[key]}")
    # the tier rule over every output a job reads, apart from the explicit map
    jobs = yaml.safe_load(text)["jobs"]
    for tier, other in (("staging", "production"), ("production", "staging")):
        for job in (f"worker-{tier}", f"pages-{tier}"):
            for ref in re.findall(
                r"needs\.validate\.outputs\.([a-z0-9_]+)", yaml.safe_dump(jobs[job])
            ):
                if re.search(rf"(^{other}_|_{other}$)", ref) and not (
                    job == "pages-staging" and ref == "production_pages_project"
                ):
                    found.append(f"{job}: reads the {other} output {ref}")
    return found


def test_each_deploy_input_reads_only_its_own_tiers_output() -> None:
    assert binding_map_problems(deploy_text()) == []
    # every validate output is either mapped or one of the two switches
    outputs = set(yaml.safe_load(deploy_text())["jobs"]["validate"]["outputs"])
    used = {v[1] for v in expected_bindings().values() if v[0] == "output"}
    for name in outputs - used:
        assert name.startswith("allow_"), f"validate output {name} is never used"


def test_the_per_tier_binding_guard_bites() -> None:
    good = deploy_text()
    swaps = {
        "worker_name": ("production_worker_name", "staging_worker_name"),
        "d1_database_id": ("production_d1_database_id", "staging_d1_database_id"),
        "ratelimit_namespace_id": (
            "production_ratelimit_namespace_id",
            "staging_ratelimit_namespace_id",
        ),
        "site_host": ("production_site_host", "staging_site_host"),
        "zone_name": ("production_zone_name", "staging_zone_name"),
        "doppler_identity_id": (
            "doppler_identity_id_production",
            "doppler_identity_id_staging",
        ),
    }
    for key, (production, staging) in swaps.items():
        # each job reads the other tier's output, in both directions
        for want, wrong in ((production, staging), (staging, production)):
            line = f"      {key}: ${{{{ needs.validate.outputs.{want} }}}}"
            assert good.count(line) == 1, line
            bad = good.replace(
                line, f"      {key}: ${{{{ needs.validate.outputs.{wrong} }}}}"
            )
            assert binding_map_problems(bad), (key, want, wrong)
    # the Pages project, in the check step and in the publish step
    for tier, other in (("production", "staging"), ("staging", "production")):
        line = f"PAGES_PROJECT: ${{{{ needs.validate.outputs.{tier}_pages_project }}}}"
        assert good.count(line) >= 2
        bad = good.replace(
            line,
            f"PAGES_PROJECT: ${{{{ needs.validate.outputs.{other}_pages_project }}}}",
        )
        assert binding_map_problems(bad), tier
    # the secret-manager step of a Pages job
    for tier, other in (("production", "staging"), ("staging", "production")):
        line = (
            f"identity-id: ${{{{ needs.validate.outputs.doppler_identity_id_{tier} }}}}"
        )
        assert line in good
        assert binding_map_problems(good.replace(line, line.replace(tier, other)))
    # a literal that names the tier is bound too
    assert binding_map_problems(
        good.replace("environment: production", "environment: staging")
    )
    assert binding_map_problems(
        good.replace("doppler_config: prd", "doppler_config: dev")
    )


def test_a_new_deploy_input_fails_until_it_is_mapped() -> None:
    good = deploy_text()
    anchor = "      zone_name: ${{ needs.validate.outputs.production_zone_name }}\n"
    assert anchor in good
    extra = anchor + "      new_input: literal\n"
    problems = binding_map_problems(good.replace(anchor, extra))
    assert any("not mapped" in p and "new_input" in p for p in problems), problems
    # a new validated value in a Pages step
    env = (
        '          WRANGLER_SEND_METRICS: "false"\n'
        "        run: >-\n"
        "          npx --no-install wrangler pages deploy ../../site-out\n"
        '          --project-name "$PAGES_PROJECT"\n'
        "          --branch main"
    )
    assert env in good
    new_env = (
        "          NEW_VALUE: ${{ needs.validate.outputs.production_site_host }}\n"
    )
    added = env.replace(
        "          WRANGLER_SEND_METRICS",
        new_env + "          WRANGLER_SEND_METRICS",
        1,
    )
    problems = binding_map_problems(good.replace(env, added))
    assert any("not mapped" in p and "NEW_VALUE" in p for p in problems), problems
    # a new step that reads a validated value
    step = (
        "      - name: Publish the site to Pages\n"
        "        working-directory: engine/worker\n"
        "        env:\n"
        "          CLOUDFLARE_API_TOKEN"
    )
    assert good.count(step) == 2
    sneaky = (
        "      - name: Extra\n        run: echo\n        env:\n"
        "          X: ${{ needs.validate.outputs.staging_site_host }}\n" + step
    )
    assert any(
        "Extra" in p for p in binding_map_problems(good.replace(step, sneaky, 1))
    )


def test_the_docs_say_exactly_when_staging_runs() -> None:
    root = ROOT
    for path in (
        root / "docs" / "deploy.md",
        root / "docs" / "instance-repos.md",
        TEMPLATE / "README.md",
        TEMPLATE / ".github" / "workflows" / "deploy.yml",
    ):
        text = path.read_text(encoding="utf-8")
        assert "on every push to `main`" not in text, path
        assert "every push to `main`, and" not in text, path
        assert "runs on every push" not in text, path
    deploy_doc = (root / "docs" / "deploy.md").read_text(encoding="utf-8")
    start = deploy_doc.index("Staging runs")
    assert "ALLOW_STAGING_DEPLOY" in deploy_doc[start : start + 400]
