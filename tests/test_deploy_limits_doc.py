# SPDX-License-Identifier: Apache-2.0
"""The kit limits table in docs/deploy.md must match its arithmetic and the Worker."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_START = "## Kit limits and the daily write budget"
_END = "## Secrets and Doppler"
_WRITES = {"gate": 5, "view": 2, "alert": 1, "send": 7, "inquiry": 1}
_RESERVE = 10_000
_PAGE_VIEWS = 22_700
_CLICKS = 300_501
_FUNNEL = 758  # Free profile, one site, no link ids (worker/src/funnelcaps.ts)
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
        "CLICK_DAILY_WRITE_CEILING",
    }


def test_worst_case_table_adds_up_and_is_under_the_warning_line() -> None:
    section = _section()
    d = _defaults(section)
    expected = {
        "gate": 24 * d["ROUTE_BUDGET_GATE"] * _WRITES["gate"],
        "view": 24 * d["ROUTE_BUDGET_VIEW"] * _WRITES["view"],
        "alert": d["DAILY_ALERT_BUDGET"] * _WRITES["alert"],
        "send": d["DAILY_SEND_BUDGET"] * _WRITES["send"],
        # an inquiry is capped by the daily alert budget (worker/src/kit/inquiry.ts)
        "inquiry": d["DAILY_ALERT_BUDGET"] * _WRITES["inquiry"],
    }
    rows = {
        m.group(1): int(m.group(2))
        for m in re.finditer(
            r"^\| (gate|view|alert|send|inquiry) \|.*\| (\d+) \|$", section, re.M
        )
    }
    assert rows == expected
    total = sum(expected.values()) + _RESERVE
    combined = total + _PAGE_VIEWS + _CLICKS + _FUNNEL + _MARGIN
    assert f"**{combined}**" in section
    assert f"| page views, rollup and purge | | | {_PAGE_VIEWS} |" in section
    assert f"| clicks (enforced ceiling, all sites) | | | {_CLICKS} |" in section
    funnel_row = f"| click funnel (Free profile, one site, no link ids) | | | {_FUNNEL}"
    assert f"{funnel_row} |" in section
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
    assert "CLICK_DAILY_WRITES = CLICK_ACCOUNT_DAILY_WRITES" in src
    cc = (ROOT / "worker" / "src" / "clickceiling.ts").read_text(encoding="utf-8")
    ceiling = re.search(r"CLICK_ADMIT_CEILING = ([0-9_]+)", cc)
    drops = re.search(r"CLICK_DROP_COUNT_CAP = ([0-9_]+)", cc)
    flag = re.search(r"CLICK_SETUP_FLAG_WRITES = ([0-9_]+)", cc)
    assert ceiling is not None and drops is not None and flag is not None
    assert (
        int(ceiling.group(1).replace("_", ""))
        + int(drops.group(1).replace("_", ""))
        + int(flag.group(1).replace("_", ""))
        == _CLICKS
    )
    assert (
        "CLICK_ACCOUNT_DAILY_WRITES = CLICK_ADMIT_CEILING + CLICK_DROP_COUNT_CAP"
        " + CLICK_SETUP_FLAG_WRITES" in cc
    )
    assert f"COMBINED_MARGIN = {_MARGIN:_}" in src


def test_click_ceiling_section_states_the_enforced_numbers() -> None:
    """The click ceiling paragraph quotes the constants the Worker enforces, and no old figure."""
    cc = (ROOT / "worker" / "src" / "clickceiling.ts").read_text(encoding="utf-8")

    def const(name: str) -> int:
        m = re.search(rf"{name} = ([0-9_]+);", cc)
        assert m is not None, name
        return int(m.group(1).replace("_", ""))

    ceiling = const("CLICK_ADMIT_CEILING")
    reserve = const("CLICK_FIRST_RESERVE_PERCENT")
    lane = const("CLICK_UNVERIFIED_PERCENT")
    slices = const("CLICK_HOUR_SLICES")
    text = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")
    start = text.index("**Click write ceiling")
    para = text[start : text.index("**Click funnel in the check", start)]
    assert f"ceiling on its write charge of {ceiling}" in para
    total = ceiling + const("CLICK_DROP_COUNT_CAP") + const("CLICK_SETUP_FLAG_WRITES")
    assert f"ceiling plus drop cap plus the setup flag, {total}" in para
    # the charge is documented as a conservative bound, with the changed-rows range, not "the rows it writes"
    assert "deliberately conservative upper bound" in para
    assert "6 to 9 rows" in para and "9 to 13" in para
    assert "really writes" not in text
    assert "best-effort shared pool" in para
    assert reserve == 20 and "that 20 percent is held back" in para
    assert f"at most {lane} percent of the share" in para
    assert slices == 12 and "one twelfth of the share" in para
    # the old ceiling's arithmetic is gone from the paragraph (the old figure is named only as history)
    for stale in ("8000", "750 clicks", "7500 over 10 rows", "(6 rows)"):
        assert stale not in para
    # the docs say the same quota the Worker defaults to
    quota = _defaults(_section())["D1_DAILY_WRITE_QUOTA"]
    assert quota == 1_600_000
    assert 50_000_000 // 31 >= quota
    assert "100,000-row free plan figure" not in text
    # the click ceiling is a documented setting with the Worker's default and range, and a Free-plan value
    row = re.search(
        r"^\| `CLICK_DAILY_WRITE_CEILING` \| (\d+) to (\d+) \| (\d+) \|", text, re.M
    )
    assert row is not None
    assert (int(row.group(1)), int(row.group(2)), int(row.group(3))) == (
        const("CLICK_CEILING_MIN"),
        const("CLICK_CEILING_MAX"),
        ceiling,
    )
    assert "set `D1_DAILY_WRITE_QUOTA` to `100000` and this to `7500`" in text
    # the free-plan pair fits: kit 42440 + page views + funnel + margin + ceiling + drop cap, under 80 percent
    free = 42_440 + _PAGE_VIEWS + _FUNNEL + _MARGIN + 7_500
    assert (
        free + const("CLICK_DROP_COUNT_CAP") + const("CLICK_SETUP_FLAG_WRITES")
        <= 100_000 * 0.8
    )
    # the secret requirement, the health condition and the lane switch are documented
    assert "The hashing secret is required on every Worker that counts clicks" in text
    assert "click_setup_invalid" in text
    assert "BEACON_LANE_CAP_ENFORCED" in text
