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


def test_a_total_under_five_is_not_printed_and_five_is() -> None:
    low = [metric(2), metric(2)]
    assert _total(UnifiedTotal(4, 2, 0), low)["value"] == UNDER_FLOOR  # type: ignore[index]
    assert _total(UnifiedTotal(5, 2, 0), low)["value"] == "5"  # type: ignore[index]


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
