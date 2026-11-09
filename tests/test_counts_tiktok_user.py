# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S105
"""TikTok followers via a user token (#388). Mocked transport only.

Fixtures follow the response shapes on the vendor pages read 2026-11-09. No
network, no real token: every value below is a sentinel.
"""

from __future__ import annotations

import io
import logging
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config import load_site_config
from reach_around.config.numbers import source_platforms
from reach_around.config.numbers_sources import TiktokSource
from reach_around.counts.history import read_history
from reach_around.counts.providers import (
    PROVIDERS,
    Context,
    SourceError,
    SourceSkip,
)
from reach_around.counts.tiktok_user import (
    NoopSink,
    TokenNames,
    TokenPair,
)
from reach_around.net.egress import EgressBlocked
from reach_around.net.fetch import FetchError, fetch_json

SRC = TiktokSource.model_validate({"platform": "tiktok", "kind": "tiktok"})
SRC_AUTO = SRC.model_copy(update={"auto_refresh": True})
ENV = {
    "TIKTOK_ACCESS_TOKEN": "SENTINEL-ACCESS-OLD",
    "TIKTOK_REFRESH_TOKEN": "SENTINEL-REFRESH-OLD",
    "TIKTOK_CLIENT_KEY": "SENTINEL-CKEY",
    "TIKTOK_CLIENT_SECRET": "SENTINEL-CSECRET",
}
SECRETS = [*ENV.values(), "SENTINEL-ACCESS-NEW", "SENTINEL-REFRESH-NEW"]
INFO = "https://open.tiktokapis.com/v2/user/info/?fields=follower_count"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INFO_OK = reply(
    {
        "data": {"user": {"follower_count": 321}},
        "error": {"code": "ok", "message": "", "log_id": "x"},
    }
)
REFRESH_OK = reply(
    {
        "access_token": "SENTINEL-ACCESS-NEW",
        "expires_in": 7200,
        "refresh_token": "SENTINEL-REFRESH-NEW",
        "refresh_expires_in": 15552000,
        "open_id": "abc",
        "scope": "user.info.basic,user.info.stats",
        "token_type": "Bearer",
    }
)


class Sink:
    def __init__(self, fail: bool = False) -> None:
        self.stored: list[tuple[TokenPair, TokenNames, str]] = []
        self.alerts: list[Any] = []
        self.fail = fail

    def store(self, pair: TokenPair, names: TokenNames, source: str) -> Any:
        if self.fail:
            raise RuntimeError(pair.access_token + pair.refresh_token)
        self.stored.append((pair, names, source))
        return True

    def alert(self, alert: Any) -> None:
        self.alerts.append(alert)


class DiscardSink(Sink):
    def store(self, pair: TokenPair, names: TokenNames, source: str) -> Any:
        return None


class TruthySink(Sink):
    def store(self, pair: TokenPair, names: TokenNames, source: str) -> Any:
        return "stored"


def routes(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {INFO: INFO_OK, TOKEN_URL: REFRESH_OK}
    base.update(over)
    return base


def make_ctx(
    transport: FakeTransport, sink: Any = None, env: dict[str, str] | None = None
) -> Context:
    fetch = partial(fetch_json, transport=transport, sleep=lambda s: None)
    return Context(fetch=fetch, env=ENV if env is None else env, tiktok_sink=sink)


def run(
    transport: FakeTransport,
    sink: Any = None,
    env: dict[str, str] | None = None,
    source: TiktokSource | None = None,
) -> int:
    chosen = source if source is not None else (SRC_AUTO if sink else SRC)
    return PROVIDERS["tiktok"](chosen, make_ctx(transport, sink, env))


def kind(transport: FakeTransport, sink: Any = None, **kw: Any) -> str:
    with pytest.raises((SourceError, FetchError)) as caught:
        run(transport, sink, **kw)
    return str(caught.value)


def no_leak(*texts: str) -> None:
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


def chain_text(exc: BaseException | None) -> str:
    parts: list[str] = []
    n = 0
    while exc is not None and n < 10:
        parts += [repr(exc), str(exc), repr(exc.args)]
        exc = exc.__cause__ or exc.__context__
        n += 1
    return " ".join(parts)


def expired_then_ok() -> FakeTransport:
    n = {"i": 0}

    def info(req: Any) -> Any:
        n["i"] += 1
        if n["i"] == 1:
            return reply({"error": {"code": "access_token_invalid"}}, status=401)
        return INFO_OK

    return FakeTransport(routes(**{INFO: info}))


# ---- the happy path ----


def test_reads_follower_count_with_bearer_header_not_in_the_url() -> None:
    t = FakeTransport(routes())
    assert run(t) == 321
    assert t.urls == [INFO]
    call = t.calls[0]
    assert call.host == "open.tiktokapis.com" and call.method == "GET"
    assert call.headers["Authorization"] == "Bearer SENTINEL-ACCESS-OLD"
    assert all("SENTINEL" not in u for u in t.urls)


def test_missing_access_token_is_a_skip_without_a_request() -> None:
    env = {k: v for k, v in ENV.items() if k != "TIKTOK_ACCESS_TOKEN"}
    t = FakeTransport(routes())
    with pytest.raises(SourceSkip) as caught:
        run(t, env=env)
    assert caught.value.reason == "token_not_set"
    assert t.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"user": {"follower_count": -1}}},
        {"data": {"user": {"follower_count": 1.5}}},
        {"data": {"user": {"follower_count": True}}},
        {"data": {"user": {"follower_count": "12"}}},
        {"data": {"user": {"follower_count": 10**30}}},
        {"data": {"user": {}}},
        {"data": {"user": None}},
        {"data": []},
        {"data": {}},
        {},
    ],
)
def test_hostile_counts_are_refused(payload: dict[str, Any]) -> None:
    got = kind(FakeTransport(routes(**{INFO: reply(payload)})))
    assert got in {"missing_field", "bad_count", "count_out_of_range"}
    no_leak(got)


def test_error_object_that_is_not_ok_is_a_fixed_word() -> None:
    body = {
        "data": {"user": {"follower_count": 5}},
        "error": {"code": "scope_permission_missed", "message": "SENTINEL-ACCESS-OLD"},
    }
    assert kind(FakeTransport(routes(**{INFO: reply(body)}))) == "api_error"


def test_huge_body_is_capped() -> None:
    big = reply(body=b"{" + b" " * 70_000 + b"}")
    assert kind(FakeTransport(routes(**{INFO: big}))) == "too_large"


@pytest.mark.parametrize("body", [b"not json", b"[1]", b"\xff\xfe", b""])
def test_malformed_json(body: bytes) -> None:
    assert kind(FakeTransport(routes(**{INFO: reply(body=body)}))) == "bad_json"


def test_wrong_content_type_is_refused() -> None:
    odd = reply(body=b"{}", headers={"content-type": "text/html"})
    assert kind(FakeTransport(routes(**{INFO: odd}))) == "content_type"


# ---- transport behavior ----


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_refused_even_on_same_host(status: int) -> None:
    moved = reply(
        {}, status=status, headers={"location": "https://open.tiktokapis.com/x"}
    )
    t = FakeTransport(routes(**{INFO: moved}))
    assert kind(t) == "redirect"
    assert len(t.calls) == 1


def test_refresh_redirect_is_refused() -> None:
    t = expired_then_ok()
    t.routes[TOKEN_URL] = reply(
        {}, status=307, headers={"location": "https://evil.example/"}
    )
    assert kind(t, Sink()) == "refresh_failed"
    assert all(c.host == "open.tiktokapis.com" for c in t.calls)


def test_429_honors_retry_after_and_5xx_is_bounded() -> None:
    n = {"i": 0}
    sleeps: list[float] = []

    def info(req: Any) -> Any:
        n["i"] += 1
        if n["i"] == 1:
            return reply({}, status=429, headers={"retry-after": "3"})
        return INFO_OK

    fetch = partial(
        fetch_json, transport=FakeTransport(routes(**{INFO: info})), sleep=sleeps.append
    )
    assert PROVIDERS["tiktok"](SRC, Context(fetch=fetch, env=ENV)) == 321
    assert sleeps and sleeps[0] >= 3
    t = FakeTransport(routes(**{INFO: reply({}, status=503)}))
    assert kind(t) == "server_error"
    assert len(t.calls) <= 3


def test_egress_block_is_a_fixed_word() -> None:
    def boom(req: Any) -> Any:
        raise EgressBlocked("timeout")

    assert kind(FakeTransport(routes(**{INFO: boom}))) == "egress_timeout"


def test_unexpected_exception_becomes_a_fixed_word_with_no_chain() -> None:
    def boom(url: str, **kw: Any) -> Any:
        raise RuntimeError("SENTINEL-ACCESS-OLD " + url)

    with pytest.raises(SourceError) as caught:
        PROVIDERS["tiktok"](SRC, Context(fetch=boom, env=ENV))
    assert caught.value.kind == "fetch_failed"
    no_leak(chain_text(caught.value))
    assert caught.value.__cause__ is None


def test_fetch_is_called_with_bounds_and_no_redirects() -> None:
    seen: dict[str, Any] = {}

    def spy(url: str, **kw: Any) -> dict[str, object]:
        seen.update(kw)
        return {"data": {"user": {"follower_count": 7}}}

    assert PROVIDERS["tiktok"](SRC, Context(fetch=spy, env=ENV)) == 7
    assert seen["follow_redirects"] is False
    assert 0 < seen["max_bytes"] <= 65_536
    assert 0 < seen["timeout"] <= 10.0


# ---- refresh ----


def test_401_without_auto_refresh_makes_no_refresh_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    t = FakeTransport(routes(**{INFO: reply({}, status=401)}))
    assert kind(t) == "token_expired"
    assert TOKEN_URL not in t.urls
    err = capsys.readouterr().err
    assert "token-health: source=tiktok kind=refresh_needed" in err
    no_leak(err)


def test_auto_refresh_defaults_off_and_is_strict() -> None:
    assert SRC.auto_refresh is False
    for bad in ("true", 1, "yes", None):
        with pytest.raises(ValidationError):
            TiktokSource.model_validate(
                {"platform": "tiktok", "kind": "tiktok", "auto_refresh": bad}
            )


@pytest.mark.parametrize("sink", [None, NoopSink(io.StringIO())])
def test_auto_refresh_is_refused_without_a_storing_sink(sink: Any) -> None:
    t = expired_then_ok()
    with pytest.raises(SourceError) as caught:
        PROVIDERS["tiktok"](SRC_AUTO, make_ctx(t, sink))
    assert caught.value.kind == "refresh_needs_sink"
    assert t.calls == []


@pytest.mark.parametrize("bad", ["false", "true", 1, 0, None])
def test_auto_refresh_type_is_checked_at_use_time(bad: Any) -> None:
    src = SRC.model_copy(update={"auto_refresh": bad})
    t = expired_then_ok()
    assert kind(t, source=src) == "bad_auto_refresh"
    assert t.calls == []


def test_401_refreshes_once_stores_pair_and_retries() -> None:
    t = expired_then_ok()
    sink = Sink()
    assert run(t, sink) == 321
    form_call = t.calls[1]
    assert form_call.method == "POST" and form_call.path == "/v2/oauth/token/"
    assert form_call.headers["Content-Type"] == "application/x-www-form-urlencoded"
    body = (form_call.body or b"").decode()
    assert "grant_type=refresh_token" in body
    assert "client_key=SENTINEL-CKEY" in body
    assert "refresh_token=SENTINEL-REFRESH-OLD" in body
    pair, names, source = sink.stored[0]
    assert (pair.access_token, pair.refresh_token) == (
        "SENTINEL-ACCESS-NEW",
        "SENTINEL-REFRESH-NEW",
    )
    assert names == TokenNames("TIKTOK_ACCESS_TOKEN", "TIKTOK_REFRESH_TOKEN")
    assert source == "tiktok"
    assert t.calls[2].headers["Authorization"] == "Bearer SENTINEL-ACCESS-NEW"
    assert "SENTINEL" not in repr(pair)


def test_only_one_refresh_even_if_still_401() -> None:
    t = FakeTransport(routes(**{INFO: reply({}, status=401)}))
    assert kind(t, Sink()) == "token_rejected"
    assert [c.path for c in t.calls].count("/v2/oauth/token/") == 1


@pytest.mark.parametrize(
    "bad",
    [
        reply({"error": "invalid_grant"}, status=400),
        reply({"access_token": "A"}),
        reply({"access_token": "A B", "refresh_token": "R"}),
        reply({"access_token": "A", "refresh_token": 5}),
        reply({"access_token": "x" * 600, "refresh_token": "R"}),
    ],
)
def test_refresh_failure_alerts_without_values(bad: Any) -> None:
    t = expired_then_ok()
    t.routes[TOKEN_URL] = bad
    sink = Sink()
    assert kind(t, sink) == "refresh_failed"
    assert sink.stored == []
    assert [a.kind for a in sink.alerts] == ["refresh_failed"]
    no_leak(*[a.line() for a in sink.alerts])


@pytest.mark.parametrize(
    "drop", ["TIKTOK_REFRESH_TOKEN", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]
)
def test_missing_refresh_or_client_values_make_no_refresh_request(drop: str) -> None:
    env = {k: v for k, v in ENV.items() if k != drop}
    t = expired_then_ok()
    sink = Sink()
    assert kind(t, sink, env=env) == "token_expired"
    assert TOKEN_URL not in t.urls
    assert [a.kind for a in sink.alerts] == ["refresh_needed"]


def test_hostile_sink_leaves_no_token_in_the_chain() -> None:
    t = expired_then_ok()
    with pytest.raises(SourceError) as caught:
        run(t, Sink(fail=True))
    assert caught.value.kind == "refresh_store_failed"
    no_leak(chain_text(caught.value))
    assert caught.value.__cause__ is None
    no_leak(chain_text(caught.value.__context__))


@pytest.mark.parametrize("sink_cls", [DiscardSink, TruthySink])
def test_only_a_true_receipt_counts_as_stored(sink_cls: Any) -> None:
    t = expired_then_ok()
    sink = sink_cls()
    assert kind(t, sink) == "refresh_store_failed"
    assert [a.kind for a in sink.alerts] == ["rotated_unstored"]
    assert len(t.calls) == 2  # no retry with a token nobody kept


def test_a_failing_alert_sink_never_blocks() -> None:
    class Bad(Sink):
        def alert(self, alert: Any) -> None:
            raise ValueError("SENTINEL-ACCESS-NEW")

    t = FakeTransport(routes(**{INFO: reply({}, status=401)}))
    assert kind(t, Bad()) == "token_rejected"


def test_noop_sink_prints_names_and_instruction_only() -> None:
    stream = io.StringIO()
    ok = NoopSink(stream).store(
        TokenPair("SENTINEL-ACCESS-NEW", "SENTINEL-REFRESH-NEW"),
        TokenNames("TIKTOK_ACCESS_TOKEN", "TIKTOK_REFRESH_TOKEN"),
        "tiktok",
    )
    assert ok is False
    text = stream.getvalue()
    assert "TIKTOK_ACCESS_TOKEN" in text and "TIKTOK_REFRESH_TOKEN" in text
    assert "token-health: source=tiktok kind=rotated_unstored" in text
    assert "docs/tiktok-source.md" in text
    no_leak(text)


def test_nothing_secret_reaches_logs_alerts_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    t = expired_then_ok()
    sink = Sink(fail=True)
    with pytest.raises(SourceError) as caught:
        run(t, sink)
    no_leak(caplog.text, repr(caught.value), *[a.line() for a in sink.alerts])


# ---- credential names are fixed ----

BAD_NAMES = ["DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "TIKTOK_APP_ID", ""]


@pytest.mark.parametrize("name", BAD_NAMES)
@pytest.mark.parametrize(
    "field", ["access_token_env", "client_key_env", "client_secret_env"]
)
def test_config_cannot_name_a_credential_variable(field: str, name: str) -> None:
    with pytest.raises(ValidationError):
        TiktokSource.model_validate(
            {"platform": "tiktok", "kind": "tiktok", field: name}
        )


class SpyEnv(dict[str, str]):
    reads: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        SpyEnv.reads.append(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> str:
        SpyEnv.reads.append(key)
        return super().__getitem__(key)


@pytest.mark.parametrize("name", BAD_NAMES)
def test_tampered_names_are_refused_before_any_env_read(name: str) -> None:
    SpyEnv.reads = []
    src = SRC.model_construct(
        platform="tiktok", kind="tiktok", auto_refresh=False, access_token_env=name
    )
    ctx = Context(fetch=lambda u, **k: {}, env=SpyEnv(ENV))
    with pytest.raises(SourceError) as caught:
        PROVIDERS["tiktok"](src, ctx)
    assert caught.value.kind == "bad_credential_name"
    assert SpyEnv.reads == []


def test_tampered_auto_refresh_type_is_refused_before_any_env_read() -> None:
    SpyEnv.reads = []
    src = SRC.model_construct(platform="tiktok", kind="tiktok", auto_refresh="false")
    ctx = Context(fetch=lambda u, **k: {}, env=SpyEnv(ENV))
    with pytest.raises(SourceError) as caught:
        PROVIDERS["tiktok"](src, ctx)
    assert caught.value.kind == "bad_auto_refresh"
    assert SpyEnv.reads == []


def test_unrelated_env_values_never_leave_the_process() -> None:
    env = {**ENV, "DATABASE_URL": "SENTINEL-DB", "GITHUB_TOKEN": "SENTINEL-GH"}
    t = expired_then_ok()
    run(t, Sink(), env=env)
    blob = b"".join(
        (c.body or b"") + c.path.encode() + str(c.headers).encode() for c in t.calls
    ).decode()
    assert "SENTINEL-DB" not in blob and "SENTINEL-GH" not in blob


def test_client_secret_is_only_sent_in_the_refresh_body() -> None:
    t = expired_then_ok()
    run(t, Sink())
    for c in t.calls:
        assert "SENTINEL-CSECRET" not in c.headers.get("Authorization", "")
        assert "SENTINEL-CSECRET" not in c.path
    carrying = [c for c in t.calls if b"SENTINEL-CSECRET" in (c.body or b"")]
    assert [c.path for c in carrying] == ["/v2/oauth/token/"]


@pytest.mark.parametrize("bad", ["a b", "tab\there", "x" * 600, "new\nline", "é"])
def test_unusable_access_token_is_refused_before_any_request(bad: str) -> None:
    t = FakeTransport(routes())
    assert kind(t, env={**ENV, "TIKTOK_ACCESS_TOKEN": bad}) == "invalid_credential"
    assert t.calls == []


# ---- config, history, stale gate ----

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: tiktok, handle: "@example", url: "https://example.com/t"}
numbers:
  history_file: history/social.jsonl
  sources:
    - {platform: tiktok, kind: tiktok}
"""


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "history").mkdir()
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    transport = FakeTransport({INFO: INFO_OK})
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)
    return tmp_path


def do_refresh(root: Path) -> int:
    return main(["counts", "refresh", "--config", str(root / "site.yaml")])


def test_refresh_with_token_appends_api_history(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", ENV["TIKTOK_ACCESS_TOKEN"])
    assert do_refresh(setup) == 0
    history = read_history(setup / "history" / "social.jsonl")
    assert [(r.platform, r.metric, r.value, r.source) for r in history.records] == [
        ("tiktok", "followers", 321, "api")
    ]
    out = capsys.readouterr()
    no_leak(out.out, out.err)


def test_refresh_without_token_skips_and_keeps_numbers(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
    (setup / "numbers.json").write_text(
        '{"version": 1, "platforms": {"tiktok": {"count": 50, '
        '"as_of": "2026-11-01T00:00:00Z"}}}\n',
        encoding="utf-8",
    )
    assert do_refresh(setup) == 0
    assert "skipped (token_not_set)" in capsys.readouterr().out
    assert '"count": 50' in (setup / "numbers.json").read_text()
    assert not (setup / "history" / "social.jsonl").exists()


def test_a_source_without_its_token_is_hand_kept_for_the_stale_gate(
    setup: Path,
) -> None:
    config = load_site_config(setup / "site.yaml")
    assert source_platforms(config, {}) == frozenset()
    assert source_platforms(config, {"TIKTOK_ACCESS_TOKEN": "x"}) == {"tiktok"}
