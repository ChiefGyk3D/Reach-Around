# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S105
"""The Doppler token sink, against a local fake HTTP server.

No real network, no real token: every value is a sentinel. The fake server
echoes written values in its answer, as the real update call does, so the
tests prove the adapter never logs, returns or raises what it was sent.
"""

from __future__ import annotations

import http.client
import json
import logging
import sys
import threading
from collections.abc import Iterator
from datetime import date
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.counts.meta_user import HealthAlert as MetaAlert
from reach_around.counts.meta_user import NewToken
from reach_around.counts.refresh import refresh as real_refresh
from reach_around.counts.secret_sink import (
    DopplerSink,
    MetaStyleSink,
    PairStyleSink,
    SinkError,
)
from reach_around.counts.twitch_user import HealthAlert, TokenNames, TokenPair
from reach_around.net.egress import RawResponse, TransportRequest
from reach_around.net.fetch import fetch_json

API_TOKEN = "SENTINEL-DOPPLER-API-TOKEN"
ACCESS = "SENTINEL-ACCESS-NEW"
REFRESH = "SENTINEL-REFRESH-NEW"
SECRETS = [API_TOKEN, ACCESS, REFRESH, "SENTINEL-ACCESS-OLD", "SENTINEL-REFRESH-OLD"]
PROJECT = "demo-project"
CONFIG = "dev"
NAMES = TokenNames("TWITCH_USER_ACCESS_TOKEN", "TWITCH_USER_REFRESH_TOKEN")


class FakeDoppler:
    """A local HTTP server that behaves like the two Doppler endpoints used."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str, str], str] = {}
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.mode = "ok"  # ok | drop | mutate | fail500 | restricted | no_read
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _send(self, status: int, payload: object) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size)
                outer.requests.append(
                    ("POST", self.path, dict(self.headers.items()), body)
                )
                if outer.mode == "fail500":
                    self._send(500, {"messages": ["boom " + body.decode()]})
                    return
                data = json.loads(body)
                echoed = {}
                for name, value in data["secrets"].items():
                    if outer.mode == "mutate":
                        value = value + "-changed"
                    if outer.mode != "drop":
                        outer.store[(data["project"], data["config"], name)] = value
                    echoed[name] = {"raw": value, "computed": value, "note": ""}
                self._send(200, {"secrets": echoed})

            def do_GET(self) -> None:
                outer.requests.append(
                    ("GET", self.path, dict(self.headers.items()), b"")
                )
                query = parse_qs(urlsplit(self.path).query)
                key = (query["project"][0], query["config"][0], query["name"][0])
                if outer.mode == "no_read" or key not in outer.store:
                    self._send(404, {"messages": ["not found"]})
                    return
                value: dict[str, Any] = {"raw": outer.store[key], "note": ""}
                if outer.mode == "restricted":
                    value = {"rawVisibility": "restricted", "note": ""}
                self._send(200, {"name": key[2], "value": value})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()

    def transport(self, other: FakeTransport | None = None) -> Any:
        """A fetch transport that sends api.doppler.com to this local server."""

        def send(request: TransportRequest) -> RawResponse:
            if request.host != "api.doppler.com":
                assert other is not None
                return other(request)
            conn = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_address[1], timeout=5
            )
            try:
                conn.request(
                    request.method,
                    request.path,
                    body=request.body,
                    headers=dict(request.headers),
                )
                answer = conn.getresponse()
                body = answer.read()
                headers = {k.lower(): v for k, v in answer.getheaders()}
                return RawResponse(answer.status, headers, body)
            finally:
                conn.close()

        return send


@pytest.fixture
def doppler() -> Iterator[FakeDoppler]:
    fake = FakeDoppler()
    try:
        yield fake
    finally:
        fake.close()


def make_sink(fake: FakeDoppler, **over: Any) -> DopplerSink:
    fetch = partial(fetch_json, transport=fake.transport(), sleep=lambda s: None)
    args: dict[str, Any] = {
        "project": PROJECT,
        "config": CONFIG,
        "token": API_TOKEN,
        "fetch": fetch,
    }
    args.update(over)
    return DopplerSink(**args)


def no_leak(*texts: str) -> None:
    for text in texts:
        for secret in SECRETS:
            assert secret not in text


# ---- the sink itself -------------------------------------------------------


def test_put_stores_then_verifies_and_returns_none(doppler: FakeDoppler) -> None:
    sink = make_sink(doppler)
    sink.put({"A_TOKEN": ACCESS, "B_TOKEN": REFRESH})
    assert doppler.store[(PROJECT, CONFIG, "A_TOKEN")] == ACCESS
    assert doppler.store[(PROJECT, CONFIG, "B_TOKEN")] == REFRESH
    methods = [r[0] for r in doppler.requests]
    assert methods == ["POST", "GET", "GET"]  # store first, then read back
    auth = doppler.requests[0][2].get("Authorization")
    assert auth == f"Bearer {API_TOKEN}"


def test_no_name_is_hard_coded_the_names_are_injected(doppler: FakeDoppler) -> None:
    make_sink(doppler, project="other-proj", config="stg").put({"X_TOKEN": ACCESS})
    assert ("other-proj", "stg", "X_TOKEN") in doppler.store


def test_value_never_appears_in_output_logs_exceptions_or_repr(
    doppler: FakeDoppler,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    sink = make_sink(doppler)
    sink.put({"A_TOKEN": ACCESS})
    no_leak(repr(sink), str(sink))
    for mode in ("mutate", "fail500", "drop", "restricted", "no_read"):
        doppler.mode = mode
        with pytest.raises(SinkError) as caught:
            sink.put({"A_TOKEN": ACCESS})
        no_leak(str(caught.value), repr(caught.value), repr(caught.value.args))
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ is True
    captured = capsys.readouterr()
    no_leak(captured.out, captured.err, caplog.text)


def test_read_back_mismatch_fails(doppler: FakeDoppler) -> None:
    doppler.mode = "mutate"
    with pytest.raises(SinkError) as caught:
        make_sink(doppler).put({"A_TOKEN": ACCESS})
    assert caught.value.kind == "verify_mismatch"


def test_a_write_that_did_not_land_fails(doppler: FakeDoppler) -> None:
    doppler.mode = "drop"
    with pytest.raises(SinkError) as caught:
        make_sink(doppler).put({"A_TOKEN": ACCESS})
    assert caught.value.kind == "verify_failed"


def test_store_failure_is_reported_and_not_retried(doppler: FakeDoppler) -> None:
    doppler.mode = "fail500"
    with pytest.raises(SinkError) as caught:
        make_sink(doppler).put({"A_TOKEN": ACCESS})
    assert caught.value.kind == "store_failed"
    assert [r[0] for r in doppler.requests] == ["POST"]  # no replay, no read


def test_restricted_value_cannot_be_verified_so_it_fails(
    doppler: FakeDoppler,
) -> None:
    doppler.mode = "restricted"
    with pytest.raises(SinkError) as caught:
        make_sink(doppler).put({"A_TOKEN": ACCESS})
    assert caught.value.kind == "verify_failed"


def test_bad_names_and_values_are_refused_before_any_request(
    doppler: FakeDoppler,
) -> None:
    sink = make_sink(doppler)
    for bad in (
        {"lower": ACCESS},
        {"A B": ACCESS},
        {"": ACCESS},
        {"A_TOKEN": ""},
        {"A_TOKEN": "has\nnewline"},
        {},
    ):
        with pytest.raises(SinkError) as caught:
            sink.put(bad)
        assert caught.value.kind == "bad_input"
    for field, value in (("project", "a/b"), ("config", "x y"), ("token", "")):
        with pytest.raises(SinkError):
            make_sink(doppler, **{field: value})
    assert doppler.requests == []


def test_redirect_is_not_followed(doppler: FakeDoppler) -> None:
    def redirect(request: TransportRequest) -> RawResponse:
        return RawResponse(307, {"location": "https://api.doppler.com/x"}, b"")

    fetch = partial(fetch_json, transport=redirect, sleep=lambda s: None)
    with pytest.raises(SinkError) as caught:
        make_sink(doppler, fetch=fetch).put({"A_TOKEN": ACCESS})
    assert caught.value.kind == "store_failed"


def test_guard_bites_a_leaky_exception_would_be_caught() -> None:
    with pytest.raises(AssertionError):
        no_leak(f"failed with {ACCESS}")


# ---- adapters for the three source protocols ------------------------------


def test_pair_style_returns_exactly_true_only_after_verified_store(
    doppler: FakeDoppler,
) -> None:
    adapter = PairStyleSink(make_sink(doppler))
    pair = TokenPair(ACCESS, REFRESH)
    assert adapter.store(pair, NAMES, "twitch") is True
    assert doppler.store[(PROJECT, CONFIG, NAMES.access)] == ACCESS
    assert doppler.store[(PROJECT, CONFIG, NAMES.refresh)] == REFRESH


@pytest.mark.parametrize("mode", ["mutate", "fail500", "drop", "restricted"])
def test_pair_style_returns_false_on_any_failure(
    doppler: FakeDoppler, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    doppler.mode = mode
    adapter = PairStyleSink(make_sink(doppler))
    assert adapter.store(TokenPair(ACCESS, REFRESH), NAMES, "twitch") is False
    captured = capsys.readouterr()
    no_leak(captured.out, captured.err)
    assert "secret-sink: source=twitch kind=" in captured.err


def test_pair_style_alert_prints_the_content_free_health_line(
    doppler: FakeDoppler, capsys: pytest.CaptureFixture[str]
) -> None:
    PairStyleSink(make_sink(doppler)).alert(
        HealthAlert("twitch", "refresh_needed", ("A", "B"))
    )
    assert capsys.readouterr().err == (
        "token-health: source=twitch kind=refresh_needed names=A,B\n"
    )


def test_meta_style_stores_one_token_and_raises_on_failure(
    doppler: FakeDoppler, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter = MetaStyleSink(make_sink(doppler))
    adapter.store(NewToken(ACCESS), "IG_TOKEN", "instagram", date(2026, 12, 1))
    assert doppler.store[(PROJECT, CONFIG, "IG_TOKEN")] == ACCESS
    err = capsys.readouterr().err
    assert "2026-12-01" in err and "IG_TOKEN" in err
    no_leak(err)
    doppler.mode = "mutate"
    with pytest.raises(SinkError) as caught:
        adapter.store(NewToken(ACCESS), "IG_TOKEN", "instagram", date(2026, 12, 1))
    no_leak(str(caught.value))
    adapter.alert(MetaAlert("instagram", "refresh_store_failed", ("IG_TOKEN",)))
    assert "token-health: source=instagram kind=refresh_store_failed" in (
        capsys.readouterr().err
    )


# ---- command line wiring ---------------------------------------------------

TWITCH_CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: twitch, handle: "@example", url: "https://example.com/t"}
numbers:
  sources:
    - {platform: twitch, kind: twitch, login: some_user, auto_refresh: true}
"""
OLD_ENV = {
    "TWITCH_CLIENT_ID": "SENTINEL-CID",
    "TWITCH_CLIENT_SECRET": "SENTINEL-CSECRET",
    "TWITCH_USER_ACCESS_TOKEN": "SENTINEL-ACCESS-OLD",
    "TWITCH_USER_REFRESH_TOKEN": "SENTINEL-REFRESH-OLD",
}
USERS = "https://api.twitch.tv/helix/users?login=some_user"
FOLLOWERS = "https://api.twitch.tv/helix/channels/followers?broadcaster_id=4242&first=1"


def twitch_routes() -> dict[str, Any]:
    state = {"n": 0}

    def followers(request: TransportRequest) -> RawResponse:
        # the old token is expired once; the refreshed token works
        if request.headers["Authorization"].endswith("OLD"):
            return reply({"message": "no"}, status=401)
        state["n"] += 1
        return reply({"total": 321, "data": [], "pagination": {}})

    return {
        "https://id.twitch.tv/oauth2/token": reply(
            {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 14000}
        ),
        USERS: reply({"data": [{"id": "4242", "login": "some_user"}]}),
        FOLLOWERS: followers,
    }


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "site.yaml").write_text(TWITCH_CONFIG)
    for key, value in OLD_ENV.items():
        monkeypatch.setenv(key, value)
    for key in (
        "REACH_AROUND_DOPPLER_TOKEN",
        "REACH_AROUND_DOPPLER_PROJECT",
        "REACH_AROUND_DOPPLER_CONFIG",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def refresh_args(root: Path, *extra: str) -> list[str]:
    return ["counts", "refresh", "--config", str(root / "site.yaml"), *extra]


def use(monkeypatch: pytest.MonkeyPatch, transport: Any) -> None:
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)


def sink_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REACH_AROUND_DOPPLER_TOKEN", API_TOKEN)
    monkeypatch.setenv("REACH_AROUND_DOPPLER_PROJECT", PROJECT)
    monkeypatch.setenv("REACH_AROUND_DOPPLER_CONFIG", CONFIG)


def test_default_has_no_sink_and_refresh_stays_manual(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, Any] = {}
    real = real_refresh

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(counts_cmd, "refresh", spy)
    use(monkeypatch, FakeTransport(twitch_routes()))
    # auto_refresh with no sink is refused before any request: unchanged
    assert main(refresh_args(site)) == 3
    assert seen["token_sink"] is None
    assert seen["meta_sink"] is None
    assert seen["tiktok_sink"] is None
    assert "twitch: error (refresh_needs_sink)" in capsys.readouterr().err


def test_flag_stores_the_rotated_pair_and_the_count_succeeds(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink_env(monkeypatch)
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    assert main(refresh_args(site, "--token-sink", "doppler")) == 0
    out = capsys.readouterr()
    assert "twitch: ok 321" in out.out
    no_leak(out.out, out.err)
    assert doppler.store[(PROJECT, CONFIG, "TWITCH_USER_ACCESS_TOKEN")] == ACCESS
    assert doppler.store[(PROJECT, CONFIG, "TWITCH_USER_REFRESH_TOKEN")] == REFRESH


def test_unverified_store_does_not_report_success_and_keeps_health_line(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink_env(monkeypatch)
    doppler.mode = "mutate"
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    assert main(refresh_args(site, "--token-sink", "doppler")) == 3
    out = capsys.readouterr()
    assert "twitch: ok" not in out.out
    assert "twitch: error (refresh_store_failed)" in out.err
    assert "token-health: source=twitch kind=rotated_unstored" in out.err
    no_leak(out.out, out.err)
    assert not (site / "numbers.json").exists()


def test_missing_project_or_token_is_a_usage_error_naming_only_variables(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use(monkeypatch, FakeTransport(twitch_routes()))
    assert main(refresh_args(site, "--token-sink", "doppler")) == 2
    err = capsys.readouterr().err
    assert "REACH_AROUND_DOPPLER_TOKEN" in err
    monkeypatch.setenv("REACH_AROUND_DOPPLER_TOKEN", API_TOKEN)
    assert main(refresh_args(site, "--token-sink", "doppler")) == 2
    err = capsys.readouterr().err
    assert "REACH_AROUND_DOPPLER_PROJECT" in err
    no_leak(err)


def test_argv_cannot_carry_the_api_token(
    site: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(refresh_args(site, "--token-sink", "doppler", "--doppler-token", "x"))
    assert caught.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_token_can_come_from_stdin(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink_env(monkeypatch)
    monkeypatch.delenv("REACH_AROUND_DOPPLER_TOKEN")
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(API_TOKEN + "\n"))
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    args = refresh_args(site, "--token-sink", "doppler", "--doppler-token-stdin")
    assert main(args) == 0
    assert doppler.requests[0][2]["Authorization"] == f"Bearer {API_TOKEN}"
    out = capsys.readouterr()
    no_leak(out.out, out.err)


def test_project_and_config_flags_override_the_environment(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
) -> None:
    sink_env(monkeypatch)
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    args = refresh_args(
        site,
        "--token-sink",
        "doppler",
        "--doppler-project",
        "flag-proj",
        "--doppler-config",
        "dev_flag",
    )
    assert main(args) == 0
    assert ("flag-proj", "dev_flag", "TWITCH_USER_ACCESS_TOKEN") in doppler.store
