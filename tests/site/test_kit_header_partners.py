# SPDX-License-Identifier: Apache-2.0
"""Kit photo header and brand partner logo grid (#549)."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from test_kit_build import TODAY, gated_doc, make_config

from reach_around.kit.stage import stage_page
from reach_around.site.build import build_site
from reach_around.site.kit import GATED_FILE

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
    "ASsJTYQAAAAASUVORK5CYII="
)


def build_pages(tmp_path: Path, kit: dict[str, object]) -> tuple[str, str]:
    instance = tmp_path / "instance"
    (instance / "media").mkdir(parents=True)
    (instance / "media" / "kit-photo.png").write_bytes(PNG)
    for index in range(50):
        (instance / "media" / f"brand-{index}.png").write_bytes(PNG)
    out, gated = tmp_path / "public", tmp_path / "gated"
    build_site(
        make_config(kit),
        out,
        today=TODAY,
        gated_out=gated,
        assets_dir=instance,
    )
    return (
        (out / "kit" / "index.html").read_text(encoding="utf-8"),
        (gated / GATED_FILE).read_text(encoding="utf-8"),
    )


def partners(count: int = 8) -> list[dict[str, str]]:
    return [
        {
            "name": f"Synthetic Brand {index + 1}",
            "logo": f"media/brand-{index}.png",
            "url": f"https://example.com/brand-{index}",
        }
        for index in range(count)
    ]


def kit_with_partners(count: int, *, photo: str | None = None) -> dict[str, object]:
    kit = gated_doc()
    kit["partners"] = partners(count)
    if photo is not None:
        kit["photo"] = photo
    return kit


def test_photo_is_in_the_public_and_gated_header_blocks(tmp_path: Path) -> None:
    public, gated = build_pages(
        tmp_path,
        kit_with_partners(1, photo="media/kit-photo.png"),
    )
    for page in (public, gated):
        header = page[page.index('class="kit-head') : page.index("Audience numbers")]
        match = re.search(
            r'<img class="kit-photo" src="([^"]+)" alt="" width="1200" '
            r'height="675">',
            header,
        )
        assert match is not None
        assert re.fullmatch(r"(?:\.\./|/)?media/kit-photo\.[0-9a-f]{20}\.png", match[1])
        assert header.index("kit-photo") < header.index('id="kit-title"')


def test_partner_logos_are_a_linked_grid_with_overflow_under_show_more(
    tmp_path: Path,
) -> None:
    _, gated = build_pages(tmp_path, kit_with_partners(8))
    section = gated[gated.index('class="kit-partners"') : gated.index("kit-cases")]
    assert 'class="kit-list kit-partner-grid"' in section
    assert section.count('class="kit-partner-logo"') == 8
    assert section.count('rel="noopener noreferrer"') == 8
    disclosure = section[section.index('<details class="kit-partners-more"') :]
    assert disclosure.startswith(
        '<details class="kit-partners-more">\n<summary>Show more</summary>'
    )
    for index in range(6):
        assert section.index(f"Synthetic Brand {index + 1}") < section.index(
            '<details class="kit-partners-more"'
        )


def test_six_partner_logos_need_no_show_more(tmp_path: Path) -> None:
    _, gated = build_pages(tmp_path, kit_with_partners(6))
    section = gated[gated.index('class="kit-partners"') : gated.index("kit-cases")]
    assert 'class="kit-list kit-partner-grid"' in section
    assert "Show more" not in section
    assert "kit-partners-more" not in section


def test_partner_logo_bytes_stay_out_of_public_tree(tmp_path: Path) -> None:
    public, gated = build_pages(tmp_path, kit_with_partners(1))
    out = tmp_path / "public"
    public_bytes = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())

    assert PNG not in public_bytes
    assert "data:image/png;base64," not in public
    assert f"data:image/png;base64,{base64.b64encode(PNG).decode()}" in gated
    assert not list(out.rglob("brand-0*"))


def test_public_and_gated_kit_images_are_embedded_for_pdf(tmp_path: Path) -> None:
    build_pages(tmp_path, kit_with_partners(8, photo="media/kit-photo.png"))
    site, gated = tmp_path / "public", tmp_path / "gated"

    public_pdf_html = stage_page(site, site, public=True).html
    gated_pdf_html = stage_page(gated, site).html

    assert public_pdf_html.count("data:image/png;base64,") == 1
    assert gated_pdf_html.count("data:image/png;base64,") == 9
