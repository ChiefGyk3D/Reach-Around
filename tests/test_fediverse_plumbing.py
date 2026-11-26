# SPDX-License-Identifier: Apache-2.0
"""Slice 1 of the fediverse collector (#921): array fetch, window, row, probe.

Hermetic: a fake transport serves everything; nothing opens a socket."""

from __future__ import annotations

import dataclasses
import importlib.util
import io
import json
import sys
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
    assert not window.server_clock_skewed(None, NOW)
    assert not window.server_clock_skewed("garbage", NOW)


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
    assert "header names" in text and "x-secret-name" in text  # names only
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


def test_probe_is_not_wired_into_ci_or_tests() -> None:
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert "fediverse-probe" not in wf.read_text(encoding="utf-8")
    source = (ROOT / "scripts" / "fediverse-probe.py").read_text(encoding="utf-8")
    assert source.rstrip().endswith("sys.exit(run(sys.argv[1:]))")
    assert 'if __name__ == "__main__":' in source
