# SPDX-License-Identifier: Apache-2.0
"""Fake HTTP and TCP servers must not leave threads behind (#859).

A server thread that outlives its test can print a socketserver traceback into
whatever test runs next on the same pytest-xdist worker, which then fails on an
unrelated stderr assertion (tests/test_report.py flaked this way). These tests
count threads before and after, so a server left running fails here.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from test_make_ours_days_http import GOOD, Local, reply
from test_net_egress import DripServer, _silent, _slow_body


def leaked(before: set[threading.Thread], wait: float = 1.0) -> list[threading.Thread]:
    """Fake-server threads started since ``before`` that are still alive.

    Only threads this module's servers create (named ``fake-*``) count, so a pytest
    plugin or other background thread cannot make the check flake. A bounded
    settle loop gives a thread that is just exiting up to ``wait`` seconds.
    """
    deadline = time.monotonic() + wait
    while True:
        left = [
            t
            for t in threading.enumerate()
            if t not in before and t.is_alive() and t.name.startswith("fake-")
        ]
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.05)


def test_an_unrelated_thread_is_not_a_leak() -> None:
    before = set(threading.enumerate())
    gate = threading.Event()
    other = threading.Thread(target=gate.wait, args=(5,), daemon=True, name="plugin-x")
    other.start()
    try:
        assert leaked(before, wait=0) == []
    finally:
        gate.set()
        other.join(5)


def test_the_detector_sees_a_server_left_running() -> None:
    before = set(threading.enumerate())
    loc = Local(reply(GOOD))
    try:
        assert leaked(before, wait=0.2), "a running server must count as a leak"
    finally:
        loc.close()
    assert leaked(before) == []


def test_local_server_leaves_no_slow_handler_behind() -> None:
    before = set(threading.enumerate())

    entered = threading.Event()

    def slow(r: BaseHTTPRequestHandler) -> None:
        entered.set()
        time.sleep(0.5)
        with contextlib.suppress(OSError):
            reply(GOOD)(r)

    loc = Local(slow)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert entered.wait(5)  # the handler thread is now inside slow()
    sock.close()  # the client gives up, as the timeout test does
    loc.close()  # must wait for the handler, not abandon it
    assert leaked(before, wait=0) == []


@pytest.mark.parametrize("behavior", [_silent, _slow_body, None])
def test_drip_server_close_stops_every_thread(behavior) -> None:  # type: ignore[no-untyped-def]
    before = set(threading.enumerate())
    server = DripServer(behavior)
    if behavior is not None:
        client = socket.create_connection(("127.0.0.1", server.port))
        time.sleep(0.2)  # accepted, and the behavior is blocked
    server.close()
    if behavior is not None:
        client.close()
    assert leaked(before, wait=0) == []


def test_local_close_returns_with_an_incomplete_request_open() -> None:
    """A client that sends half a request line and stalls must not hang close()."""
    before = set(threading.enumerate())
    loc = Local(reply(GOOD))
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HT")  # never finished
        time.sleep(0.3)  # the handler is now blocked reading the request line
        start = time.monotonic()
        loc.close()
        assert time.monotonic() - start < 3
    finally:
        sock.close()
    assert leaked(before, wait=0) == []


def test_local_handlers_have_a_socket_timeout() -> None:
    loc = Local(reply(GOOD))
    try:
        assert getattr(loc.srv.RequestHandlerClass, "timeout", None) is not None
    finally:
        loc.close()


def test_local_close_fails_loudly_when_a_handler_is_stuck() -> None:
    release = threading.Event()
    entered = threading.Event()

    def stuck(_r: BaseHTTPRequestHandler) -> None:
        entered.set()
        release.wait(10)  # ignores the socket: shutdown cannot wake it

    loc = Local(stuck)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert entered.wait(5)
        with pytest.raises(AssertionError, match="did not stop"):
            loc.close(join_timeout=0.3)
    finally:
        release.set()
        sock.close()
        loc.close()


def test_drip_close_fails_loudly_when_a_worker_is_stuck() -> None:
    release = threading.Event()
    server = DripServer(lambda _conn: release.wait(10) and None)
    client = socket.create_connection(("127.0.0.1", server.port))
    try:
        time.sleep(0.2)
        with pytest.raises(AssertionError, match="did not stop"):
            server.close(join_timeout=0.3)
    finally:
        release.set()
        client.close()
        server.close()


def test_drip_server_forgets_finished_connections() -> None:
    server = DripServer(lambda conn: conn.sendall(b"x"))
    try:
        for _ in range(5):
            socket.create_connection(("127.0.0.1", server.port)).close()
            time.sleep(0.05)
        deadline = time.monotonic() + 3
        while server._conns and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server._conns == []  # dropped once the behavior returned
        # workers prune on accept
        assert len(server._workers) <= 2
    finally:
        server.close()
