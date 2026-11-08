# SPDX-License-Identifier: Apache-2.0
"""docs/examples/deploy-private.sh (dry run) keeps the lead retention single
source: the value comes from the build manifest and lands in the Worker vars."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs" / "examples" / "deploy-private.sh"
MISSING = object()

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("bash") is None,
    reason="needs bash and jq",
)

# A stand-in for `reach-around build`: it writes the manifest the test chose to
# the --manifest path, like the real CLI.
FAKE_BUILD = """#!/bin/sh
out=""; manifest=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) out="$2"; shift 2 ;;
    --manifest) manifest="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$out"
[ -n "$manifest" ] || manifest="$out.manifest.json"
cp "$FAKE_MANIFEST" "$manifest"
"""


def _dry_run(
    tmp_path: Path, retention: Any, variable: str | None = None
) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "repo"
    (repo / "worker").mkdir(parents=True)
    shutil.copy(ROOT / "worker" / "wrangler.toml", repo / "worker" / "wrangler.toml")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "reach-around"
    fake.write_text(FAKE_BUILD, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    body: dict[str, Any] = {"links": {"alpha": "https://example.invalid/a"}}
    if retention is not MISSING:
        body["lead_retention_days"] = retention
    manifest = tmp_path / "fake-manifest.json"
    manifest.write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "site.yaml").write_text("version: 1\n", encoding="utf-8")
    settings = tmp_path / "settings"
    lines = [
        f"REPO_DIR={repo}",
        f"INSTANCE_CONFIG={tmp_path / 'site.yaml'}",
        f"PRIVATE_DIR={tmp_path / 'private'}",
        f"CLOUDFLARE_ACCOUNT_ID={'0' * 32}",
        "PAGES_PROJECT=example",
        "D1_DATABASE_ID=11111111-2222-3333-4444-555555555555",
        "RATELIMIT_NAMESPACE_ID=4242",
        "SITE_HOST=links.example.invalid",
    ]
    if variable is not None:
        lines.append(f"LEAD_RETENTION_DAYS='{variable}'")
    settings.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "FAKE_MANIFEST": str(manifest),
        "HOME": str(tmp_path),
    }
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/bin/bash", str(SCRIPT), "--dry-run", str(settings)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _vars(tmp_path: Path) -> dict[str, Any]:
    text = (tmp_path / "private" / "wrangler.deploy.toml").read_text(encoding="utf-8")
    vars_: dict[str, Any] = tomllib.loads(text)["vars"]
    return vars_


def test_the_manifest_retention_lands_in_the_worker_vars(tmp_path: Path) -> None:
    done = _dry_run(tmp_path, 45)
    assert done.returncode == 0, done.stderr
    assert _vars(tmp_path)["LEAD_RETENTION_DAYS"] == "45"
    assert "45" in done.stdout


def test_a_matching_variable_passes(tmp_path: Path) -> None:
    done = _dry_run(tmp_path, 45, "45")
    assert done.returncode == 0, done.stderr
    assert _vars(tmp_path)["LEAD_RETENTION_DAYS"] == "45"


def test_a_differing_variable_fails_naming_both(tmp_path: Path) -> None:
    done = _dry_run(tmp_path, 45, "90")
    assert done.returncode == 1
    assert "90" in done.stderr and "45" in done.stderr
    assert "LEAD_RETENTION_DAYS" in done.stderr
    assert "privacy.lead_retention_days" in done.stderr


@pytest.mark.parametrize("var", ["0", "366", "abc", "045", "4 5", "-1"])
def test_a_malformed_variable_is_refused(tmp_path: Path, var: str) -> None:
    done = _dry_run(tmp_path, 45, var)
    assert done.returncode == 1
    assert "LEAD_RETENTION_DAYS" in done.stderr


@pytest.mark.parametrize(
    "bad", [MISSING, 0, 366, -1, "30", True, False, 1.5, None, [30], 10**30]
)
def test_a_bad_manifest_retention_is_refused(tmp_path: Path, bad: Any) -> None:
    done = _dry_run(tmp_path, bad)
    assert done.returncode == 1
    assert "lead_retention_days" in done.stderr
    assert not (tmp_path / "private" / "wrangler.deploy.toml").exists()


def test_the_script_does_not_read_the_retention_from_the_environment() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "jq -e" in text and "lead_retention_days" in text
    assert "--manifest" in text  # the manifest sits beside, never inside, the output
