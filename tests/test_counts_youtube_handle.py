# SPDX-License-Identifier: Apache-2.0
"""YouTube source looked up by handle (issue #709).

The handle is read from the fixed environment variable YOUTUBE_USERNAME at fetch
time. Fake keys only; no network.
"""

from collections.abc import Mapping
from functools import partial
from typing import Any

import pytest

from fakenet import FakeTransport, reply
from reach_around.config.numbers_sources import YoutubeSource
from reach_around.counts.providers import PROVIDERS, Context, SourceError, SourceSkip
from reach_around.net.fetch import FetchError, fetch_json

KEY = "SENTINEL-YOUTUBE-KEY"
UNRELATED = "SENTINEL-UNRELATED-SECRET"
YTH = YoutubeSource(platform="yt", kind="youtube", handle_env="YOUTUBE_USERNAME")
URL = (
    "https://www.googleapis.com/youtube/v3/channels"
    "?part=statistics&forHandle=%40ExampleHandle"
)
ENV = {"YOUTUBE_API_KEY": KEY, "YOUTUBE_USERNAME": "ExampleHandle"}


def body(stats: dict[str, object]) -> dict[str, object]:
    return {"items": [{"id": "UCabcdefghijklmnopqrstuv", "statistics": stats}]}


def run(source: Any, transport: FakeTransport, env: Mapping[str, str]) -> int:
    fetch = partial(fetch_json, transport=transport, sleep=lambda s: None)
    return PROVIDERS[source.kind](source, Context(fetch=fetch, env=env))


def error_of(source: Any, transport: FakeTransport, env: Mapping[str, str]) -> str:
    with pytest.raises((SourceError, FetchError)) as caught:
        run(source, transport, env)
    return str(caught.value)


@pytest.mark.parametrize("handle", ["ExampleHandle", "@ExampleHandle"])
def test_handle_lookup_uses_for_handle_and_the_header_key(handle: str) -> None:
    transport = FakeTransport({URL: reply(body({"subscriberCount": "77"}))})
    env = {"YOUTUBE_API_KEY": KEY, "YOUTUBE_USERNAME": handle}
    assert run(YTH, transport, env) == 77
    assert transport.urls == [URL]
    assert transport.calls[0].headers["x-goog-api-key"] == KEY
    assert KEY not in transport.urls[0]


def test_handle_hidden_count_is_a_skip() -> None:
    transport = FakeTransport({URL: reply(body({"hiddenSubscriberCount": True}))})
    with pytest.raises(SourceSkip):
        run(YTH, transport, ENV)


@pytest.mark.parametrize(
    "bad",
    [
        "https://www.youtube.com/@ExampleHandle",
        "youtube.com/@ExampleHandle",
        "@@ExampleHandle",
        "Example/Handle",
        "Example\\Handle",
        "ab",
        "a" * 31,
        "@",
        " ExampleHandle",
        "ExampleHandle\n",
        "Example Handle",
        "Example&part=id",
        "Example%26x",
        "Example?x=1",
        "Example#x",
        ".Example",
        "Example.",
        "-Example",
        "Example_",
        "Exämple",
        "Example\x00",
        "Example‮",
    ],
)
def test_hostile_handle_is_refused_before_any_request(bad: str) -> None:
    transport = FakeTransport({URL: reply(body({"subscriberCount": 5}))})
    env = {"YOUTUBE_API_KEY": KEY, "YOUTUBE_USERNAME": bad}
    text = error_of(YTH, transport, env)
    assert transport.calls == []
    assert text == "invalid_handle"
    assert bad not in text and KEY not in text


def test_missing_or_empty_handle_fails_before_any_request() -> None:
    transport = FakeTransport({URL: reply(body({"subscriberCount": 5}))})
    assert error_of(YTH, transport, {"YOUTUBE_API_KEY": KEY}) == "missing_handle"
    env = {"YOUTUBE_API_KEY": KEY, "YOUTUBE_USERNAME": ""}
    assert error_of(YTH, transport, env) == "missing_handle"
    assert transport.calls == []


def test_use_time_name_check_sends_nothing() -> None:
    transport = FakeTransport({URL: reply(body({"subscriberCount": 5}))})
    for name in ("OTHER_SECRET", ["YOUTUBE_USERNAME"], None):
        forged = YoutubeSource.model_construct(
            platform="yt", kind="youtube", channel_id=None, handle_env=name
        )
        env = {**ENV, "OTHER_SECRET": UNRELATED}
        assert error_of(forged, transport, env) == "bad_source_setting"
        assert transport.calls == []


def test_forged_both_or_neither_is_refused_at_use_time() -> None:
    transport = FakeTransport({URL: reply(body({"subscriberCount": 5}))})
    both = YoutubeSource.model_construct(
        platform="yt",
        kind="youtube",
        channel_id="UCabcdefghijklmnopqrstuv",
        handle_env="YOUTUBE_USERNAME",
    )
    neither = YoutubeSource.model_construct(
        platform="yt", kind="youtube", channel_id=None, handle_env=None
    )
    for forged in (both, neither):
        assert error_of(forged, transport, ENV) == "bad_source_setting"
    assert transport.calls == []


def test_errors_never_echo_the_handle_or_key() -> None:
    transport = FakeTransport(
        {URL: reply({}, status=302, headers={"location": "https://evil.example/x"})}
    )
    text = error_of(YTH, transport, ENV)
    assert text == "redirect"
    assert "ExampleHandle" not in text and KEY not in text
    transport = FakeTransport({URL: reply({"items": []})})
    assert "ExampleHandle" not in error_of(YTH, transport, ENV)


def test_config_cannot_name_another_variable() -> None:
    for name in ("YT_HANDLE", "GITHUB_TOKEN", "youtube_username"):
        with pytest.raises(ValueError):
            YoutubeSource.model_validate(
                {"platform": "yt", "kind": "youtube", "handle_env": name}
            )
    ok = YoutubeSource.model_validate(
        {"platform": "yt", "kind": "youtube", "handle_env": "YOUTUBE_USERNAME"}
    )
    assert ok.channel_id is None


def test_config_needs_exactly_one_of_channel_id_or_handle_env() -> None:
    base = {"platform": "yt", "kind": "youtube"}
    with pytest.raises(ValueError):
        YoutubeSource.model_validate(base)
    both = {
        **base,
        "channel_id": "UCabcdefghijklmnopqrstuv",
        "handle_env": "YOUTUBE_USERNAME",
    }
    with pytest.raises(ValueError):
        YoutubeSource.model_validate(both)
    with pytest.raises(ValueError):
        YoutubeSource.model_validate({**base, "channel_id": None})


def test_channel_id_form_is_unchanged() -> None:
    src = YoutubeSource.model_validate(
        {"platform": "yt", "kind": "youtube", "channel_id": "UCabcdefghijklmnopqrstuv"}
    )
    assert src.handle_env is None
    url = (
        "https://www.googleapis.com/youtube/v3/channels"
        "?part=statistics&id=UCabcdefghijklmnopqrstuv"
    )
    transport = FakeTransport({url: reply(body({"subscriberCount": 5}))})
    assert run(src, transport, {"YOUTUBE_API_KEY": KEY}) == 5
