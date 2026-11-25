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
    """Threads started since ``before`` that are still alive after ``wait`` seconds."""
    deadline = time.monotonic() + wait
    while True:
        left = [t for t in threading.enumerate() if t not in before and t.is_alive()]
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.05)


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
