# SPDX-License-Identifier: Apache-2.0
"""Schedule visibility: inclusive day windows and the Eastern day."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from reach_around.config.models import Link, Section
from reach_around.site.visibility import is_visible, today_in, visible_blocks

NY = "America/New_York"


def link(
    ident: str = "a",
    show_from: date | None = None,
    show_until: date | None = None,
) -> Link:
    return Link(
        id=ident,
        title="Example",
        url="https://example.com/",
        show_from=show_from,
        show_until=show_until,
    )


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_visible_on_show_from_day() -> None:
    assert is_visible(link(show_from=date(2027, 3, 10)), date(2027, 3, 10))


def test_visible_through_show_until_day() -> None:
    assert is_visible(link(show_until=date(2027, 3, 10)), date(2027, 3, 10))


def test_hidden_the_day_after_show_until() -> None:
    assert not is_visible(link(show_until=date(2027, 3, 10)), date(2027, 3, 11))


def test_hidden_before_show_from() -> None:
    assert not is_visible(link(show_from=date(2027, 3, 10)), date(2027, 3, 9))


def test_no_dates_always_visible() -> None:
    assert is_visible(link(), date(2000, 1, 1))
    assert is_visible(link(), date(2999, 12, 31))


def test_section_with_no_visible_links_is_dropped() -> None:
    gone = link("gone", show_until=date(2027, 1, 1))
    section = Section(id="s", title="S", links=[gone])
    assert visible_blocks([section], date(2027, 1, 2)) == []


def test_section_keeps_only_its_visible_links() -> None:
    gone = link("gone", show_until=date(2027, 1, 1))
    kept = link("kept")
    section = Section(id="s", title="S", links=[gone, kept])
    out = visible_blocks([section], date(2027, 1, 2))
    assert len(out) == 1
    first = out[0]
    assert isinstance(first, Section)
    assert first.id == "s"
    assert first.links == [kept]


def test_eastern_day_across_dst_change() -> None:
    # Spring forward is 2027-03-14 02:00 EST = 07:00Z. Midnight Eastern
    # starting the 14th is 05:00Z, so a link ending the 13th is visible at
    # 04:59Z and hidden from 05:00Z, and the skipped hour changes nothing.
    scheduled = link(show_until=date(2027, 3, 13))
    last_minute = today_in(NY, utc(2027, 3, 14, 4, 59))
    first_minute = today_in(NY, utc(2027, 3, 14, 5, 0))
    assert last_minute == date(2027, 3, 13)
    assert first_minute == date(2027, 3, 14)
    assert is_visible(scheduled, last_minute)
    assert not is_visible(scheduled, first_minute)
    assert today_in(NY, utc(2027, 3, 14, 6, 30)) == date(2027, 3, 14)
    assert today_in(NY, utc(2027, 3, 14, 7, 30)) == date(2027, 3, 14)


def test_eastern_day_across_fall_back_change() -> None:
    # Fall back is 2026-11-01 02:00 EDT = 06:00Z. Midnight Eastern starting
    # Nov 1 is 04:00Z; the repeated 01:xx hour stays on Nov 1.
    assert today_in(NY, utc(2026, 11, 1, 3, 59)) == date(2026, 10, 31)
    assert today_in(NY, utc(2026, 11, 1, 4, 0)) == date(2026, 11, 1)
    assert today_in(NY, utc(2026, 11, 1, 4, 30)) == date(2026, 11, 1)
    assert today_in(NY, utc(2026, 11, 1, 5, 30)) == date(2026, 11, 1)
    assert today_in(NY, utc(2026, 11, 1, 6, 30)) == date(2026, 11, 1)


def test_today_in_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValueError):
        today_in(NY, datetime(2027, 3, 14, 6, 30))


def test_today_in_unknown_timezone_raises() -> None:
    with pytest.raises(ValueError):
        today_in("Mars/Olympus", utc(2027, 3, 14))


def test_blocks_that_are_not_links_or_sections_pass_through_unchanged() -> None:
    class Text:
        id = "t"

    text = Text()
    blocks: list[Any] = [text, link()]
    out: list[Any] = list(visible_blocks(blocks, date(2027, 1, 1)))
    assert out[0] is text
    assert len(out) == 2


def test_visible_blocks_does_not_mutate_its_input() -> None:
    gone = link("gone", show_until=date(2027, 1, 1))
    kept = link("kept")
    section = Section(id="s", title="S", links=[gone, kept])
    blocks: list[Link | Section] = [section, gone, kept]
    visible_blocks(blocks, date(2027, 1, 2))
    assert blocks == [section, gone, kept]
    assert section.links == [gone, kept]
