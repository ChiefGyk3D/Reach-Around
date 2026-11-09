# SPDX-License-Identifier: Apache-2.0
"""Border frames (theme.frame): off by default, minimal and faithful, dark only."""

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
FA_FILES = 7 * 2  # seven size classes, each with an on-amber variant


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
    for level in ("minimal", "faithful", "l1", "l2", "l3"):
        for rel, html in pages(build(tmp_path / level, level)).items():
            assert "<svg" not in html.lower(), (level, rel)


def test_the_artwork_is_packaged_svg_files_used_by_the_stylesheet(
    tmp_path: Path,
) -> None:
    out = build(tmp_path, "faithful")
    svgs = sorted(p.name for p in (out / "static").glob("frame*.svg"))
    assert len(svgs) == 1 + FA_FILES
    css = next((out / "static").glob("frame.*.css")).read_text(encoding="utf-8")
    for name in svgs:
        assert name in css  # url() rewritten to the fingerprinted file
    assert "data:" not in css and "http" not in css
    off = build(tmp_path / "off", None)
    assert not list((off / "static").glob("frame*"))


def test_minimal_is_the_light_frame_and_l1_is_its_alias(tmp_path: Path) -> None:
    for level in ("minimal", "l1"):
        every = pages(build(tmp_path / level, level))
        for rel, html in every.items():
            if rel in ("privacy/index.html", "404.html"):
                continue
            assert "ra-mn" in html and "ra-fa" not in html, (level, rel)


def test_faithful_puts_the_art_on_the_featured_link_and_l2_l3_are_aliases(
    tmp_path: Path,
) -> None:
    for level in ("faithful", "l2", "l3"):
        home = pages(build(tmp_path / level, level))["index.html"]
        assert re.search(r'class="link link-featured[^"]*ra-fa ra-z-xl ra-am', home)
        assert "ra-mn" in home, level


COMPONENTS = {
    # class on the element -> the slot it must carry a frame for (home page)
    "tabs": r'<a class="[^"]*ra-bx[^"]*ra-bare[^"]*" href=',
    "link": r'class="link[^"]*ra-bx',
    "platform": r'class="platform-link[^"]*ra-bx',
    "group title": r'class="platforms-title[^"]*ra-bx',
    "section heading": r'<summary class="[^"]*ra-bx',
    "profile card": r'class="profile-card[^"]*ra-bx',
}
# Left bare by default: small icons, chips, list rows, cards, the footer.
UNFRAMED = {
    "social": r'class="social[^"]*ra-bx',
    "crypto card": r'class="crypto-card[^"]*ra-bx',
    "email card": r'class="email-card[^"]*ra-bx',
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


def test_default_placement_frames_some_classes_and_leaves_the_rest_bare(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config("faithful"),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    home = pages(out)["index.html"]
    for name, pattern in COMPONENTS.items():
        assert re.search(pattern, home), name
    for name, pattern in UNFRAMED.items():
        assert not re.search(pattern, home), name
    kit = pages(out)["kit/index.html"]
    assert re.search(r'class="kit-head[^"]*ra-bx', kit)
    assert re.search(r'<h3 id="kit-numbers-title" class="[^"]*ra-bx', kit)
    assert re.search(r'<button class="kit-submit[^"]*ra-bx[^"]*ra-fa', kit)
    assert not re.search(r'class="kit-total[^"]*ra-bx', kit)
    assert not re.search(r'class="kit-table[^"]*ra-bx', kit)


def test_a_class_override_adds_removes_and_changes_a_frame(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config(
            "faithful",
            frame_classes={"ctl": "minimal", "link": "none", "head": "faithful"},
        ),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    home = pages(out)["index.html"]
    assert re.search(r'class="social[^"]*ra-bx[^"]*ra-mn', home)
    assert 'class="link"' in home and 'class="link ra-bx' not in home
    assert re.search(r'<summary class="[^"]*ra-fa', home)


def test_an_override_does_nothing_while_the_frame_is_none(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(
        _all_blocks_config(None, frame_classes={"link": "faithful"}),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    for rel, html in pages(out).items():
        assert "ra-" not in html, rel
    assert not list((out / "static").glob("frame*"))


@pytest.mark.parametrize(
    "bad",
    [{"nope": "minimal"}, {"link": "l1"}, {"link": "bright"}, {"link": True}, []],
)
def test_a_bad_override_is_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config(None).theme.model_validate({"frame_classes": bad})


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
        _all_blocks_config("faithful"),
        out,
        today=TODAY,
        gated_out=out.parent / (out.name + "-g"),
    )
    for rel, html in pages(out).items():
        parser = _Nest()
        parser.feed(html)
        assert parser.nested is None, (rel, parser.nested)


def test_the_old_l3_limit_and_motion_settings_are_accepted_and_ignored(
    tmp_path: Path,
) -> None:
    assert config(None).theme.frame_l3_max == 1
    out = tmp_path / "out"
    build_site(
        _all_blocks_config("l3", frame_l3_max=8, frame_motion="live"),
        out,
        today=TODAY,
        gated_out=tmp_path / "g",
    )
    for html in pages(out).values():
        assert "ra-mo" not in html and "ra-l3" not in html


@pytest.mark.parametrize("bad", [0, 65, -1, 1.5, "x"])
def test_the_ignored_l3_limit_is_still_bounded(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config(None).theme.model_validate({"frame_l3_max": bad})


@pytest.mark.parametrize("bad", ["on", "LIVE", "", True, 1])
def test_unknown_frame_motion_is_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        config(None).theme.model_validate({"frame_motion": bad})


def test_no_style_attribute_or_inline_style_anywhere(tmp_path: Path) -> None:
    for level in ("minimal", "faithful"):
        for rel, html in pages(build(tmp_path / level, level)).items():
            assert not STYLE_ATTR.search(html), (level, rel)
            assert "<style" not in html.lower(), (level, rel)


def test_ids_are_unique_per_page(tmp_path: Path) -> None:
    for rel, html in pages(build(tmp_path, "faithful")).items():
        ids = re.findall(r'\sid="([^"]*)"', html)
        assert len(ids) == len(set(ids)), rel


def test_pages_without_frames_do_not_load_the_stylesheet(tmp_path: Path) -> None:
    every = pages(build(tmp_path, "faithful"))
    for rel in ("privacy/index.html", "404.html"):
        assert "frame." not in every[rel], rel


def test_the_gated_page_carries_no_frame(tmp_path: Path) -> None:
    cfg = config("faithful")
    data = cfg.model_dump(mode="json", exclude_none=True)
    data["features"] = {"kit_gate": True}
    data["kit"]["partners"] = [{"name": "Acme Fake Co"}]
    out, gated = tmp_path / "out", tmp_path / "gated"
    build_site(SiteConfig.model_validate(data), out, today=TODAY, gated_out=gated)
    text = (gated / "kit.html").read_text(encoding="utf-8")
    assert "ra-" not in text and "<svg" not in text and "frame." not in text


def test_plan_levels() -> None:
    assert frame_plan("none") == {}
    assert frame_plan("none", {"link": "faithful"}) == {}
    plan = frame_plan("faithful")
    assert plan["featured"] == plan["cta"] == "faithful"
    assert {plan[k] for k in ("tab", "link", "platform", "head")} == {"minimal"}
    for bare in ("ctl", "chip", "tile", "foot", "bar", "card", "field", "stat"):
        assert bare not in plan, bare
    assert set(frame_plan("minimal").values()) == {"minimal"}
    assert frame_plan("l1") == frame_plan("minimal")
    assert frame_plan("l2") == frame_plan("l3") == plan
    over = frame_plan("faithful", {"tab": "faithful", "link": "none", "ctl": "minimal"})
    assert over["tab"] == "faithful" and "link" not in over and over["ctl"] == "minimal"
    assert frame_plan("minimal", {"link": "faithful"})["link"] == "faithful"


def test_check_frames_refuses_a_nested_frame_naming_the_page() -> None:
    inner = '<span class="ra-bx ra-mn ra-s2">x</span>'
    check_frames({"ok.html": inner + inner})
    with pytest.raises(ValueError, match=r"n\.html: a border frame sits inside"):
        check_frames({"n.html": f'<div class="ra-bx ra-mn ra-s3">{inner}</div>'})
    check_frames({"v.html": '<div class="ra-bx ra-mn"><img src="x"></div>' + inner})


def test_the_build_stops_on_an_override_that_nests(tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "_footer.html").write_text(
        '<footer><div class="ra-bx ra-mn ra-s5">'
        '<p class="ra-bx ra-mn ra-s2">x</p></div></footer>'
    )
    with pytest.raises(ValueError, match=r"inside another border frame"):
        build_site(
            config("faithful"), tmp_path / "o2", today=TODAY, overrides=overrides
        )


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
        build_site(
            config("faithful"), tmp_path / "out", today=TODAY, overrides=overrides
        )


GOLDEN = Path(__file__).parent / "golden_frames"


def test_golden_pages_with_frames_on(tmp_path: Path, update_golden: bool) -> None:
    out = build(tmp_path, "faithful")
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
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert urls[0] == "frame-lite.svg"
    assert all(u.startswith("frame-fa-") and u.endswith(".svg") for u in urls[1:])
    assert len(set(urls[1:])) == FA_FILES
    assert "@import" not in css and "data:" not in css


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


def _frame_css() -> str:
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/frame.css"
    ).read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_the_frame_is_a_border_only_and_paints_no_fill_of_its_own() -> None:
    """The frame keeps each element's own fill and palette (amber stays amber,
    dark stays dark): no cream or white fill, no panel layer, no color override
    of the element's text or fill tokens."""
    css = _frame_css()
    assert "cream" not in css
    assert "--ra-fr-panel" not in css
    assert not re.search(r"\bwhite\b|#fff", css, flags=re.I)
    for token in ("--ra-color-text:", "--ra-color-surface:", "--ra-color-accent:"):
        assert token not in css, token
    body = css[css.index("body .ra-bx.ra-bx {") :]
    first_rule = body[: body.index("}")]
    assert "background" not in first_rule.replace("border-image", "")


def test_bracket_art_reads_on_dark_and_on_amber() -> None:
    """Dark graphite fill (visible on amber) with a light amber outline (visible
    on a dark fill); never white."""
    root = Path(__file__).parents[2] / "src/reach_around/site/static"
    svg = (root / "frame-lite.svg").read_text(encoding="utf-8")
    assert 'stroke="#e0a43c"' in svg
    assert "#fff" not in svg.lower()
    for path in sorted(root.glob("frame-fa-*.svg")):
        art = path.read_text(encoding="utf-8")
        colors = set(re.findall(r"#[0-9a-fA-F]{3,6}\b", art))
        allowed = {"#FBB03B", "#2D200B", "#050505", "#555", "#1c1c1c", "#8a8a8a"}
        assert colors <= allowed, (path.name, colors - allowed)
        assert 'fill="url(#m)"' in art and "<image" not in art
        # on-amber art uses ink rules, the others amber rules
        assert ("#2D200B" in colors) == path.name.endswith("-am.svg"), path.name


def test_the_frame_is_dark_only_with_no_light_palette() -> None:
    css = _frame_css()
    assert "prefers-color-scheme" not in css and "light-dark" not in css
    root = Path(__file__).parents[2] / "src/reach_around/site/static"
    for path in root.glob("frame*.svg"):
        art = path.read_text(encoding="utf-8").lower()
        assert not re.search(r"#fff(fff)?\b|white|cream|fef1dc", art), path.name


def test_frame_sizes_never_go_negative_on_small_elements() -> None:
    """A 44 px control is narrower than the artwork's corner span; every
    `100% - N` size must be clamped so small frames keep a predictable layout."""
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/frame.css"
    ).read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    sizes = re.findall(r"background-size:([^;]*);", css)
    assert sizes
    for block in sizes:
        bare = re.findall(r"(?<!max\(0px, )calc\(100% - ", block)
        assert bare == [], block
