# SPDX-License-Identifier: Apache-2.0
"""The optional ``redirects.socials`` rule: config matrix and built ``_redirects``."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.config.redirects import SocialsRedirect
from reach_around.site.build import build_site
from reach_around.site.inventory import build_inventory
from reach_around.site.redirects import render_redirects

TODAY = date(2027, 3, 10)


def site_data(tab_id: str = "s") -> dict[str, Any]:
    # The first tab is the home page; the second lives at /<id>/.
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "home",
                "title": "Home",
                "blocks": [{"id": "a", "title": "A", "url": "https://example.com/"}],
            },
            {
                "id": tab_id,
                "title": "S",
                "blocks": [{"id": "b", "title": "B", "url": "https://example.org/"}],
            },
        ],
    }


def cfg(redirects: object, tab_id: str = "s") -> SiteConfig:
    return SiteConfig.model_validate({**site_data(tab_id), "redirects": redirects})


def built(config: SiteConfig, out: Path) -> dict[str, bytes]:
    build_site(config, out, today=TODAY)
    return {
        str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()
    }


def rules(text: str) -> list[list[str]]:
    return [ln.split() for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_absent_key_emits_nothing(tmp_path: Path) -> None:
    files = built(SiteConfig.model_validate(site_data()), tmp_path / "o")
    assert "_redirects" not in files


def test_empty_redirects_emits_nothing(tmp_path: Path) -> None:
    assert "_redirects" not in built(cfg({}), tmp_path / "o")


def test_default_is_301_to_root_for_both_paths(tmp_path: Path) -> None:
    files = built(cfg({"socials": {}}), tmp_path / "o")
    assert rules(files["_redirects"].decode()) == [
        ["/socials", "/", "301"],
        ["/socials/", "/", "301"],
    ]


def test_old_tab_slugs_redirect_to_current_tab_routes(tmp_path: Path) -> None:
    files = built(cfg({"tabs": {"archive": "s", "old-home": "home"}}), tmp_path / "o")
    assert rules(files["_redirects"].decode()) == [
        ["/archive", "/s/", "301"],
        ["/archive/", "/s/", "301"],
        ["/old-home", "/", "301"],
        ["/old-home/", "/", "301"],
    ]


def test_old_tab_slugs_follow_section_routes(tmp_path: Path) -> None:
    config = cfg({"tabs": {"archive": "s"}}).model_copy(
        update={"page_layout": "sections"}
    )
    files = built(config, tmp_path / "o")
    assert rules(files["_redirects"].decode()) == [
        ["/archive", "/#s", "301"],
        ["/archive/", "/#s", "301"],
    ]


def test_static_redirect_limit_counts_socials_and_tab_rules(tmp_path: Path) -> None:
    at_limit = {f"old-{i}": "s" for i in range(999)}
    assert "_redirects" in built(
        cfg({"socials": {}, "tabs": at_limit}), tmp_path / "allowed"
    )

    over_limit = {**at_limit, "old-999": "s"}
    with pytest.raises(ValueError, match=r"2,002.*2,000"):
        build_site(
            cfg({"socials": {}, "tabs": over_limit}),
            tmp_path / "refused",
            today=TODAY,
        )
    assert not (tmp_path / "refused").exists()


def test_old_slug_cannot_hide_a_current_tab(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"redirects\.tabs.*hide.*s"):
        build_site(cfg({"tabs": {"s": "home"}}), tmp_path / "o", today=TODAY)
    assert not (tmp_path / "o").exists()


def test_302_switch(tmp_path: Path) -> None:
    files = built(cfg({"socials": {"status": 302}}), tmp_path / "o")
    got = rules(files["_redirects"].decode())
    assert len(got) == 2
    assert {r[2] for r in got} == {"302"}


def test_target_is_used_and_no_query_is_written(tmp_path: Path) -> None:
    # No query is written on either side, so nothing in the file drops one.
    files = built(cfg({"socials": {"target": "/links/"}}), tmp_path / "o")
    got = rules(files["_redirects"].decode())
    assert len(got) == 2
    assert all(r[1] == "/links/" and "?" not in " ".join(r) for r in got)


def test_other_paths_unaffected(tmp_path: Path) -> None:
    a = built(SiteConfig.model_validate(site_data()), tmp_path / "a")
    b = built(cfg({"socials": {}}), tmp_path / "b")
    assert set(b) - set(a) == {"_redirects"}
    assert {k: v for k, v in b.items() if k != "_redirects"} == a
    sources = {r[0] for r in rules(b["_redirects"].decode())}
    assert sources == {"/socials", "/socials/"}


def test_build_is_deterministic(tmp_path: Path) -> None:
    config = cfg({"socials": {"status": 302}})
    assert built(config, tmp_path / "1") == built(config, tmp_path / "2")


def test_redirects_file_is_not_in_the_inventory(tmp_path: Path) -> None:
    out = tmp_path / "o"
    build_site(cfg({"socials": {}}), out, today=TODAY)
    files = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert Path("_redirects") in files
    assert "_redirects" not in build_inventory(files, [])


def test_redirects_file_gets_no_cache_rule(tmp_path: Path) -> None:
    files = built(cfg({"socials": {}}), tmp_path / "o")
    assert b"_redirects" not in files["_headers"]


def test_a_tab_named_socials_is_refused(tmp_path: Path) -> None:
    # The redirect would hide that page; stop rather than publish a dead tab.
    with pytest.raises(ValueError, match="socials"):
        build_site(cfg({"socials": {}}, tab_id="socials"), tmp_path / "o", today=TODAY)


def test_a_tab_named_socials_builds_when_the_option_is_off(tmp_path: Path) -> None:
    config = SiteConfig.model_validate(site_data("socials"))
    assert "_redirects" not in built(config, tmp_path / "o")


@pytest.mark.parametrize(
    "status", [200, 300, 303, 307, 308, 0, -301, "301", 301.0, True, None]
)
def test_hostile_status_is_refused(status: object) -> None:
    with pytest.raises(ValidationError):
        cfg({"socials": {"status": status}})


@pytest.mark.parametrize(
    "target",
    [
        "",
        "x",
        "links",
        "//evil.example",
        "///evil.example",
        "https://evil.example/",
        "javascript:alert(1)",
        "/\\evil.example",
        "/a b",
        "/a\nb",
        "/a\r\n/b 301",
        "/a\tb",
        "/a?x=1",
        "/a#f",
        "/../x",
        "/a/./b",
        "/*",
        "/:splat",
        "/" + "a" * 250,
        "/é",
        "/a\x00",
        None,
        7,
    ],
)
def test_hostile_target_is_refused(target: object) -> None:
    with pytest.raises(ValidationError):
        cfg({"socials": {"target": target}})


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError):
        cfg({"socials": {"extra": 1}})
    with pytest.raises(ValidationError):
        cfg({"other": {}})


def test_render_guard_bites_on_a_bad_target() -> None:
    # Defense in depth: a model built without validation cannot inject a line.
    evil = SocialsRedirect.model_construct(status=301, target="/a\n/x /y 301")
    with pytest.raises(ValueError, match="target"):
        render_redirects(evil)
    odd = SocialsRedirect.model_construct(status=307, target="/")
    with pytest.raises(ValueError, match="status"):
        render_redirects(odd)


@pytest.mark.parametrize("target", ["/socials", "/socials/"])
def test_a_self_target_is_refused(target: str) -> None:
    with pytest.raises(ValidationError):
        SocialsRedirect(target=target)


@pytest.mark.parametrize("target", ["/../x", "/a/./b", "/" + "a" * 200])
def test_render_refuses_an_unvalidated_bad_target(target: str) -> None:
    rule = SocialsRedirect.model_construct(status=301, target=target)
    with pytest.raises(ValueError):
        render_redirects(rule)


@pytest.mark.parametrize("target", ["/socials", "/socials/"])
def test_render_refuses_an_unvalidated_self_target(target: str) -> None:
    rule = SocialsRedirect.model_construct(status=301, target=target)
    with pytest.raises(ValueError):
        render_redirects(rule)
