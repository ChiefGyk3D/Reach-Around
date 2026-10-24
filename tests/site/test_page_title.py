# SPDX-License-Identifier: Apache-2.0
"""Page titles: creator.title, tab page_title and site title_template."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
HOSTILE = "<script>alert(1)</script>"


def config(
    creator: dict[str, Any] | None = None,
    tab_extra: dict[str, Any] | None = None,
    **extra: Any,
) -> SiteConfig:
    def tab(ident: str, title: str, more: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "id": ident,
            "title": title,
            "blocks": [{"id": f"l-{ident}", "title": "L", "url": "https://e.com/"}],
            **(more or {}),
        }

    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator", **(creator or {})},
        "tabs": [tab("start", "Start", tab_extra), tab("more", "More", None)],
        **extra,
    }
    return SiteConfig.model_validate(data)


def titles(cfg: SiteConfig, tmp_path: Path) -> dict[str, str]:
    build_site(cfg, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    out = {}
    for name in ("index.html", "more/index.html", "privacy/index.html"):
        html = (tmp_path / name).read_text(encoding="utf-8")
        match = re.search(r"<title>(.*?)</title>", html)
        assert match, name
        out[name] = match.group(1)
    return out


def test_default_title_unchanged(tmp_path: Path) -> None:
    assert titles(config(), tmp_path) == {
        "index.html": "Start - Example Creator",
        "more/index.html": "More - Example Creator",
        "privacy/index.html": "Privacy - Example Creator",
    }


def test_creator_title_alone_on_home_and_suffix_elsewhere(tmp_path: Path) -> None:
    got = titles(config({"title": "Example Creator Links"}), tmp_path)
    assert got["index.html"] == "Example Creator Links"
    assert got["more/index.html"] == "More - Example Creator Links"
    assert got["privacy/index.html"] == "Privacy - Example Creator Links"


def test_template_all_placeholders(tmp_path: Path) -> None:
    cfg = config(
        {"title": "Links"}, title_template="{creator} | {tab} | {site} | {tab}"
    )
    got = titles(cfg, tmp_path)
    assert got["index.html"] == "Example Creator | Start | Links | Start"
    assert got["more/index.html"] == "Example Creator | More | Links | More"


def test_template_wins_over_creator_title_rule(tmp_path: Path) -> None:
    got = titles(config({"title": "Links"}, title_template="{tab}"), tmp_path)
    assert got["index.html"] == "Start"


def test_site_falls_back_to_creator_name(tmp_path: Path) -> None:
    got = titles(config(title_template="{site}: {tab}"), tmp_path)
    assert got["more/index.html"] == "Example Creator: More"


def test_tab_page_title_replaces_template_output(tmp_path: Path) -> None:
    cfg = config(
        {"title": "Links"},
        tab_extra={"page_title": "Welcome"},
        title_template="{tab} - {site}",
    )
    got = titles(cfg, tmp_path)
    assert got["index.html"] == "Welcome"
    assert got["more/index.html"] == "More - Links"


def test_tab_page_title_replaces_default(tmp_path: Path) -> None:
    got = titles(config(tab_extra={"page_title": "Welcome"}), tmp_path)
    assert got["index.html"] == "Welcome"


def test_substituted_values_are_not_expanded_again(tmp_path: Path) -> None:
    cfg = config({"name": "{tab}{site}"}, title_template="{creator}/{tab}")
    assert titles(cfg, tmp_path)["index.html"] == "{tab}{site}/Start"


@pytest.mark.parametrize("where", ["creator", "tab", "creator_name"])
def test_hostile_title_is_escaped(where: str, tmp_path: Path) -> None:
    if where == "creator":
        cfg = config({"title": HOSTILE})
    elif where == "tab":
        cfg = config(tab_extra={"page_title": HOSTILE})
    else:
        cfg = config({"name": HOSTILE}, title_template="{creator}")
    build_site(cfg, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html.split("</title>")[0]


def test_title_never_reaches_css_or_js(tmp_path: Path) -> None:
    cfg = config({"title": "Zq9Title"}, tab_extra={"page_title": "Zq9Tab"})
    build_site(cfg, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    for path in tmp_path.rglob("*"):
        if path.suffix in {".css", ".js"}:
            text = path.read_text(encoding="utf-8")
            assert "Zq9" not in text


def test_og_title_follows_title_when_present(tmp_path: Path) -> None:
    # The base template emits no og tags today; if one is added it must carry
    # the same value as <title>.
    cfg = config({"title": "Links"})
    build_site(cfg, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    for page in tmp_path.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", html)
        assert title
        for og in re.findall(r'property="og:title" content="(.*?)"', html):
            assert og == title.group(1)


CASES: list[tuple[str, Any]] = [
    ("empty", ""),
    ("spaces", "   "),
    ("leading", " x"),
    ("trailing", "x "),
    ("newline", "a\nb"),
    ("tab-char", "a\tb"),
    ("cr", "a\rb"),
    ("nul", "a\x00b"),
    ("bidi", "a‮b"),
    ("line-sep", "a b"),
    ("int", 5),
    ("bool", True),
    ("float-nan", float("nan")),
    ("list", ["a"]),
]


@pytest.mark.parametrize(("label", "value"), CASES, ids=[c[0] for c in CASES])
def test_plain_title_refusals(label: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        config({"title": value})
    with pytest.raises(ValidationError):
        config(tab_extra={"page_title": value})
    with pytest.raises(ValidationError):
        config(title_template=value)


def test_title_cap_is_80_inclusive() -> None:
    config({"title": "x" * 80}, tab_extra={"page_title": "y" * 80})
    with pytest.raises(ValidationError):
        config({"title": "x" * 81})
    with pytest.raises(ValidationError):
        config(tab_extra={"page_title": "y" * 81})


def test_template_cap_is_200_inclusive() -> None:
    config(title_template="x" * 200)
    with pytest.raises(ValidationError):
        config(title_template="x" * 201)


@pytest.mark.parametrize(
    "template",
    ["{nope}", "{tab", "tab}", "{}", "{0}", "{tab.x}", "{tab!r}", "{{tab}}", "{TAB}"],
)
def test_unknown_placeholders_refused(template: str) -> None:
    with pytest.raises(ValidationError, match="only the placeholders"):
        config(title_template=template)


def test_load_error_names_the_key(tmp_path: Path) -> None:
    from reach_around.config.loader import ConfigError

    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\ntitle_template: '{bogus} x'\ncreator:\n  name: A\n"
        "tabs:\n  - id: a\n    title: A\n    blocks: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="title_template"):
        load_site_config(path)
    path.write_text(
        "version: 1\ncreator:\n  name: A\n  title: ''\n"
        "tabs:\n  - id: a\n    title: A\n    blocks: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"creator\.title"):
        load_site_config(path)
