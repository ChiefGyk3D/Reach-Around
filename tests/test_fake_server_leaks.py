# SPDX-License-Identifier: Apache-2.0
"""Fake HTTP and TCP servers must not leave threads behind (#859).

A server thread that outlives its test can print a socketserver traceback into
whatever test runs next on the same pytest-xdist worker, which then fails on an
unrelated stderr assertion (tests/test_report.py flaked this way). These tests
count threads before and after, so a server left running fails here.

Invariants of the fake servers: every accepted socket is closed exactly once,
close() takes one global deadline (not one per thread), and every helper thread
is named ``fake-*``.
"""

from __future__ import annotations

import contextlib
import os
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
    entered = threading.Event()

    def run(conn: socket.socket) -> None:
        entered.set()
        assert behavior is not None
        behavior(conn)

    server = DripServer(None if behavior is None else run)
    client = socket.create_connection(("127.0.0.1", server.port))
    try:
        if behavior is not None:
            assert entered.wait(5)  # accepted, and the behavior is blocked
        server.close()
    finally:
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


def test_local_handler_drops_a_stalled_client_after_the_timeout() -> None:
    before = set(threading.enumerate())
    loc = Local(reply(GOOD), handler_timeout=0.3)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HT")  # never finished; close() is not called yet
        sock.settimeout(5)
        assert sock.recv(1) == b""  # the server hung up on its own
        assert leaked(before) == [loc.thread]  # handler gone, accept loop alive
    finally:
        sock.close()
        loc.close()


def test_local_start_failure_drops_one_request_and_close_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_start = threading.Thread.start
    failed: list[str] = []

    def start(self: threading.Thread) -> None:
        if self.name == "fake-http-handler" and not failed:
            failed.append(self.name)
            raise RuntimeError("can't start new thread")
        real_start(self)

    before = set(threading.enumerate())
    loc = Local(reply(GOOD))
    monkeypatch.setattr(threading.Thread, "start", start)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(5)
        assert sock.recv(1) == b""  # the request socket was closed, not leaked
        loc.close()
    finally:
        sock.close()
    assert failed
    assert leaked(before, wait=0) == []


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
    entered = threading.Event()

    def stuck(_conn: socket.socket) -> None:
        entered.set()
        release.wait(10)

    server = DripServer(stuck)
    client = socket.create_connection(("127.0.0.1", server.port))
    try:
        assert entered.wait(5)
        with pytest.raises(AssertionError, match="did not stop"):
            server.close(join_timeout=0.3)
    finally:
        release.set()
        client.close()
        server.close()


def test_drip_close_uses_one_deadline_for_all_workers() -> None:
    release = threading.Event()
    entered = threading.Semaphore(0)

    def stuck(_conn: socket.socket) -> None:
        entered.release()
        release.wait(10)

    server = DripServer(stuck)
    clients = [socket.create_connection(("127.0.0.1", server.port)) for _ in range(6)]
    try:
        for _ in clients:
            assert entered.acquire(timeout=5)
        start = time.monotonic()
        with pytest.raises(AssertionError, match="did not stop"):
            server.close(join_timeout=0.5)
        assert time.monotonic() - start < 2.0  # not 6 * 0.5 plus
    finally:
        release.set()
        for c in clients:
            c.close()
        server.close()


def test_drip_server_closes_finished_connections() -> None:
    seen: list[socket.socket] = []

    def send(conn: socket.socket) -> None:
        seen.append(conn)
        conn.sendall(b"x")

    server = DripServer(send)
    try:
        for _ in range(5):
            with socket.create_connection(("127.0.0.1", server.port)) as c:
                c.settimeout(5)
                assert c.recv(1) == b"x"
        deadline = time.monotonic() + 3
        while (server._conns or len(seen) < 5) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server._conns == []  # bounded bookkeeping
        assert len(seen) == 5
        assert [s.fileno() for s in seen] == [-1] * 5  # and the sockets are closed
    finally:
        server.close()


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_drip_server_does_not_leak_fds() -> None:
    server = DripServer(lambda conn: conn.sendall(b"x"))
    try:
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(40):
            with socket.create_connection(("127.0.0.1", server.port)) as c:
                c.settimeout(5)
                assert c.recv(1) == b"x"
        time.sleep(0.3)
        assert len(os.listdir("/proc/self/fd")) - before < 10  # 40 leaked would fail
    finally:
        server.close()


def test_drip_start_failure_closes_the_request_and_close_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_start = threading.Thread.start
    failed: list[str] = []

    def start(self: threading.Thread) -> None:
        if self.name == "fake-drip-worker" and not failed:
            failed.append(self.name)
            raise RuntimeError("can't start new thread")
        real_start(self)

    before = set(threading.enumerate())
    server = DripServer(_silent)
    monkeypatch.setattr(threading.Thread, "start", start)
    client = socket.create_connection(("127.0.0.1", server.port))
    try:
        client.settimeout(5)
        assert client.recv(1) == b""  # server side closed it
        server.close()
    finally:
        client.close()
    assert failed
    assert leaked(before, wait=0) == []
