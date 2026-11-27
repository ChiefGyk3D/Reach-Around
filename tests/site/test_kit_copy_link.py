# SPDX-License-Identifier: Apache-2.0
"""The copy-link control on the public kit page (#432).

Synthetic data only (fake brand, example.com). The control is the page's own
address as visible text with a copy button that needs script; nothing is sent
anywhere and the address comes from the configuration, never a request.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from test_kit_build import Page, build, gated_doc, kit_doc, make_config

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

# Update this pin only together with a review of static/copy-link.js.
COPY_LINK_JS_SHA256 = "72a2c0e6b7a7db6a0cdd0f4b3ebb910b9f6d3243e934c8cd656f5cd756be91b9"
ORIGIN = "https://example.com"
GOLDEN = Path(__file__).parent / "golden_kit_share"
BOX = re.compile(r'<p class="kit-share">.*?</p>', re.S)


def static_text(name: str) -> str:
    return (
        (resources.files("reach_around.site") / "static" / name)
        .read_bytes()
        .decode("utf-8")
    )


def script() -> str:
    return static_text("copy-link.js")


def config_with(kit: dict[str, Any] | None = None, **extra: Any) -> SiteConfig:
    doc = dict(kit if kit is not None else gated_doc())
    doc.setdefault("public_url", f"{ORIGIN}/kit/")
    doc.update(extra)
    return make_config(doc)


def public_html(tmp_path: Path, config: SiteConfig) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    out, _ = build(tmp_path, config)
    return (out / "kit" / "index.html").read_text(encoding="utf-8")


def box_of(html: str) -> str:
    found = BOX.search(html)
    assert found, "no copy-link box on the page"
    return found.group(0)


# --- the script -------------------------------------------------------------


def test_script_hash_is_pinned() -> None:
    assert hashlib.sha256(script().encode("utf-8")).hexdigest() == COPY_LINK_JS_SHA256


@pytest.mark.parametrize(
    "banned",
    [
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "fetch(",
        "XMLHttpRequest",
        "sendBeacon",
        "WebSocket",
        "window.open",
        "location",
        "navigator.share",
        "http://",
        "https://",
    ],
)
def test_script_uses_no_storage_network_or_navigation(banned: str) -> None:
    assert banned not in script()


def test_script_announces_in_the_live_region_and_needs_the_clipboard_api() -> None:
    text = script()
    assert "data-copy-link-status" in text
    assert 'typeof navigator.clipboard.writeText === "function"' in text
    # the button is shown only once the script has run
    assert 'classList.add("copy-link-ready")' in text


# --- the page, control on ---------------------------------------------------


def test_public_page_prints_the_configured_address_as_text(tmp_path: Path) -> None:
    html = public_html(tmp_path, config_with())
    assert (
        f'<a class="kit-share-url" href="{ORIGIN}/kit/" data-copy-link-url>'
        f"{ORIGIN}/kit/</a>"
    ) in html
    # no query string, no fragment, no tracking parameter
    assert f"{ORIGIN}/kit/?" not in html and f"{ORIGIN}/kit/#" not in html
    assert "utm_" not in html


def test_address_comes_from_config(tmp_path: Path) -> None:
    html = public_html(tmp_path, config_with(public_url="https://other.example.org/k/"))
    assert 'href="https://other.example.org/k/" data-copy-link-url' in html
    assert f"{ORIGIN}/kit/" not in html


def test_address_falls_back_to_the_site_origin_of_a_stack_page(
    tmp_path: Path,
) -> None:
    from test_smart_stack import raw as stack_raw

    data = stack_raw()
    data["page"]["url"] = "https://links.example.org"
    data["kit"] = kit_doc()
    data["features"] = {**data.get("features", {}), "kit_gate": False}
    config = SiteConfig.model_validate(data)
    assert config.kit is not None and config.kit.public_url is None
    out = tmp_path / "stack"
    build_site(config, out, today=date(2027, 3, 10))
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert 'href="https://links.example.org/kit/" data-copy-link-url' in html


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.com/kit/",
        "https://example.com/kit/?utm_source=x",
        "https://example.com/kit/#top",
        "https://user:pw@example.com/kit/",
        "javascript:alert(1)",
        "//example.com/kit/",
    ],
)
def test_public_url_is_a_plain_https_address(bad: str) -> None:
    with pytest.raises(ValueError):
        make_config(kit_doc(public_url=bad))


def test_no_script_state_is_a_visible_link(tmp_path: Path) -> None:
    text = box_of(public_html(tmp_path, config_with()))
    # a plain anchor with the address as its text, nothing hidden by markup
    assert " hidden" not in text
    assert f">{ORIGIN}/kit/</a>" in text
    # the button is hidden only by the stylesheet until the script marks the page
    css = static_text("kit.css")
    assert ".kit-share .kit-share-button { display: none;" in css
    shown = ".copy-link-ready .kit-share .kit-share-button { display: inline-flex; }"
    assert shown in css


def test_accessibility_basics(tmp_path: Path) -> None:
    text = box_of(public_html(tmp_path, config_with()))
    page = Page(text)
    (button,) = page.find("button")
    assert button["type"] == "button"
    assert "data-copy-link" in button
    assert "Copy link</button>" in text  # the visible label is the name
    (live,) = [a for a in page.find("span") if "data-copy-link-status" in a]
    assert live["role"] == "status" and live["aria-live"] == "polite"
    # the live region is in the markup the page loads with, empty
    assert "data-copy-link-status></span>" in text
    # a native link and a native button: both reachable by keyboard
    (link,) = page.find("a")
    assert link["href"] == f"{ORIGIN}/kit/"
    assert "tabindex" not in text


def test_tap_target_and_wrapping_rules_are_in_the_stylesheet() -> None:
    css = static_text("kit.css")
    assert re.search(r"\.kit-share-url \{[^}]*min-height: var\(--ra-tap\)", css)
    assert re.search(
        r"\.kit-share \.kit-share-button \{[^}]*min-height: var\(--ra-tap\)", css
    )
    assert re.search(r"\.kit-share-url \{[^}]*overflow-wrap: anywhere", css)


def test_no_share_to_network_buttons_or_third_party_requests(tmp_path: Path) -> None:
    text = box_of(public_html(tmp_path, config_with()))
    assert re.findall(r'(?:href|src|action)="([^"]+)"', text) == [f"{ORIGIN}/kit/"]
    for network in ("twitter", "facebook", "linkedin", "mailto:", "intent/", "share?"):
        assert network not in text.lower()


def test_script_is_linked_from_the_public_kit_page_only(tmp_path: Path) -> None:
    out, gated = build(tmp_path, config_with())
    public = (out / "kit" / "index.html").read_text(encoding="utf-8")
    retry = (out / "kit" / "retry" / "index.html").read_text(encoding="utf-8")
    gated_html = (gated / "kit.html").read_text(encoding="utf-8")
    assert re.search(r'<script src="\.\./static/copy-link[^"]*\.js" defer>', public)
    for text in (retry, gated_html):
        assert "copy-link" not in text
        assert "kit-share" not in text
    assert list((out / "static").glob("copy-link*.js"))


def test_no_new_inline_script_and_csp_stays_strict(tmp_path: Path) -> None:
    out, _ = build(tmp_path, config_with())
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert [a for a in Page(html).find("script") if not a.get("src")] == []
    assert " onclick" not in html and "javascript:" not in html
    headers = (out / "_headers").read_text(encoding="utf-8")
    assert "unsafe-inline" not in headers
    assert "script-src 'self'" in headers


def test_the_gated_half_is_never_given_a_link(tmp_path: Path) -> None:
    _, gated = build(tmp_path, config_with())
    assert f"{ORIGIN}/kit/" not in (gated / "kit.html").read_text(encoding="utf-8")


# --- the control off ------------------------------------------------------------


def test_default_is_on() -> None:
    kit = config_with().kit
    assert kit is not None and kit.copy_link is True


def test_off_removes_the_markup_and_the_script(tmp_path: Path) -> None:
    out, _ = build(tmp_path, config_with(copy_link=False))
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "kit-share" not in html and "data-copy-link" not in html
    assert "copy-link" not in html
    assert not list((out / "static").glob("copy-link*.js"))


def test_no_site_origin_means_no_control(tmp_path: Path) -> None:
    out, _ = build(tmp_path, make_config())
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    assert "kit-share" not in html and "copy-link" not in html
    assert not list((out / "static").glob("copy-link*.js"))


def test_no_kit_publishes_no_script(tmp_path: Path) -> None:
    config = make_config().model_copy(update={"kit": None})
    out = tmp_path / "plain"
    build_site(config, out, today=date(2027, 3, 10))
    assert not list((out / "static").glob("copy-link*.js"))


def test_schema_accepts_only_a_real_boolean() -> None:
    with pytest.raises(ValueError):
        make_config(kit_doc(copy_link="no"))
    with pytest.raises(ValueError):
        make_config(kit_doc(copy_link=0))
    kit = make_config(kit_doc(copy_link=False)).kit
    assert kit is not None and kit.copy_link is False


def test_twitch_and_kick_are_unaffected(tmp_path: Path) -> None:
    rows = [
        {"platform": p, "label": "Followers", "value": 10, "as_of": "2027-03-01"}
        for p in ("twitch", "kick")
    ]
    doc = kit_doc(metrics=rows)
    on = public_html(tmp_path / "on", config_with(doc))
    off = public_html(tmp_path / "off", config_with(doc, copy_link=False))
    control = re.compile(
        r'<p class="kit-share">.*?</p>\n'
        r'|<script src="[^"]*copy-link[^"]*" defer></script>\n',
        re.S,
    )
    # the page minus the control and its script is byte for byte the page without it
    assert control.sub("", on) == off
    assert on.count("Twitch") == off.count("Twitch") > 0
    assert on.count("Kick") == off.count("Kick") > 0


# --- golden -------------------------------------------------------------------


def test_golden_page_with_the_control(tmp_path: Path, update_golden: bool) -> None:
    html = public_html(tmp_path, config_with())
    target = GOLDEN / "public/kit/index.html"
    if update_golden:
        shutil.rmtree(GOLDEN, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    assert target.is_file(), "no golden file; run pytest --update-golden"
    assert html == target.read_text(encoding="utf-8")
