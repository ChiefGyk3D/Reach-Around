# SPDX-License-Identifier: Apache-2.0
"""``fetch_json``: one hardened JSON request over an injectable transport.

https only; no redirects except same host over https; one total timeout per
attempt; a response size cap; JSON content types only; bounded retries of
429 and 5xx for GET only, honoring ``Retry-After`` up to a cap. A failure is a
``FetchError`` whose ``kind`` is a short fixed word. Error text never holds a
response body, a header value or a secret.
"""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from reach_around import __version__
from reach_around.net.egress import (
    EgressBlocked,
    HttpsTransport,
    Transport,
    TransportRequest,
    validate_hostname,
)

USER_AGENT = f"reach-around-counts/{__version__}"
MAX_BYTES = 262_144
TIMEOUT = 10.0
MAX_RETRY_AFTER = 60.0
MAX_ATTEMPTS = 3
MAX_REDIRECTS = 3
_RNG = random.SystemRandom()


class FetchError(Exception):
    """A request failed. ``kind`` is a fixed word such as ``http_404``."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait, or None when absent, garbled, negative or not finite."""
    if value is None or len(value) > 64:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - now).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(seconds, 0.0)


def _is_json_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    return media == "application/json" or (
        media.startswith("application/") and media.endswith("+json")
    )


def _target(url: str) -> tuple[str, str]:
    """(host, path-and-query) of a plain https URL, or FetchError."""
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise FetchError("bad_url") from None
    if parts.scheme != "https":
        raise FetchError("bad_url")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise FetchError("bad_url")
    if port not in (None, 443) or not hostname:
        raise FetchError("bad_url")
    try:
        host = validate_hostname(hostname)
    except EgressBlocked:
        raise FetchError("bad_url") from None
    path = parts.path or "/"
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in path + parts.query):
        raise FetchError("bad_url")
    return host, path + (f"?{parts.query}" if parts.query else "")


def fetch_json(
    url: str,
    *,
    transport: Transport | None = None,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _RNG.random,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_bytes: int = MAX_BYTES,
    timeout: float = TIMEOUT,
) -> dict[str, object]:
    """Request ``url`` and return the JSON object in the answer.

    Only GET is retried: a request with a body is never replayed. Per-attempt
    time is ``timeout`` seconds including connect, send and read.
    """
    send = transport if transport is not None else HttpsTransport()
    host, path = _target(url)
    base: dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    base.update(headers or {})
    attempts = MAX_ATTEMPTS if method == "GET" else 1
    redirects = 0
    attempt = 0
    while True:
        attempt += 1
        request = TransportRequest(
            host=host,
            path=path,
            headers=base,
            method=method,
            body=body,
            deadline=clock() + timeout,
            max_bytes=max_bytes,
        )
        try:
            response = send(request)
        except EgressBlocked as exc:
            raise FetchError(f"egress_{exc.reason}") from None
        status = response.status
        if 300 <= status < 400:
            location = response.headers.get("location")
            if method != "GET" or location is None or redirects >= MAX_REDIRECTS:
                raise FetchError("redirect")
            try:
                next_host, next_path = _target(
                    urljoin(f"https://{host}{path}", location)
                )
            except FetchError:
                raise FetchError("redirect") from None
            if next_host != host:
                raise FetchError("redirect")
            redirects += 1
            path = next_path
            continue
        if status == 429 or status >= 500:
            kind = "rate_limited" if status == 429 else "server_error"
            if attempt >= attempts:
                raise FetchError(kind)
            wait = parse_retry_after(response.headers.get("retry-after"), now())
            if wait is None:
                wait = min(2.0 ** (attempt - 1), MAX_RETRY_AFTER)
            elif wait > MAX_RETRY_AFTER:
                raise FetchError(kind)
            sleep(min(wait + jitter(), MAX_RETRY_AFTER))
            continue
        if not 200 <= status < 300:
            raise FetchError(f"http_{status}")
        return _decode(response.headers, response.body, max_bytes)


def _decode(
    headers: Mapping[str, str], body: bytes, max_bytes: int
) -> dict[str, object]:
    if len(body) > max_bytes:
        raise FetchError("too_large")
    encoding = headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise FetchError("content_encoding")
    if not _is_json_type(headers.get("content-type")):
        raise FetchError("content_type")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise FetchError("bad_json") from None
    if not isinstance(parsed, dict):
        raise FetchError("bad_json")
    return parsed
