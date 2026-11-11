# SPDX-License-Identifier: Apache-2.0
"""The media kit config: models, display policy, staleness and the unified total.

Every name here is synthetic (fake brand, example.com).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from reach_around.commands.check import _schema_problems
from reach_around.config import SiteConfig, load_site_config
from reach_around.config.kit import (
    DISPLAY_POLICY,
    STALE_DAYS,
    CaseStudy,
    KitConfig,
    Partner,
    PlatformMetric,
    Rate,
    gated_projection,
    kit_warnings,
    policy_gaps,
    public_projection,
    resolve_metrics,
    unified_total,
    visibility,
)
from reach_around.config.loader import ConfigError
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.schema import render_schema

TODAY = date(2027, 3, 10)
CANARY = "CANARY-GATED-7f3a"


def manual(
    platform: str = "twitch", value: int | None = 100, **kw: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "platform": platform,
        "label": "Followers",
        "source": "manual",
        "value": value,
        "as_of": "2027-03-01",
    }
    out.update(kw)
    return out


def counts(platform: str = "twitch") -> dict[str, Any]:
    return {"platform": platform, "label": "Followers", "source": "counts"}


def kit_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Example Creator media kit",
        "about": "A fake creator used in tests.",
        "metrics": [manual()],
    }
    doc.update(kw)
    return doc


def site_doc(**kw: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example"},
        "tabs": [
            {
                "id": "links",
                "title": "Links",
                "blocks": [
                    {"id": "home", "title": "Home", "url": "https://example.com/"}
                ],
            }
        ],
        **kw,
    }


def numbers(**found: int) -> Numbers:
    stamp = datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
    return Numbers(
        entries={k: NumbersEntry(count=v, as_of=stamp) for k, v in found.items()}
    )


# --- models ------------------------------------------------------------------


def test_a_full_kit_validates() -> None:
    kit = KitConfig.model_validate(
        kit_doc(
            partners=[
                {
                    "name": "Acme Fake Co",
                    "note": "2026 sponsor",
                    "url": "https://example.com/a",
                }
            ],
            case_studies=[
                {"title": "Launch", "summary": "A launch.", "result": "10k views"}
            ],
            rates=[{"item": "Sponsored video", "price_text": "Ask me!"}],
            demographics=[
                {"label": "Top country", "value": "US 40%", "as_of": "2027-03-01"}
            ],
        )
    )
    assert kit.rates[0].price_text == "Ask me!"
    assert kit.unlock_link is False


def test_unlock_link_defaults_false_and_must_be_a_real_bool() -> None:
    assert KitConfig.model_validate(kit_doc()).unlock_link is False
    with pytest.raises(ValidationError):
        KitConfig.model_validate(kit_doc(unlock_link="yes"))


def test_unlock_link_true_is_refused_until_the_emailed_link_exists() -> None:
    with pytest.raises(ValidationError, match=r"emailed unlock link") as caught:
        KitConfig.model_validate(kit_doc(unlock_link=True))
    assert "unlock_link" in str(caught.value)
    assert "false" in str(caught.value)
    raw = site_doc(kit=kit_doc(unlock_link=True))
    with pytest.raises(ValidationError) as again:
        SiteConfig.model_validate(raw)
    kinds = {p.kind for p in _schema_problems(raw, again.value)}
    assert kinds == {"kit_unlock_link"}


def test_site_key_is_optional_public_and_plain() -> None:
    assert KitConfig.model_validate(kit_doc()).turnstile_site_key is None
    key = "0x4AAAAAAAexample-key_1"
    kit = KitConfig.model_validate(kit_doc(turnstile_site_key=key))
    assert kit.turnstile_site_key == key
    assert visibility("turnstile_site_key") == "public"
    for bad in ("", "has space", 'quo"te', "x" * 65, "<b>", 5):
        with pytest.raises(ValidationError):
            KitConfig.model_validate(kit_doc(turnstile_site_key=bad))


@pytest.mark.parametrize(
    "build",
    [
        lambda: KitConfig.model_validate(kit_doc(surprise=1)),
        lambda: Partner.model_validate({"name": "A", "surprise": 1}),
        lambda: CaseStudy.model_validate(
            {"title": "t", "summary": "s", "result": "r", "surprise": 1}
        ),
        lambda: Rate.model_validate({"item": "i", "price_text": "p", "surprise": 1}),
        lambda: PlatformMetric.model_validate(manual(surprise=1)),
    ],
)
def test_extra_keys_are_refused(build: Any) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        build()


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.com/",
        "//example.com/",
        "https://u:p@example.com/",
        "https://exa mple.com/",
    ],
)
def test_partner_url_is_https_only(url: str) -> None:
    with pytest.raises(ValidationError):
        Partner.model_validate({"name": "A", "url": url})
    assert Partner.model_validate({"name": "A", "url": "https://example.com/x"})


BAD_TEXT = [
    "<b>hi</b>",
    "a < b",
    "x>y",
    "<script>alert(1)</script>",
    "bad" + chr(0) + "byte",
    "line" + chr(10) + "break",
    "tab" + chr(9) + "here",
    "zero" + chr(0x200B) + "width",
    "bidi" + chr(0x202E) + "flip",
    " leading",
    "trailing ",
]


@pytest.mark.parametrize("text", BAD_TEXT)
def test_text_rejects_html_and_hidden_characters(text: str) -> None:
    builders = (
        lambda t: Partner.model_validate({"name": t}),
        lambda t: Partner.model_validate({"name": "A", "note": t}),
        lambda t: Rate.model_validate({"item": t, "price_text": "x"}),
        lambda t: Rate.model_validate({"item": "x", "price_text": t}),
        lambda t: CaseStudy.model_validate({"title": t, "summary": "s", "result": "r"}),
        lambda t: CaseStudy.model_validate({"title": "t", "summary": t, "result": "r"}),
        lambda t: CaseStudy.model_validate({"title": "t", "summary": "s", "result": t}),
        lambda t: KitConfig.model_validate(kit_doc(about=t)),
        lambda t: KitConfig.model_validate(kit_doc(title=t)),
        lambda t: PlatformMetric.model_validate(manual(label=t)),
    )
    for build in builders:
        with pytest.raises(ValidationError):
            build(text)


@pytest.mark.parametrize(
    ("build", "limit"),
    [
        (lambda t: Partner.model_validate({"name": t}), 80),
        (lambda t: Partner.model_validate({"name": "A", "note": t}), 200),
        (lambda t: Rate.model_validate({"item": t, "price_text": "x"}), 80),
        (lambda t: Rate.model_validate({"item": "x", "price_text": t}), 80),
        (
            lambda t: CaseStudy.model_validate(
                {"title": t, "summary": "s", "result": "r"}
            ),
            100,
        ),
        (
            lambda t: CaseStudy.model_validate(
                {"title": "t", "summary": t, "result": "r"}
            ),
            500,
        ),
        (
            lambda t: CaseStudy.model_validate(
                {"title": "t", "summary": "s", "result": t}
            ),
            300,
        ),
        (lambda t: KitConfig.model_validate(kit_doc(title=t)), 120),
        (lambda t: KitConfig.model_validate(kit_doc(about=t)), 1000),
        (lambda t: PlatformMetric.model_validate(manual(label=t)), 60),
    ],
)
def test_length_caps(build: Any, limit: int) -> None:
    assert build("a" * limit)
    with pytest.raises(ValidationError, match="string_too_long"):
        build("a" * (limit + 1))
    with pytest.raises(ValidationError, match="string_too_short"):
        build("")


def test_list_caps() -> None:
    rates = [{"item": f"i{n}", "price_text": "x"} for n in range(31)]
    with pytest.raises(ValidationError, match="too_long"):
        KitConfig.model_validate(kit_doc(rates=rates))


def test_text_must_be_a_string_not_a_number() -> None:
    with pytest.raises(ValidationError, match="string_type"):
        Rate.model_validate({"item": "x", "price_text": 100})


def test_metric_platform_must_be_a_known_slug() -> None:
    with pytest.raises(ValidationError):
        PlatformMetric.model_validate(manual(platform="not-a-platform"))
    assert PlatformMetric.model_validate(manual(platform="other"))


def test_manual_metric_requires_as_of() -> None:
    doc = manual()
    del doc["as_of"]
    with pytest.raises(ValidationError, match="needs an as_of"):
        PlatformMetric.model_validate(doc)
    with pytest.raises(ValidationError, match="needs an as_of"):
        PlatformMetric.model_validate(manual(as_of=None))


def test_source_defaults_to_manual() -> None:
    doc = manual()
    del doc["source"]
    assert PlatformMetric.model_validate(doc).source == "manual"


@pytest.mark.parametrize(
    "bad", ["2027-03-01T10:00:00", "March 1", 20270301, "2027-3-1"]
)
def test_as_of_is_a_plain_date(bad: Any) -> None:
    with pytest.raises(ValidationError):
        PlatformMetric.model_validate(manual(as_of=bad))


def test_as_of_accepts_a_yaml_date_object() -> None:
    metric = PlatformMetric.model_validate(manual(as_of=date(2027, 3, 1)))
    assert metric.as_of == date(2027, 3, 1)


def test_counts_metric_carries_no_value_or_date() -> None:
    assert PlatformMetric.model_validate(counts()).value is None
    with pytest.raises(ValidationError, match="counts file"):
        PlatformMetric.model_validate({**counts(), "value": 5})
    with pytest.raises(ValidationError, match="counts file"):
        PlatformMetric.model_validate({**counts(), "as_of": "2027-03-01"})


def test_unknown_source_refused() -> None:
    with pytest.raises(ValidationError, match="literal_error"):
        PlatformMetric.model_validate(manual(source="scraped"))


@pytest.mark.parametrize("value", [-1, 10**12 + 1, 1.5, "100", True])
def test_metric_value_is_a_bounded_whole_number(value: Any) -> None:
    with pytest.raises(ValidationError):
        PlatformMetric.model_validate(manual(value=value))


def test_duplicate_platform_and_label_refused() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        KitConfig.model_validate(kit_doc(metrics=[manual(), manual()]))
    assert KitConfig.model_validate(kit_doc(metrics=[manual(), manual(label="Subs")]))


def test_models_are_frozen() -> None:
    kit = KitConfig.model_validate(kit_doc())
    with pytest.raises(ValidationError):
        kit.__setattr__("title", "x")


# --- display policy ----------------------------------------------------------


def test_policy_classifies_every_field() -> None:
    assert policy_gaps(KitConfig.model_fields, DISPLAY_POLICY) == []
    assert {k for k, v in DISPLAY_POLICY.items() if v == "public"} == {
        "title",
        "about",
        "photo",
        "contact",
        "tags",
        "metrics",
        "unified_total",
        "turnstile_site_key",
    }
    for name in ("partners", "case_studies", "rates", "demographics"):
        assert visibility(name) == "gated"
    assert visibility("unlock_link") == "private"


def test_policy_guard_bites_on_a_missing_entry() -> None:
    fields = [*KitConfig.model_fields, "brand_new_field"]
    assert policy_gaps(fields, DISPLAY_POLICY) == ["brand_new_field"]
    with pytest.raises(KeyError):
        visibility("brand_new_field")


def test_policy_values_are_closed() -> None:
    assert set(DISPLAY_POLICY.values()) <= {"public", "gated", "private"}


def kit_with_canaries() -> KitConfig:
    return KitConfig.model_validate(
        kit_doc(
            partners=[{"name": f"{CANARY}-partner"}],
            case_studies=[{"title": f"{CANARY}-case", "summary": "s", "result": "r"}],
            rates=[{"item": f"{CANARY}-rate", "price_text": "Ask me!"}],
            demographics=[
                {"label": f"{CANARY}-demo", "value": "x", "as_of": "2027-03-01"}
            ],
        )
    )


def test_public_projection_has_only_header_and_headline_numbers() -> None:
    out = public_projection(kit_with_canaries(), None)
    assert set(out) == {
        "title",
        "about",
        "photo",
        "contact",
        "tags",
        "metrics",
        "unified_total",
        "turnstile_site_key",
    }
    assert CANARY not in json.dumps(out, default=str)


def test_gated_projection_adds_gated_fields_but_never_private() -> None:
    out = gated_projection(kit_with_canaries(), None)
    assert set(out) == {
        name for name, level in DISPLAY_POLICY.items() if level in ("public", "gated")
    }
    assert {
        "title",
        "about",
        "contact",
        "metrics",
        "unified_total",
        "turnstile_site_key",
        "partners",
        "case_studies",
        "rates",
        "demographics",
        "inquiry",
    } <= set(out)
    assert "unlock_link" not in out
    assert CANARY in json.dumps(out, default=str)


def test_projection_follows_the_policy_table(monkeypatch: pytest.MonkeyPatch) -> None:
    from reach_around.config import kit as kit_module

    moved = {**DISPLAY_POLICY, "rates": "public"}
    monkeypatch.setattr(kit_module, "DISPLAY_POLICY", moved)
    assert "rates" in public_projection(kit_with_canaries(), None)


# --- staleness ---------------------------------------------------------------


def test_stale_manual_numbers_warn_above_thirty_days() -> None:
    assert STALE_DAYS == 30
    fresh = manual(as_of="2027-02-08")  # exactly 30 days
    stale = manual(platform="kick", as_of="2027-02-07")  # 31 days
    kit = KitConfig.model_validate(kit_doc(metrics=[fresh, stale]))
    warnings = kit_warnings(kit, None, TODAY)
    assert len(warnings) == 1
    assert "kick" in warnings[0]
    assert "31 days" in warnings[0]


def test_fresh_numbers_do_not_warn() -> None:
    assert kit_warnings(KitConfig.model_validate(kit_doc()), None, TODAY) == []


def test_future_dates_warn() -> None:
    kit = KitConfig.model_validate(kit_doc(metrics=[manual(as_of="2027-03-11")]))
    assert any("future" in w for w in kit_warnings(kit, None, TODAY))


def test_stale_demographics_warn() -> None:
    kit = KitConfig.model_validate(
        kit_doc(
            demographics=[{"label": "Age", "value": "18-24", "as_of": "2026-01-01"}]
        )
    )
    assert any("Age" in w and "days old" in w for w in kit_warnings(kit, None, TODAY))


def test_stale_counts_file_entry_warns() -> None:
    kit = KitConfig.model_validate(kit_doc(metrics=[counts()]))
    old = Numbers(
        entries={
            "twitch": NumbersEntry(count=5, as_of=datetime(2027, 1, 1, tzinfo=UTC))
        }
    )
    assert any("twitch" in w for w in kit_warnings(kit, old, TODAY))


def test_counts_metric_with_no_entry_warns_that_it_has_no_value() -> None:
    kit = KitConfig.model_validate(kit_doc(metrics=[counts()]))
    assert any("no value" in w for w in kit_warnings(kit, None, TODAY))


# --- unified total -----------------------------------------------------------


def test_unified_total_is_the_sum_of_valued_metrics() -> None:
    kit = KitConfig.model_validate(
        kit_doc(
            metrics=[
                manual("twitch", 100),
                manual("kick", 50),
                manual("youtube", None),
                counts("bluesky"),
                counts("github"),
            ]
        )
    )
    total = unified_total(resolve_metrics(kit, numbers(bluesky=7)))
    assert total.total == 157
    assert total.counted == 3
    assert total.ignored == 2


def test_unified_total_is_none_when_nothing_has_a_value() -> None:
    kit = KitConfig.model_validate(kit_doc(metrics=[manual(value=None)]))
    total = unified_total(resolve_metrics(kit, None))
    assert total.total is None
    assert (total.counted, total.ignored) == (0, 1)


def test_in_total_false_is_left_out_of_the_sum() -> None:
    kit = KitConfig.model_validate(
        kit_doc(
            metrics=[
                manual("twitch", 100),
                manual("twitch", 900, label="Views", in_total=False),
            ]
        )
    )
    total = unified_total(resolve_metrics(kit, None))
    assert total.total == 100
    assert total.counted == 1


def test_a_manual_metric_ignores_the_counts_file() -> None:
    kit = KitConfig.model_validate(kit_doc(metrics=[manual("twitch", 100)]))
    assert unified_total(resolve_metrics(kit, numbers(twitch=999))).total == 100


def test_projection_carries_the_total_and_dates() -> None:
    out = public_projection(KitConfig.model_validate(kit_doc()), None)
    assert out["unified_total"].total == 100
    assert out["metrics"][0].as_of == date(2027, 3, 1)


# --- SiteConfig wiring -------------------------------------------------------


def test_kit_is_absent_by_default() -> None:
    assert SiteConfig.model_validate(site_doc()).kit is None


def test_kit_section_loads_in_a_site() -> None:
    cfg = SiteConfig.model_validate(site_doc(kit=kit_doc()))
    assert cfg.kit is not None
    assert cfg.kit.metrics[0].platform == "twitch"


@pytest.mark.parametrize(
    "extra",
    [
        {"partners": [{"name": "A"}]},
        {"case_studies": [{"title": "t", "summary": "s", "result": "r"}]},
        {"rates": [{"item": "i", "price_text": "Ask me!"}]},
        {"demographics": [{"label": "l", "value": "v", "as_of": "2027-03-01"}]},
    ],
)
def test_gated_content_needs_the_kit_gate_feature(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match=r"features\.kit_gate"):
        SiteConfig.model_validate(site_doc(kit=kit_doc(**extra)))
    cfg = SiteConfig.model_validate(
        site_doc(kit=kit_doc(**extra), features={"kit_gate": True})
    )
    assert cfg.features.kit_gate


def test_check_command_names_the_problems() -> None:
    raw = site_doc(kit=kit_doc(about="<b>x</b>", metrics=[manual(as_of=None)]))
    with pytest.raises(ValidationError) as caught:
        SiteConfig.model_validate(raw)
    kinds = {p.kind for p in _schema_problems(raw, caught.value)}
    assert {"no_html", "kit_manual_date"} <= kinds
    assert "not_allowed" not in kinds


def test_check_command_names_the_gate_problem() -> None:
    raw = site_doc(kit=kit_doc(rates=[{"item": "i", "price_text": "Ask me!"}]))
    with pytest.raises(ValidationError) as caught:
        SiteConfig.model_validate(raw)
    kinds = {p.kind for p in _schema_problems(raw, caught.value)}
    assert kinds == {"kit_gate_off"}


def test_load_site_config_reports_the_field(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(site_doc(kit=kit_doc(about="<i>x</i>"))), "utf-8")
    with pytest.raises(ConfigError, match=r"kit\.about"):
        load_site_config(path)


def test_schema_describes_the_kit() -> None:
    schema = json.loads(render_schema())
    assert "KitConfig" in schema["$defs"]
    assert {"type": "null"} in schema["properties"]["kit"]["anyOf"]
    kit = schema["$defs"]["KitConfig"]
    assert kit["properties"]["unlock_link"]["default"] is False
    assert kit["additionalProperties"] is False
