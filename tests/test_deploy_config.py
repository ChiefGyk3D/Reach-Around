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
    return found[0]


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
    differing = [a for a, b in zip(base.splitlines(), out.splitlines()) if a != b]
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
