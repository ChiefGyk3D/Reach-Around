# SPDX-License-Identifier: Apache-2.0
"""Public per-platform analytics tiles for the media kit (#546)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, kit_doc, make_config

from reach_around.config.kit import public_projection


def analytics_rows() -> list[dict[str, Any]]:
    return [
        {
            "platform": "instagram",
            "synced_with": "Example Social API",
            "as_of": "2027-03-08",
            "followers": 12_460,
            "followers_30_day_percent": 4.26,
            "stats": [{"label": "Example views", "value": 1_260_000}],
        },
        {
            "platform": "tiktok",
            "synced_with": "Example Video API",
            "as_of": "2027-03-07",
            "followers": 999,
            "followers_30_day_percent": -1.25,
            "stats": [],
        },
        {
            "platform": "threads",
            "synced_with": "Example Threads API",
            "as_of": "2027-03-06",
            "followers": 1000,
            "followers_30_day_percent": 0,
            "stats": [],
        },
        {
            "platform": "twitch",
            "synced_with": "Example Stream API",
            "as_of": "2027-03-05",
            "followers": 2_560_000,
            "followers_30_day_percent": 10.06,
            "stats": [{"label": "Example sessions", "value": 21_550}],
        },
    ]


def test_public_analytics_render_tabs_tiles_formats_and_dates(tmp_path: Path) -> None:
    out, _ = build(
        tmp_path,
        make_config(kit_doc(analytics=analytics_rows()), features={"kit_gate": False}),
        gated=False,
    )
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")

    assert 'aria-label="Platform analytics"' in html
    for platform in ("Instagram", "TikTok", "Threads", "Twitch"):
        assert f">{platform}</a>" in html
        assert f">{platform} analytics</h4>" in html
    assert html.index(">Instagram</a>") < html.index(">TikTok</a>")
    assert html.index(">TikTok</a>") < html.index(">Threads</a>")
    assert html.index(">Threads</a>") < html.index(">Twitch</a>")

    assert ">12.5k</strong>" in html
    assert ">999</strong>" in html
    assert ">1.0k</strong>" in html
    assert ">2.6M</strong>" in html
    assert ">1.3M</strong>" in html
    assert ">21.6k</strong>" in html
    assert "+4.3% last 30 days" in html
    assert "-1.2% last 30 days" in html
    assert "+0.0% last 30 days" in html
    assert "+10.1% last 30 days" in html

    assert "Synced with Example Social API on " in html
    assert '<time datetime="2027-03-08">2027-03-08</time>' in html
    assert 'Last updated <time datetime="2027-03-10">2027-03-10</time>' in html


def test_analytics_is_public_but_owner_report_only_data_stays_out() -> None:
    raw = kit_doc(analytics=analytics_rows())
    raw["engagement"] = [{"platform": "twitch"}]
    config = make_config(raw)

    public = public_projection(config.kit, None)  # type: ignore[arg-type]

    assert public["analytics"] == config.kit.analytics  # type: ignore[union-attr]
    assert "engagement" not in public


@pytest.mark.parametrize(
    "public_value",
    ["Example Social API", "Example views", "2027-03-09"],
)
def test_gated_text_equal_to_public_analytics_is_not_a_leak(
    tmp_path: Path, public_value: str
) -> None:
    config = make_config(
        kit_doc(
            analytics=[analytics_rows()[0] | {"as_of": "2027-03-09"}],
            partners=[{"name": public_value}],
        )
    )

    out, _ = build(tmp_path, config)

    assert public_value in (out / "kit" / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", ["youtube", "kick", "bluesky"])
def test_analytics_refuses_platforms_outside_the_public_tab_set(platform: str) -> None:
    row = analytics_rows()[0] | {"platform": platform}

    with pytest.raises(ValidationError, match=r"analytics.*platform"):
        make_config(kit_doc(analytics=[row]))


def test_analytics_rejects_duplicate_platform_rows() -> None:
    row = analytics_rows()[0]

    with pytest.raises(ValidationError, match=r"analytics row.*listed more than once"):
        make_config(kit_doc(analytics=[row, row]))
