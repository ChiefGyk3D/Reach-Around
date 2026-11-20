# SPDX-License-Identifier: Apache-2.0
"""Tombstones keep the provider tag of what they replace (issue #586).

The provider tag is the only selector for private analytics records: a
tombstone carries it, redaction can select by it, and it never holds a value.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.counts.history import (
    ANALYTICS_PROVIDER,
    GENESIS,
    HistoryError,
    Record,
    append_records,
    plan_redaction,
    read_history,
    redact_records,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)
REDACT_AT = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
VALUE = 424242
NOTE = "SENTINEL-NOTE"
ERA = "SENTINEL-ERA"
DATA_API = "youtube_data_api"


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "youtube",
        "metric": "views",
        "value": 10,
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
        "api_provider": ANALYTICS_PROVIDER,
    }
    fields.update(over)
    return Record(**fields)


def add(path: Path, *records: Record) -> None:
    append_records(path, list(records), now=NOW)


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_bytes()[:-1].split(b"\n")]


def redact_all(path: Path, **plan_over: Any) -> int:
    plan = plan_redaction(read_history(path), **plan_over)
    return redact_records(
        path,
        plan,
        reason="platform_revocation",
        trigger_date="2026-10-20",
        now=REDACT_AT,
        confirm=True,
    )


def build(path: Path) -> None:
    add(path, rec(value=VALUE, era=ERA))
    add(path, rec(value=VALUE + 1, note=NOTE, type="correction"))
    add(path, rec(as_of_utc="2026-10-02T09:00:00Z", value=11, api_provider=DATA_API))


# (a) the tombstone carries the provider of what it refers to


def test_a_tombstone_carries_the_provider_of_its_record(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    assert redact_all(path, platform="youtube", provider=ANALYTICS_PROVIDER) == 1
    first, second = lines(path)
    assert first["type"] == "tombstone"
    assert first["api_provider"] == ANALYTICS_PROVIDER
    assert second["api_provider"] == DATA_API  # the other record is untouched


def test_a_plan_whose_provider_differs_from_the_file_is_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    forged = rec(api_provider=DATA_API, value=VALUE, era=ERA)  # only the tag differs
    with pytest.raises(HistoryError):
        redact_records(
            path,
            [forged],
            reason="platform_revocation",
            trigger_date="2026-10-20",
            now=REDACT_AT,
            confirm=True,
        )
    assert path.read_bytes() == before


def tomb(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "type": "tombstone",
        "platform": "youtube",
        "metric": "views",
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "reason": "platform_revocation",
        "trigger_date": "2026-10-20",
        "redacted_at_utc": "2026-11-19T12:00:00Z",
        "run_id": "redact-1",
        "api_provider": ANALYTICS_PROVIDER,
        "prev": GENESIS,
    }
    fields.update(over)
    return {k: v for k, v in fields.items() if v is not None}


def write_tomb(path: Path, obj: dict[str, Any]) -> None:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    path.write_bytes(text.encode("ascii") + b"\n")


def test_a_tombstone_line_with_a_known_provider_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    write_tomb(path, tomb())
    assert len(read_history(path).tombstones) == 1


@pytest.mark.parametrize(
    "over",
    [
        {"api_provider": "youtube-analytics"},  # not a recognized tag
        {"api_provider": ""},
        {"api_provider": 5},
        {"api_provider": ANALYTICS_PROVIDER, "source": "hand_kept"},
        {"api_provider": ANALYTICS_PROVIDER, "source": "imported"},
    ],
)
def test_a_tombstone_with_a_bad_provider_is_refused(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    path = tmp_path / "h.jsonl"
    write_tomb(path, tomb(**over))
    with pytest.raises(HistoryError):
        read_history(path)


# (b) selection is by the provider tag only


def test_the_selector_uses_the_tag_not_a_platform_name(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(platform="odd-name", value=1))  # tagged, unrelated slug
    add(
        path,
        rec(
            platform="youtube-analytics",
            metric="subscribers",
            value=2,
            api_provider=None,
        ),
    )
    add(
        path,
        rec(platform="youtube", metric="subscribers", value=3, api_provider=DATA_API),
    )
    plan = plan_redaction(read_history(path), provider=ANALYTICS_PROVIDER)
    assert [(r.platform, r.metric) for r in plan] == [("odd-name", "views")]
    other = plan_redaction(read_history(path), provider=DATA_API)
    assert [r.platform for r in other] == ["youtube"]


def test_the_selector_refuses_an_unknown_provider_and_hand_kept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    history = read_history(path)
    with pytest.raises(HistoryError):
        plan_redaction(history, provider="youtube-analytics")
    with pytest.raises(HistoryError):
        plan_redaction(history)  # nothing selects anything


def run_cli(path: Path, *extra: str) -> int:
    return main(
        [
            "counts",
            "history-redact",
            "--file",
            str(path),
            "--reason",
            "platform_revocation",
            "--trigger-date",
            "2026-10-20",
            *extra,
        ]
    )


def test_cli_provider_selector_redacts_only_that_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    assert run_cli(path, "--provider", ANALYTICS_PROVIDER) == 0  # dry run
    assert "1 lines would be redacted" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert run_cli(path, "--provider", ANALYTICS_PROVIDER, "--confirm") == 0
    kinds = [x["type"] if "type" in x else "record" for x in lines(path)]
    assert kinds == ["tombstone", "record"]


def test_cli_needs_a_selector_and_a_known_provider(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    with pytest.raises(SystemExit):
        run_cli(path, "--provider", "youtube-analytics")
    assert run_cli(path) != 0  # neither --platform nor --provider


# (c) a tombstone stays value-free


def test_the_tombstone_stores_no_deleted_value(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    redact_all(path, provider=ANALYTICS_PROVIDER)
    raw = path.read_bytes().decode()
    for needle in (str(VALUE), str(VALUE + 1), NOTE, ERA):
        assert needle not in raw
    assert set(lines(path)[0]) == {
        "type",
        "platform",
        "metric",
        "as_of_utc",
        "source",
        "reason",
        "trigger_date",
        "redacted_at_utc",
        "run_id",
        "api_provider",
        "prev",
    }


@pytest.mark.parametrize("extra", [{"value": 5}, {"note": "x"}, {"era": "x"}])
def test_a_tombstone_that_carries_a_value_is_refused(
    tmp_path: Path, extra: dict[str, Any]
) -> None:
    path = tmp_path / "h.jsonl"
    write_tomb(path, tomb(**extra))
    with pytest.raises(HistoryError):
        read_history(path)
