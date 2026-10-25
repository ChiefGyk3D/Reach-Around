# SPDX-License-Identifier: Apache-2.0
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.analytics import Analytics


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
    }
    if analytics:
        data["analytics"] = analytics
    return SiteConfig.model_validate(data)


def test_off_by_default_with_the_standard_route() -> None:
    config = make()
    assert config.analytics.page_views is False
    assert config.analytics.route == "/e/v/"


def test_page_views_must_be_a_real_boolean() -> None:
    assert make(page_views=True).analytics.page_views is True
    for bad in ("yes", 1, "true", None):
        with pytest.raises(ValidationError):
            make(page_views=bad)


@pytest.mark.parametrize("route", ["/e/v/", "/v/", "/stats/pv/", "/a.b/c_d~e/"])
def test_same_origin_paths_are_accepted(route: str) -> None:
    assert make(route=route).analytics.route == route


@pytest.mark.parametrize(
    "route",
    [
        "",
        "/",
        "e/v/",
        "/e/v",
        "//evil.test/",
        "https://evil.test/e/v/",
        "http://x/e/v/",
        "/e/v/?a=1",
        "/e/v/#f",
        "/../v/",
        "/./v/",
        "/e//v/",
        "/e/v/ ",
        "/e/v/\n",
        "/e\\v/",
        "/e/%2e%2e/",
        "/" + "a/" * 40,
        "javascript:alert(1)/",
    ],
)
def test_anything_but_a_plain_same_origin_path_is_refused(route: str) -> None:
    with pytest.raises(ValidationError):
        make(route=route)


def test_unknown_keys_are_refused() -> None:
    for key in ("unique_visitors", "geo", "host", "url", "bots"):
        with pytest.raises(ValidationError):
            make(**{key: True})


def test_the_section_has_exactly_two_fields() -> None:
    assert set(Analytics.model_fields) == {"page_views", "route"}
