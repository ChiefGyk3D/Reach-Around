# SPDX-License-Identifier: Apache-2.0
"""Hardening of the tombstone rewrite (issue #586, review round 1)."""

import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

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


def build(path: Path, **first: Any) -> None:
    append_records(
        path,
        [
            rec(**first),
            rec(as_of_utc="2026-10-02T09:00:00Z", value=11),
            rec(as_of_utc="2026-10-03T09:00:00Z", value=12),
        ],
        now=NOW,
    )


def plan_for(path: Path) -> list[Record]:
    return plan_redaction(
        read_history(path), platform="youtube", source="api", before="2026-10-03"
    )


KW: dict[str, Any] = {
    "reason": "platform_expiry",
    "trigger_date": "2026-10-20",
    "now": REDACT_AT,
    "confirm": True,
}


# 1. a symlink anywhere in the folder chain is refused


def test_a_symlinked_ancestor_is_refused_and_nothing_outside_is_written(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    build(real / "sub" / "h.jsonl")
    plan = plan_for(real / "sub" / "h.jsonl")
    before = (real / "sub" / "h.jsonl").read_bytes()
    (tmp_path / "alias").symlink_to(real)
    via = tmp_path / "alias" / "sub" / "h.jsonl"
    with pytest.raises(HistoryError, match="symlink"):
        redact_records(via, plan, **KW)
    with pytest.raises(HistoryError, match="symlink"):
        find_stale_redaction_temps(via)
    with pytest.raises(HistoryError, match="symlink"):
        remove_stale_redaction_temps(via, [".h.jsonl.redact-0a1b2c3d.tmp"])
    assert (real / "sub" / "h.jsonl").read_bytes() == before
    assert sorted(os.listdir(real / "sub")) == ["h.jsonl"]


def test_read_append_and_verify_still_work_through_a_symlinked_ancestor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    build(real / "sub" / "h.jsonl")
    (tmp_path / "alias").symlink_to(real)
    via = tmp_path / "alias" / "sub" / "h.jsonl"
    assert len(read_history(via).records) == 3
    append_records(via, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert main(["counts", "history-verify", "--file", str(via)]) == 0
    assert "4 records" in capsys.readouterr().out
    # The last folder itself as a symlink was refused before and still is.
    (tmp_path / "linkdir").symlink_to(real / "sub")
    with pytest.raises(HistoryError):
        read_history(tmp_path / "linkdir" / "h.jsonl")


def test_the_cli_redact_refuses_a_symlinked_ancestor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    build(real / "sub" / "h.jsonl")
    (tmp_path / "alias").symlink_to(real)
    before = (real / "sub" / "h.jsonl").read_bytes()
    via = tmp_path / "alias" / "sub" / "h.jsonl"
    assert main(cli_args(via)) == 1  # the dry run refuses too
    assert main(cli_args(via, "--confirm")) == 1
    assert "symlink" in capsys.readouterr().err
    assert (real / "sub" / "h.jsonl").read_bytes() == before


def test_a_symlink_in_the_middle_of_a_relative_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real" / "sub"
    real.mkdir(parents=True)
    build(real / "h.jsonl")
    plan = plan_for(real / "h.jsonl")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HistoryError, match="symlink"):
        redact_records(Path("alias/sub/h.jsonl"), plan, **KW)
    assert len(tombstoned(read_history(real / "h.jsonl"))) == 0


def test_plain_relative_and_absolute_paths_still_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    build(tmp_path / "a" / "b" / "h.jsonl")
    monkeypatch.chdir(tmp_path)
    assert read_history(Path("a/b/h.jsonl")).records
    assert read_history(Path("./a/../a/b/h.jsonl")).records
    monkeypatch.chdir(tmp_path / "a" / "b")
    assert redact_records(Path("h.jsonl"), plan_for(Path("h.jsonl")), **KW) == 2
    assert len(tombstoned(read_history(tmp_path / "a" / "b" / "h.jsonl"))) == 2


# 2. the plan must match the file in full


@pytest.mark.parametrize(
    "changed",
    [
        {"value": 999},
        {"run_id": "other-run"},
        {"era": "changed-definition"},
        {"api_provider": "youtube_data_api"},
        {"note": "added later"},
    ],
)
def test_same_key_with_other_content_is_a_stale_plan(
    tmp_path: Path, changed: dict[str, Any]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    path.unlink()
    build(path, **changed)  # same keys, one row altered
    before = path.read_bytes()
    with pytest.raises(HistoryError, match="plan"):
        redact_records(path, plan, **KW)
    assert path.read_bytes() == before
    assert not tombstoned(read_history(path))


def test_duplicates_in_the_plan_are_refused_not_collapsed(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    before = path.read_bytes()
    with pytest.raises(HistoryError, match="duplicate"):
        redact_records(path, [*plan, plan[0]], **KW)
    assert path.read_bytes() == before


# 3. a failure after the rename is its own outcome


def fail_directory_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.fsync

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("no")
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)


def test_a_folder_fsync_failure_is_reported_as_completed_but_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)
    fail_directory_fsync(monkeypatch)
    with pytest.raises(RedactDurabilityError, match="replaced the file"):
        redact_records(path, plan, **KW)
    monkeypatch.undo()
    assert len(tombstoned(read_history(path))) == 2  # the new file is installed
    assert sorted(os.listdir(tmp_path)) == ["h.jsonl"]
    assert "value" not in path.read_text(encoding="ascii").splitlines()[0]


def test_cli_prints_fixed_words_and_a_distinct_exit_code_for_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    fail_directory_fsync(monkeypatch)
    code = main(cli_args(path, "--confirm"))
    monkeypatch.undo()
    err = capsys.readouterr().err
    assert code == 5
    assert "the file WAS replaced" in err and "history-verify" in err
    assert len(tombstoned(read_history(path))) == 2


def test_a_failure_before_the_rename_is_a_plain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("no")

    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(HistoryError) as err:
        redact_records(path, plan, **KW)
    assert not isinstance(err.value, RedactDurabilityError)


# 4. owner and group are kept where permitted


def test_owner_and_group_are_carried_to_the_new_file(tmp_path: Path) -> None:
    other = [g for g in os.getgroups() if g != os.getegid()]
    if not other:
        pytest.skip("no second group to test group preservation with")
    path = tmp_path / "h.jsonl"
    build(path)
    os.chown(path, -1, other[0])
    plan = plan_for(path)
    notices: list[str] = []
    redact_records(path, plan, notices=notices, **KW)
    info = path.stat()
    assert (info.st_uid, info.st_gid) == (os.getuid(), other[0])
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert notices == []


def test_a_refused_chown_continues_with_a_fixed_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    plan = plan_for(path)

    def deny(*a: Any, **k: Any) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(os, "fchown", deny)
    notices: list[str] = []
    assert redact_records(path, plan, notices=notices, **KW) == 2
    monkeypatch.undo()
    assert notices == [hist.NOTICE_OWNER_NOT_KEPT]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(tombstoned(read_history(path))) == 2


def test_the_cli_prints_the_owner_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)

    def deny(*a: Any, **k: Any) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(os, "fchown", deny)
    assert main(cli_args(path, "--confirm")) == 0
    assert hist.NOTICE_OWNER_NOT_KEPT in capsys.readouterr().out


# 5. stale temporaries from a crashed redaction


def cli_args(path: Path, *extra: str) -> list[str]:
    return [
        "counts",
        "history-redact",
        "--file",
        str(path),
        "--platform",
        "youtube",
        "--source",
        "api",
        "--before",
        "2026-10-03",
        "--reason",
        "platform_expiry",
        "--trigger-date",
        "2026-10-20",
        *extra,
    ]


def make_temps(tmp_path: Path) -> dict[str, str]:
    """Files around h.jsonl: which are stale redaction temporaries and which not."""
    old = time.time() - 7200
    made: dict[str, str] = {}

    def file(name: str, *, age: float = 7200) -> None:
        p = tmp_path / name
        p.write_text("private", encoding="ascii")
        os.utime(p, (time.time() - age, time.time() - age))

    file(".h.jsonl.redact-0a1b2c3d.tmp")  # stale: removed
    made["stale"] = ".h.jsonl.redact-0a1b2c3d.tmp"
    file(".h.jsonl.redact-ffffffff.tmp", age=5)  # young: may be running now
    file(".other.jsonl.redact-0a1b2c3d.tmp")  # another file's pattern
    file(".h.jsonl.redact-0a1b2c3d.tmp.bak")
    file(".h.jsonl.redact-XYZ.tmp")
    file("h.jsonl.redact-0a1b2c3d.tmp")  # no leading dot
    (tmp_path / ".h.jsonl.redact-11111111.tmp").mkdir()  # a directory
    (tmp_path / "target").write_text("x", encoding="ascii")
    (tmp_path / ".h.jsonl.redact-22222222.tmp").symlink_to(tmp_path / "target")
    os.utime(tmp_path / "target", (old, old))
    os.utime(tmp_path / ".h.jsonl.redact-11111111.tmp", (old, old))
    os.utime(
        tmp_path / ".h.jsonl.redact-22222222.tmp",
        (old, old),
        follow_symlinks=False,
    )
    return made


KEEP = [
    ".h.jsonl.redact-ffffffff.tmp",
    ".other.jsonl.redact-0a1b2c3d.tmp",
    ".h.jsonl.redact-0a1b2c3d.tmp.bak",
    ".h.jsonl.redact-XYZ.tmp",
    "h.jsonl.redact-0a1b2c3d.tmp",
    ".h.jsonl.redact-11111111.tmp",
    ".h.jsonl.redact-22222222.tmp",
    "target",
    "h.jsonl",
]


def test_only_old_regular_own_matching_temporaries_are_found_and_removed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    make_temps(tmp_path)
    found = find_stale_redaction_temps(path)
    assert found == [".h.jsonl.redact-0a1b2c3d.tmp"]
    assert remove_stale_redaction_temps(path, found) == 1
    assert sorted(os.listdir(tmp_path)) == sorted(KEEP)
    assert find_stale_redaction_temps(path) == []


def test_a_name_that_is_not_a_stale_temporary_is_never_removed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    make_temps(tmp_path)
    asked = [*KEEP, "../h.jsonl", "sub/x", ".h.jsonl.redact-0a1b2c3d.tmp"]
    assert remove_stale_redaction_temps(path, asked) == 1
    assert sorted(os.listdir(tmp_path)) == sorted(KEEP)


def test_a_temporary_of_another_owner_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    make_temps(tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 1)
    assert find_stale_redaction_temps(path) == []
    assert remove_stale_redaction_temps(path, [".h.jsonl.redact-0a1b2c3d.tmp"]) == 0
    assert (tmp_path / ".h.jsonl.redact-0a1b2c3d.tmp").exists()


def test_dry_run_lists_temporaries_by_count_and_name_and_removes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    make_temps(tmp_path)
    before = sorted(os.listdir(tmp_path))
    assert main(cli_args(path)) == 0
    out = capsys.readouterr().out
    assert "1 stale redaction temporary file" in out
    assert ".h.jsonl.redact-0a1b2c3d.tmp" in out
    assert "private" not in out
    assert sorted(os.listdir(tmp_path)) == before


def test_confirm_removes_them_even_when_there_is_nothing_to_redact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    make_temps(tmp_path)
    assert main(cli_args(path, "--confirm")) == 0
    assert main(cli_args(path, "--confirm")) == 0  # second run: nothing to redact
    out = capsys.readouterr().out
    assert "removed 1 stale redaction temporary file" in out
    assert sorted(os.listdir(tmp_path)) == sorted(KEEP)
    assert len(tombstoned(read_history(path))) == 2
