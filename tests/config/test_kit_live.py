# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 4 (#629): the live measures, their config rows and the wall.
Everything is synthetic."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import kit as kit_module
from reach_around.config.kit import (
    FieldPolicy,
    KitConfig,
    Visibility,
    public_projection,
)
from reach_around.config.measures import (
    LIVE_KEYS,
    REGISTRY,
    UNITS,
    compile_measures,
    registry_problems,
    visible,
)
from reach_around.config.trust import HAND_KEPT


def live_row(platform: str = "twitch", **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "platform": platform,
        "period_start": "2027-02-01",
        "period_end": "2027-02-28",
        "as_of": "2027-03-01",
        "avg_ccv": 40,
        "peak_ccv": 120,
        "hours_watched": 900,
        "unique_chatters": 75,
        "followers_gained": 310,
        "hours_streamed_per_week": 18,
    }
    row.update(kw)
    return row


def kit_with(*rows: dict[str, Any]) -> KitConfig:
    return KitConfig.model_validate(
        {
            "title": "Example Creator media kit",
            "about": "A fake creator used in tests.",
            "metrics": [
                {
                    "platform": "twitch",
                    "label": "Followers",
                    "value": 1200,
                    "as_of": "2027-03-01",
                }
            ],
            "live": list(rows),
        }
    )


def test_the_seven_live_measures_are_in_the_registry_with_definitions() -> None:
    assert LIVE_KEYS == (
        "followers",
        "avg_ccv",
        "peak_ccv",
        "hours_watched",
        "unique_chatters",
        "followers_gained",
        "hours_streamed_per_week",
    )
    for key in LIVE_KEYS:
        entry = REGISTRY[key]
        assert entry.unit in UNITS
        assert len(entry.definition) >= 20
        assert entry.changelog
    assert registry_problems(REGISTRY) == []


def test_a_live_row_compiles_to_six_hand_kept_measures_with_a_period() -> None:
    kit = kit_with(live_row())
    found = [m for m in compile_measures(kit, None) if m.block == "live"]
    assert [m.key for m in found] == list(LIVE_KEYS[1:])
    for m in found:
        assert m.platform == "twitch"
        assert m.period == (date(2027, 2, 1), date(2027, 2, 28))
        assert m.as_of == date(2027, 3, 1)
        assert m.visibility == "public"
    assert {m.value for m in found} == {40, 120, 900, 75, 310, 18}


def test_no_live_measure_is_api_derived() -> None:
    kit = kit_with(live_row("twitch"), live_row("kick"))
    found = [
        m
        for m in compile_measures(kit, None, frozenset({"twitch", "kick"}))
        if m.block == "live"
    ]
    assert found
    assert {m.trust.label for m in found if m.trust} == {HAND_KEPT}
    assert all(m.trust is not None for m in found)


def test_a_row_cannot_claim_an_api_source() -> None:
    for extra in ({"source": "api"}, {"trust": "api"}):
        with pytest.raises(ValidationError):
            kit_with(live_row(**extra))


def test_a_missing_figure_is_not_a_measure_and_never_zero() -> None:
    row = live_row()
    del row["peak_ccv"]
    kit = kit_with(row)
    found = [m for m in compile_measures(kit, None) if m.block == "live"]
    assert "peak_ccv" not in {m.key for m in found}


def test_only_live_platforms_and_each_once() -> None:
    with pytest.raises(ValidationError, match="twitch or kick"):
        kit_with(live_row("youtube"))
    with pytest.raises(ValidationError, match="more than once"):
        kit_with(live_row("kick"), live_row("kick"))


@pytest.mark.parametrize(
    "bad",
    [
        {"period_end": "2027-01-01"},
        {"as_of": "2027-02-10"},
        {"avg_ccv": -1},
        {"peak_ccv": 10},
        {"hours_streamed_per_week": 169},
        {"avg_ccv": 4.5},
    ],
)
def test_bad_rows_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        kit_with(live_row(**bad))


def test_figures_need_a_period() -> None:
    row = live_row()
    del row["period_start"]
    with pytest.raises(ValidationError, match="period"):
        kit_with(row)


def test_a_row_with_no_figures_is_allowed() -> None:
    kit = kit_with({"platform": "kick"})
    assert [m for m in compile_measures(kit, None) if m.block == "live"] == []


def test_live_rows_are_public_and_in_the_public_projection() -> None:
    assert kit_module.DISPLAY_POLICY["live"] == "public"
    out = public_projection(kit_with(live_row()), None)
    assert out["live"][0].platform == "twitch"


def test_the_wall_a_private_source_keeps_live_measures_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit = kit_with(live_row())
    levels: tuple[Visibility, ...] = ("gated", "private")
    for level in levels:
        policy = dict(kit_module.KIT_FIELD_POLICY)
        policy["live"] = FieldPolicy(level)
        monkeypatch.setattr(kit_module, "KIT_FIELD_POLICY", policy)
        found = [m for m in compile_measures(kit, None) if m.block == "live"]
        assert {m.visibility for m in found} == {level}
        assert [m for m in visible(found, frozenset({"public"}))] == []
        assert [m for m in visible(found, frozenset({"public", "gated"}))] == (
            [] if level == "private" else found
        )
