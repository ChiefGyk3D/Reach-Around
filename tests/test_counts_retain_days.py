# SPDX-License-Identifier: Apache-2.0
"""Per-platform ``numbers.retain_days`` and the retention warning (#620).

Synthetic data only (example.com, no real handles). The warning is a count, never
a value, and it changes nothing that is stored or deleted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import reach_around.commands.counts as counts_cmd
from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.config import SiteConfig
from reach_around.config.numbers_sources import NumbersConfig
from reach_around.counts.history import Record, append_records, read_history
from reach_around.counts.retention import RetentionPolicy, retention_warnings

TODAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
BSKY = "https://public.api.bsky.app/"


def record(
    platform: str, days_ago: int, *, source: str = "api", metric: str = "followers"
) -> Record:
    when = TODAY - timedelta(days=days_ago)
    return Record(
        platform=platform,
        metric=metric,
        value=987654,
        as_of_utc=f"{when.isoformat()}T00:00:00Z",
        source=source,
        run_id="r1",
    )


def policy(**days: int) -> RetentionPolicy:
    return RetentionPolicy(retain_days=days, warn_days=7)


# --- the pure check -----------------------------------------------------------


def test_a_row_inside_the_margin_is_counted_as_expiring() -> None:
    out = retention_warnings([record("twitch", 25)], policy(twitch=30), TODAY)
    assert [(w.platform, w.expiring, w.past, w.retain_days) for w in out] == [
        ("twitch", 1, 0, 30)
    ]


def test_the_margin_edge_is_inclusive_and_one_day_before_is_quiet() -> None:
    # 30 - 7 = 23 days old is inside; 22 is not
    assert retention_warnings([record("twitch", 23)], policy(twitch=30), TODAY)
    assert not retention_warnings([record("twitch", 22)], policy(twitch=30), TODAY)


def test_the_window_edge_is_expiring_and_one_day_over_is_past() -> None:
    (edge,) = retention_warnings([record("twitch", 30)], policy(twitch=30), TODAY)
    assert (edge.expiring, edge.past) == (1, 0)
    (over,) = retention_warnings([record("twitch", 31)], policy(twitch=30), TODAY)
    assert (over.expiring, over.past) == (0, 1)


def test_only_api_rows_are_counted() -> None:
    rows = [
        record("twitch", 28, source="hand_kept"),
        record("twitch", 28, source="imported", metric="views"),
    ]
    assert retention_warnings(rows, policy(twitch=30), TODAY) == []


def test_a_platform_without_retain_days_is_never_warned() -> None:
    assert retention_warnings([record("kick", 400)], policy(twitch=30), TODAY) == []


def test_each_platform_uses_its_own_window() -> None:
    rows = [record("twitch", 5), record("youtube", 5)]
    out = retention_warnings(rows, policy(twitch=30, youtube=10), TODAY)
    assert [w.platform for w in out] == ["youtube"]


def test_a_correction_is_not_counted_twice() -> None:
    fix = Record(**{**record("twitch", 25).__dict__, "type": "correction"})
    out = retention_warnings([record("twitch", 25), fix], policy(twitch=30), TODAY)
    assert out[0].expiring == 1


def test_no_policy_means_no_warning() -> None:
    assert retention_warnings([record("twitch", 99)], RetentionPolicy(), TODAY) == []


def test_the_line_is_a_count_and_never_a_value() -> None:
    (w,) = retention_warnings([record("twitch", 25)], policy(twitch=30), TODAY)
    line = w.line()
    assert line.startswith("twitch: retention warning: 1 api record(s)")
    assert "30 day" in line and "7 days" in line
    assert "987654" not in line


# --- the config ---------------------------------------------------------------


def numbers(**extra: Any) -> NumbersConfig:
    return NumbersConfig.model_validate(extra)


def test_retain_days_and_the_margin_parse_with_a_default_margin() -> None:
    config = numbers(retain_days={"twitch": 30})
    assert config.retain_days == {"twitch": 30}
    assert config.retain_warn_days == 7


@pytest.mark.parametrize(
    "bad",
    [
        {"twitch": 0},
        {"twitch": -1},
        {"twitch": 3651},
        {"twitch": "30"},
        {"twitch": True},
        {"twitch": 30.0},
        {"Twitch": 30},
        {"not a slug": 30},
        {"twitch": None},
        # the margin must be smaller than the window
        {"twitch": 7},
        {"twitch": 3},
    ],
)
def test_bad_retain_days_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        numbers(retain_days=bad)


@pytest.mark.parametrize("bad", [0, -1, 366, "7", True, 7.0])
def test_bad_margins_are_refused(bad: Any) -> None:
    with pytest.raises(ValidationError):
        numbers(retain_days={"twitch": 30}, retain_warn_days=bad)


def test_the_margin_must_stay_under_every_window() -> None:
    with pytest.raises(ValidationError):
        numbers(retain_days={"twitch": 30, "kick": 10}, retain_warn_days=10)
    numbers(retain_days={"twitch": 30, "kick": 10}, retain_warn_days=9)


def test_the_site_config_accepts_the_keys() -> None:
    SiteConfig.model_validate(
        {
            "version": 1,
            "timezone": "UTC",
            "creator": {"name": "Example"},
            "tabs": [{"id": "home", "title": "Home", "blocks": []}],
            "numbers": {"retain_days": {"youtube": 30}, "retain_warn_days": 5},
        }
    )


# --- the refresh warning ------------------------------------------------------

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: bluesky, handle: "@example", url: "https://example.com/b"}
numbers:
  history_file: history/social.jsonl
  retain_days: {bluesky: 30}
  retain_warn_days: 7
  sources:
    - {platform: bluesky, kind: bluesky, actor: example.bsky.social}
"""


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "history").mkdir()
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    monkeypatch.setattr(
        counts_cmd,
        "default_transport",
        lambda: FakeTransport({BSKY: reply({"followersCount": 111})}),
    )
    return tmp_path


def run(root: Path) -> int:
    return counts_cmd.run_refresh(
        root / "site.yaml", None, None, False, env={}, now=lambda: NOW
    )


def test_refresh_warns_before_the_window_ends(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup / "history" / "social.jsonl"
    append_records(
        path, [record("bluesky", 26), record("bluesky", 40, metric="m2")], now=NOW
    )
    assert run(setup) == 0
    err = capsys.readouterr().err
    assert (
        "bluesky: retention warning: 1 api record(s) expire within 7 days of the "
        "30 day window, 1 are past it"
    ) in err
    assert "987654" not in err


def test_refresh_is_quiet_when_nothing_is_near(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    append_records(setup / "history" / "social.jsonl", [record("bluesky", 2)], now=NOW)
    assert run(setup) == 0
    assert "retention warning" not in capsys.readouterr().err


def test_the_warning_changes_nothing_stored(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup / "history" / "social.jsonl"
    append_records(
        path, [record("bluesky", 26), record("bluesky", 90, metric="m2")], now=NOW
    )
    assert run(setup) == 0
    first = path.read_bytes()
    capsys.readouterr()
    assert run(setup) == 0  # a second run the same day writes no new row
    assert "retention warning" in capsys.readouterr().err
    assert path.read_bytes() == first
    history = read_history(path)
    assert history.tombstones == ()  # nothing was redacted
    assert len(history.records) == 3  # the two old rows and today's row


def test_no_retain_days_means_the_run_prints_no_warning(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (setup / "site.yaml").write_text(
        CONFIG.replace("  retain_days: {bluesky: 30}\n", ""), encoding="utf-8"
    )
    append_records(setup / "history" / "social.jsonl", [record("bluesky", 28)], now=NOW)
    assert run(setup) == 0
    assert "retention warning" not in capsys.readouterr().err


def test_the_cli_refuses_a_bad_retain_days(
    setup: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (setup / "site.yaml").write_text(
        CONFIG.replace("{bluesky: 30}", "{bluesky: 0}"), encoding="utf-8"
    )
    code = main(["counts", "refresh", "--config", str(setup / "site.yaml")])
    assert code == 2
