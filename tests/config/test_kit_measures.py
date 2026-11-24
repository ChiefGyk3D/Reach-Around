# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 3 (#628): the measure model, the registry and the compile shim.
Everything is synthetic."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config.blocks import PLATFORM_SLUGS
from reach_around.config.engagement import FORMULAS
from reach_around.config.kit import (
    DISPLAY_POLICY,
    KitConfig,
    gated_projection,
    policy_gaps,
    public_projection,
)
from reach_around.config.measures import (
    DEFAULT_SETS,
    REGISTRY,
    UNITS,
    Measure,
    change_tenths,
    compile_measures,
    default_keys,
    group_of,
    narrow,
    registry_problems,
    visible,
)
from reach_around.config.trust import API, HAND_KEPT

DOCS = Path(__file__).resolve().parents[2] / "docs" / "kit.md"
GATED_CANARY = 7_654_321


def kit_doc(**extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests.",
        "metrics": [
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 4200,
                "as_of": "2027-03-01",
            },
            {
                "platform": "discord",
                "label": "Members",
                "value": 800,
                "as_of": "2027-03-01",
                "in_total": False,
            },
        ],
        "analytics": [
            {
                "platform": "instagram",
                "synced_with": "Example Social API",
                "as_of": "2027-03-02",
                "followers": 900,
                "followers_30_day_percent": 12.34,
                "stats": [{"label": "Profile visits", "value": 77}],
            }
        ],
        "engagement": [
            {
                "platform": "tiktok",
                "period_start": "2027-02-01",
                "period_end": "2027-02-28",
                "as_of": "2027-03-01",
                "views": GATED_CANARY,
                "posts": 10,
                "interactions": 500_000,
                "counts": "likes and comments",
            },
            {"platform": "kick"},
        ],
        "partners": [{"name": "Acme Fake Co"}],
    }
    doc.update(extra)
    return doc


# --- the registry -------------------------------------------------------------


def test_every_registry_entry_has_a_definition_a_changelog_line_and_a_unit() -> None:
    assert REGISTRY, "the registry must not be empty"
    assert registry_problems(REGISTRY) == []
    for key, entry in REGISTRY.items():
        assert entry.key == key
        assert entry.unit in UNITS, key
        assert entry.changelog.startswith(
            ("Added in kit v2 slice 3 (#628)", "Added in kit v2 slice 4 (#629)")
        ), key


def test_the_registry_check_bites() -> None:
    from dataclasses import replace

    base = REGISTRY["followers"]
    em_dash = chr(0x2014)
    odd_level: Any = "hidden"
    for broken, word in (
        (replace(base, definition=""), "definition"),
        (replace(base, changelog=""), "changelog"),
        (replace(base, unit="widgets"), "unit"),
        (replace(base, visibility=odd_level), "visibility"),
        (replace(base, definition=f"Counts {em_dash} followers, always."), "dash"),
        (replace(base, key="other"), "key"),
    ):
        problems = registry_problems({"followers": broken})
        assert any(word in p for p in problems), (word, problems)


def test_every_registry_key_is_documented_in_the_kit_guide() -> None:
    text = DOCS.read_text(encoding="utf-8")
    for key, entry in REGISTRY.items():
        assert f"`{key}`" in text, f"{key} is not in docs/kit.md"
        assert entry.definition in text, f"the definition of {key} is not documented"


def test_computed_entries_carry_the_formulas_the_page_prints() -> None:
    printed = {row["formula"] for row in FORMULAS}
    for key in ("avg_views_per_item", "engagement_rate"):
        assert REGISTRY[key].computed
        assert REGISTRY[key].formula in printed


def test_default_sets_name_registry_keys_and_cover_every_platform() -> None:
    assert DEFAULT_SETS
    for group, keys in DEFAULT_SETS.items():
        assert keys, group
        assert set(keys) <= set(REGISTRY), group
    for slug in PLATFORM_SLUGS:
        assert group_of(slug) in DEFAULT_SETS
        assert default_keys(slug) == DEFAULT_SETS[group_of(slug)]


def test_twitch_and_kick_have_the_same_default_set() -> None:
    assert default_keys("twitch") == default_keys("kick")
    assert group_of("twitch") == group_of("kick") == "live"


# --- the shim -----------------------------------------------------------------


def measures() -> list[Measure]:
    kit = KitConfig.model_validate(kit_doc())
    return compile_measures(kit, None)


def test_metrics_compile_to_followers_and_members() -> None:
    twitch, discord = [m for m in measures() if m.block == "headline"]
    assert (twitch.key, twitch.unit, twitch.value) == ("followers", "follows", 4200)
    assert twitch.as_of == date(2027, 3, 1)
    assert twitch.visibility == "public"
    assert twitch.in_summary is True
    assert (discord.key, discord.in_summary) == ("members", False)


def test_analytics_compile_to_followers_change_and_stats() -> None:
    found = {m.key: m for m in measures() if m.block == "tile"}
    assert found["followers"].value == 900
    assert found["followers"].source_note == "Example Social API"
    assert found["followers_change_30d"].value == 123  # tenths of a percent
    assert found["followers_change_30d"].unit == "percent"
    assert found["analytics_stat"].label == "Profile visits"
    assert found["analytics_stat"].value == 77
    assert all(m.visibility == "public" for m in found.values())
    assert all(not m.in_summary for m in found.values())
    assert all(
        m.trust is not None and m.trust.label == HAND_KEPT for m in found.values()
    )


def test_engagement_compiles_to_two_gated_measures_per_row() -> None:
    rows = [m for m in measures() if m.block == "derived"]
    assert [m.key for m in rows] == [
        "avg_views_per_item",
        "engagement_rate",
        "avg_views_per_item",
        "engagement_rate",
    ]
    assert all(m.visibility == "gated" for m in rows)
    avg, rate, empty_avg, empty_rate = rows
    assert avg.value == (2 * GATED_CANARY + 10) // 20
    assert avg.period == (date(2027, 2, 1), date(2027, 2, 28))
    assert rate.value == 65  # 500000 / 7654321 = 6.5 percent, in tenths
    assert rate.note == "Interactions counted: likes and comments"
    assert empty_avg.value is None and empty_avg.as_of is None
    assert empty_rate.value is None and empty_rate.period is None


def test_a_measure_without_a_trust_label_only_exists_without_a_config() -> None:
    kit = KitConfig.model_validate(kit_doc())
    unlabeled = [m for m in compile_measures(kit, None) if m.block == "headline"]
    assert all(m.trust is None for m in unlabeled)
    labeled = [
        m for m in compile_measures(kit, None, frozenset()) if m.block == "headline"
    ]
    assert all(m.trust is not None and m.trust.label == HAND_KEPT for m in labeled)
    assert API not in {m.trust.label for m in labeled if m.trust}


@pytest.mark.parametrize("step", range(-1200, 1201))
def test_change_tenths_prints_the_same_text_the_old_float_did(step: int) -> None:
    percent = step / 100 + 0.004
    tenths = change_tenths(percent)
    old = f"{percent:+.1f}"
    new = f"{'-' if tenths < 0 else '+'}{abs(tenths) // 10}.{abs(tenths) % 10}"
    # The one difference: a change that rounds to zero has no sign in tenths.
    assert new == ("+0.0" if old == "-0.0" else old)


def test_the_ties_round_to_even_as_the_old_format_did() -> None:
    for percent in (0.25, 0.75, 1.25, -0.25, 2.5, 66.75, 12.5):
        tenths = change_tenths(percent)
        assert (
            f"{percent:+.1f}"
            == f"{'-' if tenths < 0 else '+'}{abs(tenths) // 10}.{abs(tenths) % 10}"
        )


# --- sections -----------------------------------------------------------------


def with_sections(*sections: dict[str, Any]) -> dict[str, Any]:
    return kit_doc(sections=list(sections))


def test_a_section_defaults_to_the_default_set() -> None:
    kit = KitConfig.model_validate(with_sections({"platform": "twitch"}))
    section = kit.sections[0]
    assert section.show == "default"
    assert section.hide_if_empty is False
    assert section.headline is None
    assert section.account is None


def test_a_section_names_registry_keys_only() -> None:
    ok = {"platform": "twitch", "show": ["followers"], "headline": "followers"}
    assert KitConfig.model_validate(with_sections(ok)).sections[0].show == ["followers"]
    with pytest.raises(ValidationError, match="registry"):
        KitConfig.model_validate(
            with_sections({"platform": "twitch", "show": ["invented_key"]})
        )
    with pytest.raises(ValidationError, match="headline"):
        KitConfig.model_validate(
            with_sections(
                {
                    "platform": "twitch",
                    "show": ["followers"],
                    "headline": "engagement_rate",
                }
            )
        )
    with pytest.raises(ValidationError, match="more than once"):
        KitConfig.model_validate(
            with_sections({"platform": "twitch"}, {"platform": "twitch"})
        )
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_sections({"platform": "nowhere"}))
    with pytest.raises(ValidationError):
        KitConfig.model_validate(with_sections({"platform": "twitch", "html": "x"}))
    with pytest.raises(ValidationError):
        KitConfig.model_validate(
            with_sections({"platform": "twitch", "account": "<b>x</b>"})
        )


# --- the display policy and the wall ------------------------------------------


def test_the_new_fields_are_in_the_display_policy_table() -> None:
    assert policy_gaps(KitConfig.model_fields, DISPLAY_POLICY) == []
    assert DISPLAY_POLICY["sections"] == "public"
    assert DISPLAY_POLICY["measures"] == "public"


def test_a_gated_measure_never_reaches_the_public_projection() -> None:
    kit = KitConfig.model_validate(with_sections({"platform": "tiktok"}))
    public = public_projection(kit, None)
    gated = gated_projection(kit, None)
    assert {m.visibility for m in public["measures"]} == {"public"}
    assert "gated" in {m.visibility for m in gated["measures"]}
    assert str(GATED_CANARY) not in json.dumps(public, default=str)
    assert "engagement" not in public
    assert [s.platform for s in public["sections"]] == ["tiktok"]


def test_the_projection_guard_bites_when_a_measure_is_marked_wider_than_its_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry says engagement figures are gated; a registry that said public
    must still not widen them, because the source field's policy is the cap."""
    from dataclasses import replace

    from reach_around.config import measures as module

    widened = {
        **module.REGISTRY,
        "avg_views_per_item": replace(
            module.REGISTRY["avg_views_per_item"], visibility="public"
        ),
    }
    monkeypatch.setattr(module, "REGISTRY", widened)
    kit = KitConfig.model_validate(kit_doc())
    public = public_projection(kit, None)
    assert "gated" not in {m.visibility for m in public["measures"]}
    assert "avg_views_per_item" not in {m.key for m in public["measures"]}


# --- a measure is never wider than its source (Daybreak round 1) ---------------

LEVELS = ("public", "gated", "private")


@pytest.mark.parametrize("registry", LEVELS)
@pytest.mark.parametrize("source", LEVELS)
def test_narrow_is_never_wider_than_either_input(registry: Any, source: Any) -> None:
    rank = {"public": 0, "gated": 1, "private": 2}
    got = narrow(registry, source)
    assert rank[got] == max(rank[registry], rank[source])
    if "private" in (registry, source):
        assert got == "private"


def test_a_private_measure_is_returned_for_no_audience() -> None:
    secret = Measure(
        platform="other",
        key="future_private_measure",
        label="Secret",
        unit="count",
        value=4815162342,
        as_of=None,
        visibility="private",
        block="tile",
    )
    for allowed in (
        frozenset({"public"}),
        frozenset({"public", "gated"}),
        frozenset({"public", "gated", "private"}),
        frozenset({"private"}),
    ):
        assert visible([secret], allowed) == []


@pytest.mark.parametrize("field", ["metrics", "analytics", "engagement"])
def test_a_private_source_field_reaches_neither_projection(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's reproduction: a source policy of private must not be
    widened to gated by the compile shim."""
    from dataclasses import replace

    from reach_around.config import kit as kit_module

    policy = kit_module.KIT_FIELD_POLICY
    monkeypatch.setattr(
        kit_module,
        "KIT_FIELD_POLICY",
        {**policy, field: replace(policy[field], visibility="private")},
    )
    kit = KitConfig.model_validate(kit_doc())
    compiled = compile_measures(kit, None)
    blocks = {"metrics": "headline", "analytics": "tile", "engagement": "derived"}
    mine = [m for m in compiled if m.block == blocks[field]]
    assert mine and {m.visibility for m in mine} == {"private"}
    for projection in (public_projection(kit, None), gated_projection(kit, None)):
        assert not [m for m in projection["measures"] if m.block == blocks[field]]
