# SPDX-License-Identifier: Apache-2.0
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import pytest

from fakenet import FakeTransport, reply
from reach_around import __version__
from reach_around.net.egress import EgressBlocked, RawResponse, TransportRequest
from reach_around.net.fetch import (
    FetchError,
    fetch_json,
    parse_retry_after,
)

URL = "https://api.example.social/v1/thing?x=1"
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)


def _no_sleep(seconds: float) -> None:
    return None


def call(
    transport: FakeTransport,
    url: str = URL,
    *,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    sleep: Callable[[float], None] = _no_sleep,
    jitter: Callable[[], float] = lambda: 0.0,
    max_bytes: int = 262_144,
) -> dict[str, object]:
    return fetch_json(
        url,
        transport=transport,
        headers=headers,
        method=method,
        body=body,
        sleep=sleep,
        jitter=jitter,
        now=lambda: NOW,
        max_bytes=max_bytes,
    )


def kind_of(
    transport: FakeTransport,
    url: str = URL,
    *,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    sleep: Callable[[float], None] = _no_sleep,
    max_bytes: int = 262_144,
) -> str:
    with pytest.raises(FetchError) as caught:
        call(
            transport,
            url,
            headers=headers,
            method=method,
            body=body,
            sleep=sleep,
            max_bytes=max_bytes,
        )
    return caught.value.kind


def test_returns_the_json_object_and_sends_the_agent_and_accept() -> None:
    transport = FakeTransport({URL: reply({"followers": 5})})
    assert call(transport) == {"followers": 5}
    sent = transport.calls[0]
    assert sent.headers["User-Agent"] == f"reach-around-counts/{__version__}"
    assert sent.headers["Accept"] == "application/json"
    assert sent.headers["Accept-Encoding"] == "identity"
    assert (sent.host, sent.path, sent.method, sent.port) == (
        "api.example.social",
        "/v1/thing?x=1",
        "GET",
        443,
    )
    assert sent.max_bytes == 262_144 and sent.deadline > 0


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.social/x",
        "ftp://api.example.social/x",
        "https://api.example.social:8443/x",
        "https://user:pw@api.example.social/x",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "https://api.example.social/x#frag",
        "https://api.example.social/a b",
        "//api.example.social/x",
        "https://[::1]/x",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_bad_urls_never_reach_the_transport(url: str) -> None:
    transport = FakeTransport()
    assert kind_of(transport, url) == "bad_url"
    assert transport.calls == []


def test_redirect_is_an_error_by_default_and_never_followed_off_host() -> None:
    transport = FakeTransport(
        {
            URL: reply(
                status=302, headers={"location": "https://10.0.0.5/x"}, body=b""
            ),
            "https://10.0.0.5": reply({"followers": 1}),
        }
    )
    assert kind_of(transport, headers={"Authorization": "Bearer SECRET"}) == "redirect"
    assert [c.host for c in transport.calls] == ["api.example.social"]


@pytest.mark.parametrize(
    "location",
    [
        "https://other.example.social/x",
        "http://api.example.social/x",
        "https://api.example.social:8443/x",
        "//evil.example.net/x",
        "https://127.0.0.1/x",
        "https://api.example.social@evil.example.net/x",
    ],
)
def test_redirect_to_another_host_scheme_or_port_is_an_error(location: str) -> None:
    transport = FakeTransport(
        {URL: reply(status=301, headers={"location": location}, body=b"")}
    )
    assert kind_of(transport) == "redirect"
    assert len(transport.calls) == 1


def test_same_host_https_redirect_is_followed() -> None:
    transport = FakeTransport(
        {
            URL: reply(status=301, headers={"location": "/v2/thing"}, body=b""),
            "https://api.example.social/v2/thing": reply({"followers": 9}),
        }
    )
    assert call(transport) == {"followers": 9}
    assert transport.calls[1].path == "/v2/thing"


def test_redirect_loop_is_bounded() -> None:
    transport = FakeTransport(
        {
            "https://api.example.social/": reply(
                status=302, headers={"location": "/"}, body=b""
            )
        }
    )
    assert kind_of(transport, "https://api.example.social/") == "redirect"
    assert len(transport.calls) <= 5


def test_redirect_without_location_is_an_error() -> None:
    transport = FakeTransport({URL: reply(status=302, body=b"")})
    assert kind_of(transport) == "redirect"


def test_post_is_never_redirected_or_retried() -> None:
    transport = FakeTransport(
        {URL: reply(status=307, headers={"location": "/other"}, body=b"")}
    )
    assert kind_of(transport, method="POST", body=b"a=b") == "redirect"
    assert len(transport.calls) == 1
    flaky = FakeTransport({URL: reply({}, status=503)})
    assert kind_of(flaky, method="POST", body=b"a=b") == "server_error"
    assert len(flaky.calls) == 1
    assert flaky.calls[0].body == b"a=b"


def test_response_over_the_cap_is_refused() -> None:
    big = b'{"x": "' + b"a" * 300_000 + b'"}'
    transport = FakeTransport({URL: reply(body=big)})
    assert kind_of(transport) == "too_large"


def test_response_exactly_at_the_cap_is_accepted() -> None:
    body = b'{"x": "' + b"a" * 91 + b'"}'
    assert len(body) == 100
    assert call(FakeTransport({URL: reply(body=body)}), max_bytes=100)["x"] == "a" * 91
    over = FakeTransport({URL: reply(body=body + b" ")})
    assert kind_of(over, max_bytes=100) == "too_large"


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "text/plain", "application/xml", "application/octet-stream", ""],
)
def test_non_json_content_type_is_refused(content_type: str) -> None:
    transport = FakeTransport(
        {URL: RawResponse(200, {"content-type": content_type}, b'{"a": 1}')}
    )
    assert kind_of(transport) == "content_type"


def test_missing_content_type_is_refused() -> None:
    transport = FakeTransport({URL: RawResponse(200, {}, b'{"a": 1}')})
    assert kind_of(transport) == "content_type"


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "Application/JSON; charset=utf-8", "application/vnd.api+json"],
)
def test_json_content_types_are_accepted(content_type: str) -> None:
    transport = FakeTransport(
        {URL: RawResponse(200, {"content-type": content_type}, b'{"a": 1}')}
    )
    assert call(transport) == {"a": 1}


def test_compressed_answers_are_refused() -> None:
    transport = FakeTransport(
        {URL: reply({"a": 1}, headers={"content-encoding": "gzip"})}
    )
    assert kind_of(transport) == "content_encoding"


@pytest.mark.parametrize(
    "body",
    [b"", b"not json", b"[1, 2]", b'"text"', b"12", b"null", b'{"a": ', b"\xff\xfe{}"],
)
def test_malformed_or_non_object_json_is_refused(body: bytes) -> None:
    assert kind_of(FakeTransport({URL: reply(body=body)})) == "bad_json"


def test_deeply_nested_json_is_a_typed_error_not_a_recursion_error() -> None:
    body = b'{"a":' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    transport = FakeTransport({URL: reply(body=body)})
    assert kind_of(transport, max_bytes=1_000_000) == "bad_json"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "http_400"), (401, "http_401"), (403, "http_403"), (404, "http_404")],
)
def test_client_errors_are_not_retried(status: int, kind: str) -> None:
    transport = FakeTransport({URL: reply({"message": "BODY-TEXT"}, status=status)})
    assert kind_of(transport) == kind
    assert len(transport.calls) == 1


def test_error_text_never_holds_the_response_body() -> None:
    transport = FakeTransport({URL: reply({"message": "BODY-TEXT"}, status=403)})
    with pytest.raises(FetchError) as caught:
        call(transport)
    assert "BODY-TEXT" not in str(caught.value) + repr(caught.value)


def test_429_with_retry_after_sleeps_then_retries() -> None:
    answers = iter(
        [reply({}, status=429, headers={"retry-after": "3"}), reply({"a": 1})]
    )
    sleeps: list[float] = []
    transport = FakeTransport({URL: lambda r: next(answers)})
    assert call(transport, sleep=sleeps.append) == {"a": 1}
    assert sleeps == [3.0]
    assert len(transport.calls) == 2


def test_retry_after_over_the_cap_is_not_retried_after_a_capped_sleep() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        {URL: reply({}, status=429, headers={"retry-after": "9999"})}
    )
    assert kind_of(transport, sleep=sleeps.append) == "rate_limited"
    assert sleeps == [] and len(transport.calls) == 1


def test_retry_after_at_the_cap_is_honored() -> None:
    answers = iter(
        [reply({}, status=503, headers={"retry-after": "60"}), reply({"a": 1})]
    )
    sleeps: list[float] = []
    transport = FakeTransport({URL: lambda r: next(answers)})
    assert call(transport, sleep=sleeps.append) == {"a": 1}
    assert sleeps == [60.0]


def test_jitter_is_added_but_total_sleep_stays_capped() -> None:
    answers = iter(
        [reply({}, status=503, headers={"retry-after": "60"}), reply({"a": 1})]
    )
    sleeps: list[float] = []
    call(
        FakeTransport({URL: lambda r: next(answers)}),
        sleep=sleeps.append,
        jitter=lambda: 0.9,
    )
    assert sleeps == [60.0]
    answers2 = iter(
        [reply({}, status=503, headers={"retry-after": "5"}), reply({"a": 1})]
    )
    sleeps2: list[float] = []
    call(
        FakeTransport({URL: lambda r: next(answers2)}),
        sleep=sleeps2.append,
        jitter=lambda: 0.5,
    )
    assert sleeps2 == [5.5]


def test_5xx_without_header_backs_off_and_gives_up_after_three_attempts() -> None:
    sleeps: list[float] = []
    transport = FakeTransport({URL: reply({}, status=502)})
    assert kind_of(transport, sleep=sleeps.append) == "server_error"
    assert len(transport.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_persistent_429_ends_as_rate_limited() -> None:
    transport = FakeTransport({URL: reply({}, status=429)})
    assert kind_of(transport) == "rate_limited"
    assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "value", ["nan", "inf", "-inf", "", "abc", "1e999", "9" * 100, None]
)
def test_garbled_retry_after_is_ignored(value: str | None) -> None:
    assert parse_retry_after(value, NOW) is None


def test_retry_after_negative_is_clamped_and_date_form_is_read() -> None:
    assert parse_retry_after("-5", NOW) == 0.0
    assert parse_retry_after("Sat, 24 Oct 2026 12:00:30 GMT", NOW) == 30.0
    assert parse_retry_after("Sat, 24 Oct 2026 11:00:00 GMT", NOW) == 0.0


def test_a_hostile_retry_after_never_sleeps_long_or_loops() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        {URL: reply({}, status=429, headers={"retry-after": "nan"})}
    )
    assert kind_of(transport, sleep=sleeps.append) == "rate_limited"
    assert all(0 <= s <= 60 for s in sleeps) and len(transport.calls) == 3


def test_egress_blocks_become_a_fetch_error_with_the_reason_only() -> None:
    def blocked(request: TransportRequest) -> RawResponse:
        raise EgressBlocked("non_public_address", request.host)

    assert kind_of(FakeTransport({URL: blocked})) == "egress_non_public_address"


def test_no_retry_on_a_blocked_or_timed_out_connection() -> None:
    calls: list[int] = []

    def blocked(request: TransportRequest) -> RawResponse:
        calls.append(1)
        raise EgressBlocked("timeout", request.host)

    assert kind_of(FakeTransport({URL: blocked})) == "egress_timeout"
    assert calls == [1]


def test_secret_headers_are_sent_but_never_in_errors() -> None:
    sentinel = "SENTINEL-SECRET-VALUE"
    transport = FakeTransport({URL: reply({"message": sentinel}, status=401)})
    with pytest.raises(FetchError) as caught:
        call(transport, headers={"Authorization": f"Bearer {sentinel}"})
    assert sentinel not in str(caught.value) + repr(caught.value)
    assert transport.calls[0].headers["Authorization"] == f"Bearer {sentinel}"
