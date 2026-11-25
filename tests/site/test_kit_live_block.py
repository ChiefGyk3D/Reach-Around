# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 4 (#629): the live block, Twitch and Kick as stacked, equal cards.
Everything is synthetic."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from test_kit_build import build, gated_doc, make_config

from reach_around.config.measures import LIVE_KEYS, REGISTRY
from reach_around.site.kit import GATED_FILE

KICK_FOLLOWERS = 2_345


def row(platform: str, base: int, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "platform": platform,
        "period_start": "2027-02-01",
        "period_end": "2027-02-28",
        "as_of": "2027-03-01",
        "avg_ccv": base,
        "peak_ccv": base * 3,
        "hours_watched": base * 20,
        "unique_chatters": base + 7,
        "followers_gained": base * 5,
        "hours_streamed_per_week": 18,
    }
    out.update(kw)
    return out


def kit_kw(*rows: dict[str, Any], **kw: Any) -> dict[str, Any]:
    metrics = [
        {
            "platform": "twitch",
            "label": "Followers",
            "value": 1200,
            "as_of": "2027-03-01",
        },
        {
            "platform": "kick",
            "label": "Followers",
            "value": KICK_FOLLOWERS,
            "as_of": "2027-03-01",
        },
    ]
    return {"metrics": metrics, "live": list(rows)} | kw


def pages(tmp_path: Path, *rows: dict[str, Any], **kw: Any) -> tuple[str, str]:
    doc = gated_doc(**kit_kw(*rows, **kw))
    out, gated = build(tmp_path, make_config(doc))
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


def live_cards(html: str) -> list[str]:
    return re.findall(r'<article class="kit-live-card.*?</article>', html, re.S)


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def shape(card: str, name: str) -> str:
    """The card with its platform name, its numbers and ids made neutral."""
    neutral = card.replace(name, "X").replace(name.lower(), "x")
    neutral = re.sub(r"kit-live-title-\d+", "kit-live-title-N", neutral)
    return re.sub(r"\d[\d,]*", "N", neutral)


def test_both_platforms_print_the_seven_fields_in_one_order(tmp_path: Path) -> None:
    for html in pages(tmp_path, row("twitch", 40), row("kick", 25)):
        twitch, kick = live_cards(html)
        labels = [REGISTRY[k].label for k in LIVE_KEYS]
        for card in (twitch, kick):
            found = re.findall(
                r'<li class="kit-live-row"[^>]*>\s*<span>(.*?)</span>', card
            )
            assert found == labels
            assert "Hand-kept" in card
        assert "Twitch" in text(twitch)
        assert "Kick" in text(kick)
        assert shape(twitch, "Twitch") == shape(kick, "Kick")


def test_swapping_the_platforms_gives_the_same_shape(tmp_path: Path) -> None:
    straight = pages(tmp_path / "a", row("twitch", 40), row("kick", 25))
    swapped = pages(tmp_path / "b", row("twitch", 25), row("kick", 40))
    for one, two in zip(straight, swapped, strict=True):
        (t1, k1), (t2, k2) = live_cards(one), live_cards(two)
        assert shape(t1, "Twitch") == shape(t2, "Twitch")
        assert shape(k1, "Kick") == shape(k2, "Kick")
        # The values moved with the platform; the structure did not.
        assert text(t1).replace("Twitch", "X") != text(t2).replace("Twitch", "X")


def test_config_order_sets_the_block_order_and_reversing_reverses_it(
    tmp_path: Path,
) -> None:
    a = pages(tmp_path / "a", row("twitch", 40), row("kick", 25))
    b = pages(tmp_path / "b", row("kick", 25), row("twitch", 40))
    for one, two in zip(a, b, strict=True):
        first = [("Twitch" in text(c)) for c in live_cards(one)]
        second = [("Twitch" in text(c)) for c in live_cards(two)]
        assert first == [True, False]
        assert second == [False, True]
        assert [text(c) for c in live_cards(two)] == [
            text(c) for c in reversed(live_cards(one))
        ]


def test_a_bigger_platform_is_never_sorted_first(tmp_path: Path) -> None:
    for html in pages(tmp_path, row("twitch", 4), row("kick", 4000)):
        first, _ = live_cards(html)
        assert "Twitch" in text(first)


def test_the_empty_side_says_so_in_the_same_style(tmp_path: Path) -> None:
    only_twitch = kit_kw()["metrics"][:1]
    for html in pages(
        tmp_path, row("twitch", 40), {"platform": "kick"}, metrics=only_twitch
    ):
        twitch, kick = live_cards(html)
        assert "No live numbers yet" in text(kick)
        assert "No live numbers yet" not in text(twitch)
        assert "<h4" in kick and "<h4" in twitch


def test_a_missing_figure_prints_not_entered_never_zero(tmp_path: Path) -> None:
    partial = row("twitch", 40)
    del partial["unique_chatters"]
    for html in pages(tmp_path, partial, row("kick", 25)):
        twitch, kick = live_cards(html)
        assert "Not entered" in text(twitch)
        assert "Not entered" not in text(kick)
        # Both still list the same seven fields.
        assert len(re.findall(r"kit-live-row", twitch)) == 7
        assert len(re.findall(r"kit-live-row", kick)) == 7


def test_followers_come_from_the_headline_number_for_each_platform(
    tmp_path: Path,
) -> None:
    for html in pages(tmp_path, row("twitch", 40), row("kick", 25)):
        twitch, kick = live_cards(html)
        assert "1,200" in text(twitch)
        assert f"{KICK_FOLLOWERS:,}" in text(kick)


def test_only_the_follower_count_may_be_api_derived(tmp_path: Path) -> None:
    for html in pages(tmp_path, row("twitch", 40), row("kick", 25)):
        for card in live_cards(html):
            items = re.findall(r'<li class="kit-live-row".*?</li>', card, re.S)
            assert len(items) == 7
            for item in items[1:]:
                assert "Hand-kept" in item
                assert "API" not in text(item)


def test_the_period_is_printed_on_every_live_figure(tmp_path: Path) -> None:
    for html in pages(tmp_path, row("twitch", 40), row("kick", 25)):
        for card in live_cards(html):
            items = re.findall(r'<li class="kit-live-row".*?</li>', card, re.S)
            for item in items[1:]:
                assert "2027-02-01 to 2027-02-28" in text(item)


def test_no_live_block_without_rows(tmp_path: Path) -> None:
    for html in pages(tmp_path):
        assert live_cards(html) == []
        assert "kit-live" not in html


def test_live_cards_are_spaced_like_the_platform_cards() -> None:
    """The cards sit in a grid with a gap, so the page never shows through as a
    thin strip at the seam; the live block and Platforms share one rule."""
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/kit.css"
    ).read_text(encoding="utf-8")
    rule = re.search(r"([^{}]*\.kit-live\b[^{}]*)\{([^}]*)\}", css)
    assert rule is not None, "no spacing rule for the live block"
    assert ".kit-sections" in rule.group(1)
    assert re.search(r"gap:\s*var\(--ra-space-[3-6]\)", rule.group(2))
