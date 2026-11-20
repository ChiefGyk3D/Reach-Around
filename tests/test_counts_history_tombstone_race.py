# SPDX-License-Identifier: Apache-2.0
"""Races, ambiguous renames, umask and printed names (issue #586, review round 2)."""

import fcntl
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.counts as counts_cmd
import reach_around.counts.history as hist
from reach_around.cli import main
from reach_around.counts.history import (
    HistoryError,
    Record,
    RedactDurabilityError,
    append_records,
    find_stale_redaction_temps,
    plan_redaction,
    read_history,
    redact_records,
    remove_stale_redaction_temps,
    tombstoned,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)
REDACT_AT = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
KW: dict[str, Any] = {
    "reason": "platform_expiry",
    "trigger_date": "2026-10-20",
    "now": REDACT_AT,
    "confirm": True,
}


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "youtube",
        "metric": "subscribers",
        "value": 10,
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
    }
    fields.update(over)
    return Record(**fields)


def build(path: Path, *, value: int = 10) -> None:
    append_records(
        path,
        [
            rec(value=value),
            rec(as_of_utc="2026-10-02T09:00:00Z", value=11),
            rec(as_of_utc="2026-10-03T09:00:00Z", value=12),
        ],
        now=NOW,
    )


def plan_for(path: Path) -> list[Record]:
    return plan_redaction(
        read_history(path), platform="youtube", source="api", before="2026-10-03"
    )


def swap_in_other_file(path: Path) -> bytes:
    """Replace ``path`` by a different valid file, as a git checkout would."""
    other = path.parent / "swapped-in"
    build(other, value=999)
    data = other.read_bytes()
    os.replace(other, path)
    (path.parent / ".swapped-in.lock").unlink()
    return data


def swap_once(monkeypatch: pytest.MonkeyPatch, path: Path, stage: str) -> list[bytes]:
    """Swap the file the first time the code reaches ``stage``."""
    result: list[bytes] = []
    started: list[bool] = []

    def hook(name: str) -> None:
        if name == stage and not started:
            started.append(True)  # building the other file calls this hook too
            result.append(swap_in_other_file(path))

    monkeypatch.setattr(hist, "_before_mutation", hook)
    return result


def names(folder: Path) -> list[str]:
    return sorted(os.listdir(folder))


# 1. the final-name race


def test_an_append_whose_file_is_swapped_after_the_last_check_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    swapped = swap_once(monkeypatch, path, "append")
    with pytest.raises(HistoryError, match="replaced"):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert swapped
    assert path.read_bytes() == swapped[0]  # the swapped-in file is untouched
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_a_redaction_whose_file_is_swapped_after_the_last_check_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    swapped = swap_once(monkeypatch, path, "redact")
    with pytest.raises(HistoryError, match="replaced"):
        redact_records(path, plan, **KW)
    assert swapped
    assert path.read_bytes() == swapped[0]  # not overwritten by the old output
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]  # no temp file left
    assert not tombstoned(read_history(path))


def test_the_fallback_without_an_atomic_swap_still_refuses_a_swapped_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hist, "_exchange", lambda *a: False)
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    swapped = swap_once(monkeypatch, path, "redact")
    with pytest.raises(HistoryError, match="replaced"):
        redact_records(path, plan, **KW)
    assert path.read_bytes() == swapped[0]
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_a_redaction_works_with_and_without_the_atomic_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for flavor in ("atomic", "fallback"):
        folder = tmp_path / flavor
        folder.mkdir()
        if flavor == "fallback":
            monkeypatch.setattr(hist, "_exchange", lambda *a: False)
        path = folder / "h.jsonl"
        build(path)
        assert redact_records(path, plan_for(path), **KW) == 2
        assert len(tombstoned(read_history(path))) == 2
        assert names(folder) == [".h.jsonl.lock", "h.jsonl"], flavor
        body = (folder / "h.jsonl").read_bytes()
        assert b'"value":10' not in body and b'"value":11' not in body


def test_the_lock_file_is_stable_private_and_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    lock = tmp_path / ".h.jsonl.lock"
    first = lock.stat()
    assert stat.S_IMODE(first.st_mode) == 0o600
    append_records(path, [rec(as_of_utc="2026-10-04T09:00:00Z")], now=NOW)
    redact_records(path, plan_for(path), **KW)
    after = lock.stat()
    assert (after.st_dev, after.st_ino) == (first.st_dev, first.st_ino)


def test_reading_never_creates_the_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    (tmp_path / ".h.jsonl.lock").unlink()
    read_history(path)
    assert main(["counts", "history-verify", "--file", str(path)]) == 0
    assert names(tmp_path) == ["h.jsonl"]


def hold(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def test_the_lock_file_serializes_append_redact_and_stale_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    before = path.read_bytes()
    monkeypatch.setattr(hist, "LOCK_WAIT_SECONDS", 0.05)
    fd = hold(tmp_path / ".h.jsonl.lock")
    try:
        with pytest.raises(HistoryError, match="locked"):
            append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
        with pytest.raises(HistoryError, match="locked"):
            redact_records(path, plan, lock_wait=0.05, **KW)
        with pytest.raises(HistoryError, match="locked"):
            remove_stale_redaction_temps(
                path, [".h.jsonl.redact-0a1b2c3d.tmp"], lock_wait=0.05
            )
    finally:
        os.close(fd)
    assert path.read_bytes() == before


def test_the_lock_file_is_not_a_stale_temporary(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    old = time.time() - 7200
    os.utime(tmp_path / ".h.jsonl.lock", (old, old))
    assert find_stale_redaction_temps(path) == []
    assert remove_stale_redaction_temps(path, [".h.jsonl.lock"]) == 0
    assert (tmp_path / ".h.jsonl.lock").exists()


def test_a_lock_file_that_is_a_fifo_or_a_symlink_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    (tmp_path / ".h.jsonl.lock").unlink()
    os.mkfifo(tmp_path / ".h.jsonl.lock")
    with pytest.raises(HistoryError):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    (tmp_path / ".h.jsonl.lock").unlink()
    (tmp_path / "elsewhere").write_bytes(b"")
    (tmp_path / ".h.jsonl.lock").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(HistoryError):
        redact_records(path, plan_for(path), **KW)
    assert not tombstoned(read_history(path))


# 2. an ambiguous rename


def real_then_raise(real: Any) -> Any:
    def call(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise OSError("interrupted after the rename")

    return call


def test_a_rename_that_happened_and_then_raised_is_reported_as_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    monkeypatch.setattr(hist, "_exchange", lambda *a: False)
    monkeypatch.setattr(os, "rename", real_then_raise(os.rename))
    with pytest.raises(RedactDurabilityError, match="reported an error"):
        redact_records(path, plan, **KW)
    monkeypatch.undo()
    assert len(tombstoned(read_history(path))) == 2
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_an_exchange_that_happened_and_then_raised_is_reported_as_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    monkeypatch.setattr(hist, "_exchange", real_then_raise(hist._exchange))
    with pytest.raises(RedactDurabilityError, match="reported an error"):
        redact_records(path, plan, **KW)
    monkeypatch.undo()
    assert len(tombstoned(read_history(path))) == 2
    # The old copy sits under the temporary name until the owner runs it again.
    assert len(find_stale_redaction_temps(path)) == 0  # young: not yet "stale"
    left = [n for n in names(tmp_path) if n.endswith(".tmp")]
    assert len(left) <= 1


def test_a_rename_that_did_not_happen_is_a_plain_error_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    before = path.read_bytes()

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("no")

    monkeypatch.setattr(hist, "_exchange", lambda *a: False)
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(HistoryError) as err:
        redact_records(path, plan, **KW)
    assert not isinstance(err.value, RedactDurabilityError)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_a_failed_exchange_is_a_plain_error_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    before = path.read_bytes()

    def boom(*a: Any, **k: Any) -> bool:
        raise OSError("no")

    monkeypatch.setattr(hist, "_exchange", boom)
    with pytest.raises(HistoryError) as err:
        redact_records(path, plan, **KW)
    assert not isinstance(err.value, RedactDurabilityError)
    assert path.read_bytes() == before
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


@pytest.mark.parametrize("how", ["replaced again", "changed in place"])
def test_an_installed_file_that_does_not_check_out_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    real = hist._exchange

    def exchange_then_tamper(dir_fd: int, first: str, second: str) -> bool:
        done = real(dir_fd, first, second)
        if how == "replaced again":
            swap_in_other_file(path)
        else:
            with path.open("ab") as handle:
                handle.write(b"x")
        return done

    monkeypatch.setattr(hist, "_exchange", exchange_then_tamper)
    with pytest.raises(RedactDurabilityError, match="check out"):
        redact_records(path, plan, **KW)


# 3. the umask


def test_the_new_file_is_0600_whatever_the_umask(tmp_path: Path) -> None:
    old = os.umask(0o777)
    try:
        path = tmp_path / "h.jsonl"
        build(path)
        assert redact_records(path, plan_for(path), **KW) == 2
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / ".h.jsonl.lock").stat().st_mode) == 0o600
        assert main(["counts", "history-verify", "--file", str(path)]) == 0
        assert len(tombstoned(read_history(path))) == 2
    finally:
        os.umask(old)


# 4. printed names are control-safe


def test_stale_names_with_escape_sequences_are_printed_escaped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h\x1b[2Jx.jsonl"
    build(path)
    temp = tmp_path / ".h\x1b[2Jx.jsonl.redact-0a1b2c3d.tmp"
    temp.write_text("private", encoding="ascii")
    old = time.time() - 7200
    os.utime(temp, (old, old))
    code = main(
        [
            "counts",
            "history-redact",
            "--file",
            str(path),
            "--platform",
            "youtube",
            "--reason",
            "platform_expiry",
            "--trigger-date",
            "2026-10-20",
        ]
    )
    out = capsys.readouterr()
    assert code == 0
    assert "\x1b" not in out.out + out.err
    assert "\\x1b[2J" in out.out
    assert temp.exists()


def test_error_text_is_printed_control_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def bad(*a: Any, **k: Any) -> Any:
        raise HistoryError("bad \x1b[31mred\x07 name\r\nnext")

    monkeypatch.setattr(counts_cmd, "read_history", bad)
    code = main(
        [
            "counts",
            "history-redact",
            "--file",
            str(tmp_path / "h.jsonl"),
            "--platform",
            "youtube",
            "--reason",
            "platform_expiry",
            "--trigger-date",
            "2026-10-20",
        ]
    )
    err = capsys.readouterr().err
    assert code == 1
    for ch in ("\x1b", "\x07", "\r"):
        assert ch not in err
    assert err.count("\n") == 1
    assert "\\x1b[31mred" in err
