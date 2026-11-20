# SPDX-License-Identifier: Apache-2.0
"""Token health report (#390): fixed vocabulary, boundary days, every platform
state, unknown expiry, and no token value anywhere."""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config.numbers_sources import (
    InstagramSource,
    ThreadsSource,
    TiktokSource,
    TwitchSource,
    YoutubeSource,
)
from reach_around.counts import meta_user, providers, tiktok_user, twitch_user
from reach_around.counts import token_health as th

TODAY = date(2026, 11, 20)
CANARY = "SENTINEL-TOKEN-VALUE-do-not-print"


def ig(expires: date | None, within: int = 10) -> InstagramSource:
    return InstagramSource(
        platform="instagram", kind="instagram", token_expires=expires,
        refresh_within_days=within,
    )  # fmt: skip


def threads(expires: date | None) -> ThreadsSource:
    return ThreadsSource(
        platform="threads", kind="threads", user_id="12345", token_expires=expires
    )


def yt() -> YoutubeSource:
    return YoutubeSource(
        platform="youtube", kind="youtube", channel_id="UCabcdefghijklmnopqrstuv"
    )


def tw() -> TwitchSource:
    return TwitchSource(platform="twitch", kind="twitch", login="some_user")


def tt() -> TiktokSource:
    return TiktokSource(platform="tiktok", kind="tiktok")


def state_of(
    source: object, status: dict[str, dict[str, str]] | None = None
) -> th.TokenHealth:
    (item,) = th.assess([source], status or {}, TODAY)
    return item


@pytest.mark.parametrize(
    ("delta", "state"),
    [(11, "ok"), (10, "expiring"), (1, "expiring"), (0, "expiring"), (-1, "expired")],
)
def test_meta_boundary_days(delta: int, state: str) -> None:
    expires = date.fromordinal(TODAY.toordinal() + delta)
    item = state_of(ig(expires))
    assert (item.state, item.days_left) == (state, delta)


def test_meta_window_follows_refresh_within_days() -> None:
    expires = date.fromordinal(TODAY.toordinal() + 5)
    assert state_of(ig(expires, within=4)).state == "ok"
    assert state_of(ig(expires, within=5)).state == "expiring"


def test_unknown_expiry_is_ok_with_unknown_days() -> None:
    item = state_of(threads(None))
    assert (item.state, item.days_left) == ("ok", None)
    assert "days_left=unknown" in item.line()


@pytest.mark.parametrize(
    ("source", "word", "state"),
    [
        (tw(), "token_expired", "expired"),
        (tw(), "token_rejected", "rejected"),
        (tw(), "refresh_failed", "rejected"),
        (tt(), "token_expired", "expired"),
        (tt(), "scope_missing", "rejected"),
        (ig(None), "token_rejected", "rejected"),
        (yt(), "http_400", "rejected"),
        (yt(), "http_401", "rejected"),
    ],
)
def test_error_words_map_to_states(source: object, word: str, state: str) -> None:
    status = {source.platform: {"status": "error", "detail": word}}  # type: ignore[attr-defined]
    assert state_of(source, status).state == state


@pytest.mark.parametrize("word", ["http_403", "http_429", "timeout", "missing_field"])
def test_other_youtube_failures_are_not_a_token_verdict(word: str) -> None:
    assert (
        state_of(yt(), {"youtube": {"status": "error", "detail": word}}).state == "ok"
    )


def test_youtube_words_do_not_mark_other_platforms() -> None:
    status = {"twitch": {"status": "error", "detail": "http_401"}}
    assert state_of(tw(), status).state == "ok"


def test_a_word_wins_over_the_date_and_only_when_the_source_errored() -> None:
    far = date.fromordinal(TODAY.toordinal() + 40)
    bad = {"instagram": {"status": "error", "detail": "token_rejected"}}
    assert state_of(ig(far), bad).state == "rejected"
    ok = {"instagram": {"status": "ok", "detail": "token_rejected"}}
    assert state_of(ig(far), ok).state == "ok"


@pytest.mark.parametrize("delta", [40, 5, -3])  # ok, expiring, expired dates
def test_a_rejection_word_beats_every_date(delta: int) -> None:
    expires = date.fromordinal(TODAY.toordinal() + delta)
    bad = {"instagram": {"status": "error", "detail": "token_rejected"}}
    assert state_of(ig(expires), bad).state == "rejected"


@pytest.mark.parametrize("delta", [40, 5])
def test_an_expired_word_beats_an_ok_or_expiring_date(delta: int) -> None:
    expires = date.fromordinal(TODAY.toordinal() + delta)
    bad = {"instagram": {"status": "error", "detail": "token_expired"}}
    assert state_of(ig(expires), bad).state == "expired"


def test_sources_without_a_token_are_left_out() -> None:
    class Other:
        kind = "bluesky"
        platform = "bluesky"

    assert th.assess([Other()], {}, TODAY) == []


def test_exit_codes() -> None:
    def mk(state: str) -> th.TokenHealth:
        return th.TokenHealth("x", "twitch", state)

    assert th.exit_code([mk("ok")], 3, 4) == 0
    assert th.exit_code([mk("ok"), mk("expiring")], 3, 4) == 4
    assert th.exit_code([mk("expiring"), mk("rejected")], 3, 4) == 3
    assert th.exit_code([mk("expired")], 3, 4) == 3
    assert th.exit_code([], 3, 4) == 0


def test_renderers_use_only_the_fixed_vocabulary() -> None:
    report = [
        th.TokenHealth("instagram", "instagram", "expiring", 3),
        th.TokenHealth("twitch", "twitch", "rejected", None),
        th.TokenHealth("youtube", "youtube", "ok", None),
    ]
    assert th.render_text(report).splitlines() == [
        "token-health: source=instagram state=expiring days_left=3",
        "token-health: source=twitch state=rejected days_left=unknown",
        "token-health: source=youtube state=ok days_left=unknown",
    ]
    gh = th.render_github(report).splitlines()
    assert gh[0].startswith("::warning title=Token health::instagram token expiring")
    assert gh[1].startswith("::error title=Token health::twitch token rejected")
    assert len(gh) == 2  # ok is silent
    md = th.render_markdown(report)
    assert (
        "| instagram | expiring | 3 |" in md and "| twitch | rejected | unknown |" in md
    )
    data = json.loads(th.render_json(report))
    assert data["tokens"][0] == {
        "source": "instagram",
        "state": "expiring",
        "days_left": 3,
    }
    assert {t["state"] for t in data["tokens"]} <= set(th.STATES)


def test_repr_and_lines_hold_no_value() -> None:
    item = state_of(tw(), {"twitch": {"status": "error", "detail": CANARY}})
    for text in (repr(item), item.line(), th.render_markdown([item]),
                 th.render_github([item]), th.render_json([item])):  # fmt: skip
        assert CANARY not in text
    assert item.state == "ok"  # an unknown word is never echoed or promoted


# ---- the commands -------------------------------------------------------

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
          - {platform: youtube, handle: "@example", url: "https://example.com/y"}
numbers:
  sources:
    - {platform: instagram, kind: instagram, token_expires: "%s"}
    - {platform: youtube, kind: youtube, channel_id: UCabcdefghijklmnopqrstuv}
"""


def write_config(root: Path, expires: str) -> Path:
    path = root / "site.yaml"
    path.write_text(CONFIG % expires, encoding="utf-8")
    return path


def write_status(root: Path, sources: dict[str, dict[str, str]]) -> None:
    (root / "numbers.status.json").write_text(
        json.dumps({"version": 1, "committed": True, "sources": sources})
    )


def run_tokens(root: Path, *extra: str, today: date = TODAY) -> int:
    return counts_cmd.run_tokens(
        root / "site.yaml", None, extra[0] if extra else "text", today=today
    )


def test_tokens_command_exit_codes_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(tmp_path, "2027-01-15")  # 56 days out
    assert run_tokens(tmp_path) == 0
    assert "source=instagram state=ok days_left=56" in capsys.readouterr().out

    write_config(tmp_path, "2026-11-25")  # 5 days
    assert run_tokens(tmp_path) == counts_cmd.EXIT_TOKEN_WARNING == 4
    assert "state=expiring days_left=5" in capsys.readouterr().out

    write_status(
        tmp_path, {"youtube": {"status": "error", "at": "x", "detail": "http_400"}}
    )
    assert run_tokens(tmp_path) == counts_cmd.EXIT_SOURCE_FAILED == 3
    assert "source=youtube state=rejected" in capsys.readouterr().out


def test_tokens_command_formats_and_missing_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(tmp_path, "2026-11-25")
    assert run_tokens(tmp_path, "github") == 4
    assert capsys.readouterr().out.startswith("::warning title=Token health::")
    run_tokens(tmp_path, "markdown")
    assert "### Token health" in capsys.readouterr().out
    run_tokens(tmp_path, "json")
    assert json.loads(capsys.readouterr().out)["tokens"][0]["state"] == "expiring"


def test_tokens_command_usage_error(tmp_path: Path) -> None:
    assert counts_cmd.run_tokens(tmp_path / "missing.yaml", None, "text") == 2
    assert main(["counts", "tokens", "--config", str(tmp_path / "missing.yaml")]) == 2


def test_the_config_refuses_an_implausible_expiry_so_tokens_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(tmp_path, "2099-01-01")
    # an implausible far date is refused by the config (120-day rule)
    assert main(["counts", "tokens", "--config", str(tmp_path / "site.yaml")]) == 2


def test_refresh_prints_health_lines_and_expiring_does_not_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    today = datetime.now(UTC).date()
    soon = date.fromordinal(today.toordinal() + 3).isoformat()
    write_config(tmp_path, soon)
    monkeypatch.setenv("YOUTUBE_API_KEY", CANARY)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", CANARY)
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    routes = {
        "https://www.googleapis.com/youtube/v3/channels": reply(
            {"items": [{"statistics": {"subscriberCount": "5"}}]}
        ),
        "https://graph.instagram.com/": reply({"followers_count": 7}),
    }
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: FakeTransport(routes))
    code = main(["counts", "refresh", "--config", str(tmp_path / "site.yaml")])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "token-health: source=instagram state=expiring days_left=3" in text
    assert "token-health: source=youtube state=ok" in text
    assert CANARY not in text
    assert code == 0  # a warning alone never turns a run red


def test_refresh_reports_a_rejected_key_and_fails_that_source_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    far = date.fromordinal(datetime.now(UTC).date().toordinal() + 40).isoformat()
    write_config(tmp_path, far)
    monkeypatch.setenv("YOUTUBE_API_KEY", CANARY)
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    routes = {
        "https://www.googleapis.com/youtube/v3/channels": reply(
            {"error": {"message": CANARY}}, status=400
        )
    }
    monkeypatch.setattr(counts_cmd, "default_transport", lambda: FakeTransport(routes))
    code = main(["counts", "refresh", "--config", str(tmp_path / "site.yaml")])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "token-health: source=youtube state=rejected" in text
    assert CANARY not in text
    assert code == 3


# ---- every word a collector can end a source with ------------------------


def collector_words() -> set[str]:
    """The fixed words the collectors raise, read from their own source."""
    words: set[str] = set()
    for module in (meta_user, twitch_user, tiktok_user, providers):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.endswith("Error")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                words.add(node.args[0].value)
    return words


def unmapped(words: set[str]) -> set[str]:
    return words - set(th.WORD_STATES) - th.NOT_TOKEN_WORDS


def test_every_collector_word_is_classified() -> None:
    words = collector_words()
    # the guard collected something real, and it would notice a new word
    assert {
        "token_expired",
        "refresh_store_failed",
        "refresh_response_invalid",
    } <= words
    assert len(words) > 20
    assert unmapped(words | {"brand_new_failure"}) == {"brand_new_failure"}
    assert unmapped(words) == set(), "classify the new word in token_health.py"
    assert not set(th.WORD_STATES) & th.NOT_TOKEN_WORDS


@pytest.mark.parametrize("word", sorted(th.WORD_STATES))
@pytest.mark.parametrize(
    "source",
    [ig(None), threads(None), tw(), tt(), yt()],
    ids=lambda s: s.platform,
)
def test_a_failed_source_with_a_token_word_is_never_ok(
    word: str, source: InstagramSource | ThreadsSource | TwitchSource
) -> None:
    status = {source.platform: {"status": "error", "detail": word}}
    assert state_of(source, status).state == th.WORD_STATES[word] != th.OK
