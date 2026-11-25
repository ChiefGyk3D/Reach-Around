# SPDX-License-Identifier: Apache-2.0
"""Fake HTTP and TCP servers must not leave threads behind (#859).

A server thread that outlives its test can print a socketserver traceback into
whatever test runs next on the same pytest-xdist worker, which then fails on an
unrelated stderr assertion (tests/test_report.py flaked this way). These tests
count threads before and after, so a server left running fails here.

Invariants of the fake servers: every accepted socket is closed exactly once,
close() takes one global deadline (not one per thread), and a failure in a
handler (or in starting one) fails close() unless it is the client hanging up
while its request was being read.
"""

from __future__ import annotations

import contextlib
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from test_make_ours_days_http import GOOD, Local, reply
from test_net_egress import DripServer, _silent, _slow_body


def leaked(before: set[threading.Thread], wait: float = 1.0) -> list[threading.Thread]:
    """Every thread started since ``before`` that is still alive.

    Counted by identity, not by name, so a server that leaks an unnamed thread is
    caught too. A bounded settle loop gives a thread that is just exiting up to
    ``wait`` seconds.
    """
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


@pytest.mark.parametrize("failure", [RuntimeError, OSError, MemoryError])
def test_local_start_failure_drops_one_request_and_close_still_works(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    real_start = threading.Thread.start
    failed: list[str] = []

    def start(self: threading.Thread) -> None:
        if self.name == "fake-http-handler" and not failed:
            failed.append(self.name)
            raise failure("can't start new thread")
        real_start(self)

    before = set(threading.enumerate())
    loc = Local(reply(GOOD))
    monkeypatch.setattr(threading.Thread, "start", start)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(5)
        assert sock.recv(1) == b""  # the request socket was closed, not leaked
        with pytest.raises(AssertionError, match="start new thread"):
            loc.close()  # the infrastructure failure is not swallowed
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


@pytest.mark.parametrize("failure", [RuntimeError, OSError, MemoryError])
def test_drip_start_failure_closes_the_request_and_close_still_works(
    monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    real_start = threading.Thread.start
    failed: list[str] = []

    def start(self: threading.Thread) -> None:
        if self.name == "fake-drip-worker" and not failed:
            failed.append(self.name)
            raise failure("can't start new thread")
        real_start(self)

    before = set(threading.enumerate())
    server = DripServer(_silent)
    monkeypatch.setattr(threading.Thread, "start", start)
    client = socket.create_connection(("127.0.0.1", server.port))
    try:
        client.settimeout(5)
        assert client.recv(1) == b""  # server side closed it
        with pytest.raises(AssertionError, match="start new thread"):
            server.close()
    finally:
        client.close()
    assert failed
    assert leaked(before, wait=0) == []


class _Interrupt(BaseException):
    """Stands in for KeyboardInterrupt or SystemExit during a thread start."""


def _capture_thread_deaths(monkeypatch: pytest.MonkeyPatch) -> list[BaseException]:
    deaths: list[BaseException] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: deaths.append(args.exc_value)
    )
    return deaths


def test_local_interrupt_during_start_closes_the_request_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_start = threading.Thread.start
    failed: list[str] = []

    def start(self: threading.Thread) -> None:
        if self.name == "fake-http-handler" and not failed:
            failed.append(self.name)
            raise _Interrupt
        real_start(self)

    deaths = _capture_thread_deaths(monkeypatch)
    loc = Local(reply(GOOD))
    monkeypatch.setattr(threading.Thread, "start", start)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(5)
        assert sock.recv(1) == b""  # closed, not leaked
        loc.thread.join(5)
        assert [type(d) for d in deaths] == [_Interrupt]  # it was not swallowed
        loc.close()  # an interrupt kills the accept loop; teardown still works
    finally:
        sock.close()


def test_drip_interrupt_during_start_closes_the_request_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_start = threading.Thread.start
    failed: list[str] = []

    def start(self: threading.Thread) -> None:
        if self.name == "fake-drip-worker" and not failed:
            failed.append(self.name)
            raise _Interrupt
        real_start(self)

    deaths = _capture_thread_deaths(monkeypatch)
    server = DripServer(_silent)
    monkeypatch.setattr(threading.Thread, "start", start)
    client = socket.create_connection(("127.0.0.1", server.port))
    try:
        client.settimeout(5)
        assert client.recv(1) == b""
        server._thread.join(5)
        assert [type(d) for d in deaths] == [_Interrupt]
        assert server._conns == [] and server._workers == []
    finally:
        client.close()
        with pytest.raises(AssertionError, match="could not start a worker"):
            server.close()


def test_local_close_fails_when_a_handler_raises() -> None:
    """A handler's own failure must not vanish with its thread (#859)."""

    def broken(_r: BaseHTTPRequestHandler) -> None:
        raise ValueError("unexpected credentials")

    loc = Local(broken)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(5)
        assert sock.recv(1) == b""
        with pytest.raises(AssertionError, match="unexpected credentials"):
            loc.close()
        with pytest.raises(AssertionError, match="unexpected credentials"):
            loc.close()  # idempotent: the same outcome again
    finally:
        sock.close()


def test_local_close_ignores_a_client_that_resets_mid_request() -> None:
    """A client hang-up raised while the request is read is expected noise."""
    loc = Local(reply(GOOD))
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    sock.sendall(b"GET / HT")  # the handler is reading the request line
    time.sleep(0.2)
    # SO_LINGER 0: close() sends an RST, so the handler's read raises
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        c.thread.is_alive() for c in loc.srv.conns.values()
    ):
        time.sleep(0.05)
    assert [c.hung_up for c in loc.srv.conns.values()] != [None]  # raised on read
    loc.close()


def test_local_close_fails_when_the_handler_itself_raises_a_hang_up() -> None:
    """The same exception type, raised by the handler, is a failure (by origin)."""

    def broken(_r: BaseHTTPRequestHandler) -> None:
        raise ConnectionResetError("not from the client")

    loc = Local(broken)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(5)
        assert sock.recv(1) == b""
        with pytest.raises(AssertionError, match="not from the client"):
            loc.close()
    finally:
        sock.close()


def test_local_close_fails_when_a_handler_hits_a_socket_error_while_closing() -> None:
    """Closing does not excuse an OSError the handler raised (no 'closing' flag)."""
    entered = threading.Event()
    release = threading.Event()

    def slow_then_fail(_r: BaseHTTPRequestHandler) -> None:
        entered.set()
        release.wait(5)
        raise OSError("handler bug")

    loc = Local(slow_then_fail)
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert entered.wait(5)
        threading.Timer(0.2, release.set).start()
        with pytest.raises(AssertionError, match="handler bug"):
            loc.close()
    finally:
        release.set()
        sock.close()


def test_local_close_deadline_bounds_a_stalled_accept_loop() -> None:
    """join_timeout bounds shutdown() too, not just the thread joins."""
    gate = threading.Event()
    entered = threading.Event()
    loc = Local(reply(GOOD))

    def stall() -> None:
        entered.set()
        gate.wait(10)

    loc.srv.service_actions = stall  # type: ignore[method-assign]
    try:
        assert entered.wait(5)  # the accept loop is now stuck
        start = time.monotonic()
        with pytest.raises(AssertionError, match="did not stop"):
            loc.close(join_timeout=0.3)
        assert time.monotonic() - start < 2.0
        with pytest.raises(AssertionError, match="did not stop"):
            loc.close(join_timeout=0.1)  # again: still reported, still bounded
        helpers = [t for t in threading.enumerate() if t.name == "fake-http-shutdown"]
        assert len(helpers) == 1  # one tracked helper, not one per call
    finally:
        gate.set()
        loc.close()  # the tracked helper finishes; the outcome is clean


def test_local_close_joins_a_handler_started_while_it_runs() -> None:
    """The accept loop is stopped before handlers are snapshotted (no late miss)."""
    entered = threading.Event()
    gate = threading.Event()
    loc = Local(reply(GOOD))
    real = loc.srv.process_request

    def paused(request: socket.socket, client_address: object) -> None:
        entered.set()
        gate.wait(5)  # the accept thread is mid-dispatch while close() starts
        real(request, client_address)

    loc.srv.process_request = paused  # type: ignore[method-assign]
    sock = socket.create_connection(("127.0.0.1", loc.srv.server_address[1]))
    try:
        assert entered.wait(5)
        threading.Timer(0.3, gate.set).start()  # released after close() began
        loc.close()  # blocks until the late handler exists, wakes it, joins it
        late = [c.thread for c in loc.srv.conns.values()]
        assert len(late) == 1
        assert not late[0].is_alive()
    finally:
        gate.set()
        sock.close()


def test_drip_server_drops_finished_workers() -> None:
    server = DripServer(lambda conn: conn.sendall(b"x"))
    try:
        for _ in range(5):
            with socket.create_connection(("127.0.0.1", server.port)) as c:
                c.settimeout(5)
                assert c.recv(1) == b"x"
        deadline = time.monotonic() + 3
        while server._workers and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server._workers == []  # no connection follows to prune them
    finally:
        server.close()
