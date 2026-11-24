# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 8 (#633): PeerTube, Lemmy, podcast, newsletter and blog measures.

Every figure is typed by the owner (hand-kept); nothing is fetched. Synthetic
data only."""

from __future__ import annotations

import html as htmllib
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_kit_build import build, gated_doc, kit_doc, make_config

from reach_around.config.channels import (
    ALLOWED,
    NOT_PROVIDED,
    NOTES,
    ChannelEntry,
    channel_measures,
)
from reach_around.config.kit import KitConfig, public_projection
from reach_around.config.measures import REGISTRY, compile_measures, default_keys
from reach_around.site.kit import GATED_FILE

PEERTUBE = "https://video.example/c/example"
LEMMY = "https://lemmy.example/c/example"
BLOG = "https://blog.example/"

FIXTURE: dict[str, dict[str, Any]] = {
    "peertube": {
        "platform": "peertube",
        "url": PEERTUBE,
        "as_of": "2027-03-08",
        "subscribers": 340,
        "watch_time_hours": 1250,
        "avg_watch_seconds": 95,
        "unique_viewers": 8200,
    },
    "lemmy": {
        "platform": "lemmy",
        "url": LEMMY,
        "as_of": "2027-03-08",
        "subscribers": 77,
        "active_users_month": 21,
    },
    "podcast": {"platform": "podcast", "as_of": "2027-03-08", "downloads": 5400},
    "newsletter": {
        "platform": "newsletter",
        "as_of": "2027-03-08",
        "subscribers": 2400,
        "delivered": 2300,
        "clicks": 190,
    },
    "blog": {"platform": "blog", "url": BLOG, "as_of": "2027-03-08", "views": 12345},
}


def entry(platform: str, **kw: Any) -> dict[str, Any]:
    doc = dict(FIXTURE[platform])
    doc.update(kw)
    return doc


def kit_with(**kw: Any) -> KitConfig:
    return KitConfig.model_validate(kit_doc(**kw))


def cards(html: str) -> list[str]:
    return re.findall(r'<article class="kit-section-card.*?</article>', html, re.S)


def text(card: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", card))).strip()


def page(tmp_path: Path, **kw: Any) -> tuple[str, str]:
    out, gated = build(tmp_path, make_config(gated_doc(**kw)))
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


def everything() -> dict[str, Any]:
    return {
        "sections": [{"platform": p} for p in FIXTURE],
        "channels": [entry(p) for p in FIXTURE],
    }


# --- the measures ---------------------------------------------------------------


def values(platform: str) -> dict[str, int | None]:
    found = channel_measures([ChannelEntry(**entry(platform))])
    return {m.key: m.value for m in found}


def test_each_platform_compiles_exactly_the_typed_figures() -> None:
    assert values("peertube") == {
        "subscribers": 340,
        "peertube_watch_time_30d": 1250,
        "peertube_avg_watch_time": 95,
        "peertube_unique_viewers_30d": 8200,
    }
    assert values("lemmy") == {"subscribers": 77, "lemmy_active_users_month": 21}
    assert values("podcast") == {"downloads_30d": 5400}
    assert values("newsletter") == {
        "subscribers": 2400,
        "newsletter_delivered": 2300,
        "newsletter_clicks": 190,
    }
    assert values("blog") == {"blog_views_30d": 12345}


def test_units_dates_trust_and_periods() -> None:
    found = {
        m.key: m
        for m in channel_measures(
            [ChannelEntry(**entry(p)) for p in ("peertube", "newsletter")]
        )
    }
    assert found["peertube_watch_time_30d"].unit == "hours"
    assert found["peertube_avg_watch_time"].unit == "seconds"
    assert found["peertube_unique_viewers_30d"].unit == "viewers"
    assert found["newsletter_delivered"].unit == "emails"
    assert found["newsletter_clicks"].unit == "clicks"
    assert REGISTRY["downloads_30d"].unit == "downloads"
    assert REGISTRY["blog_views_30d"].unit == "views"
    for m in found.values():
        assert m.block == "channels"
        assert m.as_of == date(2027, 3, 8)
        assert m.trust is not None and m.trust.label == "hand-kept"
    window = (date(2027, 2, 7), date(2027, 3, 8))
    assert found["peertube_watch_time_30d"].period == window
    assert found["newsletter_clicks"].period == window
    assert found["peertube_avg_watch_time"].period is None


def test_a_value_that_was_not_typed_is_no_measure_and_never_zero() -> None:
    bare = ChannelEntry(**entry("newsletter", subscribers=None, delivered=0))
    found = {m.key: m.value for m in channel_measures([bare])}
    assert found == {"newsletter_delivered": 0, "newsletter_clicks": 190}


# --- validation ----------------------------------------------------------------


def test_a_platform_only_takes_its_own_fields() -> None:
    assert set(ALLOWED) == set(FIXTURE)
    with pytest.raises(ValidationError, match="does not take"):
        ChannelEntry(**entry("blog", clicks=3))
    with pytest.raises(ValidationError, match="does not take"):
        ChannelEntry(**entry("podcast", subscribers=3))
    with pytest.raises(ValidationError):
        ChannelEntry(**{**entry("blog"), "platform": "twitch"})


def test_the_public_page_link_is_https_and_kept_for_peertube_and_lemmy() -> None:
    for platform in ("peertube", "lemmy"):
        with pytest.raises(ValidationError, match="url"):
            ChannelEntry(**entry(platform, url=None))
    with pytest.raises(ValidationError, match="https"):
        ChannelEntry(**entry("blog", url="http://blog.example/"))
    with pytest.raises(ValidationError):
        ChannelEntry(**entry("blog", url="javascript:alert(1)"))
    assert ChannelEntry(**entry("podcast")).url is None  # no public page to link


def test_an_entry_holds_at_least_one_figure_and_a_plain_day() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ChannelEntry.model_validate({"platform": "podcast", "as_of": "2027-03-08"})
    with pytest.raises(ValidationError):
        ChannelEntry(**entry("blog", as_of="2027-03-08T00:00:00Z"))


def test_figures_are_whole_and_bounded() -> None:
    for bad in (-1, 1.5, "7", True):
        with pytest.raises(ValidationError):
            ChannelEntry(**entry("blog", views=bad))
    with pytest.raises(ValidationError):
        ChannelEntry(**entry("blog", views=10**11))
    with pytest.raises(ValidationError):
        ChannelEntry(**entry("blog", extra=1))


def test_a_platform_is_listed_once() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        kit_with(channels=[entry("blog"), entry("blog")])


def test_the_new_slugs_are_platforms_for_sections() -> None:
    for platform in FIXTURE:
        assert kit_with(sections=[{"platform": platform}]).sections


# --- the page ------------------------------------------------------------------


def test_each_card_prints_its_figures_and_labels(tmp_path: Path) -> None:
    for html in page(tmp_path, **everything()):
        peertube, lemmy, podcast, newsletter, blog = (text(c) for c in cards(html))
        for body, expected in (
            (peertube, ("340", "1,250", "95", "8,200")),
            (lemmy, ("77", "21")),
            (podcast, ("5,400",)),
            (newsletter, ("2,400", "2,300", "190")),
            (blog, ("12,345",)),
        ):
            for figure in expected:
                assert re.search(rf"\b{figure}\b", body), (figure, body)
        assert "Downloads (30 days)" in podcast
        assert "Emails delivered (30 days)" in newsletter
        assert "Email clicks (30 days)" in newsletter
        assert "Blog views (30 days)" in blog
        assert "Unique viewers (30 days)" in peertube
        assert "Active users per month" in lemmy
        assert "Hand-kept" in peertube


def test_counts_say_who_reported_them_and_never_overclaim(tmp_path: Path) -> None:
    for html in page(tmp_path, **everything()):
        peertube, lemmy, podcast, newsletter, blog = (text(c) for c in cards(html))
        assert "as reported by the instance" in peertube.lower()
        assert "as reported by the instance" in lemmy.lower()
        assert "as reported by the podcast host" in podcast.lower()
        assert "as reported by the sending service" in newsletter.lower()
        assert "as reported by the site's own analytics" in blog.lower()
        # the federated-completeness question is open, so no card claims it
        assert "federat" not in peertube.lower()
        assert "complete" not in peertube.lower()
    assert set(NOTES) == set(FIXTURE)


def test_the_newsletter_shows_clicks_and_says_opens_are_not_shown(
    tmp_path: Path,
) -> None:
    assert NOT_PROVIDED["newsletter"] == ("opens",)
    for html in page(tmp_path, **everything()):
        newsletter = text(cards(html)[3])
        assert "Not provided by Newsletter: opens." in newsletter
        assert "shown, not opens" in newsletter.lower()
        assert "opens" not in text(cards(html)[0])


def test_every_count_with_a_public_page_carries_the_recheck_link(
    tmp_path: Path,
) -> None:
    for html in page(tmp_path, **everything()):
        peertube, lemmy, podcast, newsletter, blog = cards(html)
        for card, url in ((peertube, PEERTUBE), (lemmy, LEMMY), (blog, BLOG)):
            links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>', card)
            assert set(links) == {url}
            assert len(links) >= card.count("<li>") + 1
            assert 'rel="noopener noreferrer"' in card
        for card in (podcast, newsletter):  # no public page: no link
            assert "<a " not in card


def test_a_value_left_out_is_stated_not_zeroed(tmp_path: Path) -> None:
    sparse = entry("peertube", subscribers=None, unique_viewers=None)
    for html in page(tmp_path, sections=[{"platform": "peertube"}], channels=[sparse]):
        body = text(cards(html)[0])
        assert "Not provided: Subscribers, Unique viewers (30 days)." in body
        assert "Subscribers 0" not in body


def test_a_platform_without_an_entry_prints_no_channel_lines(tmp_path: Path) -> None:
    for html in page(tmp_path, sections=[{"platform": "blog"}]):
        body = text(cards(html)[0])
        assert "as reported by" not in body
        assert "Not provided" not in body


def test_the_block_builds_on_both_pages_and_is_deterministic(tmp_path: Path) -> None:
    first = page(tmp_path / "a", **everything())
    assert first == page(tmp_path / "b", **everything())
    for html in first:
        assert len(cards(html)) == 5


def test_a_small_people_count_prints_under_the_floor(tmp_path: Path) -> None:
    small = entry("lemmy", subscribers=3, active_users_month=4)
    for html in page(tmp_path, sections=[{"platform": "lemmy"}], channels=[small]):
        body = text(cards(html)[0])
        assert "Under 5" in body
        assert not re.search(r"Subscribers\D{0,40}\b3\b", body)


# --- the wall ------------------------------------------------------------------


def test_the_block_is_public_and_never_wider_than_its_field() -> None:
    from reach_around.config.kit import KIT_FIELD_POLICY

    assert KIT_FIELD_POLICY["channels"].visibility == "public"
    kit = kit_with(channels=[entry(p) for p in FIXTURE])
    found = [m for m in compile_measures(kit, None) if m.block == "channels"]
    assert found and all(m.visibility == "public" for m in found)
    assert "channels" in public_projection(kit, None)


def test_a_private_source_keeps_the_block_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from reach_around.config import kit as kit_module
    from reach_around.config.kit import FieldPolicy

    policy = dict(kit_module.KIT_FIELD_POLICY)
    policy["channels"] = FieldPolicy("private")
    monkeypatch.setattr(kit_module, "KIT_FIELD_POLICY", policy)
    found = compile_measures(kit_with(channels=[entry(p) for p in FIXTURE]), None)
    chan = [m for m in found if m.block == "channels"]
    assert chan and all(m.visibility == "private" for m in chan)


def test_the_default_sets_list_the_new_keys() -> None:
    assert default_keys("peertube") == default_keys("mastodon")
    for key in ("subscribers", "peertube_watch_time_30d", "lemmy_active_users_month"):
        assert key in default_keys("peertube")
    audio = default_keys("podcast")
    assert audio == default_keys("blog") == default_keys("newsletter")
    assert audio == (
        "subscribers",
        "downloads_30d",
        "newsletter_delivered",
        "newsletter_clicks",
        "blog_views_30d",
    )


def test_the_registry_defines_every_new_key_once_per_slice() -> None:
    new = {
        "subscribers",
        "lemmy_active_users_month",
        "peertube_watch_time_30d",
        "peertube_avg_watch_time",
        "peertube_unique_viewers_30d",
        "downloads_30d",
        "newsletter_delivered",
        "newsletter_clicks",
        "blog_views_30d",
    }
    assert new <= set(REGISTRY)
    for key in new:
        assert "(#633)" in REGISTRY[key].changelog
        assert REGISTRY[key].visibility == "public"
