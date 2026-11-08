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


def _run(tmp_path: Path, zone: str | None, extra: dict[str, str] | None = None) -> str:
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
    env.update(extra or {})
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
    # Issue #206: the inline Python moved to a script so its ids can be
    # validated and tested; the step now only calls it, isolated (-I).
    assert "python3 -I .github/scripts/verify-manifest.py" in run
    script = (ROOT / ".github" / "scripts" / "verify-manifest.py").read_text(
        encoding="utf-8"
    )
    assert "secrets." not in script and "CLOUDFLARE" not in script
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


# --- the Live Worker (issue #314) ---------------------------------------------

KV_ID = "0123456789abcdef0123456789abcdef"
LIVE_ON = {"LIVE_TWITCH_HANDLE": "example_channel", "LIVE_KV_ID": KV_ID}
CHECK_BASE = {
    "CLOUDFLARE_ACCOUNT_ID": "acct",
    "PAGES_PROJECT": "proj",
    "D1_DATABASE_ID": D1_ID,
    "RATELIMIT_NAMESPACE_ID": NS_ID,
    "SITE_HOST": HOST,
    "LINK_IDS": LINKS,
}


def _check(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the real 'Check configuration' script with sample values."""
    script = _step("Check configuration")["run"]
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", **CHECK_BASE, **extra},
        capture_output=True,
        text=True,
        check=False,
    )


def test_live_off_changes_nothing_even_with_a_kv_id(tmp_path: Path) -> None:
    out = _run(tmp_path, None, {"LIVE_KV_ID": KV_ID})
    assert "kv_namespaces" not in tomllib.loads(out)
    assert KV_ID not in out
    assert _check({"LIVE_KV_ID": KV_ID}).returncode == 0


@pytest.mark.parametrize(
    "handles",
    [
        {"LIVE_TWITCH_HANDLE": "example_channel"},
        {"LIVE_KICK_HANDLE": "example-channel"},
        {
            "LIVE_TWITCH_HANDLE": "example_channel",
            "LIVE_KICK_HANDLE": "example-channel",
        },
    ],
)
def test_live_on_fills_the_kv_id_handles_and_cron(
    tmp_path: Path, handles: dict[str, str]
) -> None:
    out = _run(tmp_path, ZONE, {**handles, "LIVE_KV_ID": KV_ID})
    data = tomllib.loads(out)
    assert data["kv_namespaces"] == [{"binding": "LIVE_KV", "id": KV_ID}]
    assert data["triggers"]["crons"] == ["5 * * * *", "*/2 * * * *"]
    for name, value in handles.items():
        assert data["vars"][name] == value
    # the other substitutions and the route are still there
    assert data["d1_databases"][0]["database_id"] == D1_ID
    assert data["routes"] == [{"pattern": f"{HOST}/e/*", "zone_name": ZONE}]
    # no all-zero placeholder id on an active line
    active = [ln for ln in out.splitlines() if not ln.lstrip().startswith("#")]
    assert "0" * 32 not in "\n".join(active)
    assert _check({**handles, "LIVE_KV_ID": KV_ID}).returncode == 0


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "0" * 32,
        KV_ID[:-1],
        KV_ID + "0",
        KV_ID.upper(),
        "g" * 32,
        "../" + KV_ID[3:],
        KV_ID + "\nname = 'x'",
        f'{KV_ID[:-2]}"x',
        "11111111-2222-3333-4444-555555555555",
    ],
)
def test_live_on_with_an_empty_or_bad_kv_id_fails_closed(
    tmp_path: Path, bad: str
) -> None:
    extra = {"LIVE_TWITCH_HANDLE": "example_channel", "LIVE_KV_ID": bad}
    checked = _check(extra)
    assert checked.returncode != 0
    assert "LIVE_KV_ID" in checked.stdout + checked.stderr
    # the writer refuses on its own as well (second lock)
    with pytest.raises(subprocess.CalledProcessError):
        _run(tmp_path, None, extra)


def test_live_on_with_the_variable_unset_fails_closed(tmp_path: Path) -> None:
    checked = _check({"LIVE_KICK_HANDLE": "example-channel"})
    assert checked.returncode != 0
    assert "LIVE_KV_ID is not set" in checked.stdout + checked.stderr
    with pytest.raises(subprocess.CalledProcessError):
        _run(tmp_path, None, {"LIVE_KICK_HANDLE": "example-channel"})


@pytest.mark.parametrize(
    "extra",
    [
        {"LIVE_TWITCH_HANDLE": "bad handle"},
        {"LIVE_TWITCH_HANDLE": "x" * 26},
        {"LIVE_TWITCH_HANDLE": "a-b"},
        {"LIVE_KICK_HANDLE": "a.b"},
        {"LIVE_KICK_HANDLE": "x" * 41},
        {"LIVE_KICK_HANDLE": 'a"\n[vars]'},
    ],
)
def test_live_bad_handles_fail_closed(tmp_path: Path, extra: dict[str, str]) -> None:
    both = {**extra, "LIVE_KV_ID": KV_ID}
    assert _check(both).returncode != 0
    with pytest.raises(subprocess.CalledProcessError):
        _run(tmp_path, None, both)


def test_live_negative_cases_do_bite() -> None:
    """The guard is not vacuous: a good value passes the same check script."""
    assert _check(LIVE_ON).returncode == 0
    assert _check({**LIVE_ON, "LIVE_KV_ID": ""}).returncode != 0


def test_live_variables_are_plain_variables_in_two_steps_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "vars.LIVE_KV_ID" in text
    assert "secrets.LIVE_KV_ID" not in text
    for name in ("Check configuration", "Write deploy config"):
        env = _step(name)["env"]
        for var in ("LIVE_KV_ID", "LIVE_TWITCH_HANDLE", "LIVE_KICK_HANDLE"):
            assert env[var] == "${{ vars." + var + " }}"
    for name in (
        "Apply D1 migrations",
        "Deploy the Worker",
        "Publish the site to Pages",
    ):
        assert "LIVE_KV_ID" not in str(_step(name))
    # still no write to the runner's shared files
    assert "GITHUB_ENV" not in str(_step("Write deploy config"))
    assert "GITHUB_PATH" not in str(_step("Write deploy config"))
