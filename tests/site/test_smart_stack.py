# SPDX-License-Identifier: Apache-2.0
"""The smart stack is the production adaptive link-page layout."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_smart_stack" / "index.html"


def raw(*, frame: str = "none") -> dict[str, Any]:
    groups = []
    for intent, title in (
        ("watch", "Watch"),
        ("videos", "Latest videos"),
        ("follow", "Follow"),
        ("community", "Community"),
        ("support", "Support"),
        ("work", "Work with me"),
        ("shop", "Shop"),
        ("blog", "Blog"),
    ):
        groups.append(
            {
                "id": intent,
                "title": title,
                "intent": intent,
                "blocks": [
                    {
                        "id": f"{intent}-{number}",
                        "title": f"{title} link {number}",
                        "url": f"https://example.com/{intent}/{number}",
                        "icon": (
                            "twitch"
                            if intent == "watch" and number == 1
                            else "kick"
                            if intent == "watch"
                            else "link"
                        ),
                        "featured": intent == "videos" and number == 1,
                    }
                    for number in (1, 2)
                ],
            }
        )
    return {
        "version": 1,
        "creator": {"name": "Example Creator", "header": "centered"},
        "features": {"click_tracking": True},
        "theme": {"frame": frame},
        "page": {
            "layout": "stack",
            "status": {
                "text": "Live Friday at 8 PM Eastern",
                "schedule": "Next stream: November 13",
            },
            "watch": ["watch-1", "watch-2"],
        },
        "tabs": groups,
    }


def build(tmp_path: Path, *, frame: str = "none") -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(raw(frame=frame)), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def smart_css(out: Path) -> str:
    path = next((out / "static").glob("smart-stack.*.css"))
    return path.read_text(encoding="utf-8")


def test_stack_builds_one_ordered_page_with_status_and_equal_watch_links(
    tmp_path: Path,
) -> None:
    out, html = build(tmp_path)
    assert 'class="smart-stack"' in html
    assert "Live Friday at 8 PM Eastern" in html
    assert "Next stream: November 13" in html
    assert html.index("Watch link 1") < html.index("Watch link 2")
    assert len(re.findall(r'class="[^"]*\bsmart-watch-link\b', html)) == 2
    positions = [
        html.index(f'id="group-{intent}"')
        for intent in (
            "videos",
            "follow",
            "community",
            "support",
            "work",
            "shop",
            "blog",
        )
    ]
    assert positions == sorted(positions)
    assert not (out / "follow" / "index.html").exists()
    assert not list((out / "static").glob("variants.*"))
    assert len(list((out / "static").glob("smart-stack.*.css"))) == 1
    for ident in ("watch-1", "watch-2", "videos-1", "blog-2"):
        assert f'data-link-id="{ident}"' in html


def test_stack_css_supports_320px_reflow_zoom_and_touch_targets(tmp_path: Path) -> None:
    out, _html = build(tmp_path)
    css = smart_css(out)
    assert "min-height: var(--ra-tap)" in css
    assert "min-width: 0" in css
    assert "width: 100%" in css
    assert "max-width: 100%" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "@media (min-width: 48rem)" in css
    assert "@media (max-width: 29.999rem)" in css
    assert "flex-basis: 100%" in css
    assert "px" not in re.sub(r"29\.999rem", "", css)


def test_stack_keeps_frame_levels_on_status_and_links(tmp_path: Path) -> None:
    out, html = build(tmp_path, frame="faithful")
    assert re.search(r'class="smart-status[^\"]*ra-bx', html)
    assert re.search(r'class="link[^\"]*smart-watch-link[^\"]*ra-bx', html)
    assert re.search(r'class="link link-featured[^\"]*ra-fa ra-z-xl ra-am', html)
    assert list((out / "static").glob("frame.*.css"))


def test_stack_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path)
    if update_golden:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(html, encoding="utf-8")
    assert GOLDEN.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN.read_text(encoding="utf-8")
