# SPDX-License-Identifier: Apache-2.0
"""Slice 1 of the fediverse collector (#921): array fetch, window, row, probe.

Hermetic: a fake transport serves everything; nothing opens a socket."""

from __future__ import annotations

import dataclasses
import importlib.util
import io
import json
import re
import sys
import types
import unittest.mock
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from fakenet import FakeTransport, reply
from reach_around.counts import fediverse_rows as rows
from reach_around.counts import fediverse_window as window
from reach_around.net.egress import EgressBlocked, RawResponse, TransportRequest
from reach_around.net.fetch import (
    ARRAY_MAX_BYTES,
    MAX_INT_DIGITS,
    USER_AGENT,
    FetchedArray,
    FetchError,
    fetch_json,
    fetch_json_array,
)

ROOT = Path(__file__).resolve().parents[1]
URL = "https://social.example/api/v1/accounts/1/statuses?limit=20"
NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)


def _no_sleep(seconds: float) -> None:
    return None


def get(
    transport: FakeTransport, url: str = URL, max_bytes: int = ARRAY_MAX_BYTES
) -> FetchedArray:
    return fetch_json_array(
        url,
        transport=transport,
        sleep=_no_sleep,
        jitter=lambda: 0.0,
        now=lambda: NOW,
        max_bytes=max_bytes,
    )


def kind(
    transport: FakeTransport, url: str = URL, max_bytes: int = ARRAY_MAX_BYTES
) -> str:
    with pytest.raises(FetchError) as caught:
        get(transport, url, max_bytes)
    return caught.value.kind


# --- fetch_json_array ---------------------------------------------------------


def test_returns_the_array_and_sends_the_fixed_agent() -> None:
    t = FakeTransport({URL: reply([{"a": 1}, {"a": 2}])})
    result = get(t)
    assert result.items == [{"a": 1}, {"a": 2}]
    sent = t.calls[0]
    assert sent.headers["User-Agent"] == USER_AGENT
    assert sent.headers["Accept-Encoding"] == "identity"
    assert sent.method == "GET"
    assert sent.max_bytes == ARRAY_MAX_BYTES


def test_an_object_is_bad_json_and_fetch_json_still_refuses_an_array() -> None:
    assert kind(FakeTransport({URL: reply({"a": 1})})) == "bad_json"
    t = FakeTransport({URL: reply([1])})
    with pytest.raises(FetchError) as caught:
        fetch_json(URL, transport=t)
    assert caught.value.kind == "bad_json"


def test_header_view_is_read_only_and_holds_only_named_headers() -> None:
    t = FakeTransport(
        {
            URL: reply(
                [],
                headers={
                    "link": '<https://elsewhere.example/n>; rel="next"',
                    "retry-after": "5",
                    "x-ratelimit-remaining": "299",
                    "date": "Thu, 26 Nov 2026 12:00:00 GMT",
                    "set-cookie": "session=SECRET",
                    "authorization": "SECRET",
                },
            )
        }
    )
    view = get(t).headers
    assert isinstance(view, MappingProxyType)
    assert view["x-ratelimit-remaining"] == "299"
    assert view["retry-after"] == "5"
    assert "link" in view
    assert "set-cookie" not in view and "authorization" not in view
    assert "SECRET" not in json.dumps(dict(view))
    writable: Any = view
    with pytest.raises(TypeError):
        writable["x"] = "y"


def test_header_names_are_matched_case_insensitively_and_duplicates_dropped() -> None:
    t = FakeTransport(
        {
            URL: reply(
                [],
                headers={
                    "Link": "<https://social.example/n>",
                    "Retry-After": "7",
                    "DATE": "Thu, 26 Nov 2026 12:00:00 GMT",
                    "x-ratelimit-remaining": "1",
                    "X-RateLimit-Remaining": "999",  # two spellings: ambiguous
                },
            )
        }
    )
    view = get(t).headers
    assert view["link"] == "<https://social.example/n>"
    assert view["retry-after"] == "7"
    assert view["date"] == "Thu, 26 Nov 2026 12:00:00 GMT"
    assert "x-ratelimit-remaining" not in view  # never guess between two values


@pytest.mark.parametrize(
    "body",
    [
        b"[NaN]",
        b"[Infinity]",
        b"[-Infinity]",
        b"[1e999]",
        b"[-1e999]",
        b"[" + b"9" * 40 + b"]",
        b"[-" + b"9" * 19 + b"]",
        b"[1." + b"0" * 100 + b"]",
    ],
)
def test_non_json_numbers_and_huge_integers_are_bad_json(body: bytes) -> None:
    assert kind(FakeTransport({URL: reply(body=body)})) == "bad_json"
    with pytest.raises(FetchError) as caught:
        fetch_json(
            URL,
            transport=FakeTransport({URL: reply(body=b'{"a": ' + body + b"}")}),
            sleep=_no_sleep,
            jitter=lambda: 0.0,
            now=lambda: NOW,
        )
    assert caught.value.kind == "bad_json"


def test_ordinary_numbers_still_parse() -> None:
    body = b"[0, -5, 1.5, 1e3, " + b"9" * 18 + b"]"
    assert len(get(FakeTransport({URL: reply(body=body)})).items) == 5


def test_unexpected_transport_failure_is_a_fixed_word_with_no_path() -> None:
    def boom(request: TransportRequest) -> RawResponse:
        raise RuntimeError(f"failed for {request.host}{request.path} person.example")

    with pytest.raises(FetchError) as caught:
        get(FakeTransport({"https://": boom}))
    assert caught.value.kind == "transport_error"
    assert str(caught.value) == "transport_error"
    # Stronger than suppressing the display: the original is not chained at all.
    assert caught.value.__cause__ is None and caught.value.__context__ is None

    def stop(request: TransportRequest) -> RawResponse:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        get(FakeTransport({"https://": stop}))


def test_link_header_is_never_followed() -> None:
    t = FakeTransport(
        {URL: reply([], headers={"link": '<https://evil.example/x>; rel="next"'})}
    )
    get(t)
    assert [c.host for c in t.calls] == ["social.example"]


def test_size_cap_is_one_mib_and_cannot_be_raised() -> None:
    small = b'["' + b"a" * 1_000_000 + b'"]'  # about 1 MB, under 1 MiB
    assert len(small) < ARRAY_MAX_BYTES
    assert len(get(FakeTransport({URL: reply(body=small)})).items) == 1
    big = b'["' + b"a" * 1_100_000 + b'"]'
    assert len(big) > ARRAY_MAX_BYTES
    t = FakeTransport({URL: reply(body=big)})
    assert kind(t, max_bytes=50_000_000) == "too_large"
    assert t.calls[0].max_bytes == ARRAY_MAX_BYTES
    assert kind(FakeTransport({URL: reply(body=small)}), max_bytes=1000) == "too_large"


def test_too_many_items_is_bad_json() -> None:
    body = json.dumps([0] * 1001).encode()
    assert kind(FakeTransport({URL: reply(body=body)})) == "bad_json"


def test_timeout_is_set_per_attempt_and_a_transport_timeout_is_a_fixed_word() -> None:
    t = FakeTransport({URL: reply([])})
    fetch_json_array(
        URL, transport=t, timeout=3.0, clock=lambda: 1000.0, now=lambda: NOW
    )
    assert t.calls[0].deadline == 1003.0

    def boom(request: TransportRequest) -> RawResponse:
        raise EgressBlocked("timeout", "social.example")

    with pytest.raises(FetchError) as caught:
        fetch_json_array(URL, transport=boom)
    assert caught.value.kind == "egress_timeout"
    assert "social.example" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://social.example/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://2130706433/x",
        "https://0x7f.0.0.1/x",
        "https://10.0.0.1/x",
        "https://192.168.1.1/x",
        "https://localhost/x",
        "https://printer.local/x",
        "https://user:pw@social.example/x",
        "https://user@social.example/x",
        "https://social.example:8443/x",
        "https://social.example./x",
        "https://social.example/x#frag",
        "https://social.example/a b",
        "ftp://social.example/x",
        "https:///x",
    ],
)
def test_bad_urls_are_refused_before_any_request(url: str) -> None:
    t = FakeTransport({"https://": reply([])})
    assert kind(t, url) == "bad_url"
    assert t.calls == []


def test_uppercase_and_unicode_hosts_are_normalized_not_trusted() -> None:
    t = FakeTransport({"https://social.example/": reply([])})
    get(t, "https://SOCIAL.Example/x")
    assert t.calls[0].host == "social.example"
    t2 = FakeTransport({"https://xn--bcher-kva.example/": reply([])})
    get(t2, "https://bücher.example/x")
    assert t2.calls[0].host == "xn--bcher-kva.example"


def _redirect(location: str) -> FakeTransport:
    return FakeTransport(
        {
            URL: reply([], status=302, headers={"location": location}),
            "https://social.example/moved": reply([1]),
            "https://other.example/": reply([2]),
        }
    )


def test_same_host_redirect_is_followed() -> None:
    t = _redirect("/moved")
    assert get(t).items == [1]
    assert [c.host for c in t.calls] == ["social.example", "social.example"]


@pytest.mark.parametrize(
    "location",
    [
        "https://other.example/x",
        "http://social.example/moved",
        "https://10.0.0.1/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest",
        "https://user:pw@social.example/moved",
        "https://social.example:8443/moved",
    ],
)
def test_a_redirect_elsewhere_is_refused_and_never_requested(location: str) -> None:
    t = _redirect(location)
    assert kind(t) == "redirect"
    assert [c.host for c in t.calls] == ["social.example"]


def test_redirect_loop_is_bounded() -> None:
    t = FakeTransport(
        {
            "https://social.example/": reply(
                [], status=302, headers={"location": "/again"}
            )
        }
    )
    assert kind(t) == "redirect"
    assert len(t.calls) <= 4


def test_errors_never_echo_remote_text_or_the_token() -> None:
    sentinel = "REMOTE-SECRET-TEXT"
    for status in (400, 401, 404, 429, 500):
        t = FakeTransport(
            {
                URL: reply(
                    {"error": sentinel}, status=status, headers={"retry-after": "0"}
                )
            }
        )
        with pytest.raises(FetchError) as caught:
            fetch_json_array(
                URL,
                transport=t,
                headers={"Authorization": "Bearer TOKEN-SENTINEL"},
                sleep=_no_sleep,
                jitter=lambda: 0.0,
                now=lambda: NOW,
            )
        err = caught.value
        text = f"{err!s} {err!r} {err.kind} {err.code}"
        assert sentinel not in text and "TOKEN-SENTINEL" not in text
        assert err.code is None


def test_wrong_content_type_and_encoding_are_fixed_words() -> None:
    html = RawResponse(200, {"content-type": "text/html"}, b"[]")
    assert kind(FakeTransport({URL: html})) == "content_type"
    gz = RawResponse(
        200, {"content-type": "application/json", "content-encoding": "gzip"}, b"[]"
    )
    assert kind(FakeTransport({URL: gz})) == "content_encoding"
    bad = RawResponse(200, {"content-type": "application/json"}, b"[1,")
    assert kind(FakeTransport({URL: bad})) == "bad_json"


def test_429_retries_then_reports_rate_limited() -> None:
    t = FakeTransport({URL: reply([], status=429, headers={"retry-after": "1"})})
    assert kind(t) == "rate_limited"
    assert len(t.calls) == 3


# --- window -------------------------------------------------------------------


def _iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (-timedelta(days=30), "in"),  # exactly 30 days: inside
        (-timedelta(days=30, seconds=1), "before"),
        (-timedelta(days=29, hours=23), "in"),
        (timedelta(0), "in"),
        (timedelta(minutes=10), "in"),  # exactly the skew
        (timedelta(minutes=10, seconds=1), "future"),
        (timedelta(days=2), "future"),
    ],
)
def test_window_boundaries(delta: timedelta, expected: str) -> None:
    when = window.parse_server_time(_iso(delta))
    assert window.classify(when, NOW) == expected
    assert window.in_window(when, NOW) is (expected == "in")


def test_window_start_is_thirty_days_before_now() -> None:
    assert window.window_start(NOW) == NOW - timedelta(days=30)


def test_offsets_are_converted_and_naive_or_garbled_times_are_invalid() -> None:
    plus5 = timezone(timedelta(hours=5))
    local = (NOW - timedelta(days=30)).astimezone(plus5).isoformat()
    assert window.in_window(window.parse_server_time(local), NOW)
    bad_values: list[object] = [
        "2026-11-26T12:00:00",
        "yesterday",
        "",
        None,
        5,
        "2026-13-45T00:00:00Z",
        "x" * 100,
    ]
    for bad in bad_values:
        assert window.parse_server_time(bad) is None
        assert window.classify(window.parse_server_time(bad), NOW) == "invalid"
    assert window.classify(datetime(2026, 11, 26), NOW) == "invalid"  # naive datetime
    with pytest.raises(ValueError):
        window.classify(NOW, datetime(2026, 11, 26))  # naive run instant


def test_clock_is_injected_not_read() -> None:
    later = NOW + timedelta(days=40)
    when = window.parse_server_time(_iso(timedelta(0)))
    assert window.in_window(when, NOW)
    assert not window.in_window(when, later)


def test_both_server_time_fields_work_and_author_createdat_is_refused() -> None:
    stamp = _iso(-timedelta(days=1))
    assert window.post_time({"created_at": stamp}, "created_at") is not None
    assert window.post_time({"indexedAt": stamp}, "indexedAt") is not None
    assert window.post_time({"created_at": stamp}, "indexedAt") is None
    for field in ("createdAt", "edited_at", "anything"):
        with pytest.raises(ValueError):
            window.post_time({field: stamp}, field)


def test_server_clock_skew() -> None:
    def http_date(d: timedelta) -> str:
        return (NOW + d).strftime("%a, %d %b %Y %H:%M:%S GMT")

    assert not window.server_clock_skewed(http_date(timedelta(minutes=9)), NOW)
    assert window.server_clock_skewed(http_date(timedelta(minutes=11)), NOW)
    assert window.server_clock_skewed(http_date(-timedelta(hours=2)), NOW)
    # Fails closed: no usable server time means the window cannot be trusted.
    loose: Any = window.server_clock_skewed
    assert loose(None, NOW)
    assert loose("garbage", NOW)
    assert loose("", NOW)
    assert loose("x" * 200, NOW)
    assert loose(b"Thu, 26 Nov 2026 12:00:00 GMT", NOW)


# --- aggregate row ------------------------------------------------------------


def test_row_has_exactly_three_fields_and_cannot_grow() -> None:
    names = {f.name for f in dataclasses.fields(rows.InteractionRow)}
    assert names == {"boosts", "favorites", "replies"}
    row = rows.InteractionRow(1, 2, 3)
    assert row.as_dict() == {"boosts": 1, "favorites": 2, "replies": 3}
    loose: Any = row
    with pytest.raises((AttributeError, TypeError)):
        loose.author = "@someone"
    factory: Any = rows.InteractionRow
    with pytest.raises(TypeError):
        factory(1, 2, 3, "text")
    with pytest.raises(TypeError):
        factory(boosts=1, favorites=2, replies=3, id="9")


@pytest.mark.parametrize("bad", [-1, 10**9 + 1, True, 1.0, "3", None])
def test_row_refuses_non_counts(bad: object) -> None:
    factory: Any = rows.InteractionRow
    with pytest.raises(ValueError):
        factory(bad, 0, 0)


def test_incomplete_read_keeps_prior_and_is_never_zero() -> None:
    prior = [rows.InteractionRow(4, 5, 6)]
    kept, reason = rows.keep_prior(rows.NotProvided("partial_pages"), prior)
    assert kept is prior and reason == "partial_pages"
    kept, reason = rows.keep_prior(rows.NotProvided("rate_limited"), None)
    assert kept is None and reason == "rate_limited"  # not [] and not zeros


def test_complete_read_replaces_and_over_cap_is_window_truncated() -> None:
    prior = [rows.InteractionRow(9, 9, 9)]
    fresh = [rows.InteractionRow(1, 1, 1)]
    kept, reason = rows.keep_prior(fresh, prior)
    assert kept == fresh and reason is None
    kept, reason = rows.keep_prior(fresh * 201, prior)
    assert kept is prior and reason == "window_truncated"
    kept, reason = rows.keep_prior([], prior)  # a read in full with zero posts
    assert kept == [] and reason is None


def test_marker_accepts_only_fixed_reason_words() -> None:
    assert rows.NotProvided("egress_timeout").reason == "egress_timeout"
    for bad in ("hello world", "<script>", "", "Timeout", "egress_" + "x" * 40):
        with pytest.raises(ValueError):
            rows.NotProvided(bad)


def test_reason_list_is_closed_including_egress_words() -> None:
    from typing import get_args

    from reach_around.net.egress import Reason

    for word in get_args(Reason):
        assert rows.NotProvided(f"egress_{word}").reason == f"egress_{word}"
    for invented in ("egress_completely_invented", "egress_", "egress_timeoutx"):
        with pytest.raises(ValueError):
            rows.NotProvided(invented)
    assert isinstance(rows.REASONS, frozenset)
    assert not rows.is_reason("egress_completely_invented")


class _SubRow(rows.InteractionRow):
    """A subclass; without slots it can carry any extra state."""


def _bad_reads() -> list[tuple[str, list[object]]]:
    good = rows.InteractionRow(1, 2, 3)
    sub = _SubRow(1, 2, 3)
    object.__setattr__(sub, "author", "@person")  # identifying state on a subclass
    duck = types.SimpleNamespace(boosts=1, favorites=2, replies=3, author="@p")
    mock = unittest.mock.Mock(boosts=1, favorites=2, replies=3)
    tampered = rows.InteractionRow(1, 2, 3)
    object.__setattr__(tampered, "boosts", "7")  # changed after construction
    out_of_range = rows.InteractionRow(1, 2, 3)
    object.__setattr__(out_of_range, "replies", 10**9 + 1)
    flt = rows.InteractionRow(1, 2, 3)
    object.__setattr__(flt, "favorites", 2.0)
    flag = rows.InteractionRow(1, 2, 3)
    object.__setattr__(flag, "boosts", True)
    return [
        ("dict", [{"boosts": 1, "favorites": 2, "replies": 3, "author": "@p"}]),
        ("subclass", [sub]),
        ("duck", [duck]),
        ("mock", [mock]),
        ("str-field", [tampered]),
        ("range", [out_of_range]),
        ("float-field", [flt]),
        ("bool-field", [flag]),
        ("tuple", [(1, 2, 3)]),
        ("one-bad-among-good", [good, good, {"boosts": 1}, good]),
    ]


@pytest.mark.parametrize("name,read", _bad_reads())
def test_keep_prior_rejects_anything_but_exact_rows(
    name: str, read: list[object]
) -> None:
    prior = [rows.InteractionRow(4, 5, 6)]
    loose: Any = read
    kept, reason = rows.keep_prior(loose, prior)
    assert kept is prior and reason == "bad_shape", name
    kept, reason = rows.keep_prior(loose, None)
    assert kept is None and reason == "bad_shape", name


def test_keep_prior_rejects_non_sequences_and_forged_markers() -> None:
    prior = [rows.InteractionRow(4, 5, 6)]
    for read in ("abc", {"boosts": 1}, None, 5, iter([rows.InteractionRow(1, 1, 1)])):
        loose: Any = read
        assert rows.keep_prior(loose, prior) == (prior, "bad_shape")
    forged = rows.NotProvided("timeout")
    object.__setattr__(forged, "reason", "free text from a remote")
    assert rows.keep_prior(forged, prior) == (prior, "bad_shape")


def test_keep_prior_accepts_a_tuple_of_good_rows() -> None:
    good = (rows.InteractionRow(1, 2, 3), rows.InteractionRow(0, 0, 0))
    kept, reason = rows.keep_prior(good, None)
    assert kept == list(good) and reason is None


# --- the live probe -----------------------------------------------------------


def _load_probe() -> Any:
    spec = importlib.util.spec_from_file_location(
        "fediverse_probe", ROOT / "scripts" / "fediverse-probe.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fediverse_probe"] = module
    spec.loader.exec_module(module)
    return module


def test_probe_import_makes_no_call_and_prints_no_body_text() -> None:
    probe = _load_probe()  # importing must not touch the network
    secret = "BODY-TEXT-SENTINEL @handle someone@mail.example 203.0.113.9"  # noqa: S105 (a fake body, not a credential)
    status = {
        "id": "12345",
        "content": secret,
        "replies_count": 1,
        "reblogs_count": 2,
        "favourites_count": 3,
        "created_at": "2026-11-25T00:00:00Z",
        "visibility": "public",
        "in_reply_to_account_id": None,
        "reblog": None,
    }

    def route(request: TransportRequest) -> RawResponse:
        if "lookup" in request.path:
            return RawResponse(
                200,
                {"content-type": "application/json", "x-secret-name": "VALUE-SENTINEL"},
                json.dumps({"id": "12345", "note": secret}).encode(),
            )
        return RawResponse(
            200,
            {"content-type": "application/json", "link": "<https://x.example/>"},
            json.dumps([status, status]).encode(),
        )

    t = FakeTransport({"https://": route})
    out = io.StringIO()
    code = probe.run(
        ["mastodon", "--host", "social.example", "--acct", "example"],
        transport=t,
        out=out,
        sleep=_no_sleep,
    )
    text = out.getvalue()
    assert code == 0
    assert "lookup: status 200" in text
    assert "statuses: array yes, 2 items" in text
    assert "header names" in text and "content-type" in text  # allowlisted names
    assert "x-secret-name" not in text  # a remote-chosen name is never printed
    assert "lookup: other headers 1" in text
    for leaked in (
        "BODY-TEXT-SENTINEL",
        "VALUE-SENTINEL",
        "@handle",
        "mail.example",
        "203.0.113.9",
        "12345",
        "x.example",
    ):
        assert leaked not in text
    assert len(t.calls) == 3 and {c.method for c in t.calls} == {"GET"}
    assert all("Authorization" not in c.headers for c in t.calls)


def test_probe_prints_only_allowlisted_header_names_and_a_count() -> None:
    probe = _load_probe()
    smuggled = "X-Account-alice-example-com"
    t = FakeTransport(
        {
            "https://": reply(
                {"id": "x"},
                headers={smuggled: "v", "Retry-After": "1", "x-other-1": "v"},
            )
        }
    )
    out = io.StringIO()
    argv = ["mastodon", "--host", "social.example", "--acct", "example"]
    probe.run(argv, transport=t, out=out, sleep=_no_sleep)
    text = out.getvalue()
    assert "alice" not in text.lower() and "x-other-1" not in text.lower()
    assert "lookup: header names content-type, retry-after" in text
    assert "lookup: other headers 2" in text


@pytest.mark.parametrize(
    "argv",
    [
        ["mastodon", "--host", "social.example", "--acct", "person.example"],
        ["bluesky", "--host", "public.api.bsky.app", "--actor", "person.example"],
    ],
)
def test_probe_unexpected_failure_prints_a_fixed_word_only(argv: list[str]) -> None:
    probe = _load_probe()

    def boom(request: TransportRequest) -> RawResponse:
        raise RuntimeError(f"failed for {request.host}{request.path} person.example")

    out = io.StringIO()
    code = probe.run(argv, transport=boom, out=out, sleep=_no_sleep)
    text = out.getvalue()
    assert code == 0 and "transport_error" in text
    for leaked in ("person.example", "RuntimeError", "failed for", "/api/", "/xrpc/"):
        assert leaked not in text

    def parse_boom(request: TransportRequest) -> RawResponse:
        return reply({"id": "1"})

    original = probe.probe_mastodon
    original_sky = probe.probe_bluesky

    def explode(*args: object, **kwargs: object) -> None:
        raise ValueError("person.example leaked")

    probe.probe_mastodon = explode
    probe.probe_bluesky = explode
    try:
        out2 = io.StringIO()
        code2 = probe.run(argv, transport=parse_boom, out=out2, sleep=_no_sleep)
    finally:
        probe.probe_mastodon = original
        probe.probe_bluesky = original_sky
    assert code2 == 3 and out2.getvalue() == "failed: unexpected_error\n"


def test_probe_bluesky_and_refusals() -> None:
    probe = _load_probe()
    post = {
        "likeCount": 1,
        "repostCount": 0,
        "replyCount": 0,
        "quoteCount": 0,
        "indexedAt": "2026-11-25T00:00:00Z",
        "record": {"text": "TEXT-SENTINEL"},
    }
    feed = {"feed": [{"post": post, "reason": {}}], "cursor": "CURSOR-SENTINEL"}
    t = FakeTransport({"https://public.api.bsky.app/": reply(feed)})
    out = io.StringIO()
    argv = ["bluesky", "--host", "public.api.bsky.app", "--actor", "a.bsky.example"]
    assert probe.run(argv, transport=t, out=out, sleep=_no_sleep) == 0
    text = out.getvalue()
    assert "feed: list yes, 1 items" in text and "cursor present" in text
    assert "TEXT-SENTINEL" not in text and "CURSOR-SENTINEL" not in text
    for host in ("127.0.0.1", "localhost", "10.0.0.1", "user@social.example"):
        t2 = FakeTransport()
        out2 = io.StringIO()
        refused = ["mastodon", "--host", host, "--acct", "x"]
        assert probe.run(refused, transport=t2, out=out2, sleep=_no_sleep) == 2
        assert t2.calls == []
    with pytest.raises(SystemExit):
        probe.run(
            ["mastodon", "--host", "social.example", "--acct", "a/../b"],
            transport=FakeTransport(),
            out=io.StringIO(),
            sleep=_no_sleep,
        )


_NAME = re.compile(r"fediverse[-_]probe")
# Ways to reach a script without naming it: a glob after an interpreter, a
# loop over scripts/, `python -m scripts...`, `runpy`, `find scripts ... -exec`.
_INDIRECT = (
    re.compile(
        r"\b(?:python3?|bash|sh|xargs|exec|source)\b[^\n]*scripts/[^\s'\"]*[*?\[]"
    ),
    re.compile(r"\bfor\s+\w+\s+in\b[^\n;]*scripts/[^\n;]*[*?]"),
    re.compile(r"-m\s+scripts\b"),
    re.compile(r"\brunpy\b"),
    re.compile(r"\bfind\s+(?:\./)?scripts\b[^\n]*-exec"),
    # find over scripts piped to xargs, or any find that names scripts/ and runs.
    re.compile(r"\bfind\b[^\n]*\bscripts\b[^\n]*(?:\bxargs\b|-exec(?:dir)?\b)"),
)


def _reaches_probe(text: str) -> bool:
    return bool(_NAME.search(text)) or any(p.search(text) for p in _INDIRECT)


def _automation_files() -> list[Path]:
    """Every file CI, a task runner or the local gate could run a script from."""
    found: set[Path] = set()
    for sub in ("workflows", "actions", "scripts"):
        base = ROOT / ".github" / sub
        if base.is_dir():
            found.update(p for p in base.rglob("*") if p.is_file())
    for name in (
        "Makefile",
        "makefile",
        "GNUmakefile",
        "justfile",
        "Justfile",
        "tox.ini",
        "noxfile.py",
        "pyproject.toml",
        "setup.cfg",
        "package.json",
        "worker/package.json",
        ".pre-commit-config.yaml",
    ):
        if (ROOT / name).is_file():
            found.add(ROOT / name)
    found.update(
        p
        for p in (ROOT / "scripts").iterdir()
        if p.is_file() and p.name != "fediverse-probe.py"
    )
    return sorted(found)


@pytest.mark.parametrize(
    "text",
    [
        "run: python3 -I scripts/fediverse-probe.py mastodon",
        "- run: ./scripts/fediverse_probe.py",
        "  python3 scripts/*.py",
        "for f in scripts/*.py; do",
        "python -m scripts.fediverse_probe",
        "runpy.run_path(p)",
        "find scripts -name '*.py' -exec python3 {} \\;",
        "bash scripts/run-*.sh",
    ],
)
def test_the_wiring_detector_catches_direct_and_indirect_runs(text: str) -> None:
    assert _reaches_probe(text)


@pytest.mark.parametrize(
    "text",
    [
        "run: python3 -I scripts/check-edge.py --offline",
        "scripts/*.py | .github/scripts/*.py)",  # a case pattern, not a run
        "run: ruff check scripts",
    ],
)
def test_the_wiring_detector_has_no_false_positives(text: str) -> None:
    assert not _reaches_probe(text)


def test_probe_is_not_wired_into_ci_or_tests() -> None:
    files = _automation_files()
    names = {p.name for p in files}
    assert "ci.yml" in names and "gate.sh" in names  # the scan sees real files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert not _reaches_probe(text), path.relative_to(ROOT).as_posix()
    source = (ROOT / "scripts" / "fediverse-probe.py").read_text(encoding="utf-8")
    assert source.rstrip().endswith("sys.exit(run(sys.argv[1:]))")
    assert 'if __name__ == "__main__":' in source


@pytest.mark.parametrize(
    "text",
    [
        "- run: find scripts -name '*.py' -print0 | xargs -0 -n1 python3",
        "find ./scripts -type f | xargs -I{} python3 {}",
        "find . -path './scripts/*' -execdir python3 {} +",
    ],
)
def test_the_wiring_detector_catches_find_piped_to_xargs(text: str) -> None:
    assert _reaches_probe(text)


@pytest.mark.parametrize(
    "marker", ["CI", "GITHUB_ACTIONS", "BUILD_ID", "TF_BUILD", "JENKINS_URL"]
)
def test_probe_refuses_the_real_network_in_automation(marker: str) -> None:
    probe = _load_probe()
    argv = ["mastodon", "--host", "social.example", "--acct", "person.example"]
    out = io.StringIO()
    with unittest.mock.patch.object(
        probe, "HttpsTransport", side_effect=AssertionError("network")
    ):
        code = probe.run(argv, out=out, sleep=_no_sleep, env={marker: "true"})
    assert code == 4 and out.getvalue() == "refused: automation\n"


def test_probe_automation_guard_allows_fakes_overrides_and_plain_shells() -> None:
    probe = _load_probe()
    argv = ["mastodon", "--host", "social.example", "--acct", "person.example"]
    # A fake transport is allowed even with a CI marker (the tests run in CI).
    out = io.StringIO()
    fake = FakeTransport({"https://social.example/": reply([])})
    code = probe.run(argv, transport=fake, out=out, sleep=_no_sleep, env={"CI": "true"})
    assert code != 4 and "refused: automation" not in out.getvalue()
    # The owner override and a shell without markers pass the guard.
    assert not probe._in_automation({"CI": "true", "RA_PROBE_OWNER_RUN": "1"})
    assert not probe._in_automation({})
    assert not probe._in_automation({"CI": ""})
    assert probe._in_automation({"CI": "1", "RA_PROBE_OWNER_RUN": "yes"})


_MARKER = "PRIVATE-MARKER-person.example"


def _assert_chain_is_broken(err: BaseException) -> None:
    import traceback

    assert err.__context__ is None and err.__cause__ is None
    rendered = "".join(traceback.format_exception(err))
    for text in (str(err), repr(err), rendered):
        assert _MARKER not in text and "RuntimeError" not in text


def test_unexpected_transport_error_leaves_no_exception_chain() -> None:
    def boom(request: TransportRequest) -> RawResponse:
        raise RuntimeError(f"Bearer SECRET {request.host}{request.path} {_MARKER}")

    with pytest.raises(FetchError) as caught:
        fetch_json_array(
            URL,
            transport=boom,
            headers={"Authorization": "Bearer SECRET"},
            sleep=_no_sleep,
            jitter=lambda: 0.0,
            now=lambda: NOW,
        )
    assert caught.value.kind == "transport_error"
    _assert_chain_is_broken(caught.value)


def test_egress_block_bad_url_bad_json_and_redirect_leave_no_chain() -> None:
    def blocked(request: TransportRequest) -> RawResponse:
        raise EgressBlocked("non_public_address", _MARKER)

    with pytest.raises(FetchError) as c1:
        fetch_json_array(
            URL, transport=blocked, sleep=_no_sleep, jitter=lambda: 0.0, now=lambda: NOW
        )
    assert c1.value.kind.startswith("egress_")
    _assert_chain_is_broken(c1.value)

    for bad_url in ("https://social.example:99999/x", "https://127.0.0.1/x"):
        with pytest.raises(FetchError) as c2:
            get(FakeTransport(), bad_url)
        assert c2.value.kind == "bad_url"
        _assert_chain_is_broken(c2.value)

    with pytest.raises(FetchError) as c3:
        get(FakeTransport({URL: reply(body=b"[" + _MARKER.encode() + b"]")}))
    assert c3.value.kind == "bad_json"
    _assert_chain_is_broken(c3.value)

    redirect = reply(
        body=b"",
        status=302,
        headers={"location": "https://127.0.0.1/" + _MARKER},
    )
    with pytest.raises(FetchError) as c4:
        get(FakeTransport({URL: redirect}))
    assert c4.value.kind == "redirect"
    _assert_chain_is_broken(c4.value)


def test_fetch_json_shared_path_leaves_no_chain() -> None:
    for bad in ("https://social.example:99999/x", "https://127.0.0.1/x"):
        with pytest.raises(FetchError) as caught:
            fetch_json(bad, transport=FakeTransport(), sleep=_no_sleep)
        _assert_chain_is_broken(caught.value)
    with pytest.raises(FetchError) as c2:
        fetch_json(
            URL,
            transport=FakeTransport({URL: reply(body=b"{" + _MARKER.encode() + b"}")}),
            sleep=_no_sleep,
            jitter=lambda: 0.0,
            now=lambda: NOW,
        )
    assert c2.value.kind == "bad_json"
    _assert_chain_is_broken(c2.value)


@pytest.mark.parametrize(
    ("digits", "sign", "ok"),
    [
        (18, "", True),
        (19, "", False),
        (18, "-", True),
        (19, "-", False),
        (1, "", True),
        (1, "-", True),
    ],
)
def test_integer_digit_limit_is_exact(digits: int, sign: str, ok: bool) -> None:
    number = sign + "1" * digits
    for body in (
        f"[{number}]",
        f'[{{"a": [{number}]}}]',
        f"[[{number}, 2]]",
    ):
        t = FakeTransport({URL: reply(body=body.encode())})
        if ok:
            assert get(t).items
        else:
            assert kind(FakeTransport({URL: reply(body=body.encode())})) == "bad_json"


def test_nineteen_digit_ids_are_refused_by_design() -> None:
    assert MAX_INT_DIGITS == 18
    body = b"[1234567890123456789]"  # a 64-bit-style id: read these as strings
    assert kind(FakeTransport({URL: reply(body=body)})) == "bad_json"
    assert get(FakeTransport({URL: reply(body=b'["1234567890123456789"]')})).items
