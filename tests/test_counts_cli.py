# SPDX-License-Identifier: Apache-2.0
import json
import logging
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.commands.counts import run_refresh
from reach_around.net.egress import RawResponse

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks: []
numbers:
  sources:
    - {platform: bluesky, kind: bluesky, actor: example.bsky.social}
    - {platform: github, kind: github, user: some-user}
    - {platform: youtube, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv,
       api_key_env: YT_KEY}
    - {platform: twitch, kind: twitch, login: some_user, client_id_env: TW_ID,
       client_secret_env: TW_SECRET}
"""
BSKY = "https://public.api.bsky.app/"
GH = "https://api.github.com/users/some-user"
YT = "https://www.googleapis.com/youtube/v3/channels"
KEY = "SENTINEL-YOUTUBE-KEY"
NOW = datetime(2026, 10, 24, 9, 30, 0, tzinfo=UTC)
STAMP = "2026-10-24T09:30:00Z"
OLD_BSKY = {"count": 100, "as_of": "2026-10-01T00:00:00Z"}
OLD_TIKTOK = {"count": 555, "as_of": "2026-09-01T00:00:00Z"}
OLD = {"version": 1, "platforms": {"bluesky": OLD_BSKY, "tiktok": OLD_TIKTOK}}


def good_routes() -> dict[str, RawResponse]:
    return {
        BSKY: reply({"followersCount": 111}),
        GH: reply({"followers": 222}),
        YT: reply({"items": [{"statistics": {"subscriberCount": "333"}}]}),
        "https://id.twitch.tv/": reply({"access_token": "SENTINEL-APP-TOKEN"}),
        "https://api.twitch.tv/helix/users": reply({"data": [{"id": "4242"}]}),
        "https://api.twitch.tv/helix/channels/followers": reply(
            {"message": "x"}, status=401
        ),
    }


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("YT_KEY", KEY)
    monkeypatch.setenv("TW_ID", "SENTINEL-CLIENT-ID")
    monkeypatch.setenv("TW_SECRET", "SENTINEL-CLIENT-SECRET")
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    return tmp_path


def use(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport) -> None:
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: transport)


def out_files(root: Path) -> tuple[Path, Path]:
    return root / "numbers.json", root / "numbers.status.json"


def refresh_args(root: Path, *extra: str) -> list[str]:
    return ["counts", "refresh", "--config", str(root / "site.yaml"), *extra]


def test_success_writes_validated_numbers_and_status(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, FakeTransport(good_routes()))
    assert main(refresh_args(setup)) == 0  # twitch is skipped, not failed
    numbers, status = out_files(setup)
    data = json.loads(numbers.read_text())
    assert data["version"] == 1
    assert data["platforms"]["bluesky"]["count"] == 111
    assert data["platforms"]["github"]["count"] == 222
    assert data["platforms"]["youtube"]["count"] == 333
    assert "twitch" not in data["platforms"]
    for entry in data["platforms"].values():
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry["as_of"])
    assert stat.S_IMODE(numbers.stat().st_mode) == 0o600
    assert stat.S_IMODE(status.stat().st_mode) == 0o600
    report = json.loads(status.read_text())["sources"]
    assert report["bluesky"]["status"] == "ok"
    assert report["twitch"]["status"] == "skipped"
    assert report["twitch"]["detail"] == "needs_user_token"
    out = capsys.readouterr().out
    assert "bluesky: ok 111" in out and "twitch: skipped (needs_user_token)" in out


def test_as_of_is_the_injected_run_time(setup: Path) -> None:
    transport = FakeTransport(good_routes())
    code = run_refresh(
        setup / "site.yaml",
        None,
        None,
        False,
        env={"YT_KEY": KEY},
        transport=transport,
        now=lambda: NOW,
    )
    assert code == 3  # twitch has no credentials in this env: a failure
    data = json.loads(out_files(setup)[0].read_text())
    assert {e["as_of"] for e in data["platforms"].values()} == {STAMP}


def test_failure_keeps_the_previous_number_byte_for_byte_and_never_writes_zero(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    numbers, status = out_files(setup)
    numbers.write_text(json.dumps(OLD, indent=2) + "\n")
    before = numbers.read_bytes()
    routes = good_routes()
    routes[BSKY] = reply({"message": "RESPONSE-BODY-SHOULD-NOT-LEAK"}, status=500)
    routes[GH] = reply({"followers": -4})
    routes[YT] = reply({"items": []})
    transport = FakeTransport(routes)
    use(monkeypatch, transport)
    assert main(refresh_args(setup, "--only", "bluesky", "github", "youtube")) == 3
    assert numbers.read_bytes() == before  # nothing succeeded: file not even rewritten
    report = json.loads(status.read_text())["sources"]
    assert report["bluesky"]["detail"] == "server_error"
    assert report["github"]["detail"] == "bad_count"
    assert report["youtube"]["detail"] == "missing_field"
    assert set(report["bluesky"]) == {"status", "detail", "at"}
    assert "RESPONSE-BODY" not in status.read_text()
    captured = capsys.readouterr()
    assert "RESPONSE-BODY" not in captured.out + captured.err
    assert "bluesky: error (server_error)" in captured.err


def test_partial_failure_updates_the_rest_and_preserves_manual_platforms(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    numbers, _ = out_files(setup)
    numbers.write_text(json.dumps(OLD, indent=2) + "\n")
    routes = good_routes()
    routes[BSKY] = reply({}, status=404)
    use(monkeypatch, FakeTransport(routes))
    assert main(refresh_args(setup, "--only", "bluesky", "github")) == 3
    data = json.loads(numbers.read_text())["platforms"]
    assert data["bluesky"] == OLD_BSKY  # kept, old date and all
    assert data["tiktok"] == OLD_TIKTOK  # manual value untouched
    assert data["github"]["count"] == 222


def test_status_for_sources_not_run_is_kept(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeTransport(good_routes()))
    assert main(refresh_args(setup)) == 0
    first = json.loads(out_files(setup)[1].read_text())["sources"]["github"]
    assert main(refresh_args(setup, "--only", "bluesky")) == 0
    again = json.loads(out_files(setup)[1].read_text())["sources"]
    assert again["github"] == first
    assert set(again) == {"bluesky", "github", "youtube", "twitch"}


def test_only_limits_the_requests(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport(good_routes())
    use(monkeypatch, transport)
    assert main(refresh_args(setup, "--only", "github")) == 0
    assert transport.urls == [GH]
    data = json.loads(out_files(setup)[0].read_text())["platforms"]
    assert list(data) == ["github"]


def test_only_unknown_slug_is_a_usage_error(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport(good_routes())
    use(monkeypatch, transport)
    assert main(refresh_args(setup, "--only", "nope")) == 2
    assert "nope" in capsys.readouterr().err
    assert transport.calls == [] and not out_files(setup)[0].exists()


def test_exit_codes_for_usage_and_config_errors(
    setup: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["counts", "refresh", "--config", str(setup / "missing.yaml")]) == 2
    bare = setup / "bare.yaml"
    bare.write_text(CONFIG.split("numbers:")[0], encoding="utf-8")
    assert main(["counts", "refresh", "--config", str(bare)]) == 2
    assert "numbers.sources" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:
        main(["counts", "refresh"])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught2:
        main(["counts"])
    assert caught2.value.code == 2


def test_out_must_not_be_the_config_or_a_directory_or_in_a_missing_dir(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = setup / "site.yaml"
    assert main(refresh_args(setup, "--out", str(config))) == 2
    assert "--out is the config file" in capsys.readouterr().err
    assert main(refresh_args(setup, "--out", str(setup))) == 2
    assert main(refresh_args(setup, "--out", str(setup / "nodir" / "n.json"))) == 2
    assert config.read_text() == CONFIG


def test_corrupt_existing_numbers_file_is_never_overwritten(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    numbers, status = out_files(setup)
    numbers.write_text('{"version": 1, "platforms": {"tiktok": {"count": -5}}}')
    before = numbers.read_bytes()
    transport = FakeTransport(good_routes())
    use(monkeypatch, transport)
    assert main(refresh_args(setup)) == 2
    assert numbers.read_bytes() == before
    assert transport.calls == [] and not status.exists()


def test_custom_out_path_names_the_status_file_beside_it(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeTransport(good_routes()))
    target = setup / "data" / "follower-counts.json"
    target.parent.mkdir()
    assert main(refresh_args(setup, "--out", str(target), "--only", "github")) == 0
    assert (
        target.is_file() and (target.parent / "follower-counts.status.json").is_file()
    )


def test_all_failing_creates_no_numbers_file(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, FakeTransport({}))  # every route 404s
    assert main(refresh_args(setup, "--only", "bluesky", "github")) == 3
    numbers, status = out_files(setup)
    assert not numbers.exists() and status.is_file()


def test_write_failure_exits_one(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, FakeTransport(good_routes()))

    def boom(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    assert main(refresh_args(setup, "--only", "github")) == 1
    assert "cannot write" in capsys.readouterr().err
    assert not out_files(setup)[0].exists()
    assert [p.name for p in setup.iterdir() if p.name.endswith(".tmp")] == []


def test_dry_run_prints_hosts_and_kinds_and_touches_nothing(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport(good_routes())
    use(monkeypatch, transport)
    assert main(refresh_args(setup, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "bluesky: bluesky from public.api.bsky.app" in out
    assert "youtube: youtube from www.googleapis.com" in out
    assert "twitch: twitch from" in out
    assert "SENTINEL" not in out and KEY not in out
    assert transport.calls == []
    assert sorted(p.name for p in setup.iterdir()) == ["site.yaml"]


def test_dry_run_only_filters(setup: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(refresh_args(setup, "--dry-run", "--only", "github")) == 0
    out = capsys.readouterr().out
    assert "github" in out and "bluesky" not in out


def test_secrets_never_reach_output_logs_files_or_exceptions(
    setup: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    echo = (
        "echo " + KEY + " SENTINEL-CLIENT-SECRET SENTINEL-APP-TOKEN SENTINEL-CLIENT-ID"
    )
    routes = good_routes()
    routes[YT] = reply({"error": {"message": echo}}, status=401)
    routes["https://api.twitch.tv/helix/users"] = reply({"message": echo}, status=500)
    use(monkeypatch, FakeTransport(routes))
    assert main(refresh_args(setup)) == 3
    captured = capsys.readouterr()
    haystacks = [
        captured.out,
        captured.err,
        caplog.text,
        out_files(setup)[0].read_text(),
        out_files(setup)[1].read_text(),
    ]
    for hay in haystacks:
        assert "SENTINEL" not in hay
    # and nothing else was left in the directory that could hold one
    for path in setup.iterdir():
        if path.is_file():
            assert "SENTINEL" not in path.read_text()


def test_unexpected_exception_is_reported_by_class_only(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(request: object) -> object:
        raise RuntimeError("SENTINEL-IN-EXCEPTION-TEXT")

    monkeypatch.setattr(counts_cmd, "default_transport", lambda: explode)
    assert main(refresh_args(setup, "--only", "github")) == 3
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert (
        "SENTINEL" not in captured.out + captured.err + out_files(setup)[1].read_text()
    )


def test_a_blocked_address_is_a_failed_source(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reach_around.net.egress import EgressBlocked

    def blocked(request: object) -> object:
        raise EgressBlocked("non_public_address", "example.social")

    monkeypatch.setattr(counts_cmd, "default_transport", lambda: blocked)
    assert main(refresh_args(setup, "--only", "github")) == 3
    detail = json.loads(out_files(setup)[1].read_text())["sources"]["github"]["detail"]
    assert detail == "egress_non_public_address"
