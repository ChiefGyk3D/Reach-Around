# SPDX-License-Identifier: Apache-2.0
"""The post-deploy smoke script (scripts/smoke.sh), run against a local stub.

Nothing here leaves 127.0.0.1. The stub plays a deployed site: a page at /, and
the Worker's write-only routes under /e/ (an empty 204 for everything).
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke.sh"

GOOD_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
}


SEEN: list[str] = []


def make_handler(mode: str) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep test output quiet
            pass

        def _send(
            self, status: int, body: bytes = b"", extra: dict[str, str] | None = None
        ) -> None:
            self.send_response(status)
            headers = dict(GOOD_HEADERS)
            if mode == "no-csp":
                del headers["Content-Security-Policy"]
            if mode == "cookie":
                headers["Set-Cookie"] = "sid=1"
            headers.update(extra or {})
            ctype = "text/html; charset=utf-8" if status == 200 else None
            if ctype and mode != "plain-root":
                self.send_header("Content-Type", ctype)
            if mode == "plain-root" and status == 200:
                self.send_header("Content-Type", "text/plain")
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _worker(self) -> None:
            if mode == "view-200":
                self._send(200, b"ok")
            elif mode == "echo":
                self._send(204, extra={"X-Seen": self.headers.get("X-Smoke-Probe", "")})
            elif mode == "data-route" and self.path.startswith("/e/v/"):
                self._send(200, b'{"views": 3}')
            else:
                self._send(204)

        def do_GET(self) -> None:
            SEEN.append(self.path)
            if self.path.startswith("/e/"):
                return self._worker()
            if mode == "root-500":
                return self._send(500, b"boom")
            self._send(200, b"<!doctype html><html><body>hi</body></html>")

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            if self.path.startswith("/e/"):
                return self._worker()
            self._send(404)

    return H


@contextmanager
def serve(mode: str = "good") -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mode))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["bash", str(SCRIPT), *args],  # noqa: S607 - bash on PATH
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_good_site_passes() -> None:
    with serve("good") as url:
        r = run(url)
    assert r.returncode == 0, r.stdout + r.stderr
    # Loose collection: it really ran several checks.
    assert r.stdout.count("ok:") >= 6


def test_trailing_slash_in_url_is_accepted() -> None:
    with serve("good") as url:
        assert run(url + "/").returncode == 0


@pytest.mark.parametrize(
    ("mode", "needle"),
    [
        ("root-500", "page"),
        ("plain-root", "content-type"),
        ("no-csp", "content-security-policy"),
        ("cookie", "set-cookie"),
        ("view-200", "204"),
        ("data-route", "204"),
        ("echo", "echo"),
    ],
)
def test_each_break_is_caught(mode: str, needle: str) -> None:
    with serve(mode) as url:
        r = run(url)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL:" in r.stdout + r.stderr
    assert needle in (r.stdout + r.stderr).lower()


def test_failures_are_all_reported_not_just_the_first() -> None:
    with serve("root-500") as url:
        r = run(url)
    # The page check failed but the worker-route checks still ran.
    assert "ok:" in r.stdout
    assert "FAIL:" in r.stdout


def test_unreachable_target_fails_quickly() -> None:
    r = run("--timeout", "2", "http://127.0.0.1:9")
    assert r.returncode == 1


@pytest.mark.parametrize(
    "bad", ["", "ftp://x.example.test", "not a url", "file:///etc/passwd"]
)
def test_bad_target_is_a_usage_error(bad: str) -> None:
    r = run(bad) if bad else run()
    assert r.returncode == 2
    assert "usage" in (r.stdout + r.stderr).lower()


@pytest.mark.parametrize("zero", ["0", "00", "000", "0.0", "-1", "1e3", " 5", "5s"])
def test_a_timeout_that_is_not_a_positive_whole_number_is_a_usage_error(
    zero: str,
) -> None:
    # curl treats --max-time 0 as "no limit", which would break the guarantee
    # that every request is bounded.
    with serve("good") as url:
        r = run("--timeout", zero, url)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "usage" in (r.stdout + r.stderr).lower()


@pytest.mark.parametrize(
    "bad",
    [
        "http://user:pw@127.0.0.1:1",
        "http://user@127.0.0.1:1",
        "https://127.0.0.1:1@evil.example.test",
        "http://:pw@127.0.0.1:1/",
        "http://127.0.0.1:1/a?x=1@y",
        "http://127.0.0.1:1\\@evil.example.test",
        "http://",
        "https:///path",
    ],
)
def test_urls_with_credentials_or_no_host_are_refused(bad: str) -> None:
    r = run(bad)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "usage" in (r.stdout + r.stderr).lower()
    assert "pw" not in r.stdout + r.stderr


def test_url_globbing_is_off() -> None:
    # Without --globoff curl expands [1-2] into two requests.
    SEEN.clear()
    with serve("good") as url:
        run(url + "/[1-2]")
    assert not any(p in ("/1", "/2", "/1/", "/2/") for p in SEEN), SEEN
    assert any("[1-2]" in p or "%5B1-2%5D" in p for p in SEEN), SEEN
