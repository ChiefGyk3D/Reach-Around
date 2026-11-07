# SPDX-License-Identifier: Apache-2.0
"""The kit limits table in docs/deploy.md must match its own arithmetic and the Worker."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_START = "## Kit limits and the free-plan budget"
_END = "## Secrets and Doppler"
_WRITES = {"gate": 5, "view": 2, "alert": 1, "send": 7}
_RESERVE = 10_000


def _section() -> str:
    text = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")
    return text[text.index(_START) : text.index(_END)]


def _defaults(section: str) -> dict[str, int]:
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^\| `([A-Z0-9_]+)` \| [^|]+ \| (\d+) \|", section, re.M)
    }


def test_defaults_table_collected_every_variable() -> None:
    assert set(_defaults(_section())) == {
        "ROUTE_BUDGET_GATE",
        "ROUTE_BUDGET_VIEW",
        "MAX_LEADS",
        "MAX_KIT_EVENTS",
        "DAILY_ALERT_BUDGET",
        "DAILY_SEND_BUDGET",
        "D1_DAILY_WRITE_QUOTA",
    }


def test_worst_case_table_adds_up_and_is_under_the_warning_line() -> None:
    section = _section()
    d = _defaults(section)
    expected = {
        "gate": 24 * d["ROUTE_BUDGET_GATE"] * _WRITES["gate"],
        "view": 24 * d["ROUTE_BUDGET_VIEW"] * _WRITES["view"],
        "alert": d["DAILY_ALERT_BUDGET"] * _WRITES["alert"],
        "send": d["DAILY_SEND_BUDGET"] * _WRITES["send"],
    }
    rows = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^\| (gate|view|alert|send) \|.*\| (\d+) \|$", section, re.M)
    }
    assert rows == expected
    total = sum(expected.values()) + _RESERVE
    assert f"**{total}**" in section
    assert total < d["D1_DAILY_WRITE_QUOTA"] * 0.7
    assert f"{round(100 * total / d['D1_DAILY_WRITE_QUOTA'], 1)} percent" in section


def test_doc_defaults_match_the_worker_source() -> None:
    src = (ROOT / "worker" / "src" / "kit" / "budgets.ts").read_text(encoding="utf-8")
    d = _defaults(_section())
    pairs = {
        "routeGatePerHour": "ROUTE_BUDGET_GATE",
        "routeViewPerHour": "ROUTE_BUDGET_VIEW",
        "maxLeads": "MAX_LEADS",
        "maxKitEvents": "MAX_KIT_EVENTS",
        "dailyAlertBudget": "DAILY_ALERT_BUDGET",
        "dailySendBudget": "DAILY_SEND_BUDGET",
        "d1DailyWriteQuota": "D1_DAILY_WRITE_QUOTA",
    }
    block = src[src.index("export const DEFAULTS") : src.index("} as const;")]
    for field, var in pairs.items():
        m = re.search(rf"{field}: ([0-9_]+)", block)
        assert m is not None, field
        assert int(m.group(1).replace("_", "")) == d[var], var
    wr = src[src.index("export const WRITES_PER_OP") :].split(";")[0]
    for name, n in _WRITES.items():
        assert re.search(rf"{name}: {n}\b", wr), name
    assert f"PURGE_RESERVE = {_RESERVE:_}" in src
