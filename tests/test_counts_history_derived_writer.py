# SPDX-License-Identifier: Apache-2.0
"""`counts history-redact --derived` writes the history file and its manifest as
one recoverable pair (issue #619): every manifest condition is checked and the
manifest bytes are written to a private temporary file BEFORE the history file
is touched, the folder is opened without following a symlink, and a failure of
the last step names the exact recovery action."""

from __future__ import annotations

import os
import stat
import subprocess
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
    # Changed: the printed step must never be a plain mv (it can overwrite).
    assert f"ln {left[0]} {final} && rm {left[0]}" in err
    assert "mv " not in err
    assert str(SECRET) not in err
    # Following the printed step completes the pair.
    run_printed_step(err, folder)
    assert [p.name for p in folder.iterdir()] == [final]


def run_printed_step(err: str, folder: Path) -> subprocess.CompletedProcess[str]:
    line = next(x for x in err.splitlines() if "then run: " in x)
    command = line.split("then run: ", 1)[1]
    return subprocess.run(  # noqa: S603 - fixed argv, command built by the code under test
        ["sh", "-c", command],  # noqa: S607
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_existing_final_manifest_at_install_time_is_never_overwritten(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    final = manifest_name(history)
    folder = history.parent / "redactions"
    real = getattr(counts_cmd, "redact_records")  # noqa: B009 - not re-exported

    def racing(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        (folder / final).write_text("made by someone else", encoding="ascii")
        return out

    monkeypatch.setattr(counts_cmd, "redact_records", racing)
    before = history.read_bytes()
    assert main(argv(history)) == 5
    assert history.read_bytes() != before  # rewritten, as designed
    err = capsys.readouterr().err
    assert (folder / final).read_text() == "made by someone else"
    tmp = next(p.name for p in folder.iterdir() if p.name.startswith("."))
    assert f"ln {tmp} {final} && rm {tmp}" in err and "cmp" in err
    assert "mv " not in err
    done = run_printed_step(err, folder)
    assert done.returncode != 0  # ln refuses to replace the existing file
    assert (folder / final).read_text() == "made by someone else"
    assert (folder / tmp).exists()  # nothing was removed either


class FsyncLog:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        real_fsync = os.fsync
        real_redact = getattr(counts_cmd, "redact_records")  # noqa: B009

        def fsync(fd: int) -> None:
            self.events.append("fsync:" + os.readlink(f"/proc/self/fd/{fd}"))
            real_fsync(fd)

        def redact(*a: Any, **k: Any) -> Any:
            self.events.append("redact")
            return real_redact(*a, **k)

        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(counts_cmd, "redact_records", redact)

    def before_redact(self) -> list[str]:
        return self.events[: self.events.index("redact")]


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_the_manifest_and_a_new_folder_are_durable_before_the_history_is_replaced(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = FsyncLog(monkeypatch)
    assert main(argv(history)) == 0
    parent = history.parent.resolve()
    kinds = []
    for e in log.before_redact():
        path = Path(e.split(":", 1)[1])
        kinds.append(
            "history-dir"
            if path == parent
            else "redactions"
            if path.name == "redactions"
            else "manifest"
        )
    # The parent of the new folder, then the temporary manifest, then its folder.
    assert kinds == ["history-dir", "manifest", "redactions"]


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_an_existing_folder_needs_no_parent_sync_but_the_folder_is_synced(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (history.parent / "redactions").mkdir()
    log = FsyncLog(monkeypatch)
    assert main(argv(history)) == 0
    kinds = [
        "redactions" if e.endswith("redactions") else "manifest"
        for e in log.before_redact()
    ]
    assert kinds == ["manifest", "redactions"]


def test_the_fsync_calls_come_in_order_without_needing_proc(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order of the fsync calls, by descriptor kind (works without /proc)."""
    events: list[str] = []
    inodes: list[int] = []
    real_fsync = os.fsync
    real_redact = getattr(counts_cmd, "redact_records")  # noqa: B009

    def fsync(fd: int) -> None:
        info = os.fstat(fd)
        if "redact" not in events:
            inodes.append(info.st_ino)
        events.append("dir" if stat.S_ISDIR(info.st_mode) else "file")
        real_fsync(fd)

    def redact(*a: Any, **k: Any) -> Any:
        events.append("redact")
        return real_redact(*a, **k)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(counts_cmd, "redact_records", redact)
    assert main(argv(history)) == 0
    # Parent of the new folder, the temporary manifest, then the folder itself.
    assert events[: events.index("redact")] == ["dir", "file", "dir"]
    # The two directories are the history folder and the redactions folder, by
    # inode (an unrelated directory fsync cannot satisfy this).
    parent_ino = history.parent.stat().st_ino
    folder_ino = (history.parent / "redactions").stat().st_ino
    assert parent_ino != folder_ino
    assert inodes[0] == parent_ino
    assert inodes[2] == folder_ino


class InstallProbe:
    """Fail one operation of install() after it ran, and record the folder fd."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, step: str, failure: BaseException
    ) -> None:
        self.active = False
        self.folder_fd = -1
        self.calls = {"link": 0, "unlink": 0, "fsync": 0}
        real_install = counts_cmd._StagedManifest.install
        real: dict[str, Any] = {
            "link": os.link,
            "unlink": os.unlink,
            "fsync": os.fsync,
        }
        probe = self

        def install(staged: Any) -> None:
            probe.folder_fd = staged.folder_fd
            probe.active = True
            try:
                real_install(staged)
            finally:
                probe.active = False

        def wrap(name: str) -> Any:
            def call(*a: Any, **k: Any) -> Any:
                if not probe.active:
                    return real[name](*a, **k)
                probe.calls[name] += 1
                out = real[name](*a, **k)  # the operation completes first
                if name == step:
                    raise failure
                return out

            return call

        monkeypatch.setattr(counts_cmd._StagedManifest, "install", install)
        for name in real:
            monkeypatch.setattr(os, name, wrap(name))


def assert_closed(fd: int) -> None:
    with pytest.raises(OSError) as info:
        os.fstat(fd)
    assert info.value.errno == 9  # EBADF: the folder descriptor was closed


@pytest.mark.parametrize("failure_kind", ["interrupt", "oserror"])
@pytest.mark.parametrize("step", ["link", "unlink", "fsync"])
def test_a_stop_during_installation_prints_guidance_for_the_state_reached(
    history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    step: str,
    failure_kind: str,
) -> None:
    failure: BaseException = (
        KeyboardInterrupt() if failure_kind == "interrupt" else OSError(5, "boom")
    )
    probe = InstallProbe(monkeypatch, step, failure)
    before = history.read_bytes()
    final = manifest_name(history)
    folder = history.parent / "redactions"
    if failure_kind == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            main(argv(history))
    else:
        assert main(argv(history)) == 5
    assert history.read_bytes() != before  # the rewrite stays
    assert probe.calls[step] == 1
    assert_closed(probe.folder_fd)
    err = capsys.readouterr().err
    assert str(SECRET) not in err and "mv " not in err
    names = sorted(p.name for p in folder.iterdir())
    assert "ln " not in err and "could not be installed" not in err
    if step == "link":
        # Both names exist: the printed step checks them and removes the temp.
        assert len(names) == 2 and final in names
        assert "cmp" in err and "identical" in err
        assert run_printed_step(err, folder).returncode == 0
        assert [p.name for p in folder.iterdir()] == [final]
    else:
        # Only the final name exists: nothing to link, nothing to remove.
        assert names == [final]
        assert "installed as" in err and "nothing else to do" in err
        assert "sync" in err and "history-verify" in err
        assert "then run: " not in err


@pytest.mark.parametrize("failure_kind", ["interrupt", "oserror"])
def test_a_stop_before_the_link_prints_the_original_recovery_and_closes(
    history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_kind: str,
) -> None:
    active = False
    seen: list[int] = []
    real_install = counts_cmd._StagedManifest.install

    def install(staged: Any) -> None:
        nonlocal active
        seen.append(staged.folder_fd)
        active = True
        try:
            real_install(staged)
        finally:
            active = False

    real_link = os.link

    def refuse(*a: Any, **k: Any) -> None:
        if not active:
            real_link(*a, **k)
            return
        raise KeyboardInterrupt() if failure_kind == "interrupt" else OSError(5, "x")

    monkeypatch.setattr(counts_cmd._StagedManifest, "install", install)
    monkeypatch.setattr(os, "link", refuse)
    final = manifest_name(history)
    folder = history.parent / "redactions"
    if failure_kind == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            main(argv(history))
    else:
        assert main(argv(history)) == 5
    assert_closed(seen[0])
    err = capsys.readouterr().err
    tmp = next(p.name for p in folder.iterdir() if p.name.startswith("."))
    assert f"ln {tmp} {final} && rm {tmp}" in err
    run_printed_step(err, folder)
    assert [p.name for p in folder.iterdir()] == [final]


def replace_then_stop(monkeypatch: pytest.MonkeyPatch, stop: BaseException) -> None:
    """The history file really is replaced, then the run is stopped."""
    real_redact = getattr(counts_cmd, "redact_records")  # noqa: B009

    def redact(*a: Any, **k: Any) -> Any:
        real_redact(*a, **k)
        raise stop

    monkeypatch.setattr(counts_cmd, "redact_records", redact)


@pytest.mark.parametrize("stop", [KeyboardInterrupt(), SystemExit(3)])
def test_an_interrupt_after_the_replacement_keeps_the_manifest_and_names_the_step(
    history: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stop: BaseException,
) -> None:
    replace_then_stop(monkeypatch, stop)
    before = history.read_bytes()
    final = manifest_name(history)
    with pytest.raises(type(stop)):
        main(argv(history))
    assert history.read_bytes() != before  # the rewrite happened
    err = capsys.readouterr().err
    folder = history.parent / "redactions"
    left = [p.name for p in folder.iterdir()]
    assert len(left) == 1 and left[0].startswith(".")  # the only copy is kept
    assert f"ln {left[0]} {final} && rm {left[0]}" in err
    assert "cmp" in err and "mv " not in err
    assert str(SECRET) not in err
    run_printed_step(err, folder)
    assert [p.name for p in folder.iterdir()] == [final]


def test_an_unexpected_error_after_the_replacement_exits_five_and_keeps_it(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_then_stop(monkeypatch, RuntimeError("late"))
    assert main(argv(history)) == 5
    folder = history.parent / "redactions"
    assert [p.name for p in folder.iterdir()][0].startswith(".")
    assert "history-verify" in capsys.readouterr().err


def test_a_stop_before_the_replacement_still_removes_the_staged_manifest(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*a: Any, **k: Any) -> Any:
        raise HistoryError("refused before anything was replaced")

    monkeypatch.setattr(counts_cmd, "redact_records", refuse)
    before = history.read_bytes()
    assert main(argv(history)) == 1
    assert history.read_bytes() == before
    assert list((history.parent / "redactions").iterdir()) == []
