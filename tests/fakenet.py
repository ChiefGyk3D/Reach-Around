# SPDX-License-Identifier: Apache-2.0
"""Offline stand-ins for the network: a fake transport and a fake socket."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from typing import Any

from reach_around.net.egress import RawResponse, TransportRequest

JSON = {"content-type": "application/json"}


def reply(
    payload: object = None,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> RawResponse:
    merged = dict(JSON)
    merged.update(headers or {})
    data = body if body is not None else json.dumps(payload).encode()
    return RawResponse(status, merged, data)


Handler = Callable[[TransportRequest], RawResponse]


class FakeTransport:
    """Routes requests by URL prefix; records every request it sees."""

    def __init__(self, routes: Mapping[str, RawResponse | Handler] | None = None):
        self.routes = dict(routes or {})
        self.calls: list[TransportRequest] = []

    def __call__(self, request: TransportRequest) -> RawResponse:
        self.calls.append(request)
        url = f"https://{request.host}{request.path}"
        for prefix, target in self.routes.items():
            if url.startswith(prefix):
                return target(request) if callable(target) else target
        return reply({"error": "unrouted"}, status=404)

    @property
    def urls(self) -> list[str]:
        return [f"https://{c.host}{c.path}" for c in self.calls]


class FakeSock:
    """Just enough socket for ``http.client`` to talk to a canned answer."""

    def __init__(self, response: bytes) -> None:
        self.sent = b""
        self._response = response
        self.timeouts: list[float | None] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent += bytes(data)

    def makefile(self, mode: str = "rb", *args: Any, **kwargs: Any) -> io.BytesIO:
        return io.BytesIO(self._response)

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)

    def close(self) -> None:
        self.closed = True


def http_answer(
    body: bytes, status: str = "200 OK", headers: Mapping[str, str] | None = None
) -> bytes:
    head = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    head.update(headers or {})
    lines = [f"HTTP/1.1 {status}"] + [f"{k}: {v}" for k, v in head.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body
