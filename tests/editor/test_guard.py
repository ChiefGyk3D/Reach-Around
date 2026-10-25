# SPDX-License-Identifier: Apache-2.0
"""The request guard: Host, Origin, Sec-Fetch-Site, token, type, method, caps."""

from __future__ import annotations

import json
from collections.abc import Iterator
from email.message import Message
from pathlib import Path

import pytest

from editorharness import (
    CONFIG_TEXT,
    SeededRandom,
    raw,
    request,
    running_editor,
    tree_snapshot,
)
from reach_around.editor.guard import Guard, Verdict
from reach_around.editor.server import EditorSession

ROUTES = ["/__edit/", "/__edit/app.js", "/__edit/api/state", "/nope", "/"]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[EditorSession]:
    with running_editor(tmp_path) as running:
        yield running


def tok(session: EditorSession) -> dict[str, str]:
    return {"X-RA-Token": session.token}


# ---- Host and DNS rebinding -------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_rebound_host_name_is_421_for_every_route(
    session: EditorSession, route: str
) -> None:
    for host in (f"evil.example:{session.port}", "evil.example", "127.0.0.1.evil.test"):
        got = request(session.port, "GET", route, tok(session), host=host)
        assert got.status == 421, host


@pytest.mark.parametrize(
    "template",
    [
        "127.0.0.1:{wrong}",
        "localhost:{wrong}",
        "[::1]:{wrong}",
        "127.0.0.1",
        "localhost",
        "127.0.0.1:",
        "127.0.0.1:0{port}",
        "user@127.0.0.1:{port}",
        "127.0.0.1:{port}@evil.example",
        "127.0.0.1:{port}.evil.example",
        "[fe80::1%25eth0]:{port}",
        "[::1%25lo]:{port}",
        "0.0.0.0:{port}",
        "127.0.0.2:{port}",
        "2130706433:{port}",
        "",
        "LOCALHOST.:{port}",
    ],
)
def test_host_with_wrong_port_or_shape_is_421(
    session: EditorSession, template: str
) -> None:
    host = template.format(port=session.port, wrong=session.port + 1)
    got = request(session.port, "GET", "/__edit/", host=host)
    assert got.status == 421, host


def test_missing_host_is_421(session: EditorSession) -> None:
    assert request(session.port, "GET", "/__edit/", host=None).status == 421


@pytest.mark.parametrize("name", ["127.0.0.1", "localhost", "[::1]", "LocalHost"])
def test_the_three_allowed_names_with_the_exact_port_work(
    session: EditorSession, name: str
) -> None:
    got = request(session.port, "GET", "/__edit/", host=f"{name}:{session.port}")
    assert got.status == 200


def test_unit_host_with_whitespace_or_newline_is_refused() -> None:
    for host in ("127.0.0.1:4321 ", " 127.0.0.1:4321", "127.0.0.1:4321\n"):
        assert GUARD.allowed_host(host) is None


def test_duplicate_host_headers_are_refused(session: EditorSession) -> None:
    data = (
        f"GET /__edit/ HTTP/1.1\r\nHost: 127.0.0.1:{session.port}\r\n"
        "Host: evil.example\r\n\r\n"
    ).encode()
    assert raw(session.port, data).status == 400


# ---- methods ----------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"]
)
def test_other_methods_are_405_and_no_cors_headers(
    session: EditorSession, method: str
) -> None:
    got = request(session.port, method, "/__edit/api/state", tok(session))
    assert got.status == 405
    assert not [h for h in got.headers if h.startswith("access-control-")]


# ---- Origin and Sec-Fetch-Site ----------------------------------------------


def test_cross_origin_origin_header_is_403(session: EditorSession) -> None:
    for origin in ("http://evil.example", "null", "https://127.0.0.1", ""):
        got = request(
            session.port,
            "GET",
            "/__edit/api/state",
            {**tok(session), "Origin": origin},
        )
        assert got.status == 403, origin


def test_origin_with_another_port_is_403(session: EditorSession) -> None:
    other = f"http://127.0.0.1:{session.preview_port}"
    got = request(
        session.port, "GET", "/__edit/api/state", {**tok(session), "Origin": other}
    )
    assert got.status == 403


def test_sec_fetch_site_cross_site_is_403(session: EditorSession) -> None:
    for value in ("cross-site", "same-site", "bogus"):
        got = request(
            session.port,
            "GET",
            "/__edit/api/state",
            {**tok(session), "Sec-Fetch-Site": value},
        )
        assert got.status == 403, value


def test_sec_fetch_site_none_is_allowed_for_navigation(
    session: EditorSession,
) -> None:
    got = request(session.port, "GET", "/__edit/", {"Sec-Fetch-Site": "none"})
    assert got.status == 200


def test_post_without_origin_is_403(session: EditorSession) -> None:
    got = request(
        session.port,
        "POST",
        "/__edit/api/op",
        {**tok(session), "Content-Type": "application/json"},
        b"{}",
    )
    assert got.status == 403


# ---- token ------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/__edit/api/state", "/__edit/api/anything"])
def test_api_without_token_is_401_even_for_get(
    session: EditorSession, route: str
) -> None:
    got = request(session.port, "GET", route)
    assert got.status == 401
    assert session.token.encode() not in got.body


def test_wrong_token_of_correct_length_is_401(session: EditorSession) -> None:
    wrong = "A" * len(session.token)
    assert wrong != session.token
    got = request(session.port, "GET", "/__edit/api/state", {"X-RA-Token": wrong})
    assert got.status == 401


def test_empty_and_prefix_tokens_are_401(session: EditorSession) -> None:
    for value in ("", session.token[:-1], session.token + "x"):
        got = request(session.port, "GET", "/__edit/api/state", {"X-RA-Token": value})
        assert got.status == 401, value


def test_non_ascii_token_header_is_401_not_500(session: EditorSession) -> None:
    data = (
        f"GET /__edit/api/state HTTP/1.1\r\nHost: 127.0.0.1:{session.port}\r\n"
        "X-RA-Token: \xe9\xff\r\n\r\n"
    ).encode("latin-1")
    assert raw(session.port, data).status == 401


def test_token_in_query_string_or_cookie_does_not_authenticate(
    session: EditorSession,
) -> None:
    by_query = request(session.port, "GET", f"/__edit/api/state?t={session.token}")
    by_cookie = request(
        session.port, "GET", "/__edit/api/state", {"Cookie": f"t={session.token}"}
    )
    assert by_query.status == 401
    assert by_cookie.status == 401


def test_good_token_gets_state(session: EditorSession) -> None:
    got = request(session.port, "GET", "/__edit/api/state", tok(session))
    assert got.status == 200
    assert got.headers["content-type"] == "application/json"
    assert json.loads(got.body)["config"] == {}


def test_token_differs_between_runs(tmp_path: Path) -> None:
    with running_editor(tmp_path) as one:
        first = one.token
    with running_editor(tmp_path) as two:
        assert two.token != first
    assert len(first) >= 43  # 256 bits of urlsafe base64


# ---- content type, length, caps ---------------------------------------------


def post(
    session: EditorSession,
    extra: dict[str, str],
    body: bytes = b"{}",
    path: str = "/__edit/api/op",
) -> int | None:
    sent = {**tok(session), **extra}
    return request(session.port, "POST", path, sent, body, same_origin=True).status


def test_form_encoded_and_other_content_types_are_415(
    session: EditorSession,
) -> None:
    for kind in (
        "application/x-www-form-urlencoded",
        "text/plain",
        "multipart/form-data; boundary=x",
        "application/json; charset=utf-8",
        "application/jsonx",
        "APPLICATION/JSON",
        "application/octet-stream",
    ):
        assert post(session, {"Content-Type": kind}) == 415, kind


def test_missing_content_type_is_415(session: EditorSession) -> None:
    assert post(session, {}) == 415


def test_missing_content_length_is_411(session: EditorSession) -> None:
    data = (
        f"POST /__edit/api/op HTTP/1.1\r\nHost: 127.0.0.1:{session.port}\r\n"
        f"Origin: http://127.0.0.1:{session.port}\r\n"
        f"X-RA-Token: {session.token}\r\nContent-Type: application/json\r\n\r\n{{}}"
    ).encode()
    assert raw(session.port, data).status == 411


def test_chunked_is_refused(session: EditorSession) -> None:
    assert (
        post(
            session,
            {"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
            b"2\r\n{}\r\n0\r\n\r\n",
        )
        == 400
    )


@pytest.mark.parametrize("length", ["-1", "abc", "1e3", "\xb2", "0x10", "1 2"])
def test_garbage_content_length_is_400(session: EditorSession, length: str) -> None:
    got = post(session, {"Content-Type": "application/json", "Content-Length": length})
    assert got in (400, 411)


def test_json_over_cap_is_413_and_not_read(session: EditorSession) -> None:
    got = post(
        session,
        {"Content-Type": "application/json", "Content-Length": str(64 * 1024 + 1)},
        b"{}",
    )
    assert got == 413


def test_authenticated_post_to_unknown_route_is_404_and_writes_nothing(
    tmp_path: Path,
) -> None:
    with running_editor(tmp_path) as session:
        before = tree_snapshot(tmp_path)
        got = post(
            session, {"Content-Type": "application/json"}, path="/__edit/api/nothing"
        )
        assert got == 404
        assert tree_snapshot(tmp_path) == before


def test_post_without_token_is_401_and_changes_nothing(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        before = tree_snapshot(tmp_path)
        got = request(
            session.port,
            "POST",
            "/__edit/api/op",
            {"Content-Type": "application/json"},
            b"{}",
            same_origin=True,
        )
        assert got.status == 401
        assert tree_snapshot(tmp_path) == before


def test_oversized_headers_are_431(session: EditorSession) -> None:
    got = request(session.port, "GET", "/__edit/", {"X-Pad": "a" * 20000})
    assert got.status == 431


def test_oversized_request_line_is_414(session: EditorSession) -> None:
    got = request(session.port, "GET", "/" + "a" * 20000)
    assert got.status == 414


# ---- reads change nothing ---------------------------------------------------


def test_get_never_modifies_the_file(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        before = tree_snapshot(tmp_path)
        for route in ROUTES + ["/__edit/api/op", "/__edit/api/upload", "/%2e%2e/"]:
            request(session.port, "GET", route, tok(session))
        assert tree_snapshot(tmp_path) == before


# ---- unit table for the pure guard ------------------------------------------

GUARD = Guard(4321, "s3cret-token")


def ok_headers(**overrides: str) -> Message:
    fields = {"Host": "127.0.0.1:4321", "X-RA-Token": "s3cret-token"}
    fields.update(overrides)
    message = Message()
    for key, value in fields.items():
        message[key] = value
    return message


def test_unit_good_get_passes() -> None:
    assert GUARD.check("GET", "/__edit/api/state", ok_headers()) is None


@pytest.mark.parametrize(
    ("method", "path", "extra", "status"),
    [
        ("GET", "/__edit/", {"Host": "evil:4321"}, 421),
        ("DELETE", "/__edit/", {}, 405),
        ("GET", "/__edit/", {"Origin": "http://evil"}, 403),
        ("GET", "/__edit/api/x", {"X-RA-Token": "wrong"}, 401),
        ("POST", "/__edit/api/op", {"Origin": "http://127.0.0.1:4321"}, 415),
    ],
)
def test_unit_table(method: str, path: str, extra: dict[str, str], status: int) -> None:
    verdict = GUARD.check(method, path, ok_headers(**extra))
    assert isinstance(verdict, Verdict)
    assert verdict.status == status


def test_unit_guard_never_raises_on_random_input() -> None:
    rng = SeededRandom(20261025)
    alphabet = "abc:/[]%@.\r\n \t\x00\xe9٣-_0123456789"
    for _ in range(3000):
        message = Message()
        for name in ("Host", "Origin", "Sec-Fetch-Site", "X-RA-Token", "Content-Type"):
            if rng.random() < 0.6:
                message[name] = "".join(
                    rng.choice(alphabet) for _ in range(rng.randrange(0, 30))
                )
        if rng.random() < 0.5:
            message["Content-Length"] = str(rng.randrange(-5, 10**12))
        method = rng.choice(["GET", "POST", "PUT", "get", "", "G\x00T"])
        path = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))
        verdict = GUARD.check(method, path, message)
        assert verdict is None or 400 <= verdict.status < 500


# ---- seeded random-bytes fuzz against a live server -------------------------


def test_fuzz_random_requests_never_500_and_never_write(tmp_path: Path) -> None:
    rng = SeededRandom(20261025)
    with running_editor(tmp_path) as session:
        before = tree_snapshot(tmp_path)
        valid = (
            f"POST /__edit/api/op HTTP/1.1\r\nHost: 127.0.0.1:{session.port}\r\n"
            f"Origin: http://127.0.0.1:{session.port}\r\n"
            f"X-RA-Token: {session.token}\r\nContent-Type: application/json\r\n"
            "Content-Length: 2\r\n\r\n{}"
        ).encode()
        for i in range(250):
            kind = i % 4
            if kind == 0:
                data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 400)))
            elif kind == 1:
                mutated = bytearray(valid)
                for _ in range(rng.randrange(1, 8)):
                    mutated[rng.randrange(len(mutated))] = rng.randrange(256)
                data = bytes(mutated)
            elif kind == 2:
                data = valid[: rng.randrange(1, len(valid))]
            else:
                data = valid.replace(
                    b"POST", rng.choice([b"GET", b"PUT", b"XYZ", b"get", b"\x00"])
                )
            got = raw(session.port, data)
            assert got.status is None or got.status < 500, data
        assert tree_snapshot(tmp_path) == before
        assert (tmp_path / "site.yaml").read_text() == CONFIG_TEXT
        # the server still answers after the abuse
        assert request(session.port, "GET", "/__edit/").status == 200


def test_token_is_compared_with_hmac_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hmac

    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert GUARD.token_ok("s3cret-token") is True
    assert GUARD.token_ok("nope") is False
    assert len(calls) == 2


def test_an_internal_error_is_a_400_never_a_500_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import reach_around.editor.server as server

    def boom(self: object) -> None:
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(server._EditorHandler, "_state", boom)
    with running_editor(tmp_path) as session:
        before = tree_snapshot(tmp_path)
        got = request(
            session.port, "GET", "/__edit/api/state", {"X-RA-Token": session.token}
        )
        assert got.status == 400
        assert b"simulated" not in got.body
        assert tree_snapshot(tmp_path) == before
