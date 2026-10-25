# SPDX-License-Identifier: Apache-2.0
"""The private-instance template (examples/instance-repo): its config builds
and its workflows keep the hardening docs/instance-repos.md promises."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import sys
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


# Every job holds contents: read, except these two: the comment job is the only
# one allowed to write to a pull request, and the gate needs nothing at all.
JOB_PERMISSIONS: dict[str, dict[str, str]] = {
    "comment": {"contents": "read", "pull-requests": "write"},
    "ci-green": {},
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
        if job.get("environment") not in (None, "production"):
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
        "scripts/check-config.sh",
        "scripts/render-comment.py",
        "scripts/write-result.py",
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


# --- pull request previews and the plain-language comment (issue #157) --------


def preview_text() -> str:
    return (WORKFLOWS / "preview.yml").read_text(encoding="utf-8")


def code_only(text: str) -> str:
    """The file without comment lines, for text searches."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


COMMENT_PERMISSIONS = {"contents": "read", "pull-requests": "write"}
UNTRUSTED_SCRIPT = re.compile(r"(?<!trusted/)scripts/")


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
    if jobs["preview"].get("timeout-minutes") != 10:
        found.append("preview: timeout-minutes is not 10")
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
    if "environment" in jobs["preview"]:
        found.append("preview: an environment")
    # The preview job runs the pull request's code: its engine image must come
    # from the base branch's lock.
    for step in jobs["preview"]["steps"]:
        run = step.get("run", "")
        for script in ("check-config.sh", "validate.sh"):
            if f"scripts/{script}" in run and "--lock base/engine.lock" not in run:
                found.append(f"preview: {script} does not use the base engine.lock")
    comment = jobs["comment"]
    if comment.get("permissions") != COMMENT_PERMISSIONS:
        found.append("comment: permissions are not exactly contents + pull-requests")
    if "head.repo.full_name == github.repository" not in str(comment.get("if")):
        found.append("comment: no guard against pull requests from forks")
    steps = comment["steps"]
    checkouts = [s for s in steps if "checkout" in str(s.get("uses", ""))]
    if len(checkouts) != 1:
        found.append("comment: not exactly one checkout")
    for checkout in checkouts:
        with_ = checkout.get("with", {})
        if "pull_request.base.sha" not in str(with_.get("ref")):
            found.append("comment: the checkout is not of the base commit")
        if with_.get("persist-credentials") is not False:
            found.append("comment: the checkout keeps credentials")
        if with_.get("sparse-checkout") != "scripts/render-comment.py":
            found.append("comment: the checkout is not only the renderer")
    for step in steps:
        run = step.get("run", "")
        if UNTRUSTED_SCRIPT.search(run):
            found.append("comment: runs a script that is not from the base commit")
    renders = [s for s in steps if "render-comment.py" in s.get("run", "")]
    if len(renders) != 1 or "python3 -I trusted/scripts/render-comment.py" not in str(
        renders[0].get("run")
    ):
        found.append("comment: the comment is not rendered by the trusted script")
    posting = [s for s in steps if "gh api" in s.get("run", "")]
    if len(posting) != 1:
        found.append("comment: not exactly one posting step")
    else:
        run = posting[0]["run"]
        for needle in ("startswith", "github-actions[bot]", "--method PATCH"):
            if needle not in run:
                found.append(f"comment: the posting step lacks {needle}")
        if "result-in" in run or "--edit-last" in run or "pr comment" in run:
            found.append("comment: posts from the artifact or edits the last comment")
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
    # a push to main uses main's own lock: no --lock override
    assert all("--lock" not in r for r in runs)


def test_the_preview_guard_bites() -> None:
    good = preview_text()
    assert preview_violations(good) == []
    checkout = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
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
        "no fork guard": good.replace(
            " && github.event.pull_request.head.repo.full_name == github.repository",
            "",
        ),
        "no cancel": good.replace(
            "cancel-in-progress: true", "cancel-in-progress: false"
        ),
        "no timeout": good.replace("    timeout-minutes: 10\n", "", 1),
        "check uses the head lock": good.replace(
            "--lock base/engine.lock --format json", "--format json"
        ),
        "build uses the head lock": good.replace(
            "scripts/validate.sh --lock base/engine.lock", "scripts/validate.sh"
        ),
        "renderer from the head": good.replace(
            "ref: ${{ github.event.pull_request.base.sha }}\n          path: trusted",
            "path: trusted",
        ),
        "renderer checkout keeps credentials": good.replace(
            "sparse-checkout-cone-mode: false\n          persist-credentials: false\n",
            "sparse-checkout-cone-mode: false\n          persist-credentials: true\n",
        ),
        "whole repository checked out in comment": good.replace(
            "          sparse-checkout: scripts/render-comment.py\n", ""
        ),
        "comment runs the head renderer": good.replace(
            "python3 -I trusted/scripts/render-comment.py",
            "python3 -I scripts/render-comment.py",
        ),
        "comment posts the artifact": good.replace(
            '-F "body=@$COMMENT_FILE" > /dev/null\n          else',
            '-F "body=@$RUNNER_TEMP/result-in/result.json" > /dev/null\n          else',
        ),
        "comment edits the last comment": good.replace(
            "--method PATCH", "--method PUT"
        ).replace('startswith("\'"$marker"\'")', "true"),
        "second checkout": good.replace(
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
            "bash-ci.yml@8fdb8d106b11aae0f37e893bb57e9bd8e8b0e7b7 # v1.24.0",
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
        ' echo "Docker version 1"; exit 0; fi\n' + body,
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


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
        root, "--check-status", "0", "--build-status", "0",
        "--check", str(check), "--out", str(out),
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "check_status": 0,
        "build_status": 0,
        "check": {"valid": True},
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
            root, "--check-status", "2", "--check", str(check), "--out", str(result)
        ).returncode
        == 0
    )
    comment = tmp_path / "comment.md"
    done = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable, "-I", str(root / "scripts" / "render-comment.py"),
            "--in", str(result), "--out", str(comment),
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
