# SPDX-License-Identifier: Apache-2.0
"""Loopback-only live preview of the built site.

A local development tool: it is never deployed and never public. It builds
into a temporary directory, serves that directory, rebuilds when the config or
the package templates and static files change, and shows the build error in
the browser instead of crashing. ``/__preview`` shows the site at desktop and
phone width side by side; those assets live outside the site package data so
they can never reach a production build.
"""

from __future__ import annotations

import html
import ipaddress
import mimetypes
import shutil
import socket
import tempfile
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import reach_around.site as _site_package
from reach_around.config import ConfigError, load_site_config
from reach_around.site.assets import referenced_assets
from reach_around.site.build import build_site
from reach_around.site.overrides import OverridesError, check_overrides_dir
from reach_around.site.visibility import today_in

POLL_SECONDS = 0.5
_PREVIEW_ASSETS = {
    "/__preview": ("preview.html", "text/html; charset=utf-8"),
    "/__preview/preview.css": ("preview.css", "text/css; charset=utf-8"),
    "/__preview/preview.js": ("preview.js", "text/javascript; charset=utf-8"),
}
_PREVIEW_CSP = (
    "default-src 'self'; frame-src 'self'; img-src 'self' data:; "
    "style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'none'"
)
_BASE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_FORBIDDEN_ESCAPES = ("%2f", "%5c", "%00")


class PreviewError(Exception):
    """The preview cannot start (for example, a non-loopback host)."""


def _is_loopback(host: str) -> bool:
    """True only when ``host`` is, or resolves entirely to, loopback addresses."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if host.lower() != "localhost":
        return False
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = {str(info[4][0]) for info in infos}
    return bool(addresses) and all(
        ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addresses
    )


def _watched_files(
    config: Path, overrides: Path | None = None
) -> dict[Path, tuple[int, int]]:
    """Snapshot (mtime, size) of the config, the overrides and package files."""
    package = Path(_site_package.__file__).resolve().parent
    paths = [config]
    try:
        names = referenced_assets(load_site_config(config))
    except (ConfigError, OSError):
        names = []  # the build reports a broken config; just keep watching it
    paths.extend(config.parent / name for name in names)
    if overrides is not None:
        paths.extend(p for p in overrides.rglob("*") if p.is_file())
    for sub in ("templates", "static"):
        paths.extend(p for p in (package / sub).rglob("*") if p.is_file())
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        snapshot[path] = (st.st_mtime_ns, st.st_size)
    return snapshot


def _host_name(host_header: str) -> str:
    """Lowercase host from a Host header value, port and IPv6 brackets removed."""
    value = host_header.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    if value.count(":") == 1:
        return value.split(":")[0]
    return value


def _error_page(message: str) -> bytes:
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Build error</title><h1>Build error</h1>"
        f"<pre>{html.escape(message)}</pre></html>\n"
    ).encode()


def _safe_segments(raw_path: str) -> list[str] | None:
    """Split a request path into safe segments, or None when it is hostile."""
    path = urlsplit(raw_path).path
    lowered = path.lower()
    if "\\" in path or any(esc in lowered for esc in _FORBIDDEN_ESCAPES):
        return None
    decoded = unquote(path)
    if not decoded.startswith("/") or "\\" in decoded or "\0" in decoded:
        return None
    segments = decoded.split("/")[1:]
    for i, seg in enumerate(segments):
        if seg in (".", "..") or (seg == "" and i != len(segments) - 1):
            return None
    return [s for s in segments if s]


def _resolve(root: Path, segments: list[str]) -> Path | None:
    """Map segments to a regular file inside ``root``; None if it escapes."""
    base = root.resolve()
    candidate = base.joinpath(*segments)
    if candidate.is_dir():
        candidate = candidate / "index.html"
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return None
    return resolved


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "ReachAroundPreview"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return  # keep the terminal quiet; build errors are shown in the browser

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        head: bool,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _not_found(self, head: bool) -> None:
        self._send(
            HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8", head=head
        )

    def _guard(self) -> bool:
        """Refuse DNS-rebinding and cross-site requests; True when allowed."""
        allowed = self.server.preview.allowed_hosts
        host = self.headers.get("Host")
        if host is None or _host_name(host) not in allowed:
            self._deny(HTTPStatus.MISDIRECTED_REQUEST)
            return False
        origin = self.headers.get("Origin")
        if origin is not None:
            origin_host = urlsplit(origin).hostname if origin != "null" else None
            if origin_host is None or origin_host.lower() not in allowed:
                self._deny(HTTPStatus.FORBIDDEN)
                return False
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            self._deny(HTTPStatus.FORBIDDEN)
            return False
        return True

    def _deny(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve(self, head: bool) -> None:
        if not self._guard():
            return
        route = urlsplit(self.path).path
        preview = self.server.preview
        if route == "/__preview/version":
            body = str(preview.version).encode()
            self._send(HTTPStatus.OK, body, "text/plain; charset=utf-8", head=head)
            return
        if route in _PREVIEW_ASSETS:
            name, ctype = _PREVIEW_ASSETS[route]
            asset = resources.files("reach_around") / "preview_assets" / name
            extra = {"Content-Security-Policy": _PREVIEW_CSP}
            self._send(HTTPStatus.OK, asset.read_bytes(), ctype, head=head, extra=extra)
            return
        segments = _safe_segments(self.path)
        if segments is None or (segments and segments[0] == "__preview"):
            self._not_found(head)
            return
        error = preview.error
        if error is not None:
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _error_page(error),
                "text/html; charset=utf-8",
                head=head,
            )
            return
        target = _resolve(preview.root, segments)
        if target is None:
            self._not_found(head)
            return
        try:
            data = target.read_bytes()
        except OSError:
            self._not_found(head)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/json":
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, data, ctype, head=head)

    def do_GET(self) -> None:
        self._serve(head=False)

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def _refuse(self) -> None:
        if not self._guard():
            return
        self._send(
            HTTPStatus.METHOD_NOT_ALLOWED,
            b"only GET and HEAD are served\n",
            "text/plain; charset=utf-8",
            head=False,
            extra={"Allow": "GET, HEAD"},
        )

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    preview: Preview


class _Server6(_Server):
    address_family = socket.AF_INET6


class Preview:
    """A running preview: temp output directory, watcher thread, HTTP server."""

    def __init__(
        self,
        config: Path,
        host: str,
        port: int,
        overrides: Path | None = None,
    ) -> None:
        if not _is_loopback(host):
            raise PreviewError(
                f"refusing to bind {host!r}: the preview serves only on a "
                "loopback address (127.0.0.1, ::1 or localhost)"
            )
        if overrides is not None:
            try:
                check_overrides_dir(overrides)
            except OverridesError as exc:
                raise PreviewError(str(exc)) from exc
        self.config = config
        self.overrides = overrides
        self.allowed_hosts = _BASE_HOSTS | {host.lower()}
        self.version = 0
        self.error: str | None = None
        self._tmp = Path(tempfile.mkdtemp(prefix="reach-around-preview-"))
        self.root = self._tmp / "build-0"
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._serving = threading.Event()
        self._closed = False
        self._seen = _watched_files(
            config, overrides
        )  # before the build: no change is missed
        self._rebuild()
        server_class = _Server6 if ":" in host else _Server
        try:
            self._server = server_class((host, port), _Handler)
        except OSError:
            shutil.rmtree(self._tmp, ignore_errors=True)
            raise
        self._server.preview = self

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def _rebuild(self) -> None:
        """Build into a fresh directory, then swap; on failure show the error."""
        new_root = self._tmp / f"build-{self.version + 1}"
        try:
            config = load_site_config(self.config)
            today = today_in(config.timezone, datetime.now(UTC))
            build_site(
                config,
                new_root,
                today=today,
                protect=[self.config],
                overrides=self.overrides,
                assets_dir=self.config.parent,
            )
        except (ConfigError, ValueError, OSError) as exc:
            shutil.rmtree(new_root, ignore_errors=True)
            self.error = str(exc)
            self.version += 1
            return
        old_root = self.root
        self.root = new_root
        self.error = None
        self.version += 1
        shutil.rmtree(old_root, ignore_errors=True)

    def _watch(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            now = _watched_files(self.config, self.overrides)
            if now != self._seen:
                self._seen = now
                self._rebuild()

    def serve_forever(self) -> None:
        self._serving.set()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()
        self._server.serve_forever(poll_interval=0.1)

    def close(self) -> None:
        """Stop serving, release the port and delete the temporary directory."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._serving.is_set():  # shutdown() would block forever otherwise
            self._server.shutdown()
        if self._watcher is not None:
            self._watcher.join(timeout=5)
        self._server.server_close()
        shutil.rmtree(self._tmp, ignore_errors=True)


def serve(
    config: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    overrides: Path | None = None,
) -> None:
    """Serve a live preview of ``config`` on a loopback ``host`` until interrupted.

    ``overrides`` is an optional overrides directory (see
    ``reach_around.site.overrides``); it is built in and watched for changes.
    """
    preview = Preview(config, host, port, overrides)
    shown = f"[{host}]" if ":" in host else host
    print(f"preview: http://{shown}:{preview.port}/__preview  (Ctrl+C to stop)")
    try:
        preview.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        preview.close()
