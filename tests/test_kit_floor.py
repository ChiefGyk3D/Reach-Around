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
from reach_around.config.kit import (
    AnalyticsStat,
    PlatformAnalytics,
    ResolvedMetric,
    UnifiedTotal,
)
from reach_around.config.measures import analytics_measures, numbers_measures
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
    rows = _metric_rows(numbers_measures([metric(value)]))
    assert rows[0]["value"] == text


def total_of(*values: int | None) -> dict[str, Any] | None:
    members = [metric(v) for v in values]
    counted = [v for v in values if v is not None]
    return _total(
        UnifiedTotal(sum(counted), len(counted), 0), numbers_measures(members)
    )


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
    assert _total(UnifiedTotal(9, 1, 0), numbers_measures(members)) is not None


def analytics_row(followers: int, stat: int, pct: float = 50.0) -> PlatformAnalytics:
    return PlatformAnalytics(
        platform="twitch",
        synced_with="Example Social API",
        as_of=date(2027, 3, 8),
        followers=followers,
        followers_30_day_percent=pct,
        stats=[AnalyticsStat(label="Example views", value=stat)],
    )


def test_analytics_followers_and_stats_follow_the_floor() -> None:
    low = _analytics_rows(analytics_measures([analytics_row(4, 4)]), TODAY)[0]
    assert low["followers"] == UNDER_FLOOR
    assert low["stats"][0]["value"] == UNDER_FLOOR
    ok = _analytics_rows(analytics_measures([analytics_row(5, 5)]), TODAY)[0]
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
        (0, -100.0, False),
    ],
)
def test_the_change_never_pins_a_count_under_the_floor(
    followers: int, pct: float, printed: bool
) -> None:
    delta = _analytics_rows(
        analytics_measures([analytics_row(followers, 9, pct)]), TODAY
    )[0]["delta"]
    assert bool(delta) is printed


def test_the_change_is_not_printed_when_the_followers_are_under_the_floor() -> None:
    low = _analytics_rows(analytics_measures([analytics_row(4, 9, 33.3)]), TODAY)[0]
    assert "33.3" not in low["delta"]
    assert (
        _analytics_rows(analytics_measures([analytics_row(50, 9, 33.3)]), TODAY)[0][
            "delta"
        ]
        == "+33.3%"
    )


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
        "Kick Followers 5 2027-03-01 9 Twitch analytics "
        "Synced with Example Social API on 2027-03-08 "
        "Followers Under 5 30-day change not shown Example views Under 5"
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


# --- the change text: every earlier count the printed text allows -------------


def pins_small_earlier_count(followers: int, text: str) -> bool:
    """True if some earlier count of 1 to 4 would print exactly ``text``."""
    return any(
        f"{(followers - earlier) / earlier * 100:+.1f}%" == text
        for earlier in range(1, 5)
    )


def test_a_typed_two_decimal_percent_cannot_pin_an_earlier_count() -> None:
    # the owner types 66.72 and the page prints +66.7%: 3 was the earlier count
    assert (
        _analytics_rows(analytics_measures([analytics_row(5, 9, 66.72)]), TODAY)[0][
            "delta"
        ]
        == ""
    )
    checked = 0
    for followers in range(1, 401):
        for earlier in range(1, 5):
            exact = (followers - earlier) / earlier * 100
            if exact > 1_000_000:
                continue
            for step in range(-60, 61):
                typed = round(exact + step / 100, 2)
                if typed < -100:
                    continue
                delta = _analytics_rows(
                    analytics_measures([analytics_row(followers, 9, typed)]), TODAY
                )[0]["delta"]
                checked += 1
                if delta:
                    assert not pins_small_earlier_count(followers, delta), (
                        followers,
                        typed,
                        delta,
                    )
    assert checked > 100_000


def test_a_large_change_still_prints() -> None:
    assert (
        _analytics_rows(analytics_measures([analytics_row(1000, 9, 0.5)]), TODAY)[0][
            "delta"
        ]
        == "+0.5%"
    )
    assert (
        _analytics_rows(analytics_measures([analytics_row(400, 9, 10.0)]), TODAY)[0][
            "delta"
        ]
        == "+10.0%"
    )


# --- every other printed count -------------------------------------------------


def test_recent_post_and_case_study_counts_follow_the_floor() -> None:
    from reach_around.site.kit import _case_rows, _post_rows

    post = type(
        "P",
        (),
        {
            "platform": "twitch",
            "thumbnail": "t.png",
            "views": 4,
            "likes": 3,
            "comments": 2,
            "reposts": 1,
            "quotes": 4,
        },
    )()
    row = _post_rows([post])["Twitch"][0]
    assert [row[k] for k in ("views", "likes", "comments", "reposts", "quotes")] == [
        UNDER_FLOOR
    ] * 5
    edge = type(
        "P",
        (),
        {
            "platform": "twitch",
            "thumbnail": "t.png",
            "views": 5,
            "likes": 0,
            "comments": 1234,
            "reposts": None,
            "quotes": None,
        },
    )()
    out = _post_rows([edge])["Twitch"][0]
    assert (out["views"], out["likes"], out["comments"]) == ("5", "0", "1,234")
    assert (out["reposts"], out["quotes"]) == (None, None)
    case = type(
        "C",
        (),
        {
            "thumbnails": [],
            "views": 1,
            "likes": 2,
            "comments": 4,
            "period_start": date(2027, 2, 1),
            "period_end": date(2027, 2, 28),
        },
    )()
    out = _case_rows([case])[0]
    assert [out[k] for k in ("views", "likes", "comments")] == [UNDER_FLOOR] * 3
    case.views, case.likes, case.comments = 5, 0, 1234
    out = _case_rows([case])[0]
    assert (out["views"], out["likes"], out["comments"]) == ("5", "0", "1,234")


@pytest.mark.parametrize(
    ("count", "short"),
    [
        (0, "0"),
        (1, UNDER_FLOOR),
        (4, UNDER_FLOOR),
        (5, "5"),
        (999, "999"),
        (1500, "1.5k"),
    ],
)
def test_the_platform_short_count_follows_the_floor(count: int, short: str) -> None:
    from reach_around.site.platforms import abbreviate

    assert abbreviate(count) == short


# --- percentages beside a printed count ------------------------------------------


def demo_doc(base: int, male: int = 3) -> dict[str, Any]:
    doc = kit_doc()
    doc["metrics"] = [
        {
            "platform": "instagram",
            "label": "Followers",
            "value": base,
            "as_of": "2027-03-01",
        }
    ]
    doc["analytics"] = []
    doc["demographics"] = [
        {
            "platform": "instagram",
            "as_of": "2027-03-01",
            "gender": {"male": male, "female": 100 - male},
            "ages": [
                {"range": "18-24", "all": 3, "male": 3, "female": 3},
                {"range": "25-34", "all": 97, "male": 97, "female": 97},
            ],
            "countries": [{"name": "Exampleland", "percentage": 70}],
            "cities": [{"name": "Smalltown", "percentage": 3}],
        }
    ]
    return doc


def gated_text(tmp_path: Path, doc: dict[str, Any]) -> str:
    _, _, gated = build_instance(tmp_path, make_config(doc))
    return (gated / "kit.html").read_text(encoding="utf-8")


def test_a_share_that_pins_a_small_count_is_withheld_in_the_kit(
    tmp_path: Path,
) -> None:
    page = gated_text(tmp_path, demo_doc(100))
    assert "3% male" not in page and "97% female" not in page
    assert "Smalltown" not in page  # a group with a pinned cell is withheld whole
    assert "Exampleland" in page  # a group with none is not
    assert "not shown" in page.lower()


def test_a_large_audience_keeps_its_shares(tmp_path: Path) -> None:
    page = gated_text(tmp_path, demo_doc(12_000))
    assert "3% male" in page and "Exampleland" in page and "Smalltown" in page


@pytest.mark.parametrize(
    ("base", "percent", "hidden"),
    [
        (100, 3, True),  # about 3 people
        (100, 4, True),  # about 4 people
        (100, 5, False),  # 4.5 to 5.5 people: 5
        (100, 0, False),  # under one person: zero
        (400, 0, True),  # at most 2 people
        (400, 1, False),  # 2 to 6 people
        (12_000, 3, False),
        (50, 9, True),  # 4.25 to 4.75
        (50, 10, False),
    ],
)
def test_the_share_rule_boundaries(base: int, percent: int, hidden: bool) -> None:
    from reach_around.funnel.floor import share_pins_small_count

    assert share_pins_small_count(percent, 100, base) is hidden


def test_the_kit_pdf_text_check_skips_a_withheld_group(tmp_path: Path) -> None:
    from reach_around.kit.pdf import build_kit_pdf
    from reach_around.kit.pdftext import extract_pages

    config = make_config(demo_doc(100))
    _, site, gated = build_instance(tmp_path, config)
    out = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "o",
        name="k.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    text = "".join(" ".join(extract_pages(out.read_bytes())).split())
    assert "Smalltown" not in text and "3%male" not in text
    assert "Exampleland" in text and "Followers100" in text


# --- the analytics tile in the PDF text ---------------------------------------


def tile_kit() -> dict[str, Any]:
    doc = kit_doc()
    doc["metrics"] = []
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


def tile_text(delta: str, followers: str = "Under 5", stat: str = "Under 5") -> str:
    return (
        "Example Creator media kit A fake creator used in tests. "
        "Streams games & talks. Twitch analytics "
        "Synced with Example Social API on 2027-03-08 "
        f"Followers {followers} {delta} Example views {stat}"
    )


def test_the_pdf_text_check_reads_the_analytics_tile_alone() -> None:
    config = make_config(tile_kit())
    good = tile_text("30-day change not shown")
    check_text(config.kit, good, public=True)  # type: ignore[arg-type]
    for bad in (
        tile_text("30-day change not shown", followers="3"),
        tile_text("+41.7% last 30 days"),
        tile_text("30-day change not shown", stat="2"),
        good.replace("Synced with Example Social API on 2027-03-08", ""),
    ):
        with pytest.raises(PdfError):
            check_text(config.kit, bad, public=True)  # type: ignore[arg-type]


def test_an_unrelated_metric_row_does_not_satisfy_the_analytics_tile() -> None:
    doc = tile_kit()
    doc["metrics"] = [
        {"platform": "twitch", "label": "Followers", "value": 4, "as_of": "2027-03-01"}
    ]
    config = make_config(doc)
    text = (
        "Example Creator media kit A fake creator used in tests. "
        "Streams games & talks. Twitch Followers Under 5 2027-03-01 "
        "Twitch analytics Synced with Example Social API on 2027-03-08 "
        "Followers 3 30-day change not shown Example views Under 5"
    )
    with pytest.raises(PdfError):
        check_text(config.kit, text, public=True)  # type: ignore[arg-type]


def _demo(all_: int, male: int, female: int) -> Any:
    from reach_around.config.kit import Demographic

    return Demographic.model_validate(
        {
            "platform": "instagram",
            "as_of": "2027-03-01",
            "gender": {"male": 50, "female": 50},
            "ages": [
                {"range": "18-24", "all": all_, "male": male, "female": female},
                {
                    "range": "25-34",
                    "all": 100 - all_,
                    "male": 100 - male,
                    "female": 100 - female,
                },
            ],
            "countries": [{"name": "Exampleland", "percentage": 50}],
        }
    )


def test_each_age_column_is_checked_on_its_own() -> None:
    from reach_around.site.kit import demographic_withheld

    assert demographic_withheld(_demo(10, 10, 10), [100]) == frozenset()
    assert demographic_withheld(_demo(3, 10, 10), [100]) == {"ages"}  # all column
    assert demographic_withheld(_demo(10, 3, 10), [100]) == {"ages"}  # male column
    assert demographic_withheld(_demo(10, 10, 3), [100]) == {"ages"}  # female column
    assert demographic_withheld(_demo(3, 3, 3), []) == frozenset()  # no printed count


# --- every printed count of the platform is a base ---------------------------------


def place_doc() -> dict[str, Any]:
    """Only the countries group has a small cell; no kit number for instagram."""
    doc = kit_doc()
    doc["metrics"] = []
    doc["analytics"] = []
    doc["demographics"] = [
        {
            "platform": "instagram",
            "as_of": "2027-03-01",
            "gender": {"male": 50, "female": 50},
            "ages": [
                {"range": "18-24", "all": 50, "male": 50, "female": 50},
                {"range": "25-34", "all": 50, "male": 50, "female": 50},
            ],
            "countries": [
                {"name": "Exampleland", "percentage": 3},
                {"name": "Otherland", "percentage": 97},
            ],
        }
    ]
    return doc


def platform_config(doc: dict[str, Any], count: dict[str, Any] | None) -> Any:
    from reach_around.config import SiteConfig

    item: dict[str, Any] = {
        "platform": "instagram",
        "handle": "@example",
        "url": "https://example.com/ig",
    }
    item.update(count or {})
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [{"id": "socials", "items": [item]}],
                }
            ],
            "features": {"kit_gate": True},
            "privacy": {
                "controller_name": "Example Creator",
                "contact": "example.com/contact",
            },
            "kit": doc,
        }
    )


def numbers_for(count: int) -> Any:
    from datetime import UTC, datetime

    from reach_around.config.numbers import Numbers, NumbersEntry

    return Numbers(
        entries={
            "instagram": NumbersEntry(
                count=count, as_of=datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
            )
        }
    )


def countries_shown(
    tmp_path: Path, doc: dict[str, Any], config: Any, numbers: Any
) -> bool:
    _, _, gated = build_instance(tmp_path, config, numbers)
    return "Exampleland" in (gated / "kit.html").read_text(encoding="utf-8")


def test_no_printed_count_for_the_platform_means_the_shares_print(
    tmp_path: Path,
) -> None:
    doc = place_doc()
    assert countries_shown(tmp_path, doc, platform_config(doc, None), None)


def test_a_link_page_count_typed_on_the_platform_item_is_a_base(
    tmp_path: Path,
) -> None:
    doc = place_doc()
    config = platform_config(doc, {"count": 100, "as_of": "2027-03-02"})
    assert not countries_shown(tmp_path, doc, config, None)
    # the public link page really prints that count, so the base is real
    _, site, _ = build_instance(tmp_path / "again", config)
    assert "100" in (site / "index.html").read_text(encoding="utf-8")


def test_a_link_page_count_from_the_numbers_file_is_a_base(tmp_path: Path) -> None:
    doc = place_doc()
    config = platform_config(doc, None)
    assert not countries_shown(tmp_path, doc, config, numbers_for(100))


def test_a_large_link_page_count_keeps_the_shares(tmp_path: Path) -> None:
    doc = place_doc()
    config = platform_config(doc, {"count": 12_000, "as_of": "2027-03-02"})
    assert countries_shown(tmp_path, doc, config, None)


def test_a_link_page_count_under_the_floor_is_not_printed_so_not_a_base(
    tmp_path: Path,
) -> None:
    doc = place_doc()
    config = platform_config(doc, {"count": 4, "as_of": "2027-03-02"})
    assert countries_shown(tmp_path, doc, config, None)


def test_the_analytics_followers_are_a_base(tmp_path: Path) -> None:
    doc = place_doc()
    doc["analytics"] = [
        {
            "platform": "instagram",
            "synced_with": "Example Social API",
            "as_of": "2027-03-08",
            "followers": 100,
            "followers_30_day_percent": 12.0,
        }
    ]
    assert not countries_shown(tmp_path, doc, platform_config(doc, None), None)


def test_the_pdf_text_check_follows_a_numbers_file_only_link_page_base(
    tmp_path: Path,
) -> None:
    """The count exists only in the numbers file (none typed on the platform
    item), so the PDF check gets it from the numbers alone. The group the page
    withheld must not be reported missing (#668)."""
    from reach_around.kit.pdf import build_kit_pdf

    doc = place_doc()
    config = platform_config(doc, None)
    numbers = numbers_for(100)
    _, site, gated = build_instance(tmp_path, config, numbers)
    out = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "o",
        name="k.pdf",
        created=CREATED,
        numbers=numbers,
        renderer=fake_renderer,
    ).path
    assert "Exampleland" not in flat(out)
    text = "\n".join(extract_pages(out.read_bytes()))
    # no base known: the same text is missing the withheld group (the check bites)
    with pytest.raises(PdfError, match="countries"):
        check_text(config.kit, text, None, None, platform_counts={})


def test_a_kit_metric_under_the_floor_is_no_audience_base() -> None:
    """A headline value of 4 prints as "Under 5", so it is not a count a reader
    can see; it must not withhold a group (it would only withhold more, so this
    pins the filter itself). 5 is printed, so it is a base (#668)."""
    from reach_around.site.kit import audience_bases, demographic_withheld

    def metric_of(value: int) -> ResolvedMetric:
        return ResolvedMetric("instagram", "Followers", "manual", value, TODAY, True)

    demo = _demo(97, 97, 97)  # a 3 percent cell is pinned by base 100 only
    assert audience_bases("instagram", numbers_measures([metric_of(4)])) == []
    assert audience_bases("instagram", numbers_measures([metric_of(5)])) == [5]
    small = audience_bases("instagram", numbers_measures([metric_of(4)]))
    assert demographic_withheld(demo, small) == frozenset()
    assert demographic_withheld(demo, [100]) == {"ages"}  # the demo does bite


def test_a_rounded_form_is_read_at_its_true_value() -> None:
    """A tile prints 1,004 followers as 1.0k, so a reader holds the range 950 to
    1,049. The rule uses the true count, which is never below the reader's upper
    bound, so a cell the reader could not call small is never hidden, and a cell
    the reader can call small always is."""
    from reach_around.site.kit import demographic_withheld

    demo = _demo(50, 50, 50)
    assert demographic_withheld(demo, [999]) == frozenset()
    assert demographic_withheld(_zero_share_demo(), [999]) == {"countries"}
    assert demographic_withheld(_zero_share_demo(), [1000]) == frozenset()


def _zero_share_demo() -> Any:
    from reach_around.config.kit import Demographic

    return Demographic.model_validate(
        {
            "platform": "instagram",
            "as_of": "2027-03-01",
            "gender": {"male": 50, "female": 50},
            "ages": [{"range": "18-24", "all": 100, "male": 100, "female": 100}],
            "countries": [
                {"name": "Exampleland", "percentage": 0},
                {"name": "Otherland", "percentage": 100},
            ],
        }
    )


def test_the_pdf_text_check_follows_the_link_page_base(tmp_path: Path) -> None:
    from reach_around.kit.pdf import build_kit_pdf

    doc = place_doc()
    config = platform_config(doc, {"count": 100, "as_of": "2027-03-02"})
    _, site, gated = build_instance(tmp_path, config)
    out = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "o",
        name="k.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    assert "Exampleland" not in flat(out)
    from reach_around.kit.pdftext import extract_pages

    text = "\n".join(extract_pages(out.read_bytes()))
    # the group the page withheld is not expected in the text ...
    check_text(config.kit, text, None, None, platform_counts={"instagram": [100]})
    # ... and with no base known the same text is missing the group
    with pytest.raises(PdfError, match="countries"):
        check_text(config.kit, text, None, None, platform_counts={})


# --- the raw forms of the floored counts ---------------------------------------


def test_the_screen_reader_count_follows_the_floor_too() -> None:
    from reach_around.config.blocks import Platforms
    from reach_around.site.platforms import platform_rows

    block = Platforms.model_validate(
        {
            "id": "socials",
            "items": [
                {
                    "platform": "instagram",
                    "handle": "@a",
                    "url": "https://example.com/a",
                    "count": c,
                    "as_of": "2027-03-01",
                }
                for c in (4, 5, 1234)
            ],
        }
    )
    rows = platform_rows(block, None, TODAY)
    assert [r.exact for r in rows] == [UNDER_FLOOR, "5", "1,234"]
    assert [r.short for r in rows] == [UNDER_FLOOR, "5", "1.2k"]


def test_a_small_case_study_value_is_floored_in_the_private_pdf_text(
    tmp_path: Path,
) -> None:
    """A case study with 3 views prints "Under 5" in the private PDF, and the
    text check expects that form, not the raw count (#668)."""
    from reach_around.kit.pdf import build_kit_pdf

    doc = kit_doc()
    doc["case_studies"][0]["views"] = 3
    config = make_config(doc)
    _, site, gated = build_instance(tmp_path, config)
    out = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "o",
        name="k.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    assert f"Views{UNDER_FLOOR}".replace(" ", "") in flat(out)
    assert "Views3" not in flat(out)

    # a renderer that prints the raw count instead: the check refuses it
    def raw_renderer(html: str, work: Path) -> bytes:
        assert UNDER_FLOOR in html
        return fake_renderer(html.replace(UNDER_FLOOR, "3"), work)

    with pytest.raises(PdfError, match="case_studies"):
        build_kit_pdf(
            config,
            gated_dir=gated,
            site_dir=site,
            out_dir=tmp_path / "o2",
            name="k.pdf",
            created=CREATED,
            renderer=raw_renderer,
        )
