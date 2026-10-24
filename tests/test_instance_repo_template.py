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
        for step in job["steps"]:
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
    downloads = {
        s["with"]["name"]: s["with"]["path"]
        for s in jobs["deploy"]["steps"]
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    }
    assert downloads == {"site-out": "site-out", "site-manifest": "manifest-in"}
    pages = [
        s
        for s in jobs["deploy"]["steps"]
        if s.get("name") == "Publish the site to Pages"
    ]
    assert len(pages) == 1
    assert "manifest" not in pages[0]["run"]


def test_the_manifest_reader_step_has_no_secrets() -> None:
    steps = _deploy_jobs()["deploy"]["steps"]
    readers = [s for s in steps if "manifest-in" in s.get("run", "")]
    assert len(readers) == 1
    reader = readers[0]
    text = yaml.safe_dump(reader)
    assert "secrets." not in text
    assert CF_ENV_VAR not in text
    run = reader["run"]
    assert "jq " in run or "python3 -I" in run
    assert "-mindepth 1" in run
    assert "! -L" in run


def test_the_manifest_guard_bites() -> None:
    good = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    assert manifest_violations(good) == []
    old_read = good.replace("manifest-in/manifest.json", "site-out/manifest.json")
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
