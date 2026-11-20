# SPDX-License-Identifier: Apache-2.0
"""Page-weight budget (#670): every built page is inside it, none loads from a
third-party origin, and docs/performance.md carries the same numbers."""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pageweight import (
    BUDGET,
    DOCS_HEADING,
    FIELDS,
    GATE_ORIGINS,
    PAGE_TYPES,
    docs_differences,
    docs_table,
    measure,
    over_budget,
    page_type,
    third_party,
)
from test_kit_build import gated_doc, make_config

from reach_around.config import load_site_config
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
DOC = ROOT / "docs" / "performance.md"


@pytest.fixture(scope="module")
def sites(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """The example instance, and a kit with no Turnstile key (no widget)."""
    base = tmp_path_factory.mktemp("weight")
    build_site(
        load_site_config(EXAMPLE), base / "a", today=TODAY, assets_dir=EXAMPLE.parent
    )
    doc = gated_doc()
    del doc["turnstile_site_key"]
    build_site(
        make_config(doc),
        base / "k",
        today=TODAY,
        gated_out=base / "kg",
    )
    return [base / "a", base / "k"]


def all_pages(sites: list[Path]) -> list[tuple[Path, Path]]:
    pairs = [(root, p) for root in sites for p in sorted(root.rglob("*.html"))]
    assert len(pairs) >= 8, "the golden example pages were not all built"
    kinds = {page_type(str(p.relative_to(root))) for root, p in pairs}
    assert kinds == set(PAGE_TYPES), kinds
    return pairs


def test_every_page_is_within_budget(sites: list[Path]) -> None:
    for root, page in all_pages(sites):
        rel = str(page.relative_to(root))
        kind = page_type(rel)
        assert not over_budget(kind, measure(root, page)), (
            rel,
            measure(root, page),
        )


def test_padded_page_fails_the_budget(sites: list[Path], tmp_path: Path) -> None:
    root = tmp_path / "copy"
    shutil.copytree(sites[0], root)
    page = root / "index.html"
    assert not over_budget("hub", measure(root, page))
    page.write_bytes(
        page.read_bytes() + b"<!--" + b"x" * BUDGET["hub"]["html"] + b"-->"
    )
    msgs = over_budget("hub", measure(root, page))
    assert any(m.startswith("html:") for m in msgs), msgs


def test_no_page_references_a_third_party_origin(sites: list[Path]) -> None:
    for root, page in all_pages(sites):
        assert third_party(root, page) == [], str(page.relative_to(root))


def test_third_party_check_bites(sites: list[Path], tmp_path: Path) -> None:
    root = tmp_path / "copy"
    shutil.copytree(sites[0], root)
    html = (root / "index.html").read_text()
    for extra in (
        '<script src="https://cdn.example.net/a.js"></script>',
        '<link rel="stylesheet" href="//cdn.example.net/a.css">',
        '<img src="https://img.example.net/a.png" alt="">',
        '<style>@import "https://f.example.net/a.css";</style>',
        "<style>a{background:url(https://f.example.net/a.png)}</style>",
    ):
        page = root / "index.html"
        page.write_text(html.replace("</head>", extra + "</head>"))
        assert third_party(root, page), extra
    # a plain outbound link is navigation, not a request
    page.write_text(
        html.replace("</body>", '<a href="https://example.com/">x</a></body>')
    )
    assert third_party(root, page) == []


def test_only_the_gate_pages_load_turnstile_and_only_that_origin(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    build_site(make_config(), out, today=TODAY, gated_out=tmp_path / "g")
    for page in sorted(out.rglob("*.html")):
        rel = str(page.relative_to(out))
        hosts = {urlsplit(u).hostname for u in third_party(out, page)}
        if rel in ("kit/index.html", "kit/retry/index.html"):
            assert hosts == set(GATE_ORIGINS), rel
        else:
            assert hosts == set(), rel


def test_docs_budget_table_matches_the_constants() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert set(docs_table(text)) == set(PAGE_TYPES)
    assert docs_differences(text) == []


def test_docs_mismatch_is_detected() -> None:
    text = DOC.read_text(encoding="utf-8")
    first = re.search(r"^\| hub \| (\d+) \|", text, re.M)
    assert first, DOCS_HEADING
    bumped = text.replace(first.group(0), f"| hub | {int(first.group(1)) + 1} |", 1)
    assert docs_differences(bumped) == ["hub.html: docs 5201 != 5200"]
    assert docs_differences(text.replace("| support |", "| other |", 1))


def test_budget_headroom_is_bounded(
    sites: list[Path],
) -> None:
    """Headroom is bounded: a budget raised far above the measured pages fails."""
    biggest: dict[str, dict[str, int]] = {k: dict.fromkeys(FIELDS, 0) for k in BUDGET}
    for root, page in all_pages(sites):
        kind = page_type(str(page.relative_to(root)))
        for f, v in measure(root, page).items():
            biggest[kind][f] = max(biggest[kind][f], v)
    for kind in PAGE_TYPES:
        for f in FIELDS:
            assert BUDGET[kind][f] <= biggest[kind][f] * 1.5, (kind, f)
