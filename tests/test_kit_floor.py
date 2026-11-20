# SPDX-License-Identifier: Apache-2.0
"""The privacy floor in the kit (#397): a count of people or views under 5 is
never printed on the page or in either PDF. Synthetic data only."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kitpdfharness import (
    TODAY,
    build_instance,
    fake_renderer,
    kit_doc,
    make_config,
)
from reach_around.config.imported_history import ImportedHistory, display_rows
from reach_around.config.kit import ResolvedMetric, UnifiedTotal
from reach_around.funnel.floor import UNDER_FLOOR, shown_count
from reach_around.kit.pdf import PdfError, build_public_kit_pdf, check_text
from reach_around.kit.pdftext import extract_pages
from reach_around.site.kit import _analytics_rows, _metric_rows, _total

CREATED = date(2027, 3, 10)


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (1, UNDER_FLOOR), (4, UNDER_FLOOR), (5, "5"), (1234, "1,234")],
)
def test_a_count_under_five_is_never_printed(value: int, text: str) -> None:
    assert shown_count(value) == text


def metric(value: int | None) -> ResolvedMetric:
    return ResolvedMetric("twitch", "Followers", "manual", value, TODAY, True)


@pytest.mark.parametrize(("value", "text"), [(4, UNDER_FLOOR), (5, "5"), (0, "0")])
def test_a_headline_metric_follows_the_floor(value: int, text: str) -> None:
    rows = _metric_rows([metric(value)])
    assert rows[0]["value"] == text


def total_of(*values: int | None) -> dict[str, Any] | None:
    members = [metric(v) for v in values]
    counted = [v for v in values if v is not None]
    return _total(UnifiedTotal(sum(counted), len(counted), 0), members)


def test_a_total_is_hidden_when_a_member_is_under_the_floor() -> None:
    # 12 minus the printed 7 would isolate the 4 and the 1 hidden behind "Under 5"
    assert total_of(7, 4, 1) is None
    assert total_of(7, 5) is not None
    assert total_of(5, 4) is None
    assert total_of(5, 5) is not None
    assert total_of(7, 0)["value"] == "7"  # type: ignore[index]
    assert total_of(5, 5)["value"] == "10"  # type: ignore[index]


def test_a_member_outside_the_total_does_not_hide_it() -> None:
    members = [metric(9), metric(4)]
    members[1] = ResolvedMetric("twitch", "Followers", "manual", 4, TODAY, False)
    assert _total(UnifiedTotal(9, 1, 0), members) is not None


class Row:
    platform = "twitch"
    synced_with = "Example Social API"
    as_of = date(2027, 3, 8)

    def __init__(self, followers: int, stat: int, pct: float = 50.0) -> None:
        self.followers = followers
        self.followers_30_day_percent = pct
        self.stats = [type("S", (), {"label": "Example views", "value": stat})()]


def test_analytics_followers_and_stats_follow_the_floor() -> None:
    low = _analytics_rows([Row(4, 4)], TODAY)[0]
    assert low["followers"] == UNDER_FLOOR
    assert low["stats"][0]["value"] == UNDER_FLOOR
    ok = _analytics_rows([Row(5, 5)], TODAY)[0]
    assert ok["followers"] == "5"
    assert ok["stats"][0]["value"] == "5"


@pytest.mark.parametrize(
    ("followers", "pct", "printed"),
    [
        (5, 25.0, False),  # the count before was 4
        (6, 20.0, True),  # before: 5
        (1000, 0.5, True),  # before: about 995
        (4, 50.0, False),
        (5, -50.0, True),  # before: 10
        (2, 300.0, False),
        (50, 1150.0, False),  # before: about 4
        (50, 900.0, True),  # before: 5
        (0, -100.0, True),
    ],
)
def test_the_change_never_pins_a_count_under_the_floor(
    followers: int, pct: float, printed: bool
) -> None:
    delta = _analytics_rows([Row(followers, 9, pct)], TODAY)[0]["delta"]
    assert bool(delta) is printed


def test_the_change_is_not_printed_when_the_followers_are_under_the_floor() -> None:
    low = _analytics_rows([Row(4, 9, 33.3)], TODAY)[0]
    assert "33.3" not in low["delta"]
    assert _analytics_rows([Row(50, 9, 33.3)], TODAY)[0]["delta"] == "+33.3%"


def flat(path: Path) -> str:
    return "".join(" ".join(extract_pages(path.read_bytes())).split())


def small_kit() -> dict[str, Any]:
    doc = kit_doc()
    doc["metrics"] = [
        {"platform": "twitch", "label": "Followers", "value": 4, "as_of": "2027-03-01"},
        {"platform": "kick", "label": "Followers", "value": 5, "as_of": "2027-03-01"},
    ]
    doc["analytics"] = [
        {
            "platform": "twitch",
            "synced_with": "Example Social API",
            "as_of": "2027-03-08",
            "followers": 3,
            "followers_30_day_percent": 41.7,
            "stats": [{"label": "Example views", "value": 2}],
        }
    ]
    return doc


def test_the_public_pdf_and_page_print_nothing_under_the_floor(tmp_path: Path) -> None:
    config = make_config(small_kit())
    _, site, _ = build_instance(tmp_path, config)
    page = (site / "kit" / "index.html").read_text(encoding="utf-8")
    assert "41.7" not in page
    out = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    text = flat(out)
    assert "41.7" not in text
    assert "".join(UNDER_FLOOR.split()) in text
    assert "Followers4" not in text
    assert "Followers3" not in text
    assert "Exampleviews2" not in text
    assert "Followers5" in text


def test_the_text_check_fails_when_a_printed_count_is_not_the_floored_form() -> None:
    config = make_config(small_kit())
    good = (
        "Example Creator media kit A fake creator used in tests. "
        "Streams games & talks. Twitch Followers Under 5 2027-03-01 "
        "Kick Followers 5 2027-03-01 9 Under 5 Example views Under 5"
    )
    check_text(config.kit, good, public=True)  # type: ignore[arg-type]
    raw = good.replace("Twitch Followers Under 5", "Twitch Followers 4")
    with pytest.raises(PdfError):
        check_text(config.kit, raw, public=True)  # type: ignore[arg-type]


def test_imported_history_counts_follow_the_floor() -> None:
    history = ImportedHistory.model_validate(
        {
            "version": 1,
            "source": "imported",
            "note": "Beacons",
            "platform": "link-page",
            "periods": [
                {
                    "start": "2026-01-01",
                    "end": "2026-01-31",
                    "days": 31,
                    "views": 4,
                    "clicks": 5,
                }
            ],
        }
    )
    row = display_rows(history, None)[0]
    assert (row["views"], row["clicks"]) == (UNDER_FLOOR, "5")


def engagement_value(views: int, posts: int) -> str:
    from reach_around.config.engagement import rows_for
    from reach_around.config.kit import EngagementRow

    row = EngagementRow.model_validate(
        {
            "platform": "twitch",
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "as_of": "2027-03-01",
            "views": views,
            "posts": posts,
            "interactions": 1,
        }
    )
    return str(rows_for([row])[0]["value"])


def test_an_average_views_figure_follows_the_floor() -> None:
    assert engagement_value(9, 3) == UNDER_FLOOR
    assert engagement_value(15, 3) == "5"
    assert engagement_value(0, 3) == "0"
