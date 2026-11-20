# SPDX-License-Identifier: Apache-2.0
"""Collapsed groups of the smart stack."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from test_smart_stack import raw as stack_raw

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
GOLDEN_COLLAPSED = Path(__file__).parent / "golden_smart_stack_collapsed" / "index.html"
FEATURED = {"link": "follow-1", "kicker": "New this week"}


def raw(*, collapse: tuple[int, ...] = (), featured: bool = False) -> dict[str, Any]:
    data = stack_raw()
    if featured:
        data["page"]["featured"] = FEATURED
    for index in collapse:
        data["tabs"][index]["collapsed"] = True
    return data


def build(tmp_path: Path, data: dict[str, Any]) -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def smart_css(out: Path) -> str:
    return next((out / "static").glob("smart-stack.*.css")).read_text(encoding="utf-8")


def test_collapsed_group_renders_a_closed_details_with_a_heading_summary(
    tmp_path: Path,
) -> None:
    _out, html = build(tmp_path, raw(collapse=(2,)))
    section = html[html.index('id="group-follow"') :]
    section = section[: section.index("</section>")]
    assert '<details class="smart-collapse">' in section
    assert '<details class="smart-collapse" open' not in section
    assert re.search(
        r'<summary><h2 id="group-follow-title">Follow</h2></summary>', section
    )
    assert 'data-link-id="follow-1"' in section
    assert 'data-link-id="follow-2"' in section
    # Other groups are plain, and nothing is collapsed unless asked.
    assert html.count("<details") == 1
    _out2, plain = build(tmp_path / "plain", raw())
    assert "<details" not in plain


def test_collapsed_default_hides_nothing(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    assert "smart-collapse" not in html
    assert "<summary" not in html


def test_collapsed_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path, raw(collapse=(2, 6)))
    if update_golden:
        GOLDEN_COLLAPSED.parent.mkdir(exist_ok=True)
        GOLDEN_COLLAPSED.write_text(html, encoding="utf-8")
    assert GOLDEN_COLLAPSED.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN_COLLAPSED.read_text(encoding="utf-8")


def test_collapsed_summary_is_a_44px_target_with_a_visible_focus_ring(
    tmp_path: Path,
) -> None:
    out, _html = build(tmp_path, raw(collapse=(2,)))
    css = smart_css(out)
    rule = re.search(r"\.smart-collapse > summary \{([^}]*)\}", css)
    assert rule, "no summary rule"
    assert "min-height: var(--ra-tap)" in rule.group(1)
    assert "cursor: pointer" in rule.group(1)
    site = next((out / "static").glob("site.*.css")).read_text(encoding="utf-8")
    assert re.search(r"summary:focus-visible[^{]*\{[^}]*outline: 3px solid", site)
    assert "outline: none" not in css
    assert "outline: 0" not in css
    tokens = next((out / "static").glob("tokens.*.css")).read_text(encoding="utf-8")
    assert "--ra-tap: 2.75rem" in tokens  # 44 px at the default root size


def test_print_opens_a_collapsed_group(tmp_path: Path) -> None:
    out, _html = build(tmp_path, raw(collapse=(2,)))
    css = smart_css(out)
    printed = re.search(r"@media print \{(.*?)\n\}", css, re.S)
    assert printed, "no print rule"
    assert ".smart-collapse::details-content" in printed.group(1)
    assert "content-visibility: visible" in printed.group(1)


def test_collapsed_and_featured_add_no_script(tmp_path: Path) -> None:
    _out, base = build(tmp_path / "a", raw())
    _out, both = build(tmp_path / "b", raw(featured=True, collapse=(2, 3)))
    assert both.count("<script") == base.count("<script")
    assert re.findall(r"<script[^>]*>", both) == re.findall(r"<script[^>]*>", base)
    assert not list((tmp_path / "b" / "out" / "static").glob("*.js")) or sorted(
        p.name for p in (tmp_path / "b" / "out" / "static").glob("*.js")
    ) == sorted(p.name for p in (tmp_path / "a" / "out" / "static").glob("*.js"))
