# SPDX-License-Identifier: Apache-2.0
"""The optional profile card shown after the content on every page."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from reach_around.config import ConfigError, SiteConfig, load_site_config
from reach_around.site.build import build_site, manifest_json

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
URL = "https://media.example.com/kit"
BUTTON = {"label": "Media kit", "url": URL}
CARD = {"tags": ["Retro games", "Security"], "button": BUTTON}


def make_config(profile_card: Any = None, **extra: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "disclosure": "We may earn a commission.",
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [
                    {
                        "id": "one",
                        "title": "One",
                        "url": "https://example.com/1",
                        "affiliate": True,
                    }
                ],
            },
            {"id": "more", "title": "More", "blocks": []},
        ],
    }
    if profile_card is not None:
        data["profile_card"] = profile_card
    data.update(extra)
    return SiteConfig.model_validate(data)


def build(config: SiteConfig, out: Path) -> dict[str, str]:
    build_site(config, out, today=TODAY)
    return {
        str(p.relative_to(out)): p.read_text(encoding="utf-8")
        for p in sorted(out.rglob("*.html"))
    }


def snapshot(out: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(out)): p.read_bytes()
        for p in sorted(out.rglob("*"))
        if p.is_file()
    }


def bad(profile_card: Any) -> str:
    with pytest.raises(ValidationError) as info:
        make_config(profile_card)
    return str(info.value)


# --- validation matrix ------------------------------------------------------


def test_absent_card_is_none() -> None:
    assert make_config().profile_card is None


def test_valid_card_loads_with_defaults() -> None:
    card = make_config(CARD).profile_card
    assert card is not None
    assert card.title is None
    assert card.tags == ["Retro games", "Security"]
    assert card.button is not None and card.button.url == URL


def test_tags_only_and_button_only_are_valid() -> None:
    assert make_config({"tags": ["One"]}).profile_card is not None
    assert make_config({"button": BUTTON}).profile_card is not None


def test_eight_tags_ok_nine_refused() -> None:
    make_config({"tags": [f"t{n}" for n in range(8)]})
    assert "at most 8" in bad({"tags": [f"t{n}" for n in range(9)]})


def test_tag_length_bounds() -> None:
    make_config({"tags": ["x" * 30, "y"]})
    assert "profile_card.tags.0" in bad({"tags": ["x" * 31]})
    assert "profile_card.tags.0" in bad({"tags": [""]})


@pytest.mark.parametrize(
    "tag",
    [
        "bell\x07",
        "new\nline",
        "tab\there",
        "nul\x00",
        "rtl‮override",
        "iso⁦late",
        "mark‏here",
        "zw​space",
        "sep line",
        " padded",
        "padded ",
        "   ",
    ],
)
def test_tag_refuses_control_bidi_and_padding(tag: str) -> None:
    assert "profile_card.tags.0" in bad({"tags": [tag]})


@pytest.mark.parametrize("tag", [5, None, True, ["a"]])
def test_tag_must_be_a_string(tag: Any) -> None:
    assert "profile_card.tags.0" in bad({"tags": [tag]})


def test_button_label_bounds() -> None:
    make_config({"button": {"label": "x" * 40, "url": URL}})
    assert "profile_card.button.label" in bad(
        {"button": {"label": "x" * 41, "url": URL}}
    )
    assert "profile_card.button.label" in bad({"button": {"label": "", "url": URL}})
    assert "profile_card.button.label" in bad({"button": {"label": "a‮b", "url": URL}})


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.com/kit",
        "javascript:alert(1)",
        "data:text/html,hi",
        "//media.example.com/kit",
        "/kit/",
        "kit.html",
        "https://user:pw@media.example.com/kit",
        "https://media.example.com/a b",
        "https:///nohost",
        "https://media.example.com/" + "a" * 2100,
    ],
)
def test_button_url_must_be_plain_https(url: str) -> None:
    assert "profile_card.button.url" in bad({"button": {"label": "Kit", "url": url}})


def test_button_needs_both_keys_and_refuses_unknown_keys() -> None:
    assert "profile_card.button.url" in bad({"button": {"label": "Kit"}})
    assert "profile_card.button.label" in bad({"button": {"url": URL}})
    assert "profile_card.button.color" in bad(
        {"button": {"label": "Kit", "url": URL, "color": "red"}}
    )
    assert "profile_card.style" in bad({"tags": ["a"], "style": "x"})


def test_title_rules() -> None:
    assert make_config({**CARD, "title": "Work with me"}).profile_card is not None
    assert "profile_card.title" in bad({**CARD, "title": ""})
    assert "profile_card.title" in bad({**CARD, "title": "x" * 81})
    assert "profile_card.title" in bad({**CARD, "title": "a\nb"})


def test_empty_card_is_refused() -> None:
    assert "tags or a button" in bad({})
    assert "tags or a button" in bad({"title": "Only a title"})


def test_media_kit_id_is_reserved_when_there_is_a_button() -> None:
    tabs = [{"id": "profile-media-kit", "title": "T", "blocks": []}]
    with pytest.raises(ValidationError) as info:
        make_config(CARD, tabs=tabs)
    assert "profile-media-kit" in str(info.value)
    make_config({"tags": ["a"]}, tabs=tabs)  # no button: the id is free


# --- rendering --------------------------------------------------------------


def card_html(page: str) -> str:
    match = re.search(r'<section class="profile-card".*?</section>', page, re.S)
    assert match, "profile card not found"
    return match.group(0)


def test_card_renders_on_every_tab_privacy_and_the_404_page(tmp_path: Path) -> None:
    pages = build(make_config(CARD), tmp_path)
    assert set(pages) == {
        "index.html",
        "more/index.html",
        "privacy/index.html",
        "404.html",
    }
    for name, page in pages.items():
        assert page.count('class="profile-card"') == 1, name
        html = card_html(page)
        assert "<h2" in html and "Example Creator" in html
        assert '<li class="profile-tag">Retro games</li>' in html
        assert '<li class="profile-tag">Security</li>' in html
        assert f'href="{URL}"' in html and ">Media kit</a>" in html


def test_card_sits_after_content_and_before_disclosure_and_footer(
    tmp_path: Path,
) -> None:
    pages = build(make_config(CARD), tmp_path)
    page = pages["index.html"]
    assert (
        page.index('class="block block-link"')
        < page.index('class="profile-card"')
        < page.index("<footer>")
        < page.index('class="disclosure"')
    )
    empty = pages["more/index.html"]
    assert empty.index('class="empty"') < empty.index('class="profile-card"')
    privacy = pages["privacy/index.html"]
    assert privacy.index('class="privacy"') < privacy.index('class="profile-card"')
    assert privacy.index('class="profile-card"') < privacy.index("<footer>")


def test_title_defaults_to_creator_name_and_can_be_set(tmp_path: Path) -> None:
    html = card_html(build(make_config(CARD), tmp_path / "a")["index.html"])
    assert re.search(r"<h2[^>]*>Example Creator</h2>", html)
    titled = make_config({**CARD, "title": "Work with me"})
    html = card_html(build(titled, tmp_path / "b")["index.html"])
    assert re.search(r"<h2[^>]*>Work with me</h2>", html)
    assert "Example Creator" not in html


def test_button_is_external_link_with_id_and_ping(tmp_path: Path) -> None:
    html = card_html(build(make_config(CARD), tmp_path)["index.html"])
    anchor = re.search(r"<a [^>]*>", html)
    assert anchor
    tag = anchor.group(0)
    assert 'id="profile-media-kit"' in tag
    assert 'rel="noopener noreferrer"' in tag
    assert 'ping="/e/c/profile-media-kit"' in tag
    assert 'data-link-id="profile-media-kit"' in tag
    assert "target=" not in tag


def test_button_is_not_tracked_when_click_tracking_is_off(tmp_path: Path) -> None:
    config = make_config(CARD, features={"click_tracking": False})
    html = card_html(build(config, tmp_path)["index.html"])
    assert "ping=" not in html and "data-link-id" not in html
    assert 'id="profile-media-kit"' in html


def test_manifest_lists_the_media_kit_when_there_is_a_button(tmp_path: Path) -> None:
    manifest = build_site(make_config(CARD), tmp_path, today=TODAY)
    assert not (tmp_path / "manifest.json").exists()
    links = json.loads(manifest_json(manifest))["links"]
    assert links["profile-media-kit"] == URL
    assert links["one"] == "https://example.com/1"


def test_manifest_has_no_media_kit_without_a_button(tmp_path: Path) -> None:
    manifest = build_site(make_config({"tags": ["a"]}), tmp_path, today=TODAY)
    links = json.loads(manifest_json(manifest))["links"]
    assert "profile-media-kit" not in links


def test_tags_only_card_has_no_button(tmp_path: Path) -> None:
    html = card_html(build(make_config({"tags": ["a"]}), tmp_path)["index.html"])
    assert "<a " not in html and "profile-tag" in html


def test_button_only_card_has_no_tag_list(tmp_path: Path) -> None:
    config = make_config({"button": BUTTON})
    html = card_html(build(config, tmp_path)["index.html"])
    assert "profile-tags" not in html and "profile-button" in html


def test_absent_card_output_is_unchanged(tmp_path: Path) -> None:
    manifest = build_site(make_config(), tmp_path / "a", today=TODAY)
    assert "profile-media-kit" not in manifest.links
    built = snapshot(tmp_path / "a")
    for name, data in built.items():
        if name.endswith(".html"):
            assert b"profile" not in data, name
    build_site(make_config(CARD), tmp_path / "b", today=TODAY)
    assert snapshot(tmp_path / "b") != built


def test_hostile_strings_are_escaped(tmp_path: Path) -> None:
    evil = "<script>alert(1)</script>"
    config = make_config(
        {
            "title": evil,
            "tags": [evil, '"><img src=x onerror=alert(1)>'],
            "button": {
                "label": evil,
                "url": 'https://media.example.com/"><script>alert(2)</script>',
            },
        },
        creator={"name": evil},
    )
    pages = build(config, tmp_path)
    assert pages
    for name, page in pages.items():
        html = card_html(page)
        assert "<script>" not in html and "<img" not in html, name
        assert html.count("&lt;script&gt;") >= 3
        assert "&#34;&gt;&lt;img" in html
        assert 'href="https://media.example.com/&#34;&gt;' in html


def test_no_inline_style_or_extra_script(tmp_path: Path) -> None:
    pages = build(make_config(CARD), tmp_path)
    assert pages
    for page in pages.values():
        html = card_html(page)
        assert "style=" not in html and "<script" not in html
        assert "onclick" not in html


def test_build_is_deterministic(tmp_path: Path) -> None:
    build_site(make_config(CARD), tmp_path / "a", today=TODAY)
    build_site(make_config(CARD), tmp_path / "b", today=TODAY)
    assert snapshot(tmp_path / "a") == snapshot(tmp_path / "b")


# --- stylesheet -------------------------------------------------------------


def profile_rules() -> dict[str, str]:
    css = (ROOT / "src/reach_around/site/static/site.css").read_text()
    rules = {
        sel.strip(): body
        for sel, body in re.findall(r"(\.profile[^{]*)\{([^}]*)\}", css)
    }
    assert rules, "no profile card rules"
    return rules


def test_profile_css_uses_tokens_only() -> None:
    for selector, body in profile_rules().items():
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), selector
        assert not re.search(r"\b(rgb|hsl)a?\(", body), selector
        for value in re.findall(r":\s*([^;]+)", body):
            assert not re.fullmatch(r"\d*\.?\d+(px|rem|em)", value.strip()), (
                f"{selector}: bare length {value}"
            )


def test_profile_button_is_tap_sized_and_chips_wrap() -> None:
    rules = profile_rules()
    assert "min-height: var(--ra-tap)" in rules[".profile-button"]
    assert "flex-wrap: wrap" in rules[".profile-tags"]


# --- documentation and example ---------------------------------------------


def test_commented_example_keys_validate_when_uncommented(tmp_path: Path) -> None:
    lines = (ROOT / "examples/instance/site.yaml").read_text().splitlines()
    start = lines.index("# profile_card:")
    block = []
    for line in lines[start:]:
        if not line.startswith("#"):
            break
        block.append(line[2:])
    assert len(block) == 5
    raw = yaml.safe_load("\n".join(block))
    assert make_config(raw["profile_card"]).profile_card is not None


def load_error(tmp_path: Path, profile_card: Any) -> str:
    raw = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [{"id": "start", "title": "Start", "blocks": []}],
        "profile_card": profile_card,
    }
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_site_config(path)
    return str(info.value)


def test_loader_names_the_bad_tag_with_a_bracket_path(tmp_path: Path) -> None:
    message = load_error(tmp_path, {"tags": ["ok", "x" * 31]})
    assert "profile_card.tags[1]: " in message
    assert "at most 30 characters" in message


def test_loader_names_the_bad_button_url_and_label(tmp_path: Path) -> None:
    message = load_error(
        tmp_path, {"button": {"label": "", "url": "http://media.example.com/"}}
    )
    assert "profile_card.button.label: " in message
    assert "profile_card.button.url: url must start with https://" in message


def test_loader_refuses_nine_tags_and_an_empty_card(tmp_path: Path) -> None:
    nine = load_error(tmp_path, {"tags": [f"t{n}" for n in range(9)]})
    assert "profile_card.tags: " in nine
    empty = load_error(tmp_path, {})
    assert "profile_card: a profile card needs tags or a button" in empty


def test_config_doc_profile_card_example_validates() -> None:
    doc = (ROOT / "docs/config.md").read_text()
    section = doc[doc.index("## `profile_card`") :]
    match = re.search(r"```yaml\n(.*?)```", section, re.S)
    assert match
    raw = yaml.safe_load(match.group(1))
    assert make_config(raw["profile_card"]).profile_card is not None
