# SPDX-License-Identifier: Apache-2.0
"""Concurrency, backup and durability behavior of the edit commands."""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from reach_around import editing
from reach_around.cli import main
from reach_around.config import load_site_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "instance" / "site.yaml"


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    path.write_text(EXAMPLE.read_text(), encoding="utf-8")
    return path


def set_mode(path: Path) -> int:
    return main(["set", "--config", str(path), "theme.mode", "dark"])


def change_in_place_longer(path: Path) -> None:
    path.write_text(path.read_text() + "# someone else was here\n")


def change_in_place_same_size(path: Path) -> None:
    data = path.read_bytes()
    stat_before = path.stat()
    path.write_bytes(data.replace(b"Example Creator", b"Exampel Creator", 1))
    os.utime(path, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))


def swap_for_identical_new_inode(path: Path) -> None:
    other = path.with_name("other.tmp")
    other.write_bytes(path.read_bytes())
    os.replace(other, path)


@pytest.mark.parametrize(
    "change",
    [change_in_place_longer, change_in_place_same_size, swap_for_identical_new_inode],
)
def test_a_change_during_the_edit_is_refused(
    cfg: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[Path], None],
) -> None:
    (cfg.parent / "site.yaml.bak").write_text("old backup\n")
    monkeypatch.setattr(editing, "_before_replace", lambda path: change(path))
    assert set_mode(cfg) == 1
    err = capsys.readouterr().err
    assert "changed while editing" in err and "nothing was written" in err
    assert "dark" not in cfg.read_text()
    assert (cfg.parent / "site.yaml.bak").read_text() == "old backup\n"
    assert sorted(p.name for p in cfg.parent.iterdir()) == [
        "site.yaml",
        "site.yaml.bak",
    ]


def test_two_concurrent_edits_both_land(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = threading.Event()
    real = editing._before_replace

    def slow(path: Path) -> None:
        # the first command to get here holds the lock while the other waits
        if not first.is_set():
            first.set()
            time.sleep(0.4)
        real(path)

    monkeypatch.setattr(editing, "_before_replace", slow)

    def add(title: str) -> int:
        return main(
            ["add-link", "--config", str(cfg), "--tab", "events"]
            + ["--title", title, "--url", "https://e.com/"]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(add, "Alpha")
        first.wait(5)
        two = pool.submit(add, "Beta")
        assert (one.result(10), two.result(10)) == (0, 0)
    (tab,) = [t for t in load_site_config(cfg).tabs if t.id == "events"]
    assert {b.id for b in tab.blocks} >= {"alpha", "beta"}


def test_a_failed_backup_leaves_the_previous_backup_and_the_original(
    cfg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backup = cfg.parent / "site.yaml.bak"
    backup.write_text("previous backup\n")
    before = cfg.read_bytes()
    real = os.replace

    def flaky(src: str | Path, dst: str | Path) -> None:
        if str(dst).endswith(".bak"):
            raise OSError(28, "No space left on device")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    assert set_mode(cfg) == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err
    assert backup.read_text() == "previous backup\n"
    assert cfg.read_bytes() == before
    assert sorted(p.name for p in cfg.parent.iterdir()) == [
        "site.yaml",
        "site.yaml.bak",
    ]


def test_the_backup_is_replaced_not_written_through_a_symlink(cfg: Path) -> None:
    victim = cfg.parent / "victim.txt"
    victim.write_text("keep me\n")
    (cfg.parent / "site.yaml.bak").symlink_to(victim)
    assert set_mode(cfg) == 0
    assert victim.read_text() == "keep me\n"
    backup = cfg.parent / "site.yaml.bak"
    assert not backup.is_symlink()
    assert backup.read_bytes() == EXAMPLE.read_bytes()


def test_renames_are_followed_by_a_directory_fsync(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_replace = os.replace
    real_fsync = os.fsync

    def replace(src: str | Path, dst: str | Path) -> None:
        real_replace(src, dst)
        events.append(f"replace:{Path(dst).name}")

    def fsync(fd: int) -> None:
        real_fsync(fd)
        events.append("fsync-dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync")

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", fsync)
    assert set_mode(cfg) == 0
    seen = [e for e in events if e != "fsync"]
    assert seen == [
        "replace:site.yaml.bak",
        "fsync-dir",
        "replace:site.yaml",
        "fsync-dir",
    ]
    assert events.count("fsync") >= 2  # the temp files themselves


def test_a_failed_directory_fsync_is_reported(
    cfg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "Input/output error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    assert set_mode(cfg) == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err
