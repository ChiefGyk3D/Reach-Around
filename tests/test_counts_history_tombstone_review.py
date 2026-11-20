# SPDX-License-Identifier: Apache-2.0
"""Review follow-ups for the history tombstone (issue #586)."""

from pathlib import Path
from typing import Any

import pytest

import reach_around.counts.history as hist
from reach_around.cli import main
from reach_around.counts.history import HistoryError, append_records
from test_counts_history_tombstone_final import swap_in_other_file
from test_counts_history_tombstone_race import NOW, build, names, rec, stale_pair


def redact_argv(path: Path, *extra: str, trigger: str = "2026-10-20") -> list[str]:
    return [
        "counts",
        "history-redact",
        "--file",
        str(path),
        "--platform",
        "youtube",
        "--reason",
        "platform_expiry",
        "--trigger-date",
        trigger,
        *extra,
    ]


# Arguments are checked before any cleanup runs


@pytest.mark.parametrize("trigger", ["2026-02-30", "2999-01-01", "not-a-date"])
def test_a_bad_trigger_date_removes_nothing(tmp_path: Path, trigger: str) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    stale = stale_pair(tmp_path, path.name)
    before = names(tmp_path)
    assert main(redact_argv(path, "--confirm", trigger=trigger)) != 0
    assert names(tmp_path) == before
    assert (tmp_path / stale).exists()


def test_a_valid_confirm_with_no_matching_rows_still_cleans_up(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    stale = stale_pair(tmp_path, path.name)
    argv = redact_argv(path, "--confirm")
    argv[argv.index("youtube")] = "tiktok"  # selects nothing
    assert main(argv) == 0
    assert not (tmp_path / stale).exists()


# An append that waited for the writers' lock through a replacement is refused


def test_an_append_that_waited_for_the_writers_lock_through_a_replacement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    real = hist._open_sidecar
    armed = [True]

    def swap_while_waiting(*args: Any, **kwargs: Any) -> int:
        fd = real(*args, **kwargs)
        if armed[0]:
            armed[0] = False  # the helper's own build() uses this function too
            swap_in_other_file(path)  # what a redaction does while we wait
        return fd

    monkeypatch.setattr(hist, "_open_sidecar", swap_while_waiting)
    with pytest.raises(HistoryError, match="replaced"):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert b"2026-10-09" not in path.read_bytes()


def test_an_append_without_a_replacement_still_works_and_a_new_file_is_created(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    append_records(path, [rec()], now=NOW)  # the file does not exist yet
    append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert path.read_bytes().count(b"\n") == 2
