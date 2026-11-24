# SPDX-License-Identifier: Apache-2.0
"""scripts/cost_estimate.py: the three worked examples on docs/wiki/Costs.md.

The numbers below are the ones printed on the wiki page. If the script's model
changes, the page and this table change together."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cost_estimate.py"

EXPECTED: dict[str, dict[str, object]] = {
    "small": {
        "requests": 23_820,
        "d1_rows_written": 215_500,
        "daily_rows_written": 7_183,
        "plan": "Free possible (see notes)",
        "cost_workers_floor": "5.00",
        "cost_workers_requests": "0.00",
        "cost_workers_cpu": "0.00",
        "cost_d1_rows_written": "0.00",
        "cost_total": "5.00",
        "view_budget_ok": True,
    },
    "medium": {
        "requests": 346_920,
        "d1_rows_written": 2_317_200,
        "daily_rows_written": 77_240,
        "plan": "Paid",
        "cost_workers_floor": "5.00",
        "cost_workers_requests": "0.00",
        "cost_workers_cpu": "0.00",
        "cost_d1_rows_written": "0.00",
        "cost_total": "5.00",
        "view_budget_ok": True,
    },
    "busy": {
        "requests": 11_540_720,
        "worker_cpu_ms": 34_982_160,
        "d1_rows_written": 70_001_000,
        "daily_rows_written": 2_333_366,
        "plan": "Paid",
        "cost_workers_floor": "5.00",
        "cost_workers_requests": "0.46",
        "cost_workers_cpu": "0.10",
        "cost_d1_rows_written": "20.00",
        "cost_total": "25.56",
        "view_budget_ok": True,
    },
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_worked_example_matches_the_page(name: str) -> None:
    p = run("--example", name, "--json")
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    for key, want in EXPECTED[name].items():
        assert got[key] == want, key


def test_flags_match_the_small_example() -> None:
    p = run(
        "--views",
        "20000",
        "--sites",
        "1",
        "--clicks",
        "3000",
        "--leads",
        "50",
        "--json",
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["cost_total"] == "5.00"


def test_text_output_names_the_price_date() -> None:
    p = run("--example", "busy")
    assert p.returncode == 0
    assert "Prices as of 2026-11-24" in p.stdout
    assert "TOTAL a month" in p.stdout and "$25.56" in p.stdout


def test_a_view_count_over_the_budget_is_flagged() -> None:
    p = run(
        "--views", "1000000", "--sites", "1", "--clicks", "0", "--leads", "0", "--json"
    )
    assert json.loads(p.stdout)["view_budget_ok"] is False


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--views", "10"],
        ["--example", "small", "--views", "-1"],
        ["--example", "small", "--sites", "0"],
        ["--example", "nope"],
    ],
)
def test_bad_input_exits_2(args: list[str]) -> None:
    assert run(*args).returncode == 2
