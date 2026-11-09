# SPDX-License-Identifier: Apache-2.0
"""Display zones and era classification (#372, #375). Synthetic data only."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from reach_around.config.era import (
    AFTER,
    BEFORE,
    SPANS,
    UNKNOWN,
    classify_era,
    classify_window,
    era_text,
    record_marker,
)
from reach_around.config.zones import (
    ZONES,
    check_zone,
    format_day_start,
    format_instant,
    utc_day_start_iso,
    utc_label,
)

CUT = date(2026, 11, 8)
TODAY = date(2026, 11, 20)


def test_utc_label_and_default() -> None:
    assert utc_label() == "UTC"
    assert (
        format_instant(datetime(2026, 11, 8, 0, 0, tzinfo=UTC))
        == "2026-11-08 00:00 UTC"
    )


def test_list_is_short_and_starts_with_utc() -> None:
    assert ZONES[0] == "UTC"
    assert len(ZONES) <= 24
    assert len(set(ZONES)) == len(ZONES)


@pytest.mark.parametrize("zone", ZONES)
def test_every_listed_zone_formats_with_a_label(zone: str) -> None:
    text = format_instant(datetime(2026, 7, 1, 12, 0, tzinfo=UTC), zone)
    assert text.startswith("2026-")
    assert text.split(" ")[-1]


@pytest.mark.parametrize(
    "bad",
    ["", "utc", "America/Nowhere", "../etc/passwd", "EST5EDT", "Asia/Tokyo ", "<b>"],
)
def test_invalid_zone_is_refused_without_echo(bad: str) -> None:
    with pytest.raises(ValueError) as err:
        check_zone(bad)
    assert bad not in str(err.value) or bad == ""
    with pytest.raises(ValueError):
        format_instant(datetime(2026, 1, 1, tzinfo=UTC), bad)


def test_non_string_zone_is_refused() -> None:
    with pytest.raises(ValueError):
        check_zone(None)  # type: ignore[arg-type]


def test_naive_instant_is_refused() -> None:
    with pytest.raises(ValueError):
        format_instant(datetime(2026, 1, 1, 0, 0))


def test_dst_boundaries_new_york() -> None:
    # US fall back: 05:00 UTC is 01:00 EDT, 06:00 UTC is 01:00 EST.
    assert format_instant(
        datetime(2026, 11, 1, 5, 0, tzinfo=UTC), "America/New_York"
    ) == ("2026-11-01 01:00 EDT")
    assert format_instant(
        datetime(2026, 11, 1, 6, 0, tzinfo=UTC), "America/New_York"
    ) == ("2026-11-01 01:00 EST")
    # Spring forward: 2026-03-08 06:59 UTC is 01:59 EST, 07:00 UTC is 03:00 EDT.
    assert format_instant(
        datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "America/New_York"
    ) == ("2026-03-08 01:59 EST")
    assert format_instant(
        datetime(2026, 3, 8, 7, 0, tzinfo=UTC), "America/New_York"
    ) == ("2026-03-08 03:00 EDT")


def test_day_crosses_into_previous_and_next_local_day() -> None:
    assert format_day_start(CUT, "America/Los_Angeles") == "2026-11-07 16:00 PST"
    assert format_day_start(CUT, "Pacific/Auckland") == "2026-11-08 13:00 NZDT"
    assert format_day_start(CUT, "Asia/Kolkata") == "2026-11-08 05:30 IST"


def test_other_offset_input_is_converted() -> None:
    plus5 = timezone(timedelta(hours=5))
    assert format_instant(datetime(2026, 11, 8, 5, 0, tzinfo=plus5), "UTC") == (
        "2026-11-08 00:00 UTC"
    )


def test_day_start_iso() -> None:
    assert utc_day_start_iso(CUT) == "2026-11-08T00:00:00Z"


# --- era ---------------------------------------------------------------------


def test_cutover_day_itself_is_after() -> None:
    assert classify_era(CUT, CUT, TODAY) == AFTER
    assert classify_era(CUT - timedelta(days=1), CUT, TODAY) == BEFORE
    assert classify_era(CUT + timedelta(days=1), CUT, TODAY) == AFTER


def test_unset_or_future_cutover_or_no_date_is_unknown() -> None:
    assert classify_era(CUT, None, TODAY) == UNKNOWN
    assert classify_era(CUT, date(2026, 12, 1), TODAY) == UNKNOWN
    assert classify_era(None, CUT, TODAY) == UNKNOWN
    assert era_text(UNKNOWN) == ""


def test_cutover_today_is_active() -> None:
    assert classify_era(CUT, CUT, CUT) == AFTER


def test_texts() -> None:
    assert era_text(BEFORE) == "before Reach Around"
    assert era_text(AFTER) == "after"


def test_window_classes() -> None:
    d = date.fromisoformat
    assert classify_window(d("2026-11-01"), d("2026-11-07"), CUT, TODAY) == BEFORE
    assert classify_window(d("2026-11-08"), d("2026-11-10"), CUT, TODAY) == AFTER
    assert classify_window(d("2026-11-07"), d("2026-11-08"), CUT, TODAY) == SPANS
    assert classify_window(d("2026-11-01"), d("2026-11-07"), None, TODAY) == UNKNOWN
    assert classify_window(None, None, CUT, TODAY) == UNKNOWN
    assert (
        classify_window(d("2026-11-01"), d("2026-11-07"), date(2027, 1, 1), TODAY)
        == UNKNOWN
    )


def test_record_marker_prefers_note_then_era() -> None:
    assert (
        record_marker("definition-change", "Kick subs changed") == "Kick subs changed"
    )
    assert record_marker("definition-change", None) == "definition-change"
    assert record_marker(None, None) == ""
