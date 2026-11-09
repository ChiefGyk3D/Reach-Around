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
TH_COUNT = "https://graph.threads.com/v1.0/1234567890/threads_insights?"
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
            **over,
        }
    )


def th(**over: Any) -> ThreadsSource:
    return ThreadsSource(
        **{
            "platform": "threads",
            "kind": "threads",
            "user_id": "1234567890",
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
        "/v1.0/1234567890/threads_insights?metric=followers_count&access_token="
    )


def test_threads_user_id_is_required() -> None:
    with pytest.raises(ValueError):
        ThreadsSource(platform="threads", kind="threads")
    source = ThreadsSource.model_construct(platform="threads", kind="threads")
    transport = FakeTransport({TH_COUNT: reply(TH_BODY)})
    assert fails(source, transport) == "bad_source_setting"
    assert transport.calls == []


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
    expires = ig(token_expires="2027-01-31").token_expires  # noqa: S106
    assert expires == date(2027, 1, 31)
    assert th(user_id="123").user_id == "123"
    for bad in (
        {"token_expires": "31/01/2027"},
        {"token_expires": "2027-1-3"},
        {"refresh_within_days": 0},
        {"refresh_within_days": 60},
        {"refresh_within_days": "10"},
        {"access_token_env": "INSTAGRAM_ACCESS_TOKEN"},
        {"auto_refresh": "yes"},
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
    source = ig(auto_refresh=True, token_expires=in_days(5))
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
    source = th(auto_refresh=True, token_expires=TODAY.isoformat())
    transport = FakeTransport(
        {TH_REFRESH: reply(REFRESH_BODY), TH_COUNT: reply(TH_BODY)}
    )
    assert run(source, transport, sink=Sink()) == 987
    query = parse_qs(urlsplit(transport.urls[0]).query)
    assert query["grant_type"] == ["th_refresh_token"]
    assert transport.calls[0].host == "graph.threads.com"


def test_window_edges() -> None:
    for days, expected_calls in ((10, 2), (11, 1), (0, 2)):
        source = ig(auto_refresh=True, token_expires=in_days(days))
        transport = FakeTransport(routes_ig())
        run(source, transport, sink=Sink())
        assert len(transport.calls) == expected_calls, days


def test_no_expiry_recorded_means_no_refresh() -> None:
    transport = FakeTransport(routes_ig())
    run(ig(), transport, sink=Sink())
    assert len(transport.calls) == 1


def test_configurable_window() -> None:
    source = ig(auto_refresh=True, token_expires=in_days(30), refresh_within_days=45)
    transport = FakeTransport(routes_ig())
    run(source, transport, sink=Sink())
    assert len(transport.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_refresh_refused_keeps_going_with_the_old_token(status: int) -> None:
    sink = Sink()
    source = ig(auto_refresh=True, token_expires=in_days(2))
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
        {"access_token": NEW_TOKEN, "expires_in": 1},
        {"access_token": NEW_TOKEN, "expires_in": 7 * 86_400 - 1},
        {"access_token": NEW_TOKEN, "expires_in": 120 * 86_400 + 1},
        {"access_token": 5, "expires_in": 5184000},
    ],
)
def test_malformed_refresh_answer_is_a_refusal_not_a_store(
    body: dict[str, Any],
) -> None:
    sink = Sink()
    source = ig(auto_refresh=True, token_expires=TODAY.isoformat())
    transport = FakeTransport(routes_ig(**{IG_REFRESH: reply(body)}))
    assert run(source, transport, sink=sink) == 1234
    assert sink.stored == [] and [a.kind for a in sink.alerts] == ["refresh_refused"]
    assert token_of(transport.urls[-1]) == TOKEN


def test_refresh_redirect_is_refused() -> None:
    sink = Sink()
    redirect = reply({}, status=302, headers={"location": "https://evil.example/"})
    source = ig(auto_refresh=True, token_expires=TODAY.isoformat())
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


def test_a_failing_sink_keeps_the_original_token_and_says_so() -> None:
    sink = Sink(fail=True)
    source = ig(auto_refresh=True, token_expires=TODAY.isoformat())
    transport = FakeTransport(routes_ig())
    assert run(source, transport, sink=sink) == 1234
    assert [a.kind for a in sink.alerts] == ["refresh_store_failed"]
    assert token_of(transport.urls[-1]) == TOKEN  # the unstored token is not used
    assert NEW_TOKEN not in repr(sink.alerts)


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
    source = ig(auto_refresh=True, token_expires=in_days(1))
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
    - {platform: instagram, kind: instagram}
    - {platform: threads, kind: threads, user_id: '1234567890'}
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
    both = {"INSTAGRAM_ACCESS_TOKEN": "x", "THREADS_ACCESS_TOKEN": "y"}
    assert source_platforms(config, both) == {"instagram", "threads"}
    assert shown_numbers(config, None, TODAY) == []


# ---- the credential variable is fixed, never configurable ----

UNRELATED = "unrelated-value-9f8e7d6c"  # noqa: S105
BAD_NAMES = [
    "DATABASE_URL",
    "AWS_SECRET_ACCESS_KEY",
    "INSTAGRAM_APP_SECRET",
    "THREADS_WEBHOOK_SECRET",
    "INSTAGRAM_ACCESS_TOKEN_2",
    "THREADS_ACCESS_TOKEN",
    "INSTAGRAM_",
    "instagram_access_token",
    "INSTAGRAM_ACCESS_TOKEN\n",
    "INSTAGRAM_ACCESS" + chr(0) + "TOKEN",
    "",
]


@pytest.mark.parametrize("name", BAD_NAMES + ["INSTAGRAM_ACCESS_TOKEN"])
def test_config_cannot_name_a_credential_variable(name: str) -> None:
    with pytest.raises(ValueError):
        ig(access_token_env=name)
    with pytest.raises(ValueError):
        th(access_token_env=name)


def test_the_names_are_fixed() -> None:
    assert ig().access_token_env == "INSTAGRAM_ACCESS_TOKEN"  # noqa: S105
    assert th().access_token_env == "THREADS_ACCESS_TOKEN"  # noqa: S105


def test_the_use_time_check_refuses_a_tampered_source() -> None:
    class Tampered(InstagramSource):
        @property
        def access_token_env(self) -> str:
            return "INSTAGRAM_APP_SECRET"

    source = Tampered(platform="instagram", kind="instagram")
    env = {"INSTAGRAM_APP_SECRET": UNRELATED, "INSTAGRAM_ACCESS_TOKEN": TOKEN}
    transport = FakeTransport({IG_COUNT: reply(IG_BODY)})
    assert fails(source, transport, env=env) == "bad_credential_name"
    assert transport.calls == []


def test_an_unrelated_variable_value_never_reaches_a_request() -> None:
    env = dict(
        ENV,
        DATABASE_URL=UNRELATED,
        AWS_SECRET_ACCESS_KEY=UNRELATED,
        INSTAGRAM_APP_SECRET=UNRELATED,
        THREADS_WEBHOOK_SECRET=UNRELATED,
        INSTAGRAM_ACCESS_TOKEN_2=UNRELATED,
    )
    source = ig(auto_refresh=True, token_expires=in_days(1))
    transport = FakeTransport(routes_ig())
    assert run(source, transport, env=env, sink=Sink()) == 1234
    assert len(transport.calls) == 2
    for call in transport.calls:
        blob = repr((call.host, call.path, dict(call.headers), call.body))
        assert UNRELATED not in blob


# ---- refresh is report-only unless the caller supplies a sink ----


def test_inside_the_window_the_default_path_reads_and_reports_only() -> None:
    stream = io.StringIO()
    source = ig(token_expires=in_days(4))
    transport = FakeTransport(routes_ig())
    assert run(source, transport, sink=NoopSink(stream)) == 1234
    assert [c.path.split("?")[0] for c in transport.calls] == ["/v27.0/me"]
    assert token_of(transport.urls[0]) == TOKEN
    lines = stream.getvalue().splitlines()
    assert lines == [
        "token-health: source=instagram kind=refresh_needed "
        "names=INSTAGRAM_ACCESS_TOKEN days_left=4"
    ]


def test_the_cli_default_path_makes_no_refresh_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    soon = in_days(2)
    text = CONFIG.replace(
        "kind: instagram}", f"kind: instagram, token_expires: {soon}}}"
    )
    (tmp_path / "history").mkdir()
    (tmp_path / "site.yaml").write_text(text, encoding="utf-8")
    transport = FakeTransport({IG_COUNT: reply(IG_BODY), TH_COUNT: reply(TH_BODY)})
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(
        "reach_around.commands.counts.datetime",
        type("D", (), {"now": staticmethod(lambda tz=None: _noon(TODAY))}),
        raising=False,
    )
    do_refresh(tmp_path)
    paths = [c.path.split("?")[0] for c in transport.calls]
    assert "/refresh_access_token" not in paths
    err = capsys.readouterr().err
    assert err.count("kind=refresh_needed") == 1
    assert TOKEN not in err


def _noon(day: date) -> Any:
    from datetime import UTC, datetime

    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def test_auto_refresh_is_refused_without_a_caller_supplied_sink() -> None:
    source = ig(auto_refresh=True, token_expires=in_days(2))
    transport = FakeTransport(routes_ig())
    assert fails(source, transport, sink=None) == "refresh_sink_required"
    assert transport.calls == []


def test_auto_refresh_defaults_off() -> None:
    assert ig().auto_refresh is False and th().auto_refresh is False


# ---- round 2 hardening ----


def test_expires_in_bounds_and_utc_expiry_day() -> None:
    for seconds, expected in ((7 * 86_400, 7), (120 * 86_400, 120)):
        sink = Sink()
        source = ig(auto_refresh=True, token_expires=TODAY.isoformat())
        body = dict(REFRESH_BODY, expires_in=seconds)
        run(source, FakeTransport(routes_ig(**{IG_REFRESH: reply(body)})), sink=sink)
        assert sink.stored[0][3] == TODAY + timedelta(days=expected)


class Boom:
    """A fetch that raises with the URL in its message and records the URL."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs: Any) -> dict[str, object]:
        self.urls.append(url)
        raise RuntimeError(url)


def no_url_in_chain(exc: BaseException | None) -> bool:
    """No exception in the cause or context chain is the fake's RuntimeError."""
    while exc is not None:
        if isinstance(exc, RuntimeError):
            return False
        if exc.__cause__ is None and exc.__context__ is None:
            return True
        exc = exc.__cause__ or exc.__context__
    return True


def test_an_unexpected_fetch_exception_becomes_a_fixed_word() -> None:
    sink = Sink()
    boom = Boom()
    source = ig(auto_refresh=True, token_expires=in_days(1))
    context = Context(fetch=boom, env=ENV, meta_sink=sink, today=TODAY)
    with pytest.raises(SourceError) as caught:
        PROVIDERS["instagram"](source, context)
    err = caught.value
    assert err.kind == "fetch_failed" and str(err) == "fetch_failed"
    assert no_url_in_chain(err)
    assert TOKEN in boom.urls[0]  # the fake did get the URL; nothing came back
    text = repr(err) + repr(sink.alerts) + str(err.args)
    assert TOKEN not in text and "graph.instagram.com" not in text
    assert [a.kind for a in sink.alerts] == ["refresh_refused"]


def test_an_unexpected_exception_on_the_count_call_is_fixed_too() -> None:
    boom = Boom()
    context = Context(fetch=boom, env=ENV, today=TODAY)
    with pytest.raises(SourceError) as caught:
        PROVIDERS["threads"](th(), context)
    assert str(caught.value) == "fetch_failed"
    assert no_url_in_chain(caught.value)


def test_copy_with_a_string_false_does_not_refresh() -> None:
    source = ig(token_expires=in_days(1)).model_copy(update={"auto_refresh": "false"})
    transport = FakeTransport(routes_ig())
    assert fails(source, transport, sink=Sink()) == "bad_source_setting"
    assert transport.calls == []


def test_model_construct_does_not_refresh() -> None:
    source = InstagramSource.model_construct(
        platform="instagram",
        kind="instagram",
        token_expires=TODAY,
        refresh_within_days="10",
        auto_refresh=1,
    )
    transport = FakeTransport(routes_ig())
    assert fails(source, transport, sink=Sink()) == "bad_source_setting"
    assert transport.calls == []
    wild = ig().model_copy(update={"refresh_within_days": 9999})
    assert fails(wild, transport, sink=Sink()) == "bad_source_setting"
    odd_day = th().model_copy(update={"user_id": "12/../x"})
    assert fails(odd_day, transport, sink=Sink()) == "bad_source_setting"
    assert transport.calls == []


def test_far_future_expiry_is_refused_at_config_load() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).date()
    ig(token_expires=(now + timedelta(days=120)).isoformat())
    for bad in ("9999-12-31", (now + timedelta(days=121)).isoformat()):
        with pytest.raises(ValueError):
            ig(token_expires=bad)


@pytest.mark.parametrize("days", [200, -200])
def test_implausible_expiry_is_reported_at_use_time(days: int) -> None:
    sink = Sink()
    source = ig().model_copy(update={"token_expires": TODAY + timedelta(days=days)})
    transport = FakeTransport(routes_ig())
    try:
        run(source, transport, sink=sink)
    except SourceError:
        pass
    kinds = [a.kind for a in sink.alerts]
    assert kinds[0] == "expiry_implausible"
    assert sink.alerts[0].days_left == days


def test_status_and_outcome_hold_only_the_fixed_word() -> None:
    from reach_around.counts.refresh import refresh, render_status

    boom = Boom()
    source = ig(token_expires=in_days(1))
    from datetime import UTC, datetime

    moment = datetime(2026, 11, 9, 12, tzinfo=UTC)
    _, outcomes = refresh(
        [source], {}, env=ENV, fetch=boom, now=lambda: moment, meta_sink=Sink()
    )
    assert [(o.status, o.detail) for o in outcomes] == [("error", "fetch_failed")]
    text = render_status({}, outcomes, moment)
    assert TOKEN not in text and "graph.instagram.com" not in text


# ---- review threads ----


class AlertBoom(Sink):
    def alert(self, alert: HealthAlert) -> None:
        raise RuntimeError("sink down")


def test_a_failing_alert_sink_never_blocks_the_count() -> None:
    source = ig(token_expires=in_days(1))  # refresh_needed alert, sink raises
    assert run(source, FakeTransport(routes_ig()), sink=AlertBoom()) == 1234
    rejected = FakeTransport({IG_COUNT: reply({}, status=401)})
    assert fails(ig(), rejected, sink=AlertBoom()) == "token_rejected"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_the_refresh_request_is_sent_once(status: int) -> None:
    sink = Sink()
    source = ig(auto_refresh=True, token_expires=TODAY.isoformat())
    transport = FakeTransport(routes_ig(**{IG_REFRESH: reply({}, status=status)}))
    assert run(source, transport, sink=sink) == 1234
    refreshes = [c for c in transport.calls if "refresh_access_token" in c.path]
    assert len(refreshes) == 1


def test_a_source_without_its_token_is_hand_kept_for_the_stale_gate(
    setup: Path,
) -> None:
    config = load_site_config(setup / "site.yaml")
    assert source_platforms(config, {}) == frozenset()
    only_ig = {"INSTAGRAM_ACCESS_TOKEN": "x"}
    assert source_platforms(config, only_ig) == {"instagram"}
    assert source_platforms(config, {"INSTAGRAM_ACCESS_TOKEN": ""}) == frozenset()
    both = dict(only_ig, THREADS_ACCESS_TOKEN="y")  # noqa: S106
    assert source_platforms(config, both) == {"instagram", "threads"}
