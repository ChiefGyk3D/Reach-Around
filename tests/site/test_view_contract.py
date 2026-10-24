# SPDX-License-Identifier: Apache-2.0
"""view.js against the merged Worker source (read-only), and the 404 page.

The Worker file is read as text; nothing in worker/ is changed or run. If the
Worker's constants move, these tests fail and the script must follow.
"""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path

import pytest

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
VIEW_TS = ROOT / "worker" / "src" / "view.ts"
TODAY = date(2027, 3, 10)


def worker_source() -> str:
    assert VIEW_TS.is_file(), "worker/src/view.ts is missing"
    return VIEW_TS.read_text(encoding="utf-8")


def script() -> str:
    return (resources.files("reach_around.site") / "static" / "view.js").read_text(
        encoding="utf-8"
    )


def ts_regex(name: str) -> str:
    match = re.search(rf"^const {name} = /(.+)/;$", worker_source(), re.MULTILINE)
    assert match, name
    return match.group(1).replace("\\/", "/")


def ts_int(name: str) -> int:
    match = re.search(rf"const {name} = (\d+);", worker_source())
    assert match, name
    return int(match.group(1))


def test_the_worker_route_is_the_default_route_plus_a_tab_id() -> None:
    route = re.compile(ts_regex("ROUTE"))
    assert route.fullmatch("/e/v/start")
    assert route.fullmatch("/e/v/" + "a" * 40)
    assert not route.fullmatch("/e/v/" + "a" * 41)
    # The page-id rule in the script is the Worker's capture group.
    group = re.search(r"\((\[a-z0-9\]\[a-z0-9-\]\{0,39\})\)", ts_regex("ROUTE"))
    assert group
    assert group.group(1) in script()


def test_the_utm_rule_is_the_workers() -> None:
    assert ts_regex("UTM") == "^[a-z0-9-]{1,30}$"
    assert "/^[a-z0-9-]{1,30}$/" in script()


def test_the_worker_reads_exactly_the_keys_the_script_sends() -> None:
    src = worker_source()
    for key in ("r", "u", "w"):
        assert f'params.get("{key}")' in src
        assert f'params.append("{key}"' in script()
    assert len(re.findall(r"params\.append\(", script())) == 3
    letters = re.search(r'w !== "s" && w !== "m" && w !== "l" && w !== "x"', src)
    assert letters
    sent = set(re.findall(r'return "([smlx])"', script()))
    assert sent == {"s", "m", "l", "x"}


def test_the_longest_possible_body_fits_the_workers_cap() -> None:
    cap = ts_int("MAX_BODY")
    host = ts_int("MAX_HOST_LENGTH")
    assert f"host.length > {host}" in script()
    longest = f"r={'a' * host}&u={'b' * 30}&w=x"
    assert len(longest) <= cap


def test_the_worker_still_checks_the_signals_the_script_honors() -> None:
    src = worker_source()
    assert 'request.headers.get("Sec-GPC")' in src
    assert 'request.headers.get("DNT")' in src
    assert 'request.method !== "POST"' in src
    assert "Content-Length" in src  # sendBeacon with a form body sets it


# --- the 404 page -------------------------------------------------------------


def make(**extra: object) -> SiteConfig:
    data: dict[str, object] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
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
    data.update(extra)
    return SiteConfig.model_validate(data)


@pytest.mark.parametrize("route", ["/e/v/", "/stats/pv/"])
def test_the_404_page_builds_with_analytics_on_and_has_no_beacon(
    tmp_path: Path, route: str
) -> None:
    build_site(
        make(analytics={"page_views": True, "route": route}), tmp_path, today=TODAY
    )
    page = (tmp_path / "404.html").read_text(encoding="utf-8")
    assert "That page does not exist." in page
    assert "view.js" not in page
    assert "<script" not in page.lower()
    assert "data-page-id" not in page and "data-view-route" not in page
    # The tab page does carry it, so the check above can fail.
    assert "view.js" in (tmp_path / "index.html").read_text(encoding="utf-8")
