# SPDX-License-Identifier: Apache-2.0
"""Validation matrix for the optional top-level ``robots`` config."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.robots import Robots


def site(robots: object) -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "main",
                "title": "Main",
                "blocks": [{"id": "a", "title": "A", "url": "https://example.com/"}],
            }
        ],
        "robots": robots,
    }


def test_absent_key_means_none() -> None:
    data = site({})
    del data["robots"]
    assert SiteConfig.model_validate(data).robots is None


def test_defaults() -> None:
    robots = SiteConfig.model_validate(site({})).robots
    assert robots is not None
    assert robots.allow_all is True
    assert robots.block == []
    assert robots.ai_crawlers == "allow"
    assert robots.sitemap is False
    assert robots.noindex_paths == []


def test_good_values() -> None:
    robots = Robots.model_validate(
        {
            "allow_all": False,
            "block": ["BadBot", "x_y.z-1"],
            "ai_crawlers": "block",
            "noindex_paths": ["/private/", "/a/b"],
        }
    )
    assert robots.block == ["BadBot", "x_y.z-1"]


@pytest.mark.parametrize(
    "token",
    [
        "",
        "a b",
        "a\nAllow: /",
        "a\rb",
        "a\x00b",
        "a\tb",
        "bad:token",
        "bad/token",
        "café",
        "Ａbot",
        "a" * 61,
        "*",  # a bare wildcard is allow_all: false, not a block entry
        " Bot",
        "Bot\n",
    ],
)
def test_bad_block_tokens_refused(token: str) -> None:
    with pytest.raises(ValidationError):
        Robots.model_validate({"block": [token]})


def test_token_at_limit_ok() -> None:
    Robots.model_validate({"block": ["a" * 60]})


def test_block_size_cap() -> None:
    Robots.model_validate({"block": [f"bot{i}" for i in range(100)]})
    with pytest.raises(ValidationError):
        Robots.model_validate({"block": [f"bot{i}" for i in range(101)]})


def test_duplicate_tokens_refused_case_insensitively() -> None:
    with pytest.raises(ValidationError):
        Robots.model_validate({"block": ["Bot", "bot"]})


@pytest.mark.parametrize("value", ["deny", "disallow", "", True, None, 1])
def test_ai_crawlers_values(value: object) -> None:
    with pytest.raises(ValidationError):
        Robots.model_validate({"ai_crawlers": value})


@pytest.mark.parametrize("value", ["yes", "true", 1, 0, None])
def test_allow_all_is_a_strict_bool(value: object) -> None:
    with pytest.raises(ValidationError):
        Robots.model_validate({"allow_all": value})


@pytest.mark.parametrize("value", [True, "yes", 1, "false"])
def test_sitemap_true_is_refused(value: object) -> None:
    with pytest.raises(ValidationError):
        Robots.model_validate({"sitemap": value})


def test_extra_rules_and_unknown_keys_refused() -> None:
    for key in ("extra_rules", "crawl_delay"):
        with pytest.raises(ValidationError):
            Robots.model_validate({key: ["Disallow: /x"]})


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "/*",
        "private",
        "/a b",
        "/a\nX-Evil: 1",
        "/a\r\n",
        "/a\x00",
        "/a*",
        "/a:b",
        "/a?x=1",
        "/a#f",
        "/café",
        "/" + "a" * 200,
        "//x",
        "/a/../b",
        "/./a",
    ],
)
def test_bad_noindex_paths_refused(path: str) -> None:
    with pytest.raises(ValidationError):
        Robots.model_validate({"noindex_paths": [path]})


def test_noindex_path_caps_and_duplicates() -> None:
    Robots.model_validate({"noindex_paths": [f"/p{i}" for i in range(50)]})
    with pytest.raises(ValidationError):
        Robots.model_validate({"noindex_paths": [f"/p{i}" for i in range(51)]})
    with pytest.raises(ValidationError):
        Robots.model_validate({"noindex_paths": ["/a", "/a/"]})
