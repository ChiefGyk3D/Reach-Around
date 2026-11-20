# SPDX-License-Identifier: Apache-2.0
"""deploy-test.yml and the beacon token (issue #610): BEACON_MODE is exact and
off by default, only observe writes anything, the mint limiter namespace is
validated, the key is never written, and the budget gate sees the mode. The
shell is run for real, with sample values."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-worker.yml"

BASE = {
    "PATH": "/usr/bin:/bin",
    "D1_DATABASE_ID": "11111111-2222-3333-4444-555555555555",
    "RATELIMIT_NAMESPACE_ID": "4242",
    "SITE_HOST": "links-test.example.invalid",
    "LINK_IDS": '["alpha"]',
    "LEAD_RETENTION_DAYS": "30",
    "ZONE_NAME": "example.invalid",
    "PAGE_VIEWS": "true",
    "PAGE_IDS": '["start-here"]',
    "VIEW_LIMITER_NAMESPACE_ID": "4343",
}
MINT_NS = "4545"


def _run(tmp_path: Path, extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"][
        "steps"
    ]
    found: list[dict[str, Any]] = [
        s for s in steps if s.get("name") == "Write deploy config"
    ]
    assert len(found) == 1
    (tmp_path / "wrangler.toml").write_text(
        (ROOT / "worker" / "wrangler.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", found[0]["run"]],
        cwd=tmp_path,
        env={**BASE, **extra},
        capture_output=True,
        text=True,
        check=False,
    )


def _data(tmp_path: Path) -> dict[str, Any]:
    out: dict[str, Any] = tomllib.loads(
        (tmp_path / "wrangler.deploy.toml").read_text(encoding="utf-8")
    )
    return out


@pytest.mark.parametrize("flag", [{}, {"BEACON_MODE": ""}, {"BEACON_MODE": "off"}])
def test_off_writes_nothing_even_with_a_namespace_set(
    tmp_path: Path, flag: dict[str, str]
) -> None:
    done = _run(tmp_path, {**flag, "MINT_LIMITER_NAMESPACE_ID": MINT_NS})
    assert done.returncode == 0, done.stderr
    data = _data(tmp_path)
    assert "BEACON_MODE" not in data["vars"]
    assert "MINT_LIMITER" not in [r["name"] for r in data["ratelimits"]]
    assert MINT_NS not in (tmp_path / "wrangler.deploy.toml").read_text(
        encoding="utf-8"
    )


def test_observe_writes_the_mode_and_a_mint_limiter_and_never_a_key(
    tmp_path: Path,
) -> None:
    done = _run(
        tmp_path, {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": MINT_NS}
    )
    assert done.returncode == 0, done.stderr
    data = _data(tmp_path)
    assert data["vars"]["BEACON_MODE"] == "observe"
    limiter = {r["name"]: r for r in data["ratelimits"]}
    assert limiter["MINT_LIMITER"]["namespace_id"] == MINT_NS
    assert limiter["MINT_LIMITER"]["simple"] == {"limit": 6, "period": 60}
    assert {"CLICK_LIMITER", "VIEW_LIMITER"} <= set(limiter)
    text = (tmp_path / "wrangler.deploy.toml").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert (
        "BEACON_HMAC_KEY" not in code
    )  # the key is a secret the owner sets; CI never writes it


BAD: list[tuple[str, dict[str, str]]] = [
    (
        "enforce is not available yet",
        {"BEACON_MODE": "enforce", "MINT_LIMITER_NAMESPACE_ID": MINT_NS},
    ),
    ("capitalized", {"BEACON_MODE": "Observe", "MINT_LIMITER_NAMESPACE_ID": MINT_NS}),
    ("true", {"BEACON_MODE": "true", "MINT_LIMITER_NAMESPACE_ID": MINT_NS}),
    (
        "quote injection",
        {"BEACON_MODE": 'observe"\nx = "y', "MINT_LIMITER_NAMESPACE_ID": MINT_NS},
    ),
    ("no namespace", {"BEACON_MODE": "observe"}),
    (
        "namespace not a number",
        {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": "12ab"},
    ),
    (
        "namespace newline",
        {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": "12\n34"},
    ),
    (
        "namespace placeholder",
        {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": "1004"},
    ),
    (
        "namespace equals clicks",
        {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": "4242"},
    ),
    (
        "namespace equals views",
        {"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": "4343"},
    ),
]


@pytest.mark.parametrize(("label", "extra"), BAD, ids=[b[0] for b in BAD])
def test_a_bad_value_fails_closed(
    tmp_path: Path, label: str, extra: dict[str, str]
) -> None:
    assert _run(tmp_path, extra).returncode != 0, label


@pytest.mark.parametrize(
    ("extra", "ok"),
    [
        ({}, True),
        ({"BEACON_MODE": "off"}, True),
        ({"BEACON_MODE": "observe", "MINT_LIMITER_NAMESPACE_ID": MINT_NS}, True),
        ({"BEACON_MODE": "observe"}, False),
        ({"BEACON_MODE": "enforce", "MINT_LIMITER_NAMESPACE_ID": MINT_NS}, False),
        ({"BEACON_MODE": "Observe", "MINT_LIMITER_NAMESPACE_ID": MINT_NS}, False),
    ],
)
def test_check_configuration_applies_the_same_rule(
    extra: dict[str, str], ok: bool
) -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"][
        "steps"
    ]
    script = [s for s in steps if s.get("name") == "Check configuration"][0]["run"]
    env = {
        **BASE,
        "CLOUDFLARE_ACCOUNT_ID": "acct",
        "WORKER_NAME": "reach-around-clicks-test",
        **extra,
    }
    done = subprocess.run(  # noqa: S603 - fixed argv and sample values
        ["/bin/bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode == 0) is ok, done.stdout + done.stderr


def test_the_budget_gate_step_sees_the_mode() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"][
        "steps"
    ]
    gate = [s for s in steps if s.get("name") == "Check the page-view write budget"]
    assert len(gate) == 1
    assert gate[0]["env"]["BEACON_MODE"] == "${{ inputs.beacon_mode }}"
