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
import reach_around.counts.secret_sink as secret_sink
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

_REAL_PASSWD_HOME = secret_sink._passwd_home

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


# ---- round 1 hardening: destination, single writer, env token --------------


def test_no_constructor_argument_can_change_the_destination(
    doppler: FakeDoppler,
) -> None:
    import inspect

    params = inspect.signature(DopplerSink.__init__).parameters
    assert set(params) == {"self", "project", "config", "token", "fetch"}
    for extra in ("base_url", "host", "url", "base"):
        with pytest.raises(TypeError):
            make_sink(doppler, **{extra: "http://127.0.0.1:1"})
    seen: list[tuple[str, int]] = []
    inner = doppler.transport()

    def record(request: TransportRequest) -> RawResponse:
        seen.append((request.host, request.port))
        answer: RawResponse = inner(request)
        return answer

    fetch = partial(fetch_json, transport=record, sleep=lambda s: None)
    DopplerSink(PROJECT, CONFIG, API_TOKEN, fetch).put({"A_B": "value-1"})
    assert seen and set(seen) == {("api.doppler.com", 443)}


def test_cli_exposes_no_destination_option(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for flag in ("--doppler-base-url", "--doppler-url", "--doppler-host"):
        with pytest.raises(SystemExit) as caught:
            main(refresh_args(site, "--token-sink", "doppler", flag, "http://x"))
        assert caught.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err
    sink_env(monkeypatch)
    monkeypatch.setenv("REACH_AROUND_DOPPLER_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("REACH_AROUND_DOPPLER_URL", "http://127.0.0.1:1")
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    assert main(refresh_args(site, "--token-sink", "doppler")) == 0
    assert doppler.requests


def test_build_sinks_takes_no_destination() -> None:
    import inspect

    assert set(inspect.signature(counts_cmd.build_sinks).parameters) == {
        "spec",
        "transport",
    }


def test_api_token_leaves_the_environment_before_any_network_work(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
) -> None:
    import os

    sink_env(monkeypatch)
    inner = doppler.transport(FakeTransport(twitch_routes()))
    seen: list[bool] = []

    def watch(request: TransportRequest) -> RawResponse:
        seen.append(
            "REACH_AROUND_DOPPLER_TOKEN" in os.environ
            or API_TOKEN in os.environ.values()
        )
        answer: RawResponse = inner(request)
        return answer

    use(monkeypatch, watch)
    assert main(refresh_args(site, "--token-sink", "doppler")) == 0
    assert seen and not any(seen)
    assert "REACH_AROUND_DOPPLER_TOKEN" not in os.environ
    assert API_TOKEN not in os.environ.values()
    assert doppler.requests[0][2]["Authorization"] == f"Bearer {API_TOKEN}"


def test_api_token_leaves_the_environment_even_when_setup_fails(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setenv("REACH_AROUND_DOPPLER_TOKEN", API_TOKEN)
    use(monkeypatch, FakeTransport(twitch_routes()))
    assert main(refresh_args(site, "--token-sink", "doppler")) == 2
    assert "REACH_AROUND_DOPPLER_TOKEN" not in os.environ


def test_stdin_run_also_clears_a_stray_env_token(
    site: Path, monkeypatch: pytest.MonkeyPatch, doppler: FakeDoppler
) -> None:
    import io
    import os

    sink_env(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO(API_TOKEN + "\n"))
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    args = refresh_args(site, "--token-sink", "doppler", "--doppler-token-stdin")
    assert main(args) == 0
    assert "REACH_AROUND_DOPPLER_TOKEN" not in os.environ


@pytest.fixture(autouse=True)
def lock_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the lock root at a scratch home: no test writes under the real one.

    The seam is a module function; the CLI and the environment cannot reach it.
    """
    home = tmp_path_factory.mktemp("lockhome")
    monkeypatch.setattr(secret_sink, "_passwd_home", lambda: str(home))
    return home


def _lock_dir(home: Path) -> Path:
    return home / ".local" / "state" / "reach-around"


def test_scope_lock_admits_one_writer_and_holds_only_a_pid(lock_home: Path) -> None:
    from reach_around.counts.secret_sink import ScopeBusy, scope_lock

    with scope_lock(PROJECT, CONFIG):
        files = [p for p in lock_home.rglob("*") if p.is_file()]
        assert len(files) == 1
        assert files[0].read_text().strip().isdigit()
        assert PROJECT not in files[0].name
        assert files[0].stat().st_mode & 0o777 == 0o600
        with pytest.raises(ScopeBusy):
            with scope_lock(PROJECT, CONFIG):
                raise AssertionError("second writer must not get in")
        with scope_lock(PROJECT, "dev_other"):  # another config, another scope
            pass
    with scope_lock(PROJECT, CONFIG):  # free again after release
        pass


def test_lock_path_ignores_home_and_xdg_state_home(
    lock_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = secret_sink.lock_file(PROJECT, CONFIG)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "x"))
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert secret_sink.lock_file(PROJECT, CONFIG) == before
    assert before.parent == _lock_dir(lock_home)
    assert not (tmp_path / "x").exists() and not (tmp_path / "h").exists()


def test_lock_root_comes_from_the_passwd_database() -> None:
    import os
    import pwd

    assert _REAL_PASSWD_HOME() == pwd.getpwuid(os.geteuid()).pw_dir


def test_hard_linked_lock_file_is_refused_and_not_modified(lock_home: Path) -> None:
    import os

    folder = _lock_dir(lock_home)
    folder.mkdir(parents=True, mode=0o700)
    victim = lock_home / "victim"
    victim.write_text("keep me")
    victim.chmod(0o600)
    os.link(victim, secret_sink.lock_file(PROJECT, CONFIG))
    with pytest.raises(SinkError) as seen:
        with secret_sink.scope_lock(PROJECT, CONFIG):
            raise AssertionError("must not get in")
    assert seen.value.kind == "unsafe_state"
    assert victim.read_text() == "keep me"


def test_wide_mode_lock_file_is_refused(lock_home: Path) -> None:
    folder = _lock_dir(lock_home)
    folder.mkdir(parents=True, mode=0o700)
    path = secret_sink.lock_file(PROJECT, CONFIG)
    path.write_text("")
    path.chmod(0o644)
    with pytest.raises(SinkError) as seen:
        with secret_sink.scope_lock(PROJECT, CONFIG):
            raise AssertionError("must not get in")
    assert seen.value.kind == "unsafe_state"


def test_symlinked_state_directory_is_refused(lock_home: Path) -> None:
    other = lock_home / "elsewhere"
    other.mkdir(mode=0o700)
    folder = _lock_dir(lock_home)
    folder.parent.mkdir(parents=True)
    folder.symlink_to(other)
    with pytest.raises(SinkError) as seen:
        with secret_sink.scope_lock(PROJECT, CONFIG):
            raise AssertionError("must not get in")
    assert seen.value.kind == "unsafe_state"
    assert list(other.iterdir()) == []


def test_wrong_mode_state_directory_is_refused(lock_home: Path) -> None:
    folder = _lock_dir(lock_home)
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    with pytest.raises(SinkError) as seen:
        with secret_sink.scope_lock(PROJECT, CONFIG):
            raise AssertionError("must not get in")
    assert seen.value.kind == "unsafe_state"
    assert list(folder.iterdir()) == []


def test_state_directory_owned_by_someone_else_is_refused(
    lock_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    _lock_dir(lock_home).mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    with pytest.raises(SinkError) as seen:
        with secret_sink.scope_lock(PROJECT, CONFIG):
            raise AssertionError("must not get in")
    assert seen.value.kind == "unsafe_state"


def test_unsafe_lock_state_gets_its_own_fixed_message(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
    lock_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = _lock_dir(lock_home)
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    sink_env(monkeypatch)
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    code = main(refresh_args(site, "--token-sink", "doppler"))
    err = capsys.readouterr().err
    assert code != 0
    assert "output folder" not in err
    assert "unsafe_state" in err
    assert str(lock_home) not in err
    assert doppler.requests == []
    no_leak(err)


def test_refresh_refuses_a_second_writer_through_the_real_path(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The first run holds both locks; the second must fail fast, before any request."""
    from reach_around.counts.files import FileLock

    real_lock = counts_cmd.FileLock
    monkeypatch.setattr(
        counts_cmd,
        "FileLock",
        lambda path, wait=120.0: real_lock(path, wait=0.2),
    )
    assert FileLock is real_lock
    inner = doppler.transport(FakeTransport(twitch_routes()))
    state: dict[str, Any] = {"nested": False}

    def outer(request: TransportRequest) -> RawResponse:
        if not state["nested"]:
            state["nested"] = True
            sink_env(monkeypatch)
            seen = len(doppler.requests)
            state["code"] = main(refresh_args(site, "--token-sink", "doppler"))
            state["extra"] = len(doppler.requests) - seen
            state["fetched"] = state.get("fetched", 0)
        answer: RawResponse = inner(request)
        return answer

    use(monkeypatch, outer)
    sink_env(monkeypatch)
    assert main(refresh_args(site, "--token-sink", "doppler")) == 0
    err = capsys.readouterr().err
    assert state["code"] == 3
    assert state["extra"] == 0
    assert "another writer" in err
    assert "another counts refresh" not in err


def test_scope_lock_is_taken_before_the_output_lock(
    site: Path,
    monkeypatch: pytest.MonkeyPatch,
    doppler: FakeDoppler,
) -> None:
    order: list[str] = []
    real_lock = counts_cmd.FileLock
    real_scope = counts_cmd.scope_lock

    class Spy:
        def __init__(self, path: Path, wait: float = 120.0) -> None:
            self._inner = real_lock(path, wait=wait)

        def __enter__(self) -> None:
            order.append("output")
            self._inner.__enter__()

        def __exit__(self, *exc: object) -> None:
            self._inner.__exit__(*exc)

    def scoped(project: str, config: str) -> Any:
        order.append("scope")
        return real_scope(project, config)

    monkeypatch.setattr(counts_cmd, "FileLock", Spy)
    monkeypatch.setattr(counts_cmd, "scope_lock", scoped)
    sink_env(monkeypatch)
    use(monkeypatch, doppler.transport(FakeTransport(twitch_routes())))
    assert main(refresh_args(site, "--token-sink", "doppler")) == 0
    assert order == ["scope", "output"]


def test_bad_option_leaves_no_api_token_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv("REACH_AROUND_DOPPLER_TOKEN", API_TOKEN)
    with pytest.raises(SystemExit):
        main(["counts", "refresh", "--no-such-option"])
    assert "REACH_AROUND_DOPPLER_TOKEN" not in os.environ
    assert API_TOKEN not in os.environ.values()


def test_a_run_without_the_sink_drops_the_token_and_still_works(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setenv("REACH_AROUND_DOPPLER_TOKEN", API_TOKEN)
    assert main(refresh_args(site, "--dry-run")) == 0
    assert "REACH_AROUND_DOPPLER_TOKEN" not in os.environ
    assert main(["--version"]) == 0


def test_a_help_exit_also_scrubs_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("REACH_AROUND_DOPPLER_TOKEN", API_TOKEN)
    with pytest.raises(SystemExit):
        main(["counts", "refresh", "--help"])
    assert "REACH_AROUND_DOPPLER_TOKEN" not in os.environ
