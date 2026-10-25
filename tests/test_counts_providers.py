# SPDX-License-Identifier: Apache-2.0
from collections.abc import Mapping
from functools import partial
from typing import Any

import pytest

from fakenet import FakeTransport, reply
from reach_around.config.numbers_sources import (
    BlueskySource,
    DiscordSource,
    GithubSource,
    MastodonSource,
    MatrixSource,
    TwitchSource,
    YoutubeSource,
)
from reach_around.counts.providers import (
    PROVIDERS,
    Context,
    SourceError,
    SourceSkip,
)
from reach_around.net.fetch import FetchError, fetch_json


def ctx(transport: FakeTransport, env: Mapping[str, str] | None = None) -> Context:
    fetch = partial(fetch_json, transport=transport, sleep=lambda s: None)
    return Context(fetch=fetch, env=env or {})


def run(
    source: Any, transport: FakeTransport, env: Mapping[str, str] | None = None
) -> int:
    return PROVIDERS[source.kind](source, ctx(transport, env))


def error_of(
    source: Any, transport: FakeTransport, env: Mapping[str, str] | None = None
) -> str:
    with pytest.raises((SourceError, FetchError)) as caught:
        run(source, transport, env)
    return str(caught.value)


# ---- Bluesky -----------------------------------------------------------------------

BSKY = BlueskySource(platform="bsky", kind="bluesky", actor="example.bsky.social")
BSKY_URL = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=example.bsky.social"


def test_bluesky_parses_followers_count() -> None:
    body = {
        "did": "did:plc:x",
        "handle": "example.bsky.social",
        "followersCount": 1234,
        "followsCount": 5,
        "postsCount": 9,
        "associated": {"lists": 1},
    }
    transport = FakeTransport({BSKY_URL: reply(body)})
    assert run(BSKY, transport) == 1234
    assert transport.urls == [BSKY_URL]


def test_bluesky_did_actor_is_percent_encoded() -> None:
    source = BlueskySource(
        platform="b", kind="bluesky", actor="did:plc:abcdefghijklmnopqrstuvwx"
    )
    transport = FakeTransport(
        {"https://public.api.bsky.app/": reply({"followersCount": 3})}
    )
    assert run(source, transport) == 3
    assert transport.urls[0].endswith("actor=did%3Aplc%3Aabcdefghijklmnopqrstuvwx")


# ---- hostile values, exercised on one provider and every provider's field ------------

HOSTILE = [
    ({"followersCount": -1}, "bad_count"),
    ({"followersCount": 1.5}, "bad_count"),
    ({"followersCount": 12.0}, "bad_count"),
    ({"followersCount": "12"}, "bad_count"),
    ({"followersCount": True}, "bad_count"),
    ({"followersCount": None}, "bad_count"),
    ({"followersCount": [5]}, "bad_count"),
    ({"followersCount": {"n": 5}}, "bad_count"),
    ({"followersCount": 10**10 + 1}, "bad_count"),
    ({"followersCount": 10**40}, "bad_count"),
    ({"other": 5}, "missing_field"),
    ({}, "missing_field"),
]


@pytest.mark.parametrize(("body", "kind"), HOSTILE)
def test_hostile_count_values_are_refused(body: dict[str, object], kind: str) -> None:
    assert error_of(BSKY, FakeTransport({BSKY_URL: reply(body)})) == kind


def test_count_boundaries_are_accepted() -> None:
    for value in (0, 10**10):
        transport = FakeTransport({BSKY_URL: reply({"followersCount": value})})
        assert run(BSKY, transport) == value


def test_hostile_transport_level_answers_are_failures_not_numbers() -> None:
    big = b'{"followersCount": 5, "pad": "' + b"a" * 300_000 + b'"}'
    assert error_of(BSKY, FakeTransport({BSKY_URL: reply(body=big)})) == "too_large"
    html = FakeTransport(
        {BSKY_URL: reply(body=b"<html>5</html>", headers={"content-type": "text/html"})}
    )
    assert error_of(BSKY, html) == "content_type"
    assert (
        error_of(
            BSKY, FakeTransport({BSKY_URL: reply({"followersCount": 5}, status=500)})
        )
        == "server_error"
    )


# ---- Mastodon ----------------------------------------------------------------------

MASTO = MastodonSource(
    platform="m", kind="mastodon", host="example.social", acct="someone"
)
MASTO_URL = "https://example.social/api/v1/accounts/lookup?acct=someone"


def test_mastodon_parses_followers_count() -> None:
    body = {
        "id": "1",
        "username": "someone",
        "acct": "someone",
        "followers_count": 77,
        "following_count": 3,
    }
    transport = FakeTransport({MASTO_URL: reply(body)})
    assert run(MASTO, transport) == 77
    assert transport.calls[0].host == "example.social"


def test_mastodon_missing_field_and_limited_mode_401() -> None:
    assert (
        error_of(MASTO, FakeTransport({MASTO_URL: reply({"id": "1"})}))
        == "missing_field"
    )
    assert (
        error_of(MASTO, FakeTransport({MASTO_URL: reply({"error": "x"}, status=401)}))
        == "http_401"
    )


def test_mastodon_redirect_to_a_private_host_is_an_error() -> None:
    transport = FakeTransport(
        {
            MASTO_URL: reply(
                status=302, headers={"location": "https://10.0.0.5/x"}, body=b""
            )
        }
    )
    assert error_of(MASTO, transport) == "redirect"
    assert [c.host for c in transport.calls] == ["example.social"]


# ---- GitHub ------------------------------------------------------------------------

GH = GithubSource(platform="gh", kind="github", user="some-user")


def test_github_parses_followers_and_sends_api_headers() -> None:
    body = {
        "login": "some-user",
        "id": 1,
        "public_repos": 8,
        "followers": 24739,
        "following": 9,
    }
    transport = FakeTransport({"https://api.github.com/users/some-user": reply(body)})
    assert run(GH, transport) == 24739
    assert transport.calls[0].headers["Accept"] == "application/vnd.github+json"


def test_github_rate_limit_is_a_failure() -> None:
    transport = FakeTransport(
        {
            "https://api.github.com/": reply(
                {}, status=403, headers={"x-ratelimit-remaining": "0"}
            )
        }
    )
    assert error_of(GH, transport) == "http_403"


# ---- Discord -----------------------------------------------------------------------

DISCORD = DiscordSource(platform="d", kind="discord", invite="abc-DEF123")


def test_discord_parses_approximate_member_count() -> None:
    body = {
        "type": 0,
        "code": "abc-DEF123",
        "approximate_member_count": 309232,
        "approximate_presence_count": 80774,
        "guild": {"id": "1"},
    }
    url = "https://discord.com/api/v10/invites/abc-DEF123?with_counts=true"
    transport = FakeTransport({url: reply(body)})
    assert run(DISCORD, transport) == 309232
    assert transport.urls == [url]


def test_discord_without_counts_is_missing_field() -> None:
    transport = FakeTransport(
        {"https://discord.com/": reply({"code": "x", "approximate_presence_count": 5})}
    )
    assert error_of(DISCORD, transport) == "missing_field"


# ---- Matrix ------------------------------------------------------------------------

MATRIX = MatrixSource(platform="mx", kind="matrix", room="#room:example.org")
WELL_KNOWN = "https://example.org/.well-known/matrix/client"
ALIAS = (
    "https://chat.example.org/_matrix/client/v3/directory/room/%23room%3Aexample.org"
)
SUMMARY = "https://chat.example.org/_matrix/client/v1/room_summary/%21abc%3Aexample.org?via=example.org"


def matrix_routes(**over: Any) -> dict[str, Any]:
    routes: dict[str, Any] = {
        WELL_KNOWN: reply({"m.homeserver": {"base_url": "https://chat.example.org"}}),
        ALIAS: reply({"room_id": "!abc:example.org", "servers": ["example.org"]}),
        SUMMARY: reply(
            {"room_id": "!abc:example.org", "num_joined_members": 7579, "name": "Room"}
        ),
    }
    routes.update(over)
    return routes


def test_matrix_resolves_alias_then_reads_member_count() -> None:
    transport = FakeTransport(matrix_routes())
    assert run(MATRIX, transport) == 7579
    assert transport.urls == [WELL_KNOWN, ALIAS, SUMMARY]


def test_matrix_without_well_known_uses_the_alias_server_directly() -> None:
    routes = matrix_routes(**{WELL_KNOWN: reply({}, status=404)})
    routes = {
        WELL_KNOWN: routes[WELL_KNOWN],
        ALIAS.replace("chat.example.org", "example.org"): routes[ALIAS],
        SUMMARY.replace("chat.example.org", "example.org"): routes[SUMMARY],
    }
    transport = FakeTransport(routes)
    assert run(MATRIX, transport) == 7579
    assert {c.host for c in transport.calls} == {"example.org"}


@pytest.mark.parametrize(
    "base",
    [
        "http://chat.example.org",
        "https://127.0.0.1",
        "https://localhost",
        "https://chat.example.org:8448",
        "https://u:p@chat.example.org",
        "https://chat.example.org/path",
        "https://chat.example.org?x=1",
        "https://169.254.169.254",
        5,
        None,
    ],
)
def test_matrix_well_known_pointing_somewhere_unsafe_is_refused(base: object) -> None:
    transport = FakeTransport(
        matrix_routes(**{WELL_KNOWN: reply({"m.homeserver": {"base_url": base}})})
    )
    assert error_of(MATRIX, transport) == "bad_well_known"
    assert len(transport.calls) == 1


def test_matrix_well_known_server_error_is_a_failure_not_a_fallback() -> None:
    transport = FakeTransport(matrix_routes(**{WELL_KNOWN: reply({}, status=403)}))
    assert error_of(MATRIX, transport) == "http_403"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_matrix_room_not_public(status: int) -> None:
    transport = FakeTransport(
        matrix_routes(**{SUMMARY: reply({"errcode": "M_FORBIDDEN"}, status=status)})
    )
    assert error_of(MATRIX, transport) == "room_not_public"


def test_matrix_bad_room_id_and_counts() -> None:
    for room_id in (None, 5, "no-bang", "!" + "a" * 300):
        transport = FakeTransport(matrix_routes(**{ALIAS: reply({"room_id": room_id})}))
        assert error_of(MATRIX, transport) == "missing_field"
    for count in (-1, "7", 1.5, None):
        transport = FakeTransport(
            matrix_routes(**{SUMMARY: reply({"num_joined_members": count})})
        )
        assert error_of(MATRIX, transport) in ("bad_count", "missing_field")
    transport = FakeTransport(matrix_routes(**{SUMMARY: reply({"room_id": "x"})}))
    assert error_of(MATRIX, transport) == "missing_field"


# ---- YouTube -----------------------------------------------------------------------

YT = YoutubeSource(
    platform="yt",
    kind="youtube",
    channel_id="UCabcdefghijklmnopqrstuv",
    api_key_env="YT_KEY",
)
YT_URL = "https://www.googleapis.com/youtube/v3/channels?part=statistics&id=UCabcdefghijklmnopqrstuv"
KEY = "SENTINEL-YOUTUBE-KEY"


def yt_body(stats: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "youtube#channelListResponse",
        "items": [{"id": "UCabcdefghijklmnopqrstuv", "statistics": stats}],
    }


def test_youtube_parses_the_digit_string_and_sends_the_key_in_a_header_only() -> None:
    stats = {
        "viewCount": "999",
        "subscriberCount": "12300",
        "hiddenSubscriberCount": False,
        "videoCount": "4",
    }
    transport = FakeTransport({YT_URL: reply(yt_body(stats))})
    assert run(YT, transport, {"YT_KEY": KEY}) == 12300
    assert transport.calls[0].headers["x-goog-api-key"] == KEY
    assert KEY not in transport.urls[0]


def test_youtube_accepts_an_integer_too() -> None:
    transport = FakeTransport({YT_URL: reply(yt_body({"subscriberCount": 5}))})
    assert run(YT, transport, {"YT_KEY": KEY}) == 5


def test_youtube_hidden_subscriber_count_is_a_skip_not_a_zero() -> None:
    stats = {"hiddenSubscriberCount": True, "subscriberCount": "0"}
    transport = FakeTransport({YT_URL: reply(yt_body(stats))})
    with pytest.raises(SourceSkip) as caught:
        run(YT, transport, {"YT_KEY": KEY})
    assert caught.value.reason == "hidden_subscriber_count"


@pytest.mark.parametrize(
    "value",
    [
        "9" * 5000,
        "-5",
        "1.5",
        "12abc",
        "",
        " 5",
        "٣",
        "1" * 12,
        1.5,
        -1,
        None,
        True,
        [1],
        "0x10",
    ],
)
def test_youtube_hostile_subscriber_counts_refused(value: object) -> None:
    transport = FakeTransport({YT_URL: reply(yt_body({"subscriberCount": value}))})
    assert error_of(YT, transport, {"YT_KEY": KEY}) == "bad_count"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": []},
        {"items": "x"},
        {"items": [5]},
        {"items": [{}]},
        {"items": [{"statistics": []}]},
        yt_body({}),
    ],
)
def test_youtube_missing_pieces_refused(body: dict[str, object]) -> None:
    assert (
        error_of(YT, FakeTransport({YT_URL: reply(body)}), {"YT_KEY": KEY})
        == "missing_field"
    )


@pytest.mark.parametrize("env", [{}, {"YT_KEY": ""}])
def test_youtube_missing_key_fails_before_any_request(env: dict[str, str]) -> None:
    transport = FakeTransport()
    assert error_of(YT, transport, env) == "missing_credential"
    assert transport.calls == []


@pytest.mark.parametrize("bad", ["a b", "line\nbreak", "tab\tx", "x" * 600, "ctl\x00"])
def test_youtube_key_with_control_characters_refused(bad: str) -> None:
    transport = FakeTransport()
    assert error_of(YT, transport, {"YT_KEY": bad}) == "invalid_credential"
    assert transport.calls == []


def test_youtube_error_text_never_holds_the_key() -> None:
    transport = FakeTransport(
        {YT_URL: reply({"error": {"message": f"bad key {KEY}"}}, status=400)}
    )
    text = error_of(YT, transport, {"YT_KEY": KEY})
    assert text == "http_400" and KEY not in text


# ---- Twitch ------------------------------------------------------------------------

TW = TwitchSource.model_validate(
    {
        "platform": "tw",
        "kind": "twitch",
        "login": "some_user",
        "client_id_env": "TW_ID",
        "client_secret_env": "TW_SECRET",
    }
)
TW_ENV = {"TW_ID": "SENTINEL-CLIENT-ID", "TW_SECRET": "SENTINEL-CLIENT-SECRET"}
AUTH_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users?login=some_user"
FOLLOWERS_URL = (
    "https://api.twitch.tv/helix/channels/followers?broadcaster_id=4242&first=1"
)


def twitch_routes(**over: Any) -> dict[str, Any]:
    routes: dict[str, Any] = {
        AUTH_URL: reply(
            {
                "access_token": "SENTINEL-APP-TOKEN",
                "expires_in": 5000,
                "token_type": "bearer",
            }
        ),
        USERS_URL: reply({"data": [{"id": "4242", "login": "some_user"}]}),
        FOLLOWERS_URL: reply({"total": 321, "data": [], "pagination": {}}),
    }
    routes.update(over)
    return routes


def test_twitch_gets_a_token_by_post_then_reads_total() -> None:
    transport = FakeTransport(twitch_routes())
    assert run(TW, transport, TW_ENV) == 321
    token_call = transport.calls[0]
    assert token_call.method == "POST" and token_call.host == "id.twitch.tv"
    assert (
        token_call.body is not None
        and b"grant_type=client_credentials" in token_call.body
    )
    # the secret travels in the POST body, never in a URL
    assert all("SENTINEL" not in url for url in transport.urls)
    helix = transport.calls[1]
    assert helix.headers["Authorization"] == "Bearer SENTINEL-APP-TOKEN"
    assert helix.headers["Client-Id"] == "SENTINEL-CLIENT-ID"


@pytest.mark.parametrize("status", [401, 403])
def test_twitch_followers_needing_a_user_token_is_a_skip(status: int) -> None:
    transport = FakeTransport(
        twitch_routes(
            **{
                FOLLOWERS_URL: reply(
                    {"message": "Missing User OAuth Token"}, status=status
                )
            }
        )
    )
    with pytest.raises(SourceSkip) as caught:
        run(TW, transport, TW_ENV)
    assert caught.value.reason == "needs_user_token"


def test_twitch_token_rejection_is_a_failure_not_a_skip() -> None:
    transport = FakeTransport(
        twitch_routes(
            **{AUTH_URL: reply({"message": "invalid client secret"}, status=403)}
        )
    )
    assert error_of(TW, transport, TW_ENV) == "http_403"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "env", [{}, {"TW_ID": "x"}, {"TW_SECRET": "x"}, {"TW_ID": "", "TW_SECRET": "x"}]
)
def test_twitch_missing_credentials_fail_before_any_request(
    env: dict[str, str],
) -> None:
    transport = FakeTransport(twitch_routes())
    assert error_of(TW, transport, env) == "missing_credential"
    assert transport.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {AUTH_URL: reply({"token_type": "bearer"})},
        {AUTH_URL: reply({"access_token": 5})},
        {USERS_URL: reply({"data": []})},
        {USERS_URL: reply({"data": [{"id": 4242}]})},
        {USERS_URL: reply({"data": [{"id": "4242/../x"}]})},
        {FOLLOWERS_URL: reply({"data": []})},
        {FOLLOWERS_URL: reply({"total": -1})},
        {FOLLOWERS_URL: reply({"total": "5"})},
    ],
)
def test_twitch_hostile_answers_are_refused(overrides: dict[str, Any]) -> None:
    transport = FakeTransport(twitch_routes(**overrides))
    assert error_of(TW, transport, TW_ENV) in ("missing_field", "bad_count")


def test_twitch_secrets_never_in_errors() -> None:
    transport = FakeTransport(
        twitch_routes(
            **{
                USERS_URL: reply(
                    {"message": "SENTINEL-APP-TOKEN SENTINEL-CLIENT-SECRET"}, status=500
                )
            }
        )
    )
    text = error_of(TW, transport, TW_ENV)
    assert "SENTINEL" not in text
