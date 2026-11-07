# SPDX-License-Identifier: Apache-2.0
"""A refused save puts the backup back without touching anyone else's entry.

The rollback moves the backup name aside first, looks at what it moved, and
only then removes or restores. An entry another process put under the name is
moved back with a no-replace rename, never deleted and never overwritten. The
seams ``_before_aside`` and ``_after_aside`` let a test play that other
process at the two moments a check-then-act rollback would have been fooled.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from editkit import CONFIG as CONFIG_TEXT
from reach_around import editing


def make_real(tmp_path: Path) -> Path:
    real = tmp_path / "real"
    real.mkdir()
    cfg = real / "site.yaml"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    return cfg


def append_note(text: str, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return text + "# x\n", dict(raw)


def save(cfg: Path) -> editing.Result:
    return editing.apply(
        cfg,
        append_note,
        dry_run=False,
        backup=True,
        force_path=True,
        no_symlinks=True,
    )


def contents(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()}


class Rival:
    """Plays the other process: steals the backup, plants its own entry."""

    def __init__(self, cfg: Path) -> None:
        self.bak = cfg.parent / "site.yaml.bak"
        self.cfg = cfg
        self.fired: list[str] = []

    def refuse_the_save(self, path: Path) -> None:
        # The change that makes the save fail its final check.
        self.cfg.write_text(CONFIG_TEXT + "# elsewhere\n", encoding="utf-8")

    def steal_and_plant(self, text: str) -> None:
        self.bak.rename(self.bak.with_name("stolen"))
        self.bak.write_text(text, encoding="utf-8")
        self.fired.append(text)

    def plant(self, text: str) -> None:
        self.bak.write_text(text, encoding="utf-8")
        self.fired.append(text)


@pytest.fixture(params=["renameat2", "link"])
def noreplace_mode(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run each test with the kernel call and again with the link fallback."""
    if request.param == "link":
        monkeypatch.setattr(editing, "_RENAMEAT2", None)
    return str(request.param)


# ---- no previous backup: the backup this save made is rolled back ----


def test_a_rollback_removes_the_backup_this_save_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    cfg = make_real(tmp_path)
    rival = Rival(cfg)
    monkeypatch.setattr(editing, "_before_replace", rival.refuse_the_save)
    with pytest.raises(editing.EditError):
        save(cfg)
    assert sorted(contents(cfg.parent)) == ["site.yaml"]


def test_a_backup_replaced_just_before_the_rollback_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    cfg = make_real(tmp_path)
    rival = Rival(cfg)

    def refuse_then_steal(path: Path) -> None:
        rival.refuse_the_save(path)

    monkeypatch.setattr(editing, "_before_replace", refuse_then_steal)
    monkeypatch.setattr(
        editing, "_before_aside", lambda path: rival.steal_and_plant("rival\n")
    )
    with pytest.raises(editing.EditError):
        save(cfg)
    assert rival.fired, "the race seam did not run"
    assert contents(cfg.parent)["site.yaml.bak"] == "rival\n"
    # the entry the rival moved away is still its own; nothing else was added
    assert sorted(contents(cfg.parent)) == ["site.yaml", "site.yaml.bak", "stolen"]


def test_a_name_taken_after_the_backup_moved_aside_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    cfg = make_real(tmp_path)
    rival = Rival(cfg)
    monkeypatch.setattr(editing, "_before_replace", rival.refuse_the_save)
    monkeypatch.setattr(editing, "_after_aside", lambda *a: rival.plant("rival\n"))
    with pytest.raises(editing.EditError):
        save(cfg)
    assert rival.fired, "the race seam did not run"
    assert contents(cfg.parent) == {
        "site.yaml": CONFIG_TEXT + "# elsewhere\n",
        "site.yaml.bak": "rival\n",
    }


def test_a_foreign_entry_is_not_restored_over_a_newer_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    """Two rivals: the second takes the name while the first's entry is aside."""
    cfg = make_real(tmp_path)
    rival = Rival(cfg)
    monkeypatch.setattr(editing, "_before_replace", rival.refuse_the_save)
    monkeypatch.setattr(
        editing, "_before_aside", lambda path: rival.steal_and_plant("first\n")
    )
    monkeypatch.setattr(editing, "_after_aside", lambda *a: rival.plant("second\n"))
    with pytest.raises(editing.EditError):
        save(cfg)
    assert rival.fired == ["first\n", "second\n"]
    held = contents(cfg.parent)
    assert held["site.yaml.bak"] == "second\n"
    assert "first\n" in held.values()  # moved aside, not deleted


# ---- a previous backup: it is put back, never over a foreign entry ----


def with_previous(tmp_path: Path) -> Path:
    cfg = make_real(tmp_path)
    (cfg.parent / "site.yaml.bak").write_text("old backup\n", encoding="utf-8")
    return cfg


def test_the_previous_backup_is_put_back_after_a_refused_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    cfg = with_previous(tmp_path)
    rival = Rival(cfg)
    monkeypatch.setattr(editing, "_before_replace", rival.refuse_the_save)
    with pytest.raises(editing.EditError):
        save(cfg)
    assert contents(cfg.parent) == {
        "site.yaml": CONFIG_TEXT + "# elsewhere\n",
        "site.yaml.bak": "old backup\n",
    }


def test_a_backup_replaced_just_before_the_restore_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    cfg = with_previous(tmp_path)
    rival = Rival(cfg)
    monkeypatch.setattr(editing, "_before_replace", rival.refuse_the_save)
    monkeypatch.setattr(
        editing, "_before_aside", lambda path: rival.steal_and_plant("rival\n")
    )
    with pytest.raises(editing.EditError):
        save(cfg)
    assert rival.fired, "the race seam did not run"
    held = contents(cfg.parent)
    assert held["site.yaml.bak"] == "rival\n"
    assert "old backup\n" in held.values()  # the previous backup is not lost


def test_a_name_taken_while_the_previous_backup_is_restored_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noreplace_mode: str
) -> None:
    cfg = with_previous(tmp_path)
    rival = Rival(cfg)
    monkeypatch.setattr(editing, "_before_replace", rival.refuse_the_save)
    monkeypatch.setattr(editing, "_after_aside", lambda *a: rival.plant("rival\n"))
    with pytest.raises(editing.EditError):
        save(cfg)
    assert rival.fired, "the race seam did not run"
    held = contents(cfg.parent)
    assert held["site.yaml.bak"] == "rival\n"
    assert "old backup\n" in held.values()


# ---- the no-replace rename itself ----


@pytest.mark.parametrize("by_descriptor", [True, False])
def test_no_replace_rename_moves_to_a_free_name(
    tmp_path: Path, noreplace_mode: str, by_descriptor: bool
) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        editing._rename_noreplace(
            tmp_path / "a", tmp_path / "b", fd if by_descriptor else None
        )
    finally:
        os.close(fd)
    assert contents(tmp_path) == {"b": "a"}


@pytest.mark.parametrize("by_descriptor", [True, False])
def test_no_replace_rename_refuses_to_replace(
    tmp_path: Path, noreplace_mode: str, by_descriptor: bool
) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FileExistsError):
            editing._rename_noreplace(
                tmp_path / "a", tmp_path / "b", fd if by_descriptor else None
            )
    finally:
        os.close(fd)
    assert contents(tmp_path) == {"a": "a", "b": "b"}
