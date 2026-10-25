# SPDX-License-Identifier: Apache-2.0
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from assetpaths import asset_file, has_asset
from reach_around.config import SiteConfig
from reach_around.site.build import build_site
from reach_around.site.privacy import Disclosure, collected_items, sets_cookies

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_privacy" / "privacy" / "index.html"
INTRO = "Cookies, sadly not the chocolate chip kind."


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
            {
                "id": "more",
                "title": "More",
                "blocks": [
                    {"id": "two", "title": "Two", "url": "https://example.com/2"}
                ],
            },
        ],
    }
    data.update(extra)
    return SiteConfig.model_validate(data)


def text_of(items: list[Disclosure]) -> str:
    return " ".join(f"{i.what} {i.why} {i.kept}" for i in items)


_CLICK_SCRIPT = re.compile(
    r'<script src="(?:\.\./)?static/click\.[0-9a-f]{20}\.js" defer></script>'
)


def without_click_script(text: str) -> str:
    """Drop the one allowed tag (external, deferred click.js)."""
    return _CLICK_SCRIPT.sub("", text)


def read_all(out: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(out.rglob("*.html")))


def test_default_disclosure_lists_click_counts_and_says_no_ip_address() -> None:
    items = collected_items(make_config())
    assert len(items) == 1
    text = text_of(items)
    assert "country" in text
    assert "no IP address" in text
    assert "90 days" in text


def test_no_item_is_marked_sensitive() -> None:
    config = make_config(features={"kit_gate": True})
    assert collected_items(config)
    assert not any(item.sensitive for item in collected_items(config))


def test_disclosure_never_marks_collected_items_sensitive(tmp_path: Path) -> None:
    for features in ({}, {"kit_gate": True}, {"click_tracking": False}):
        config = make_config(features=features)
        assert not any(i.sensitive for i in collected_items(config))
    build_site(make_config(features={"kit_gate": True}), tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "This is not sensitive data." in page
    assert "This is sensitive data." not in page


def test_no_cookies_when_gate_off(tmp_path: Path) -> None:
    config = make_config()
    assert sets_cookies(config) is False
    build_site(config, tmp_path, today=TODAY)
    text = read_all(tmp_path)
    assert "document.cookie" not in text
    assert "cookie-banner" not in text
    assert "Reject" not in text


def test_gate_on_adds_email_cookie_items_and_banner_with_preferences_reject_accept(
    tmp_path: Path,
) -> None:
    config = make_config(
        features={"kit_gate": True}, privacy={"lead_retention_days": 200}
    )
    assert sets_cookies(config) is True
    items = collected_items(config)
    gate_text = text_of(items[1:])
    assert "email" in gate_text
    assert "200 days" in gate_text
    assert "cookie" in gate_text and "remembers" in gate_text
    build_site(config, tmp_path, today=TODAY)
    for page in (tmp_path / "index.html", tmp_path / "more" / "index.html"):
        html = page.read_text()
        assert "cookie-banner" in html
        for label in ("Preferences", "Reject", "Accept"):
            assert re.search(rf"<button[^>]*>{label}</button>", html)
        form = re.search(r"<form[^>]*>", html)
        assert form and "privacy/" in form.group(0)
    privacy = (tmp_path / "privacy" / "index.html").read_text()
    assert "200 days" in privacy and "email" in privacy


def test_banner_text_comes_from_config_when_set(tmp_path: Path) -> None:
    config = make_config(features={"kit_gate": True}, privacy={"intro": INTRO})
    build_site(config, tmp_path, today=TODAY)
    assert INTRO in (tmp_path / "privacy" / "index.html").read_text()
    assert INTRO in (tmp_path / "index.html").read_text()


def test_every_page_links_to_privacy_in_the_footer(tmp_path: Path) -> None:
    build_site(make_config(), tmp_path, today=TODAY)
    pages = sorted(tmp_path.rglob("*.html"))
    assert len(pages) == 4  # two tabs, privacy and 404
    for page in pages:
        html = page.read_text()
        href = re.search(r'<footer>.*<a href="([^"]*privacy/)"', html, re.S)
        assert href, page
        # 404.html is root-relative (it is served at any depth); the rest are not.
        base = tmp_path if href.group(1).startswith("/") else page.parent
        assert (base / href.group(1).lstrip("/")).resolve() == (
            tmp_path / "privacy"
        ).resolve()


def test_privacy_page_states_the_retention_numbers_from_config(
    tmp_path: Path,
) -> None:
    config = make_config(
        features={"kit_gate": True},
        privacy={"retention_days": 30, "lead_retention_days": 45},
    )
    build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "30 days" in page and "45 days" in page
    assert "90 days" not in page and "365 days" not in page


def test_disabling_click_tracking_removes_the_click_item(tmp_path: Path) -> None:
    config = make_config(features={"click_tracking": False})
    assert collected_items(config) == []
    build_site(config, tmp_path, today=TODAY)
    page = (tmp_path / "privacy" / "index.html").read_text()
    assert "click" not in page.lower()
    assert "collects nothing" in page


def test_privacy_page_has_no_scripts_and_escapes_the_intro(tmp_path: Path) -> None:
    evil = '<script>alert(1)</script> "q"'
    config = make_config(
        features={"kit_gate": True},
        privacy={"intro": evil, "contact": "<b>me</b>@example.com"},
    )
    build_site(config, tmp_path, today=TODAY)
    text = read_all(tmp_path)
    assert "<script" not in without_click_script(text).lower()
    assert "&lt;script&gt;" in (tmp_path / "privacy" / "index.html").read_text()
    assert "<b>" not in text


def test_unknown_privacy_keys_and_bad_retention_are_refused() -> None:
    with pytest.raises(ValueError):
        make_config(privacy={"retention_days": 0})
    with pytest.raises(ValueError):
        make_config(features={"tracking": True})


def test_golden_privacy_page(tmp_path: Path, update_golden: bool) -> None:
    config = make_config(
        features={"kit_gate": True},
        privacy={"contact": "privacy@example.com"},
    )
    build_site(config, tmp_path, today=TODAY)
    built = (tmp_path / "privacy" / "index.html").read_bytes()
    if update_golden:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_bytes(built)
    assert GOLDEN.is_file(), "no golden file; run pytest --update-golden"
    assert built == GOLDEN.read_bytes()


def test_click_tracking_off_removes_ping_and_the_click_script(tmp_path: Path) -> None:
    config = make_config(features={"click_tracking": False})
    build_site(config, tmp_path, today=TODAY)
    text = read_all(tmp_path)
    assert "<script" not in text.lower()
    assert "ping=" not in text and "data-link-id" not in text
    assert not has_asset(tmp_path, "static/click.js")
    assert not list((tmp_path / "static").glob("click*"))


def test_click_tracking_on_adds_ping_and_exactly_one_script_per_page(
    tmp_path: Path,
) -> None:
    build_site(make_config(), tmp_path, today=TODAY)
    pages = sorted(tmp_path.rglob("*.html"))
    assert pages
    for page in pages:
        # The 404 page is never click-counted, so it has no script at all.
        expected = 0 if page.name == "404.html" else 1
        assert page.read_text().lower().count("<script") == expected, page
    assert 'ping="/e/c/one"' in (tmp_path / "index.html").read_text()
    assert asset_file(tmp_path, "static/click.js").is_file()
