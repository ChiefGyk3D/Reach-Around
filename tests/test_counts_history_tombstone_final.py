# SPDX-License-Identifier: Apache-2.0
"""Final review of the tombstone work (issue #586): guards that must bite."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

import reach_around.counts.history as hist
from reach_around.cli import main
from reach_around.counts.history import (
    GENESIS,
    HistoryError,
    RedactDurabilityError,
    append_records,
    find_stale_redaction_temps,
    plan_redaction,
    read_history,
    redact_records,
    remove_stale_redaction_temps,
)
from test_counts_history_tombstone_hardening import cli_args, make_temps
from test_counts_history_tombstone_race import (
    KW,
    NOW,
    build,
    names,
    plan_for,
    rec,
    stale_pair,
    swap_in_other_file,
)

# 3. the proof, the writers' own checks and the marker


def test_the_right_digest_under_the_wrong_inode_is_not_proof(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    temp = tmp_path / ".h.jsonl.redact-0a1b2c3d.tmp"
    temp.write_bytes(b"private")
    info = temp.stat()
    sha = hashlib.sha256(b"private").hexdigest()
    mark = tmp_path / ".h.jsonl.redact-0a1b2c3d.mark"
    other = (0, 0, "0" * 64)
    for ino in (info.st_ino + 1, info.st_ino):
        mark.write_text(
            hist._marker_text((info.st_dev, ino, sha), other), encoding="ascii"
        )
        for item in (temp, mark):
            os.utime(item, (1, 1))  # old
        found = find_stale_redaction_temps(path)
        assert found == ([temp.name] if ino == info.st_ino else [])  # control
    wrong_device = hist._marker_text((info.st_dev + 1, info.st_ino, sha), other)
    mark.write_text(wrong_device, encoding="ascii")
    os.utime(mark, (1, 1))
    assert find_stale_redaction_temps(path) == []


GOOD = (0, 0, "a" * 64)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t + "\n",  # a second trailing newline
        lambda t: t.replace("marker 1", "marker 2"),
        lambda t: t.replace("out ", "out  "),
        lambda t: t.replace("a" * 64, "A" * 64, 1),
        lambda t: t.replace("a" * 64, "a" * 63, 1),
        lambda t: t.replace("old ", "new "),
        lambda t: t + "extra line\n",
        lambda t: t.rstrip("\n"),
        lambda t: " " + t,
        lambda t: t.replace("\n", "\r\n"),
    ],
)
def test_a_marker_that_is_not_exactly_the_format_proves_nothing(
    tmp_path: Path, mangle: Any
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    name = stale_pair(tmp_path)
    mark = tmp_path / ".h.jsonl.redact-0a1b2c3d.mark"
    info = (tmp_path / name).stat()
    sha = hashlib.sha256(b"private").hexdigest()
    text = hist._marker_text((info.st_dev, info.st_ino, sha), GOOD)
    mark.write_text(text, encoding="ascii", newline="")
    os.utime(mark, (1, 1))
    assert find_stale_redaction_temps(path) == [name]  # the exact text proves it
    mark.write_text(mangle(text), encoding="ascii", newline="")
    os.utime(mark, (1, 1))
    assert find_stale_redaction_temps(path) == []


def swap_on_call(
    monkeypatch: pytest.MonkeyPatch, path: Path, attr: str, number: int
) -> list[bytes]:
    """Replace the file on the ``number``-th call of ``hist.<attr>``."""
    real = getattr(hist, attr)
    calls: list[int] = []
    swapped: list[bytes] = []
    armed = [True]

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if armed[0]:
            calls.append(1)
            if len(calls) == number:
                armed[0] = False  # the swap itself runs this code too
                swapped.append(swap_in_other_file(path))
        return result

    monkeypatch.setattr(hist, attr, wrapper)
    return swapped


def test_an_append_whose_file_was_replaced_while_it_waited_for_the_lock_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    # The 1st lock is the writers' lock file, the 2nd the history file itself.
    swapped = swap_on_call(monkeypatch, path, "_lock", 2)
    with pytest.raises(HistoryError, match="while waiting"):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert path.read_bytes() == swapped[0]


def test_an_append_whose_file_was_replaced_before_the_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    # The check of the file (streamed) is the last step before the final look at
    # the name.
    swapped = swap_on_call(monkeypatch, path, "_chain_from_fd", 1)
    with pytest.raises(HistoryError, match="before the write"):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert path.read_bytes() == swapped[0]


def test_a_redaction_whose_file_was_replaced_after_locking_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    swapped = swap_on_call(monkeypatch, path, "_lock", 2)
    with pytest.raises(HistoryError, match="changed while locking"):
        redact_records(path, plan, **KW)
    assert path.read_bytes() == swapped[0]
    assert names(tmp_path) == [".h.jsonl.lock", "h.jsonl"]


def test_a_rename_that_leaves_an_unknown_file_under_the_name_exits_5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)

    def exchange_then_fail(*args: Any, **kwargs: Any) -> bool:
        swap_in_other_file(path)  # someone else's file is now under the name
        raise OSError("failed")

    monkeypatch.setattr(hist, "_exchange", exchange_then_fail)
    with pytest.raises(RedactDurabilityError, match="could not tell"):
        redact_records(path, plan, **KW)
    # The same through the command: exit 5.
    path2 = tmp_path / "g.jsonl"
    build(path2)

    def exchange_then_fail2(*args: Any, **kwargs: Any) -> bool:
        swap_in_other_file(path2)
        raise OSError("failed")

    monkeypatch.setattr(hist, "_exchange", exchange_then_fail2)
    assert main(cli_args(path2, "--confirm")) == 5
    assert "could not tell" in capsys.readouterr().err


def test_the_marker_is_private_whatever_the_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = os.umask(0o777)
    try:
        path = tmp_path / "h.jsonl"
        build(path)
        plan = plan_for(path)
        seen: list[int] = []

        def hook(stage: str) -> None:
            if stage == "redact":
                for entry in tmp_path.iterdir():
                    if entry.name.endswith(".mark"):
                        seen.append(stat.S_IMODE(entry.stat().st_mode))

        monkeypatch.setattr(hist, "_before_mutation", hook)
        assert redact_records(path, plan, **KW) == 2
    finally:
        os.umask(old)
    assert seen == [0o600]


# 5. a trailing newline is not part of a name


def tomb(**over: Any) -> bytes:
    fields: dict[str, Any] = {
        "type": "tombstone",
        "platform": "youtube",
        "metric": "subscribers",
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "reason": "platform_expiry",
        "trigger_date": "2026-10-20",
        "redacted_at_utc": "2026-11-19T12:00:00Z",
        "run_id": "redact-1",
        "prev": GENESIS,
    }
    fields.update(over)
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode() + b"\n"


@pytest.mark.parametrize(
    "over",
    [
        {"platform": "youtube\n"},
        {"metric": "subscribers\n"},
        {"run_id": "redact-1\n"},
        {"trigger_date": "2026-10-20\n"},
        {"prev": GENESIS + "\n"},
    ],
)
def test_a_trailing_newline_in_a_tombstone_field_is_refused(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(tomb())
    assert read_history(path).tombstones  # the control parses
    path.write_bytes(tomb(**over))
    with pytest.raises(HistoryError):
        read_history(path)


def test_a_trailing_newline_in_a_plan_filter_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    history = read_history(path)
    for platform, metric, before in (
        ("youtube\n", None, None),
        ("youtube", "subscribers\n", None),
        ("youtube", None, "2026-10-03\n"),
    ):
        with pytest.raises(HistoryError):
            plan_redaction(
                history,
                platform=platform,
                source="api",
                before=before,
                metric=metric,
            )


def test_a_stale_name_with_a_trailing_newline_is_not_a_leftover(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    stale_pair(tmp_path)
    (tmp_path / ".h.jsonl.redact-0a1b2c3d.tmp").rename(
        tmp_path / ".h.jsonl.redact-0a1b2c3d.tmp\n"
    )
    # Only the marker, now an orphan, is listed; the newline name is no leftover.
    assert find_stale_redaction_temps(path) == [".h.jsonl.redact-0a1b2c3d.mark"]
    assert remove_stale_redaction_temps(path, [".h.jsonl.redact-0a1b2c3d.tmp\n"]) == 0
    assert (tmp_path / ".h.jsonl.redact-0a1b2c3d.tmp\n").exists()


# 6. a folder that cannot be opened is a HistoryError, not a traceback


def test_find_and_remove_report_a_missing_folder_as_a_history_error(
    tmp_path: Path,
) -> None:
    gone = tmp_path / "nope" / "h.jsonl"
    with pytest.raises(HistoryError):
        find_stale_redaction_temps(gone)
    with pytest.raises(HistoryError):
        remove_stale_redaction_temps(gone, [])
    with pytest.raises(HistoryError):
        hist.find_unmarked_redaction_temps(gone)


def test_the_dry_run_counts_unmarked_leftovers_and_never_removes_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    make_temps(tmp_path)  # one old .redact-*.tmp with no marker, two with one
    before = names(tmp_path)
    assert hist.find_unmarked_redaction_temps(path) == 1
    assert main(cli_args(path)) == 0
    out = capsys.readouterr().out
    assert "1 file(s) named like a redaction temporary have no marker" in out
    assert ".h.jsonl.redact-55555555" not in out  # counts only, no names
    assert names(tmp_path) == before
    assert main(cli_args(path, "--confirm")) == 0
    assert ".h.jsonl.redact-55555555.tmp" in names(tmp_path)  # never removed


# 7. the help names exit 5


def test_the_redact_help_names_exit_5(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["counts", "history-redact", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "5 the file was replaced" in text
