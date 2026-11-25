# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 3 (#628): platform cards, the degrade rules and the wall.
Everything is synthetic."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from test_kit_build import build, gated_doc, make_config

from reach_around.site.kit import GATED_FILE

GATED_VIEWS = 7_654_321
ROOT = Path(__file__).resolve().parents[2]


def page_pair(tmp_path: Path, **kw: Any) -> tuple[str, str]:
    out, gated = build(tmp_path, make_config(gated_doc(**kw)))
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


def cards(html: str) -> list[str]:
    return re.findall(r'<article class="kit-section-card.*?</article>', html, re.S)


def shape(card: str, name: str) -> str:
    """The card with its platform name and its numbered ids made neutral."""
    neutral = card.replace(name, "X").replace(name.lower(), "x")
    return re.sub(r"kit-sections-title-\d+", "kit-sections-title-N", neutral)


def text(card: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card)).strip()


def engagement_for(platform: str) -> list[dict[str, Any]]:
    return [
        {
            "platform": platform,
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "as_of": "2027-03-01",
            "views": GATED_VIEWS,
            "posts": 10,
            "interactions": 500_000,
        }
    ]


# --- the degrade rules --------------------------------------------------------


def test_a_configured_platform_with_no_numbers_prints_the_no_numbers_card(
    tmp_path: Path,
) -> None:
    for html in page_pair(
        tmp_path, sections=[{"platform": "kick", "account": "example_kick"}]
    ):
        (card,) = cards(html)
        assert "Kick" in text(card)
        assert "example_kick" in text(card)
        assert "No numbers yet" in text(card)


def test_hide_if_empty_omits_the_empty_card(tmp_path: Path) -> None:
    sections = [
        {"platform": "kick", "hide_if_empty": True},
        {"platform": "twitch"},
    ]
    for html in page_pair(tmp_path, sections=sections):
        found = cards(html)
        assert len(found) == 1
        assert "Twitch" in text(found[0])
        assert "Kick" not in text(found[0])
        assert "No numbers yet" not in html


def test_hide_if_empty_never_hides_a_card_that_has_numbers(tmp_path: Path) -> None:
    for html in page_pair(
        tmp_path, sections=[{"platform": "twitch", "hide_if_empty": True}]
    ):
        (card,) = cards(html)
        assert "1,200" in text(card)
        assert "No numbers yet" not in text(card)


def test_no_sections_means_no_cards_and_no_heading(tmp_path: Path) -> None:
    for html in page_pair(tmp_path):
        assert "kit-section-card" not in html
        assert "No numbers yet" not in html


def test_the_card_prints_the_value_its_label_date_and_source(tmp_path: Path) -> None:
    for html in page_pair(tmp_path, sections=[{"platform": "twitch"}]):
        (card,) = cards(html)
        body = text(card)
        assert "1,200" in body
        assert "Followers" in body
        assert "2027-03-01" in body and "UTC" in body
        assert "Hand-kept" in body  # no collector is configured in this fixture
        assert "Typed by the owner" in body


def test_a_missing_measure_is_left_out_never_zeroed(tmp_path: Path) -> None:
    for html in page_pair(tmp_path, sections=[{"platform": "twitch"}]):
        body = text(cards(html)[0])
        assert "Average views" not in body
        assert "Engagement rate" not in body
        assert "not available" not in body
        assert " 0 " not in f" {body} "


def test_a_section_shows_only_the_keys_it_names(tmp_path: Path) -> None:
    sections = [{"platform": "twitch", "show": ["analytics_stat"]}]
    for html in page_pair(tmp_path, sections=sections):
        (card,) = cards(html)
        # the only key named has no value, so the card is empty
        assert "No numbers yet" in text(card)
        assert "1,200" not in text(card)


# --- parity ---------------------------------------------------------------------


def test_twitch_and_kick_cards_have_the_same_markup(tmp_path: Path) -> None:
    def metric(platform: str) -> dict[str, Any]:
        return {
            "platform": platform,
            "label": "Followers",
            "value": 3100,
            "as_of": "2027-03-01",
        }

    both = [{"platform": "twitch"}, {"platform": "kick"}]
    _, gated_a = page_pair(
        tmp_path,
        metrics=[metric("twitch"), metric("kick")],
        engagement=engagement_for("twitch") + engagement_for("kick"),
        sections=both,
    )
    twitch, kick = cards(gated_a)
    assert "Engagement rate" in text(twitch)
    assert shape(twitch, "Twitch") == shape(kick, "Kick")


def test_a_card_for_one_empty_live_platform_has_the_same_shape(tmp_path: Path) -> None:
    html, _ = page_pair(
        tmp_path, sections=[{"platform": "twitch"}, {"platform": "kick"}]
    )
    twitch, kick = cards(html)
    assert "1,200" in text(twitch)
    assert text(kick) == "Kick No numbers yet"
    assert re.sub(r"\d+", "N", twitch.split(">", 1)[0]) == re.sub(
        r"\d+", "N", kick.split(">", 1)[0]
    )


# --- the wall ---------------------------------------------------------------------


def test_a_gated_measure_in_a_section_never_reaches_the_public_page(
    tmp_path: Path,
) -> None:
    public, gated = page_pair(
        tmp_path,
        engagement=engagement_for("tiktok"),
        sections=[{"platform": "tiktok"}],
    )
    average = f"{(2 * GATED_VIEWS + 10) // 20:,}"
    assert average not in public
    assert f"{GATED_VIEWS:,}" not in public
    assert "Engagement rate" not in public
    (public_card,) = cards(public)
    assert "No numbers yet" in text(public_card)
    (gated_card,) = cards(gated)
    assert average in text(gated_card)
    assert "6.5%" in text(gated_card)
    assert "Engagement rate" in text(gated_card)


def test_a_section_cannot_widen_a_gated_measure(tmp_path: Path) -> None:
    public, _ = page_pair(
        tmp_path,
        engagement=engagement_for("tiktok"),
        sections=[
            {"platform": "tiktok", "show": ["engagement_rate", "avg_views_per_item"]}
        ],
    )
    assert "6.5%" not in public
    assert str((2 * GATED_VIEWS + 10) // 20) not in public.replace(",", "")


def test_the_legend_prints_when_a_card_prints_a_labeled_number(tmp_path: Path) -> None:
    for html in page_pair(tmp_path, sections=[{"platform": "twitch"}]):
        assert "How to read these numbers" in html


# --- the renderer reads measures only -----------------------------------------------

OLD_FIELDS = frozenset({"metrics", "analytics", "engagement", "unified_total"})
# The functions that build what a page prints. They may name the old fields in
# no subscript, attribute or string key: the measures are the only input.
RENDERER = (
    "_metric_rows",
    "_analytics_rows",
    "_engagement_rows",
    "_total",
    "_community",
    "_legend",
    "_content",
    "audience_bases",
    "section_cards",
    "_card",
)


def old_field_reads(source: str, names: tuple[str, ...]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef) or node.name not in names:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in OLD_FIELDS:
                found.append((node.name, child.attr))
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value in OLD_FIELDS
            ):
                found.append((node.name, child.value))
    return found


def test_the_renderer_has_no_code_path_that_reads_an_old_field() -> None:
    source = (ROOT / "src" / "reach_around" / "site" / "kit.py").read_text("utf-8")
    present = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }
    assert {"_metric_rows", "_content", "section_cards"} <= present
    assert old_field_reads(source, RENDERER) == []


def test_the_old_field_guard_bites() -> None:
    bad = (
        "def _content(projection):\n"
        "    return projection['metrics']\n"
        "def _total(kit):\n"
        "    return kit.engagement\n"
        "def other(kit):\n"
        "    return kit.analytics\n"
    )
    assert old_field_reads(bad, RENDERER) == [
        ("_content", "metrics"),
        ("_total", "engagement"),
    ]
