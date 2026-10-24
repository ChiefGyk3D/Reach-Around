# SPDX-License-Identifier: Apache-2.0
"""Source guards for the responsive rules. They run without a browser; the
browser tests in test_responsive.py prove the rules do what they say."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

STATIC = resources.files("reach_around.site") / "static"
SITE = (STATIC / "site.css").read_text()
CRYPTO = (STATIC / "crypto.css").read_text()
DOC = (Path(__file__).resolve().parents[2] / "docs" / "design.md").read_text()


def _queries(css: str) -> list[str]:
    return re.findall(r"@media\s*([^{]+)\{", css)


def test_breakpoints_are_rem_based_and_collected() -> None:
    queries = _queries(SITE)
    assert len(queries) >= 8, "the guard collected too few media queries"
    widths = [q for q in queries if "width" in q]
    assert widths, "no width breakpoints found"
    for query in widths:
        assert "px" not in query, f"pixel breakpoint: {query}"
    assert "(min-width: 48rem)" in queries
    assert "(min-width: 56rem)" in queries
    assert "(min-width: 56rem)" in _queries(CRYPTO)


def test_the_breakpoint_table_is_documented() -> None:
    assert "## Responsive layout" in DOC
    for needle in ("48rem", "56rem", "100rem", "140rem", "--ra-column-desktop"):
        assert needle in DOC, needle


def test_input_motion_print_and_notch_rules_exist() -> None:
    queries = _queries(SITE)
    assert "(hover: hover)" in queries
    assert "(pointer: coarse)" in queries
    assert "print" in queries
    assert any("prefers-reduced-motion" in q for q in queries)
    assert "env(safe-area-inset-left" in SITE and "env(safe-area-inset-bottom" in SITE
    assert "::details-content" in SITE[SITE.index("prefers-reduced-motion") :]
    assert "prefers-color-scheme" not in SITE, "the palette stays light-dark()"


def test_hover_effects_only_live_inside_a_hover_query() -> None:
    outside = re.sub(r"@media \(hover: hover\) \{.*?\n\}", "", SITE, flags=re.S)
    assert ":hover" not in outside
    assert ":hover" in SITE


def test_grid_tracks_never_exceed_the_screen() -> None:
    tracks = re.findall(r"minmax\(([^,]+),", SITE)
    assert tracks and all(t.startswith("min(") or t == "0" for t in tracks), tracks


def test_type_scale_steps_are_fluid_rem_clamps() -> None:
    for step in ("base", "md", "lg", "xl", "2xl"):
        line = re.search(rf"--ra-text-{step}:\s*([^;]+);", SITE)
        assert line is not None, step
        assert re.fullmatch(r"clamp\([\d.]+rem, [^,]+, [\d.]+rem\)", line.group(1)), (
            step,
            line.group(1),
        )


def test_the_guard_bites() -> None:
    assert re.search(r"px", "(min-width: 768px)")
    bad = "a { grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); }"
    tracks = re.findall(r"minmax\(([^,]+),", bad)
    assert not all(t.startswith("min(") for t in tracks)
