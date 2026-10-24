# SPDX-License-Identifier: Apache-2.0
"""Validation tests for the site config models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.config import ConfigError, SiteConfig, load_site_config

EXAMPLE = Path(__file__).parents[2] / "examples" / "instance" / "site.yaml"


def good() -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example Creator", "socials": []},
        "tabs": [
            {
                "id": "main",
                "title": "Main",
                "blocks": [
                    {"id": "first", "title": "First", "url": "https://example.com/"}
                ],
            }
        ],
    }


def load(tmp_path: Path, data: object) -> SiteConfig:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_site_config(path)


def refused(tmp_path: Path, data: object) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        load(tmp_path, data)
    return info.value


def first_link(data: dict[str, Any]) -> dict[str, Any]:
    link: dict[str, Any] = data["tabs"][0]["blocks"][0]
    return link


def test_good_config_loads(tmp_path: Path) -> None:
    config = load(tmp_path, good())
    assert config.timezone == "America/New_York"
    assert config.tabs[0].blocks[0].id == "first"


def test_example_config_loads() -> None:
    config = load_site_config(EXAMPLE)
    assert config.creator.name == "Example Creator"
    assert len(config.tabs) == 2


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,x", "http://example.com/", "//example.com"],
)
def test_https_urls_only(tmp_path: Path, url: str) -> None:
    data = good()
    first_link(data)["url"] = url
    assert "https" in refused(tmp_path, data).message


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/",
        "https://user@example.com/",
        "https://example.com/a\nb",
        "https://example.com/a b",
        "https://example.com/\x00",
        "https:///path",
    ],
)
def test_url_with_userinfo_or_control_characters_refused(
    tmp_path: Path, url: str
) -> None:
    data = good()
    first_link(data)["url"] = url
    assert "blocks[0].url" in refused(tmp_path, data).message


@pytest.mark.parametrize("bad", ["Has Space", "../x", "", "-x", "UP", "a" * 41])
def test_ids_are_slugs(tmp_path: Path, bad: str) -> None:
    data = good()
    first_link(data)["id"] = bad
    assert "blocks[0].id" in refused(tmp_path, data).message


def test_slug_of_forty_characters_is_allowed(tmp_path: Path) -> None:
    data = good()
    first_link(data)["id"] = "a" * 40
    assert load(tmp_path, data).tabs[0].blocks[0].id == "a" * 40


def test_ids_are_unique_across_the_whole_site(tmp_path: Path) -> None:
    data = good()
    # A link inside a section reuses the tab id.
    data["tabs"][0]["blocks"].append(
        {
            "id": "group",
            "title": "Group",
            "links": [{"id": "main", "title": "Dup", "url": "https://example.com/d"}],
        }
    )
    error = refused(tmp_path, data)
    assert "duplicate id" in error.message
    assert "main" in error.message


def test_ids_unique_between_tabs(tmp_path: Path) -> None:
    data = good()
    data["tabs"].append(
        {
            "id": "other",
            "title": "Other",
            "blocks": [{"id": "first", "title": "X", "url": "https://example.com/x"}],
        }
    )
    assert "duplicate id" in refused(tmp_path, data).message


def test_show_until_not_before_show_from(tmp_path: Path) -> None:
    data = good()
    first_link(data).update(show_from="2027-02-02", show_until="2027-02-01")
    assert "show_until" in refused(tmp_path, data).message


def test_same_day_window_is_allowed(tmp_path: Path) -> None:
    data = good()
    first_link(data).update(show_from="2027-02-02", show_until="2027-02-02")
    assert load(tmp_path, data).tabs[0].blocks[0].id == "first"


def test_at_least_one_tab(tmp_path: Path) -> None:
    data = good()
    data["tabs"] = []
    assert "tabs" in refused(tmp_path, data).message


def test_unknown_keys_refused(tmp_path: Path) -> None:
    data = good()
    data["surprise"] = 1
    assert "surprise" in refused(tmp_path, data).message
    data = good()
    first_link(data)["surprise"] = 1
    assert "surprise" in refused(tmp_path, data).message


@pytest.mark.parametrize("zone", ["Mars/Base", "", "../etc/passwd", "America"])
def test_unknown_timezone_refused(tmp_path: Path, zone: str) -> None:
    data = good()
    data["timezone"] = zone
    assert "timezone" in refused(tmp_path, data).message


def test_title_length_is_bounded(tmp_path: Path) -> None:
    data = good()
    first_link(data)["title"] = "t" * 201
    assert "title" in refused(tmp_path, data).message
    data = good()
    first_link(data)["subtitle"] = "s" * 501
    assert "subtitle" in refused(tmp_path, data).message
    data = good()
    first_link(data).update(title="t" * 200, subtitle="s" * 500)
    load(tmp_path, data)


@pytest.mark.parametrize(
    "value", ["2027-02-01T10:00:00", "02/01/2027", 20270201, "2027-2-1"]
)
def test_dates_must_be_iso_dates_not_datetimes(tmp_path: Path, value: object) -> None:
    data = good()
    first_link(data)["show_from"] = value
    assert "show_from" in refused(tmp_path, data).message


def test_unquoted_yaml_timestamp_refused(tmp_path: Path) -> None:
    path = tmp_path / "ts.yaml"
    path.write_text(
        "version: 1\n"
        "creator: {name: X, socials: []}\n"
        "tabs:\n"
        "  - id: main\n"
        "    title: Main\n"
        "    blocks:\n"
        "      - id: first\n"
        "        title: First\n"
        "        url: https://example.com/\n"
        "        show_from: 2027-02-01 10:00:00\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as info:
        load_site_config(path)
    assert "show_from" in info.value.message


def test_error_message_names_the_dotted_key_path(tmp_path: Path) -> None:
    data = good()
    data["tabs"][0]["blocks"].append(
        {"id": "second", "title": "Second", "url": "ftp://example.com/"}
    )
    error = refused(tmp_path, data)
    assert "tabs[0].blocks[1].url" in error.message
    assert error.path.endswith("site.yaml")


def test_section_link_error_path(tmp_path: Path) -> None:
    data = good()
    data["tabs"][0]["blocks"].append(
        {
            "id": "group",
            "title": "Group",
            "links": [{"id": "inner", "title": "In", "url": "javascript:x"}],
        }
    )
    assert "tabs[0].blocks[1].links[0].url" in refused(tmp_path, data).message


def test_non_mapping_root_refused(tmp_path: Path) -> None:
    assert refused(tmp_path, ["a"]).message


def test_models_are_frozen(tmp_path: Path) -> None:
    config = load(tmp_path, good())
    field = "timezone"
    with pytest.raises(ValueError, match="frozen"):
        setattr(config, field, "UTC")


def test_html_in_text_is_kept_verbatim_for_the_template_to_escape(
    tmp_path: Path,
) -> None:
    data = good()
    text = '<script>alert(1)</script> "q" café'
    first_link(data)["title"] = text
    assert load(tmp_path, data).tabs[0].blocks[0].title == text
