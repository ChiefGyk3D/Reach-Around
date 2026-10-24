# SPDX-License-Identifier: Apache-2.0
import json
import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import BuildManifest, build_site, manifest_json

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
GOLDEN = Path(__file__).parent / "golden"


def make_config(tabs: list[dict[str, Any]] | None = None, **extra: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": tabs
        or [
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


def pages(out: Path) -> list[Path]:
    return sorted(out.rglob("*.html"))


def read_all(out: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in pages(out))


def snapshot(out: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(out)): p.read_bytes()
        for p in sorted(out.rglob("*"))
        if p.is_file()
    }


def two_tabs() -> list[dict[str, Any]]:
    return [
        {
            "id": "start",
            "title": "Start",
            "blocks": [{"id": "a", "title": "A", "url": "https://example.com/a"}],
        },
        {
            "id": "socials",
            "title": "Socials",
            "blocks": [{"id": "b", "title": "B", "url": "https://example.com/b"}],
        },
    ]


def test_each_tab_has_its_own_page(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(make_config(two_tabs()), out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert (out / "index.html").is_file()
    assert (out / "socials" / "index.html").is_file()
    assert "https://example.com/a" in (out / "index.html").read_text()
    assert "https://example.com/b" in (out / "socials" / "index.html").read_text()
    assert "https://example.com/b" not in (out / "index.html").read_text()


def test_html_in_titles_is_escaped(tmp_path: Path) -> None:
    evil = "<script>alert(1)</script>"
    config = make_config(
        [
            {
                "id": "start",
                "title": evil,
                "blocks": [
                    {
                        "id": "one",
                        "title": evil,
                        "subtitle": evil + '"quote"',
                        "url": "https://example.com/1",
                        "affiliate": True,
                    },
                    {
                        "id": "sec",
                        "title": evil,
                        "subtitle": evil,
                        "links": [
                            {"id": "two", "title": evil, "url": "https://example.com/2"}
                        ],
                    },
                ],
            }
        ],
        creator={"name": evil},
        disclosure=evil,
    )
    build_site(config, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    text = read_all(tmp_path)
    assert "&lt;script&gt;" in text
    # The only script tag is the external click.js; none comes from the config.
    assert text.lower().count("<script") == len(pages(tmp_path))


def test_no_script_in_any_page(tmp_path: Path) -> None:
    build_site(
        load_site_config(EXAMPLE), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    assert pages(tmp_path)
    for page in pages(tmp_path):
        # Exactly one script tag per page: the external, deferred click.js.
        text = page.read_text().lower()
        assert text.count("<script") == 1
        assert '<script src="' in text and 'click.js" defer></script>' in text
        assert 'href=""' not in page.read_text()


def test_affiliate_links_get_rel_sponsored_noopener_and_the_disclosure_is_shown(
    tmp_path: Path,
) -> None:
    config = make_config(
        [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "aff",
                        "title": "Aff",
                        "url": "https://example.com/aff",
                        "affiliate": True,
                    }
                ],
            },
            {
                "id": "plain",
                "title": "Plain",
                "blocks": [{"id": "p", "title": "P", "url": "https://example.com/p"}],
            },
        ],
        disclosure="We may earn a commission.",
    )
    build_site(config, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    first = (tmp_path / "index.html").read_text()
    assert 'rel="sponsored noopener noreferrer"' in first
    assert "We may earn a commission." in first
    other = (tmp_path / "plain" / "index.html").read_text()
    assert "sponsored" not in other
    assert "We may earn a commission." not in other


def test_hidden_affiliate_link_does_not_trigger_the_disclosure(
    tmp_path: Path,
) -> None:
    config = make_config(
        [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "aff",
                        "title": "Aff",
                        "url": "https://example.com/aff",
                        "affiliate": True,
                        "show_until": "2027-03-01",
                    }
                ],
            }
        ],
        disclosure="We may earn a commission.",
    )
    build_site(config, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    assert "commission" not in (tmp_path / "index.html").read_text()


def test_sections_use_details_summary(tmp_path: Path) -> None:
    def section(sid: str, collapsed: bool) -> dict[str, Any]:
        return {
            "id": sid,
            "title": f"Title {sid}",
            "collapsed": collapsed,
            "links": [{"id": sid + "-l", "title": "L", "url": "https://example.com/l"}],
        }

    config = make_config(
        [
            {
                "id": "start",
                "title": "Start",
                "blocks": [section("closed", True), section("opened", False)],
            }
        ]
    )
    build_site(config, tmp_path, today=TODAY, assets_dir=EXAMPLE.parent)
    text = (tmp_path / "index.html").read_text()
    details = re.findall(r"<details[^>]*>", text)
    assert len(details) == 2
    assert sum(" open" in d for d in details) == 1
    assert text.count("<summary") == 2
    assert re.search(r"<details[^>]* open>\s*<summary[^>]*>\s*Title opened", text)


def test_scheduled_link_hidden_by_today(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    build_site(
        config, tmp_path / "before", today=date(2027, 2, 28), assets_dir=EXAMPLE.parent
    )
    build_site(
        config, tmp_path / "during", today=date(2027, 3, 1), assets_dir=EXAMPLE.parent
    )
    assert "tickets.example.invalid" not in read_all(tmp_path / "before")
    assert "tickets.example.invalid" in read_all(tmp_path / "during")


def test_manifest_lists_only_visible_links(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    manifest = build_site(
        config, tmp_path / "a", today=date(2027, 2, 28), assets_dir=EXAMPLE.parent
    )
    assert isinstance(manifest, BuildManifest)
    assert "spring-meetup" not in manifest.links
    assert manifest.links["example-mic"] == "https://shop.example.com/mic?ref=example"
    assert "gear-picks" not in manifest.links  # sections are not links
    assert json.loads(manifest_json(manifest)) == {"links": manifest.links}
    later = build_site(
        config, tmp_path / "b", today=date(2027, 3, 1), assets_dir=EXAMPLE.parent
    )
    assert "spring-meetup" in later.links


def test_build_never_publishes_a_manifest(tmp_path: Path) -> None:
    build_site(
        load_site_config(EXAMPLE), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    names = [p.name for p in tmp_path.rglob("*")]
    assert "index.html" in names  # something was collected
    assert not [n for n in names if "manifest" in n.lower()]
    assert "manifest" not in (tmp_path / "_headers").read_text().lower()


def test_manifest_json_has_sorted_keys(tmp_path: Path) -> None:
    manifest = build_site(
        load_site_config(EXAMPLE), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    raw = manifest_json(manifest)
    ids = list(json.loads(raw)["links"])
    assert ids == sorted(ids)
    assert raw == json.dumps(json.loads(raw), sort_keys=True, indent=2) + "\n"


def test_output_directory_is_cleaned_of_stale_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(make_config(two_tabs()), out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert (out / "socials" / "index.html").is_file()
    (out / "junk.txt").write_text("old")
    build_site(make_config(two_tabs()[:1]), out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert not (out / "socials").exists()
    assert not (out / "junk.txt").exists()
    assert (out / "index.html").is_file()


def test_build_refuses_an_out_dir_that_is_the_config_directory_or_a_parent(
    tmp_path: Path,
) -> None:
    conf_dir = tmp_path / "project" / "conf"
    conf_dir.mkdir(parents=True)
    config_file = conf_dir / "site.yaml"
    config_file.write_text("keep me")
    for bad in (conf_dir, conf_dir.parent, tmp_path, conf_dir / ".." / "conf"):
        with pytest.raises(ValueError, match="config"):
            build_site(
                make_config(),
                bad,
                today=TODAY,
                protect=[config_file],
                assets_dir=EXAMPLE.parent,
            )
    assert config_file.read_text() == "keep me"
    # A sibling directory is fine.
    build_site(
        make_config(), conf_dir.parent / "dist", today=TODAY, protect=[config_file]
    )


def test_build_refuses_an_out_dir_that_is_a_file(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        build_site(make_config(), target, today=TODAY, assets_dir=EXAMPLE.parent)


def test_every_external_link_has_rel_noopener(tmp_path: Path) -> None:
    build_site(
        load_site_config(EXAMPLE), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    seen = 0
    for page in pages(tmp_path):
        for tag in re.findall(r"<a\b[^>]*>", page.read_text()):
            href = re.search(r'href="([^"]*)"', tag)
            assert href, tag
            if href.group(1).startswith("https://"):
                seen += 1
                assert "noopener" in tag and "noreferrer" in tag, tag
    assert seen >= 4


def test_pages_declare_lang_and_viewport_and_a_title(tmp_path: Path) -> None:
    build_site(
        make_config(two_tabs()), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    assert len(pages(tmp_path)) == 3  # two tabs plus the privacy page
    for page in pages(tmp_path):
        text = page.read_text()
        assert text.startswith("<!DOCTYPE html>")
        assert '<html lang="en">' in text
        assert 'name="viewport"' in text
        assert re.search(r"<title>[^<]+</title>", text)
        assert '<meta charset="utf-8">' in text


def test_stylesheet_path_resolves_from_every_page(tmp_path: Path) -> None:
    build_site(
        make_config(two_tabs()), tmp_path, today=TODAY, assets_dir=EXAMPLE.parent
    )
    for page in pages(tmp_path):
        href = re.search(r'<link rel="stylesheet" href="([^"]+)"', page.read_text())
        assert href
        assert (page.parent / href.group(1)).resolve().is_file()


def test_build_is_deterministic(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    build_site(config, tmp_path / "a", today=TODAY, assets_dir=EXAMPLE.parent)
    build_site(config, tmp_path / "b", today=TODAY, assets_dir=EXAMPLE.parent)
    first = snapshot(tmp_path / "a")
    assert first
    assert first == snapshot(tmp_path / "b")


def test_package_data_is_found_by_importlib_resources() -> None:
    base = resources.files("reach_around.site")
    assert (base / "static" / "site.css").is_file()
    names = {p.name for p in (base / "templates").iterdir()}
    assert {"base.html", "tab.html"} <= names


def test_package_data_patterns_cover_templates_and_static() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    assert "site/templates/*" in text and "site/static/*" in text


def test_golden_output_for_example_config(tmp_path: Path, update_golden: bool) -> None:
    out = tmp_path / "out"
    build_site(load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent)
    built = snapshot(out)
    if update_golden:
        for old in sorted(GOLDEN.rglob("*"), reverse=True):
            if old.is_file():
                old.unlink()
            else:
                old.rmdir()
        for name, data in built.items():
            target = GOLDEN / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    expected = {
        str(p.relative_to(GOLDEN)): p.read_bytes()
        for p in sorted(GOLDEN.rglob("*"))
        if p.is_file()
    }
    assert expected, "no golden files; run pytest --update-golden"
    assert built == expected
