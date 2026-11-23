# SPDX-License-Identifier: Apache-2.0
"""Kit first screen (#684): niche line, chips, range line, Request the kit and
Download the PDF, in that order and before the numbers. Everything is synthetic.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_kit_build import build, gated_doc, kit_doc, make_config

from reach_around.config.kit import KitConfig, public_projection
from reach_around.kit.stage import stage_page

PDF_URL = "https://example.com/kit-public.pdf"


def _html(tmp_path: Path, doc: dict | None = None) -> str:  # type: ignore[type-arg]
    out, _ = build(tmp_path, make_config(doc if doc is not None else gated_doc()))
    return (out / "kit" / "index.html").read_text(encoding="utf-8")


def _first(html: str) -> str:
    match = re.search(r'<div class="kit-first".*?</div>\s*</div>', html, re.S)
    assert match, "no first-screen block"
    return match.group(0)


def test_first_screen_comes_before_the_numbers_in_reading_order(
    tmp_path: Path,
) -> None:
    html = _html(tmp_path, gated_doc(tags=["Gaming", "Hardware"], public_pdf=PDF_URL))
    first = _first(html)
    order = [
        first.index('id="kit-title"'),
        first.index('class="kit-about"'),
        first.index("kit-tags"),
        first.index('class="kit-range"'),
        first.index('class="kit-actions"'),
    ]
    assert order == sorted(order)
    assert html.index('class="kit-first"') < html.index('class="kit-numbers"')
    assert html.index('class="kit-actions"') < html.index('class="kit-numbers"')


def test_range_line_is_the_largest_audience_to_the_sum_never_one_number(
    tmp_path: Path,
) -> None:
    # twitch 1,200 + youtube 3,400 count; bluesky 66 is outside the total
    html = _html(tmp_path)
    match = re.search(r'<p class="kit-range">(.*?)</p>', html, re.S)
    assert match
    text = re.sub(r"<[^>]+>", "", match.group(1))
    assert text == "Unique people are somewhere between 3,400 and 4,600."


@pytest.mark.parametrize(
    "metrics",
    [
        # one counted number: there is no range to state
        [
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 1200,
                "as_of": "2027-03-01",
            }
        ],
        # a member under the privacy floor hides the total, so it hides the range
        [
            {
                "platform": "twitch",
                "label": "Followers",
                "value": 3,
                "as_of": "2027-03-01",
            },
            {
                "platform": "youtube",
                "label": "Subscribers",
                "value": 3400,
                "as_of": "2027-03-05",
            },
        ],
    ],
)
def test_no_range_line_without_a_printable_total(
    tmp_path: Path,
    metrics: list[dict],  # type: ignore[type-arg]
) -> None:
    html = _html(tmp_path, kit_doc(metrics=metrics))
    assert 'class="kit-range"' not in html
    assert "somewhere between" not in html


def test_request_the_kit_goes_to_the_gate_only_when_there_is_a_gate(
    tmp_path: Path,
) -> None:
    html = _html(tmp_path)
    assert 'id="kit-request"' in html
    assert re.search(r'<a [^>]*href="#kit-request"[^>]*>Request the kit</a>', html)
    # no gated content: no gate box, so no dead link
    plain = _html(tmp_path / "plain", kit_doc())
    assert "Request the kit" not in plain
    assert "kit-request" not in plain


def test_download_pdf_needs_a_configured_https_url(tmp_path: Path) -> None:
    html = _html(tmp_path, gated_doc(public_pdf=PDF_URL))
    assert re.search(
        rf'<a [^>]*href="{re.escape(PDF_URL)}"[^>]*>Download the PDF</a>', html
    )
    assert "Download the PDF" not in _html(tmp_path / "none")
    for bad in ("http://example.com/a.pdf", "javascript:alert(1)", "/kit.pdf", " "):
        with pytest.raises(ValueError):
            KitConfig.model_validate(kit_doc(public_pdf=bad))


def test_public_pdf_url_is_public_policy_and_never_reaches_the_gated_only_text() -> (
    None
):
    kit = KitConfig.model_validate(kit_doc(public_pdf=PDF_URL))
    assert public_projection(kit, None)["public_pdf"] == PDF_URL
    assert not kit.has_gated_content


def test_the_actions_are_dropped_from_the_public_pdf_but_the_range_stays(
    tmp_path: Path,
) -> None:
    out, _ = build(tmp_path, make_config(gated_doc(public_pdf=PDF_URL)))
    staged = stage_page(out, out, public=True).html
    assert 'class="kit-actions"' not in staged
    assert "Request the kit" not in staged
    assert "Download the PDF" not in staged
    assert PDF_URL not in staged
    assert "somewhere between" in staged


def test_gated_page_shows_the_range_but_no_actions(tmp_path: Path) -> None:
    _, gated = build(tmp_path, make_config(gated_doc(public_pdf=PDF_URL)))
    html = (gated / "kit.html").read_text(encoding="utf-8")
    assert "somewhere between 3,400 and 4,600" in re.sub(r"<[^>]+>", "", html)
    assert 'class="kit-actions"' not in html
