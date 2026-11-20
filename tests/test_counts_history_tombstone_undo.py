# SPDX-License-Identifier: Apache-2.0
"""The exchange undo and the old copy (issue #586, review round 3)."""

import os
import time
from pathlib import Path
from typing import Any

import pytest

import reach_around.counts.history as hist
from reach_around.cli import main
from reach_around.counts.history import (
    RedactDurabilityError,
    find_stale_redaction_temps,
    read_history,
    redact_records,
    remove_stale_redaction_temps,
    tombstoned,
)
from test_counts_history_tombstone_hardening import cli_args
from test_counts_history_tombstone_race import (
    KW,
    build,
    names,
    plan_for,
    stale_pair,
    swap_in_other_file,
)


def age(folder: Path, seconds: float = 7200) -> None:
    when = time.time() - seconds
    for entry in folder.iterdir():
        if entry.name.startswith(".h.jsonl.redact-"):
            os.utime(entry, (when, when), follow_symlinks=False)


def temps(folder: Path) -> list[str]:
    return [n for n in names(folder) if n.endswith(".tmp")]


def swap_at_stages(
    monkeypatch: pytest.MonkeyPatch, path: Path, values: dict[str, int]
) -> dict[str, bytes]:
    """Replace the file the first time each named stage is reached."""
    done: dict[str, bytes] = {}

    def hook(stage: str) -> None:
        if stage in values and stage not in done:
            done[stage] = b""  # building the other file calls this hook too
            done[stage] = swap_in_other_file(path, values[stage])

    monkeypatch.setattr(hist, "_before_mutation", hook)
    return done


def test_a_second_replacement_before_the_undo_loses_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    done = swap_at_stages(monkeypatch, path, {"redact": 999, "redact-undo": 998})
    code = main(cli_args(path, "--confirm"))
    err = capsys.readouterr().err
    monkeypatch.undo()
    assert code == 5
    assert "nothing was changed" not in err and "history-verify" in err
    first, second = done["redact"], done["redact-undo"]
    assert path.read_bytes() == first  # the undo put the first replacement back
    left = temps(tmp_path)
    assert len(left) == 1
    assert (tmp_path / left[0]).read_bytes() == second  # the newer file is kept
    # Stale cleanup must never delete it, however old it is.
    age(tmp_path)
    assert find_stale_redaction_temps(path) == []
    assert remove_stale_redaction_temps(path, left) == 0
    assert main(cli_args(path, "--confirm")) == 0
    assert (tmp_path / left[0]).read_bytes() == second


def test_an_undo_that_cannot_be_done_leaves_every_file_as_it_is(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    done = swap_at_stages(monkeypatch, path, {"redact": 999})
    real = hist._exchange
    calls: list[int] = []

    def exchange(dir_fd: int, first: str, second: str) -> bool:
        calls.append(1)
        return real(dir_fd, first, second) if len(calls) == 1 else False

    monkeypatch.setattr(hist, "_exchange", exchange)
    code = main(cli_args(path, "--confirm"))
    err = capsys.readouterr().err
    monkeypatch.undo()
    assert code == 5
    assert "nothing was changed" not in err
    assert len(tombstoned(read_history(path))) == 2  # our output is installed
    left = temps(tmp_path)
    assert len(left) == 1
    assert (tmp_path / left[0]).read_bytes() == done["redact"]  # theirs, kept
    age(tmp_path)
    assert find_stale_redaction_temps(path) == []
    assert remove_stale_redaction_temps(path, left) == 0
    assert (tmp_path / left[0]).exists()


def test_the_marker_exists_before_the_exchange_and_is_gone_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    seen: list[list[str]] = []

    def hook(stage: str) -> None:
        if stage == "redact":
            seen.append(names(tmp_path))

    monkeypatch.setattr(hist, "_before_mutation", hook)
    assert redact_records(path, plan_for(path), **KW) == 2
    (listing,) = seen
    assert len([n for n in listing if n.endswith(".mark")]) == 1
    assert len([n for n in listing if n.endswith(".tmp")]) == 1
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_a_failed_install_removes_its_temporary_and_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)

    def boom(*a: Any, **k: Any) -> bool:
        raise OSError("no")

    monkeypatch.setattr(hist, "_exchange", boom)
    with pytest.raises(hist.HistoryError):
        redact_records(path, plan, **KW)
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_a_marker_proves_what_a_file_is(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    young_old = stale_pair(tmp_path, code="aaaaaaaa", age=5, kind="old")
    stale_pair(tmp_path, code="bbbbbbbb", age=5, kind="output")  # too young
    old_output = stale_pair(tmp_path, code="cccccccc", kind="output")
    linked = stale_pair(tmp_path, code="dddddddd")
    mark = tmp_path / ".h.jsonl.redact-dddddddd.mark"
    real = tmp_path / "real-marker"
    mark.rename(real)
    mark.symlink_to(real)  # a marker that is a symlink proves nothing
    assert find_stale_redaction_temps(path) == sorted([young_old, old_output])
    assert linked not in find_stale_redaction_temps(path)


def test_an_orphan_marker_is_found_and_removed_when_old(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    gone = stale_pair(tmp_path, code="eeeeee01")
    (tmp_path / gone).unlink()
    young = stale_pair(tmp_path, code="eeeeee02", age=5)
    (tmp_path / young).unlink()
    found = find_stale_redaction_temps(path)
    assert found == [".h.jsonl.redact-eeeeee01.mark"]
    assert remove_stale_redaction_temps(path, found) == 1
    assert ".h.jsonl.redact-eeeeee02.mark" in names(tmp_path)
    assert ".h.jsonl.redact-eeeeee01.mark" not in names(tmp_path)


def fail_unlink_of_temporaries(
    monkeypatch: pytest.MonkeyPatch, times: int | None
) -> None:
    real = os.unlink
    failed: list[int] = []

    def unlink(path: Any, *, dir_fd: int | None = None) -> None:
        if str(path).endswith(".tmp") and (times is None or len(failed) < times):
            failed.append(1)
            raise OSError("no")
        real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", unlink)


def test_an_old_copy_that_cannot_be_removed_is_reported_and_then_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    fail_unlink_of_temporaries(monkeypatch, None)
    code = main(cli_args(path, "--confirm"))
    err = capsys.readouterr().err
    monkeypatch.undo()
    assert code == 5
    assert "remain" in err and ".h.jsonl.redact-" in err
    assert len(tombstoned(read_history(path))) == 2
    left = temps(tmp_path)
    assert len(left) == 1
    assert b'"value":10' in (tmp_path / left[0]).read_bytes()  # old values on disk
    # Young, yet the marker proves it is the old copy, so it is listed.
    assert find_stale_redaction_temps(path) == left
    assert main(cli_args(path, "--confirm")) == 0
    assert "removed 1" in capsys.readouterr().out
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]
    assert len(tombstoned(read_history(path))) == 2


def test_one_failed_unlink_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    fail_unlink_of_temporaries(monkeypatch, 1)
    assert redact_records(path, plan_for(path), **KW) == 2
    monkeypatch.undo()
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_the_error_names_the_old_copy_for_manual_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    fail_unlink_of_temporaries(monkeypatch, None)
    with pytest.raises(RedactDurabilityError) as err:
        redact_records(path, plan, **KW)
    monkeypatch.undo()
    assert getattr(err.value, "temp_name", "") == temps(tmp_path)[0]
