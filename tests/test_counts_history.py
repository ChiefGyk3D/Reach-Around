# SPDX-License-Identifier: Apache-2.0
import json
import multiprocessing
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.counts.history import (
    GENESIS,
    HistoryError,
    Record,
    append_records,
    read_history,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "twitch",
        "metric": "followers",
        "value": 10,
        "as_of_utc": "2026-11-08T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
    }
    fields.update(over)
    return Record(**fields)


def add(path: Path, *records: Record) -> None:
    append_records(path, list(records), now=NOW)


def lines(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines(keepends=True)


def test_append_creates_file_and_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(), rec(platform="youtube", metric="subscribers", value=5))
    history = read_history(path)
    assert [r.platform for r in history.records] == ["twitch", "youtube"]
    first = json.loads(lines(path)[0])
    assert first["prev"] == GENESIS
    assert first["as_of_utc"] == "2026-11-08T09:00:00Z"
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_append_never_rewrites_earlier_lines(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    before = path.read_bytes()
    add(path, rec(as_of_utc="2026-11-09T09:00:00Z", value=11))
    after = path.read_bytes()
    assert after.startswith(before)
    assert len(lines(path)) == 2


def test_modifying_an_earlier_line_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    add(path, rec(as_of_utc="2026-11-09T09:00:00Z", value=11))
    tampered = path.read_bytes().replace(b'"value":10', b'"value":99', 1)
    assert tampered != path.read_bytes()
    path.write_bytes(tampered)
    with pytest.raises(HistoryError, match="chain"):
        read_history(path)
    with pytest.raises(HistoryError):
        add(path, rec(as_of_utc="2026-11-10T09:00:00Z"))


def test_deleting_an_earlier_line_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    for day in (8, 9, 10):
        add(path, rec(as_of_utc=f"2026-11-{day:02d}T09:00:00Z"))
    rows = lines(path)
    path.write_bytes(rows[0] + rows[2])
    with pytest.raises(HistoryError, match="chain"):
        read_history(path)


def test_duplicate_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    before = path.read_bytes()
    with pytest.raises(HistoryError, match="duplicate"):
        add(path, rec(value=11))
    assert path.read_bytes() == before
    add(path, rec(source="hand_kept"))  # another source is another key


def test_duplicate_inside_one_batch_is_refused_and_nothing_written(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    with pytest.raises(HistoryError, match="duplicate"):
        add(path, rec(), rec())
    assert not path.exists() or path.read_bytes() == b""


def test_correction_replaces_value_without_touching_the_original(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    original = path.read_bytes()
    add(path, rec(value=12, type="correction", note="typo"))
    assert path.read_bytes().startswith(original)
    history = read_history(path)
    assert len(history.records) == 2
    point = history.series("twitch", "followers").points()[0]
    assert point.value == 12
    assert point.corrected is True


def test_correction_needs_note_and_an_existing_original(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    with pytest.raises(HistoryError, match="note"):
        add(path, rec(type="correction"))
    with pytest.raises(HistoryError, match="no earlier record"):
        add(path, rec(type="correction", note="x"))
    add(path, rec())
    with pytest.raises(HistoryError, match="note"):
        add(path, rec(value=1, type="correction"))


def test_truncated_last_line_is_refused_not_dropped(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    add(path, rec(as_of_utc="2026-11-09T09:00:00Z"))
    cut = path.read_bytes()[:-15]  # crash mid-write
    path.write_bytes(cut)
    with pytest.raises(HistoryError, match="truncated"):
        read_history(path)
    with pytest.raises(HistoryError, match="truncated"):
        add(path, rec(as_of_utc="2026-11-10T09:00:00Z"))
    assert path.read_bytes() == cut  # left exactly as found


def test_last_line_missing_only_newline_is_truncated(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(HistoryError, match="truncated"):
        read_history(path)


def test_empty_file_is_valid_and_missing_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    with pytest.raises(HistoryError, match="cannot open the history file"):
        read_history(path)
    path.write_bytes(b"")
    assert read_history(path).records == ()


def test_gaps_are_gaps_never_zeros(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(as_of_utc="2026-11-01T09:00:00Z", value=5))
    add(path, rec(as_of_utc="2026-11-04T09:00:00Z", value=8))
    series = read_history(path).series("twitch", "followers")
    assert series.gaps(date(2026, 11, 1), date(2026, 11, 5)) == [
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 5),
    ]
    by_day = series.by_day()
    assert date(2026, 11, 2) not in by_day
    assert by_day[date(2026, 11, 4)].value == 8
    assert read_history(path).series("twitch", "nothing").points() == []


def test_second_record_for_one_utc_day_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(as_of_utc="2026-11-01T01:00:00Z", value=5))
    with pytest.raises(HistoryError, match="second record"):
        add(path, rec(as_of_utc="2026-11-01T20:00:00Z", value=7))
    add(path, rec(as_of_utc="2026-11-01T20:00:00Z", source="hand_kept"))
    add(path, rec(as_of_utc="2026-11-02T00:00:00Z", value=9))
    # a correction of that day's record is still allowed
    add(
        path,
        rec(as_of_utc="2026-11-01T01:00:00Z", value=6, type="correction", note="x"),
    )


def test_skip_days_leaves_out_recorded_days_and_writes_the_rest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(as_of_utc="2026-11-01T01:00:00Z", value=5))
    kept = append_records(
        path,
        [
            rec(as_of_utc="2026-11-01T20:00:00Z", value=7),
            rec(platform="github", as_of_utc="2026-11-01T20:00:00Z", value=3),
        ],
        now=NOW,
        skip_days=True,
    )
    assert kept == 1
    assert [r.platform for r in read_history(path).records] == ["twitch", "github"]
    assert (
        append_records(
            path, [rec(as_of_utc="2026-11-01T22:00:00Z")], now=NOW, skip_days=True
        )
        == 0
    )


def test_imported_source_is_its_own_source(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(source="imported"), rec(source="api"))
    assert {r.source for r in read_history(path).records} == {"imported", "api"}


def test_existing_file_with_wide_mode_is_tightened(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(b"")
    path.chmod(0o644)
    add(path, rec())
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_failed_fsync_rolls_the_append_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec())
    before = path.read_bytes()
    real = os.fsync
    calls = {"n": 0}

    def flaky(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk")
        real(fd)

    monkeypatch.setattr(os, "fsync", flaky)
    with pytest.raises(HistoryError):
        add(path, rec(as_of_utc="2026-11-09T09:00:00Z"))
    assert path.read_bytes() == before
    add(path, rec(as_of_utc="2026-11-09T09:00:00Z"))  # a retry is clean


def test_symlinked_folder_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(HistoryError):
        add(tmp_path / "link" / "h.jsonl", rec())
    assert list(real.iterdir()) == []


def test_era_and_note_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(era="includes-gifted-2026-08-11", note="kept by hand"))
    got = read_history(path).records[0]
    assert got.era == "includes-gifted-2026-08-11"
    assert got.note == "kept by hand"


def test_api_provider_is_validated_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    add(path, rec(api_provider="youtube_data_api"))
    assert read_history(path).records[0].api_provider == "youtube_data_api"
    with pytest.raises(HistoryError, match="api_provider"):
        add(
            path,
            rec(
                as_of_utc="2026-11-09T09:00:00Z",
                source="hand_kept",
                api_provider="youtube_data_api",
            ),
        )
    with pytest.raises(HistoryError, match="api_provider"):
        add(
            path,
            rec(
                as_of_utc="2026-11-09T09:00:00Z",
                api_provider="youtube_other_api",
            ),
        )


def _worker(args: tuple[str, int]) -> None:
    path, n = args
    stamp = (date(2026, 1, 1) + timedelta(days=n)).isoformat() + "T09:00:00Z"
    append_records(Path(path), [rec(as_of_utc=stamp, value=n)], now=NOW)


def test_concurrent_appends_stay_valid(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    context = multiprocessing.get_context("fork")
    with context.Pool(6) as pool:
        pool.map(_worker, [(str(path), n) for n in range(30)])
    history = read_history(path)
    assert sorted(r.value for r in history.records) == list(range(30))


@pytest.mark.parametrize(
    "bad",
    [
        {"platform": "Twitch"},
        {"platform": ""},
        {"platform": "a" * 41},
        {"metric": "has space"},
        {"metric": ""},
        {"value": -1},
        {"value": 2**50},
        {"value": True},
        {"value": 1.5},
        {"value": "10"},
        {"as_of_utc": "2026-11-08"},
        {"as_of_utc": "2026-11-08T09:00:00+00:00"},
        {"as_of_utc": "2026-13-08T09:00:00Z"},
        {"as_of_utc": "2027-01-01T00:00:00Z"},  # after NOW
        {"source": "typed"},
        {"run_id": "has space"},
        {"note": "x" * 201},
        {"note": "line\nbreak"},
        {"era": "\x00"},
        {"type": "delete"},
    ],
)
def test_hostile_field_values_are_refused(tmp_path: Path, bad: dict[str, Any]) -> None:
    path = tmp_path / "h.jsonl"
    with pytest.raises(HistoryError):
        add(path, rec(**bad))
    assert not path.exists() or path.read_bytes() == b""


def good_line(**over: Any) -> bytes:
    obj: dict[str, Any] = {
        "platform": "twitch",
        "metric": "followers",
        "value": 1,
        "as_of_utc": "2026-11-08T09:00:00Z",
        "source": "api",
        "run_id": "r",
        "prev": GENESIS,
    }
    obj.update(over)
    return json.dumps(obj).encode() + b"\n"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}\n',
        good_line().replace(b'"value": 1', b'"value": NaN'),
        good_line().replace(b'"value": 1', b'"value": Infinity'),
        good_line().replace(b'"value": 1', b'"value": 1.0'),
        good_line().replace(b'"value": 1', b'"value": 1e3'),
        good_line(extra="x"),
        good_line(value=[1]),
        good_line(prev="nothex"),
        b"\n",
        b"[1]\n",
        b'"x"\n',
        b"\xff\xfe\n",
        b"\xef\xbb\xbf" + good_line(),
        b"[" * 100000 + b"\n",
        good_line(note="x" * 5000),
        b'{"platform":"twitch"}\n',
    ],
)
def test_hostile_file_content_is_refused(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(raw)
    with pytest.raises(HistoryError):
        read_history(path)


def test_a_well_formed_foreign_line_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(good_line())
    assert len(read_history(path).records) == 1


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(b"x" * 100)
    with pytest.raises(HistoryError, match="too large"):
        read_history(path, max_bytes=50)


def test_symlink_path_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.jsonl"
    target.write_bytes(b"")
    link = tmp_path / "h.jsonl"
    link.symlink_to(target)
    with pytest.raises(HistoryError):
        add(link, rec())
    with pytest.raises(HistoryError):
        read_history(link)


def test_missing_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HistoryError):
        add(tmp_path / "nope" / "h.jsonl", rec())


def test_error_text_never_echoes_content(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(good_line(note="SENTINEL-SECRET", extra="x"))
    with pytest.raises(HistoryError) as err:
        read_history(path)
    assert "SENTINEL-SECRET" not in str(err.value)
