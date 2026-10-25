# SPDX-License-Identifier: Apache-2.0
"""robots.txt rendering, the crawler data file, and the noindex headers."""

from __future__ import annotations

from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig
from reach_around.config.robots import TOKEN, Robots
from reach_around.site.build import build_site
from reach_around.site.overrides import OverridesError, render_headers
from reach_around.site.robots import (
    ai_crawler_tokens,
    parse_crawler_list,
    render_robots,
)

TODAY = date(2027, 3, 10)


def site_data() -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "s",
                "title": "S",
                "blocks": [{"id": "a", "title": "A", "url": "https://example.com/"}],
            }
        ],
    }


def cfg(**robots: Any) -> SiteConfig:
    return SiteConfig.model_validate({**site_data(), "robots": robots})


def groups(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("User-agent: "):
            current = line.removeprefix("User-agent: ")
            out[current] = []
        elif line and not line.startswith("#"):
            assert current is not None
            out[current].append(line)
    return out


def test_default_allows_everything_with_advisory_header() -> None:
    text = render_robots(Robots())
    assert text.startswith("#")
    assert "advisory" in text.lower()
    assert "not access control" in text.lower()
    assert groups(text) == {"*": ["Allow: /"]}


def test_allow_all_false_disallows_everything() -> None:
    assert groups(render_robots(Robots(allow_all=False))) == {"*": ["Disallow: /"]}


def test_block_list_sorted_and_deterministic() -> None:
    text = render_robots(Robots(block=["Zed", "alpha", "Mid"]))
    assert list(groups(text)) == ["*", "alpha", "Mid", "Zed"]
    assert groups(text)["Zed"] == ["Disallow: /"]
    assert text == render_robots(Robots(block=["Mid", "Zed", "alpha"]))


def test_output_is_ascii_and_newline_terminated() -> None:
    text = render_robots(Robots(block=["Bot"], ai_crawlers="block"))
    text.encode("ascii")
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text


def test_ai_crawlers_allow_lists_none() -> None:
    out = groups(render_robots(Robots(ai_crawlers="allow")))
    assert set(out) == {"*"}


def test_ai_crawlers_block_lists_every_token() -> None:
    tokens = ai_crawler_tokens()
    assert len(tokens) >= 8
    out = groups(render_robots(Robots(ai_crawlers="block")))
    assert set(out) == {"*", *tokens}
    assert all(out[t] == ["Disallow: /"] for t in tokens)


def test_ai_block_merges_with_block_without_duplicates() -> None:
    mixed = ai_crawler_tokens()[0].swapcase()
    text = render_robots(Robots(ai_crawlers="block", block=[mixed, "Extra"]))
    names = [line for line in text.splitlines() if line.startswith("User-agent: ")]
    lowered = [n.lower() for n in names]
    assert len(lowered) == len(set(lowered))
    assert "User-agent: Extra" in names


def test_search_engines_are_not_in_the_ai_list() -> None:
    lowered = {t.lower() for t in ai_crawler_tokens()}
    assert not lowered & {"googlebot", "bingbot", "applebot", "duckduckbot"}


# --- data file ---------------------------------------------------------------


def data_lines() -> list[str]:
    path = resources.files("reach_around.site") / "data" / "ai_crawlers.txt"
    return path.read_text(encoding="ascii").splitlines()


def test_data_file_has_header_sources_and_warning() -> None:
    lines = data_lines()
    head = "\n".join(line for line in lines if line.startswith("#"))
    assert lines[0].startswith("#")
    assert "verify before relying" in head.lower()
    assert head.count("https://") >= 5


def test_data_file_tokens_valid_and_unique() -> None:
    tokens = [
        line for line in data_lines() if line.strip() and not line.startswith("#")
    ]
    assert len(tokens) >= 8  # collected loosely, asserted strictly below
    for agent in tokens:
        assert TOKEN.fullmatch(agent), agent
        assert agent != "*"
    lowered = [t.lower() for t in tokens]
    assert len(lowered) == len(set(lowered))
    assert tokens == ai_crawler_tokens()


@pytest.mark.parametrize(
    "bad", ["x\rAllow: /", "a b", "*", "ok # trailing", "café", " lead", "a:b"]
)
def test_parser_refuses_hostile_data_lines(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_crawler_list(f"# header\nGoodBot\n{bad}\n")


def test_parser_refuses_duplicates_and_empty() -> None:
    with pytest.raises(ValueError):
        parse_crawler_list("A\na\n")
    with pytest.raises(ValueError):
        parse_crawler_list("# only comments\n")


# --- headers -----------------------------------------------------------------


def test_headers_unchanged_without_noindex() -> None:
    assert "X-Robots-Tag" not in render_headers()
    assert render_headers() == render_headers(noindex_paths=[])


def test_noindex_header_blocks() -> None:
    text = render_headers(noindex_paths=["/private", "/z/"])
    assert "\n/private\n  X-Robots-Tag: noindex\n" in text
    assert "\n/private/*\n  X-Robots-Tag: noindex\n" in text
    assert "\n/z\n  X-Robots-Tag: noindex\n" in text
    assert "\n/z/*\n  X-Robots-Tag: noindex\n" in text
    assert text.count("X-Robots-Tag") == 4
    assert text == render_headers(noindex_paths=["/z/", "/private"])


@pytest.mark.parametrize(
    "path",
    ["/a\nX-Evil: 1", "/a\r\nSet-Cookie: x=1", "/*", "/", "", "x", "/a b", "/a\x00"],
)
def test_noindex_injection_refused_by_the_generator(path: str) -> None:
    with pytest.raises(OverridesError):
        render_headers(noindex_paths=[path])


# --- build -------------------------------------------------------------------


def files(out: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()
    }


def test_absent_key_builds_no_robots(tmp_path: Path) -> None:
    config = SiteConfig.model_validate(site_data())
    build_site(config, tmp_path / "o", today=TODAY)
    built = files(tmp_path / "o")
    assert "robots.txt" not in built
    assert b"X-Robots-Tag" not in built["_headers"]


def test_key_present_adds_only_robots_txt(tmp_path: Path) -> None:
    build_site(SiteConfig.model_validate(site_data()), tmp_path / "a", today=TODAY)
    build_site(cfg(block=["Bot"]), tmp_path / "b", today=TODAY)
    a, b = files(tmp_path / "a"), files(tmp_path / "b")
    assert set(b) - set(a) == {"robots.txt"}
    # the only other difference: robots.txt gets its revalidate rule in _headers
    rule = b"\n/robots.txt\n  Cache-Control: public, max-age=0, must-revalidate\n"
    assert b["_headers"].count(rule) == 1
    b["_headers"] = b["_headers"].replace(rule, b"")
    assert {k: v for k, v in b.items() if k != "robots.txt"} == a
    assert b["robots.txt"] == render_robots(Robots(block=["Bot"])).encode("ascii")


def test_noindex_paths_reach_built_headers(tmp_path: Path) -> None:
    build_site(cfg(noindex_paths=["/thanks"]), tmp_path / "o", today=TODAY)
    text = (tmp_path / "o" / "_headers").read_text()
    assert "/thanks/*\n  X-Robots-Tag: noindex" in text


def test_robots_build_is_deterministic(tmp_path: Path) -> None:
    config = cfg(block=["B", "A"], ai_crawlers="block", noindex_paths=["/x", "/y"])
    build_site(config, tmp_path / "1", today=TODAY)
    build_site(config, tmp_path / "2", today=TODAY)
    assert files(tmp_path / "1") == files(tmp_path / "2")
