# SPDX-License-Identifier: Apache-2.0
"""The history store works under the config folder the editor verified.

A swap of the config's folder at any moment between the editor's own check and
a history operation must not move the history store: it goes to (or is read
from) the folder that was verified, or the operation is refused. A decoy
folder with its own history stands in for the swap target; it must come out
byte for byte as it went in.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from editkit import CONFIG as CONFIG_TEXT
from reach_around import editing
from reach_around.editor.server import EditorSession
from reach_around.editor.service import EditorService, RouteError
from reach_around.editor_history import HISTORY_DIRNAME, History


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def make_real(tmp_path: Path) -> Path:
    real = tmp_path / "real"
    real.mkdir()
    cfg = real / "site.yaml"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    return cfg


def make_decoy(tmp_path: Path) -> Path:
    """A folder the swapped path would lead to, with a history of its own.

    Its last state is the real config's text, so an undo that landed here
    would find something to move to.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    cfg = decoy / "site.yaml"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    History(cfg).record_write("before\n", CONFIG_TEXT, "decoy label")
    return decoy


def swap_for_link(folder: Path, other: Path) -> Path:
    moved = folder.with_name(folder.name + "-moved")
    folder.rename(moved)
    folder.symlink_to(other, target_is_directory=True)
    return moved


def op_body(text: str) -> bytes:
    return json.dumps(
        {
            "base": sha(text),
            "op": "set_link_field",
            "args": {"id": "first", "field": "title", "value": "Renamed"},
        }
    ).encode()


def undo_body(text: str) -> bytes:
    return json.dumps({"base": sha(text)}).encode()


def run(service: EditorService, body: bytes) -> None:
    try:
        service.run_op(body, "127.0.0.1", 1)
    except RouteError:
        pass  # the reply is built from a re-read the swapped path refuses


def test_a_save_records_its_history_in_the_verified_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    decoy = make_decoy(tmp_path)
    before = tree(decoy)
    service = EditorSession(cfg).service
    real_record = History.record_write
    moved: list[Path] = []

    def swap_then_record(self: History, *args: Any, **kwargs: Any) -> None:
        moved.append(swap_for_link(cfg.parent, decoy))
        real_record(self, *args, **kwargs)

    monkeypatch.setattr(History, "record_write", swap_then_record)
    run(service, op_body(CONFIG_TEXT))
    assert moved, "the swap seam did not run"
    assert tree(decoy) == before
    assert (moved[0] / HISTORY_DIRNAME / "index.json").is_file()


def test_the_summary_is_not_read_from_a_folder_swapped_after_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    decoy = make_decoy(tmp_path)
    service = EditorSession(cfg).service
    real_summary = EditorService._summary
    swapped: list[Path] = []

    def swap_then_summary(self: EditorService, digest: str | None = None) -> Any:
        swapped.append(swap_for_link(cfg.parent, decoy))
        return real_summary(self, digest)

    monkeypatch.setattr(EditorService, "_summary", swap_then_summary)
    got = service.history()
    assert swapped, "the swap seam did not run"
    assert "decoy label" not in json.dumps(got)
    assert got["entries"] == []


def test_a_move_does_not_open_a_history_in_a_swapped_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    decoy = make_decoy(tmp_path)
    before = tree(decoy)
    service = EditorSession(cfg).service
    real_move = History._move
    swapped: list[Path] = []

    def swap_then_move(self: History, *args: Any, **kwargs: Any) -> Any:
        swapped.append(swap_for_link(cfg.parent, decoy))
        return real_move(self, *args, **kwargs)

    monkeypatch.setattr(History, "_move", swap_then_move)
    try:
        service.run_move(undo_body(CONFIG_TEXT), "undo", "127.0.0.1", 1)
    except RouteError:
        pass
    assert swapped, "the swap seam did not run"
    assert tree(decoy) == before


def test_a_move_is_refused_when_the_folder_is_swapped_before_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The swap lands after the config read and before the history walk."""
    cfg = make_real(tmp_path)
    decoy = make_decoy(tmp_path)
    before = tree(decoy)
    service = EditorSession(cfg).service
    real_history = History.undo
    swapped: list[Path] = []

    def swap_then_undo(self: History, *args: Any, **kwargs: Any) -> Any:
        swapped.append(swap_for_link(cfg.parent, decoy))
        return real_history(self, *args, **kwargs)

    monkeypatch.setattr(History, "undo", swap_then_undo)
    try:
        service.run_move(undo_body(CONFIG_TEXT), "undo", "127.0.0.1", 1)
    except RouteError:
        pass
    assert swapped, "the swap seam did not run"
    assert tree(decoy) == before


# ---- the history takes a held folder ----


def test_a_history_given_a_folder_descriptor_ignores_a_swapped_path(
    tmp_path: Path,
) -> None:
    cfg = make_real(tmp_path)
    decoy = make_decoy(tmp_path)
    before = tree(decoy)
    fd, _resolved = editing.open_config_dir(cfg)
    try:
        moved = swap_for_link(cfg.parent, decoy)
        history = History(cfg, dir_fd=fd)
        history.record_write("a\n", "b\n", "kept")
        assert [e.label for e in history.summary().entries] == ["start", "kept"]
    finally:
        os.close(fd)
    assert tree(decoy) == before
    assert (moved / HISTORY_DIRNAME / "index.json").is_file()


def test_a_history_does_not_close_the_descriptor_it_was_given(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    fd, _resolved = editing.open_config_dir(cfg)
    try:
        History(cfg, dir_fd=fd).record_write("a\n", "b\n", "kept")
        os.fstat(fd)  # still open
    finally:
        os.close(fd)


def test_a_history_without_a_descriptor_still_works_by_path(tmp_path: Path) -> None:
    """The non-editor path: the folder is opened from the config's path."""
    cfg = make_real(tmp_path)
    history = History(cfg)
    history.record_write("a\n", "b\n", "kept")
    assert [e.label for e in History(cfg).summary().entries] == ["start", "kept"]


def test_no_descriptor_is_left_open_by_a_save_and_an_undo(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    service = EditorSession(cfg).service

    def open_fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    service.run_op(op_body(CONFIG_TEXT), "127.0.0.1", 1)  # warm up
    text = cfg.read_text(encoding="utf-8")
    before = open_fds()
    service.history()
    service.run_move(undo_body(text), "undo", "127.0.0.1", 1)
    assert open_fds() == before
