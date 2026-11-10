# SPDX-License-Identifier: Apache-2.0
"""Rendering the engagement and average-views rows (#423).

Public by policy: shown on the public kit page, the gated page and the owner PDF.
Synthetic data only.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import GATED_FILE, build, gated_doc, kit_doc, make_config

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.config.engagement import (
    AVERAGE_VIEWS_FORMULA,
    ENGAGEMENT_FORMULA,
)
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import extract_pages


def rows() -> list[dict[str, Any]]:
    return [
        {
            "platform": "twitch",
            "period_start": "2027-02-01",
            "period_end": "2027-02-28",
            "as_of": "2027-03-01",
            "views": 12000,
            "posts": 8,
            "interactions": 600,
            "counts": "Chat messages and follows",
        },
        {"platform": "kick"},
    ]


def public_html(tmp_path: Path, kit: dict[str, Any]) -> str:
    out, _ = build(
        tmp_path, make_config(kit, features={"kit_gate": False}), gated=False
    )
    return (out / "kit" / "index.html").read_text(encoding="utf-8")


def test_rows_render_on_the_public_page_with_formula_period_and_date(
    tmp_path: Path,
) -> None:
    html = public_html(tmp_path, kit_doc(engagement=rows()))
    assert "Average views per post" in html and "Engagement rate" in html
    assert "1,500" in html and "5.0%" in html  # 12000/8 and 600/12000
    assert "2027-02-01 to 2027-02-28" in re.sub(r"<[^>]+>", "", html)
    assert 'datetime="2027-03-01"' in html
    assert AVERAGE_VIEWS_FORMULA in html and ENGAGEMENT_FORMULA in html
    assert "Interactions counted: Chat messages and follows" in html
    assert 'class="kit-engagement"' in html
    assert html.index("Audience numbers") < html.index("kit-engagement")


def test_a_platform_with_no_source_shows_not_available_and_keeps_its_row(
    tmp_path: Path,
) -> None:
    html = public_html(tmp_path, kit_doc(engagement=rows()))
    assert html.count("not available") == 2
    kick = html[html.index("Kick", html.index("kit-engagement")) :]
    assert "Average views per post" in kick and "Engagement rate" in kick


def test_twitch_and_kick_use_the_same_layout(tmp_path: Path) -> None:
    both = rows()
    both[1] = {**both[0], "platform": "kick"}
    html = public_html(tmp_path, kit_doc(engagement=both))
    section = html[html.index("kit-engagement") :]
    assert section.count('<th scope="row">Twitch<') == 2
    assert section.count('<th scope="row">Kick<') == 2
    # the same cells in the same order, apart from the platform name
    cells = re.findall(r"<tr><th scope=\"row\">(Twitch|Kick)<(.*?)</tr>", section)
    assert [r for n, r in cells if n == "Twitch"] == [
        r for n, r in cells if n == "Kick"
    ]


def test_a_note_says_nothing_is_added_across_platforms(tmp_path: Path) -> None:
    html = public_html(tmp_path, kit_doc(engagement=rows()))
    assert "never added or averaged across platforms" in html.lower()


def test_values_are_escaped(tmp_path: Path) -> None:
    both = rows()
    both[0]["counts"] = 'Likes & "shares" {{ 7*7 }} it\'s &amp; done'
    html = public_html(tmp_path, kit_doc(engagement=both))
    assert "Likes &amp; &#34;shares&#34; {{ 7*7 }} it&#39;s &amp;amp; done" in html


def test_absent_or_empty_renders_nothing(tmp_path: Path) -> None:
    html = public_html(tmp_path, kit_doc())
    assert "kit-engagement" not in html and "Engagement rate" not in html
    html = public_html(tmp_path / "e", kit_doc(engagement=[]))
    assert "kit-engagement" not in html


def test_the_rows_are_public_so_they_need_no_gate(tmp_path: Path) -> None:
    out, gated = build(
        tmp_path,
        make_config(kit_doc(engagement=rows()), features={"kit_gate": False}),
        gated=False,
    )
    assert "5.0%" in (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert not (gated / GATED_FILE).exists()


def test_rows_also_render_on_the_gated_page(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc(engagement=rows())))
    page = (gated / GATED_FILE).read_text(encoding="utf-8")
    assert "5.0%" in page
    assert "2027-02-01 to 2027-02-28" in re.sub(r"<[^>]+>", "", page)


def test_no_script_form_or_remote_request_is_added(tmp_path: Path) -> None:
    html = public_html(tmp_path, kit_doc(engagement=rows()))
    section = html[html.index("kit-engagement") :]
    assert "<script" not in section and "http" not in section.split("</section>")[0]


def _pdf(tmp_path: Path, tamper: bool = False) -> Path:
    kit = pdf_kit_doc()
    kit["engagement"] = rows()
    config, site, gated = build_instance(tmp_path, pdf_config(kit))
    if tamper:
        page = gated / GATED_FILE
        page.write_text(
            page.read_text(encoding="utf-8").replace("5.0%", "6.0%"), encoding="utf-8"
        )
    return build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
    ).path


def test_the_rows_are_in_the_owner_pdf(tmp_path: Path) -> None:
    flat = "".join(" ".join(extract_pages(_pdf(tmp_path).read_bytes())).split())
    for text in ("5.0%", "1,500", "2027-02-01 to 2027-02-28", "not available"):
        assert "".join(text.split()) in flat


def test_the_pdf_check_fails_when_a_figure_differs(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match=r"engagement"):
        _pdf(tmp_path, tamper=True)
