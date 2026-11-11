# SPDX-License-Identifier: Apache-2.0
"""Engagement and average-views rows: the model, the formulas and the rounding (#423).

Every number here is made up. The expected values are worked out by hand in the
comments, never by calling the code under test.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config.engagement import (
    AVERAGE_VIEWS_FORMULA,
    ENGAGEMENT_FORMULA,
    NOT_AVAILABLE,
    average_views,
    engagement_percent,
    engagement_rows,
)
from reach_around.config.kit import (
    DISPLAY_POLICY,
    EngagementRow,
    KitConfig,
    gated_projection,
    kit_warnings,
    policy_gaps,
    public_projection,
    visibility,
)

TODAY = date(2027, 3, 10)


def kit_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests.",
    }
    doc.update(kw)
    return doc


def row(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "platform": "twitch",
        "period_start": "2027-02-01",
        "period_end": "2027-02-28",
        "as_of": "2027-03-01",
        "views": 12000,
        "posts": 8,
        "interactions": 600,
    }
    doc.update(kw)
    return doc


# --- the arithmetic, with known values --------------------------------------


@pytest.mark.parametrize(
    ("views", "posts", "expected"),
    [
        (12000, 8, 1500),  # exact
        (1000, 3, 333),  # 333.33 rounds down
        (2000, 3, 667),  # 666.67 rounds up
        (5, 2, 3),  # 2.5 is a tie: half rounds up
        (7, 2, 4),  # 3.5 tie
        (3, 2, 2),  # 1.5 tie
        (1, 3, 0),  # 0.33
        (2, 3, 1),  # 0.67
        (0, 5, 0),
        (10**12, 1, 10**12),
        (10**12, 7, 142857142857),  # 142857142857.14
    ],
)
def test_average_views_known_values(views: int, posts: int, expected: int) -> None:
    assert average_views(views, posts) == expected


@pytest.mark.parametrize(
    ("interactions", "views", "expected"),
    [
        (600, 12000, "5.0%"),  # exactly 5
        (37, 1200, "3.1%"),  # 3.0833
        (1, 3, "33.3%"),  # 33.333
        (2, 3, "66.7%"),  # 66.667
        (1, 2000, "0.1%"),  # 0.05 is a tie: half rounds up
        (3, 2000, "0.2%"),  # 0.15 tie
        (1, 1600, "0.1%"),  # 0.0625
        (1, 4000, "0.0%"),  # 0.025
        (0, 500, "0.0%"),
        (250, 100, "250.0%"),  # more interactions than views is allowed
        (10**12, 10**12, "100.0%"),
    ],
)
def test_engagement_known_values(interactions: int, views: int, expected: str) -> None:
    assert engagement_percent(interactions, views) == expected


def test_a_missing_or_zero_denominator_is_not_available_never_an_error() -> None:
    assert average_views(None, 4) is None
    assert average_views(100, None) is None
    assert engagement_percent(None, 100) is None
    assert engagement_percent(5, None) is None
    assert engagement_percent(5, 0) is None


def test_the_arithmetic_is_exact_not_float() -> None:
    # 0.29 * 100 is 28.999999999999996 in floating point; exact is 29.0 -> 29.0%
    assert engagement_percent(29, 100) == "29.0%"
    # 1.005 style trap: 1005 / 100000 * 100 = 1.005 -> half up to one place is 1.0
    assert engagement_percent(1005, 100000) == "1.0%"
    # 2.675 trap for whole numbers: 2675 views over 1000 posts is 2.675 -> 3
    assert average_views(2675, 1000) == 3


def test_the_same_inputs_give_the_same_text_every_time() -> None:
    first = [engagement_percent(37, 1200) for _ in range(5)]
    assert len(set(first)) == 1


# --- the model --------------------------------------------------------------


def test_a_row_validates_and_the_default_is_none() -> None:
    kit = KitConfig.model_validate(kit_doc(engagement=[row()]))
    assert kit.engagement[0].views == 12000
    assert KitConfig.model_validate(kit_doc()).engagement == []


@pytest.mark.parametrize("field", ["views", "interactions"])
def test_engagement_figures_accept_the_documented_upper_bound(field: str) -> None:
    assert (
        getattr(EngagementRow.model_validate(row(**{field: 10**12})), field) == 10**12
    )
    with pytest.raises(ValidationError):
        EngagementRow.model_validate(row(**{field: 10**12 + 1}))


def test_a_row_with_no_figures_is_a_platform_with_no_source() -> None:
    kick = EngagementRow.model_validate({"platform": "kick"})
    assert kick.views is None and kick.as_of is None


def test_figures_need_a_period_and_an_as_of() -> None:
    for missing in ("period_start", "period_end", "as_of"):
        doc = row()
        del doc[missing]
        with pytest.raises(ValidationError, match="period"):
            EngagementRow.model_validate(doc)


def test_the_period_is_ordered_and_bounded() -> None:
    with pytest.raises(ValidationError, match="before"):
        EngagementRow.model_validate(row(period_end="2027-01-31"))
    assert EngagementRow.model_validate(row(period_end="2027-02-01"))
    with pytest.raises(ValidationError, match="366"):
        EngagementRow.model_validate(
            row(period_start="2026-01-01", period_end="2027-01-02")
        )
    assert EngagementRow.model_validate(
        row(period_start="2026-01-01", period_end="2027-01-01")
    )


def test_as_of_is_not_before_the_period_ends() -> None:
    with pytest.raises(ValidationError, match="as_of"):
        EngagementRow.model_validate(row(as_of="2027-02-27"))


@pytest.mark.parametrize(
    "bad",
    [
        {"views": -1},
        {"views": 10**12 + 1},
        {"views": 1.5},
        {"views": "100"},
        {"views": True},
        {"posts": 0},
        {"posts": -3},
        {"posts": 1_000_001},
        {"posts": 2.5},
        {"interactions": -1},
        {"interactions": "5"},
        {"platform": "nowhere"},
        {"platform": 5},
        {"period_start": "2027-02-01T00:00:00"},
        {"period_start": "01/02/2027"},
        {"as_of": 20270301},
        {"surprise": 1},
    ],
)
def test_hostile_values_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EngagementRow.model_validate(row(**bad))


@pytest.mark.parametrize(
    "text",
    [
        "<b>x</b>",
        "a < b",
        "bad" + chr(0) + "byte",
        "zero" + chr(0x200B) + "width",
        " leading",
        "",
    ],
)
def test_the_counts_note_is_plain_text(text: str) -> None:
    with pytest.raises(ValidationError):
        EngagementRow.model_validate(row(counts=text))
    assert EngagementRow.model_validate(row(counts="a" * 120))
    with pytest.raises(ValidationError, match="string_too_long"):
        EngagementRow.model_validate(row(counts="a" * 121))


def test_list_cap_and_repeats() -> None:
    names = ["twitch", "youtube", "kick", "bluesky", "mastodon", "tiktok"]
    many = [row(platform=n) for n in names]
    assert KitConfig.model_validate(kit_doc(engagement=many))
    with pytest.raises(ValidationError, match="more than once"):
        KitConfig.model_validate(kit_doc(engagement=[row(), row()]))
    assert KitConfig.model_validate(
        kit_doc(
            engagement=[
                row(),
                row(period_start="2027-01-01", period_end="2027-01-31"),
            ]
        )
    )
    cap = [
        row(
            platform="twitch",
            period_start=f"2026-{m:02d}-01",
            period_end=f"2026-{m:02d}-02",
        )
        for m in range(1, 13)
    ]
    assert KitConfig.model_validate(kit_doc(engagement=cap))
    with pytest.raises(ValidationError, match="too_long"):
        KitConfig.model_validate(
            kit_doc(
                engagement=[
                    row(
                        platform=n,
                        period_start=f"2026-{m:02d}-01",
                        period_end=f"2026-{m:02d}-02",
                    )
                    for n in ("twitch", "youtube", "kick")
                    for m in range(1, 12)
                ]
            )
        )


def test_engagement_is_gated_by_policy() -> None:
    assert visibility("engagement") == "gated"
    assert DISPLAY_POLICY["engagement"] == "gated"
    assert policy_gaps(KitConfig.model_fields) == []
    kit = KitConfig.model_validate(kit_doc(engagement=[row()]))
    assert "engagement" not in public_projection(kit, None)
    assert gated_projection(kit, None)["engagement"] == kit.engagement
    assert kit.has_gated_content


def test_engagement_policy_marks_owner_report_only() -> None:
    from reach_around.config.kit import KIT_FIELD_POLICY

    assert KIT_FIELD_POLICY["engagement"].owner_report_only


# --- the rows the pages print -------------------------------------------------


def test_rows_carry_platform_measure_formula_period_and_date() -> None:
    kit = KitConfig.model_validate(
        kit_doc(engagement=[row(counts="Likes and replies")])
    )
    avg, eng = engagement_rows(kit)
    assert avg == {
        "platform": "Twitch",
        "measure": "Average views per post",
        "formula": AVERAGE_VIEWS_FORMULA,
        "value": "1,500",  # 12000 / 8
        "period": "2027-02-01 to 2027-02-28",
        "start": "2027-02-01",
        "end": "2027-02-28",
        "as_of": "2027-03-01",
        "provenance": "",
        "note": "",
    }
    assert eng["measure"] == "Engagement rate"
    assert eng["value"] == "5.0%"  # 600 / 12000 * 100
    assert eng["formula"] == ENGAGEMENT_FORMULA
    assert eng["note"] == "Interactions counted: Likes and replies"
    assert eng["period"] == avg["period"] and eng["as_of"] == avg["as_of"]


def test_a_platform_with_no_source_says_not_available_for_both_figures() -> None:
    kit = KitConfig.model_validate(kit_doc(engagement=[{"platform": "kick"}]))
    rows = engagement_rows(kit)
    assert [r["platform"] for r in rows] == ["Kick", "Kick"]
    assert [r["value"] for r in rows] == [NOT_AVAILABLE, NOT_AVAILABLE]
    assert all(r["period"] == "" and r["as_of"] == "" for r in rows)
    assert NOT_AVAILABLE == "not available"


def test_a_figure_that_cannot_be_worked_out_is_not_available_alone() -> None:
    kit = KitConfig.model_validate(kit_doc(engagement=[row(posts=None)]))
    avg, eng = engagement_rows(kit)
    assert avg["value"] == NOT_AVAILABLE
    assert eng["value"] == "5.0%"
    kit = KitConfig.model_validate(kit_doc(engagement=[row(views=0)]))
    avg, eng = engagement_rows(kit)
    assert avg["value"] == "0" and eng["value"] == NOT_AVAILABLE


def test_twitch_and_kick_rows_have_the_same_shape() -> None:
    kit = KitConfig.model_validate(
        kit_doc(engagement=[row(platform="twitch"), row(platform="kick")])
    )
    twitch, kick = engagement_rows(kit)[:2], engagement_rows(kit)[2:]
    assert [set(r) for r in twitch] == [set(r) for r in kick]
    assert [r["measure"] for r in twitch] == [r["measure"] for r in kick]
    assert [r["value"] for r in twitch] == [r["value"] for r in kick]


def test_no_figure_is_ever_added_across_platforms() -> None:
    kit = KitConfig.model_validate(
        kit_doc(engagement=[row(platform="twitch"), row(platform="youtube", views=100)])
    )
    values = [r["value"] for r in engagement_rows(kit)]
    assert len(values) == 4  # one per figure per platform, no total row
    assert all(r["platform"] in {"Twitch", "YouTube"} for r in engagement_rows(kit))


def test_old_and_future_figures_warn() -> None:
    kit = KitConfig.model_validate(
        kit_doc(
            engagement=[
                row(
                    period_start="2026-12-01",
                    period_end="2026-12-31",
                    as_of="2027-01-15",
                )
            ]
        )
    )
    warnings = kit_warnings(kit, None, TODAY)
    assert any("engagement" in w and "2027-01-15" in w for w in warnings)
    future = KitConfig.model_validate(
        kit_doc(
            engagement=[
                row(
                    period_start="2027-04-01",
                    period_end="2027-04-30",
                    as_of="2027-05-01",
                )
            ]
        )
    )
    assert any("future" in w for w in kit_warnings(future, None, TODAY))
    fresh = KitConfig.model_validate(kit_doc(engagement=[row(as_of="2027-03-05")]))
    assert not [w for w in kit_warnings(fresh, None, TODAY) if "engagement" in w]
    none = KitConfig.model_validate(kit_doc(engagement=[{"platform": "kick"}]))
    assert kit_warnings(none, None, TODAY) == []
