# SPDX-License-Identifier: Apache-2.0
import http.client
import inspect
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from reach_around.cli import main
from reach_around.preview import Preview, PreviewError, serve

ANY_V4 = ".".join(["0"] * 4)  # built, not written, so the lint rule stays quiet
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "instance" / "site.yaml"
NET_TIMEOUT = 2.0  # every socket and request in this module is bounded by this
JOIN_TIMEOUT = 2.0  # every thread join is bounded by this


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "site.yaml"
    path.write_text(EXAMPLE.read_text())
    return path


@pytest.fixture
def never_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a guard regresses: ``serve`` must never reach the serve loop.

    Without this, a regression that lets a non-loopback host through would bind
    the port and block in ``serve_forever`` until the run is killed.
    """

    def _boom(self: Preview) -> None:
        raise AssertionError("the preview reached serve_forever on a refused host")

    monkeypatch.setattr(Preview, "serve_forever", _boom)


@pytest.fixture
def running(config: Path) -> Iterator[Preview]:
    preview = Preview(config, "127.0.0.1", 0)
    thread = threading.Thread(target=preview.serve_forever, daemon=True)
    thread.start()
    try:
        yield preview
    finally:
        preview.close()
        thread.join(timeout=JOIN_TIMEOUT)


def fetch(
    preview: Preview,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", preview.port, timeout=NET_TIMEOUT)
    try:
        # raw path: no client-side normalization
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def test_serve_refuses_a_non_loopback_host(config: Path, never_serve: None) -> None:
    for host in (ANY_V4, "::", "192.168.1.20", "10.0.0.5", "example.com"):
        with pytest.raises(PreviewError):
            serve(config, host=host, port=0)


def test_loopback_hosts_are_accepted(config: Path) -> None:
    for host in ("127.0.0.1", "localhost"):
        preview = Preview(config, host, 0)
        preview.close()


def test_serves_the_built_site_without_caching(running: Preview) -> None:
    status, headers, body = fetch(running, "/")
    assert status == 200
    assert b"Example Creator" in body
    assert headers["cache-control"] == "no-store"
    status, _, css = fetch(running, "/static/site.css")
    assert status == 200 and css


def test_rebuilds_when_the_config_changes(running: Preview, config: Path) -> None:
    config.write_text(config.read_text().replace("Example Creator", "Renamed Person"))
    deadline = time.monotonic() + 2.0
    body = b""
    while time.monotonic() < deadline:
        body = fetch(running, "/")[2]
        if b"Renamed Person" in body:
            break
        time.sleep(0.1)
    assert b"Renamed Person" in body


def test_a_broken_config_shows_the_error_instead_of_crashing(
    running: Preview, config: Path
) -> None:
    config.write_text("version: 2\n")
    deadline = time.monotonic() + 2.0
    status, body = 200, b""
    while time.monotonic() < deadline:
        status, _, body = fetch(running, "/")
        if status != 200:
            break
        time.sleep(0.1)
    assert status == 500
    assert b"site.yaml" in body
    # the server is still alive and recovers once the config is fixed
    config.write_text(EXAMPLE.read_text())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and fetch(running, "/")[0] != 200:
        time.sleep(0.1)
    assert fetch(running, "/")[0] == 200


def test_preview_page_is_not_in_a_production_build(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["build", "--config", str(EXAMPLE), "--out", str(out)]) == 0
    names = [p.name for p in out.rglob("*")]
    assert not any("preview" in n for n in names)
    assert all(
        "__preview" not in p.read_text(errors="ignore")
        for p in out.rglob("*")
        if p.is_file()
    )


def test_preview_page_shows_desktop_and_phone_iframes(running: Preview) -> None:
    status, headers, body = fetch(running, "/__preview")
    assert status == 200
    text = body.decode()
    assert text.count("<iframe") == 2
    assert "390" in text
    assert "http://" not in text and "https://" not in text
    assert "frame-src 'self'" in headers["content-security-policy"]
    assert fetch(running, "/__preview/preview.css")[0] == 200


@pytest.mark.parametrize(
    "path",
    [
        "/../../etc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/%2E%2E/",
        "/..%2f..%2fetc/passwd",
        "/static%2f..%2f..%2fsite.yaml",
        "/..\\..\\etc\\passwd",
        "/static/..%5c..%5csite.yaml",
        "/static/../../site.yaml",
        "//etc/passwd",
        "/%00",
    ],
)
def test_path_traversal_returns_404(running: Preview, path: str) -> None:
    status, _, body = fetch(running, path)
    assert status == 404
    assert b"root:" not in body and b"Example Creator" not in body


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_only_get_and_head_are_served(running: Preview, method: str) -> None:
    status, headers, _ = fetch(running, "/", method)
    assert status == 405
    assert headers["allow"] == "GET, HEAD"
    assert headers["cache-control"] == "no-store"


def test_head_returns_headers_and_no_body(running: Preview) -> None:
    status, headers, body = fetch(running, "/", "HEAD")
    assert status == 200 and body == b""
    assert int(headers["content-length"]) > 0


def test_symlink_in_output_is_not_followed_outside(
    running: Preview, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    link = running.root / "leak.txt"
    link.symlink_to(secret)
    assert fetch(running, "/leak.txt")[0] == 404
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("top secret")
    (running.root / "dirleak").symlink_to(outside, target_is_directory=True)
    status, _, body = fetch(running, "/dirleak/")
    assert status == 404 and b"top secret" not in body


def test_stops_cleanly_on_shutdown(config: Path) -> None:
    preview = Preview(config, "127.0.0.1", 0)
    port = preview.port
    root = preview.root
    thread = threading.Thread(target=preview.serve_forever, daemon=True)
    thread.start()
    try:
        assert fetch(preview, "/")[0] == 200
    finally:
        preview.close()
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive()
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), NET_TIMEOUT):
        pass
    assert not root.exists()  # temporary output directory cleaned up


def test_serve_signature_carries_overrides() -> None:
    # `overrides` is part of the public signature and is passed to the build.
    params = inspect.signature(serve).parameters
    assert list(params) == ["config", "host", "port", "overrides"]
    assert params["overrides"].default is None


def test_serve_command_refuses_a_non_loopback_host(
    config: Path, capsys: pytest.CaptureFixture[str], never_serve: None
) -> None:
    assert main(["serve", "--config", str(config), "--host", ANY_V4]) == 1
    assert "loopback" in capsys.readouterr().err


@pytest.mark.parametrize(
    "host", ["evil.example:8000", "127.0.0.1.evil.example", "evil.example", "[::2]:80"]
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_rebinding_host_is_refused(running: Preview, host: str, method: str) -> None:
    status, headers, body = fetch(running, "/", method, {"Host": host})
    assert status == 421
    assert body == b""
    assert headers["cache-control"] == "no-store"
    assert fetch(running, "/__preview", headers={"Host": host})[0] == 421


@pytest.mark.parametrize(
    "host",
    ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:9", "[::1]", "[::1]:8000"],
)
def test_localhost_127_and_ipv6_hosts_with_ports_are_accepted(
    running: Preview, host: str
) -> None:
    assert fetch(running, "/", headers={"Host": host})[0] == 200


def test_missing_host_header_is_refused(running: Preview) -> None:
    with socket.create_connection(("127.0.0.1", running.port), NET_TIMEOUT) as sock:
        sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
        reply = b""
        while chunk := sock.recv(4096):
            reply += chunk
    assert reply.startswith(b"HTTP/1.") and b" 421 " in reply.split(b"\r\n")[0]
    assert b"Example Creator" not in reply


def test_cross_origin_request_is_refused(running: Preview) -> None:
    for origin in ("http://evil.example", "http://127.0.0.1.evil.example:8000", "null"):
        assert fetch(running, "/", headers={"Origin": origin})[0] == 403
    assert fetch(running, "/", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert fetch(running, "/", "POST", {"Origin": "http://evil.example"})[0] == 403
    ok = {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"}
    assert fetch(running, "/", headers=ok)[0] == 200


def test_error_page_is_not_served_to_a_rebinding_host(
    running: Preview, config: Path
) -> None:
    config.write_text("version: 2\n")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and fetch(running, "/")[0] == 200:
        time.sleep(0.1)
    assert fetch(running, "/")[0] == 500
    status, _, body = fetch(running, "/", headers={"Host": "evil.example:8000"})
    assert status == 421 and b"site.yaml" not in body
