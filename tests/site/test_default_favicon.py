# SPDX-License-Identifier: Apache-2.0
"""A page with no configured favicon still names one (#363).

Without a link, the browser asks for /favicon.ico, the host answers 404 and
Lighthouse logs a console error. The default icon is a packaged static file, so
the CSP (`img-src 'self' https:`) is untouched.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

ICON = re.compile(r'<link rel="icon" href="([^"]+)"')


def config(creator: dict[str, Any]) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": creator,
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [
                        {"id": "one", "title": "One", "url": "https://example.com/1"}
                    ],
                }
            ],
        }
    )


def test_no_favicon_links_the_packaged_default(tmp_path: Path) -> None:
    out = tmp_path / "public"
    build_site(config({"name": "Example Creator"}), out, today=date(2027, 3, 10))
    for page in ("index.html", "404.html", "privacy/index.html"):
        html = (out / page).read_text(encoding="utf-8")
        match = ICON.search(html)
        assert match, page
        href = match.group(1)
        assert re.search(r"static/favicon\.[0-9a-f]+\.svg$", href), href
        base = out if href.startswith("/") else out / Path(page).parent
        assert (base / href.lstrip("/")).resolve().is_file(), (page, href)


def test_configured_favicon_still_wins(tmp_path: Path) -> None:
    (tmp_path / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">'
        '<rect width="8" height="8"/></svg>',
        encoding="utf-8",
    )
    out = tmp_path / "public"
    build_site(
        config({"name": "Example Creator", "favicon": "icon.svg"}),
        out,
        today=date(2027, 3, 10),
        assets_dir=tmp_path,
    )
    match = ICON.search((out / "index.html").read_text(encoding="utf-8"))
    assert match and "static/favicon" not in match.group(1)
