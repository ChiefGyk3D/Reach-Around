# SPDX-License-Identifier: Apache-2.0
"""Rendering the engagement and average-views rows (#423).

Gated by policy (owner decision): shown on the gated page and the owner PDF only.
Synthetic data only.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import build, kit_doc, make_config, tree_bytes

from kitpdfharness import build_instance, fake_renderer
from kitpdfharness import kit_doc as pdf_kit_doc
from kitpdfharness import make_config as pdf_config
from reach_around.config.engagement import (
    AVERAGE_VIEWS_FORMULA,
    ENGAGEMENT_FORMULA,
)
from reach_around.config.kit import DISPLAY_POLICY
from reach_around.kit.pdf import PdfError, build_kit_pdf
from reach_around.kit.pdftext import extract_pages
from reach_around.site.kit import GATED_FILE, KitLeakError


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


def gated_html(tmp_path: Path, kit: dict[str, Any]) -> str:
    _, gated = build(tmp_path, make_config(kit))
    return (gated / GATED_FILE).read_text(encoding="utf-8")


public_html = gated_html  # the tests below read the gated page


def test_rows_render_on_the_gated_page_with_formula_period_and_date(
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
    rates = [{"item": "Sponsored video fake", "price_text": "Ask me!"}]
    html = gated_html(tmp_path, kit_doc(rates=rates))
    assert "kit-engagement" not in html and "Engagement rate" not in html
    html = gated_html(tmp_path / "e", kit_doc(rates=rates, engagement=[]))
    assert "kit-engagement" not in html


def test_the_public_output_holds_no_engagement_value_or_heading(
    tmp_path: Path,
) -> None:
    out, gated = build(tmp_path, make_config(kit_doc(engagement=rows())))
    public = tree_bytes(out).decode("utf-8", "replace")
    for text in (
        "Engagement",
        "engagement",
        "Average views per post",
        "5.0%",
        "1,500",
        "2027-02-28",
        "Chat messages and follows",
        "kit-engagement",
        "never added or averaged",
    ):
        assert text not in public, text
    assert "5.0%" in (gated / GATED_FILE).read_text(encoding="utf-8")


def test_engagement_alone_makes_the_kit_gated_and_needs_the_gate(
    tmp_path: Path,
) -> None:
    out, gated = build(tmp_path, make_config(kit_doc(engagement=rows())))
    assert (gated / GATED_FILE).is_file()
    assert "kit-gate" in (out / "kit" / "index.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="kit_gate"):
        make_config(kit_doc(engagement=rows()), features={"kit_gate": False})


def test_the_leak_search_covers_the_engagement_note(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "kit.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        "<p>Chat messages and follows</p>{% endblock %}",
        encoding="utf-8",
    )
    with pytest.raises(KitLeakError, match=r"engagement"):
        build(tmp_path, make_config(kit_doc(engagement=rows())), overrides=overrides)


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


def test_an_engagement_only_public_kit_still_builds_the_owner_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = pdf_kit_doc()
    kit = {key: kit[key] for key in ("title", "about")}
    kit["engagement"] = rows()
    config = pdf_config(kit)
    monkeypatch.setitem(DISPLAY_POLICY, "engagement", "public")
    assert not config.kit.has_gated_content

    config, site, staged = build_instance(tmp_path, config)
    result = build_kit_pdf(
        config,
        gated_dir=staged,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=date(2027, 3, 10),
        renderer=fake_renderer,
    )

    flat = "".join(" ".join(extract_pages(result.path.read_bytes())).split())
    assert "5.0%" in flat and "1,500" in flat


def test_the_pdf_check_fails_when_a_figure_differs(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match=r"engagement"):
        _pdf(tmp_path, tamper=True)
