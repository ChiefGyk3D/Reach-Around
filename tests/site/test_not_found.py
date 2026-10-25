# SPDX-License-Identifier: Apache-2.0
"""The 404 page: written at the site root, root-relative, untracked, noindex."""

import re
from datetime import date
from pathlib import Path
from typing import Any

from assetpaths import asset
from reach_around.config import SiteConfig, load_site_config
from reach_around.config.footer import CREDIT_URL
from reach_around.site.build import build_site, manifest_json

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"


def make_config(**extra: Any) -> SiteConfig:
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
            },
            {"id": "more", "title": "More", "blocks": []},
        ],
    }
    data.update(extra)
    return SiteConfig.model_validate(data)


def build(tmp_path: Path, config: SiteConfig) -> str:
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    return (out / "404.html").read_text(encoding="utf-8")


def test_404_is_written_at_the_site_root(tmp_path: Path) -> None:
    text = build(tmp_path, make_config())
    assert "That page does not exist." in text
    assert 'href="/"' in text


def test_404_always_written_for_the_example_config(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert (out / "404.html").is_file()


def test_assets_and_nav_are_root_relative(tmp_path: Path) -> None:
    # The host serves this file at any depth (/a/b/c), so no link may depend on
    # the URL it is shown at.
    config = make_config(
        creator={"name": "Example Creator", "avatar": "media/mascot.svg"},
    )
    text = build(tmp_path, config)
    out = tmp_path / "out"
    for needle in (
        f'href="/{asset(out, "static/site.css")}"',
        f'href="/{asset(out, "static/theme.css")}"',
        'href="/privacy/"',
        'href="/more/"',
        f'src="/{asset(out, "media/mascot.svg")}"',
    ):
        assert needle in text, needle
    # A pure in-page fragment (the skip link) resolves against any URL.
    refs = re.findall(r'(?:href|src|action)="([^"#][^"]*)"', text)
    assert len(refs) >= 4
    for ref in refs:
        # The one allowed exception is the footer credit's external https link.
        assert ref.startswith("/") or ref == CREDIT_URL, ref
    assert 'href="../' not in text and 'href="./' not in text


def test_no_tracking(tmp_path: Path) -> None:
    text = build(tmp_path, make_config())
    assert "data-link-id" not in text
    assert "click.js" not in text
    assert "<script" not in text.lower()


def test_noindex_only_on_the_404(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(make_config(), out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert '<meta name="robots" content="noindex">' in (out / "404.html").read_text()
    for other in ("index.html", "more/index.html", "privacy/index.html"):
        assert "noindex" not in (out / other).read_text(), other


def test_title_uses_the_existing_rules(tmp_path: Path) -> None:
    assert "<title>Not found - Example Creator</title>" in build(
        tmp_path, make_config()
    )
    custom = build(tmp_path, make_config(title_template="{tab} | {creator}!"))
    assert "<title>Not found | Example Creator!</title>" in custom


def test_hostile_creator_name_is_escaped(tmp_path: Path) -> None:
    evil = '<script>alert(1)</script>"x'
    text = build(
        tmp_path,
        make_config(creator={"name": evil}, title_template="{tab} - {creator}"),
    )
    assert "<script" not in text.lower()
    assert "&lt;script&gt;" in text
    assert evil not in text


def test_footer_privacy_link_and_cookie_notice(tmp_path: Path) -> None:
    text = build(tmp_path, make_config(features={"kit_gate": True}))
    assert '<a href="/privacy/">Privacy</a>' in text
    assert 'action="/privacy/#cookies"' in text
    assert "disclosure" not in text.replace("disclosure-", "")


def test_a_404_template_override_is_used(tmp_path: Path) -> None:
    overrides = tmp_path / "ov"
    (overrides / "templates").mkdir(parents=True)
    (overrides / "templates" / "404.html").write_text(
        '{% extends "base.html" %}{% block content %}'
        '<p class="mine">Lost? <a href="{{ root }}">Start</a></p>{% endblock %}'
    )
    out = tmp_path / "out"
    build_site(
        make_config(),
        out,
        today=TODAY,
        assets_dir=EXAMPLE.parent,
        overrides=overrides,
    )
    text = (out / "404.html").read_text()
    assert 'class="mine"' in text and 'href="/"' in text
    assert '<meta name="robots" content="noindex">' in text


def test_manifest_is_unaffected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    manifest = build_site(make_config(), out, today=TODAY, assets_dir=EXAMPLE.parent)
    assert manifest.links == {"one": "https://example.com/1"}
    assert "404" not in manifest_json(manifest)
