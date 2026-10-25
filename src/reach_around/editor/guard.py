# SPDX-License-Identifier: Apache-2.0
"""The request guard for the local editor.

Every request passes ``Guard.check`` before a route runs. It is a pure function
of the method, the request target and the headers, so it is tested without a
socket. A refusal is a ``Verdict`` with a status and a fixed code; the message
shown to the client never echoes a header or the token (T-24, T-35).

Order: duplicate headers (400), Host with the exact port (421), method (405),
Sec-Fetch-Site and Origin (403), token on API paths (401), then for POST the
content type (415), transfer encoding (400) and length (411, 400, 413).
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Protocol

API_PREFIX = "/__edit/api/"
UPLOAD_PATH = "/__edit/api/upload"
JSON_CAP = 64 * 1024
UPLOAD_CAP = 8 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
ALLOWED_METHODS = ("GET", "POST")

_HOST = re.compile(
    r"(127\.0\.0\.1|localhost|\[::1\]):([1-9][0-9]{0,4})", re.ASCII | re.IGNORECASE
)
_LENGTH = re.compile(r"[0-9]{1,12}", re.ASCII)
# Headers whose repetition could make two parsers disagree (smuggling, rebinding).
_SINGLE = (
    "Host",
    "Origin",
    "Sec-Fetch-Site",
    "X-RA-Token",
    "Content-Type",
    "Content-Length",
)


class HeaderSource(Protocol):
    """What ``Guard`` needs from a header collection (``email.message.Message``)."""

    def get(self, name: str, failobj: None = None) -> str | None: ...

    def get_all(self, name: str, failobj: None = None) -> list[str] | None: ...


@dataclass(frozen=True)
class Verdict:
    """A refusal: the HTTP status and a short fixed code."""

    status: int
    code: str


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


class Guard:
    """Checks one request against the rules of one listener (one port)."""

    def __init__(self, port: int, token: str) -> None:
        self._port = port
        self._token = token.encode("utf-8")

    @property
    def port(self) -> int:
        return self._port

    def allowed_host(self, host: str | None) -> str | None:
        """The normalized Host (lower case) when it names this listener, else None."""
        if host is None:
            return None
        match = _HOST.fullmatch(host)
        if match is None or int(match.group(2)) != self._port:
            return None
        return host.lower()

    def token_ok(self, supplied: str | None) -> bool:
        if supplied is None:
            return False
        try:
            candidate = supplied.encode("latin-1")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(candidate, self._token)

    def check(self, method: str, target: str, headers: HeaderSource) -> Verdict | None:
        for name in _SINGLE:
            values = headers.get_all(name)
            if values is not None and len(values) > 1:
                return Verdict(400, "bad_request")
        host = self.allowed_host(_text(headers.get("Host")))
        if host is None:
            return Verdict(421, "misdirected")
        if method not in ALLOWED_METHODS:
            return Verdict(405, "method_not_allowed")
        fetch_site = _text(headers.get("Sec-Fetch-Site"))
        if fetch_site is not None:
            allowed = ("same-origin", "none") if method == "GET" else ("same-origin",)
            if fetch_site not in allowed:
                return Verdict(403, "forbidden")
        origin = _text(headers.get("Origin"))
        if origin is not None:
            if origin.lower() != f"http://{host}":
                return Verdict(403, "forbidden")
        elif method == "POST":
            return Verdict(403, "forbidden")
        path = target.split("?", 1)[0]
        if path.startswith(API_PREFIX) and not self.token_ok(
            _text(headers.get("X-RA-Token"))
        ):
            return Verdict(401, "unauthorized")
        if method == "POST":
            return self._check_body_headers(path, headers)
        return None

    @staticmethod
    def _check_body_headers(path: str, headers: HeaderSource) -> Verdict | None:
        upload = path == UPLOAD_PATH
        wanted = "application/octet-stream" if upload else "application/json"
        if _text(headers.get("Content-Type")) != wanted:
            return Verdict(415, "unsupported_media_type")
        if headers.get("Transfer-Encoding") is not None:
            return Verdict(400, "bad_request")
        length = _text(headers.get("Content-Length"))
        if length is None:
            return Verdict(411, "length_required")
        if _LENGTH.fullmatch(length) is None:
            return Verdict(400, "bad_request")
        if int(length) > (UPLOAD_CAP if upload else JSON_CAP):
            return Verdict(413, "payload_too_large")
        return None


class PreviewGuard:
    """The preview listener's rules: Host with its own port, GET or HEAD, no token.

    The preview serves only the public build output, so there is no token; it
    still refuses a rebound name (421) and any write method (405).
    """

    def __init__(self, port: int) -> None:
        self._guard = Guard(port, "")

    def allowed_host(self, host: str | None) -> str | None:
        return self._guard.allowed_host(host)

    def check(self, method: str, target: str, headers: HeaderSource) -> Verdict | None:
        for name in ("Host",):
            values = headers.get_all(name)
            if values is not None and len(values) > 1:
                return Verdict(400, "bad_request")
        if self.allowed_host(_text(headers.get("Host"))) is None:
            return Verdict(421, "misdirected")
        if method not in ("GET", "HEAD"):
            return Verdict(405, "method_not_allowed")
        return None
