# SPDX-License-Identifier: Apache-2.0
"""The owner-review example is a complete side-by-side build input."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reach_around.config import load_site_config
from reach_around.site.build import PAGE_VARIANTS, build_variants

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "layout-variants" / "site.yaml"


def test_layout_variant_example_builds_every_review_path(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    out = tmp_path / "review"
    build_variants(
        config,
        out,
        today=date(2027, 3, 10),
        protect=[EXAMPLE],
        assets_dir=EXAMPLE.parent,
    )
    assert [
        layout
        for layout in PAGE_VARIANTS
        if (out / "v" / layout / "index.html").is_file()
    ] == list(PAGE_VARIANTS)
