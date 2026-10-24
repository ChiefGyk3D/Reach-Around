# SPDX-License-Identifier: Apache-2.0
"""The private-instance template (examples/instance-repo): its config builds
and its workflows keep the hardening docs/instance-repos.md promises."""

from __future__ import annotations

import re
import shutil
import subprocess
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

PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S.*?)\s*$", re.MULTILINE)
CF_ENV_VAR = "CLOUDFLARE_API_TOKEN"
DIGEST_IMAGE = re.compile(r"^ENGINE_IMAGE=\S+:[\w.-]+@sha256:[0-9a-f]{64}$", re.M)


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


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
        if job.get("permissions", {}) != {"contents": "read"}:
            found.append(f"{job_name}: permissions are not exactly contents: read")
        if "env" in job:
            found.append(f"{job_name}: job-level env")
        if job.get("environment") not in (None, "production"):
            found.append(f"{job_name}: unexpected environment")
        for step in job["steps"]:
            label = f"{job_name}/{step.get('name', step.get('uses'))}"
            run = step.get("run", "")
            if "${{" in run:
                found.append(f"{label}: expression inside run")
            step_text = yaml.safe_dump(step)
            is_wrangler = "wrangler" in run and "--version" not in run
            token_step = (
                is_wrangler
                and job_name == "deploy"
                and CF_ENV_VAR in step.get("env", {})
            )
            if "secrets." in step_text and not token_step:
                found.append(f"{label}: secret outside a wrangler step")
            if CF_ENV_VAR in step_text and not token_step:
                found.append(f"{label}: token name outside a wrangler step")
    return found


def test_the_template_files_exist() -> None:
    for rel in (
        "README.md",
        "site.yaml",
        "engine.lock",
        "renovate.json",
        ".gitignore",
        "media/.gitkeep",
        "docs/EDITING.md",
        "scripts/deploy-local.sh",
        "scripts/validate.sh",
        ".github/workflows/deploy.yml",
        ".github/workflows/preview.yml",
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


def test_both_workflows_are_collected() -> None:
    assert [p.name for p in workflow_files()] == ["deploy.yml", "preview.yml"]


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_workflow_hardening(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8")) == []


def test_deploy_workflow_shape() -> None:
    data = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text(encoding="utf-8"))
    assert data[True] == {"push": {"branches": ["main"]}, "workflow_dispatch": None}
    jobs = data["jobs"]
    assert "environment" not in jobs["validate"]
    assert jobs["deploy"]["environment"] == "production"
    assert jobs["deploy"]["needs"] == "validate"
    with_token = [
        s["name"] for s in jobs["deploy"]["steps"] if CF_ENV_VAR in s.get("env", {})
    ]
    assert with_token == [
        "Apply D1 migrations",
        "Deploy the Worker",
        "Publish the site to Pages",
    ]


def test_preview_workflow_deploys_nothing() -> None:
    text = (WORKFLOWS / "preview.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert set(data[True]) == {"pull_request"}
    assert "wrangler" not in text
    assert data["jobs"]
    assert all("environment" not in job for job in data["jobs"].values())
    assert "secrets." not in text


def test_the_guard_bites() -> None:
    good = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
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
    }
    for label, mutated in mutations.items():
        assert mutated != good, label
        assert violations(mutated), label


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
        ("0d1f96a0268cf99890c7d5ee7b9cd6edf7d4a207", "main", "ENGINE_REF"),
        ("ENGINE_REPO=ChiefGyk3D/Reach-Around", "ENGINE_REPO=a;b", "ENGINE_REPO"),
    ],
)
def test_read_lock_refuses_bad_shapes(
    tmp_path: Path, old: str, new: str, key: str
) -> None:
    root = _copy_template(tmp_path)
    lock = root / "engine.lock"
    text = lock.read_text(encoding="utf-8")
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
