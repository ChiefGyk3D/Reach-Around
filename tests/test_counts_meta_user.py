# SPDX-License-Identifier: Apache-2.0
"""Instagram and Threads follower sources (#387). Mocks only: no live call.

Every response below is a recorded shape from the vendor docs, read 2026-11-09.
The token values are fake and must never appear in any output.
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config import load_site_config
from reach_around.config.numbers import shown_numbers, source_platforms
from reach_around.config.numbers_sources import InstagramSource, ThreadsSource
from reach_around.counts.history import read_history
from reach_around.counts.meta_user import (
    HealthAlert,
    MetaError,
    NewToken,
    NoopSink,
    check_url,
)
from reach_around.counts.providers import (
    PROVIDERS,
    Context,
    SourceError,
    SourceSkip,
)
from reach_around.net.egress import EgressBlocked
from reach_around.net.fetch import FetchError, fetch_json

TOKEN = "IGAAfakeTOKENvalue1234567890"  # noqa: S105
NEW_TOKEN = "IGAAbrandNEWtokenvalue0987654321"  # noqa: S105
TODAY = date(2026, 11, 9)
ENV = {"INSTAGRAM_ACCESS_TOKEN": TOKEN, "THREADS_ACCESS_TOKEN": TOKEN}

IG_COUNT = "https://graph.instagram.com/v27.0/me?"
IG_REFRESH = "https://graph.instagram.com/refresh_access_token?"
TH_COUNT = "https://graph.threads.com/v1.0/me/threads_insights?"
TH_REFRESH = "https://graph.threads.com/refresh_access_token?"

IG_BODY = {"followers_count": 1234, "id": "17841400000000000"}
TH_BODY = {
    "data": [
        {
            "name": "followers_count",
            "period": "day",
            "title": "Followers",
            "total_value": {"value": 987},
            "id": "1234/insights/followers_count/day",
        }
    ]
}
REFRESH_BODY = {
    "access_token": NEW_TOKEN,
    "token_type": "bearer",
    "expires_in": 5184000,
}


def ig(**over: Any) -> InstagramSource:
    return InstagramSource(
        **{
            "platform": "instagram",
            "kind": "instagram",
            "access_token_env": "INSTAGRAM_ACCESS_TOKEN",
            **over,
        }
    )


def th(**over: Any) -> ThreadsSource:
    return ThreadsSource(
        **{
            "platform": "threads",
            "kind": "threads",
            "access_token_env": "THREADS_ACCESS_TOKEN",
            **over,
        }
    )


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.stored: list[tuple[str, str, str, date]] = []
        self.alerts: list[HealthAlert] = []
        self.fail = fail

    def store(self, token: NewToken, name: str, source: str, expires_on: date) -> None:
        if self.fail:
            raise RuntimeError(token.value)
        self.stored.append((token.value, name, source, expires_on))

    def alert(self, alert: HealthAlert) -> None:
        self.alerts.append(alert)


def ctx(
    transport: FakeTransport,
    sink: Any = None,
    env: dict[str, str] | None = None,
    sleeps: list[float] | None = None,
) -> Context:
    fetch = partial(
        fetch_json,
        transport=transport,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )
    return Context(
        fetch=fetch, env=ENV if env is None else env, meta_sink=sink, today=TODAY
    )


def run(source: Any, transport: FakeTransport, **kw: Any) -> int:
    return PROVIDERS[source.kind](source, ctx(transport, **kw))


def fails(source: Any, transport: FakeTransport, **kw: Any) -> str:
    with pytest.raises((SourceError, FetchError)) as caught:
        run(source, transport, **kw)
    return str(caught.value)


def token_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["access_token"][0]


def in_days(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


# ---- happy paths ----


def test_instagram_reads_followers_count() -> None:
    transport = FakeTransport({IG_COUNT: reply(IG_BODY)})
    assert run(ig(), transport) == 1234
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call.host == "graph.instagram.com"
    assert call.path.startswith("/v27.0/me?fields=followers_count&access_token=")
    assert token_of(transport.urls[0]) == TOKEN
    assert call.max_bytes <= 65_536


def test_threads_reads_total_value() -> None:
    transport = FakeTransport({TH_COUNT: reply(TH_BODY)})
    assert run(th(), transport) == 987
    assert transport.calls[0].host == "graph.threads.com"
    assert transport.calls[0].path.startswith(
        "/v1.0/me/threads_insights?metric=followers_count&access_token="
    )


def test_threads_user_id_goes_in_the_path() -> None:
    url = "https://graph.threads.com/v1.0/12345/threads_insights?"
    transport = FakeTransport({url: reply(TH_BODY)})
    assert run(th(user_id="12345"), transport) == 987


def test_graph_text_javascript_content_type_is_accepted() -> None:
    body = b'{"followers_count": 5}'
    transport = FakeTransport(
        {IG_COUNT: reply(body=body, headers={"content-type": "text/javascript"})}
    )
    assert run(ig(), transport) == 5


def test_other_content_types_are_still_refused() -> None:
    transport = FakeTransport(
        {IG_COUNT: reply(body=b"{}", headers={"content-type": "text/html"})}
    )
    assert fails(ig(), transport) == "content_type"


def test_token_not_set_is_a_skip_not_an_error() -> None:
    transport = FakeTransport({})
    for source in (ig(), th()):
        with pytest.raises(SourceSkip) as caught:
            run(source, transport, env={})
        assert caught.value.reason == "token_not_set"
        with pytest.raises(SourceSkip):
            run(source, transport, env={source.access_token_env: ""})
    assert transport.calls == []


def test_unusable_token_value_is_refused_without_a_call() -> None:
    transport = FakeTransport({})
    for bad in ("has space", "tab\there", "x" * 513, "café"):
        assert (
            fails(ig(), transport, env={"INSTAGRAM_ACCESS_TOKEN": bad})
            == "invalid_credential"
        )
    assert transport.calls == []


# ---- hostile values ----

BAD_IG = [
    ({"followers_count": -1}, "bad_count"),
    ({"followers_count": 1.5}, "bad_count"),
    ({"followers_count": 12.0}, "bad_count"),
    ({"followers_count": "12"}, "bad_count"),
    ({"followers_count": True}, "bad_count"),
    ({"followers_count": None}, "bad_count"),
    ({"followers_count": 10**10 + 1}, "bad_count"),
    ({"id": "1"}, "missing_field"),
    ({}, "missing_field"),
]


@pytest.mark.parametrize(("body", "kind"), BAD_IG)
def test_instagram_hostile_counts(body: dict[str, object], kind: str) -> None:
    assert fails(ig(), FakeTransport({IG_COUNT: reply(body)})) == kind


def _threads(value: object) -> dict[str, object]:
    return {"data": [{"name": "followers_count", "total_value": {"value": value}}]}


BAD_TH = [
    (_threads(-1), "bad_count"),
    (_threads(2.5), "bad_count"),
    (_threads("7"), "bad_count"),
    (_threads(None), "bad_count"),
    ({"data": []}, "missing_field"),
    ({"data": "x"}, "missing_field"),
    ({"data": [{"name": "views", "total_value": {"value": 3}}]}, "missing_field"),
    ({"data": [{"name": "followers_count"}]}, "missing_field"),
    ({"data": [{"name": "followers_count", "total_value": {}}]}, "missing_field"),
    ({}, "missing_field"),
]


@pytest.mark.parametrize(("body", "kind"), BAD_TH)
def test_threads_hostile_counts(body: dict[str, object], kind: str) -> None:
    assert fails(th(), FakeTransport({TH_COUNT: reply(body)})) == kind


def test_huge_body_is_capped() -> None:
    big = b'{"followers_count": 5, "pad": "' + b"a" * 70_000 + b'"}'
    assert fails(ig(), FakeTransport({IG_COUNT: reply(body=big)})) == "too_large"


def test_malformed_json_and_non_objects() -> None:
    for raw in (b"{not json", b"[1, 2]", b'"text"', b"\xff\xfe"):
        assert fails(ig(), FakeTransport({IG_COUNT: reply(body=raw)})) == "bad_json"


# ---- HTTP failures, rate limits, timeouts, redirects ----


@pytest.mark.parametrize("status", [400, 401])
def test_rejected_token_is_content_free_and_alerts(status: int) -> None:
    sink = Sink()
    transport = FakeTransport(
        {IG_COUNT: reply({"error": {"message": TOKEN, "code": 190}}, status=status)}
    )
    assert fails(ig(), transport, sink=sink) == "token_rejected"
    assert [a.kind for a in sink.alerts] == ["token_rejected"]
    assert sink.alerts[0].names == ("INSTAGRAM_ACCESS_TOKEN",)


@pytest.mark.parametrize("status", [403, 404])
def test_other_4xx_keep_their_status_word(status: int) -> None:
    transport = FakeTransport({IG_COUNT: reply({}, status=status)})
    assert fails(ig(), transport) == f"http_{status}"


def test_5xx_is_retried_a_bounded_number_of_times() -> None:
    transport = FakeTransport({IG_COUNT: reply({}, status=503)})
    assert fails(ig(), transport) == "server_error"
    assert len(transport.calls) == 3


def test_429_retry_after_is_honored_within_the_cap() -> None:
    answers = iter(
        [reply({}, status=429, headers={"retry-after": "7"}), reply(IG_BODY)]
    )
    sleeps: list[float] = []
    transport = FakeTransport({IG_COUNT: lambda request: next(answers)})
    assert run(ig(), transport, sleeps=sleeps) == 1234
    assert len(sleeps) == 1 and 7 <= sleeps[0] <= 8


def test_429_with_a_huge_retry_after_gives_up_without_waiting() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        {IG_COUNT: reply({}, status=429, headers={"retry-after": "86400"})}
    )
    assert fails(ig(), transport, sleeps=sleeps) == "rate_limited"
    assert sleeps == [] and len(transport.calls) == 1


def test_endless_429_stops_after_the_attempt_cap() -> None:
    transport = FakeTransport({IG_COUNT: reply({}, status=429)})
    assert fails(ig(), transport) == "rate_limited"
    assert len(transport.calls) == 3


def test_timeout_surfaces_as_a_fixed_word() -> None:
    def boom(request: Any) -> Any:
        raise EgressBlocked("timeout", "graph.instagram.com")

    assert fails(ig(), FakeTransport({IG_COUNT: boom})) == "egress_timeout"


def test_requests_carry_a_bounded_deadline() -> None:
    transport = FakeTransport({IG_COUNT: reply(IG_BODY)})
    run(ig(), transport)
    assert transport.calls[0].deadline > 0


def test_redirects_are_never_followed() -> None:
    redirect = reply({}, status=302, headers={"location": "https://evil.example/x"})
    transport = FakeTransport({IG_COUNT: redirect})
    assert fails(ig(), transport) == "redirect"
    assert [c.host for c in transport.calls] == ["graph.instagram.com"]
    same_host = reply(
        {}, status=302, headers={"location": "https://graph.instagram.com/other"}
    )
    transport = FakeTransport({IG_COUNT: same_host})
    assert fails(ig(), transport) == "redirect"
    assert len(transport.calls) == 1


# ---- host validation ----


@pytest.mark.parametrize(
    "url",
    [
        "https://graph.facebook.com/v27.0/me?access_token=x",
        "https://graph.threads.net/v1.0/me?access_token=x",
        "https://evil.example/graph.instagram.com",
        "https://graph.instagram.com.evil.example/me",
        "http://graph.instagram.com/me",
        "https://graph.instagram.com:8443/me",
        "https://user@graph.instagram.com/me",
        "https://graph.instagram.com/me#frag",
        "ftp://graph.instagram.com/me",
    ],
)
def test_a_wrong_host_is_refused(url: str) -> None:
    with pytest.raises(MetaError) as caught:
        check_url(url, "graph.instagram.com")
    assert caught.value.kind == "bad_host"


def test_the_right_host_passes() -> None:
    check_url("https://graph.instagram.com/v27.0/me?x=1", "graph.instagram.com")
    check_url("https://graph.threads.com/refresh_access_token", "graph.threads.com")


# ---- config ----


def test_config_defaults_and_bounds() -> None:
    s = ig()
    assert s.token_expires is None and s.refresh_within_days == 10
    assert ig(token_expires="2027-01-31").token_expires  # noqa: S106 == date(2027, 1, 31)
    assert th(user_id="123").user_id == "123"
    for bad in (
        {"token_expires": "31/01/2027"},
        {"token_expires": "2027-1-3"},
        {"refresh_within_days": 0},
        {"refresh_within_days": 60},
        {"refresh_within_days": "10"},
        {"access_token_env": "lower"},
        {"extra": 1},
    ):
        with pytest.raises(ValueError):
            ig(**bad)
    for bad_id in ("abc", "1 2", "", "9" * 25):
        with pytest.raises(ValueError):
            th(user_id=bad_id)


# ---- refresh ----


def routes_ig(**over: Any) -> dict[str, Any]:
    routes: dict[str, Any] = {
        IG_REFRESH: reply(REFRESH_BODY),
        IG_COUNT: reply(IG_BODY),
    }
    routes.update(over)
    return routes


def test_refresh_inside_the_window_runs_once_then_uses_the_new_token() -> None:
    sink = Sink()
    source = ig(token_expires=in_days(5))
    transport = FakeTransport(routes_ig())
    assert run(source, transport, sink=sink) == 1234
    assert [c.path.split("?")[0] for c in transport.calls] == [
        "/refresh_access_token",
        "/v27.0/me",
    ]
    refresh_query = parse_qs(urlsplit(transport.urls[0]).query)
    assert refresh_query["grant_type"] == ["ig_refresh_token"]
    assert refresh_query["access_token"] == [TOKEN]
    assert token_of(transport.urls[1]) == NEW_TOKEN
    assert sink.stored == [
        (NEW_TOKEN, "INSTAGRAM_ACCESS_TOKEN", "instagram", TODAY + timedelta(days=60))
    ]
    assert sink.alerts == []


def test_threads_refresh_uses_its_own_grant_and_host() -> None:
    source = th(token_expires=TODAY.isoformat())
    transport = FakeTransport(
        {TH_REFRESH: reply(REFRESH_BODY), TH_COUNT: reply(TH_BODY)}
    )
    assert run(source, transport, sink=Sink()) == 987
    query = parse_qs(urlsplit(transport.urls[0]).query)
    assert query["grant_type"] == ["th_refresh_token"]
    assert transport.calls[0].host == "graph.threads.com"


def test_window_edges() -> None:
    for days, expected_calls in ((10, 2), (11, 1), (0, 2)):
        source = ig(token_expires=in_days(days))
        transport = FakeTransport(routes_ig())
        run(source, transport, sink=Sink())
        assert len(transport.calls) == expected_calls, days


def test_no_expiry_recorded_means_no_refresh() -> None:
    transport = FakeTransport(routes_ig())
    run(ig(), transport, sink=Sink())
    assert len(transport.calls) == 1


def test_configurable_window() -> None:
    source = ig(token_expires=in_days(30), refresh_within_days=45)
    transport = FakeTransport(routes_ig())
    run(source, transport, sink=Sink())
    assert len(transport.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_refresh_refused_keeps_going_with_the_old_token(status: int) -> None:
    sink = Sink()
    source = ig(token_expires=in_days(2))
    transport = FakeTransport(
        routes_ig(**{IG_REFRESH: reply({"error": {"message": TOKEN}}, status=status)})
    )
    assert run(source, transport, sink=sink) == 1234
    assert [a.kind for a in sink.alerts] == ["refresh_refused"]
    assert token_of(transport.urls[-1]) == TOKEN
    assert sink.stored == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"access_token": ""},
        {"access_token": "has space", "expires_in": 5184000},
        {"access_token": NEW_TOKEN},
        {"access_token": NEW_TOKEN, "expires_in": -5},
        {"access_token": NEW_TOKEN, "expires_in": 1.5},
        {"access_token": NEW_TOKEN, "expires_in": 10**12},
        {"access_token": 5, "expires_in": 5184000},
    ],
)
def test_malformed_refresh_answer_is_a_refusal_not_a_store(
    body: dict[str, Any],
) -> None:
    sink = Sink()
    source = ig(token_expires=TODAY.isoformat())
    transport = FakeTransport(routes_ig(**{IG_REFRESH: reply(body)}))
    assert run(source, transport, sink=sink) == 1234
    assert sink.stored == [] and [a.kind for a in sink.alerts] == ["refresh_refused"]
    assert token_of(transport.urls[-1]) == TOKEN


def test_refresh_redirect_is_refused() -> None:
    sink = Sink()
    redirect = reply({}, status=302, headers={"location": "https://evil.example/"})
    source = ig(token_expires=TODAY.isoformat())
    transport = FakeTransport(routes_ig(**{IG_REFRESH: redirect}))
    run(source, transport, sink=sink)
    assert "evil.example" not in {c.host for c in transport.calls}
    assert [a.kind for a in sink.alerts] == ["refresh_refused"]


def test_expired_recorded_day_stops_before_any_call() -> None:
    sink = Sink()
    source = ig(token_expires=in_days(-1))
    transport = FakeTransport(routes_ig())
    assert fails(source, transport, sink=sink) == "token_expired"
    assert transport.calls == []
    assert [a.kind for a in sink.alerts] == ["token_expired"]


def test_a_failing_sink_does_not_lose_the_number() -> None:
    sink = Sink(fail=True)
    source = ig(token_expires=TODAY.isoformat())
    assert run(source, FakeTransport(routes_ig()), sink=sink) == 1234
    assert [a.kind for a in sink.alerts] == ["rotated_unstored"]


def test_health_alert_line_shape() -> None:
    alert = HealthAlert("instagram", "refresh_refused", ("INSTAGRAM_ACCESS_TOKEN",))
    assert alert.line() == (
        "token-health: source=instagram kind=refresh_refused "
        "names=INSTAGRAM_ACCESS_TOKEN"
    )


# ---- no secret in any output ----

ALL_FAILURES: list[tuple[str, Any]] = [
    ("400", lambda: reply({"error": TOKEN}, status=400)),
    ("401", lambda: reply({"error": TOKEN}, status=401)),
    ("403", lambda: reply({"error": TOKEN}, status=403)),
    ("429", lambda: reply({"error": TOKEN}, status=429)),
    ("500", lambda: reply({"error": TOKEN}, status=500)),
    (
        "redirect",
        lambda: reply({}, status=302, headers={"location": f"/x?access_token={TOKEN}"}),
    ),
    ("badjson", lambda: reply(body=TOKEN.encode())),
    ("bigbody", lambda: reply(body=TOKEN.encode() + b" " * 80_000)),
    ("float", lambda: reply({"followers_count": 1.5, "t": TOKEN})),
    ("missing", lambda: reply({"t": TOKEN})),
]


@pytest.mark.parametrize(
    ("label", "make"), ALL_FAILURES, ids=[n for n, _ in ALL_FAILURES]
)
def test_no_token_value_in_any_error_log_or_alert(
    label: str, make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stream = io.StringIO()
    sink = NoopSink(stream)
    source = ig(token_expires=in_days(1))
    transport = FakeTransport({IG_REFRESH: make(), IG_COUNT: make()})
    with pytest.raises((SourceError, FetchError)) as caught:
        run(source, transport, sink=sink)
    seen = [str(caught.value), repr(caught.value), stream.getvalue(), caplog.text]
    seen += [repr(arg) for arg in caught.value.args]
    for text in seen:
        assert TOKEN not in text and NEW_TOKEN not in text


def test_noop_sink_prints_names_and_instruction_only() -> None:
    stream = io.StringIO()
    NoopSink(stream).store(
        NewToken(NEW_TOKEN), "INSTAGRAM_ACCESS_TOKEN", "instagram", TODAY
    )
    out = stream.getvalue()
    assert NEW_TOKEN not in out
    assert "INSTAGRAM_ACCESS_TOKEN" in out and "secret manager" in out
    assert "2026-11-09" in out
    assert NEW_TOKEN not in repr(NewToken(NEW_TOKEN))


# ---- end to end through counts refresh and the history store ----

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: instagram, handle: "@example", url: "https://example.com/i"}
          - {platform: threads, handle: "@example", url: "https://example.com/t"}
numbers:
  history_file: history/social.jsonl
  sources:
    - {platform: instagram, kind: instagram, access_token_env: INSTAGRAM_ACCESS_TOKEN}
    - {platform: threads, kind: threads, access_token_env: THREADS_ACCESS_TOKEN}
"""


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "history").mkdir()
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    transport = FakeTransport({IG_COUNT: reply(IG_BODY), TH_COUNT: reply(TH_BODY)})
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    return tmp_path


def do_refresh(root: Path, *extra: str) -> int:
    return main(["counts", "refresh", "--config", str(root / "site.yaml"), *extra])


def test_refresh_with_tokens_uses_api_and_appends_history(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", TOKEN)
    assert do_refresh(setup) == 0
    history = read_history(setup / "history" / "social.jsonl")
    assert sorted(
        (r.platform, r.metric, r.value, r.source) for r in history.records
    ) == [
        ("instagram", "followers", 1234, "api"),
        ("threads", "followers", 987, "api"),
    ]
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert "1234" in (setup / "numbers.json").read_text()


def test_refresh_without_tokens_skips_and_keeps_numbers(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    (setup / "numbers.json").write_text(
        '{"version": 1, "platforms": {"instagram": {"count": 50, '
        '"as_of": "2026-11-01T00:00:00Z"}}}\n',
        encoding="utf-8",
    )
    assert do_refresh(setup) == 0
    assert "skipped (token_not_set)" in capsys.readouterr().out
    assert '"count": 50' in (setup / "numbers.json").read_text()
    assert not (setup / "history" / "social.jsonl").exists()


def test_api_fed_platform_is_not_hand_kept(setup: Path) -> None:
    config = load_site_config(setup / "site.yaml")
    assert source_platforms(config) == {"instagram", "threads"}
    assert shown_numbers(config, None, TODAY) == []


# ---- credential variable names are confined to the platform prefix ----

UNRELATED = "unrelated-value-9f8e7d6c"  # noqa: S105
BAD_NAMES = [
    "DATABASE_URL",
    "AWS_SECRET_ACCESS_KEY",
    "THREADS_ACCESS_TOKEN",  # the other platform's name
    "INSTAGRAM_",
    "instagram_access_token",
    "INSTAGRAM_ACCESS\nTOKEN",
    "INSTAGRAM_ACCESS_TOKEN\n",
    "INSTAGRAM_ACCESS" + chr(0) + "TOKEN",
    "INSTAGRAM_" + "A" * 61,
    "",
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_config_refuses_unrelated_variable_names(name: str) -> None:
    with pytest.raises(ValueError):
        ig(access_token_env=name)
    with pytest.raises(ValueError):
        th(access_token_env=name.replace("THREADS", "INSTAGRAM"))


def test_default_names_and_prefixed_names() -> None:  # noqa: S105, S106
    assert (
        InstagramSource(platform="instagram", kind="instagram").access_token_env
        == "INSTAGRAM_ACCESS_TOKEN"  # noqa: S105
    )
    assert (
        ThreadsSource(platform="threads", kind="threads").access_token_env
        == "THREADS_ACCESS_TOKEN"  # noqa: S105
    )
    assert ig(access_token_env="INSTAGRAM_MAIN_TOKEN").access_token_env.startswith(  # noqa: S106
        "INSTAGRAM_"
    )


@pytest.mark.parametrize("name", BAD_NAMES[:-1])
def test_use_time_check_refuses_before_reading_the_environment(name: str) -> None:
    source = ig().model_copy(update={"access_token_env": name})  # skips validation
    env = {name: UNRELATED, "DATABASE_URL": UNRELATED}
    transport = FakeTransport({IG_COUNT: reply(IG_BODY)})
    assert fails(source, transport, env=env) == "bad_credential_name"
    assert transport.calls == []


def test_an_unrelated_variable_value_never_reaches_a_request() -> None:
    env = dict(ENV, DATABASE_URL=UNRELATED, AWS_SECRET_ACCESS_KEY=UNRELATED)
    source = ig(token_expires=in_days(1))
    transport = FakeTransport(routes_ig())
    assert run(source, transport, env=env, sink=Sink()) == 1234
    for call in transport.calls:
        blob = repr((call.host, call.path, dict(call.headers), call.body))
        assert UNRELATED not in blob
