# SPDX-License-Identifier: Apache-2.0
"""The cutover date (#374): one optional UTC calendar day in the config.

Every name here is synthetic (fake brand, example.com).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from reach_around.cli import main
from reach_around.config import SiteConfig
from reach_around.config.cutover import (
    cutover_date,
    cutover_label,
    live_since,
    utc_today,
)
from reach_around.schema import build_schema
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)


def doc(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {"id": "one", "title": "One", "url": "https://example.com/"}
                ],
            }
        ],
        "kit": {
            "title": "Example Creator media kit",
            "about": "A fake creator.",
            "metrics": [
                {
                    "platform": "twitch",
                    "label": "Followers",
                    "value": 1200,
                    "as_of": "2027-03-01",
                }
            ],
        },
    }
    data.update(extra)
    return data


def cfg(**extra: Any) -> SiteConfig:
    return SiteConfig.model_validate(doc(**extra))


# --- the field and its validation ---------------------------------------------


def test_absent_by_default_and_the_helper_returns_none() -> None:
    config = cfg()
    assert config.cutover_date is None
    assert cutover_date(config) is None
    assert live_since(config, TODAY) is None


def test_a_quoted_day_and_a_yaml_date_both_load() -> None:
    assert cutover_date(cfg(cutover_date="2026-11-08")) == date(2026, 11, 8)
    assert cutover_date(cfg(cutover_date=date(2026, 11, 8))) == date(2026, 11, 8)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-02-30",  # not a real calendar day
        "2026-13-01",
        "2026-11-8",  # not YYYY-MM-DD
        "2026-11-08T00:00:00Z",  # a timestamp is not a day
        "11/08/2026",
        "",
        20261108,
        True,
        datetime(2026, 11, 8, tzinfo=UTC),
        "1999-12-31",  # before 2000-01-01
        "9999-12-31",  # far future
    ],
)
def test_bad_values_are_refused(bad: object) -> None:
    with pytest.raises(ValidationError):
        cfg(cutover_date=bad)


def test_the_far_future_limit_is_one_year_ahead_of_the_utc_day() -> None:
    today = datetime.now(UTC).date()
    assert cutover_date(cfg(cutover_date=(today + timedelta(days=300)).isoformat()))
    with pytest.raises(ValidationError):
        cfg(cutover_date=(today + timedelta(days=500)).isoformat())


def test_live_since_is_none_for_a_future_day() -> None:
    config = cfg(cutover_date="2027-03-11")
    assert cutover_date(config) == date(2027, 3, 11)
    assert live_since(config, TODAY) is None
    assert live_since(config, date(2027, 3, 11)) == date(2027, 3, 11)


def test_kit_visibility_follows_the_utc_day_not_the_build_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build's ``today`` is the config-zone day; the line follows UTC."""
    import reach_around.site.kit as kit_module

    config = cfg(cutover_date="2026-11-08")
    # build day (zone-local) is already the 8th, but UTC is still the 7th
    monkeypatch.setattr(kit_module, "utc_today", lambda: date(2026, 11, 7))
    out = tmp_path / "a"
    build_site(config, out, today=date(2026, 11, 8))
    assert "Live since" not in (out / "kit" / "index.html").read_text("utf-8")
    # build day is still the 7th locally, but UTC has reached the 8th
    monkeypatch.setattr(kit_module, "utc_today", lambda: date(2026, 11, 8))
    out = tmp_path / "b"
    build_site(config, out, today=date(2026, 11, 7))
    assert "Live since 2026-11-08 (UTC)" in (out / "kit" / "index.html").read_text(
        "utf-8"
    )


def test_label_says_utc() -> None:
    assert cutover_label(date(2026, 11, 8)) == "Live since 2026-11-08 (UTC)"


def test_schema_has_the_field() -> None:
    props = build_schema()["properties"]
    assert "cutover_date" in props
    assert "cutover_date" not in build_schema().get("required", [])


# --- the kit -------------------------------------------------------------------


def kit_html(tmp_path: Path, config: SiteConfig) -> str:
    out = tmp_path / "out"
    build_site(config, out, today=TODAY)
    return (out / "kit" / "index.html").read_text(encoding="utf-8")


def test_kit_shows_a_plain_live_since_line_when_set(tmp_path: Path) -> None:
    html = kit_html(tmp_path, cfg(cutover_date="2026-11-08"))
    assert "Live since 2026-11-08 (UTC)" in html
    assert html.count("Live since") == 1
    assert '<time datetime="2026-11-08">' in html


def test_kit_has_no_line_when_unset(tmp_path: Path) -> None:
    assert "Live since" not in kit_html(tmp_path, cfg())


def test_kit_omits_the_line_for_a_future_day(tmp_path: Path) -> None:
    future = (utc_today() + timedelta(days=5)).isoformat()
    assert "Live since" not in kit_html(tmp_path, cfg(cutover_date=future))


def test_gated_page_carries_the_line_too(tmp_path: Path) -> None:
    data = doc(cutover_date="2026-11-08", features={"kit_gate": True})
    data["kit"]["rates"] = [{"item": "Sponsored video", "price_text": "Ask"}]
    out, gated = tmp_path / "out", tmp_path / "gated"
    build_site(SiteConfig.model_validate(data), out, today=TODAY, gated_out=gated)
    page = (gated / "kit.html").read_text(encoding="utf-8")
    assert "Live since 2026-11-08 (UTC)" in page


# --- check ---------------------------------------------------------------------


def run_check(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, data: Any, *extra: str
) -> tuple[int, dict[str, Any]]:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    code = main(["check", "--config", str(path), "--format", "json", *extra])
    return code, json.loads(capsys.readouterr().out)


def warning_kinds(report: dict[str, Any]) -> list[str]:
    return [w["kind"] for w in report["warnings"]]


def test_check_is_quiet_by_default_when_unset(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, report = run_check(capsys, tmp_path, doc())
    assert code == 0 and "cutover_unset" not in warning_kinds(report)


def test_check_production_warns_when_unset(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, report = run_check(capsys, tmp_path, doc(), "--production")
    assert code == 0  # a warning, never an error
    assert "cutover_unset" in warning_kinds(report)


def test_check_production_is_quiet_when_set(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, report = run_check(
        capsys, tmp_path, doc(cutover_date="2026-11-08"), "--production"
    )
    assert code == 0 and "cutover_unset" not in warning_kinds(report)


def test_check_warns_about_a_future_day(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    future = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    code, report = run_check(capsys, tmp_path, doc(cutover_date=future))
    assert code == 0 and "cutover_future" in warning_kinds(report)


def test_check_names_the_field_for_a_bad_value_without_echoing_it(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, report = run_check(capsys, tmp_path, doc(cutover_date="SENTINEL-4f2a9c"))
    assert code == 2
    assert "SENTINEL-4f2a9c" not in json.dumps(report)
    assert any(e.get("field") == "cutover_date" for e in report["errors"])
