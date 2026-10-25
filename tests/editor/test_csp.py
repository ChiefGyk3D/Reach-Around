# SPDX-License-Identifier: Apache-2.0
"""Headers and CSP on every editor response; the preview's own policy."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from editorharness import Response, raw, request, running_editor
from reach_around.editor.server import EditorSession


@pytest.fixture
def session(tmp_path: Path) -> Iterator[EditorSession]:
    with running_editor(tmp_path) as running:
        yield running


def every_kind_of_response(session: EditorSession) -> list[Response]:
    """Success, every refusal class, 404, and a parse failure."""
    token = {"X-RA-Token": session.token}
    port = session.port
    return [
        request(port, "GET", "/__edit/"),
        request(port, "GET", "/__edit/app.js"),
        request(port, "GET", "/__edit/app.css"),
        request(port, "GET", "/__edit/api/state", token),
        request(port, "GET", "/__edit/api/state"),  # 401
        request(port, "GET", "/nope"),  # 404
        request(port, "GET", "/__edit/", host="evil.example"),  # 421
        request(port, "DELETE", "/__edit/"),  # 405
        request(port, "GET", "/__edit/", {"Origin": "http://evil.example"}),  # 403
        request(port, "POST", "/__edit/api/op", token, b"{}", same_origin=True),  # 415
        request(port, "GET", "/__edit/", {"X-Pad": "a" * 20000}),  # 431
        raw(port, b"\x00\x01garbage\r\n\r\n"),  # unparseable
    ]


def test_csp_has_no_unsafe_directive_on_any_editor_response(
    session: EditorSession,
) -> None:
    responses = every_kind_of_response(session)
    assert len(responses) >= 10
    for got in responses:
        if got.status is None:
            continue
        policy = got.headers["content-security-policy"]
        assert "unsafe" not in policy.lower()
        assert "'nonce-" not in policy
        assert "default-src 'none'" in policy
        assert "script-src 'self'" in policy
        assert "style-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "object-src 'none'" in policy
        assert "base-uri 'none'" in policy
        assert "form-action 'none'" in policy
        assert "connect-src 'self'" in policy
        assert not re.search(
            r"https?:", policy.replace(f"http://127.0.0.1:{session.preview_port}", "")
        )


def test_every_editor_response_has_the_hardening_headers(
    session: EditorSession,
) -> None:
    for got in every_kind_of_response(session):
        if got.status is None:
            continue
        assert got.headers["x-content-type-options"] == "nosniff"
        assert got.headers["x-frame-options"] == "DENY"
        assert got.headers["referrer-policy"] == "no-referrer"
        assert got.headers["cross-origin-opener-policy"] == "same-origin"
        assert got.headers["cross-origin-resource-policy"] == "same-origin"
        assert got.headers["cache-control"] == "no-store"
        assert not [h for h in got.headers if h.startswith("access-control-")]


def test_frame_src_names_only_the_preview_origin_in_the_form_opened(
    session: EditorSession,
) -> None:
    for name in ("127.0.0.1", "localhost", "[::1]"):
        got = request(session.port, "GET", "/__edit/", host=f"{name}:{session.port}")
        policy = got.headers["content-security-policy"]
        match = re.search(r"frame-src ([^;]+);", policy)
        assert match
        assert match.group(1).lower() == f"http://{name.lower()}:{session.preview_port}"


def test_document_sandbox_directive_is_present(session: EditorSession) -> None:
    policy = request(session.port, "GET", "/__edit/").headers["content-security-policy"]
    assert "sandbox allow-same-origin allow-scripts" in policy


def test_shell_html_contains_no_token(session: EditorSession) -> None:
    for path in ("/__edit/", "/__edit/app.js", "/__edit/app.css"):
        got = request(session.port, "GET", path)
        assert session.token.encode() not in got.body
        assert session.token not in "".join(got.headers.values())


def test_shell_html_uses_no_inline_script_or_style(session: EditorSession) -> None:
    html = request(session.port, "GET", "/__edit/").body.decode()
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert match.group(0).count("src=") == 1
        assert match.group(1).strip() == ""
    assert "<style" not in html.lower()
    assert not re.search(r"\sstyle\s*=", html, re.I)


def test_preview_listener_is_sandboxed_and_only_the_editor_may_frame_it(
    session: EditorSession,
) -> None:
    got = request(session.preview_port, "GET", "/")
    assert got.status == 200
    policy = got.headers["content-security-policy"]
    assert re.search(r"(^|;\s*)sandbox allow-scripts(;|$)", policy)
    assert "allow-same-origin" not in policy
    assert f"frame-ancestors http://127.0.0.1:{session.port}" in policy
    assert "unsafe" not in policy
    assert got.headers["x-content-type-options"] == "nosniff"


def test_preview_listener_checks_host_with_port_and_method(
    session: EditorSession,
) -> None:
    port = session.preview_port
    assert request(port, "GET", "/", host="evil.example").status == 421
    assert request(port, "GET", "/", host=f"127.0.0.1:{session.port}").status == 421
    assert request(port, "POST", "/").status == 405
    assert request(port, "GET", "/__edit/api/state").status == 404
    assert request(port, "GET", "/%2e%2e/%2e%2e/etc/passwd").status == 404
