# SPDX-License-Identifier: Apache-2.0
"""Which built pages carry noindex (#443).

Only the 404 page, the gate retry page and the gated kit page are noindex by
design. Every indexable page (tabs, privacy, the public kit page) must not be,
and no robots rule may reach the output unless the config asks for it.
"""

from __future__ import annotations

from pathlib import Path

from test_kit_build import build

NOINDEX = '<meta name="robots" content="noindex">'


def test_only_the_designed_pages_are_noindex(tmp_path: Path) -> None:
    out, gated = build(tmp_path)
    marked = sorted(
        str(p.relative_to(out)) for p in out.rglob("*.html") if NOINDEX in p.read_text()
    )
    assert marked == ["404.html", "kit/retry/index.html"]
    assert NOINDEX in (gated / "kit.html").read_text()
    for page in ("index.html", "privacy/index.html", "kit/index.html"):
        assert NOINDEX not in (out / page).read_text()


def test_no_robots_output_unless_configured(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    assert not (out / "robots.txt").exists()
    assert "X-Robots-Tag" not in (out / "_headers").read_text()
