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
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from urllib.parse import urljoin, urlsplit

from reach_around import __version__
from reach_around.net.egress import (
    EgressBlocked,
    HttpsTransport,
    RawResponse,
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

    def __init__(self, kind: str, code: str | None = None) -> None:
        self.kind = kind
        # Only a short closed-alphabet error code, set when the caller asked for
        # it (``error_code=True``) on a 400 or 401. Never body text.
        self.code = code
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


_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _error_code(headers: Mapping[str, str], body: bytes) -> str | None:
    """The ``error.code`` word of a small JSON error body.

    Returns only a value of lowercase letters, digits and underscores (40 at
    most); anything else, a wrong type, a large or malformed body gives None.
    """
    if len(body) > 8_192 or not _is_json_type(headers.get("content-type")):
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    err = parsed.get("error") if isinstance(parsed, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if isinstance(code, str) and _ERROR_CODE.match(code):
        return code
    return None


def _is_json_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    return media == "application/json" or (
        media.startswith("application/") and media.endswith("+json")
    )


def _target(url: str) -> tuple[str, str]:
    """(host, path-and-query) of a plain https URL, or FetchError."""
    # Each handler below only records that it fired; the fixed-word error is
    # raised after the handler is left, so the raised error has no __context__
    # (``from None`` alone would still keep the original exception reachable).
    parts = None
    port = None
    hostname = None
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        parts = None
    if parts is None:
        raise FetchError("bad_url")
    if parts.scheme != "https":
        raise FetchError("bad_url")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise FetchError("bad_url")
    if port not in (None, 443) or not hostname:
        raise FetchError("bad_url")
    host = None
    try:
        host = validate_hostname(hostname)
    except EgressBlocked:
        host = None
    if host is None:
        raise FetchError("bad_url")
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
    follow_redirects: bool = True,
    extra_json_types: frozenset[str] = frozenset(),
    max_attempts: int = MAX_ATTEMPTS,
    error_code: bool = False,
) -> dict[str, object]:
    """Request ``url`` and return the JSON object in the answer.

    Only GET is retried: a request with a body is never replayed. Per-attempt
    time is ``timeout`` seconds including connect, send and read.
    """
    try:
        response = _fetch_raw(
            url,
            transport=transport,
            headers=headers,
            method=method,
            body=body,
            sleep=sleep,
            jitter=jitter,
            clock=clock,
            now=now,
            max_bytes=max_bytes,
            timeout=timeout,
            follow_redirects=follow_redirects,
            max_attempts=max_attempts,
            error_code=error_code,
        )
        return _decode(response.headers, response.body, max_bytes, extra_json_types)
    finally:
        # This frame holds the caller's credential-bearing parts too.
        headers = None
        body = None


def _fetch_raw(
    url: str,
    *,
    transport: Transport | None,
    headers: Mapping[str, str] | None,
    method: str,
    body: bytes | None,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
    clock: Callable[[], float],
    now: Callable[[], datetime],
    max_bytes: int,
    timeout: float,
    follow_redirects: bool,
    max_attempts: int,
    error_code: bool,
) -> RawResponse:
    """The request loop shared by ``fetch_json`` and ``fetch_json_array``.

    Returns the first 2xx answer; every other outcome is a ``FetchError``.
    """
    send = transport if transport is not None else HttpsTransport()
    host, path = _target(url)
    base: dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    base.update(headers or {})
    attempts = max(1, min(max_attempts, MAX_ATTEMPTS)) if method == "GET" else 1
    redirects = 0
    attempt = 0
    request: TransportRequest | None = None
    try:
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
            response: RawResponse | None = None
            blocked = ""
            try:
                response = send(request)
            except EgressBlocked as exc:
                blocked = str(exc.reason)
            if response is None:
                raise FetchError(f"egress_{blocked}")
            status = response.status
            if 300 <= status < 400:
                location = response.headers.get("location")
                if (
                    not follow_redirects
                    or method != "GET"
                    or location is None
                    or redirects >= MAX_REDIRECTS
                ):
                    raise FetchError("redirect")
                target: tuple[str, str] | None = None
                try:
                    target = _target(urljoin(f"https://{host}{path}", location))
                except FetchError:
                    target = None
                if target is None:
                    raise FetchError("redirect")
                next_host, next_path = target
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
                code = (
                    _error_code(response.headers, response.body)
                    if error_code and status in (400, 401)
                    else None
                )
                raise FetchError(f"http_{status}", code)
            return response
    finally:
        # Whatever leaves this function (a result, an error, or an interrupt),
        # no frame it can reach keeps the credential-bearing request parts.
        request = None
        base = {}
        headers = None
        body = None


def _decode(
    headers: Mapping[str, str],
    body: bytes,
    max_bytes: int,
    extra_json_types: frozenset[str] = frozenset(),
) -> dict[str, object]:
    parsed = _parse(headers, body, max_bytes, extra_json_types)
    if not isinstance(parsed, dict):
        raise FetchError("bad_json")
    return parsed


ARRAY_MAX_BYTES = 1_048_576  # 1 MiB: the hard ceiling for fetch_json_array
ARRAY_MAX_ITEMS = 1_000
# The only response headers the array fetch hands back, lower-case.
VIEW_HEADERS: tuple[str, ...] = (
    "link",
    "retry-after",
    "date",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
)
_VIEW_VALUE_MAX = 512


@dataclass(frozen=True)
class FetchedArray:
    """A parsed top-level JSON array and a read-only view of a few headers."""

    items: list[object]
    headers: Mapping[str, str]


def fetch_json_array(
    url: str,
    *,
    transport: Transport | None = None,
    headers: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _RNG.random,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_bytes: int = ARRAY_MAX_BYTES,
    timeout: float = TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> FetchedArray:
    """GET ``url`` and return its top-level JSON array plus a header view.

    The same checks as ``fetch_json`` (https, port 443, a validated public host,
    same-host redirects only, identity encoding, JSON content type, fixed error
    words) with three differences: the answer must be an array, the size cap is
    1 MiB and cannot be raised above it, and the result carries a read-only
    view of the headers in ``VIEW_HEADERS``. The view never holds a header that
    was not asked for, so a cookie or a token echoed by a server is dropped.
    GET only: there is no method or body parameter.
    """
    limit = max(1, min(max_bytes, ARRAY_MAX_BYTES))
    fetched: RawResponse | None = None
    try:
        fetched = _fetch_raw(
            url,
            transport=transport,
            headers=headers,
            method="GET",
            body=None,
            sleep=sleep,
            jitter=jitter,
            clock=clock,
            now=now,
            max_bytes=limit,
            timeout=timeout,
            follow_redirects=True,
            max_attempts=max_attempts,
            error_code=False,
        )
    except FetchError:
        raise
    except Exception:
        # An unexpected transport failure may carry the requested path in its
        # text. Drop it here; the fixed word is raised after the handler.
        fetched = None
    finally:
        headers = None
    if fetched is None:
        raise FetchError("transport_error")
    response = fetched
    parsed = _parse(
        response.headers, response.body, limit, frozenset(), strict_numbers=True
    )
    if not isinstance(parsed, list) or len(parsed) > ARRAY_MAX_ITEMS:
        raise FetchError("bad_json")
    return FetchedArray(items=parsed, headers=_header_view(response.headers))


def _header_view(raw: Mapping[str, str]) -> Mapping[str, str]:
    """The read-only view of the headers in ``VIEW_HEADERS`` and no others."""
    # Names are matched case-insensitively. A name present twice (two spellings)
    # is ambiguous, so it is dropped rather than guessed.
    found: dict[str, list[str]] = {}
    for raw_name, raw_value in raw.items():
        if isinstance(raw_name, str) and raw_name.lower() in VIEW_HEADERS:
            found.setdefault(raw_name.lower(), []).append(raw_value)
    view: dict[str, str] = {}
    for name in VIEW_HEADERS:
        values = found.get(name, [])
        if (
            len(values) == 1
            and isinstance(values[0], str)
            and len(values[0]) <= _VIEW_VALUE_MAX
        ):
            view[name] = values[0]
    return MappingProxyType(view)


@dataclass(frozen=True)
class FetchedObject:
    """A parsed top-level JSON object and a read-only view of a few headers."""

    data: dict[str, object]
    headers: Mapping[str, str]


def fetch_json_view(
    url: str,
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _RNG.random,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_bytes: int = ARRAY_MAX_BYTES,
    timeout: float = TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> FetchedObject:
    """GET ``url`` and return its JSON object plus the same header view.

    The object twin of ``fetch_json_array``: the same checks, the same strict
    number rules (no ``NaN``, no overflow, integers of at most 18 digits), the
    same 1 MiB ceiling, GET only, and no caller headers.
    """
    limit = max(1, min(max_bytes, ARRAY_MAX_BYTES))
    fetched: RawResponse | None = None
    try:
        fetched = _fetch_raw(
            url,
            transport=transport,
            headers=None,
            method="GET",
            body=None,
            sleep=sleep,
            jitter=jitter,
            clock=clock,
            now=now,
            max_bytes=limit,
            timeout=timeout,
            follow_redirects=True,
            max_attempts=max_attempts,
            error_code=False,
        )
    except FetchError:
        raise
    except Exception:
        fetched = None
    if fetched is None:
        raise FetchError("transport_error")
    parsed = _parse(
        fetched.headers, fetched.body, limit, frozenset(), strict_numbers=True
    )
    if not isinstance(parsed, dict):
        raise FetchError("bad_json")
    return FetchedObject(data=parsed, headers=_header_view(fetched.headers))


def _parse(
    headers: Mapping[str, str],
    body: bytes,
    max_bytes: int,
    extra_json_types: frozenset[str],
    *,
    strict_numbers: bool = False,
) -> object:
    """Decode a JSON body.

    ``strict_numbers`` (the array path only) refuses NaN, Infinity, overflowing
    or over-long floats and integers above ``MAX_INT_DIGITS`` digits. Off, the
    numbers behave exactly as plain ``json.loads`` (the object path).
    """
    if len(body) > max_bytes:
        raise FetchError("too_large")
    encoding = headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise FetchError("content_encoding")
    media = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not _is_json_type(headers.get("content-type")) and media not in extra_json_types:
        raise FetchError("content_type")
    parsed: object = None
    ok = False
    try:
        text = body.decode("utf-8")
        if strict_numbers:
            parsed = json.loads(
                text,
                parse_constant=_refuse_constant,
                parse_int=_bounded_int,
                parse_float=_finite_float,
            )
        else:
            parsed = json.loads(text)
        ok = True
    except (UnicodeDecodeError, ValueError, RecursionError):
        ok = False
    if not ok:
        raise FetchError("bad_json")
    return parsed


# At most this many digits, not counting a leading minus sign. The plumbing
# refuses every integer above 18 digits, so a 19-digit 64-bit-style id is
# refused by design: a provider that needs larger ids must read them as strings.
MAX_INT_DIGITS = 18


def _refuse_constant(name: str) -> object:
    """NaN, Infinity and -Infinity are not JSON."""
    raise ValueError("not a JSON constant")


def _finite_float(text: str) -> float:
    """A float that is finite (``1e999`` would overflow to infinity)."""
    if len(text) > 64:
        raise ValueError("number too long")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("not finite")
    return value


def _bounded_int(text: str) -> int:
    """A whole number of at most ``MAX_INT_DIGITS`` digits; longer are refused.

    JSON grammar allows an optional leading minus and no plus sign or leading
    zeros, so the digits are the text without that one minus sign.
    """
    digits = text[1:] if text.startswith("-") else text
    if len(digits) > MAX_INT_DIGITS:
        raise ValueError("integer too long")
    return int(text)
