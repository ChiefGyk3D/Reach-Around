# SPDX-License-Identifier: Apache-2.0
"""The local editor's two loopback listeners and their lifecycle.

``EditorSession`` owns the editor listener (the UI and its API) and the preview
listener (a second port, so the preview never shares an origin with the
editor). Both bind ``127.0.0.1`` only, on random ports. Nothing here opens a
browser: the URL, with the session token in the fragment, goes to the terminal
and nowhere else.

Routes are limited to the shell, the static files, ``GET /__edit/api/state``,
``GET /__edit/api/version``, ``POST /__edit/api/op`` and 404 for everything
else. A refused or malformed request never writes and never produces a 5xx:
the guard runs inside ``parse_request``, before any route.
"""

from __future__ import annotations

import io
import json
import math
import os
import secrets
import signal
import stat
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from socketserver import BaseServer
from types import FrameType
from typing import Any, Protocol

from reach_around.editor.guard import (
    JSON_CAP,
    MAX_HEADER_BYTES,
    Guard,
    PreviewGuard,
    Verdict,
)
from reach_around.editor.service import (
    MUTATIONS_PER_MINUTE,
    RATE_WINDOW_SECONDS,
    EditorService,
    RouteError,
)

BIND_ADDRESS = "127.0.0.1"
DEFAULT_IDLE_SECONDS = 30 * 60.0
READ_TIMEOUT_SECONDS = 10.0
HEADER_DEADLINE_SECONDS = 10.0
BODY_DEADLINE_SECONDS = 30.0
MAX_IDLE_SECONDS = 24 * 3600.0
_RECV_CHUNK = 8192
MAX_ACTIVE_CONNECTIONS = 16
MAX_OPEN_CONNECTIONS = 32
CONFIG_READ_CAP = 8 * 1024 * 1024
_ASSET_FILES = ("index.html", "app.js", "app.css")
_STATIC_ROUTES = {
    "/__edit/": ("index.html", "text/html; charset=utf-8"),
    "/__edit/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/__edit/app.css": ("app.css", "text/css; charset=utf-8"),
}
_MESSAGES = {
    400: "The request could not be understood.",
    401: "Authentication is required.",
    403: "The request was refused.",
    404: "Not found.",
    405: "That method is not allowed.",
    411: "A length is required.",
    413: "The request is too large.",
    414: "The request line is too long.",
    415: "That content type is not accepted.",
    417: "Expectations are not supported.",
    421: "Misdirected request.",
    431: "The request headers are too large.",
}
_PREVIEW_PAGE = (
    b'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    b"<title>Preview</title></head><body><p>The preview appears here.</p>"
    b"</body></html>\n"
)


class EditorError(Exception):
    """The editor cannot start (a non-loopback host, an unreadable config)."""


def read_config_bytes(path: Path) -> bytes | None:
    """The config bytes, or None for a missing, symlinked, special or huge file.

    ``O_NONBLOCK`` keeps a FIFO from blocking the open; ``fstat`` then refuses it.
    """
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            data = handle.read(CONFIG_READ_CAP + 1)
    except OSError:
        return None
    return data if len(data) <= CONFIG_READ_CAP else None


def editor_csp(frame_origin: str) -> str:
    """The editor document policy; ``frame_origin`` is the one preview origin."""
    return (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob: data:; connect-src 'self'; "
        f"frame-src {frame_origin}; form-action 'none'; base-uri 'none'; "
        "object-src 'none'; frame-ancestors 'none'; "
        "sandbox allow-same-origin allow-scripts allow-forms allow-modals "
        "allow-downloads"
    )


def preview_csp(editor_origin: str) -> str:
    """The preview policy: opaque origin (D-05, D-15) and only the editor may frame."""
    return (
        "sandbox allow-scripts; default-src 'self'; base-uri 'none'; "
        f"form-action 'none'; frame-ancestors {editor_origin}"
    )


class _BoundedServer(ThreadingHTTPServer):
    """A threading server with a connection cap and no error output."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]
    ) -> None:
        self._slots = threading.BoundedSemaphore(MAX_ACTIVE_CONNECTIONS)
        self._count_lock = threading.Lock()
        self._open = 0
        self.owner: EditorSession | None = None
        self.kind = "editor"
        super().__init__(address, handler)

    def open_connections(self) -> int:
        with self._count_lock:
            return self._open

    def process_request(self, request: Any, client_address: Any) -> None:
        with self._count_lock:
            if self._open >= MAX_OPEN_CONNECTIONS:
                self.shutdown_request(request)
                return
            self._open += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._count_lock:
                self._open -= 1
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            with self._slots:
                super().process_request_thread(request, client_address)
        finally:
            with self._count_lock:
                self._open -= 1

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Say nothing: a traceback could carry request data (T-35)."""


class _Receiver(Protocol):
    """The two socket methods the reader uses (no socket import here)."""

    def settimeout(self, value: float | None, /) -> None: ...

    def recv(self, bufsize: int, /) -> bytes: ...


class _HeadersTooLarge(Exception):
    """The request headers passed ``MAX_HEADER_BYTES`` on the wire."""


class _BoundedReader(io.BufferedIOBase):
    """A byte-counting, deadline-bound reader over the connection.

    It replaces ``rfile`` so the 16 KiB header cap and the receive deadlines
    apply while bytes arrive, before ``http.server`` parses or stores them.
    Every ``recv`` waits at most ``min(read_timeout, time left to the deadline)``,
    so a client that sends a byte just inside the per-read timeout still runs
    out of time. The first line is the request line; an over-long one is
    returned unterminated (``parse_request`` answers 414). The lines after it
    share one cumulative budget (``_HeadersTooLarge``, 431). A passed deadline
    raises ``TimeoutError``, which closes the connection without a reply.
    """

    def __init__(
        self,
        sock: _Receiver,
        *,
        read_timeout: float,
        header_deadline: float,
        body_deadline: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sock = sock
        self._read_timeout = read_timeout
        self._body_seconds = body_deadline
        self._clock = clock
        self._deadline = clock() + header_deadline
        self._buffer = bytearray()
        self._lines = 0
        self._header_bytes = 0
        self._in_headers = True
        self._eof = False

    def start_body(self) -> None:
        """Leave the header phase: no byte cap, the body deadline starts now."""
        if self._in_headers:
            self._in_headers = False
            self._deadline = self._clock() + self._body_seconds

    def _fill(self) -> None:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError("receive deadline passed")
        self._sock.settimeout(min(self._read_timeout, remaining))
        chunk = self._sock.recv(_RECV_CHUNK)
        if not chunk:
            self._eof = True
        self._buffer += chunk

    def readable(self) -> bool:
        return True

    def readline(self, size: int | None = -1) -> bytes:
        size = -1 if size is None else size
        first = self._lines == 0
        counted = self._in_headers and not first
        limit = MAX_HEADER_BYTES + 1 if size < 0 else min(size, MAX_HEADER_BYTES + 1)
        if counted:
            limit = min(limit, MAX_HEADER_BYTES + 1 - self._header_bytes)
        while True:
            newline = self._buffer.find(b"\n", 0, limit)
            if newline >= 0:
                end = newline + 1
                break
            if len(self._buffer) >= limit:
                end = limit
                break
            if self._eof:
                end = len(self._buffer)
                break
            self._fill()
        line = bytes(self._buffer[:end])
        del self._buffer[:end]
        self._lines += 1
        if counted:
            self._header_bytes += len(line)
            if self._header_bytes > MAX_HEADER_BYTES:
                raise _HeadersTooLarge
        return line

    def read(self, size: int | None = -1) -> bytes:
        size = -1 if size is None else size
        while (size < 0 or len(self._buffer) < size) and not self._eof:
            self._fill()
        if size < 0:
            size = len(self._buffer)
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def close(self) -> None:
        self._buffer.clear()
        self._eof = True
        super().close()


class _Handler(BaseHTTPRequestHandler):
    """Shared plumbing: fixed error bodies, hardening headers, silent logging."""

    protocol_version = "HTTP/1.0"  # one request per connection, no keep-alive
    body_read = False
    server_version = "reach-around-edit"
    sys_version = ""

    @property
    def _server(self) -> _BoundedServer:
        server: BaseServer = self.server
        if not isinstance(server, _BoundedServer) or server.owner is None:
            raise RuntimeError("handler is not attached to an editor server")
        return server

    @property
    def owner(self) -> EditorSession:
        owner = self._server.owner
        if owner is None:
            raise RuntimeError("handler is not attached to an editor session")
        return owner

    def setup(self) -> None:
        super().setup()
        owner = self.owner
        self.connection.settimeout(owner.read_timeout)
        self.rfile = _BoundedReader(
            self.connection,
            read_timeout=owner.read_timeout,
            header_deadline=owner.header_deadline,
            body_deadline=owner.body_deadline,
        )

    # -- policy hooks, overridden by the two listeners ---------------------------

    def security_headers(self) -> list[tuple[str, str]]:
        raise NotImplementedError

    def check(self) -> Verdict | None:
        raise NotImplementedError

    # -- quiet and fixed -----------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Silent: a request line can hold a path, a header or a token (T-35)."""

    def handle_expect_100(self) -> bool:
        self.send_error(417)
        return False

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        self._refuse(code)

    def _refuse(self, status: int) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": HTTPStatus(status).name.lower(),
                    "message": _MESSAGES.get(status, "The request was refused."),
                    "path": "",
                }
            }
        ).encode("ascii")
        extra = [("Allow", "GET, POST")] if status == 405 else []
        self._send(status, body, "application/json", extra)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: list[tuple[str, str]] | None = None,
    ) -> None:
        self.close_connection = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for name, value in self.security_headers() + (extra or []):
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self._drain_body()
        except OSError:
            pass

    def _drain_body(self) -> None:
        """Read a little of an unread POST body so closing does not reset the reply."""
        reader = self.rfile
        if isinstance(reader, _BoundedReader):
            reader.start_body()
        if self.command != "POST" or self.body_read:
            return
        try:
            declared = int(self.headers.get("Content-Length", "0"))
        except (ValueError, AttributeError):
            return
        want = max(0, min(declared, JSON_CAP))
        try:
            while want > 0:
                chunk = self.rfile.read(min(want, 8192))
                if not chunk:
                    break
                want -= len(chunk)
        except OSError:
            pass

    def parse_request(self) -> bool:
        try:
            return self._parse_and_guard()
        except _HeadersTooLarge:
            self.close_connection = True
            self.send_error(431)
            return False
        except OSError:  # a timeout or a dropped connection: close without a reply
            self.close_connection = True
            return False
        except Exception:
            self._refuse(400)
            return False

    def _parse_and_guard(self) -> bool:
        if len(getattr(self, "raw_requestline", b"")) > MAX_HEADER_BYTES:
            self.requestline = ""
            self.request_version = ""
            self.command = ""
            self.send_error(414)
            return False
        if not super().parse_request():
            return False
        size = sum(len(k) + len(v) + 4 for k, v in self.headers.items())
        if size > MAX_HEADER_BYTES:
            self.send_error(431)
            return False
        verdict = self.check()
        if verdict is not None:
            self._refuse(verdict.status)
            return False
        reader = self.rfile
        if isinstance(reader, _BoundedReader):
            reader.start_body()
        return True

    def route_path(self) -> str:
        return self.path.split("?", 1)[0]

    def dispatch(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:
            self._refuse(400)


class _EditorHandler(_Handler):
    def security_headers(self) -> list[tuple[str, str]]:
        session = self.owner
        host = None
        headers = getattr(self, "headers", None)
        if headers is not None and session.guard is not None:
            host = session.guard.allowed_host(headers.get("Host"))
        name = host.rsplit(":", 1)[0] if host else BIND_ADDRESS
        return [
            (
                "Content-Security-Policy",
                editor_csp(f"http://{name}:{session.preview_port}"),
            ),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Cache-Control", "no-store"),
        ]

    def check(self) -> Verdict | None:
        guard = self.owner.guard
        if guard is None:
            return Verdict(400, "bad_request")
        return guard.check(self.command, self.path, self.headers)

    def do_GET(self) -> None:
        self.dispatch(self._get)

    def do_POST(self) -> None:
        self.dispatch(self._post)

    def _get(self) -> None:
        path = self.route_path()
        route = _STATIC_ROUTES.get(path)
        if route is not None:
            name, kind = route
            self._send(200, self.owner.asset(name), kind)
        elif path == "/__edit/api/state":
            self._json_route(self._state)
        elif path == "/__edit/api/version":
            self._json_route(lambda: self.owner.service.version())
        else:
            self._refuse(404)

    def _post(self) -> None:
        if self.route_path() == "/__edit/api/op":
            self._json_route(self._op)
        else:
            self._refuse(404)

    def _json_route(self, action: Callable[[], dict[str, Any]]) -> None:
        try:
            self._send_json(200, action())
        except RouteError as error:
            self._send_json(error.status, error.body(), error.headers)

    def _send_json(
        self,
        status: int,
        data: dict[str, Any],
        extra: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps(data).encode("ascii")
        self._send(status, body, "application/json", extra)

    def _preview_host(self) -> str:
        guard = self.owner.guard
        host = guard.allowed_host(self.headers.get("Host")) if guard else None
        return host.rsplit(":", 1)[0] if host else BIND_ADDRESS

    def _state(self) -> dict[str, Any]:
        session = self.owner
        return session.service.state(self._preview_host(), session.preview_port)

    def _op(self) -> dict[str, Any]:
        session = self.owner
        session.touch()
        wait = session.service.rate_check()
        if wait is not None:
            raise RouteError(
                429,
                "too_many_requests",
                "Too many changes in a minute. Wait a moment and try again.",
                headers=[("Retry-After", str(wait))],
            )
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.body_read = True
        if len(body) != length:
            raise RouteError(400, "bad_json", "The request body was cut short.")
        return session.service.run_op(body, self._preview_host(), session.preview_port)


class _PreviewHandler(_Handler):
    def security_headers(self) -> list[tuple[str, str]]:
        session = self.owner
        host = None
        headers = getattr(self, "headers", None)
        if headers is not None and session.preview_guard is not None:
            host = session.preview_guard.allowed_host(headers.get("Host"))
        name = host.rsplit(":", 1)[0] if host else BIND_ADDRESS
        return [
            ("Content-Security-Policy", preview_csp(f"http://{name}:{session.port}")),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Cache-Control", "no-store"),
        ]

    def check(self) -> Verdict | None:
        guard = self.owner.preview_guard
        if guard is None:
            return Verdict(400, "bad_request")
        return guard.check(self.command, self.path, self.headers)

    def do_GET(self) -> None:
        self.dispatch(self._get)

    do_HEAD = do_GET

    def _get(self) -> None:
        if self.route_path() == "/":
            self._send(200, _PREVIEW_PAGE, "text/html; charset=utf-8")
        else:
            self._refuse(404)


@dataclass
class _Listener:
    server: _BoundedServer
    thread: threading.Thread


class EditorSession:
    """One editing session: two listeners, a token, an idle timer."""

    def __init__(
        self,
        config: Path,
        *,
        host: str = BIND_ADDRESS,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        watchdog_interval: float = 1.0,
        read_timeout: float = READ_TIMEOUT_SECONDS,
        header_deadline: float = HEADER_DEADLINE_SECONDS,
        body_deadline: float = BODY_DEADLINE_SECONDS,
        mutation_limit: int = MUTATIONS_PER_MINUTE,
        mutation_window: float = RATE_WINDOW_SECONDS,
    ) -> None:
        if not (math.isfinite(idle_seconds) and 0 < idle_seconds <= MAX_IDLE_SECONDS):
            raise EditorError(
                "the idle time must be a number of seconds above 0 and at most "
                f"{MAX_IDLE_SECONDS:g}"
            )
        if host != BIND_ADDRESS:
            raise EditorError(f"the editor binds {BIND_ADDRESS} only, not {host!r}")
        self.address = host
        self.config = config.resolve()
        if read_config_bytes(self.config) is None:
            raise EditorError(f"cannot read the config file {config}")
        self.service = EditorService(
            self.config,
            read_config_bytes,
            clock=clock,
            mutation_limit=mutation_limit,
            mutation_window=mutation_window,
        )
        self.idle_seconds = idle_seconds
        self.read_timeout = read_timeout
        self.header_deadline = header_deadline
        self.body_deadline = body_deadline
        self.token = secrets.token_urlsafe(32)
        self.stopped = threading.Event()
        self.stop_reason: str | None = None
        self.guard: Guard | None = None
        self.preview_guard: PreviewGuard | None = None
        self._clock = clock
        self._watchdog_interval = watchdog_interval
        self._lock = threading.Lock()
        self._last_activity = clock()
        self._closing = threading.Event()
        self._started = False
        self._editor: _Listener | None = None
        self._preview: _Listener | None = None
        self._watchdog: threading.Thread | None = None
        self._assets: dict[str, bytes] = {}

    # -- addresses -----------------------------------------------------------

    @property
    def port(self) -> int:
        return self._require(self._editor).server.server_address[1]

    @property
    def preview_port(self) -> int:
        return self._require(self._preview).server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.address}:{self.port}/__edit/#t={self.token}"

    def editor_sockname(self) -> tuple[str, int]:
        name = self._require(self._editor).server.socket.getsockname()
        return str(name[0]), int(name[1])

    def preview_sockname(self) -> tuple[str, int]:
        name = self._require(self._preview).server.socket.getsockname()
        return str(name[0]), int(name[1])

    def active_connections(self) -> int:
        return self._require(self._editor).server.open_connections()

    @staticmethod
    def _require(listener: _Listener | None) -> _Listener:
        if listener is None:
            raise EditorError("the editor is not running")
        return listener

    def asset(self, name: str) -> bytes:
        return self._assets[name]

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise EditorError("the editor was already started")
            self._started = True
        root = resources.files("reach_around").joinpath("editor_assets")
        self._assets = {n: root.joinpath(n).read_bytes() for n in _ASSET_FILES}
        try:
            editor = self._listen(_EditorHandler, "editor")
            self._editor = editor
            preview = self._listen(_PreviewHandler, "preview")
            self._preview = preview
        except BaseException:
            self.shutdown("error")
            raise
        self.guard = Guard(editor.server.server_address[1], self.token)
        self.preview_guard = PreviewGuard(preview.server.server_address[1])
        self._last_activity = self._clock()
        for listener in (editor, preview):
            listener.thread.start()
        self._watchdog = threading.Thread(
            target=self._watch, name="editor-idle", daemon=True
        )
        self._watchdog.start()

    def _listen(self, handler: type[BaseHTTPRequestHandler], kind: str) -> _Listener:
        server = _BoundedServer((self.address, 0), handler)
        server.owner = self
        server.kind = kind
        thread = threading.Thread(
            target=server.serve_forever,
            args=(0.05,),
            name=f"editor-{kind}",
            daemon=True,
        )
        return _Listener(server, thread)

    def touch(self) -> None:
        """Record owner activity (a mutating or keep-alive request)."""
        with self._lock:
            self._last_activity = self._clock()

    def check_idle(self) -> bool:
        """Stop everything if the session has been idle too long; True if it did."""
        with self._lock:
            idle = self._clock() - self._last_activity >= self.idle_seconds
        if idle:
            self.shutdown("idle")
        return idle

    def _watch(self) -> None:
        while not self._closing.wait(self._watchdog_interval):
            if self.check_idle():
                return

    def wait(self, timeout: float | None = None) -> bool:
        return self.stopped.wait(timeout)

    def shutdown(self, reason: str = "shutdown") -> None:
        """Stop both listeners and free the ports; safe to call twice."""
        with self._lock:
            if self._closing.is_set():
                first = False
            else:
                self._closing.set()
                self.stop_reason = reason
                first = True
        if not first:
            self.stopped.wait(10)
            return
        current = threading.current_thread()
        for listener in (self._editor, self._preview):
            if listener is None:
                continue
            if listener.thread.is_alive():
                listener.server.shutdown()
            listener.server.server_close()
            if listener.thread.is_alive() and listener.thread is not current:
                listener.thread.join(5)
        watchdog = self._watchdog
        if watchdog is not None and watchdog is not current:
            watchdog.join(5)
        self.stopped.set()


def run_editor(config: Path, *, idle_minutes: float = DEFAULT_IDLE_SECONDS / 60) -> int:
    """Run an editing session in the foreground; the URL goes to stdout only."""
    try:
        session = EditorSession(config, idle_seconds=idle_minutes * 60)
        session.start()
    except (EditorError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    def stop(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    on_main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, stop) if on_main_thread else None
    try:
        print(session.url, flush=True)
        print(
            f"editor: listening on 127.0.0.1 only; stops after {idle_minutes:g} "
            "minutes idle; Ctrl+C to stop. The link above holds this session's "
            "key: do not share it.",
            file=sys.stderr,
            flush=True,
        )
        while not session.wait(0.25):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        session.shutdown("interrupt")
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if session.stop_reason == "idle":
        print("editor: stopped after being idle", file=sys.stderr)
    return 0
