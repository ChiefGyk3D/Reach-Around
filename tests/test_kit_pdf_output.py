# SPDX-License-Identifier: Apache-2.0
"""Where the private PDF may be written, and how."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from reach_around.kit.output import OutputError, confine_out_dir, write_private


def out_dir(tmp_path: Path) -> Path:
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../kit.pdf",
        "a/kit.pdf",
        "/abs/kit.pdf",
        ".hidden.pdf",
        "kit.txt",
        "kit",
        "kit.pdf\x00",
        "kit .pdf",
        "k" * 200 + ".pdf",
        "",
        "kit.PDF.html",
    ],
)
def test_bad_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(OutputError):
        write_private(out_dir(tmp_path), name, b"%PDF-", overwrite=False)


def test_file_is_written_owner_only_with_no_temp_left(tmp_path: Path) -> None:
    target = write_private(out_dir(tmp_path), "kit.pdf", b"%PDF-1", overwrite=False)
    assert target.read_bytes() == b"%PDF-1"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["kit.pdf"]


def test_existing_file_needs_overwrite(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    write_private(d, "kit.pdf", b"one", overwrite=False)
    with pytest.raises(OutputError, match="exists"):
        write_private(d, "kit.pdf", b"two", overwrite=False)
    assert (d / "kit.pdf").read_bytes() == b"one"
    write_private(d, "kit.pdf", b"two", overwrite=True)
    assert (d / "kit.pdf").read_bytes() == b"two"


def test_a_symlink_at_the_target_is_never_followed(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    os.symlink(victim, d / "kit.pdf")
    for overwrite in (False, True):
        with pytest.raises(OutputError):
            write_private(d, "kit.pdf", b"%PDF-", overwrite=overwrite)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_a_directory_at_the_target_is_refused(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    (d / "kit.pdf").mkdir()
    with pytest.raises(OutputError):
        write_private(d, "kit.pdf", b"%PDF-", overwrite=True)


def test_a_symlinked_output_dir_is_refused(tmp_path: Path) -> None:
    real = out_dir(tmp_path)
    link = tmp_path / "link"
    os.symlink(real, link)
    with pytest.raises(OutputError, match="symbolic link"):
        confine_out_dir(link, forbidden=[])
    with pytest.raises(OutputError):
        write_private(link, "kit.pdf", b"%PDF-", overwrite=False)


def test_a_symlinked_parent_of_the_output_dir_is_refused(tmp_path: Path) -> None:
    real = out_dir(tmp_path)
    (real / "sub").mkdir()
    link = tmp_path / "alias"
    os.symlink(real, link)
    with pytest.raises(OutputError, match="symbolic link"):
        confine_out_dir(link / "sub", forbidden=[])


def test_output_dir_is_created_owner_only_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "new" / "deeper"
    made = confine_out_dir(target, forbidden=[])
    assert made.is_dir() and stat.S_IMODE(made.stat().st_mode) == 0o700


def test_an_output_dir_others_can_enter_is_refused(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    d.chmod(0o755)
    with pytest.raises(OutputError, match="owner"):
        confine_out_dir(d, forbidden=[])


def test_never_inside_or_around_a_published_or_gated_or_config_directory(
    tmp_path: Path,
) -> None:
    site, gated, conf = tmp_path / "site", tmp_path / "gated", tmp_path / "conf"
    for d in (site, gated, conf):
        d.mkdir(mode=0o700)
    forbidden = [site, gated, conf]
    for bad in (site, site / "kit", gated, gated / "x", conf, tmp_path):
        with pytest.raises(OutputError, match="published|inside|around|same"):
            confine_out_dir(bad, forbidden=forbidden)
    # an alias of a forbidden directory is the same directory
    alias = tmp_path / "alias"
    os.symlink(site, alias)
    with pytest.raises(OutputError):
        confine_out_dir(alias, forbidden=forbidden)
    assert confine_out_dir(out_dir(tmp_path), forbidden=forbidden).is_dir()


def test_never_inside_a_git_work_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(OutputError, match="git"):
        confine_out_dir(repo / "out", forbidden=[])
    assert not (repo / "out").exists()


def test_a_failed_write_leaves_no_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = out_dir(tmp_path)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OutputError):
        write_private(d, "kit.pdf", b"%PDF-", overwrite=False)
    assert list(d.iterdir()) == []


# --- the walk is descriptor-relative ------------------------------------------


def test_an_ancestor_swapped_for_a_link_after_the_check_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Just before the last component is opened, the parent is renamed away and
    replaced by a link to another private directory. Nothing is written through
    the link, and the directory the walk held is left with no file."""
    real = tmp_path / "race" / "private"
    real.mkdir(parents=True, mode=0o700)
    evil = tmp_path / "evil" / "private"
    evil.mkdir(parents=True, mode=0o700)
    original = os.open
    swapped: list[bool] = []

    def swap_then_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if str(path) in (str(real), "private") and not swapped:
            swapped.append(True)
            os.rename(tmp_path / "race", tmp_path / "race.old")
            os.symlink(tmp_path / "evil", tmp_path / "race")
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_then_open)
    with pytest.raises(OutputError, match="changed|symbolic"):
        write_private(real, "kit.pdf", b"%PDF-1", overwrite=False)
    monkeypatch.undo()
    assert swapped
    assert list(evil.iterdir()) == []
    assert list((tmp_path / "race.old" / "private").iterdir()) == []


def test_a_directory_renamed_into_a_forbidden_tree_is_not_written_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the checks, the held directory is renamed into the published tree.
    The write must fail and leave nothing there, temporary file included."""
    site = tmp_path / "site"
    site.mkdir(mode=0o700)
    private = out_dir(tmp_path)
    original = os.fsync
    moved: list[bool] = []

    def move_then_sync(fd: int) -> None:
        if not moved:
            moved.append(True)
            os.rename(private, site / "uploaded")
        original(fd)

    monkeypatch.setattr(os, "fsync", move_then_sync)
    with pytest.raises(OutputError, match="changed"):
        write_private(private, "kit.pdf", b"%PDF-1", overwrite=False, forbidden=[site])
    monkeypatch.undo()
    assert moved
    assert list((site / "uploaded").iterdir()) == []


def test_a_directory_replaced_by_another_at_the_same_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = out_dir(tmp_path)
    original = os.fsync
    moved: list[bool] = []

    def swap_then_sync(fd: int) -> None:
        if not moved:
            moved.append(True)
            os.rename(private, tmp_path / "old")
            os.mkdir(private, 0o700)
        original(fd)

    monkeypatch.setattr(os, "fsync", swap_then_sync)
    with pytest.raises(OutputError, match="changed"):
        write_private(private, "kit.pdf", b"%PDF-1", overwrite=False)
    monkeypatch.undo()
    assert list(private.iterdir()) == []
    assert list((tmp_path / "old").iterdir()) == []


def test_a_removed_directory_is_refused(tmp_path: Path) -> None:
    from reach_around.kit.output import open_private_dir, verify_unchanged

    private = out_dir(tmp_path)
    fd = open_private_dir(private, [], create=False)
    try:
        verify_unchanged(private, fd, [])  # control: still there
        private.rmdir()
        with pytest.raises(OutputError, match="changed"):
            verify_unchanged(private, fd, [])
    finally:
        os.close(fd)


def test_intermediate_directories_are_created_owner_only(tmp_path: Path) -> None:
    made = confine_out_dir(tmp_path / "a" / "b" / "c", forbidden=[])
    for path in (made, made.parent, made.parent.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700, path


def test_a_directory_holding_a_work_tree_below_it_is_refused(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    (d / "nested" / "deeper" / ".git").mkdir(parents=True)
    with pytest.raises(OutputError, match="git work tree"):
        confine_out_dir(d, forbidden=[])
    with pytest.raises(OutputError, match="git work tree"):
        write_private(d, "kit.pdf", b"%PDF-", overwrite=False)


def test_a_directory_with_no_work_tree_below_it_is_fine(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    (d / "nested" / "deeper").mkdir(parents=True)
    (d / "old.pdf").write_bytes(b"%PDF-")
    assert confine_out_dir(d, forbidden=[]) == d


def test_a_forbidden_directory_is_rechecked_at_the_write(tmp_path: Path) -> None:
    d = out_dir(tmp_path)
    with pytest.raises(OutputError, match="published|inside|around|same"):
        write_private(d, "kit.pdf", b"%PDF-", overwrite=False, forbidden=[d])
    assert list(d.iterdir()) == []
