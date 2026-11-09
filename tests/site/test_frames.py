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


def test_no_svg_markup_at_any_level(tmp_path: Path) -> None:
    for level in ("l1", "l2", "l3"):
        for rel, html in pages(build(tmp_path / level, level)).items():
            assert "<svg" not in html.lower(), (level, rel)
            assert "ra-gl" not in html, (level, rel)


def test_the_artwork_is_two_packaged_svg_files_used_by_the_stylesheet(
    tmp_path: Path,
) -> None:
    out = build(tmp_path, "l3")
    svgs = sorted(p.name for p in (out / "static").glob("frame*.svg"))
    assert len(svgs) == 2
    css = next((out / "static").glob("frame.*.css")).read_text(encoding="utf-8")
    for name in svgs:
        assert name in css  # url() rewritten to the fingerprinted file
    assert "data:" not in css and "http" not in css
    off = build(tmp_path / "off", None)
    assert not list((off / "static").glob("frame*"))


def test_l1_is_the_lite_frame_everywhere(tmp_path: Path) -> None:
    every = pages(build(tmp_path, "l1"))
    for rel, html in every.items():
        if rel in ("privacy/index.html", "404.html"):
            continue
        assert "ra-l1" in html and "ra-l2" not in html and "ra-l3" not in html, rel


def test_l2_adds_the_full_frame_with_plates_and_no_l3(tmp_path: Path) -> None:
    home = pages(build(tmp_path, "l2"))["index.html"]
    assert "ra-l2" in home and "ra-l1" in home and "ra-l3" not in home


COMPONENTS = {
    # class on the element -> the slot it must carry a frame for (home page)
    "tabs": r'<a class="[^"]*ra-bx[^"]*ra-bare[^"]*" href=',
    "link": r'class="link[^"]*ra-bx',
    "platform": r'class="platform-link[^"]*ra-bx',
    "social": r'class="social[^"]*ra-bx',
    "group title": r'class="platforms-title[^"]*ra-bx',
    "crypto card": r'class="crypto-card[^"]*ra-bx',
    "email card": r'class="email-card[^"]*ra-bx',
    "section heading": r'<summary class="[^"]*ra-bx',
    "profile card": r'class="profile-card[^"]*ra-bx',
    "footer": r'<footer class="[^"]*ra-bx',
}


def _all_blocks_config(frame: str | None, **theme: Any) -> SiteConfig:
    data = config(frame).model_dump(mode="json", exclude_none=True)
    data["creator"]["socials"] = [
        {"name": "Example Mail", "url": "https://example.com/m", "icon": "email"}
    ]
    data["profile_card"] = {"tags": ["One", "Two"]}
    data["features"] = {"kit_gate": True}
    data["kit"]["partners"] = [{"name": "Acme Fake Co"}]
    blocks = data["tabs"][0]["blocks"]
    blocks[2]["title"] = "Find me"
    blocks.append(
        {
            "id": "gear",
            "title": "Gear",
            "links": [{"id": "g1", "title": "G1", "url": "https://example.com/g"}],
        }
    )
    blocks.append(
        {
            "id": "pay",
            "title": "Pay",
            "coins": [
                {
                    "coin": "btc",
                    "address": "bc1qexampleexampleexampleexampleexample",
                }
            ],
        }
    )
    blocks.append({"id": "mail", "title": "Mail", "address": "name@example.test"})
    data["tabs"].append(
        {
            "id": "other",
            "title": "Other",
            "blocks": [{"id": "x2", "title": "X2", "url": "https://example.com/x2"}],
        }
    )
    data["theme"] = {"frame": frame or "none", **theme}
    return SiteConfig.model_validate(data)


def test_every_component_class_is_framed_when_on(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config("l3"),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    home = pages(out)["index.html"]
    for name, pattern in COMPONENTS.items():
        assert re.search(pattern, home), name
    kit = pages(out)["kit/index.html"]
    assert re.search(r'class="kit-total[^"]*ra-bx', kit)
    assert re.search(r'class="kit-head[^"]*ra-bx', kit)
    assert re.search(r'<h3 id="kit-numbers-title" class="[^"]*ra-bx', kit)
    assert re.search(r'class="kit-table[^"]*ra-bx', kit)
    assert re.search(r'<button class="kit-submit[^"]*ra-bx', kit)


def test_nothing_is_framed_when_off(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config(None),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    for rel, html in pages(out).items():
        assert "ra-" not in html, rel
    assert not list((out / "static").glob("frame*"))


def test_no_frame_inside_another_frame_on_any_page(tmp_path: Path) -> None:
    from reach_around.site.frames import _Nest

    out = tmp_path / "out"
    build_site(
        _all_blocks_config("l3"),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    for rel, html in pages(out).items():
        parser = _Nest()
        parser.feed(html)
        assert parser.nested is None, (rel, parser.nested)


def test_l3_default_is_one_per_page_and_the_limit_is_configurable(
    tmp_path: Path,
) -> None:
    one = tmp_path / "one"
    build_site(
        _all_blocks_config("l3"),
        one,
        today=TODAY,
        gated_out=one.parent / (one.name + "-g"),
    )
    for rel, html in pages(one).items():
        assert len(CLASS_L3.findall(html)) <= 1, rel
    many = tmp_path / "many"
    build_site(
        _all_blocks_config("l3", frame_l3_max=8),
        many,
        today=TODAY,
        gated_out=many.parent / (many.name + "-g"),
    )
    counts = {rel: len(CLASS_L3.findall(h)) for rel, h in pages(many).items()}
    assert counts["kit/index.html"] >= 2  # hero tile plus the call to action
    assert max(counts.values()) <= 8


@pytest.mark.parametrize("bad", [0, 65, -1, 1.5, "x"])
def test_the_l3_limit_is_bounded(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config(None).theme.model_validate({"frame_l3_max": bad})


def test_the_l3_limit_defaults_to_one() -> None:
    assert config(None).theme.frame_l3_max == 1


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
    assert "l3" not in frame_plan("l2").values()
    assert frame_plan("l2")["link"] == "l2" and frame_plan("l2")["ctl"] == "l1"
    assert frame_plan("l3")["hero"] == "l3"


def test_check_frames_refuses_too_many_l3_with_a_clear_message() -> None:
    page = '<div class="x ra-bx ra-l3 ra-s5"></div>'
    check_frames({"a.html": page})
    with pytest.raises(ValueError, match=r"b\.html: 2 L3 border frames"):
        check_frames({"b.html": page + page})
    check_frames({"c.html": page + page}, 2)
    with pytest.raises(ValueError, match=r"d\.html: 3 L3 border frames"):
        check_frames({"d.html": page * 3}, 2)


def test_check_frames_refuses_a_nested_frame_naming_the_page() -> None:
    inner = '<span class="ra-bx ra-l1 ra-s2">x</span>'
    check_frames({"ok.html": inner + inner})
    with pytest.raises(ValueError, match=r"n\.html: a border frame sits inside"):
        check_frames({"n.html": f'<div class="ra-bx ra-l1 ra-s3">{inner}</div>'})
    check_frames({"v.html": '<div class="ra-bx ra-l1"><img src="x"></div>' + inner})


def test_the_build_stops_on_an_override_that_nests_or_exceeds(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "_footer.html").write_text(
        '<footer><div class="ra-bx ra-l3 ra-s5"></div>'
        '<div class="ra-bx ra-l3 ra-s5"></div></footer>'
    )
    with pytest.raises(ValueError, match=r"L3 border frames"):
        build_site(config("l3"), tmp_path / "o1", today=TODAY, overrides=overrides)
    (overrides / "templates" / "_footer.html").write_text(
        '<footer><div class="ra-bx ra-l1 ra-s5">'
        '<p class="ra-bx ra-l1 ra-s2">x</p></div></footer>'
    )
    with pytest.raises(ValueError, match=r"inside another border frame"):
        build_site(config("l3"), tmp_path / "o2", today=TODAY, overrides=overrides)


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


def test_print_keeps_the_link_address_rule() -> None:
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/frame.css"
    ).read_text(encoding="utf-8")
    # No ::after rule outside @media screen / print may set content: site.css
    # prints a link's address there.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    outside = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
    assert "::after" not in outside
    screen = css[css.index("@media screen") :]
    assert "a.link.ra-bx::after" in screen.split("@media print")[0]
    printed = css[css.index("@media print") :]
    assert "::before" in printed and "border-image: none" in printed
    forced = css[
        css.index("@media (forced-colors: active)") : css.index("@media print")
    ]
    assert "border-image: none" in forced


def test_stylesheet_has_no_hex_beyond_the_artwork_and_no_unsafe_urls() -> None:
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/frame.css"
    ).read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert re.findall(r'url\("([^"]+)"\)', css) == [
        "frame-lite.svg",
        "frame.svg",
    ]
    assert "@import" not in css and "data:" not in css


def test_frame_motion_is_off_by_default_and_adds_nothing(tmp_path: Path) -> None:
    assert config(None).theme.frame_motion == "none"
    for rel, html in pages(build(tmp_path, "l3")).items():
        assert "ra-mo" not in html, rel


def test_frame_motion_live_marks_the_body_and_nothing_else(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config("l3", frame_motion="live"),
        out,
        today=TODAY,
        gated_out=tmp_path / "g",
    )
    home = pages(out)["index.html"]
    assert '<body class="ra-mo"' in home
    for html in pages(out).values():
        assert not STYLE_ATTR.search(html)


def test_frame_motion_without_frames_does_nothing(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config(None, frame_motion="live"),
        out,
        today=TODAY,
        gated_out=tmp_path / "g",
    )
    for html in pages(out).values():
        assert "ra-mo" not in html


@pytest.mark.parametrize("bad", ["on", "LIVE", "", True, 1])
def test_unknown_frame_motion_is_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config(None).theme.model_validate({"frame_motion": bad})


def test_the_animation_is_opt_in_reduced_motion_safe_and_paint_only() -> None:
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/frame.css"
    ).read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert css.count("animation:") == 1
    block = css[css.index("@media (prefers-reduced-motion: no-preference)") :]
    assert block.index("body.ra-mo .ra-l3.ra-bx") < block.index("animation:")
    keyframes = re.findall(r"@keyframes ([\w-]+) \{(.*?)\n\}", css, flags=re.S)
    assert len(keyframes) == 4
    for _name, body in keyframes:
        props = set(re.findall(r"(--?[\w-]+)\s*:", body))
        assert props <= {"--ra-fr-ct", "--ra-fr-c1", "--ra-fr-c2", "--ra-fr-cs"}


def _mix(a: tuple[float, ...], b: tuple[float, ...], pct: float) -> tuple[float, ...]:
    """color-mix(in srgb, a pct%, b), channels 0 to 255."""
    return tuple(
        x * pct / 100 + y * (100 - pct) / 100 for x, y in zip(a, b, strict=True)
    )


def _lum(c: tuple[float, ...]) -> float:
    lin = [
        v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4
        for v in c
    ]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize(
    "accent",
    [
        (0xFB, 0xB0, 0x3B),  # the brand amber
        (0x1D, 0x4E, 0xD8),  # a blue
        (0xD9, 0x2B, 0x2B),  # a red
        (0x10, 0x8A, 0x3C),  # a green
        (0xFF, 0xE0, 0x00),  # a very light yellow
        (0x7A, 0x2B, 0xD9),  # a violet
    ],
)
def test_frame_text_contrast_is_aa_for_any_accent(accent: tuple[float, ...]) -> None:
    """The frame's ink, muted ink, icon ink and on-panel button text derive from
    the accent by the same color-mix formulas as frame.css (kept equal by the
    stylesheet assertions below)."""
    white, black = (255.0, 255.0, 255.0), (0.0, 0.0, 0.0)
    cream = _mix(accent, white, 18)
    cream_hi = _mix(accent, white, 30)
    ink = _mix(accent, black, 15)
    muted = _mix(ink, cream, 72)
    accent_ink = _mix(accent, black, 45)
    assert _ratio(ink, cream) >= 7
    assert _ratio(ink, cream_hi) >= 7
    assert _ratio(muted, cream) >= 4.5
    assert _ratio(accent_ink, cream) >= 4.5  # link text and icons on the panel
    assert _ratio(cream, accent_ink) >= 4.5  # button text on the dark accent fill


def test_the_contrast_formulas_match_the_stylesheet() -> None:
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/frame.css"
    ).read_text(encoding="utf-8")
    for decl in (
        "--ra-fr-cream: color-mix(in srgb, var(--ra-color-accent) 18%, white)",
        "--ra-fr-cream-hi: color-mix(in srgb, var(--ra-color-accent) 30%, white)",
        "--ra-fr-ink: color-mix(in srgb, var(--ra-color-accent) 15%, black)",
        "--ra-fr-ink-muted: color-mix(in srgb, var(--ra-fr-ink) 72%, "
        "var(--ra-fr-cream))",
        "--ra-fr-accent-ink: color-mix(in srgb, var(--ra-color-accent) 45%, black)",
    ):
        assert decl in css, decl
