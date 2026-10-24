# SPDX-License-Identifier: Apache-2.0
"""Link thumbnails: the ``image`` and ``image_alt`` fields, end to end."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.models import Link
from reach_around.site.assets import collect_assets, referenced_assets
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG = b"\x89PNG\r\n\x1a\nsynthetic" + IEND
THUMB_MAX = 256 * 1024
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
    b'<rect width="4" height="4" fill="red"/></svg>'
)
BASE = {"id": "one", "title": "One", "url": "https://example.com/1"}


def link(**extra: Any) -> Link:
    return Link.model_validate({**BASE, **extra})


def png_of(size: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"x" * (size - 8 - len(IEND)) + IEND


def config(blocks: list[dict[str, Any]], **creator: Any) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "C", **creator},
            "tabs": [{"id": "a", "title": "A", "blocks": blocks}],
        }
    )


def inst(tmp_path: Path, files: dict[str, bytes]) -> Path:
    work = tmp_path / "inst"
    work.mkdir(exist_ok=True)
    for rel, data in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(data)
    return work


# --- validation matrix ----------------------------------------------------------


def test_defaults_are_absent_and_decorative() -> None:
    item = link()
    assert item.image is None
    assert item.image_alt == ""


@pytest.mark.parametrize(
    "path",
    [
        "m/a.png",
        "a.jpg",
        "a.jpeg",
        "a.webp",
        "a.avif",
        "a.gif",
        "a.svg",
        "a.PNG",
        "thumbs/deep/a.png",
    ],
)
def test_accepts_local_image_paths(path: str) -> None:
    assert link(image=path).image == path


@pytest.mark.parametrize(
    "path",
    [
        "https://example.com/a.png",
        "http://example.com/a.png",
        "//example.com/a.png",
        "data:image/png;base64,AAAA",
        "../a.png",
        "/etc/a.png",
        "a/../b.png",
        "a.ico",
        "site.yaml",
        "a.png.yaml",
        "static/a.png",
        "privacy/a.png",
        "a b.png",
        "",
        "a.png\n",
    ],
)
def test_refuses_bad_image_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        link(image=path)


def test_image_alt_limits() -> None:
    assert link(image="a.png", image_alt="x" * 120).image_alt == "x" * 120
    with pytest.raises(ValidationError, match="120"):
        link(image="a.png", image_alt="x" * 121)


def test_image_alt_without_image_is_refused() -> None:
    with pytest.raises(ValidationError, match="image_alt"):
        link(image_alt="A logo")


def test_image_must_be_a_string() -> None:
    with pytest.raises(ValidationError):
        link(image=5)


# --- the asset pipeline ---------------------------------------------------------


def test_thumbnail_is_collected_for_a_top_level_link(tmp_path: Path) -> None:
    work = inst(tmp_path, {"t/a.png": PNG})
    cfg = config([{**BASE, "image": "t/a.png"}])
    assert referenced_assets(cfg) == ["t/a.png"]
    assert collect_assets(cfg, work) == {Path("t/a.png"): PNG}


def test_thumbnail_is_collected_for_a_link_in_a_section(tmp_path: Path) -> None:
    work = inst(tmp_path, {"a.svg": SVG})
    cfg = config([{"id": "sec", "title": "S", "links": [{**BASE, "image": "a.svg"}]}])
    assert collect_assets(cfg, work) == {Path("a.svg"): SVG}


def test_a_renamed_text_file_is_refused_with_the_key_name(tmp_path: Path) -> None:
    work = inst(tmp_path, {"a.png": b"SECRET=1\n"})
    cfg = config([{**BASE, "image": "a.png"}])
    with pytest.raises(ValueError, match=r"tabs\[0\]\.blocks\[0\]\.image.*not a real"):
        collect_assets(cfg, work)


def test_a_hostile_svg_thumbnail_is_refused(tmp_path: Path) -> None:
    bad = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    work = inst(tmp_path, {"a.svg": bad})
    cfg = config([{"id": "s", "title": "S", "links": [{**BASE, "image": "a.svg"}]}])
    with pytest.raises(ValueError, match=r"blocks\[0\]\.links\[0\]\.image.*svg"):
        collect_assets(cfg, work)


def test_oversize_thumbnail_is_refused_with_resize_advice(tmp_path: Path) -> None:
    work = inst(tmp_path, {"a.png": png_of(THUMB_MAX + 1)})
    cfg = config([{**BASE, "image": "a.png"}])
    with pytest.raises(ValueError, match=r"blocks\[0\]\.image.*256 KiB.*resize"):
        collect_assets(cfg, work)


def test_thumbnail_at_the_cap_passes(tmp_path: Path) -> None:
    work = inst(tmp_path, {"a.png": png_of(THUMB_MAX)})
    cfg = config([{**BASE, "image": "a.png"}])
    assert len(collect_assets(cfg, work)[Path("a.png")]) == THUMB_MAX


def test_the_cap_applies_when_the_file_is_also_the_avatar(tmp_path: Path) -> None:
    work = inst(tmp_path, {"a.png": png_of(THUMB_MAX + 1)})
    both = config([{**BASE, "image": "a.png"}], avatar="a.png")
    with pytest.raises(ValueError, match="256 KiB"):
        collect_assets(both, work)


def test_the_cap_does_not_apply_to_an_avatar_alone(tmp_path: Path) -> None:
    big = png_of(THUMB_MAX + 1)
    work = inst(tmp_path, {"a.png": big})
    cfg = config([BASE], avatar="a.png")
    assert collect_assets(cfg, work)[Path("a.png")] == big


def test_symlinked_thumbnail_is_refused(tmp_path: Path) -> None:
    work = inst(tmp_path, {"real.png": PNG})
    (work / "a.png").symlink_to(work / "real.png")
    cfg = config([{**BASE, "image": "a.png"}])
    with pytest.raises(ValueError, match="symlink"):
        collect_assets(cfg, work)


def test_thumbnail_behind_a_symlinked_folder_is_refused(tmp_path: Path) -> None:
    work = inst(tmp_path, {"real/a.png": PNG})
    (work / "t").symlink_to(work / "real")
    cfg = config([{**BASE, "image": "t/a.png"}])
    with pytest.raises(ValueError, match="symlink"):
        collect_assets(cfg, work)


def test_missing_thumbnail_stops_the_build(tmp_path: Path) -> None:
    work = inst(tmp_path, {})
    cfg = config([{**BASE, "image": "nope.png"}])
    with pytest.raises(ValueError, match="nope.png"):
        build_site(cfg, tmp_path / "out", today=TODAY, assets_dir=work)


# --- rendering ------------------------------------------------------------------


def build(
    tmp_path: Path, blocks: list[dict[str, Any]], files: dict[str, bytes]
) -> Path:
    work = inst(tmp_path, files)
    out = tmp_path / "out"
    build_site(config(blocks), out, today=TODAY, assets_dir=work)
    return out


def test_renders_a_lazy_sized_square_img_with_alt(tmp_path: Path) -> None:
    out = build(
        tmp_path,
        [{**BASE, "image": "t/a.png", "image_alt": "Logo of One"}],
        {"t/a.png": PNG},
    )
    html = (out / "index.html").read_text()
    match = re.search(r'<img class="link-thumb"[^>]*>', html)
    assert match, html
    tag = match.group(0)
    assert 'src="t/a.png"' in tag
    assert 'alt="Logo of One"' in tag
    assert 'loading="lazy"' in tag
    assert 'decoding="async"' in tag
    assert 'width="44"' in tag and 'height="44"' in tag
    assert "referrerpolicy" in tag
    assert (out / "t" / "a.png").read_bytes() == PNG


def test_default_alt_is_empty_so_it_is_decorative(tmp_path: Path) -> None:
    out = build(tmp_path, [{**BASE, "image": "a.png"}], {"a.png": PNG})
    html = (out / "index.html").read_text()
    assert re.search(r'<img class="link-thumb"[^>]* alt=""', html)


def test_alt_text_is_escaped(tmp_path: Path) -> None:
    out = build(
        tmp_path,
        [{**BASE, "image": "a.png", "image_alt": '"><script>x</script>'}],
        {"a.png": PNG},
    )
    assert "<script>x" not in (out / "index.html").read_text()


def test_image_wins_over_icon(tmp_path: Path) -> None:
    out = build(tmp_path, [{**BASE, "image": "a.png", "icon": "link"}], {"a.png": PNG})
    html = (out / "index.html").read_text()
    anchor = html[html.index('<a class="link') :].split("</a>")[0]
    assert "link-thumb" in anchor
    assert "icon-link" not in anchor
    assert "link-chip" not in anchor


def test_icon_alone_still_renders_the_chip(tmp_path: Path) -> None:
    out = build(tmp_path, [{**BASE, "icon": "link"}], {})
    html = (out / "index.html").read_text()
    assert "link-chip" in html and "link-thumb" not in html


def test_absent_field_adds_nothing(tmp_path: Path) -> None:
    out = build(tmp_path, [BASE], {})
    assert "link-thumb" not in (out / "index.html").read_text()


def test_section_link_on_second_tab_uses_the_right_prefix(tmp_path: Path) -> None:
    work = inst(tmp_path, {"a.png": PNG})
    cfg = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "C"},
            "tabs": [
                {"id": "a", "title": "A", "blocks": [BASE]},
                {
                    "id": "b",
                    "title": "B",
                    "blocks": [
                        {
                            "id": "sec",
                            "title": "S",
                            "links": [{**BASE, "id": "two", "image": "a.png"}],
                        }
                    ],
                },
            ],
        }
    )
    out = tmp_path / "out"
    build_site(cfg, out, today=TODAY, assets_dir=work)
    assert 'src="../a.png"' in (out / "b" / "index.html").read_text()


def test_stylesheet_crops_the_thumbnail_with_tokens() -> None:
    css = (
        Path(__file__).parents[2] / "src/reach_around/site/static/site.css"
    ).read_text()
    rule = re.search(r"\.link-thumb\s*\{([^}]*)\}", css)
    assert rule, "no .link-thumb rule"
    body = rule.group(1)
    assert "object-fit: cover" in body
    assert "var(--ra-thumb)" in body
    assert "var(--ra-thumb-radius)" in body
