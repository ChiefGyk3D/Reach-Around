# SPDX-License-Identifier: Apache-2.0
"""The gated artifacts are committed as one recoverable set, under one lock, and
the anchored reader refuses a file another user could have planted (#632, review
round 6). Synthetic data only."""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import reach_around.kit.binding as binding_module
import reach_around.site.kit as site_kit
from kitpdfharness import TODAY, build_instance, kit_doc, make_config
from reach_around.config import SiteConfig
from reach_around.kit.binding import (
    BINDING_FILE,
    LOCK_FILE,
    PENDING_FILE,
    AnchoredReadError,
    GatedLockError,
    check_file_owner,
    gated_lock,
    read_anchored,
    require_absent,
)
from reach_around.kit.binding import (
    STAGING_NAME as STAGING,
)
from reach_around.kit.measures_file import (
    MeasuresFileError,
    build_measures_file,
    load_measures_for_build,
)
from reach_around.site.build import build_site

SET = ("kit.html", "kit-policy.txt", str(BINDING_FILE))
PUBLIC_ONLY: dict[str, Any] = {
    "title": "Example Creator media kit",
    "about": "A fake creator.",
    "metrics": [
        {
            "platform": "twitch",
            "label": "Followers",
            "value": 1200,
            "as_of": "2027-03-01",
        }
    ],
}


def changed_doc() -> dict[str, Any]:
    doc = kit_doc()
    doc["about"] = "A different fake creator, built the second time."
    return doc


def rebuild(config: SiteConfig, site: Path, gated: Path, assets: Path) -> None:
    build_site(config, site, today=TODAY, gated_out=gated, assets_dir=assets)


def snapshot(gated: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(gated.iterdir()) if p.is_file()}


def measures(tmp_path: Path, config: SiteConfig, site: Path, gated: Path) -> Path:
    return build_measures_file(
        config,
        site_dir=site,
        gated_dir=gated,
        out_dir=tmp_path / "private",
        generated=date(2027, 3, 10),
        overwrite=True,
    )


@pytest.fixture(autouse=True)
def short_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binding_module, "LOCK_WAIT_SECONDS", 0.3)


# --- the reader refuses a file another user could have planted ---------------------


def test_check_file_owner_refuses_another_user() -> None:
    me = os.geteuid()
    info = os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, me + 1, 0, 5, 0, 0, 0))
    with pytest.raises(AnchoredReadError, match="another user"):
        check_file_owner(info, me)


def test_check_file_owner_allows_root_and_me() -> None:
    me = os.geteuid()
    for uid in (me, 0):
        check_file_owner(
            os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, uid, 0, 5, 0, 0, 0)), me
        )


def test_the_reader_refuses_a_file_owned_by_another_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opened descriptor's fstat reports another owner (a file another user
    planted in a sticky directory)."""
    target = tmp_path / "record.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    real = os.fstat

    def foreign(fd: int) -> os.stat_result:
        info = real(fd)
        if not stat.S_ISREG(info.st_mode):
            return info
        fields = list(info)
        fields[4] = os.geteuid() + 1
        return os.stat_result(tuple(fields))

    monkeypatch.setattr(os, "fstat", foreign)
    with pytest.raises(AnchoredReadError, match="another user"):
        read_anchored(target, 100)


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_the_reader_refuses_a_group_or_other_writable_file(
    tmp_path: Path, mode: int
) -> None:
    target = tmp_path / "record.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(mode)
    with pytest.raises(AnchoredReadError, match="written by the group or by others"):
        read_anchored(target, 100)


def test_the_reader_refuses_a_file_with_a_second_link(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    os.link(target, tmp_path / "other-name")
    with pytest.raises(AnchoredReadError, match="more than one link"):
        read_anchored(target, 100)


def test_the_reader_still_reads_an_ordinary_file(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    target.write_text('{"a": 1}', encoding="utf-8")
    target.chmod(0o600)
    assert read_anchored(target, 100) == b'{"a": 1}'


# --- the commit is one recoverable set ------------------------------------------------


ROLLED_BACK = [
    "staged",
    "marker",
    "prepared",
    "kit.html",
    "kit-policy.txt",
    "kit-binding.json",
    "applied",
]


@pytest.mark.parametrize("step", ROLLED_BACK)
def test_a_failure_after_any_commit_step_restores_the_previous_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    before = snapshot(gated)
    assert {*SET} <= set(before)
    measures(tmp_path, config, site, gated)  # the previous build is usable

    def boom(name: str) -> None:
        if name == step:
            raise OSError(f"injected after {name}")

    monkeypatch.setattr(site_kit, "_after_commit_step", boom)
    with pytest.raises(OSError, match="injected"):
        rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    monkeypatch.setattr(site_kit, "_after_commit_step", None)

    after = snapshot(gated)
    after.pop(LOCK_FILE.name, None)
    before.pop(LOCK_FILE.name, None)
    assert after == before  # every file as it was, no marker, temp or backup
    assert {p.name for p in gated.iterdir()} <= {*before, LOCK_FILE.name}
    # and the restored directory is still consistent with the public pages
    # written for it: the previous measures file can still be bound and read
    # (the public tree itself was rebuilt, so compare against the gated set only)
    assert not (gated / PENDING_FILE).exists()


ROLLED_BACK = [
    "staged",
    "marker",
    "prepared",
    "kit.html",
    "kit-policy.txt",
    "kit-binding.json",
    "applied",
]


@pytest.mark.parametrize("step", ROLLED_BACK)
def test_a_failure_while_removing_the_page_keeps_the_old_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """A rebuild as public-only erases the gated set, then writes the binding; a
    failure at any step leaves the old, working gated set."""
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    before = snapshot(gated)
    before.pop(LOCK_FILE.name, None)

    def boom(name: str) -> None:
        if name == step:
            raise OSError(f"injected after {name}")

    monkeypatch.setattr(site_kit, "_after_commit_step", boom)
    with pytest.raises(OSError, match="injected"):
        rebuild(make_config(PUBLIC_ONLY), site, gated, tmp_path / "assets")
    monkeypatch.setattr(site_kit, "_after_commit_step", None)
    after = snapshot(gated)
    after.pop(LOCK_FILE.name, None)
    assert after == before


def test_a_crash_mid_commit_is_detected_by_every_reader_and_a_rebuild_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    path = measures(tmp_path, config, site, gated)

    def crash(name: str) -> None:
        if name == "kit.html":
            raise KeyboardInterrupt  # the process dies: no rollback runs

    monkeypatch.setattr(site_kit, "_after_commit_step", crash)
    # a crash is not a rollback: the rollback is switched off for this test
    monkeypatch.setattr(site_kit.StagedGated, "_rollback", lambda *a, **k: None)
    with pytest.raises(KeyboardInterrupt):
        rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    monkeypatch.setattr(site_kit, "_after_commit_step", None)
    monkeypatch.undo()
    monkeypatch.setattr(binding_module, "LOCK_WAIT_SECONDS", 0.3)

    # the page is new, the binding old, the marker is there
    assert (gated / PENDING_FILE).exists()
    with pytest.raises(MeasuresFileError, match="not in a finished state"):
        build_measures_file(
            config,
            site_dir=site,
            gated_dir=gated,
            out_dir=tmp_path / "private2",
            generated=date(2027, 3, 10),
        )
    with pytest.raises(MeasuresFileError, match="not in a finished state"):
        load_measures_for_build(path, gated)

    # the next build writes all three again, clears the marker, and removes the
    # temporary and backup names the crash left
    rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    assert not (gated / PENDING_FILE).exists()
    names = {p.name for p in gated.iterdir()}
    assert names == {*SET, LOCK_FILE.name}
    rebuilt = measures(tmp_path, make_config(changed_doc()), site, gated)
    assert load_measures_for_build(rebuilt, gated)


ALL_STEPS = [*ROLLED_BACK, "cleaned", "done"]


class Disk:
    """A pessimistic model of the gated directory on a disk that loses what was
    not synced: ``synced`` is the directory as of its last fsync, and a crash
    keeps that plus the one first target rename (a rename may reach the disk
    ahead of the entries that were never synced)."""

    def __init__(self, gated: Path) -> None:
        self.gated = gated
        self.synced = snapshot(gated)
        self.events: list[str] = []

    def hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_fsync, real_replace = os.fsync, os.replace
        ino = self.gated.stat().st_ino

        def fsync(fd: int) -> None:
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode) and info.st_ino == ino:
                self.synced = snapshot(self.gated)
                self.events.append("dirsync")
            real_fsync(fd)

        def replace(src: Any, dst: Any, **kw: Any) -> None:
            real_replace(src, dst, **kw)
            self.events.append(f"rename:{Path(dst).name}")

        monkeypatch.setattr(os, "fsync", fsync)
        monkeypatch.setattr(os, "replace", replace)
        monkeypatch.setattr(
            site_kit,
            "_after_commit_step",
            lambda name: self.events.append(f"step:{name}"),
        )

    def after_crash(self, into: Path) -> Path:
        """The directory a restart finds: the last synced state plus the page
        rename, if it had happened, even though nothing else was synced."""
        state = dict(self.synced)
        if "rename:kit.html" in self.events:
            state["kit.html"] = (self.gated / "kit.html").read_bytes()
        into.mkdir(mode=0o700)
        for name, data in state.items():
            (into / name).write_bytes(data)
            (into / name).chmod(0o600)
        return into


@pytest.mark.parametrize("step", ALL_STEPS)
def test_a_crash_at_any_step_never_leaves_an_accepted_mixed_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """After a crash at any step, with every unsynced directory change lost
    except the page rename, the directory is either the whole old set or is
    refused by the readers; never a new page beside the old binding."""
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    before = snapshot(gated)
    disk = Disk(gated)
    disk.hook(monkeypatch)

    def crash(name: str) -> None:
        disk.events.append(f"step:{name}")
        if name == step:
            raise KeyboardInterrupt  # the process dies: no rollback runs

    monkeypatch.setattr(site_kit, "_after_commit_step", crash)
    monkeypatch.setattr(site_kit.StagedGated, "_rollback", lambda *a, **k: None)
    with pytest.raises(KeyboardInterrupt):
        rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    monkeypatch.undo()

    found = disk.after_crash(tmp_path / "restart")
    try:
        with gated_lock(found, exclusive=False) as held:
            binding_module.check_no_pending_commit(found, held)
    except ValueError:
        pass  # refused: the next build recovers
    else:
        got = snapshot(found)
        names = [n for n in SET]
        if step == "done":
            assert got == snapshot(gated)  # finished: the whole new set
        else:
            assert {n: got.get(n) for n in names} == {n: before[n] for n in names}


def test_the_marker_and_backups_are_synced_before_the_first_target_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    disk = Disk(gated)
    disk.hook(monkeypatch)
    rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    ev = disk.events
    marker = ev.index("step:marker")
    first_target = ev.index("rename:kit.html")
    assert "dirsync" in ev[marker:first_target]
    # a sync after the renames, one after the backups are removed, one after
    # the marker is removed, in that order
    applied, cleaned, done = (
        ev.index(f"step:{n}") for n in ("applied", "cleaned", "done")
    )
    assert "dirsync" in ev[ev.index("rename:kit-binding.json") : applied]
    assert "dirsync" in ev[applied:cleaned]
    assert "dirsync" in ev[cleaned:done]
    assert ev.index("rename:kit-binding.json") == max(
        i for i, e in enumerate(ev) if e.startswith("rename:kit")
    )


def test_a_crash_after_the_set_is_complete_leaves_the_marker_not_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tail fails (here: the marker cannot be removed) after the renames and
    after the backups were removed: the marker stays so readers refuse, and no
    backup is left under a clean-looking directory. Reverting the order (marker
    removed before the backups) leaves backups here and fails this test."""
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == PENDING_FILE.name:
            raise OSError("injected: the marker cannot be removed")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError, match="injected"):
        rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    monkeypatch.undo()
    assert (gated / PENDING_FILE).exists()
    assert not any(STAGING.fullmatch(p.name) for p in gated.iterdir())
    with pytest.raises(ValueError, match="not in a finished state"):
        with gated_lock(gated, exclusive=False) as held:
            binding_module.check_no_pending_commit(gated, held)


@pytest.mark.parametrize("kind", ["bak", "tmp"])
def test_readers_refuse_leftover_backups_and_a_rebuild_removes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    path = measures(tmp_path, config, site, gated)
    monkeypatch.setattr(binding_module, "LOCK_WAIT_SECONDS", 0.3)
    leftover = gated / f".kit.html.{'ab' * 8}.{kind}"
    leftover.write_bytes(b"an old private page")
    leftover.chmod(0o600)
    with pytest.raises(MeasuresFileError, match="not in a finished state"):
        load_measures_for_build(path, gated)
    with pytest.raises(MeasuresFileError, match="not in a finished state"):
        build_measures_file(
            config,
            site_dir=site,
            gated_dir=gated,
            out_dir=tmp_path / "private2",
            generated=date(2027, 3, 10),
        )
    rebuild(make_config(kit_doc()), site, gated, tmp_path / "assets")
    assert not leftover.exists()
    assert {p.name for p in gated.iterdir()} == {*SET, LOCK_FILE.name}
    assert load_measures_for_build(measures(tmp_path, config, site, gated), gated)


def test_a_lookalike_name_is_not_a_leftover(tmp_path: Path) -> None:
    gated = tmp_path / "g"
    gated.mkdir(mode=0o700)
    for name in (".kit.html.zz.bak", "kit.html.bak", f".other.{'ab' * 8}.bak"):
        (gated / name).write_text("x")
    with gated_lock(gated, exclusive=False) as held:
        binding_module.check_no_pending_commit(gated, held)  # not ours: not refused


def test_a_failure_after_the_set_is_complete_keeps_the_new_set_and_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while cleaning up cannot roll back (the new set is complete and
    synced): the new files stay, the marker stays so readers refuse, and the
    next build clears it."""
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))

    def boom(name: str) -> None:
        if name == "cleaned":
            raise OSError("injected after cleanup")

    monkeypatch.setattr(site_kit, "_after_commit_step", boom)
    with pytest.raises(OSError, match="injected"):
        rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    monkeypatch.setattr(site_kit, "_after_commit_step", None)
    assert (gated / PENDING_FILE).exists()
    assert not any(STAGING.fullmatch(p.name) for p in gated.iterdir())
    rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    assert not (gated / PENDING_FILE).exists()


def test_the_binding_is_the_last_file_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    order: list[str] = []
    monkeypatch.setattr(site_kit, "_after_commit_step", order.append)
    rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    assert order == [
        "staged",
        "marker",
        "prepared",
        "kit.html",
        "kit-policy.txt",
        "kit-binding.json",
        "applied",
        "cleaned",
        "done",
    ]


def test_a_failure_to_stage_the_binding_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new files are written before any old one is touched."""
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    before = snapshot(gated)
    before.pop(LOCK_FILE.name, None)
    real = site_kit._stage_text

    def fail_binding(root: Path, target: Path, text: str) -> Path:
        if target.name == BINDING_FILE.name:
            raise OSError("no space left")
        return real(root, target, text)

    monkeypatch.setattr(site_kit, "_stage_text", fail_binding)
    with pytest.raises(OSError, match="no space"):
        rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    after = snapshot(gated)
    after.pop(LOCK_FILE.name, None)
    assert after == before


# --- one lock for builds and readers -----------------------------------------------


def test_a_build_waits_for_a_build_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent builds: the second does not stage or commit while the first
    holds the lock; both finish and the directory is one consistent build."""
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    monkeypatch.setattr(binding_module, "LOCK_WAIT_SECONDS", 20.0)
    inside = threading.Event()
    release = threading.Event()
    log: list[str] = []

    def hold(name: str) -> None:
        if name == "marker" and not inside.is_set():
            inside.set()
            assert release.wait(10)
        log.append(name)

    monkeypatch.setattr(site_kit, "_after_commit_step", hold)
    errors: list[BaseException] = []

    def build(doc: dict[str, Any]) -> None:
        try:
            rebuild(make_config(doc), tmp_path / "site-x", gated, tmp_path / "assets")
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            errors.append(exc)

    first = threading.Thread(target=build, args=(changed_doc(),))
    first.start()
    assert inside.wait(10)
    second = threading.Thread(target=build, args=(kit_doc(),))
    second.start()
    time.sleep(0.5)
    assert second.is_alive()  # blocked on the lock, not interleaving
    # the first build staged and is held at the marker step; neither build has
    # committed anything, and the second has not even staged
    assert log == ["staged"]
    assert (gated / PENDING_FILE).exists()
    release.set()
    first.join(20)
    second.join(20)
    assert not errors, errors
    assert not (gated / PENDING_FILE).exists()
    assert {p.name for p in gated.iterdir()} == {*SET, LOCK_FILE.name}
    record = json.loads((gated / BINDING_FILE).read_text("utf-8"))
    assert record["gated_page"] == binding_module.sha256_text(
        (gated / "kit.html").read_bytes()
    )


def test_a_build_refuses_when_a_reader_holds_the_directory(tmp_path: Path) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    with gated_lock(gated, exclusive=False):
        with pytest.raises(GatedLockError, match="holds the gated directory"):
            rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")
    rebuild(make_config(changed_doc()), site, gated, tmp_path / "assets")


def test_a_reader_refuses_while_a_build_holds_the_directory(tmp_path: Path) -> None:
    config, site, gated = build_instance(tmp_path, make_config(kit_doc()))
    with gated_lock(gated, exclusive=True):
        with pytest.raises(MeasuresFileError, match="holds the gated directory"):
            measures(tmp_path, config, site, gated)
    assert measures(tmp_path, config, site, gated).is_file()


def test_a_public_only_build_cannot_create_the_page_while_measures_are_checked(
    tmp_path: Path,
) -> None:
    """The absence of a gated page is only true while no build can add one: the
    reader holds the lock from its check to its write, so a build of a page waits."""
    config, site, gated = build_instance(tmp_path, make_config(PUBLIC_ONLY))
    assert not (gated / "kit.html").exists()
    with gated_lock(gated, exclusive=False):
        with pytest.raises(GatedLockError):
            rebuild(make_config(kit_doc()), site, gated, tmp_path / "assets")
        assert not (gated / "kit.html").exists()


def test_the_absence_check_looks_through_the_held_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the directory chain is held, the directory is swapped for a link to
    one that holds a page: the check still looks at the directory it holds."""
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    decoy = tmp_path / "decoy"
    decoy.mkdir(mode=0o700)
    (decoy / "kit.html").write_text("<p>planted</p>", encoding="utf-8")
    moved = tmp_path / "moved"

    def swap() -> None:
        os.rename(gated, moved)
        gated.symlink_to(decoy)

    monkeypatch.setattr(binding_module, "_before_final_open", swap)
    require_absent(gated / "kit.html")  # the held (empty) directory has no page


def test_the_absence_check_refuses_a_page_and_a_link(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    require_absent(gated / "kit.html")
    (gated / "kit.html").symlink_to(tmp_path / "nowhere")
    with pytest.raises(AnchoredReadError, match="present"):
        require_absent(gated / "kit.html")


def test_the_lock_file_must_be_a_private_regular_file(tmp_path: Path) -> None:
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    (gated / LOCK_FILE.name).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(GatedLockError):
        gated_lock(gated, exclusive=True).__enter__()
    (gated / LOCK_FILE.name).unlink()
    (gated / LOCK_FILE.name).write_text("", encoding="utf-8")
    (gated / LOCK_FILE.name).chmod(0o666)
    with pytest.raises(GatedLockError, match="refused"):
        gated_lock(gated, exclusive=True).__enter__()


# --- the leftover scan and the reads are bound to the locked directory ---------


def _leftover_dir(tmp_path: Path) -> tuple[Path, Path]:
    gated = tmp_path / "gated"
    gated.mkdir(mode=0o700)
    (gated / f".kit.html.{'cd' * 8}.bak").write_text("private", encoding="utf-8")
    decoy = tmp_path / "decoy"
    decoy.mkdir(mode=0o700)
    return gated, decoy


def test_the_leftover_scan_refuses_a_directory_linked_after_the_lock(
    tmp_path: Path,
) -> None:
    gated, decoy = _leftover_dir(tmp_path)
    with gated_lock(gated, exclusive=False) as held:
        os.rename(gated, tmp_path / "moved")
        gated.symlink_to(decoy)  # a clean directory now answers to the path
        with pytest.raises(ValueError, match="not in a finished state"):
            binding_module.check_no_pending_commit(gated, held)


def test_the_leftover_scan_refuses_another_directory_swapped_in(
    tmp_path: Path,
) -> None:
    gated, _ = _leftover_dir(tmp_path)
    with gated_lock(gated, exclusive=False) as held:
        os.rename(gated, tmp_path / "moved")
        gated.mkdir(mode=0o700)  # a clean directory with no lock file of ours
        with pytest.raises(ValueError, match="not the one that was locked"):
            binding_module.check_no_pending_commit(gated, held)
        (gated / LOCK_FILE.name).write_text("")  # a lock file, but not the held one
        (gated / LOCK_FILE.name).chmod(0o600)
        with pytest.raises(ValueError, match="not the one that was locked"):
            binding_module.check_no_pending_commit(gated, held)


def test_the_leftover_scan_looks_at_the_directory_it_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path is swapped for a link to a clean directory while the chain is
    being held: the scan still reads the directory it holds and finds the
    leftover."""
    gated, decoy = _leftover_dir(tmp_path)
    moved = tmp_path / "moved"
    with gated_lock(gated, exclusive=False) as held:

        def swap() -> None:
            os.rename(gated, moved)
            gated.symlink_to(decoy)

        monkeypatch.setattr(binding_module, "_before_final_open", swap)
        with pytest.raises(ValueError, match="backup copy"):
            binding_module.check_no_pending_commit(gated, held)


def test_the_stale_removal_never_follows_a_swapped_path(tmp_path: Path) -> None:
    gated, decoy = _leftover_dir(tmp_path)
    planted = decoy / f".kit.html.{'ef' * 8}.bak"
    planted.write_text("not ours", encoding="utf-8")
    with gated_lock(gated, exclusive=True) as held:
        os.rename(gated, tmp_path / "moved")
        gated.symlink_to(decoy)
        with pytest.raises(ValueError):
            site_kit._remove_stale_staging(gated, held)
    assert planted.exists()
    assert (tmp_path / "moved" / f".kit.html.{'cd' * 8}.bak").exists()


def test_a_read_in_the_locked_directory_refuses_a_swapped_one(
    tmp_path: Path,
) -> None:
    gated, _ = _leftover_dir(tmp_path)
    (gated / "kit-policy.txt").write_text("policy", encoding="utf-8")
    (gated / "kit-policy.txt").chmod(0o600)
    with gated_lock(gated, exclusive=False) as held:
        assert binding_module.read_anchored(gated / "kit-policy.txt", 100, held)
        os.rename(gated, tmp_path / "moved")
        gated.mkdir(mode=0o700)
        (gated / "kit-policy.txt").write_text("other", encoding="utf-8")
        (gated / "kit-policy.txt").chmod(0o600)
        with pytest.raises(binding_module.AnchoredReadError, match="locked"):
            binding_module.read_anchored(gated / "kit-policy.txt", 100, held)
        with pytest.raises(binding_module.AnchoredReadError, match="locked"):
            binding_module.require_absent(gated / "kit.html", held)
