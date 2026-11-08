# SPDX-License-Identifier: Apache-2.0
"""Border frames (theme.frame): off by default, one system, three levels."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.site.build import build_site
from reach_around.site.frames import check_frames, frame_plan
from reach_around.site.overrides import OverridesError, load_custom_css

TODAY = date(2027, 3, 10)
STYLE_ATTR = re.compile(r"<[^>]*\sstyle\s*=", re.IGNORECASE)
CLASS_L3 = re.compile(r'class="[^"]*\bra-l3\b')


def config(frame: str | None, *, kit: bool = True) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "top",
                        "title": "Top",
                        "url": "https://example.com/top",
                        "featured": True,
                    },
                    {"id": "two", "title": "Two", "url": "https://example.com/2"},
                    {
                        "id": "plat",
                        "items": [
                            {
                                "platform": "twitch",
                                "handle": "example",
                                "url": "https://example.com/t",
                            }
                        ],
                    },
                ],
            }
        ],
    }
    if frame is not None:
        data["theme"] = {"frame": frame}
    if kit:
        data["kit"] = {
            "title": "Example kit",
            "about": "About the kit.",
            "metrics": [
                {
                    "platform": "twitch",
                    "label": "Followers",
                    "value": 1200,
                    "as_of": "2027-03-01",
                },
                {
                    "platform": "youtube",
                    "label": "Subscribers",
                    "value": 3400,
                    "as_of": "2027-03-05",
                },
            ],
        }
    return SiteConfig.model_validate(data)


def build(tmp_path: Path, frame: str | None, **kw: Any) -> Path:
    out = tmp_path / "out"
    build_site(config(frame), out, today=TODAY, **kw)
    return out


def pages(out: Path) -> dict[str, str]:
    return {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(out.rglob("*.html"))
    }


def test_default_is_none_and_writes_nothing(tmp_path: Path) -> None:
    assert config(None).theme.frame == "none"
    out = build(tmp_path, None)
    assert not list((out / "static").glob("frame.*.css"))
    for rel, html in pages(out).items():
        assert "ra-" not in html and "frame" not in html, rel


@pytest.mark.parametrize("bad", ["L1", "l4", "", "full", True, 1])
def test_unknown_level_is_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config(None).theme.model_validate({"frame": bad})


def test_l1_is_css_only(tmp_path: Path) -> None:
    out = build(tmp_path, "l1")
    home = pages(out)["index.html"]
    assert "ra-l1" in home and "ra-l2" not in home and "ra-l3" not in home
    assert "<svg" not in home
    assert list((out / "static").glob("frame.*.css"))
    assert "<svg" not in pages(out)["kit/index.html"]


def test_l2_frames_the_featured_link_only(tmp_path: Path) -> None:
    home = pages(build(tmp_path, "l2"))["index.html"]
    assert home.count("ra-l2") == 1 and "ra-l3" not in home
    featured = home[home.index("link-featured") :]
    assert featured.index("<svg") < featured.index("link-text")
    assert home.count('<svg class="ra-fr"') == 1


def test_l3_is_the_kit_tile_and_only_one(tmp_path: Path) -> None:
    out = build(tmp_path, "l3")
    every = pages(out)
    assert not CLASS_L3.search(every["index.html"])
    kit = every["kit/index.html"]
    assert len(CLASS_L3.findall(kit)) == 1
    assert kit.count('id="ra-gl"') == 1
    assert "frame." in kit  # the stylesheet is linked on the kit page


def test_no_style_attribute_or_inline_style_anywhere(tmp_path: Path) -> None:
    for level in ("l1", "l2", "l3"):
        for rel, html in pages(build(tmp_path / level, level)).items():
            assert not STYLE_ATTR.search(html), (level, rel)
            assert "<style" not in html.lower(), (level, rel)


def test_ids_are_unique_per_page(tmp_path: Path) -> None:
    for rel, html in pages(build(tmp_path, "l3")).items():
        ids = re.findall(r'\sid="([^"]*)"', html)
        assert len(ids) == len(set(ids)), rel


def test_pages_without_frames_do_not_load_the_stylesheet(tmp_path: Path) -> None:
    every = pages(build(tmp_path, "l3"))
    for rel in ("privacy/index.html", "404.html"):
        assert "frame." not in every[rel], rel


def test_the_gated_page_carries_no_frame(tmp_path: Path) -> None:
    cfg = config("l3")
    data = cfg.model_dump(mode="json", exclude_none=True)
    data["features"] = {"kit_gate": True}
    data["kit"]["partners"] = [{"name": "Acme Fake Co"}]
    out, gated = tmp_path / "out", tmp_path / "gated"
    build_site(SiteConfig.model_validate(data), out, today=TODAY, gated_out=gated)
    text = (gated / "kit.html").read_text(encoding="utf-8")
    assert "ra-" not in text and "<svg" not in text and "frame." not in text


def test_plan_levels() -> None:
    assert frame_plan("none") == {}
    assert set(frame_plan("l1").values()) == {"l1"}
    assert frame_plan("l2")["featured"] == "l2"
    assert frame_plan("l3")["hero"] == "l3"
    assert "hero" not in frame_plan("l2")


def test_check_frames_refuses_two_l3_with_a_clear_message() -> None:
    page = '<div class="x ra-bx ra-l3 ra-lg"></div>'
    check_frames({"a.html": page})
    with pytest.raises(ValueError, match=r"b\.html: 2 L3 border frames"):
        check_frames({"b.html": page + page})


def test_check_frames_refuses_a_repeated_frame_id() -> None:
    with pytest.raises(ValueError, match="ra-gl"):
        check_frames({"c.html": '<i id="ra-gl"></i><i id="ra-gl"></i>'})


def test_the_build_stops_on_a_second_l3_from_an_override(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "_footer.html").write_text(
        '{% import "_frames.html" as f %}'
        '<footer><div class="{{ f.cls("hero") }}">{{ f.art("hero") }}</div></footer>'
    )
    out = tmp_path / "out"
    with pytest.raises(ValueError, match=r"2 L3 border frames"):
        build_site(config("l3"), out, today=TODAY, overrides=overrides)


@pytest.mark.parametrize(
    "css",
    [
        "a{background:url(https://evil.example/x.png)}",
        "@import 'https://evil.example/a.css';",
        "a{behavior:expression(alert(1))}",
    ],
)
def test_the_css_sanitizer_still_refuses_unsafe_css(tmp_path: Path, css: str) -> None:
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    (overrides / "custom.css").write_text(css)
    with pytest.raises(OverridesError):
        load_custom_css(overrides)
    with pytest.raises(OverridesError):
        build_site(config("l3"), tmp_path / "out", today=TODAY, overrides=overrides)


GOLDEN = Path(__file__).parent / "golden_frames"


def test_golden_pages_with_frames_on(tmp_path: Path, update_golden: bool) -> None:
    out = build(tmp_path, "l3")
    names = {
        "home.html": out / "index.html",
        "kit.html": out / "kit" / "index.html",
    }
    for name, built in names.items():
        golden = GOLDEN / name
        if update_golden:
            GOLDEN.mkdir(exist_ok=True)
            golden.write_bytes(built.read_bytes())
        assert golden.is_file(), "no golden file; run pytest --update-golden"
        assert built.read_bytes() == golden.read_bytes(), name
