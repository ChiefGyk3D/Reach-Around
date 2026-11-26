# SPDX-License-Identifier: Apache-2.0
"""`counts history-redact --derived` writes the history file and its manifest as
one recoverable pair (issue #619): every manifest condition is checked and the
manifest bytes are written to a private temporary file BEFORE the history file
is touched, the folder is opened without following a symlink, and a failure of
the last step names the exact recovery action."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.commands import counts as counts_cmd
from reach_around.counts.history import (
    HistoryError,
    Record,
    append_records,
    plan_redaction,
    read_history,
    rewrite_metadata,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)
SECRET = 424242424


def build(path: Path) -> None:
    for n in range(1, 31):
        record = Record(
            platform="youtube",
            metric="subscribers",
            value=SECRET if n == 1 else 50000 + n,
            as_of_utc=f"2026-10-{n:02d}T09:00:00Z",
            source="api",
            run_id="run-1",
        )
        append_records(path, [record], now=NOW)


def argv(path: Path, *extra: str) -> list[str]:
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
        "--confirm",
        "--derived",
        *extra,
    ]


@pytest.fixture
def history(tmp_path: Path) -> Path:
    folder = tmp_path / "history"
    folder.mkdir()
    path = folder / "social.jsonl"
    build(path)
    return path


def manifest_name(path: Path) -> str:
    plan = plan_redaction(
        read_history(path), platform="youtube", source="api", before="2026-10-03"
    )
    run_id, _, _ = rewrite_metadata(
        [r.key for r in plan],
        "platform_expiry",
        "2026-10-20",
        datetime.now(UTC).strftime("%Y-%m-%d"),
    )
    return f"{run_id}.manifest"


def test_the_pair_is_written_and_nothing_else_is_left(history: Path) -> None:
    expected = manifest_name(history)
    assert main(argv(history)) == 0
    folder = history.parent / "redactions"
    assert [p.name for p in folder.iterdir()] == [expected]
    assert (folder / expected).stat().st_mode & 0o777 == 0o644
    assert str(SECRET) not in (folder / expected).read_text(encoding="ascii")


def test_an_existing_manifest_stops_the_run_before_the_history_is_touched(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = history.parent / "redactions"
    folder.mkdir()
    name = manifest_name(history)
    (folder / name).write_text("already here", encoding="ascii")
    before = history.read_bytes()
    assert main(argv(history)) == 1
    assert history.read_bytes() == before  # the failure came before the rewrite
    assert (folder / name).read_text() == "already here"
    assert [p.name for p in folder.iterdir()] == [name]
    assert "nothing was written" in capsys.readouterr().err


def test_a_symlinked_redactions_folder_is_refused_and_nothing_lands_outside(
    history: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (history.parent / "redactions").symlink_to(outside, target_is_directory=True)
    before = history.read_bytes()
    assert main(argv(history)) == 1
    assert history.read_bytes() == before
    assert list(outside.iterdir()) == []


def test_a_redactions_path_that_is_a_file_is_refused_before_the_rewrite(
    history: Path,
) -> None:
    (history.parent / "redactions").write_text("not a folder", encoding="ascii")
    before = history.read_bytes()
    assert main(argv(history)) == 1
    assert history.read_bytes() == before


def test_a_failure_to_create_the_manifest_leaves_the_history_unchanged(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = os.open

    def deny(path: Any, flags: int, *a: Any, **k: Any) -> int:
        if flags & os.O_EXCL and ".manifest" in str(path):
            raise PermissionError("no")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", deny)
    before = history.read_bytes()
    assert main(argv(history)) == 1
    assert history.read_bytes() == before
    folder = history.parent / "redactions"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_failed_rewrite_removes_the_staged_manifest(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*a: Any, **k: Any) -> int:
        raise HistoryError("no")

    monkeypatch.setattr(counts_cmd, "redact_records", fail)
    before = history.read_bytes()
    assert main(argv(history)) == 1
    assert history.read_bytes() == before
    assert list((history.parent / "redactions").iterdir()) == []


def test_a_failure_of_the_last_step_names_the_recovery_and_keeps_the_manifest(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(self: Any) -> None:
        raise OSError("no")

    monkeypatch.setattr(counts_cmd._StagedManifest, "install", refuse)
    before = history.read_bytes()
    final = manifest_name(history)
    assert main(argv(history)) == 5
    assert history.read_bytes() != before  # the rewrite happened
    err = capsys.readouterr().err
    folder = history.parent / "redactions"
    left = [p.name for p in folder.iterdir()]
    assert len(left) == 1 and left[0].startswith(".")
    assert f"mv {left[0]} {final}" in err
    assert str(SECRET) not in err
    # Following the printed step completes the pair.
    (folder / left[0]).rename(folder / final)
    assert [p.name for p in folder.iterdir()] == [final]
