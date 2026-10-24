# SPDX-License-Identifier: Apache-2.0
import ast
import socket
import time
from pathlib import Path
from typing import cast

import pytest

import reach_around
from fakenet import FakeSock, http_answer
from reach_around.net.egress import (
    EgressBlocked,
    HttpsTransport,
    TransportRequest,
    is_public_address,
    system_resolver,
    validate_hostname,
)

PUBLIC = "93.184.216.34"


@pytest.mark.parametrize("ip", [PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_allowed(ip: str) -> None:
    assert is_public_address(ip)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "198.18.0.1",
        "192.0.0.8",
        ".".join(["0"] * 4),
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",
        "ff02::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.5",
        "64:ff9b::a00:5",
        "2002:a00:5::1",
        "2001::1",
        "2001:db8::1",
        "not an address",
    ],
)
def test_non_public_addresses_refused(ip: str) -> None:
    assert not is_public_address(ip)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("example.social", "example.social"),
        ("Mastodon.Example.ORG", "mastodon.example.org"),
        ("bücher.example", "xn--bcher-kva.example"),
    ],
)
def test_hostname_accepted(given: str, expected: str) -> None:
    assert validate_hostname(given) == expected


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "2130706433",
        "0x7f000001",
        "0177.0.0.1",
        "127.1",
        "0x7f.0x0.0x0.0x1",
        "[::1]",
        "::1",
        "localhost",
        "foo.localhost",
        "internal",
        "box.internal",
        "printer.local",
        "a b",
        "u@example.social",
        "example.social:8080",
        "example.social/x",
        "example.social%2f",
        "example.social.",
        "a..b.example",
        "a" * 64 + ".example",
        ("a" * 60 + ".") * 5 + "example",
        "-bad.example",
        "",
        "exa\x00mple.social",
    ],
)
def test_hostname_refused(host: str) -> None:
    with pytest.raises(EgressBlocked) as caught:
        validate_hostname(host)
    assert caught.value.reason == "host_syntax"


def _request(host: str = "example.social", port: int = 443) -> TransportRequest:
    return TransportRequest(
        host=host,
        path="/x?a=b",
        headers={"User-Agent": "t", "X-Probe": "1"},
        port=port,
        max_bytes=1000,
    )


class Spy:
    """Connector + wrapper that record what the transport asked for."""

    def __init__(self, response: bytes) -> None:
        self.connects: list[tuple[str, int]] = []
        self.sni: list[str] = []
        self.sock = FakeSock(response)

    def connector(self, address: str, port: int, timeout: float) -> socket.socket:
        self.connects.append((address, port))
        return cast(socket.socket, self.sock)

    def wrapper(self, sock: socket.socket, server_hostname: str) -> socket.socket:
        self.sni.append(server_hostname)
        return sock


def _transport(spy: Spy, answers: list[str]) -> tuple[HttpsTransport, list[str]]:
    asked: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        asked.append(host)
        return answers

    return (
        HttpsTransport(resolver=resolver, connector=spy.connector, wrapper=spy.wrapper),
        asked,
    )


def test_connects_to_the_checked_address_and_keeps_the_name() -> None:
    spy = Spy(http_answer(b'{"a": 1}'))
    transport, asked = _transport(spy, [PUBLIC])
    response = transport(_request())
    assert response.status == 200 and response.body == b'{"a": 1}'
    assert spy.connects == [(PUBLIC, 443)]
    assert spy.sni == ["example.social"]
    assert asked == ["example.social"]
    sent = spy.sock.sent.decode()
    assert sent.startswith("GET /x?a=b HTTP/1.1\r\n")
    assert "Host: example.social\r\n" in sent
    assert PUBLIC not in sent


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["::1"],
        [PUBLIC, "169.254.169.254"],  # mixed: refused whole
        ["10.0.0.5", PUBLIC],
        [PUBLIC, "::1"],  # a private AAAA beside a public A
    ],
)
def test_private_or_mixed_answers_are_refused_before_connecting(
    answers: list[str],
) -> None:
    spy = Spy(http_answer(b"{}"))
    transport, _ = _transport(spy, answers)
    with pytest.raises(EgressBlocked) as caught:
        transport(_request())
    assert caught.value.reason == "non_public_address"
    assert spy.connects == []
    assert "127.0.0.1" not in str(caught.value) and "169.254" not in str(caught.value)


def test_empty_resolution_refused() -> None:
    spy = Spy(http_answer(b"{}"))
    transport, _ = _transport(spy, [])
    with pytest.raises(EgressBlocked) as caught:
        transport(_request())
    assert caught.value.reason == "no_address"


@pytest.mark.parametrize("port", [80, 8443, 22, 6379])
def test_port_other_than_443_refused(port: int) -> None:
    spy = Spy(http_answer(b"{}"))
    transport, asked = _transport(spy, [PUBLIC])
    with pytest.raises(EgressBlocked) as caught:
        transport(_request(port=port))
    assert caught.value.reason == "port"
    assert asked == [] and spy.connects == []


def test_ip_literal_host_refused_before_resolving() -> None:
    spy = Spy(http_answer(b"{}"))
    transport, asked = _transport(spy, [PUBLIC])
    with pytest.raises(EgressBlocked):
        transport(_request(host="127.0.0.1"))
    assert asked == [] and spy.connects == []


def test_body_is_capped_at_limit_plus_one() -> None:
    spy = Spy(http_answer(b"x" * 5000))
    transport, _ = _transport(spy, [PUBLIC])
    response = transport(_request())
    assert len(response.body) == 1001


def test_response_headers_are_lowercased() -> None:
    spy = Spy(http_answer(b"{}", headers={"Retry-After": "7"}))
    transport, _ = _transport(spy, [PUBLIC])
    assert transport(_request()).headers["retry-after"] == "7"


def test_deadline_in_the_past_times_out_without_connecting() -> None:
    spy = Spy(http_answer(b"{}"))
    transport, _ = _transport(spy, [PUBLIC])
    request = TransportRequest(
        host="example.social", path="/", headers={}, deadline=1e-9, max_bytes=10
    )
    with pytest.raises(EgressBlocked) as caught:
        transport(request)
    assert caught.value.reason == "timeout"
    assert spy.connects == []


def test_connect_errors_are_a_typed_error_without_the_address() -> None:
    def connector(address: str, port: int, timeout: float) -> socket.socket:
        raise ConnectionRefusedError(f"refused {address}")

    transport = HttpsTransport(resolver=lambda h, p: [PUBLIC], connector=connector)
    with pytest.raises(EgressBlocked) as caught:
        transport(_request())
    assert caught.value.reason == "connect_failed"
    assert PUBLIC not in str(caught.value)


def test_garbled_http_is_a_typed_error() -> None:
    spy = Spy(b"this is not http\r\n\r\n")
    transport, _ = _transport(spy, [PUBLIC])
    with pytest.raises(EgressBlocked) as caught:
        transport(_request())
    assert caught.value.reason == "connect_failed"


def test_slow_resolver_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*args: object, **kwargs: object) -> list[object]:
        time.sleep(3)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    with pytest.raises(EgressBlocked) as caught:
        system_resolver("example.social", 443, timeout=0.1)
    assert caught.value.reason == "resolution_timeout"


def test_resolution_failure_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> list[object]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(EgressBlocked) as caught:
        system_resolver("example.social", 443)
    assert caught.value.reason == "resolution_failed"


# ---- guard: nothing but net/egress.py opens network connections ------------------

_FORBIDDEN = {"urllib.request", "http.client", "socket", "requests", "httpx"}
_ALLOWED = {"egress.py", "preview.py"}  # preview.py serves loopback only


def _network_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name in _FORBIDDEN]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found += [
                name
                for name in [node.module]
                + [f"{node.module}.{a.name}" for a in node.names]
                if name in _FORBIDDEN
            ]
    return found


def test_only_the_egress_module_touches_network_libraries() -> None:
    root = Path(reach_around.__file__).parent
    files = sorted(root.rglob("*.py"))
    assert len(files) > 10
    offenders = {
        str(path.relative_to(root)): hits
        for path in files
        if path.name not in _ALLOWED
        for hits in [_network_imports(path.read_text(encoding="utf-8"))]
        if hits
    }
    assert offenders == {}


def test_the_guard_flags_a_module_that_opens_connections() -> None:
    assert _network_imports("import urllib.request\n") == ["urllib.request"]
    assert _network_imports("from urllib import request\n") == ["urllib.request"]
    assert _network_imports("from http import client\n") == ["http.client"]
    assert _network_imports("import socket\n") == ["socket"]
    assert _network_imports("import json\n") == []
