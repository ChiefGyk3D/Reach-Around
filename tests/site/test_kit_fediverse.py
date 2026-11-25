# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 5 (#630): the fediverse-first block. Everything is synthetic.

The fixture below is worked by hand in the comments, so a test failure points at
the code and not at the fixture."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, gated_doc, kit_doc, make_config

from reach_around.config.fediverse import (
    NOT_PROVIDED,
    FediverseEntry,
    fediverse_measures,
    mean_of,
    median_of,
)
from reach_around.config.kit import KitConfig, public_projection
from reach_around.config.measures import REGISTRY, compile_measures, default_keys
from reach_around.site.kit import GATED_FILE

PROFILE = "https://social.example/@example"
BSKY = "https://bsky.example/profile/example.example"

# Seven posts, each column sorted by hand:
#   boosts    1 2 4 6 6 9 30   -> median 6, sum 58, mean 8.29 -> 8
#   favorites 3 8 10 14 14 22 40 -> median 14, sum 111, mean 15.86 -> 16
#   replies   0 1 2 3 3 5 7    -> median 3, sum 21, mean 3
POSTS = [
    {"boosts": 2, "favorites": 10, "replies": 0},
    {"boosts": 9, "favorites": 14, "replies": 3},
    {"boosts": 4, "favorites": 3, "replies": 1},
    {"boosts": 6, "favorites": 22, "replies": 5},
    {"boosts": 1, "favorites": 14, "replies": 3},
    {"boosts": 6, "favorites": 40, "replies": 7},
    {"boosts": 30, "favorites": 8, "replies": 2},
]


def entry(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "platform": "mastodon",
        "profile_url": PROFILE,
        "as_of": "2027-03-08",
        "followers": 1900,
        "posts": 4321,
        "interactions": POSTS,
    }
    doc.update(kw)
    return doc


def kit_with(**kw: Any) -> KitConfig:
    return KitConfig.model_validate(kit_doc(**kw))


def cards(html: str) -> list[str]:
    return re.findall(r'<article class="kit-section-card.*?</article>', html, re.S)


def text(card: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card)).strip()


def page(tmp_path: Path, **kw: Any) -> tuple[str, str]:
    out, gated = build(tmp_path, make_config(gated_doc(**kw)))
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


# --- the medians match a hand calculation --------------------------------------


def test_the_median_and_mean_helpers_match_hand_calculations() -> None:
    assert median_of([1, 2, 4, 6, 6, 9, 30]) == 6
    assert median_of([30, 1, 6, 4, 9, 2, 6]) == 6
    assert median_of([5]) == 5
    # Even count: the middle two are 2 and 3, 2.5 rounds up to 3 (halves up).
    assert median_of([1, 2, 3, 4]) == 3
    assert median_of([1, 2, 4, 9]) == 3
    assert mean_of([1, 2, 4, 6, 6, 9, 30]) == 8
    assert mean_of([3, 8, 10, 14, 14, 22, 40]) == 16
    assert mean_of([1, 2]) == 2  # 1.5 rounds up
    with pytest.raises(ValueError, match="no values"):
        median_of([])


def test_the_fixture_gives_the_hand_worked_medians() -> None:
    measures = {m.key: m.value for m in fediverse_measures([FediverseEntry(**entry())])}
    assert measures["median_boosts_30d"] == 6
    assert measures["median_favorites_30d"] == 14
    assert measures["median_replies_30d"] == 3
    assert measures["followers"] == 1900
    assert measures["posts_total"] == 4321
    assert not any(k.startswith("mean_") for k in measures)


def test_the_mean_is_compiled_only_when_enabled() -> None:
    on = {
        m.key: m.value
        for m in fediverse_measures([FediverseEntry(**entry(show_mean=True))])
    }
    assert on["mean_boosts_30d"] == 8
    assert on["mean_favorites_30d"] == 16
    assert on["mean_replies_30d"] == 3


def test_bluesky_and_pixelfed_use_their_own_words() -> None:
    labels = {
        m.label
        for m in fediverse_measures(
            [FediverseEntry(**entry(platform="bluesky", profile_url=BSKY))]
        )
    }
    assert "Median reposts per post (30 days)" in labels
    assert "Median likes per post (30 days)" in labels
    labels = {
        m.label
        for m in fediverse_measures([FediverseEntry(**entry(platform="pixelfed"))])
    }
    assert "Median comments per post (30 days)" in labels


def test_medians_are_never_zero_for_missing_data() -> None:
    found = fediverse_measures(
        [
            FediverseEntry.model_validate(
                {"platform": "mastodon", "profile_url": PROFILE, "as_of": "2027-03-08"}
            )
        ]
    )
    assert found == []


# --- validation ----------------------------------------------------------------


def test_the_profile_link_must_be_https_and_the_platform_a_fediverse_one() -> None:
    with pytest.raises(ValidationError, match="https"):
        FediverseEntry(**entry(profile_url="http://social.example/@a"))
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(profile_url="javascript:alert(1)"))
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(platform="twitch"))


def test_a_platform_is_listed_once() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        kit_with(fediverse=[entry(), entry()])


def test_counts_and_posts_are_bounded_and_whole() -> None:
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(followers=-1))
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(followers=1.5))
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(interactions=[{"boosts": -1}]))
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(interactions=[{"boosts": 1, "extra": 2}]))


# --- the page ------------------------------------------------------------------


def test_every_public_count_carries_the_recheck_link_from_the_config(
    tmp_path: Path,
) -> None:
    sections = [{"platform": "mastodon"}, {"platform": "bluesky"}]
    fed = [entry(), entry(platform="bluesky", profile_url=BSKY, followers=2100)]
    for html in page(tmp_path, sections=sections, fediverse=fed):
        mastodon, bluesky = cards(html)
        for card, url in ((mastodon, PROFILE), (bluesky, BSKY)):
            links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>', card)
            assert links, "no re-check link in the card"
            assert set(links) == {url}
            # Every number with a link beside it: count the rows and the links.
            rows = card.count("<li>")
            assert len(links) >= rows + 1  # each row, plus the headline
            assert 'rel="noopener noreferrer"' in card
            assert "Re-check" in card


def test_counts_are_labeled_as_reported_by_the_server(tmp_path: Path) -> None:
    for html in page(
        tmp_path, sections=[{"platform": "mastodon"}], fediverse=[entry()]
    ):
        (card,) = cards(html)
        assert "as reported by the server" in text(card).lower()


def test_median_prints_first_and_the_mean_only_when_enabled(tmp_path: Path) -> None:
    sections = [{"platform": "mastodon"}]
    for html in page(tmp_path, sections=sections, fediverse=[entry()]):
        (card,) = cards(html)
        assert "Median boosts per post (30 days)" in text(card)
        assert "Mean" not in text(card)
    for html in page(
        tmp_path / "mean", sections=sections, fediverse=[entry(show_mean=True)]
    ):
        (card,) = cards(html)
        body = text(card)
        for noun in ("boosts", "favorites", "replies"):
            assert body.index(f"Median {noun}") < body.index(f"Mean {noun}")
        assert body.index("Median replies") < body.index("Mean boosts")


def test_the_card_prints_the_hand_calculated_medians(tmp_path: Path) -> None:
    for html in page(
        tmp_path, sections=[{"platform": "mastodon"}], fediverse=[entry()]
    ):
        (card,) = cards(html)
        body = text(card)
        assert re.search(r"Median boosts per post \(30 days\) 6\b", body)
        assert re.search(r"Median favorites per post \(30 days\) 14\b", body)
        assert re.search(r"Median replies per post \(30 days\) 3\b", body)
        assert "1,900" in body and "4,321" in body


def test_missing_values_print_not_provided_and_never_zero(tmp_path: Path) -> None:
    bare = entry(posts=None, interactions=[])
    for html in page(tmp_path, sections=[{"platform": "mastodon"}], fediverse=[bare]):
        (card,) = cards(html)
        body = text(card)
        assert "Not provided: Public posts, Median boosts per post (30 days)" in body
        assert "Median favorites per post (30 days)" in body.split("Not provided:")[1]
        assert "Public posts" in body.split("Not provided:")[1]
        assert "Median boosts per post (30 days) 0" not in body
        assert "Mean" not in body


def test_registry_known_absences_are_stated_per_platform(tmp_path: Path) -> None:
    fed = [
        entry(),
        entry(platform="bluesky", profile_url=BSKY),
        entry(platform="pixelfed", profile_url="https://pix.example/example"),
    ]
    sections = [{"platform": p} for p in ("mastodon", "bluesky", "pixelfed")]
    for html in page(tmp_path, sections=sections, fediverse=fed):
        mastodon, bluesky, pixelfed = (text(c) for c in cards(html))
        assert "Not provided by Mastodon: impressions, reach, demographics" in mastodon
        assert "Not provided by Bluesky: impressions, reach, demographics" in bluesky
        assert "Not provided by Pixelfed" in pixelfed
        assert "insights" in pixelfed
    assert set(NOT_PROVIDED) == {"mastodon", "bluesky", "pixelfed"}


def test_a_platform_without_an_entry_prints_no_fediverse_lines(
    tmp_path: Path,
) -> None:
    for html in page(tmp_path, sections=[{"platform": "twitch"}]):
        (card,) = cards(html)
        assert "reported by the server" not in text(card)
        assert "Not provided" not in text(card)


def test_the_block_builds_on_the_page_the_gated_page_and_is_deterministic(
    tmp_path: Path,
) -> None:
    kw = {"sections": [{"platform": "mastodon"}], "fediverse": [entry()]}
    first = page(tmp_path / "a", **kw)
    second = page(tmp_path / "b", **kw)
    assert first == second
    for html in first:
        assert len(cards(html)) == 1


# --- the wall ------------------------------------------------------------------


def test_the_block_is_public_and_never_wider_than_its_field() -> None:
    from reach_around.config.kit import KIT_FIELD_POLICY

    assert KIT_FIELD_POLICY["fediverse"].visibility == "public"
    kit = kit_with(fediverse=[entry()])
    measures = compile_measures(kit, None)
    fed = [m for m in measures if m.block == "fediverse"]
    assert fed and all(m.visibility == "public" for m in fed)
    for key in (
        "posts_total",
        "median_boosts_30d",
        "median_favorites_30d",
        "median_replies_30d",
        "mean_boosts_30d",
    ):
        assert REGISTRY[key].visibility == "public"
    assert "fediverse" in public_projection(kit, None)


def test_a_private_source_would_keep_the_block_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reach_around.config import kit as kit_module
    from reach_around.config.kit import FieldPolicy

    policy = dict(kit_module.KIT_FIELD_POLICY)
    policy["fediverse"] = FieldPolicy("private")
    monkeypatch.setattr(kit_module, "KIT_FIELD_POLICY", policy)
    found = compile_measures(kit_with(fediverse=[entry()]), None)
    fed = [m for m in found if m.block == "fediverse"]
    assert fed and all(m.visibility == "private" for m in fed)


def test_the_open_default_set_lists_medians_before_means() -> None:
    keys = default_keys("mastodon")
    assert keys == default_keys("bluesky") == default_keys("pixelfed")
    for noun in ("boosts", "favorites", "replies"):
        assert keys.index(f"median_{noun}_30d") < keys.index(f"mean_{noun}_30d")
    assert keys.index("median_replies_30d") < keys.index("mean_boosts_30d")


def test_an_entry_date_is_a_plain_day() -> None:
    assert FediverseEntry(**entry()).as_of == date(2027, 3, 8)
    with pytest.raises(ValidationError):
        FediverseEntry(**entry(as_of="2027-03-08T00:00:00Z"))
