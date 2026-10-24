# SPDX-License-Identifier: Apache-2.0
"""Hostname and address checks, and a pinned HTTPS transport (standard library).

The transport resolves the name itself, requires EVERY answer to be a public
address, then connects to the checked address while keeping the original name
for the Host header and the TLS server name. Nothing here reads proxy
variables. Errors carry the host as configured and a reason, never an address.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

Reason = Literal[
    "scheme",
    "port",
    "host_syntax",
    "non_public_address",
    "no_address",
    "resolution_failed",
    "resolution_timeout",
    "connect_failed",
    "tls_failed",
    "timeout",
]

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


class EgressBlocked(Exception):
    """A request was refused before or while connecting."""

    def __init__(self, reason: Reason, host: str = "") -> None:
        self.reason: Reason = reason
        self.host = host[:253]
        super().__init__(f"{reason}: {self.host}" if self.host else reason)


def validate_hostname(host: str) -> str:
    """Return the lowercase ASCII (IDNA) form of a public-looking host name.

    Refuses IP literals in any notation, single-label names, ``localhost`` and
    local suffixes, userinfo, ports, paths, whitespace, control characters,
    ``%``, empty labels and a trailing dot.
    """
    if not isinstance(host, str) or not host or len(host) > 253 * 4:
        raise EgressBlocked("host_syntax", str(host)[:80])
    if any(ch.isspace() or not ch.isprintable() for ch in host) or any(
        ch in host for ch in "@:/\\?#%[]"
    ):
        raise EgressBlocked("host_syntax", host[:80])
    try:
        ascii_host = host.lower().encode("idna").decode("ascii")
    except UnicodeError:
        raise EgressBlocked("host_syntax", host[:80]) from None
    ascii_host = ascii_host.lower()
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or len(labels) < 2:
        raise EgressBlocked("host_syntax", host[:80])
    if not all(_LABEL.match(label) for label in labels):
        raise EgressBlocked("host_syntax", host[:80])
    # 2130706433, 0x7f.1, 127.1 and friends: a real top-level label is never
    # purely numeric and never a hex or octal number
    tld = labels[-1]
    if tld.isdigit() or re.fullmatch(r"0x[0-9a-f]*", tld):
        raise EgressBlocked("host_syntax", host[:80])
    if ascii_host == "localhost" or ascii_host.endswith(_LOCAL_SUFFIXES):
        raise EgressBlocked("host_syntax", host[:80])
    return ascii_host


def is_public_address(
    value: str | ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """True only for globally routable unicast addresses.

    IPv4-mapped, NAT64 and 6to4 addresses are unwrapped and the embedded IPv4
    address is checked; Teredo is refused outright.
    """
    try:
        ip = ipaddress.ip_address(value) if isinstance(value, str) else value
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_address(ip.ipv4_mapped)
        packed = ip.packed
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            return is_public_address(ipaddress.IPv4Address(packed[12:]))
        if ip in ipaddress.ip_network("2002::/16"):
            return is_public_address(ipaddress.IPv4Address(packed[2:6]))
        if ip in ipaddress.ip_network("2001::/32"):
            return False
    return bool(ip.is_global) and not (
        ip.is_multicast
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_unspecified
    )


Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int, *, timeout: float = 5.0) -> list[str]:
    """``getaddrinfo`` on a worker thread so a slow resolver cannot hang us."""
    result: list[list[str]] = []
    failure: list[Exception] = []

    def work() -> None:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            result.append([str(info[4][0]) for info in infos])
        except Exception as exc:
            failure.append(exc)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise EgressBlocked("resolution_timeout", host)
    if failure or not result:
        raise EgressBlocked("resolution_failed", host)
    return result[0]


@dataclass(frozen=True)
class TransportRequest:
    host: str
    path: str  # path and query, already encoded
    headers: Mapping[str, str]
    method: str = "GET"
    body: bytes | None = None
    port: int = 443
    deadline: float = 0.0  # time.monotonic() value; 0 means "use the default"
    max_bytes: int = 262_144


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes  # at most max_bytes + 1 bytes were read


Transport = Callable[[TransportRequest], RawResponse]
Connector = Callable[[str, int, float], socket.socket]
Wrapper = Callable[[socket.socket, str], socket.socket]


def _default_connector(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout=timeout)


def _default_wrapper(sock: socket.socket, server_hostname: str) -> socket.socket:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context.wrap_socket(sock, server_hostname=server_hostname)


class _Watchdog:
    """Closes every registered socket at an absolute deadline.

    Per-operation socket timeouts cannot bound the total: a server that drips
    one byte at a time never trips them. This timer shuts the connection down
    when the deadline passes, which wakes any blocked connect, TLS handshake,
    send or read from another thread.
    """

    def __init__(self, seconds: float) -> None:
        self.fired = False
        self._lock = threading.Lock()
        self._socks: list[socket.socket] = []
        self._timer = threading.Timer(max(seconds, 0.0), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def add(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.append(sock)
            fired = self.fired
        if fired:
            self._kill(sock)

    def _fire(self) -> None:
        with self._lock:
            self.fired = True
            socks = list(self._socks)
        for sock in socks:
            self._kill(sock)

    @staticmethod
    def _kill(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        self._timer.cancel()


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to a checked address; Host header and TLS name stay the host name."""

    def __init__(
        self,
        host: str,
        address: str,
        timeout: float,
        connector: Connector,
        wrapper: Wrapper,
        watchdog: _Watchdog,
    ) -> None:
        super().__init__(host, 443, timeout=timeout)
        self._address = address
        self._connector = connector
        self._wrapper = wrapper
        self._watchdog = watchdog

    def connect(self) -> None:
        sock = self._connector(self._address, 443, float(self.timeout or 10.0))
        self._watchdog.add(sock)  # before the handshake, so a stalled one is cut
        self.sock = self._wrapper(sock, self.host)


class HttpsTransport:
    """The default ``Transport``: resolve, check every answer, connect pinned.

    One absolute deadline covers resolve, connect, TLS handshake, send, header
    parse and body read.
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        connector: Connector = _default_connector,
        wrapper: Wrapper = _default_wrapper,
        clock: Callable[[], float] = time.monotonic,
        total_timeout: float = 10.0,
    ) -> None:
        self._resolver = resolver
        self._connector = connector
        self._wrapper = wrapper
        self._clock = clock
        self._total = total_timeout

    def __call__(self, request: TransportRequest) -> RawResponse:
        host = validate_hostname(request.host)
        if request.port != 443:
            raise EgressBlocked("port", host)
        deadline = request.deadline or (self._clock() + self._total)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise EgressBlocked("timeout", host)
        if self._resolver is None:
            answers = system_resolver(host, 443, timeout=remaining)
        else:
            answers = self._resolver(host, 443)
        if not answers:
            raise EgressBlocked("no_address", host)
        # every answer must be public; a mixed set is refused whole
        if not all(is_public_address(answer) for answer in answers):
            raise EgressBlocked("non_public_address", host)
        address = answers[0]
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise EgressBlocked("timeout", host)
        watchdog = _Watchdog(remaining)
        conn = _PinnedConnection(
            host, address, remaining, self._connector, self._wrapper, watchdog
        )
        try:
            try:
                conn.request(
                    request.method,
                    request.path,
                    body=request.body,
                    headers=dict(request.headers),
                )
                if conn.sock is not None:
                    conn.sock.settimeout(max(deadline - self._clock(), 0.001))
                response = conn.getresponse()
                body = self._read(conn, response, deadline, request.max_bytes + 1, host)
                headers = {key.lower(): value for key, value in response.getheaders()}
                if watchdog.fired:  # a cut connection can look like a short body
                    raise EgressBlocked("timeout", host)
                return RawResponse(response.status, headers, body)
            except EgressBlocked:
                raise
            except TimeoutError:
                raise EgressBlocked("timeout", host) from None
            except ssl.SSLError:
                raise EgressBlocked(
                    "timeout" if watchdog.fired else "tls_failed", host
                ) from None
            except (OSError, http.client.HTTPException, ValueError):
                raise EgressBlocked(
                    "timeout" if watchdog.fired else "connect_failed", host
                ) from None
        finally:
            watchdog.stop()
            conn.close()

    def _read(
        self,
        conn: http.client.HTTPSConnection,
        response: http.client.HTTPResponse,
        deadline: float,
        limit: int,
        host: str,
    ) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while size < limit:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise EgressBlocked("timeout", host)
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            chunk = response.read(min(8192, limit - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks)
