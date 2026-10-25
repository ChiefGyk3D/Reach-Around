# SPDX-License-Identifier: Apache-2.0
"""The 'Write deploy config' step of deploy-test.yml, run against the real
worker/wrangler.toml with sample values."""

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-test.yml"

D1_ID = "11111111-2222-3333-4444-555555555555"
NS_ID = "4242"
HOST = "links-test.example.invalid"
LINKS = '["alpha","beta"]'
ZONE = "example.invalid"


def _step(name: str) -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    found = [s for s in steps if s.get("name") == name]
    assert len(found) == 1, name
    step: dict[str, Any] = found[0]
    return step


def _run(tmp_path: Path, zone: str | None) -> str:
    script = _step("Write deploy config")["run"]
    (tmp_path / "wrangler.toml").write_text(
        (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "D1_DATABASE_ID": D1_ID,
        "RATELIMIT_NAMESPACE_ID": NS_ID,
        "SITE_HOST": HOST,
        "LINK_IDS": LINKS,
    }
    if zone is not None:
        env["ZONE_NAME"] = zone
    subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", script], cwd=tmp_path, env=env, check=True
    )
    return (tmp_path / "wrangler.deploy.toml").read_text(encoding="utf-8")


def test_values_are_replaced(tmp_path: Path) -> None:
    data = tomllib.loads(_run(tmp_path, None))
    assert data["d1_databases"][0]["database_id"] == D1_ID
    assert data["ratelimits"][0]["namespace_id"] == NS_ID
    assert data["vars"]["SITE_HOST"] == HOST
    assert data["vars"]["LINK_IDS"] == LINKS
    assert "routes" not in data


@pytest.mark.parametrize("zone", [None, ""])
def test_no_zone_name_leaves_output_unchanged(tmp_path: Path, zone: str | None) -> None:
    out = _run(tmp_path, zone)
    assert "[[routes]]\npattern" not in out
    base = (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8")
    # Same line count, and only the four substituted lines differ.
    assert len(out.splitlines()) == len(base.splitlines())
    pairs = zip(base.splitlines(), out.splitlines(), strict=True)
    differing = [a for a, b in pairs if a != b]
    assert len(differing) == 4
    assert out.endswith("\n")


def test_zone_name_adds_route(tmp_path: Path) -> None:
    out = _run(tmp_path, ZONE)
    data = tomllib.loads(out)
    assert data["routes"] == [{"pattern": f"{HOST}/e/*", "zone_name": ZONE}]
    assert data["d1_databases"][0]["database_id"] == D1_ID


def test_zone_name_only_where_needed() -> None:
    for name in ("Check configuration", "Write deploy config"):
        assert "ZONE_NAME" in _step(name)["env"]
    for name in (
        "Apply D1 migrations",
        "Deploy the Worker",
        "Publish the site to Pages",
    ):
        assert "ZONE_NAME" not in _step(name)["env"]


def _uploads_and_downloads() -> tuple[list[str], list[str]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    up = [
        str(s["with"]["path"])
        for s in workflow["jobs"]["build"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
    ]
    down = [
        str(s["with"]["path"])
        for s in workflow["jobs"]["deploy"]["steps"]
        if str(s.get("uses", "")).startswith("actions/download-artifact")
    ]
    return up, down


def test_workflow_manifest_is_a_sibling_never_inside_the_published_dir() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    build = [
        s
        for s in workflow["jobs"]["build"]["steps"]
        if s.get("name") == "Build the site"
    ]
    assert len(build) == 1
    assert "--out site-out" in build[0]["run"]
    # the old location must be gone everywhere, including comments
    assert "site-out/manifest.json" not in text
    up, down = _uploads_and_downloads()
    assert "site-out" in up and "site-out.manifest.json" in up
    assert "site-out" in down  # collected something
    # the Pages upload publishes exactly the site directory
    publish = _step("Publish the site to Pages")["run"]
    assert "pages deploy ../site-out " in publish + " "


def test_doppler_fetch_comes_after_every_install_step() -> None:
    """Doppler exports every secret into the rest of the job, so no step that
    installs packages (and could run an install script) may follow the fetch."""
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"][
        "steps"
    ]
    names = [s.get("name") for s in steps]
    fetch = names.index("Fetch Cloudflare credentials from Doppler")
    installers = [
        i
        for i, s in enumerate(steps)
        if any(w in str(s.get("run", "")) for w in ("npm ci", "npm install", "pip "))
    ]
    assert installers, "no install step found; the ordering rule would be vacuous"
    assert max(installers) < fetch
    # the Doppler step is the only place the token enters the environment, and
    # the presence check follows it before any wrangler step
    assert fetch < names.index("Require the Cloudflare token")
    assert names.index("Require the Cloudflare token") < names.index(
        "Apply D1 migrations"
    )
    uses = steps[fetch]["uses"]
    assert uses.startswith("ChiefGyk3D/git-your-ship-together/")
    assert steps[fetch]["with"]["project"] == "reach-around"
    assert steps[fetch]["with"]["config"] == "ci"
    assert "vars.DOPPLER_IDENTITY_ID" in steps[fetch]["with"]["identity-id"]


def test_manifest_is_read_by_a_tokenless_isolated_step() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    downloads = [
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/download-artifact")
        and s["with"]["name"] == "site-manifest"
    ]
    assert len(downloads) == 1
    # its own directory, never the working directory
    assert downloads[0]["with"]["path"] == "manifest-in"
    reader = _step("Verify the build manifest")
    run = reader["run"]
    assert "manifest-in/site-out.manifest.json" in run
    assert "python3 -I" in run
    assert "-mindepth 1" in run and "-L" in run  # one file, no symlink
    assert "secrets." not in str(reader)
    assert "CLOUDFLARE_API_TOKEN" not in str(reader)
    # the configuration check stays separate, runs no python, and runs before
    # the token exists (it is fetched from Doppler later), so it holds none
    check = _step("Check configuration")
    assert "CLOUDFLARE_API_TOKEN" not in check["env"]
    assert "manifest" not in check["run"] and "python" not in check["run"]
    require = _step("Require the Cloudflare token")
    assert "python" not in require["run"] and "manifest" not in require["run"]
    # no step that carries the token runs python or reads manifest-in
    carrying = [s for s in steps if "CLOUDFLARE_API_TOKEN" in str(s.get("env", {}))]
    assert carrying
    for step in carrying:
        body = str(step.get("run", ""))
        assert "python" not in body and "manifest-in" not in body
