# SPDX-License-Identifier: Apache-2.0
"""Kit audience summary as a range (#627, spec 6.5). Everything is synthetic.

The largest single audience is the lower bound and the sum of follows is the
upper bound. Never one deduplicated number; community members (Discord, Matrix)
stay out of both bounds and print on their own line; the poll estimate line is
off unless ``show_estimate`` is true.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, gated_doc, make_config

from reach_around.config.kit import KitConfig
from reach_around.site.kit import GATED_FILE


def metric(
    platform: str, value: int, as_of: str = "2027-03-05", **kw: Any
) -> dict[str, Any]:
    return {
        "platform": platform,
        "label": "Followers",
        "value": value,
        "as_of": as_of,
        **kw,
    }


def doc(metrics: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return gated_doc(metrics=metrics, **kw)


# 4,200 + 3,100 + 9,100 = 16,400. Discord and Matrix would make it 17,330.
SPREAD = [
    metric("twitch", 4200),
    metric("kick", 3100),
    metric("youtube", 9100),
]
COMMUNITY = [metric("discord", 800), metric("matrix", 130)]


def pages(tmp_path: Path, kit: dict[str, Any], **kw: Any) -> tuple[str, str]:
    out, gated = build(tmp_path, make_config(kit), **kw)
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


def block(html: str) -> str:
    match = re.search(r'<section class="kit-summary".*?</section>', html, re.S)
    assert match, "no audience summary block"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(0))).strip()


def test_range_has_both_bounds_from_the_fixture(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(SPREAD)):
        text = block(html)
        assert "Largest single audience: 9,100 (YouTube)." in text
        assert "16,400 follows" in text
        assert "Unique people are somewhere between 9,100 and 16,400." in text


def test_overlap_sentence_renders_when_more_than_one_platform(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(SPREAD)):
        assert (
            "People who follow on more than one platform are counted more than once."
            in block(html)
        )


def test_overlap_sentence_absent_with_one_platform_and_no_range(
    tmp_path: Path,
) -> None:
    for html in pages(tmp_path, doc([metric("twitch", 1200)])):
        text = block(html)
        assert "counted more than once" not in text
        assert "somewhere between" not in text
        assert "1,200" in text


def test_tied_largest_audience_names_both_platforms_in_list_order(
    tmp_path: Path,
) -> None:
    kit = doc([metric("twitch", 4000), metric("kick", 4000)])
    for html in pages(tmp_path, kit):
        text = block(html)
        assert "Largest single audience: 4,000 (Twitch and Kick)." in text
        assert "between 4,000 and 8,000" in text


def test_no_output_shows_one_deduplicated_audience_number(tmp_path: Path) -> None:
    kit = doc(
        SPREAD,
        audience_summary={
            "show_estimate": True,
            "poll": {
                "respondents": 200,
                "as_of": "2027-03-01",
                "avg_other_platforms": 0.5,
            },
        },
    )
    for html in pages(tmp_path, kit):
        assert "Combined audience" not in html
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        # every sentence that says "unique" carries both bounds or says Estimate
        for sentence in re.findall(r"[^.]*[Uu]nique[^.]*\.", text):
            assert "between 9,100 and 16,400" in sentence or "roughly" in sentence
        # the sum is never the headline number on its own
        assert "<strong>16,400</strong>" not in html


def test_community_members_never_enter_either_bound(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(SPREAD + COMMUNITY)):
        text = block(html)
        assert "16,400 follows" in text
        assert "between 9,100 and 16,400" in text
        assert "17,330" not in html and "17,300" not in html
        # their own line, outside the follows range
        assert "Community members: Discord 800, Matrix 130." in text
        assert "not followers" in text


def test_community_line_stands_without_any_follows_block(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(COMMUNITY)):
        assert 'class="kit-summary"' not in html or "somewhere between" not in html
        assert "Community members: Discord 800, Matrix 130." in html
        assert "Combined audience" not in html


def test_community_member_under_the_floor_prints_under_5(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(SPREAD + [metric("discord", 3)])):
        assert "Community members: Discord Under 5." in html


def test_estimate_line_absent_unless_show_estimate(tmp_path: Path) -> None:
    poll = {"respondents": 200, "as_of": "2027-03-01", "avg_other_platforms": 0.5}
    off = doc(SPREAD, audience_summary={"poll": poll})
    explicit = doc(SPREAD, audience_summary={"poll": poll, "show_estimate": False})
    for kit in (doc(SPREAD), off, explicit):
        for html in pages(tmp_path / str(id(kit)), kit):
            assert "Estimate from a poll" not in html
            assert "roughly" not in html


def test_estimate_line_is_labeled_and_beside_the_range(tmp_path: Path) -> None:
    kit = doc(
        SPREAD,
        audience_summary={
            "show_estimate": True,
            "poll": {
                "respondents": 200,
                "as_of": "2027-03-01",
                "avg_other_platforms": 0.5,
            },
        },
    )
    for html in pages(tmp_path, kit):
        text = block(html)
        # 16,400 / 1.5 = 10,933, printed to two significant figures
        assert (
            "Estimate from a poll of 200 people on 2027-03-01: "
            "roughly 11,000 unique people." in text
        )
        # the range is still there, before the estimate
        assert text.index("somewhere between 9,100 and 16,400") < text.index(
            "Estimate from"
        )
        assert "hand-kept" in text.lower() or "typed by the owner" in text


def test_estimate_outside_the_bounds_is_left_out(tmp_path: Path) -> None:
    # 16,400 / 1.01 = 16,238 is inside; avg 5 gives 2,733, below the largest audience
    kit = doc(
        SPREAD,
        audience_summary={
            "show_estimate": True,
            "poll": {
                "respondents": 200,
                "as_of": "2027-03-01",
                "avg_other_platforms": 5,
            },
        },
    )
    for html in pages(tmp_path, kit):
        assert "Estimate from a poll" not in html
        assert "somewhere between 9,100 and 16,400" in block(html)


def test_estimate_needs_two_counted_numbers(tmp_path: Path) -> None:
    kit = doc(
        [metric("twitch", 1200)],
        audience_summary={
            "show_estimate": True,
            "poll": {
                "respondents": 200,
                "as_of": "2027-03-01",
                "avg_other_platforms": 0.5,
            },
        },
    )
    for html in pages(tmp_path, kit):
        assert "Estimate from a poll" not in html


def test_stale_number_is_marked_in_the_block(tmp_path: Path) -> None:
    kit = doc([metric("twitch", 4200, "2026-10-01"), metric("youtube", 9100)])
    for html in pages(tmp_path, kit):
        assert "(out of date)" in block(html)
    fresh = doc(SPREAD)
    for html in pages(tmp_path / "fresh", fresh):
        assert "(out of date)" not in block(html)


def test_era_note_renders_when_numbers_span_the_cutover(tmp_path: Path) -> None:
    kit = doc([metric("twitch", 4200, "2026-05-01"), metric("youtube", 9100)])
    out, gated = build(tmp_path, make_config(kit, cutover_date="2026-06-01"))
    for page in (out / "kit" / "index.html", gated / GATED_FILE):
        assert "from before Reach Around and numbers from after it" in block(
            page.read_text(encoding="utf-8")
        )


def test_oldest_date_and_number_count_are_printed(tmp_path: Path) -> None:
    kit = doc([metric("twitch", 4200, "2027-03-01"), metric("youtube", 9100)])
    for html in pages(tmp_path, kit):
        text = block(html)
        assert "2 platforms" in text
        assert "2027-03-01" in text


def test_hand_kept_numbers_are_labeled_hand_kept(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(SPREAD)):
        assert "kit-trust-hand_kept" in block_html(html) or "Hand-kept" in block(html)


def block_html(html: str) -> str:
    match = re.search(r'<section class="kit-summary".*?</section>', html, re.S)
    assert match
    return match.group(0)


def test_inactive_account_caveat_is_printed(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc(SPREAD)):
        assert "inactive and automated accounts" in block(html)


def test_a_counted_number_under_the_floor_hides_the_block(tmp_path: Path) -> None:
    for html in pages(tmp_path, doc([metric("twitch", 3), metric("youtube", 9100)])):
        assert 'class="kit-summary"' not in html
        assert "somewhere between" not in html


def test_in_total_false_is_left_out_of_both_bounds(tmp_path: Path) -> None:
    kit = doc(SPREAD + [metric("bluesky", 50000, in_total=False)])
    for html in pages(tmp_path, kit):
        assert "between 9,100 and 16,400" in block(html)


def test_twitch_and_kick_are_treated_alike(tmp_path: Path) -> None:
    a = doc([metric("twitch", 4000), metric("youtube", 9000)])
    b = doc([metric("kick", 4000), metric("youtube", 9000)])
    one = block(pages(tmp_path / "a", a)[0]).replace("Twitch", "X")
    two = block(pages(tmp_path / "b", b)[0]).replace("Kick", "X")
    assert one == two


# --- config -------------------------------------------------------------------


def kit_model(**kw: Any) -> KitConfig:
    return KitConfig.model_validate(doc(SPREAD, **kw))


def test_show_estimate_needs_a_poll() -> None:
    with pytest.raises(ValidationError, match="poll"):
        kit_model(audience_summary={"show_estimate": True})


@pytest.mark.parametrize(
    "poll",
    [
        {"respondents": 0, "as_of": "2027-03-01", "avg_other_platforms": 1},
        {"respondents": 4, "as_of": "2027-03-01", "avg_other_platforms": 1},
        {"respondents": 50, "as_of": "2027-03-01", "avg_other_platforms": -1},
        {"respondents": 50, "as_of": "2027-03-01", "avg_other_platforms": 200},
        {"respondents": 50, "as_of": "not a date", "avg_other_platforms": 1},
        {"respondents": 50, "as_of": "2027-03-01"},
        {"respondents": 50, "as_of": "2027-03-01", "avg_other_platforms": 1, "x": 1},
    ],
)
def test_bad_poll_is_refused(poll: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        kit_model(audience_summary={"poll": poll})


def test_unknown_audience_summary_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        kit_model(audience_summary={"include": ["follows"]})
