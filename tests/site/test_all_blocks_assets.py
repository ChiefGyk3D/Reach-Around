# SPDX-License-Identifier: Apache-2.0
"""One site that uses every conditional asset, checked end to end.

Crypto (copy.js, crypto.css), email (email.js, email.css), platforms
(platforms.css), click tracking (click.js), page-view analytics (view.js), the
footer credit, custom.css, the 404 and privacy pages: each static reference in
each built page must be a hashed file that exists, and the built ``_headers``
must cover every published path with exactly one cache rule.
"""

from __future__ import annotations

import posixpath
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_asset_fingerprints import (
    HASHED_NAME,
    IMMUTABLE,
    REVALIDATE,
    cache_control,
    css_urls,
    html_refs,
    is_hashed,
    published,
    resolve,
    url_variants,
)

from assetpaths import asset, has_asset
from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
LOGICAL = (
    "static/site.css",
    "static/theme.css",
    "static/custom.css",
    "static/crypto.css",
    "static/email.css",
    "static/platforms.css",
    "static/click.js",
    "static/copy.js",
    "static/email.js",
    "static/view.js",
)


def _tabs(name: str) -> list[dict[str, Any]]:
    raw = yaml.safe_load((EXAMPLES / name / "site.yaml").read_text(encoding="utf-8"))
    tabs: list[dict[str, Any]] = raw["tabs"]
    return tabs


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("all")
    instance = base / "instance"
    shutil.copytree(EXAMPLES / "instance", instance)
    shutil.copytree(
        EXAMPLES / "crypto" / "media", instance / "media", dirs_exist_ok=True
    )
    raw = yaml.safe_load((instance / "site.yaml").read_text(encoding="utf-8"))
    raw["tabs"] = [
        *raw["tabs"],
        *_tabs("crypto"),
        *_tabs("email"),
        *_tabs("platforms"),
    ]
    raw["analytics"] = {"page_views": True}
    raw.pop("footer", None)  # the credit is on by default
    config = SiteConfig.model_validate(raw)
    overrides = instance / "overrides"
    overrides.mkdir()
    (overrides / "custom.css").write_text(
        'a.link { background-image: url("../media/mascot.svg"); }\n', encoding="utf-8"
    )
    out = base / "out"
    build_site(config, out, today=TODAY, assets_dir=instance, overrides=overrides)
    return out


def test_the_site_really_uses_every_conditional_asset(site: Path) -> None:
    for logical in LOGICAL:
        assert has_asset(site, logical), logical
    assert "Made with Reach Around" in (site / "index.html").read_text(encoding="utf-8")


def test_every_page_links_its_assets_by_hashed_name_and_they_exist(site: Path) -> None:
    pages = sorted(site.rglob("*.html"))
    assert len(pages) >= 7  # five tabs and more, privacy and 404
    seen: set[str] = set()
    for page in pages:
        rel = page.relative_to(site).as_posix()
        for ref in html_refs(page):
            target = resolve(rel, ref)
            if target is None:
                continue
            assert (site / target).exists(), f"{rel} -> {ref}"
            if target.startswith("static/"):
                assert HASHED_NAME.fullmatch(posixpath.basename(target)), (rel, ref)
                seen.add(target)
    # every conditional asset is linked from at least one page
    assert {asset(site, logical) for logical in LOGICAL} == seen


def test_no_page_names_an_unhashed_static_file_anywhere(site: Path) -> None:
    for page in sorted(site.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        for logical in LOGICAL:
            stem, _, ext = logical.rpartition(".")
            assert f'{stem}.{ext}"' not in text, (page, logical)


def test_every_url_in_every_stylesheet_is_hashed_and_exists(site: Path) -> None:
    sheets = sorted((site / "static").glob("*.css"))
    assert len(sheets) == 6
    seen = 0
    for sheet in sheets:
        rel = sheet.relative_to(site).as_posix()
        for url in css_urls(sheet.read_text(encoding="utf-8")):
            if url.startswith(("data:", "https:")):
                continue
            target = resolve(rel, url)
            assert target is not None
            assert (site / target).is_file(), (rel, url)
            assert HASHED_NAME.fullmatch(posixpath.basename(target)), (rel, url)
            seen += 1
    assert seen > 5


def test_the_404_and_privacy_pages_keep_their_flags_and_hashed_links(
    site: Path,
) -> None:
    not_found = (site / "404.html").read_text(encoding="utf-8")
    assert 'content="noindex"' in not_found
    assert "<script" not in not_found.lower()
    assert f'href="/{asset(site, "static/site.css")}"' in not_found
    privacy = (site / "privacy" / "index.html").read_text(encoding="utf-8")
    assert f'href="../{asset(site, "static/site.css")}"' in privacy
    assert asset(site, "static/view.js") not in privacy
    assert asset(site, "static/copy.js") not in privacy


def test_headers_cover_every_published_path_exactly_once(site: Path) -> None:
    headers = (site / "_headers").read_text(encoding="utf-8")
    checked = 0
    for path in published(site):
        rel = path.relative_to(site).as_posix()
        if rel == "_headers":
            continue
        expected = IMMUTABLE if is_hashed(rel) else REVALIDATE
        for url in url_variants(rel):
            assert cache_control(headers, url) == [expected], url
            checked += 1
    assert checked > 40
