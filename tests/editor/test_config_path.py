# SPDX-License-Identifier: Apache-2.0
"""The editor refuses a config path that crosses a symlink (T-26).

The checks must hold at the point of use, not only at an earlier look at the
path: a save works on a directory descriptor it verified, and every read of
the running editor re-verifies the whole path.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from editkit import CONFIG as CONFIG_TEXT
from reach_around import editing
from reach_around.editor.server import EditorError, EditorSession
from reach_around.editor.service import RouteError

ROOT = os.geteuid() == 0
needs_unprivileged = pytest.mark.skipif(ROOT, reason="root ignores modes")


def make_real(tmp_path: Path) -> Path:
    real = tmp_path / "real"
    real.mkdir()
    cfg = real / "site.yaml"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    return cfg


def make_other(tmp_path: Path, name: str = "other") -> Path:
    """A second, regular config folder that a swapped path could lead to."""
    other = tmp_path / name
    other.mkdir()
    (other / "site.yaml").write_text(CONFIG_TEXT + "# other\n", encoding="utf-8")
    return other


def swap_parent_for_link(parent: Path, other: Path) -> Path:
    """Move ``parent`` aside and leave a symlink to ``other`` under its name."""
    moved = parent.with_name(parent.name + "-moved")
    parent.rename(moved)
    parent.symlink_to(other, target_is_directory=True)
    return moved


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def append_note(text: str, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return text + "# x\n", dict(raw)


def save(cfg: Path, *, backup: bool = True) -> editing.Result:
    return editing.apply(
        cfg,
        append_note,
        dry_run=False,
        backup=backup,
        force_path=True,
        no_symlinks=True,
    )


@contextmanager
def mode(path: Path, bits: int) -> Iterator[None]:
    before = path.stat().st_mode & 0o7777
    path.chmod(bits)
    try:
        yield
    finally:
        path.chmod(before)


# ---- the start-up check ----


def test_a_regular_config_is_accepted(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    session = EditorSession(cfg)
    assert session.config == cfg.resolve()


def test_a_symlinked_config_is_refused_and_untouched(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    link = tmp_path / "link.yaml"
    link.symlink_to(cfg)
    with pytest.raises(EditorError, match="unsafe config path"):
        EditorSession(link)
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEXT


def test_a_symlinked_parent_directory_is_refused(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    (tmp_path / "alias").symlink_to(cfg.parent, target_is_directory=True)
    with pytest.raises(EditorError, match="unsafe config path"):
        EditorSession(tmp_path / "alias" / "site.yaml")


def test_a_dangling_or_missing_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EditorError):
        EditorSession(tmp_path / "nope.yaml")


def test_a_folder_swapped_between_the_check_and_the_first_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    other = make_other(tmp_path)
    real_check = editing.safe_config_path

    def check_then_swap(path: Path) -> Path:
        checked = real_check(path)
        swap_parent_for_link(cfg.parent, other)
        return checked

    monkeypatch.setattr(editing, "safe_config_path", check_then_swap)
    with pytest.raises(EditorError):
        EditorSession(cfg)


# ---- reads of the running editor ----


def test_state_is_refused_after_a_safe_folder_becomes_a_symlink(
    tmp_path: Path,
) -> None:
    cfg = make_real(tmp_path)
    other = make_other(tmp_path)
    session = EditorSession(cfg)
    assert session.service.version()["hash"]
    swap_parent_for_link(cfg.parent, other)
    with pytest.raises(RouteError):
        session.service.version()
    with pytest.raises(RouteError):
        session.service.state("127.0.0.1", 1)


def test_state_is_refused_after_the_config_becomes_a_symlink(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    session = EditorSession(cfg)
    other = make_other(tmp_path)
    cfg.unlink()
    cfg.symlink_to(other / "site.yaml")
    with pytest.raises(RouteError):
        session.service.version()


# ---- saves ----


def test_a_save_in_a_regular_folder_still_works(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    result = save(cfg)
    assert result.written
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEXT + "# x\n"
    assert sorted(p.name for p in cfg.parent.iterdir()) == [
        "site.yaml",
        "site.yaml.bak",
    ]


def test_a_write_refuses_a_config_swapped_for_a_symlink(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    victim = tmp_path / "victim.yaml"
    victim.write_text(CONFIG_TEXT, encoding="utf-8")
    cfg.unlink()
    cfg.symlink_to(victim)
    with pytest.raises(editing.EditError, match="unsafe config path"):
        save(cfg)
    assert victim.read_text(encoding="utf-8") == CONFIG_TEXT
    assert not (tmp_path / "victim.yaml.bak").exists()


def test_a_folder_swapped_before_the_final_check_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    other = make_other(tmp_path)
    moved = cfg.parent.with_name("real-moved")
    monkeypatch.setattr(
        editing, "_before_replace", lambda path: swap_parent_for_link(cfg.parent, other)
    )
    with pytest.raises(editing.EditError):
        save(cfg, backup=False)
    assert (moved / "site.yaml").read_text(encoding="utf-8") == CONFIG_TEXT
    assert sorted(p.name for p in moved.iterdir()) == ["site.yaml"]
    assert (other / "site.yaml").read_text(
        encoding="utf-8"
    ) == CONFIG_TEXT + "# other\n"
    assert sorted(p.name for p in other.iterdir()) == ["site.yaml"]


def test_a_folder_swapped_after_the_final_check_is_never_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window a pathname check cannot close: nothing reaches the new target.

    Whatever the editor stages or renames must go to the folder it verified.
    The other folder holds a decoy for every name the save has made, so a
    rename through the new link would land there and change it.
    """
    cfg = make_real(tmp_path)
    other = make_other(tmp_path)
    real_verify = editing._verify_unchanged
    folder = cfg.parent
    planted: dict[str, bytes] = {}

    def verify_then_swap(*args: Any, **kwargs: Any) -> None:
        real_verify(*args, **kwargs)
        for entry in folder.iterdir():
            if entry.name != "site.yaml":
                (other / entry.name).write_text("DECOY", encoding="utf-8")
        planted.update(tree(other))
        swap_parent_for_link(folder, other)

    monkeypatch.setattr(editing, "_verify_unchanged", verify_then_swap)
    try:
        save(cfg)
    except editing.EditError:
        pass
    assert planted, "the swap seam did not run"
    assert tree(other) == planted
    assert planted["site.yaml"] == (CONFIG_TEXT + "# other\n").encode()
    assert len(planted) >= 3  # site.yaml plus decoys for the staged file and backup


@pytest.mark.parametrize("had_backup", [False, True])
@pytest.mark.parametrize("cause", ["symlink", "content"])
def test_a_refused_save_leaves_no_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, had_backup: bool, cause: str
) -> None:
    cfg = make_real(tmp_path)
    victim = tmp_path / "victim.yaml"
    victim.write_text(CONFIG_TEXT, encoding="utf-8")
    if had_backup:
        (cfg.parent / "site.yaml.bak").write_text("old backup\n", encoding="utf-8")

    def interfere(path: Path) -> None:
        if cause == "symlink":
            cfg.unlink()
            cfg.symlink_to(victim)
        else:
            cfg.write_text(CONFIG_TEXT + "# elsewhere\n", encoding="utf-8")

    monkeypatch.setattr(editing, "_before_replace", interfere)
    with pytest.raises(editing.EditError):
        save(cfg, backup=True)
    expected = ["site.yaml"] + (["site.yaml.bak"] if had_backup else [])
    assert sorted(p.name for p in cfg.parent.iterdir()) == expected
    if had_backup:
        assert (cfg.parent / "site.yaml.bak").read_text() == "old backup\n"
    assert victim.read_text(encoding="utf-8") == CONFIG_TEXT


# ---- root-owned links (findings 1 to 3 of round 3) ----


def _as_root_owned(real: Any, names: set[str]) -> Any:
    """A stat seam that reports ``names`` as owned by root (tests cannot chown)."""

    def fake(name: str, dir_fd: int) -> os.stat_result:
        info = real(name, dir_fd)
        if name not in names:
            return info
        fields = list(info)
        fields[4] = 0  # st_uid
        return os.stat_result(fields)

    return fake


def _root_folder(mode_bits: int) -> Any:
    def fake(fd: int) -> os.stat_result:
        info = os.fstat(fd)
        fields = list(info)
        fields[0] = (info.st_mode & ~0o7777) | mode_bits
        fields[4] = 0
        return os.stat_result(fields)

    return fake


def test_a_root_owned_link_in_a_user_writable_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    (tmp_path / "alias").symlink_to(cfg.parent, target_is_directory=True)
    monkeypatch.setattr(
        editing, "_stat_at", _as_root_owned(editing._stat_at, {"alias"})
    )
    with pytest.raises(editing.EditError, match="unsafe config path"):
        editing.open_config_dir(tmp_path / "alias" / "site.yaml")


def test_a_root_owned_link_in_a_root_only_folder_is_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the allowance still works where only root can change it."""
    cfg = make_real(tmp_path)
    (tmp_path / "alias").symlink_to(cfg.parent, target_is_directory=True)
    monkeypatch.setattr(
        editing, "_stat_at", _as_root_owned(editing._stat_at, {"alias"})
    )
    monkeypatch.setattr(editing, "_fstat_dir", _root_folder(0o755))
    fd, resolved = editing.open_config_dir(tmp_path / "alias" / "site.yaml")
    os.close(fd)
    assert resolved == cfg.resolve()


def test_a_group_writable_root_folder_does_not_allow_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    (tmp_path / "alias").symlink_to(cfg.parent, target_is_directory=True)
    monkeypatch.setattr(
        editing, "_stat_at", _as_root_owned(editing._stat_at, {"alias"})
    )
    monkeypatch.setattr(editing, "_fstat_dir", _root_folder(0o775))
    with pytest.raises(editing.EditError):
        editing.open_config_dir(tmp_path / "alias" / "site.yaml")


def test_a_root_owned_link_replaced_after_the_look_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    other = make_other(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(cfg.parent, target_is_directory=True)
    monkeypatch.setattr(
        editing, "_stat_at", _as_root_owned(editing._stat_at, {"alias"})
    )
    monkeypatch.setattr(editing, "_fstat_dir", _root_folder(0o755))
    real_readlink = editing._readlink_at

    def read_then_swap(name: str, dir_fd: int) -> str:
        real_readlink(name, dir_fd)
        alias.unlink()
        alias.symlink_to(other, target_is_directory=True)
        return str(other)

    monkeypatch.setattr(editing, "_readlink_at", read_then_swap)
    with pytest.raises(editing.EditError, match="unsafe config path"):
        editing.open_config_dir(alias / "site.yaml")


# ---- the state read ----


def test_the_state_time_does_not_follow_a_final_name_swapped_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    victim = tmp_path / "victim.yaml"
    victim.write_text(CONFIG_TEXT, encoding="utf-8")
    os.utime(victim, (1_000_000, 1_000_000))
    session = EditorSession(cfg)
    assert session.service._mtime() > 1_000_000
    real_open = editing.open_config_dir

    def open_then_swap(path: Path) -> tuple[int, Path]:
        out = real_open(path)
        cfg.unlink()
        cfg.symlink_to(victim)
        return out

    monkeypatch.setattr(editing, "open_config_dir", open_then_swap)
    assert session.service._mtime() == 0.0


# ---- the backup name ----


@pytest.mark.parametrize("had_backup", [False, True])
def test_a_refused_save_leaves_a_backup_another_process_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, had_backup: bool
) -> None:
    cfg = make_real(tmp_path)
    bak = cfg.parent / "site.yaml.bak"
    if had_backup:
        bak.write_text("old backup\n", encoding="utf-8")

    def interfere(path: Path) -> None:
        bak.unlink()
        bak.write_text("someone else\n", encoding="utf-8")  # a new entry
        cfg.write_text(CONFIG_TEXT + "# elsewhere\n", encoding="utf-8")  # refuse

    monkeypatch.setattr(editing, "_before_replace", interfere)
    with pytest.raises(editing.EditError):
        save(cfg, backup=True)
    assert bak.read_text(encoding="utf-8") == "someone else\n"


# ---- folders the user can only pass through ----


@needs_unprivileged
@pytest.mark.parametrize("bits", [0o711, 0o311, 0o111])
def test_a_regular_config_under_an_execute_only_folder_works(
    tmp_path: Path, bits: int
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    inner = outer / "real"
    inner.mkdir()
    cfg = inner / "site.yaml"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    with mode(outer, bits):
        session = EditorSession(cfg)
        assert session.service.version()["hash"]
        assert save(cfg).written
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEXT + "# x\n"


@needs_unprivileged
def test_a_save_in_a_folder_that_cannot_be_listed_works(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    with mode(cfg.parent, 0o300):
        assert save(cfg).written
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEXT + "# x\n"


def test_the_swap_helper_really_redirects_the_path(tmp_path: Path) -> None:
    """Negative control: the swap the tests use does change what the path names."""
    cfg = make_real(tmp_path)
    other = make_other(tmp_path)
    swap_parent_for_link(cfg.parent, other)
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEXT + "# other\n"
    with pytest.raises(editing.EditError):
        editing.safe_config_path(cfg)
