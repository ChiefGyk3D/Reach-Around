# SPDX-License-Identifier: Apache-2.0
"""Wire-level header cap, absolute receive deadlines, idle value validation."""

from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest

from editorharness import CONFIG_TEXT, raw, request
from reach_around.cli import main
from reach_around.editor.guard import MAX_HEADER_BYTES
from reach_around.editor.server import (
    MAX_ACTIVE_CONNECTIONS,
    EditorError,
    EditorSession,
)

GOOD = b"GET /__edit/ HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n"


def make_session(
    tmp_path: Path,
    *,
    read_timeout: float = 10.0,
    header_deadline: float = 10.0,
    body_deadline: float = 30.0,
) -> EditorSession:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    session = EditorSession(
        config,
        read_timeout=read_timeout,
        header_deadline=header_deadline,
        body_deadline=body_deadline,
    )
    session.start()
    return session


def read_all(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError:
        pass
    return b"".join(chunks)


def status_of(blob: bytes) -> int | None:
    parts = blob.split(b" ", 2)
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


# ---- 1. the header cap is enforced on the wire --------------------------------


def test_unterminated_header_is_answered_at_the_cap_not_at_the_line_limit(
    tmp_path: Path,
) -> None:
    # 20 KiB is over our cap and under the stdlib's 64 KiB line limit. The
    # header never ends, so only a wire-level cap can answer before the timeout.
    session = make_session(tmp_path)
    try:
        with socket.create_connection(("127.0.0.1", session.port)) as sock:
            sock.settimeout(2)
            sock.sendall(b"GET /__edit/ HTTP/1.1\r\nX-Pad: " + b"a" * 20000)
            blob = read_all(sock)
        assert status_of(blob) == 431
    finally:
        session.shutdown()


def test_many_headers_are_answered_at_the_cap_before_the_block_ends(
    tmp_path: Path,
) -> None:
    # 90 headers of ~1 KiB are ~90 KiB: legal for the stdlib (100 headers, 64 KiB
    # a line), over our cap. The block never ends, so only the wire cap answers.
    session = make_session(tmp_path)
    try:
        block = b"".join(b"X-%d: %s\r\n" % (i, b"v" * 1000) for i in range(90))
        with socket.create_connection(("127.0.0.1", session.port)) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"GET /__edit/ HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % session.port
                + block
            )
            blob = read_all(sock)
        assert status_of(blob) == 431
    finally:
        session.shutdown()


def test_headers_just_under_the_cap_still_work(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    try:
        pad = "a" * (MAX_HEADER_BYTES // 2)
        assert request(session.port, "GET", "/__edit/", {"X-Pad": pad}).status == 200
    finally:
        session.shutdown()


# ---- 2. absolute deadlines ------------------------------------------------------


def test_dripped_headers_are_cut_off_by_the_absolute_deadline(tmp_path: Path) -> None:
    # Each byte arrives well inside the per-read timeout; only the absolute
    # deadline can end this connection.
    session = make_session(tmp_path, read_timeout=5.0, header_deadline=0.6)
    try:
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", session.port)) as sock:
            sock.settimeout(5)
            closed = False
            for byte in b"GET /__edit/ HTTP/1.1\r\nHost: 127.0.0.1\r\nX-A: b\r\n" * 20:
                try:
                    sock.sendall(bytes([byte]))
                except OSError:
                    closed = True
                    break
                time.sleep(0.05)
                if time.monotonic() - started > 4:
                    break
            if not closed:
                assert read_all(sock) == b""
        assert time.monotonic() - started < 4
    finally:
        session.shutdown()


def test_drip_feeders_cannot_hold_every_worker_slot(tmp_path: Path) -> None:
    session = make_session(tmp_path, read_timeout=5.0, header_deadline=0.5)
    held: list[socket.socket] = []
    try:
        for _ in range(MAX_ACTIVE_CONNECTIONS):
            sock = socket.create_connection(("127.0.0.1", session.port))
            sock.sendall(b"GET /")
            held.append(sock)
        end = time.monotonic() + 1.0
        while time.monotonic() < end:  # keep dripping, always inside read_timeout
            for sock in held:
                try:
                    sock.sendall(b"a")
                except OSError:
                    pass
            time.sleep(0.1)
        # Every dripper is now closed, so a legitimate request is served.
        assert raw(session.port, GOOD % session.port, timeout=2).status == 200
    finally:
        for sock in held:
            sock.close()
        session.shutdown()


def test_a_prompt_request_succeeds_within_the_deadline(tmp_path: Path) -> None:
    session = make_session(tmp_path, header_deadline=0.5)
    try:
        assert request(session.port, "GET", "/__edit/").status == 200
    finally:
        session.shutdown()


def test_body_deadline_cuts_off_a_dripped_body(tmp_path: Path) -> None:
    session = make_session(tmp_path, read_timeout=5.0, body_deadline=0.5)
    try:
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", session.port)) as sock:
            sock.settimeout(5)
            # An unauthenticated POST is refused, then the server drains a little
            # of the declared body; the drain must not outlast the body deadline.
            sock.sendall(
                b"POST /__edit/api/op HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                b"Content-Type: application/json\r\nContent-Length: 500\r\n\r\n"
                % session.port
            )
            for _ in range(40):
                try:
                    sock.sendall(b"x")
                except OSError:
                    break
                time.sleep(0.05)
            read_all(sock)
        assert time.monotonic() - started < 4
    finally:
        session.shutdown()


# ---- 3. the idle value is validated -------------------------------------------

BAD_IDLE = [
    "nan",
    "inf",
    "-inf",
    "0",
    "-1",
    "1e308",
    "1e9",
    str(24 * 60 + 1),
]


@pytest.mark.parametrize("value", BAD_IDLE)
def test_cli_rejects_bad_idle_minutes(
    value: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["edit", "--config", str(config), "--idle-minutes", value])
    assert caught.value.code == 2
    assert "idle-minutes" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, 1e308, 24 * 3600 + 1.0],
)
def test_session_rejects_bad_idle_seconds(tmp_path: Path, value: float) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    with pytest.raises(EditorError):
        EditorSession(config, idle_seconds=value)


def test_session_accepts_the_upper_bound(tmp_path: Path) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    assert EditorSession(config, idle_seconds=24 * 3600.0).idle_seconds == 24 * 3600.0
