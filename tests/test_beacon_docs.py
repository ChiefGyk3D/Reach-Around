# SPDX-License-Identifier: Apache-2.0
"""The beacon token docs match the per-day write figures the model computes (#610)."""

from __future__ import annotations

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")
PVLIMITS = (ROOT / "worker" / "src" / "pvlimits.ts").read_text(encoding="utf-8")


def _perday(rows_num: int) -> int:
    # 24 hours x (120 human + 30 bot) views, rows_num/5 rows each, plus fixed rows.
    return math.ceil(24 * 150 * rows_num / 5) + 2_540


def test_the_per_day_figures_in_the_docs_match_the_model() -> None:
    # The model: 28/5 rows a view, plus one lane row (5/5) while BEACON_MODE is on.
    assert "num: 28, den: 5" in PVLIMITS
    assert "PV_LANE_ROWS_PER_VIEW = 1" in PVLIMITS
    off, on = _perday(28), _perday(28 + 5)
    assert (off, on) == (22_700, 26_300)
    section = DEPLOY[DEPLOY.index("## Beacon token") : DEPLOY.index("## Kit limits")]
    assert "26,300 a day instead of 22,700" in section
    assert "6.6 rows per accepted view instead of 5.6" in section


def test_the_check_is_not_vacuous() -> None:
    assert _perday(28) != _perday(33)
    assert "26,301" not in DEPLOY
