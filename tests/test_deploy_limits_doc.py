# SPDX-License-Identifier: Apache-2.0
"""The kit limits table in docs/deploy.md must match its arithmetic and the Worker."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_START = "## Kit limits and the free-plan budget"
_END = "## Secrets and Doppler"
_WRITES = {"gate": 5, "view": 2, "alert": 1, "send": 7}
_RESERVE = 10_000
_PAGE_VIEWS = 22_700
_CLICKS = 8_000
_MARGIN = 5_000


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
        for m in re.finditer(
            r"^\| (gate|view|alert|send) \|.*\| (\d+) \|$", section, re.M
        )
    }
    assert rows == expected
    total = sum(expected.values()) + _RESERVE
    combined = total + _PAGE_VIEWS + _CLICKS + _MARGIN
    assert f"**{combined}**" in section
    assert f"| page views, rollup and purge | | | {_PAGE_VIEWS} |" in section
    assert f"| clicks (reserve) | | | {_CLICKS} |" in section
    assert f"| margin | | | {_MARGIN} |" in section
    assert f"| kit purge reserve | | | {_RESERVE} |" in section
    assert combined <= d["D1_DAILY_WRITE_QUOTA"] * 0.8
    assert f"{round(100 * combined / d['D1_DAILY_WRITE_QUOTA'], 1)} percent" in section


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
    # The page-view figure is derived (worker/src/pvlimits.ts) from the default
    # hourly budgets; the derivation must still give the documented number.
    pv = (ROOT / "worker" / "src" / "pvlimits.ts").read_text(encoding="utf-8")
    human_m = re.search(r"DEFAULT_HUMAN_PER_HOUR = (\d+)", pv)
    bot_m = re.search(r"DEFAULT_BOT_PER_HOUR = (\d+)", pv)
    assert human_m is not None and bot_m is not None
    human, bot = int(human_m.group(1)), int(bot_m.group(1))
    ratio = re.search(r"num: (\d+), den: (\d+)", pv)
    assert ratio is not None
    fixed = re.search(r"PV_FIXED_DAILY_WRITES = ([0-9_]+)", pv)
    assert fixed is not None
    derived = -(-24 * (human + bot) * int(ratio.group(1)) // int(ratio.group(2))) + int(
        fixed.group(1).replace("_", "")
    )
    assert derived == _PAGE_VIEWS
    assert "PAGE_VIEW_DAILY_WRITES = pageViewDailyWrites({})" in src
    assert f"CLICK_DAILY_WRITES = {_CLICKS:_}" in src
    assert f"COMBINED_MARGIN = {_MARGIN:_}" in src
