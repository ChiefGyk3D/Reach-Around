# SPDX-License-Identifier: Apache-2.0
"""A measure-producing field set to private reaches no projection and no PDF
(kit v2 slice 3, #628, Daybreak round 2). Synthetic data only."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kitpdfharness import build_instance, fake_renderer, kit_doc, make_config
from reach_around.config import kit as kit_module
from reach_around.config.kit import (
    KitConfig,
    gated_projection,
    public_projection,
)
from reach_around.config.measures import REGISTRY, registry_problems
from reach_around.kit import pdf as pdf_module
from reach_around.kit.pdf import (
    PdfError,
    build_kit_pdf,
    build_public_kit_pdf,
    private_measure_strings,
)
from reach_around.kit.pdftext import extract_pages

SECRET_FOLLOWERS = 481516234
SECRET_TILE = 9162534
SECRET_VIEWS = 7654321
HEADLINE_LABEL = "Canary headline label"
TILE_LABEL = "Canary tile label"

KIT = {
    "title": "Example Creator media kit",
    "about": "A fake creator used in tests.",
    "metrics": [
        {
            "platform": "instagram",
            "label": HEADLINE_LABEL,
            "value": SECRET_FOLLOWERS,
            "as_of": "2027-03-01",
        }
    ],
    "analytics": [
        {
            "platform": "tiktok",
            "synced_with": "Example Social API",
            "as_of": "2027-03-02",
            "followers": 5555555,
            "followers_30_day_percent": 12.3,
            "stats": [{"label": TILE_LABEL, "value": SECRET_TILE}],
        }
    ],
    "engagement": [
        {
            "platform": "twitch",
            "period_start": "2027-01-05",
            "period_end": "2027-01-30",
            "as_of": "2027-01-31",
            "views": SECRET_VIEWS,
            "posts": 10,
            "interactions": 500000,
        }
    ],
    "partners": [{"name": "Acme Fake Co"}],
}
# the figures each source field alone puts on a page
FIGURES = {
    "metrics": (str(SECRET_FOLLOWERS), HEADLINE_LABEL),
    "analytics": ("5555555", str(SECRET_TILE), TILE_LABEL),
    "engagement": (str(SECRET_VIEWS), "765432"),
}


def make_private(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    policy = kit_module.KIT_FIELD_POLICY
    moved = {**policy, field: replace(policy[field], visibility="private")}
    monkeypatch.setattr(kit_module, "KIT_FIELD_POLICY", moved)
    table = {name: rule.visibility for name, rule in moved.items()}
    monkeypatch.setattr(kit_module, "DISPLAY_POLICY", table)
    monkeypatch.setattr(pdf_module, "DISPLAY_POLICY", table)


@pytest.mark.parametrize("field", ["metrics", "analytics", "engagement"])
def test_no_private_figure_is_anywhere_in_either_projection(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_private(monkeypatch, field)
    kit = KitConfig.model_validate(KIT)
    for projection in (public_projection(kit, None), gated_projection(kit, None)):
        text = repr(projection)  # every value, however nested
        for needle in FIGURES[field]:
            assert needle not in text, (field, needle)
        assert field not in projection
    # the aggregate is built from the visible measures only
    if field == "metrics":
        for projection in (public_projection(kit, None), gated_projection(kit, None)):
            assert projection["unified_total"].total is None


def test_the_controls_show_the_figures_when_nothing_is_private() -> None:
    kit = KitConfig.model_validate(KIT)
    text = repr(gated_projection(kit, None))
    for figures in FIGURES.values():
        assert all(needle in text for needle in figures[:1])


def test_registry_problems_accepts_all_three_levels() -> None:
    base = REGISTRY["followers"]
    for level in ("public", "gated", "private"):
        entry: Any = replace(base, visibility=level)
        assert registry_problems({"followers": entry}) == []
    odd_level: Any = "hidden"
    bad = replace(base, visibility=odd_level)
    (problem,) = registry_problems({"followers": bad})
    assert "public, gated or private" in problem


# --- the full PDF ------------------------------------------------------------


def pdf_kit() -> dict[str, Any]:
    doc = kit_doc()
    doc["engagement"] = KIT["engagement"]
    return doc


def make_pdf(tmp_path: Path, config: Any, site: Path, gated: Path) -> Path:
    return build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
    ).path


def test_a_stale_gated_page_with_now_private_figures_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(pdf_kit())
    config, site, gated = build_instance(tmp_path, config)
    out = make_pdf(tmp_path, config, site, gated)  # control: gated, it prints
    assert "765,432" in "".join(" ".join(extract_pages(out.read_bytes())).split())
    out.unlink()
    make_private(monkeypatch, "engagement")
    # the staged page is not rebuilt: it still holds the engagement figures
    with pytest.raises(PdfError, match="private"):
        make_pdf(tmp_path, config, site, gated)


def test_a_kit_whose_only_gated_content_is_private_engagement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = {key: KIT[key] for key in ("title", "about", "engagement")}
    config = make_config(doc)
    make_private(monkeypatch, "engagement")
    with pytest.raises(PdfError, match="nothing private to print"):
        make_pdf(tmp_path, config, tmp_path / "site", tmp_path / "gated")


def test_the_needles_are_built_for_every_private_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative check is only as good as its needles: none empty, each field
    contributes at least one, and nothing is built when nothing is private."""
    kit = KitConfig.model_validate(KIT)
    assert private_measure_strings(kit, None) == {}
    for field in ("metrics", "analytics", "engagement"):
        with monkeypatch.context() as scoped:
            make_private(scoped, field)
            needles = private_measure_strings(kit, None)
            assert needles and all(needles.values())
            assert {key.split(".")[0] for key in needles} == {field}


def test_a_fresh_build_with_private_engagement_prints_and_expects_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_private(monkeypatch, "engagement")
    config, site, gated = build_instance(tmp_path, make_config(pdf_kit()))
    out = make_pdf(tmp_path, config, site, gated)
    assert "765,432" not in "".join(" ".join(extract_pages(out.read_bytes())).split())


# --- compact analytics figures (Daybreak round 3) ----------------------------


def analytics_kit() -> dict[str, Any]:
    doc = kit_doc()  # an unrelated gated partner keeps the full PDF eligible
    doc["analytics"] = [
        {
            "platform": "tiktok",
            "synced_with": "Example Social API",
            "as_of": "2027-03-08",
            "followers": 1200,
            "followers_30_day_percent": 12.3,
            "stats": [{"label": "Example views", "value": 1300}],
        }
    ]
    return doc


def test_the_needles_are_the_forms_the_analytics_panel_prints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit = KitConfig.model_validate(analytics_kit())
    make_private(monkeypatch, "analytics")
    needles = set(private_measure_strings(kit, None).values())
    assert {"Followers1.2k", "Example views1.3k", "+12.3%"} <= needles
    assert "Synced with Example Social API on 2027-03-08" in needles
    assert all(needles)


def test_a_stale_full_pdf_with_now_private_analytics_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path, make_config(analytics_kit()))
    out = make_pdf(tmp_path, config, site, gated)  # control: it prints
    flat = "".join(" ".join(extract_pages(out.read_bytes())).split())
    assert "Followers1.2k" in flat and "+12.3%" in flat
    out.unlink()
    make_private(monkeypatch, "analytics")
    with pytest.raises(PdfError, match="private"):
        make_pdf(tmp_path, config, site, gated)


def test_a_stale_public_pdf_with_now_private_analytics_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, _ = build_instance(tmp_path, make_config(analytics_kit()))

    def make_public() -> Path:
        return build_public_kit_pdf(
            config,
            site_dir=site,
            out_dir=tmp_path / "private",
            name="kit-public.pdf",
            created=date(2027, 3, 10),
            renderer=fake_renderer,
        ).path

    out = make_public()
    flat = "".join(" ".join(extract_pages(out.read_bytes())).split())
    assert "Followers1.2k" in flat
    out.unlink()
    make_private(monkeypatch, "analytics")
    with pytest.raises(PdfError, match="private"):
        make_public()
