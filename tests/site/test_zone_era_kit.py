# SPDX-License-Identifier: Apache-2.0
"""Display zone control and era marks on the kit and link page (#372, #375).

Synthetic data only (fake brand, example.com).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import Page, build, gated_doc, kit_doc, make_config

from reach_around.config.zones import ZONES

# Update this pin only together with a review of static/zone.js.
ZONE_JS_SHA256 = "83e1025518ab22b4b9cc696b4f3506cf3b1d6dd97858d97fbc4a37bc490ef046"
NOW = date(2026, 11, 9)


def zone_js() -> bytes:
    return (resources.files("reach_around.site") / "static" / "zone.js").read_bytes()


def metrics() -> list[dict[str, Any]]:
    return [
        {
            "platform": "twitch",
            "label": "Followers",
            "value": 1200,
            "as_of": "2026-07-01",
        },
        {
            "platform": "youtube",
            "label": "Subscribers",
            "value": 3400,
            "as_of": "2026-09-01",
        },
    ]


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("reach_around.site.kit.utc_today", lambda: NOW)
    monkeypatch.setattr("reach_around.site.platforms.utc_today", lambda: NOW)


def kit_page(tmp_path: Path, **extra: Any) -> str:
    config = make_config(kit_doc(metrics=metrics()), **extra)
    out, _ = build(tmp_path, config)
    return (out / "kit" / "index.html").read_text(encoding="utf-8")


# --- the script -------------------------------------------------------------


def test_script_hash_is_pinned() -> None:
    assert hashlib.sha256(zone_js()).hexdigest() == ZONE_JS_SHA256


@pytest.mark.parametrize(
    "banned",
    [
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "cookieStore",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "fetch(",
        "XMLHttpRequest",
        "sendBeacon",
        "WebSocket",
    ],
)
def test_script_uses_no_storage_network_or_html_sink(banned: str) -> None:
    assert banned not in zone_js().decode("utf-8")


def test_script_is_small() -> None:
    assert len(zone_js()) < 3500


# --- the page ---------------------------------------------------------------


def test_public_kit_has_hidden_control_and_empty_notes(tmp_path: Path) -> None:
    html = kit_page(tmp_path, cutover_date="2026-08-01")
    assert re.search(r"<p[^>]*data-zone-control[^>]* hidden>", html)
    options = re.findall(r'<option value="([^"]+)">', html)
    assert tuple(options) == ZONES
    assert 'data-zone-start="2026-07-01T00:00:00Z"></span>' in html
    assert 'data-zone-start="2026-08-01T00:00:00Z"></span>' in html
    assert "Dates here are UTC days." in html
    # UTC text stays in place and is labeled.
    assert '<time datetime="2026-07-01">2026-07-01</time> UTC' in html
    assert "document.cookie" not in html


def test_script_linked_only_on_public_kit(tmp_path: Path) -> None:
    config = make_config(gated_doc(metrics=metrics()))
    out, gated = build(tmp_path, config)
    public = (out / "kit" / "index.html").read_text(encoding="utf-8")
    retry = (out / "kit" / "retry" / "index.html").read_text(encoding="utf-8")
    gated_html = (gated / "kit.html").read_text(encoding="utf-8")
    assert re.search(r'<script src="\.\./static/zone[^"]*\.js" defer>', public)
    for text in (retry, gated_html):
        assert "zone" not in "".join(
            a.get("src") or "" for a in Page(text).find("script")
        )
        assert "data-zone" not in text
        assert "<select" not in text


def test_script_not_published_without_a_kit(tmp_path: Path) -> None:
    from reach_around.site.build import build_site

    config = make_config(None)
    config = config.model_copy(update={"kit": None})
    out = tmp_path / "plain"
    build_site(config, out, today=date(2027, 3, 10))
    assert not list((out / "static").glob("zone*.js"))


def test_headers_have_no_inline_script_allowance(tmp_path: Path) -> None:
    config = make_config(kit_doc(metrics=metrics()))
    out, _ = build(tmp_path, config)
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert "unsafe-inline" not in headers
    assert "script-src 'self'" in headers


# --- era marks --------------------------------------------------------------


def test_kit_marks_before_and_after_with_cutover_day_after(tmp_path: Path) -> None:
    config = make_config(
        kit_doc(
            metrics=[
                {**metrics()[0], "as_of": "2026-07-31"},
                {**metrics()[1], "as_of": "2026-08-01"},
            ]
        ),
        cutover_date="2026-08-01",
    )
    out, _ = build(tmp_path, config)
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    rows = re.findall(r"<tr><th scope=\"row\">.*?</tr>", html, re.S)
    assert (
        "before Reach Around" in rows[0] and "after" not in rows[0].split("</time>")[1]
    )
    assert '<span class="kit-era">after</span>' in rows[1]
    assert "before Reach Around and numbers from after" in html


def test_kit_adds_nothing_when_cutover_unset_or_ahead(tmp_path: Path) -> None:
    for extra in ({}, {"cutover_date": "2026-12-25"}):
        html = kit_page(tmp_path / ("a" if not extra else "b"), **extra)
        assert "kit-era" not in html
        assert "before Reach Around" not in html


def test_total_not_marked_mixed_when_one_era(tmp_path: Path) -> None:
    html = kit_page(tmp_path, cutover_date="2026-06-01")
    assert 'kit-era">after' in html
    assert "numbers from before" not in html


def test_link_page_marks_platform_numbers(tmp_path: Path) -> None:
    from test_platforms import block, cfg, item, page

    rows = block(
        item(count=10, as_of="2026-07-31"),
        item(platform="youtube", handle="@x", count=20, as_of="2026-08-01"),
    )
    marked = page(cfg(rows, cutover_date="2026-08-01"), tmp_path / "a")
    assert "as of 2026-07-31, before Reach Around" in marked
    assert "as of 2026-08-01, after" in marked
    for extra in ({}, {"cutover_date": "2026-12-25"}):
        plain = page(cfg(rows, **extra), tmp_path / ("b" + str(len(extra))))
        assert "before Reach Around" not in plain
        assert "as of 2026-07-31" in plain


def test_history_note_is_escaped_where_shown() -> None:
    from jinja2 import Environment

    from reach_around.config.era import record_marker

    env = Environment(autoescape=True)
    text = env.from_string("<span>{{ m }}</span>").render(
        m=record_marker("x", "<script>alert(1)</script>")
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
