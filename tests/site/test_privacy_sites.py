# SPDX-License-Identifier: Apache-2.0
"""The privacy text covers every stored field and the multi-site facts.

Design: docs/analytics-design.md section 9, items 1 to 12. A guard test here
collects loosely (it reads the Worker migrations), asserts strictly (it fails
if it found nothing) and has a negative case showing it bites.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.analytics import (
    AGGREGATE_KEEP_DAYS,
    DEFAULT_RAW_KEEP_DAYS,
    MAX_OTHER_SITES,
)
from reach_around.site.privacy import collected_items

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "worker" / "migrations"


def make(**analytics: Any) -> SiteConfig:
    data: dict[str, Any] = {
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
        "analytics": {"page_views": True, **analytics},
    }
    return SiteConfig.model_validate(data)


def views_text(config: SiteConfig) -> str:
    items = [i for i in collected_items(config) if "page views" in i.what]
    assert len(items) == 1
    i = items[0]
    return f"{i.what} {i.why} {i.kept}"


# -- every stored column has a plain-words phrase ------------------------------

# One phrase per column of the raw table. "INTERNAL" columns carry no personal
# data and no new fact for the visitor (a row id, the first-of-the-day flag that
# the daily code sets, and the closed reason list, which the text covers as "a
# short reason").
PHRASES: dict[str, str] = {
    "ts": "to the hour",
    "page": "the page",
    "src_kind": "referring site",
    "src": "referring site",
    "country": "country",
    "region": "region",
    "city": "city",
    "browser": "browser",
    "bmajor": "its version",
    "os": "operating system",
    "device": "kind of device",
    "lang": "language",
    "vp": "window width",
    "net": "network type",
    "bot_class": "automated",
    "reason": "short reason",
    "first": "daily code",
    "site": "name of the site",
    "id": "",
}


def view_event_columns(sql_files: list[Path]) -> list[str]:
    """Columns of view_events from CREATE TABLE plus later ADD COLUMN lines."""
    cols: list[str] = []
    for path in sorted(sql_files):
        text = path.read_text()
        create = re.search(r"CREATE TABLE view_events \((.*?)\n\);", text, re.S)
        if create:
            for line in create.group(1).splitlines():
                m = re.match(r"\s+(\w+)\s+(INTEGER|TEXT)\b", line)
                if m:
                    cols.append(m.group(1))
        cols += re.findall(r"ALTER TABLE view_events ADD COLUMN (\w+)\b", text)
    return cols


def undisclosed(columns: list[str], text: str) -> list[str]:
    """Columns with no phrase on file, or whose phrase is missing from the text."""
    missing = []
    for col in columns:
        phrase = PHRASES.get(col)
        if phrase is None or phrase not in text:
            missing.append(col)
    return missing


def test_every_stored_column_is_disclosed_in_plain_words() -> None:
    cols = view_event_columns(list(MIGRATIONS.glob("*.sql")))
    assert len(cols) >= 18, cols  # loose collection: something real was read
    assert "site" in cols  # the column this change adds
    assert undisclosed(cols, views_text(make())) == []


def test_the_guard_bites_a_new_column_with_no_disclosure() -> None:
    cols = view_event_columns(list(MIGRATIONS.glob("*.sql"))) + ["zodiac"]
    assert undisclosed(cols, views_text(make())) == ["zodiac"]
    # And a known column whose words were dropped from the text.
    assert undisclosed(["city"], views_text(make()).replace("city", "town")) == ["city"]


# -- the facts section 9 lists -------------------------------------------------


def test_text_says_the_ip_address_is_never_stored_and_the_raw_ua_is_not() -> None:
    text = views_text(make())
    assert "IP address is never stored" in text
    assert "raw browser identification is not stored" in text
    assert "nothing is stored in your browser" in text


def test_text_names_the_network_type_and_says_no_address_is_sent_out() -> None:
    text = views_text(make())
    assert "network type" in text
    assert "VPN" in text and "hosting provider" in text
    assert "on our own server" in text
    assert "no address is sent to anyone else" in text


def test_text_says_daily_codes_differ_between_sites() -> None:
    text = views_text(make(other_sites=["blog.example.test"]))
    assert "different on each site" in text
    assert "cannot be linked" in text


def test_single_site_text_says_so_and_multi_site_text_names_the_others() -> None:
    assert "counts this site only" in views_text(make())
    text = views_text(make(other_sites=["blog.example.test", "news.example.test"]))
    assert "blog.example.test, news.example.test" in text
    assert "kept separately" in text
    assert "counts this site only" not in text


def test_text_says_small_totals_are_merged_and_no_per_person_deletion() -> None:
    text = views_text(make())
    assert "merged into 'other'" in text
    assert "cannot find and delete the views of one person" in text
    assert "Global Privacy Control" in text and "Do Not Track" in text


def test_retention_numbers_come_from_config() -> None:
    default = views_text(make())
    assert f"deleted after {DEFAULT_RAW_KEEP_DAYS} days" in default
    assert f"up to {AGGREGATE_KEEP_DAYS} days" in default
    text = views_text(make(raw_keep_days=14))
    assert "deleted after 14 days" in text
    assert "deleted after 7 days" not in text
    assert "end of the same day" in text


def test_aggregate_keep_matches_the_worker() -> None:
    src = (ROOT / "worker" / "src" / "viewretention.ts").read_text()
    m = re.search(r"AGGREGATE_KEEP_DAYS = (\d+)", src)
    assert m is not None
    assert int(m.group(1)) == AGGREGATE_KEEP_DAYS
    m2 = re.search(r"DEFAULT_RAW_KEEP_DAYS = (\d+)", src)
    assert m2 is not None and int(m2.group(1)) == DEFAULT_RAW_KEEP_DAYS


def test_text_is_not_present_when_page_views_are_off() -> None:
    config = SiteConfig.model_validate(
        {
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
            "analytics": {"other_sites": ["blog.example.test"], "raw_keep_days": 30},
        }
    )
    text = " ".join(f"{i.what} {i.why} {i.kept}" for i in collected_items(config))
    assert "blog.example.test" not in text
    assert "page views" not in text


# -- hostile config values -----------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://blog.example.test",
        "blog.example.test:8443",
        "blog.example.test/path",
        "Blog.Example.Test",
        "*.example.test",
        "localhost",
        "198.51.100.7",
        "blog.example.test.",
        "../etc/passwd",
        "blog..example.test",
        "-a.example.test",
        "a b.example.test",
        "x@blog.example.test",
        "",
        "blog.example.test\n",
        "<script>.example.test",
        "a" * 64 + ".example.test",
        "a." * 130 + "test",
    ],
)
def test_other_sites_refuses_anything_but_a_plain_host(value: str) -> None:
    with pytest.raises(ValidationError):
        make(other_sites=[value])


def test_other_sites_refuses_duplicates_too_many_and_non_lists() -> None:
    with pytest.raises(ValidationError):
        make(other_sites=["a.example.test", "a.example.test"])
    with pytest.raises(ValidationError):
        make(other_sites=[f"s{i}.example.test" for i in range(MAX_OTHER_SITES + 1)])
    assert (
        len(
            make(
                other_sites=[f"s{i}.example.test" for i in range(MAX_OTHER_SITES)]
            ).analytics.other_sites
        )
        == MAX_OTHER_SITES
    )
    for bad in ("blog.example.test", 5, {"a": 1}, [5], [None]):
        with pytest.raises(ValidationError):
            make(other_sites=bad)


@pytest.mark.parametrize(
    "value", [0, -1, 91, 10**12, 7.5, "7", True, None, float("nan")]
)
def test_raw_keep_days_refuses_out_of_range_and_non_integers(value: Any) -> None:
    with pytest.raises(ValidationError):
        make(raw_keep_days=value)


def test_raw_keep_days_accepts_the_ends() -> None:
    assert make(raw_keep_days=1).analytics.raw_keep_days == 1
    assert make(raw_keep_days=90).analytics.raw_keep_days == 90
