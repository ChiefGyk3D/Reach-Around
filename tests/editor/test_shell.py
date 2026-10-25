# SPDX-License-Identifier: Apache-2.0
"""The editor shell: served files, structure, accessibility basics, DOM rules."""

from __future__ import annotations

import re
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from editorharness import request, running_editor
from reach_around.editor.server import EditorSession


def asset(name: str) -> str:
    root = resources.files("reach_around").joinpath("editor_assets")
    return root.joinpath(name).read_text(encoding="utf-8")


def asset_names() -> list[str]:
    root = resources.files("reach_around").joinpath("editor_assets")
    return sorted(i.name for i in root.iterdir() if i.is_file())


@pytest.fixture
def session(tmp_path: Path) -> Iterator[EditorSession]:
    with running_editor(tmp_path) as running:
        yield running


def test_every_asset_file_is_served_with_the_right_type(
    session: EditorSession,
) -> None:
    names = asset_names()
    assert len(names) >= 9
    for name in names:
        path = "/__edit/" if name == "index.html" else f"/__edit/{name}"
        got = request(session.port, "GET", path)
        assert got.status == 200, name
        assert got.body.decode() == asset(name), name
        kind = got.headers["content-type"]
        expected = "text/css" if name.endswith(".css") else "text/javascript"
        assert kind.startswith("text/html" if name == "index.html" else expected)


def test_a_file_outside_the_allowlist_is_not_served(session: EditorSession) -> None:
    for path in ("/__edit/secret.js", "/__edit/../app.js", "/__edit/vendor/x.js"):
        assert request(session.port, "GET", path).status == 404


def test_the_preview_frame_is_sandboxed_with_scripts_only() -> None:
    html = asset("index.html")
    frames = re.findall(r"<iframe\b[^>]*>", html)
    assert len(frames) == 1
    assert re.search(r'\ssandbox="allow-scripts"(\s|>)', frames[0])
    assert "allow-same-origin" not in html
    assert re.search(r'\stitle="', frames[0]) or "data-string" in frames[0]
    assert not re.search(r"\ssrc\s*=", frames[0])  # the script sets it, checked


def test_the_shell_has_the_landmarks_and_one_h1() -> None:
    html = asset("index.html")
    assert re.search(r'<html lang="en"', html)
    assert len(re.findall(r"<h1\b", html)) == 1
    for tag in ("header", "nav", "main"):
        assert len(re.findall(rf"<{tag}\b", html)) == 1, tag
    assert re.search(r"<nav\b[^>]*aria-label", html)
    assert 'name="viewport"' in html


def test_the_skip_link_is_the_first_focusable_element_and_targets_the_preview() -> None:
    html = asset("index.html")
    body = html.split("<body", 1)[1]
    first = re.search(r"<(a|button|input|select|textarea|iframe)\b[^>]*>", body)
    assert first
    assert first.group(1) == "a"
    assert 'href="#preview"' in first.group(0)
    assert re.search(r'id="preview"', html)


def test_there_is_one_polite_live_region_and_a_status_line() -> None:
    html = asset("index.html")
    regions = re.findall(r"<[^>]*aria-live=[^>]*>", html)
    assert len(regions) == 1
    assert 'aria-live="polite"' in regions[0]
    assert 'role="status"' in regions[0]


def test_visible_text_comes_from_strings_js() -> None:
    html = asset("index.html")
    strings = asset("strings.js")
    ids = re.findall(r'data-string="([a-zA-Z.]+)"', html)
    assert len(ids) >= 6
    for ident in ids:
        assert re.search(rf'^\s*"?{re.escape(ident)}"?\s*:', strings, re.M), ident


def test_css_has_focus_reduced_motion_and_color_scheme_rules() -> None:
    css = asset("app.css")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "prefers-color-scheme" in css
    assert ".visually-hidden" in css
    assert "margin-inline-start" in css or "inline-size" in css
    assert not re.search(r"@import|url\(", css)


def test_the_token_is_read_once_and_never_stored() -> None:
    token = asset("token.js")
    assert "replaceState" in token
    assert "location.hash" in token or ".hash" in token
    everything = "\n".join(asset(n) for n in asset_names() if n.endswith(".js"))
    assert not re.search(r"\b(window|globalThis|self)\s*\.\s*\w+\s*=", everything)
    assert not re.search(r"\b(document|window)\s*\.\s*(cookie|name)\b", everything)
    # The token appears only as a header value in the client.
    api = asset("api.js")
    assert api.count("X-RA-Token") == 1
    assert not re.search(r"console\s*\.", everything)
    assert not re.search(r"""[?&]t=|\+\s*token|\$\{\s*token""", api)


def test_the_client_handles_the_error_shape_and_sends_no_credentials() -> None:
    api = asset("api.js")
    for needle in ("error.code", "error.message", "error.path", 'redirect: "error"'):
        assert needle in api, needle
    assert 'referrerPolicy: "no-referrer"' in api
    assert 'cache: "no-store"' in api


def test_the_preview_address_is_checked_before_it_is_used() -> None:
    view = asset("view.js")
    assert '"http:"' in view
    for host in ("127.0.0.1", "localhost", "[::1]"):
        assert host in view
