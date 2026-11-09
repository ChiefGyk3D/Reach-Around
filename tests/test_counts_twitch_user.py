# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S105
"""Twitch followers via a broadcaster user token. Mocked transport only.

Fixtures follow the documented response shapes (docs read 2026-11-09). No
network, no real token: every value below is a sentinel.
"""

from __future__ import annotations

import io
from functools import partial
from typing import Any

import pytest

from fakenet import FakeTransport, reply
from reach_around.config.numbers_sources import TwitchSource
from reach_around.counts import twitch_user
from reach_around.counts.providers import PROVIDERS, Context, SourceError
from reach_around.counts.twitch_user import (
    NoopSink,
    TokenNames,
    TokenPair,
    TwitchUserError,
)
from reach_around.net.egress import EgressBlocked
from reach_around.net.fetch import FetchError, fetch_json

SRC = TwitchSource.model_validate(
    {
        "platform": "tw",
        "kind": "twitch",
        "login": "some_user",
        "client_id_env": "TWITCH_TW_ID",
        "client_secret_env": "TWITCH_TW_SECRET",
        "user_token_env": "TWITCH_TW_USER",
        "refresh_token_env": "TWITCH_TW_REFRESH",
    }
)
ENV = {
    "TWITCH_TW_ID": "SENTINEL-CID",
    "TWITCH_TW_SECRET": "SENTINEL-CSECRET",
    "TWITCH_TW_USER": "SENTINEL-ACCESS-OLD",
    "TWITCH_TW_REFRESH": "SENTINEL-REFRESH-OLD",
}
SECRETS = [
    "SENTINEL-CID",
    "SENTINEL-CSECRET",
    "SENTINEL-ACCESS-OLD",
    "SENTINEL-REFRESH-OLD",
    "SENTINEL-ACCESS-NEW",
    "SENTINEL-REFRESH-NEW",
]
OAUTH_URL = "https://id.twitch.tv/oauth2/token"
USERS = "https://api.twitch.tv/helix/users?login=some_user"
FOLLOWERS = "https://api.twitch.tv/helix/channels/followers?broadcaster_id=4242&first=1"
USER_REPLY = reply({"data": [{"id": "4242", "login": "some_user"}]})
FOLLOW_REPLY = reply({"total": 321, "data": [], "pagination": {}})
REFRESH_REPLY = reply(
    {
        "access_token": "SENTINEL-ACCESS-NEW",
        "refresh_token": "SENTINEL-REFRESH-NEW",
        "expires_in": 14000,
        "scope": ["moderator:read:followers"],
        "token_type": "bearer",
    }
)


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.stored: list[tuple[TokenPair, TokenNames, str]] = []
        self.alerts: list[Any] = []
        self.fail = fail

    def store(self, pair: TokenPair, names: TokenNames, source: str) -> None:
        if self.fail:
            raise RuntimeError("SENTINEL-ACCESS-NEW")
        self.stored.append((pair, names, source))

    def alert(self, alert: Any) -> None:
        self.alerts.append(alert)


def routes(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {USERS: USER_REPLY, FOLLOWERS: FOLLOW_REPLY}
    base.update(over)
    return base


def run(
    transport: FakeTransport, sink: Any = None, env: dict[str, str] | None = None
) -> int:
    fetch = partial(fetch_json, transport=transport, sleep=lambda s: None)
    ctx = Context(fetch=fetch, env=ENV if env is None else env, token_sink=sink)
    return PROVIDERS["twitch"](SRC, ctx)


def kind(transport: FakeTransport, sink: Any = None, env: Any = None) -> str:
    with pytest.raises((SourceError, FetchError)) as caught:
        run(transport, sink, env)
    return str(caught.value)


def no_leak(*texts: str) -> None:
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


def test_success_reads_total_with_user_token_and_never_uses_app_token() -> None:
    t = FakeTransport(routes())
    assert run(t) == 321
    assert OAUTH_URL not in t.urls
    assert t.calls[1].headers["Authorization"] == "Bearer SENTINEL-ACCESS-OLD"
    assert t.calls[1].headers["Client-Id"] == "SENTINEL-CID"
    assert all("SENTINEL" not in u for u in t.urls)


def test_missing_user_token_keeps_the_app_path_and_skip() -> None:
    env = {k: v for k, v in ENV.items() if k != "TWITCH_TW_USER"}
    app_routes = {
        OAUTH_URL: reply({"access_token": "SENTINEL-APP"}),
        USERS: USER_REPLY,
        FOLLOWERS: reply({"message": "no"}, status=401),
    }
    from reach_around.counts.providers import SourceSkip

    with pytest.raises(SourceSkip) as caught:
        run(FakeTransport(app_routes), env=env)
    assert caught.value.reason == "needs_user_token"


def test_401_refreshes_once_hands_pair_to_sink_and_retries() -> None:
    seen: list[str] = []

    def followers(req: Any) -> Any:
        seen.append(req.headers["Authorization"])
        if len(seen) == 1:
            return reply({"message": "expired"}, status=401)
        return FOLLOW_REPLY

    t = FakeTransport(routes(**{FOLLOWERS: followers, OAUTH_URL: REFRESH_REPLY}))
    sink = Sink()
    assert run(t, sink) == 321
    assert seen == ["Bearer SENTINEL-ACCESS-OLD", "Bearer SENTINEL-ACCESS-NEW"]
    refresh_call = next(c for c in t.calls if c.host == "id.twitch.tv")
    assert refresh_call.method == "POST" and refresh_call.body is not None
    assert b"grant_type=refresh_token" in refresh_call.body
    assert b"refresh_token=SENTINEL-REFRESH-OLD" in refresh_call.body
    ((pair, names, source),) = sink.stored
    assert (pair.access_token, pair.refresh_token) == (
        "SENTINEL-ACCESS-NEW",
        "SENTINEL-REFRESH-NEW",
    )
    assert names == TokenNames("TWITCH_TW_USER", "TWITCH_TW_REFRESH") and source == "tw"
    no_leak(repr(pair))


def test_only_one_refresh_even_if_still_401() -> None:
    t = FakeTransport(
        routes(
            **{FOLLOWERS: reply({}, status=401), USERS: reply({}, status=401)},
            **{OAUTH_URL: REFRESH_REPLY},
        )
    )
    assert kind(t, Sink()) == "token_rejected"
    assert sum(c.host == "id.twitch.tv" for c in t.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        reply({"status": 400, "message": "Invalid refresh token"}, status=400),
        reply({"message": "x"}, status=401),
        reply({"access_token": "SENTINEL-ACCESS-NEW"}),
        reply({"access_token": 5, "refresh_token": "SENTINEL-REFRESH-NEW"}),
    ],
)
def test_refresh_failure_alerts_without_values(bad: Any) -> None:
    t = FakeTransport(routes(**{FOLLOWERS: reply({}, status=401), OAUTH_URL: bad}))
    sink = Sink()
    text = kind(t, sink)
    assert text == "refresh_failed"
    assert [a.kind for a in sink.alerts] == ["refresh_failed"]
    no_leak(text, *(a.line() for a in sink.alerts))
    assert sink.stored == []


def test_no_refresh_token_configured_is_safe_read_only_with_alert() -> None:
    env = {k: v for k, v in ENV.items() if k != "TWITCH_TW_REFRESH"}
    src = SRC.model_copy(update={"refresh_token_env": None})
    t = FakeTransport(routes(**{FOLLOWERS: reply({}, status=401)}))
    sink = Sink()
    fetch = partial(fetch_json, transport=t, sleep=lambda s: None)
    with pytest.raises(SourceError) as caught:
        PROVIDERS["twitch"](src, Context(fetch=fetch, env=env, token_sink=sink))
    assert caught.value.kind == "token_expired"
    assert [a.kind for a in sink.alerts] == ["refresh_needed"]
    assert all(c.host != "id.twitch.tv" for c in t.calls)


def test_sink_failure_does_not_lose_the_number() -> None:
    n = {"i": 0}

    def followers(req: Any) -> Any:
        n["i"] += 1
        return reply({}, status=401) if n["i"] == 1 else FOLLOW_REPLY

    t = FakeTransport(routes(**{FOLLOWERS: followers, OAUTH_URL: REFRESH_REPLY}))
    sink = Sink(fail=True)
    assert run(t, sink) == 321
    assert [a.kind for a in sink.alerts] == ["rotated_unstored"]


def test_noop_sink_prints_names_and_instruction_only() -> None:
    out = io.StringIO()
    n = {"i": 0}

    def followers(req: Any) -> Any:
        n["i"] += 1
        return reply({}, status=401) if n["i"] == 1 else FOLLOW_REPLY

    t = FakeTransport(routes(**{FOLLOWERS: followers, OAUTH_URL: REFRESH_REPLY}))
    assert run(t, NoopSink(out)) == 321
    text = out.getvalue()
    assert "TWITCH_TW_USER" in text and "TWITCH_TW_REFRESH" in text
    assert "secret manager" in text and "token-health:" in text
    no_leak(text)


def test_default_sink_is_noop_writing_to_stderr(capsys: Any) -> None:
    t = FakeTransport(routes(**{FOLLOWERS: reply({}, status=401)}))
    kind(t, None, {k: v for k, v in ENV.items() if k != "TWITCH_TW_REFRESH"})
    # refresh_token_env is configured but empty: nothing to refresh with
    err = capsys.readouterr().err
    assert "kind=refresh_needed" in err
    no_leak(err)


def test_429_honors_retry_after_bounded() -> None:
    sleeps: list[float] = []
    n = {"i": 0}

    def followers(req: Any) -> Any:
        n["i"] += 1
        if n["i"] == 1:
            return reply({}, status=429, headers={"retry-after": "7"})
        return FOLLOW_REPLY

    t = FakeTransport(routes(**{FOLLOWERS: followers}))
    fetch = partial(fetch_json, transport=t, sleep=sleeps.append, jitter=lambda: 0.0)
    ctx = Context(fetch=fetch, env=ENV)
    assert PROVIDERS["twitch"](SRC, ctx) == 321
    assert sleeps == [7.0]


def test_429_with_huge_retry_after_fails_without_sleeping() -> None:
    sleeps: list[float] = []
    t = FakeTransport(
        routes(**{FOLLOWERS: reply({}, status=429, headers={"retry-after": "9999"})})
    )
    fetch = partial(fetch_json, transport=t, sleep=sleeps.append)
    with pytest.raises(FetchError) as caught:
        PROVIDERS["twitch"](SRC, Context(fetch=fetch, env=ENV))
    assert caught.value.kind == "rate_limited" and sleeps == []


def test_5xx_is_bounded_and_clean() -> None:
    t = FakeTransport(
        routes(**{FOLLOWERS: reply({"m": "SENTINEL-ACCESS-OLD"}, status=503)})
    )
    text = kind(t)
    assert text == "server_error"
    assert sum(c.path.startswith("/helix/channels") for c in t.calls) == 3
    no_leak(text)


def test_timeout_is_a_fixed_word() -> None:
    def boom(req: Any) -> Any:
        raise EgressBlocked("timeout", req.host)

    t = FakeTransport(routes(**{FOLLOWERS: boom}))
    text = kind(t)
    assert text == "egress_timeout"
    no_leak(text)


@pytest.mark.parametrize("status", [301, 302, 307])
def test_redirect_is_refused_even_on_same_host(status: int) -> None:
    t = FakeTransport(
        routes(
            **{FOLLOWERS: reply({}, status=status, headers={"location": "/helix/x"})}
        )
    )
    assert kind(t) == "redirect"
    assert len(t.calls) == 2  # users, followers; the location was never requested


def test_refresh_redirect_is_refused() -> None:
    t = FakeTransport(
        routes(
            **{
                FOLLOWERS: reply({}, status=401),
                OAUTH_URL: reply({}, status=307, headers={"location": "/oauth2/token"}),
            }
        )
    )
    assert kind(t, Sink()) == "refresh_failed"
    assert sum(c.host == "id.twitch.tv" for c in t.calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/helix/users",
        "https://api.twitch.tv.evil.example/x",
        "http://api.twitch.tv/helix/users",
        "https://api.twitch.tv:8443/helix/users",
    ],
)
def test_wrong_host_is_refused(url: str) -> None:
    with pytest.raises(TwitchUserError) as caught:
        twitch_user._check_url(url)
    assert caught.value.kind == "bad_host"


@pytest.mark.parametrize(
    "body",
    [b"{not json", b"[1]", b'"x"', b""],
)
def test_malformed_json(body: bytes) -> None:
    t = FakeTransport(routes(**{FOLLOWERS: reply(body=body)}))
    assert kind(t) == "bad_json"


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"data": []}, "missing_field"),
        ({"total": -1}, "bad_count"),
        ({"total": 1.5}, "bad_count"),
        ({"total": 12.0}, "bad_count"),
        ({"total": "5"}, "bad_count"),
        ({"total": True}, "bad_count"),
        ({"total": 10**12}, "bad_count"),
    ],
)
def test_total_must_be_a_whole_non_negative_number(payload: Any, expected: str) -> None:
    t = FakeTransport(routes(**{FOLLOWERS: reply(payload)}))
    assert kind(t) == expected


@pytest.mark.parametrize(
    "users", [{"data": []}, {"data": [{"id": 4242}]}, {"data": [{"id": "4/../2"}]}]
)
def test_bad_user_lookup(users: Any) -> None:
    t = FakeTransport(routes(**{USERS: reply(users)}))
    assert kind(t) == "missing_field"


def test_huge_body_is_capped() -> None:
    t = FakeTransport(
        routes(**{FOLLOWERS: reply(body=b'{"total":1,"x":"' + b"a" * 300_000 + b'"}')})
    )
    assert kind(t) == "too_large"


def test_user_token_with_whitespace_is_refused_before_any_request() -> None:
    t = FakeTransport(routes())
    assert kind(t, env={**ENV, "TWITCH_TW_USER": "bad token"}) == "invalid_credential"
    assert t.calls == []


def test_config_rules() -> None:
    base = {
        "platform": "tw",
        "kind": "twitch",
        "login": "some_user",
        "client_id_env": "TWITCH_TW_ID",
        "client_secret_env": "TWITCH_TW_SECRET",
    }
    with pytest.raises(ValueError):
        TwitchSource.model_validate(
            {**base, "user_token_env": None, "refresh_token_env": "TWITCH_R"}
        )
    with pytest.raises(ValueError):
        TwitchSource.model_validate({**base, "user_token_env": "TWITCH_TW_ID"})
    with pytest.raises(ValueError):
        TwitchSource.model_validate(
            {**base, "user_token_env": "TWITCH_U", "refresh_token_env": "TWITCH_U"}
        )
    defaults = TwitchSource.model_validate(
        {k: v for k, v in base.items() if not k.endswith("_env")}
    )
    assert defaults.client_id_env == "TWITCH_CLIENT_ID"
    assert defaults.client_secret_env == "TWITCH_CLIENT_SECRET"
    assert defaults.user_token_env == "TWITCH_USER_ACCESS_TOKEN"
    assert defaults.refresh_token_env == "TWITCH_REFRESH_TOKEN"


BAD_NAMES = [
    "DATABASE_URL",
    "AWS_SECRET_ACCESS_KEY",
    "twitch_user_token",
    "TWITCH",
    "TWITCHX_TOKEN",
    "TWITCH_",
    "TWITCH_\x00X",
    "TWITCH_A\n",
    "TWITCH_A B",
    "TWITCH_" + "A" * 61,
    "",
]
FIELDS = ["client_id_env", "client_secret_env", "user_token_env", "refresh_token_env"]
UNRELATED = "SENTINEL-UNRELATED-VALUE"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_credential_names_rejected_at_validation(field: str, name: str) -> None:
    base = {"platform": "tw", "kind": "twitch", "login": "some_user"}
    with pytest.raises(ValueError):
        TwitchSource.model_validate({**base, field: name})


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("name", [n for n in BAD_NAMES if n])
def test_bad_credential_names_rejected_at_runtime_and_never_sent(
    field: str, name: str
) -> None:
    # model_construct skips validation: the use-time check must still hold
    src = SRC.model_construct(**{**SRC.model_dump(), field: name})
    env = {**ENV, name: UNRELATED, "DATABASE_URL": UNRELATED}
    t = FakeTransport(routes(**{OAUTH_URL: REFRESH_REPLY}))
    fetch = partial(fetch_json, transport=t, sleep=lambda s: None)
    with pytest.raises(SourceError) as caught:
        PROVIDERS["twitch"](src, Context(fetch=fetch, env=env))
    assert caught.value.kind == "bad_env_name"
    assert UNRELATED not in str(caught.value)
    assert UNRELATED not in repr(t.calls)


def test_unrelated_env_values_never_leave_the_process() -> None:
    env = {
        **ENV,
        "DATABASE_URL": "SENTINEL-UNRELATED-DB",
        "AWS_SECRET_ACCESS_KEY": "SENTINEL-UNRELATED-AWS",
    }
    n = {"i": 0}

    def followers(req: Any) -> Any:
        n["i"] += 1
        return reply({}, status=401) if n["i"] == 1 else FOLLOW_REPLY

    t = FakeTransport(routes(**{FOLLOWERS: followers, OAUTH_URL: REFRESH_REPLY}))
    assert run(t, Sink(), env) == 321
    assert len(t.calls) >= 4
    assert "SENTINEL-UNRELATED" not in repr(t.calls)


def test_default_names_work_without_naming_them() -> None:
    src = TwitchSource.model_validate(
        {"platform": "tw", "kind": "twitch", "login": "some_user"}
    )
    env = {
        "TWITCH_CLIENT_ID": "SENTINEL-CID",
        "TWITCH_CLIENT_SECRET": "SENTINEL-CSECRET",
        "TWITCH_USER_ACCESS_TOKEN": "SENTINEL-ACCESS-OLD",
    }
    t = FakeTransport(routes())
    fetch = partial(fetch_json, transport=t, sleep=lambda s: None)
    assert PROVIDERS["twitch"](src, Context(fetch=fetch, env=env)) == 321
