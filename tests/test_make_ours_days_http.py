# SPDX-License-Identifier: Apache-2.0
"""scripts/make-ours-days.py: the HTTP runner against a loopback server, and
the hardening paths (caps, redirects, file creation). 127.0.0.1 only, synthetic
data, no real network."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest

from test_make_ours_days import FIX, TODAY, Fake, go, mod

Handler = Callable[[BaseHTTPRequestHandler], None]
GOOD = (FIX / "views.json").read_text()
ENV = {
    "RA_D1_TOKEN": "tok-x",
    "RA_D1_ACCOUNT_ID": "a" * 32,
    "RA_D1_DATABASE_ID": "b" * 32,
}


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "private"
    d.mkdir(mode=0o700)
    return d


_HANG_UPS = (
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
    TimeoutError,
)


class _Conn:
    """One handled connection: its thread, its socket and what went wrong."""

    def __init__(self, sock: socket.socket, run: Callable[[_Conn], None]) -> None:
        self.sock = sock
        self.thread = threading.Thread(
            target=run, args=(self,), daemon=True, name="fake-http-handler"
        )
        self.hung_up: BaseException | None = None  # raised reading the client
        self.error: BaseException | None = None  # anything else: a real failure


class _ClientReads:
    """Wraps the request reader: a hang-up raised here came from the client."""

    def __init__(self, raw: Any, conn: _Conn) -> None:
        self._raw = raw
        self._conn = conn

    def _guard(self, call: Callable[..., Any], *args: Any) -> Any:
        try:
            return call(*args)
        except _HANG_UPS as exc:
            self._conn.hung_up = exc
            raise

    def read(self, *args: Any) -> Any:
        return self._guard(self._raw.read, *args)

    def readline(self, *args: Any) -> Any:
        return self._guard(self._raw.readline, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class _TrackingServer(ThreadingHTTPServer):
    """Handlers are our own daemon threads, one record per connection.

    socketserver's block_on_close joins without a timeout, which would hang the
    run on a handler stuck in a read; here Local.close() wakes every open
    socket and joins with a deadline. A handler failure is kept on its record
    and fails close(), unless it is the client hanging up mid-request.
    """

    daemon_threads = True
    block_on_close = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.conns: dict[socket.socket, _Conn] = {}
        self.start_errors: list[BaseException] = []  # a handler thread never ran

    def process_request(self, request: Any, client_address: Any) -> None:
        conn = _Conn(request, lambda c: self._handle(c, client_address))
        # drop finished, clean connections; a failed one stays for close()
        self.conns = {
            s: c for s, c in self.conns.items() if c.error or c.thread.is_alive()
        }
        self.conns[request] = conn
        try:
            conn.thread.start()
        except BaseException:
            del self.conns[request]
            raise  # socketserver closes the request and calls handle_error

    def _handle(self, conn: _Conn, client_address: Any) -> None:
        try:
            self.finish_request(conn.sock, client_address)
        except Exception as exc:
            if exc is not conn.hung_up:  # only a client-side read hang-up is noise
                conn.error = exc
        finally:
            self.shutdown_request(conn.sock)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Only process_request failures arrive here (see _handle)."""
        exc = sys.exception()
        if exc is not None:
            self.start_errors.append(exc)


class Local:
    """A loopback server whose answer a test supplies; counts hits."""

    def __init__(self, handler: Handler, handler_timeout: float = 5) -> None:
        self.hits = 0
        self.auth: list[str | None] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            timeout = handler_timeout  # a stalled client cannot hold a handler

            def setup(self) -> None:
                super().setup()
                conn = outer.srv.conns[self.request]
                self.rfile = cast(Any, _ClientReads(self.rfile, conn))

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                outer.hits += 1
                outer.auth.append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                handler(self)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                outer.hits += 1
                outer.auth.append(self.headers.get("Authorization"))
                handler(self)

            def log_message(self, *_a: object) -> None:
                pass

        self.srv = _TrackingServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(
            target=self.srv.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
            name="fake-http-accept",
        )
        self.thread.start()
        self._stopper: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.srv.server_address[1]}/{{acct}}/{{db}}"

    def close(self, join_timeout: float = 5.0) -> None:
        """Stop the server and every handler, or fail the test loudly (#859).

        One deadline bounds the call. The accept loop stops first (shutdown
        runs in one retained helper, so a stalled loop cannot hold close() and
        repeated calls never start another), then the handlers are snapshotted,
        woken and joined. Calling close() again returns the same outcome.
        """
        deadline = time.monotonic() + join_timeout
        if self._stopper is None:
            self._stopper = threading.Thread(
                target=self.srv.shutdown, daemon=True, name="fake-http-shutdown"
            )
            self._stopper.start()
        for t in (self._stopper, self.thread):
            t.join(max(0.0, deadline - time.monotonic()))
        stuck = [t for t in (self._stopper, self.thread) if t.is_alive()]
        if not stuck:  # no new handler can start now: snapshot, wake, join
            self.srv.server_close()
            conns = list(self.srv.conns.values())
            for c in conns:  # a blocked read sees EOF; writes still work, so
                with contextlib.suppress(OSError):  # waking adds no error of its own
                    c.sock.shutdown(socket.SHUT_RD)
            for c in conns:
                c.thread.join(max(0.0, deadline - time.monotonic()))
            stuck = [c.thread for c in conns if c.thread.is_alive()]
        if stuck:
            raise AssertionError(
                f"fake HTTP server threads did not stop: {[t.name for t in stuck]}"
            )
        errors = [*self.srv.start_errors, *(c.error for c in conns if c.error)]
        if errors:
            raise AssertionError(
                f"fake HTTP server failed: {[repr(e) for e in errors]}"
            ) from errors[0]


Serve = Callable[[Handler], Local]


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Iterator[Serve]:
    made: list[Local] = []

    def make(handler: Handler) -> Local:
        loc = Local(handler)
        made.append(loc)
        monkeypatch.setattr(mod, "API", loc.url)
        return loc

    yield make
    for loc in made:
        loc.close()


def reply(body: str | bytes, code: int = 200) -> Handler:
    data = body.encode() if isinstance(body, str) else body

    def h(r: BaseHTTPRequestHandler) -> None:
        r.send_response(code)
        r.send_header("Content-Type", "application/json")
        r.send_header("Content-Length", str(len(data)))
        r.end_headers()
        r.wfile.write(data)

    return h


def raw(data: bytes) -> Handler:
    def h(r: BaseHTTPRequestHandler) -> None:
        r.wfile.write(data)
        r.wfile.flush()
        r.close_connection = True

    return h


def ask() -> list[dict[str, Any]]:
    run = mod.http_runner("tok-x", "a" * 32, "b" * 32)
    rows: list[dict[str, Any]] = run("SELECT 1", [])
    return rows


def test_runner_reads_rows_and_sends_bearer(serve: Serve) -> None:
    loc = serve(reply(GOOD))
    assert ask()[0]["views"] == 100
    assert loc.auth == ["Bearer tok-x"]


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_not_followed_and_token_not_forwarded(serve: Serve, code: int) -> None:
    second = serve(reply(GOOD))
    target = second.url.format(acct="x", db="y")

    def redirect(r: BaseHTTPRequestHandler) -> None:
        r.send_response(code)
        r.send_header("Location", target)
        r.send_header("Content-Length", "0")
        r.end_headers()

    first = serve(redirect)  # API now points at the redirector
    with pytest.raises(mod.QueryFailed):
        ask()
    assert first.hits == 1
    assert second.hits == 0
    assert second.auth == []


def test_garbage_status_line_is_a_query_failure(serve: Serve) -> None:
    serve(raw(b"garbage\r\n\r\n"))
    with pytest.raises(mod.QueryFailed):
        ask()


def test_truncated_chunked_body_is_a_query_failure(serve: Serve) -> None:
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    serve(raw(head + b'ff\r\n{"result"\r\n'))
    with pytest.raises(mod.QueryFailed):
        ask()


def test_main_maps_http_exception_to_3_without_traceback(
    out_dir: Path, serve: Serve, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(raw(b"garbage\r\n\r\n"))
    argv = ["--out", str(out_dir), "--since", "2026-11-01", "--until", "2026-11-02"]
    assert mod.main(argv, runner=None, today=TODAY, env=ENV) == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err and "tok-x" not in err
    assert list(out_dir.iterdir()) == []


def test_body_over_cap_is_refused(serve: Serve) -> None:
    pad = b" " * (mod.MAX_BODY + 10)  # valid JSON, only too large
    serve(reply(GOOD.encode() + pad))
    with pytest.raises(mod.QueryFailed, match="too large"):
        ask()


def test_timeout_is_enforced(serve: Serve, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(r: BaseHTTPRequestHandler) -> None:
        time.sleep(1.5)
        try:
            reply(GOOD)(r)  # a valid answer, but too late
        except OSError:
            # The client already gave up (that is the point of the test). The
            # late write must not print a traceback from this server thread
            # into whatever test runs by then (CI flake: it landed in the
            # stderr check of tests/test_platforms_cli.py).
            pass

    monkeypatch.setattr(mod, "TIMEOUT", 0.3)
    serve(slow)
    with pytest.raises(mod.QueryFailed):
        ask()


def _answer(**block: object) -> str:
    base: dict[str, object] = {"results": [], "success": True, "meta": {}}
    base.update(block)
    return json.dumps({"success": True, "result": [base]})


def test_rows_written_or_changed_db_in_meta_is_refused() -> None:
    with pytest.raises(mod.QueryFailed):
        mod.parse_response(_answer(meta={"rows_written": 1}))
    with pytest.raises(mod.QueryFailed):
        mod.parse_response(_answer(meta={"changed_db": True}))
    assert (
        mod.parse_response(_answer(meta={"rows_written": 0, "changed_db": False})) == []
    )


def test_max_rows_cap() -> None:
    ok = [{"day": "2026-11-01", "views": 1}] * mod.MAX_ROWS
    assert len(mod.parse_response(_answer(results=ok))) == mod.MAX_ROWS
    with pytest.raises(mod.QueryFailed):
        mod.parse_response(_answer(results=[*ok, ok[0]]))


def test_bool_count_is_refused(out_dir: Path) -> None:
    fake = Fake()
    fake.views = [{"day": "2026-11-01", "views": True}]
    assert go(out_dir, fake) == 3
    assert list(out_dir.iterdir()) == []


def test_window_boundary_31_ok_32_refused(out_dir: Path) -> None:
    ok = ["--out", str(out_dir), "--since", "2026-10-01", "--until", "2026-10-31"]
    month = Fake()
    month.views, month.clicks = [{"day": "2026-10-15", "views": 1}], []
    assert mod.main(ok, runner=month, today=TODAY, env={}) == 0  # 31 days
    bad = ["--out", str(out_dir), "--since", "2026-09-30", "--until", "2026-10-31"]
    fake = Fake()
    assert mod.main(bad, runner=fake, today=TODAY, env={}) == 2  # 32 days
    assert fake.seen == []


@pytest.mark.parametrize("text", ["2026-W44-7", "20261101", "2026-11-1", "2026-11-01 "])
def test_day_text_must_round_trip(out_dir: Path, text: str) -> None:
    fake = Fake()
    fake.views = [{"day": text, "views": 1}]
    assert go(out_dir, fake) == 3
    argv = ["--out", str(out_dir), "--since", text, "--until", "2026-11-02"]
    assert mod.main(argv, runner=Fake(), today=TODAY, env={}) == 2


def test_collect_rejects_iso_week_day_directly() -> None:
    rows = [{"day": "2026-W44-7", "views": 1}]
    with pytest.raises(mod.QueryFailed):
        mod.collect(rows, "views", date(2026, 11, 1), date(2026, 11, 2))


def test_write_failure_removes_the_partial_file(tmp_path: Path) -> None:
    p = tmp_path / "f.csv"
    with pytest.raises(UnicodeEncodeError):
        mod.write_new(p, "ok\udc80")
    assert not p.exists()


def test_fdopen_failure_closes_fd_and_removes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "f.csv"
    before = set(os.listdir("/proc/self/fd"))

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("no")

    monkeypatch.setattr(mod.os, "fdopen", boom)
    with pytest.raises(OSError, match="no"):
        mod.write_new(p, "x")
    assert not p.exists()
    assert set(os.listdir("/proc/self/fd")) <= before


def test_write_new_never_truncates_or_follows(tmp_path: Path) -> None:
    p = tmp_path / "f.csv"
    p.write_text("keep")
    with pytest.raises(FileExistsError):
        mod.write_new(p, "new")
    assert p.read_text() == "keep"
    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "l.csv"
    link.symlink_to(victim)
    with pytest.raises(OSError, match="Errno"):
        mod.write_new(link, "new")
    assert victim.read_text() == "keep"


def test_file_appearing_after_the_exists_check_is_kept(out_dir: Path) -> None:
    target = out_dir / "ours-days-2026-11-01_2026-11-02.csv"

    class Racy(Fake):
        def __call__(self, sql: str, params: list[object]) -> list[dict[str, object]]:
            target.write_text("someone else")  # appears after exists() ran
            return super().__call__(sql, params)

    assert go(out_dir, Racy()) == 3
    assert target.read_text() == "someone else"


def test_bad_beacons_path_fails_before_query_or_write(
    out_dir: Path, tmp_path: Path
) -> None:
    fake = Fake()
    assert go(out_dir, fake, "--beacons", str(tmp_path / "missing.csv")) == 2
    assert go(out_dir, fake, "--beacons", str(tmp_path)) == 2  # a directory
    assert fake.seen == []
    assert list(out_dir.iterdir()) == []


def test_all_days_empty_fails_closed_and_writes_nothing(out_dir: Path) -> None:
    fake = Fake()
    fake.views, fake.clicks = [], []
    assert go(out_dir, fake) == 3
    assert list(out_dir.iterdir()) == []


def test_guard_ascii_whitespace_only() -> None:
    guarded = mod.select_only(lambda _s, _p: [])
    for bad in (" SELECT 1", "SELECT 1", " SELECT 1", "SELECT 1"):
        with pytest.raises(mod.Refused):
            guarded(bad, [])
    assert guarded("\t\nSELECT\t1", []) == []


def test_unexpected_exception_exits_3_without_message(
    out_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(_sql: str, _params: list[object]) -> list[dict[str, object]]:
        raise RuntimeError("LEAKED-TEXT")

    assert go(out_dir, boom) == 3
    said = capsys.readouterr()
    assert "LEAKED-TEXT" not in said.out + said.err
