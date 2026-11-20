# SPDX-License-Identifier: Apache-2.0
"""The featured slot of the smart stack."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_smart_stack import raw as stack_raw

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
HERE = Path(__file__).parent
GOLDEN_FEATURED = HERE / "golden_smart_stack_featured" / "index.html"


def raw(*, featured: dict[str, Any] | None = None) -> dict[str, Any]:
    data = stack_raw()
    if featured is not None:
        data["page"]["featured"] = featured
    return data


def build(
    tmp_path: Path, data: dict[str, Any], today: date = TODAY
) -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=today)
    return out, (out / "index.html").read_text(encoding="utf-8")


def smart_css(out: Path) -> str:
    return next((out / "static").glob("smart-stack.*.css")).read_text(encoding="utf-8")


FEATURED = {"link": "follow-1", "kicker": "New this week"}


def test_featured_renders_the_kicker_and_link_between_watch_and_groups(
    tmp_path: Path,
) -> None:
    _out, html = build(tmp_path, raw(featured=FEATURED))
    assert 'class="smart-featured"' in html
    assert "New this week" in html
    assert html.index('class="smart-watch"') < html.index('class="smart-featured"')
    assert html.index('class="smart-featured"') < html.index('id="group-videos"')
    block = html[html.index('class="smart-featured"') : html.index('id="group-videos"')]
    assert 'data-link-id="follow-1"' in block


def test_featured_is_absent_when_not_configured(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    assert "smart-featured" not in html


@pytest.mark.parametrize(
    ("window", "day", "shown"),
    [
        ({"show_from": "2027-03-11"}, date(2027, 3, 10), False),
        ({"show_from": "2027-03-10"}, date(2027, 3, 10), True),
        ({"show_until": "2027-03-09"}, date(2027, 3, 10), False),
        ({"show_until": "2027-03-10"}, date(2027, 3, 10), True),
        ({"show_from": "2027-03-01", "show_until": "2027-03-31"}, TODAY, True),
        ({"show_from": "2027-04-01", "show_until": "2027-04-30"}, TODAY, False),
    ],
)
def test_featured_is_hidden_outside_its_dates(
    tmp_path: Path, window: dict[str, str], day: date, shown: bool
) -> None:
    _out, html = build(tmp_path, raw(featured={**FEATURED, **window}), day)
    assert ("smart-featured" in html) is shown
    assert ("New this week" in html) is shown
    # The link itself stays in its own group either way.
    assert 'data-link-id="follow-1"' in html


def test_featured_is_hidden_when_its_link_is_hidden(tmp_path: Path) -> None:
    data = raw(featured=FEATURED)
    data["tabs"][2]["blocks"][0]["show_until"] = "2027-03-01"
    _out, html = build(tmp_path, data)
    assert "smart-featured" not in html
    assert 'data-link-id="follow-1"' not in html


def test_featured_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path, raw(featured=FEATURED))
    if update_golden:
        GOLDEN_FEATURED.parent.mkdir(exist_ok=True)
        GOLDEN_FEATURED.write_text(html, encoding="utf-8")
    assert GOLDEN_FEATURED.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN_FEATURED.read_text(encoding="utf-8")


def test_featured_adds_no_script(tmp_path: Path) -> None:
    _out, base = build(tmp_path / "a", raw())
    _out, featured = build(tmp_path / "b", raw(featured=FEATURED))
    assert re.findall(r"<script[^>]*>", featured) == re.findall(r"<script[^>]*>", base)
    scripts_a = sorted(p.name for p in (tmp_path / "a" / "out" / "static").glob("*.js"))
    scripts_b = sorted(p.name for p in (tmp_path / "b" / "out" / "static").glob("*.js"))
    assert scripts_a == scripts_b
