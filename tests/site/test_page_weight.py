# SPDX-License-Identifier: Apache-2.0
"""Page-weight budget (#670): every built page is inside it, none loads from a
third-party origin, and docs/performance.md carries the same numbers."""

from __future__ import annotations

import copy
import re
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from pageweight import (
    BUDGET,
    DOCS_HEADING,
    FIELDS,
    GATE_ORIGINS,
    PAGE_TYPES,
    STACK_SITES,
    docs_differences,
    docs_table,
    measure,
    over_budget,
    page_type,
    resources,
    third_party,
)
from test_kit_build import gated_doc, make_config

from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
DOC = ROOT / "docs" / "performance.md"


def stack_doc(badges: bool) -> dict[str, object]:
    """The example instance's creator and theme, as a smart-stack home page."""
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
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
        icons = {"watch": ("twitch", "kick")}.get(intent, ("link", "link"))
        groups.append(
            {
                "id": intent,
                "title": title,
                "intent": intent,
                "blocks": [
                    {
                        "id": f"{intent}-{n}",
                        "title": f"{title} link {n}",
                        "url": f"https://example.com/{intent}/{n}",
                        "icon": icons[n - 1],
                    }
                    for n in (1, 2)
                ],
            }
        )
    out: dict[str, object] = copy.deepcopy(doc)
    out["tabs"] = groups
    out["page"] = {
        "layout": "stack",
        "status": {"text": "Live Friday at 8 PM Eastern", "schedule": "Next: Nov 13"},
        "watch": ["watch-1", "watch-2"],
        "live_badges": badges,
    }
    out["features"] = {"click_tracking": True}
    return out


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
    for name, badges in (("stack", False), ("stack-live", True)):
        build_site(
            SiteConfig.model_validate(stack_doc(badges)),
            base / name,
            today=TODAY,
            assets_dir=EXAMPLE.parent,
        )
    return [base / "a", base / "k", base / "stack", base / "stack-live"]


def all_pages(sites: list[Path]) -> list[tuple[Path, Path]]:
    pairs = [
        (root, p)
        for root in sites
        for p in sorted(root.rglob("*.html"))
        if root.name not in STACK_SITES or p == root / "index.html"
    ]
    assert len(pairs) >= 8, "the golden example pages were not all built"
    kinds = {page_type(str(p.relative_to(root)), root.name) for root, p in pairs}
    assert kinds == set(PAGE_TYPES), kinds
    return pairs


def test_every_page_is_within_budget(sites: list[Path]) -> None:
    for root, page in all_pages(sites):
        rel = str(page.relative_to(root))
        kind = page_type(rel, root.name)
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


def test_the_stack_fixtures_are_what_their_types_say(sites: list[Path]) -> None:
    plain = (sites[2] / "index.html").read_text(encoding="utf-8")
    live = (sites[3] / "index.html").read_text(encoding="utf-8")
    assert "smart-stack" in plain and "smart-stack" in live
    assert "data-watch-badge" not in plain
    assert live.count("data-watch-badge") == 2
    assert (
        measure(sites[3], sites[3] / "index.html")["js"]
        > measure(sites[2], sites[2] / "index.html")["js"]
    ), "the badge page loads the live script"


@pytest.mark.parametrize("which", [2, 3], ids=["hub-stack", "hub-live"])
def test_padded_stack_page_fails_its_own_budget(
    sites: list[Path], tmp_path: Path, which: int
) -> None:
    kind = page_type("index.html", sites[which].name)
    root = tmp_path / sites[which].name
    shutil.copytree(sites[which], root)
    page = root / "index.html"
    assert not over_budget(kind, measure(root, page))
    page.write_bytes(page.read_bytes() + b"<!--" + b"x" * BUDGET[kind]["html"] + b"-->")
    assert any(m.startswith("html:") for m in over_budget(kind, measure(root, page)))
    # the ordinary hub budget did not move to make room for either
    assert BUDGET["hub"]["js"] == 13000 and BUDGET["hub"]["html"] == 5200


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
        kind = page_type(str(page.relative_to(root)), root.name)
        for f, v in measure(root, page).items():
            biggest[kind][f] = max(biggest[kind][f], v)
    for kind in PAGE_TYPES:
        for f in FIELDS:
            assert BUDGET[kind][f] <= biggest[kind][f] * 1.5, (kind, f)


def _copy_with_head(sites: list[Path], tmp_path: Path, head: str) -> tuple[Path, Path]:
    root = tmp_path / "copy"
    shutil.copytree(sites[0], root)
    page = root / "index.html"
    page.write_text(page.read_text().replace("</head>", head + "</head>"))
    return root, page


def test_every_supported_font_extension_counts_as_fonts(
    sites: list[Path], tmp_path: Path
) -> None:
    root = tmp_path / "copy"
    shutil.copytree(sites[0], root)
    before = measure(root, root / "index.html")
    links = ""
    for name, size in (("a.woff2", 11), ("b.woff", 22), ("c.otf", 33), ("d.ttf", 44)):
        (root / "static" / name).write_bytes(b"f" * size)
        links += f'<link rel="preload" href="static/{name}" as="font" crossorigin>'
    page = root / "index.html"
    page.write_text(page.read_text().replace("</head>", links + "</head>"))
    after = measure(root, page)
    assert after["fonts"] - before["fonts"] == 11 + 22 + 33 + 44
    assert after["images"] == before["images"]


def test_unknown_extension_fails_loudly(sites: list[Path], tmp_path: Path) -> None:
    root, page = _copy_with_head(
        sites, tmp_path, '<link rel="preload" href="static/x.xyz" as="fetch">'
    )
    (root / "static" / "x.xyz").write_bytes(b"x")
    with pytest.raises(ValueError, match=r"\.xyz"):
        measure(root, page)


def _css_site(sites: list[Path], tmp_path: Path, files: dict[str, str]) -> Path:
    root, page = _copy_with_head(
        sites, tmp_path, '<link rel="stylesheet" href="static/entry.css">'
    )
    for name, text in files.items():
        (root / "static" / name).write_text(text)
    return page


def test_third_party_in_an_imported_stylesheet_is_found(
    sites: list[Path], tmp_path: Path
) -> None:
    for inner in (
        "a{background:url(https://cdn.example.net/a.png)}",
        '@import "https://cdn.example.net/a.css";',
    ):
        page = _css_site(
            sites,
            tmp_path / str(abs(hash(inner))),
            {"entry.css": '@import "inner.css";', "inner.css": inner},
        )
        assert third_party(page.parent, page), inner
    # a url() to a same-origin stylesheet is followed too, two levels deep
    page = _css_site(
        sites,
        tmp_path / "deep",
        {
            "entry.css": "a{background:url(mid.css)}",
            "mid.css": "@import url(inner.css);",
            "inner.css": "b{background:url(//cdn.example.net/x.png)}",
        },
    )
    assert third_party(page.parent, page)


def test_stylesheet_import_cycle_terminates(sites: list[Path], tmp_path: Path) -> None:
    page = _css_site(
        sites,
        tmp_path,
        {"entry.css": '@import "other.css";', "other.css": '@import "entry.css";'},
    )
    assert third_party(page.parent, page) == []
    names = {f.name for f in resources(page.parent, page)}
    assert {"entry.css", "other.css"} <= names


def test_clean_import_chain_passes_and_is_counted(
    sites: list[Path], tmp_path: Path
) -> None:
    root = tmp_path / "base"
    shutil.copytree(sites[0], root)
    before = measure(root, root / "index.html")
    page = _css_site(
        sites,
        tmp_path / "chain",
        {"entry.css": '@import "inner.css";', "inner.css": "a{color:red}"},
    )
    assert third_party(page.parent, page) == []
    base = measure(page.parent, page.parent / "index.html")
    inner = (page.parent / "static" / "inner.css").stat().st_size
    entry = (page.parent / "static" / "entry.css").stat().st_size
    assert base["css"] - before["css"] == inner + entry
    assert base["requests"] - before["requests"] == 2
