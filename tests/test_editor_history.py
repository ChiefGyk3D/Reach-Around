# SPDX-License-Identifier: Apache-2.0
"""The editor's undo history: snapshots, pointers, caps and hostile inputs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from reach_around import editor_history as eh
from reach_around.editor_history import History, HistoryError, Refusal, Restored


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Doc:
    """A stand-in for the config file: the write callback the history calls."""

    def __init__(self, path: Path, text: str = "v0\n") -> None:
        self.path = path
        self.path.write_text(text)

    @property
    def text(self) -> str:
        return self.path.read_text()

    def write(self, text: str) -> None:
        self.path.write_text(text)


@pytest.fixture
def doc(tmp_path: Path) -> Doc:
    return Doc(tmp_path / "site.yaml")


@pytest.fixture
def hist(doc: Doc) -> History:
    return History(doc.path)


def edit(hist: History, doc: Doc, new: str, label: str = "edit") -> None:
    old = doc.text
    doc.write(new)
    hist.record_write(old, new, label)


def history_dir(doc: Doc) -> Path:
    return doc.path.parent / eh.HISTORY_DIRNAME


# ---- layout and permissions ----


def test_layout_and_modes(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n", "first")
    d = history_dir(doc)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    files = sorted(p.name for p in d.iterdir())
    assert "index.json" in files
    snaps = [f for f in files if f.endswith(".yaml")]
    assert len(snaps) == 2  # the text before and after the write
    assert snaps[0].startswith("0001-") and snaps[0] == f"0001-{sha('v0\n')[:8]}.yaml"
    for p in d.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
    # no temp files left behind
    assert not [f for f in files if f.endswith(".tmp")]


def test_loose_directory_mode_is_tightened(doc: Doc) -> None:
    d = history_dir(doc)
    d.mkdir(mode=0o755)
    d.chmod(0o755)
    History(doc.path).record_write("v0\n", "v1\n", "x")
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_symlinked_history_dir_is_refused(tmp_path: Path, doc: Doc) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    history_dir(doc).symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(HistoryError, match="symlink"):
        History(doc.path).record_write("v0\n", "v1\n", "x")
    assert list(elsewhere.iterdir()) == []


def test_symlinked_snapshot_file_is_not_followed(tmp_path: Path, doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    victim = tmp_path / "victim.txt"
    victim.write_text("secret\n")
    snap = next(history_dir(doc).glob("0001-*.yaml"))
    snap.unlink()
    snap.symlink_to(victim)
    out = hist.undo(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code == "snapshot_unreadable"
    assert victim.read_text() == "secret\n"
    assert doc.text == "v1\n"


def test_history_dir_that_is_a_file_is_refused(doc: Doc) -> None:
    history_dir(doc).write_text("not a dir")
    with pytest.raises(HistoryError):
        History(doc.path).record_write("v0\n", "v1\n", "x")


# ---- undo and redo ----


def test_undo_redo_roundtrip(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    edit(hist, doc, "v2\n")
    out = hist.undo(doc.text, doc.write)
    assert isinstance(out, Restored) and doc.text == "v1\n"
    out = hist.undo(doc.text, doc.write)
    assert isinstance(out, Restored) and doc.text == "v0\n"
    again = hist.undo(doc.text, doc.write)
    assert isinstance(again, Refusal) and again.code == "nothing_to_undo"
    assert doc.text == "v0\n"
    assert isinstance(hist.redo(doc.text, doc.write), Restored) and doc.text == "v1\n"
    assert isinstance(hist.redo(doc.text, doc.write), Restored) and doc.text == "v2\n"
    last = hist.redo(doc.text, doc.write)
    assert isinstance(last, Refusal) and last.code == "nothing_to_redo"


def test_new_operation_clears_redo(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    edit(hist, doc, "v2\n")
    hist.undo(doc.text, doc.write)
    edit(hist, doc, "v3\n")
    out = hist.redo(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code == "nothing_to_redo"
    assert doc.text == "v3\n"
    # the cleared states are gone from disk too
    assert not any(sha("v2\n")[:8] in p.name for p in history_dir(doc).iterdir())
    assert isinstance(hist.undo(doc.text, doc.write), Restored)
    assert doc.text == "v1\n"


def test_undo_with_no_history_is_a_typed_refusal(doc: Doc, hist: History) -> None:
    out = hist.undo(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code == "nothing_to_undo"
    assert not history_dir(doc).exists()  # reading never creates the folder


def test_undo_refused_after_outside_edit_and_file_untouched(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    doc.write("outside\n")
    calls: list[str] = []
    out = hist.undo(doc.text, lambda t: calls.append(t))
    assert isinstance(out, Refusal) and out.code == "outside_edit"
    assert calls == [] and doc.text == "outside\n"
    assert calls == []
    # redo is guarded the same way once there is something to redo
    doc.write("v1\n")
    assert isinstance(hist.undo(doc.text, doc.write), Restored)
    doc.write("outside again\n")
    out = hist.redo(doc.text, lambda t: calls.append(t))
    assert isinstance(out, Refusal) and out.code == "outside_edit"
    assert calls == [] and doc.text == "outside again\n"


def test_outside_edit_that_restores_same_bytes_is_not_an_outside_edit(
    doc: Doc, hist: History
) -> None:
    edit(hist, doc, "v1\n")
    doc.write("other\n")
    doc.write("v1\n")
    assert isinstance(hist.undo(doc.text, doc.write), Restored)


def test_restore_anyway_snapshots_current_first(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    edit(hist, doc, "v2\n")
    doc.write("outside\n")
    out = hist.restore_anyway(doc.text, doc.write)
    assert isinstance(out, Restored) and doc.text == "v1\n"
    # the outside version was kept, so even this is undoable via redo
    assert any(sha("outside\n")[:8] in p.name for p in history_dir(doc).iterdir())
    assert isinstance(hist.redo(doc.text, doc.write), Restored) and doc.text == "v2\n"
    assert isinstance(hist.redo(doc.text, doc.write), Restored) and doc.text == "outside\n"


def test_restore_anyway_snapshot_survives_a_failed_write(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    doc.write("outside\n")

    def boom(_: str) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        hist.restore_anyway(doc.text, boom)
    assert any(sha("outside\n")[:8] in p.name for p in history_dir(doc).iterdir())
    assert doc.text == "outside\n"


def test_failed_undo_write_leaves_pointers_alone(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")

    def boom(_: str) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        hist.undo(doc.text, boom)
    assert isinstance(hist.undo(doc.text, doc.write), Restored)
    assert doc.text == "v0\n"


def test_noop_write_is_not_recorded(doc: Doc, hist: History) -> None:
    hist.record_write("v0\n", "v0\n", "same")
    assert not history_dir(doc).exists()


def test_write_after_outside_edit_keeps_the_outside_text(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    doc.write("outside\n")
    edit(hist, doc, "v2\n")
    assert any(sha("outside\n")[:8] in p.name for p in history_dir(doc).iterdir())
    assert isinstance(hist.undo(doc.text, doc.write), Restored)
    assert doc.text == "outside\n"


def test_state_persists_across_instances(doc: Doc) -> None:
    edit(History(doc.path), doc, "v1\n")
    assert isinstance(History(doc.path).undo(doc.text, doc.write), Restored)
    assert doc.text == "v0\n"


# ---- caps ----


def test_entry_cap_drops_oldest_and_files(doc: Doc) -> None:
    hist = History(doc.path, max_entries=5)
    for i in range(1, 12):
        edit(hist, doc, f"v{i}\n")
    index = json.loads((history_dir(doc) / "index.json").read_text())
    assert len(index["entries"]) == 5
    assert len(list(history_dir(doc).glob("*.yaml"))) == 5
    assert sha("v0\n")[:8] not in "".join(p.name for p in history_dir(doc).iterdir())
    for want in ("v10\n", "v9\n", "v8\n", "v7\n"):
        assert isinstance(hist.undo(doc.text, doc.write), Restored)
        assert doc.text == want
    assert isinstance(hist.undo(doc.text, doc.write), Refusal)


def test_default_caps_are_the_spec_numbers() -> None:
    assert eh.MAX_ENTRIES == 100
    assert eh.MAX_TOTAL_BYTES == 5 * 1024 * 1024


def test_byte_cap_drops_oldest(doc: Doc) -> None:
    hist = History(doc.path, max_total_bytes=1000)
    for i in range(1, 8):
        edit(hist, doc, f"{i}" * 300 + "\n")
    total = sum(p.stat().st_size for p in history_dir(doc).glob("*.yaml"))
    assert total <= 1000
    assert isinstance(hist.undo(doc.text, doc.write), Restored)


def test_cap_never_drops_the_current_state(doc: Doc) -> None:
    hist = History(doc.path, max_entries=1)
    edit(hist, doc, "v1\n")
    edit(hist, doc, "v2\n")
    files = list(history_dir(doc).glob("*.yaml"))
    assert len(files) == 1 and sha("v2\n")[:8] in files[0].name


def test_huge_snapshot_is_refused_and_nothing_stored(doc: Doc, hist: History) -> None:
    huge = "x" * (eh.MAX_SNAPSHOT_BYTES + 1)
    with pytest.raises(HistoryError, match="too large"):
        hist.record_write("v0\n", huge, "big")
    with pytest.raises(HistoryError, match="too large"):
        hist.record_write(huge, "v0\n", "big")
    d = history_dir(doc)
    assert not d.exists() or not list(d.glob("*.yaml"))


def test_huge_current_text_cannot_be_undone(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    doc.write("x" * (eh.MAX_SNAPSHOT_BYTES + 1))
    out = hist.restore_anyway(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code == "too_large"
    assert len(doc.text) > eh.MAX_SNAPSHOT_BYTES  # untouched


def test_non_text_input_is_refused(doc: Doc, hist: History) -> None:
    with pytest.raises(HistoryError):
        hist.record_write("v0\n", "bad \ud800 surrogate", "x")


# ---- corrupted state ----


def _index(doc: Doc) -> Path:
    return history_dir(doc) / "index.json"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{not json",
        b"[]",
        b'{"version": 1}',
        b'{"version": 99, "entries": [], "cursor": 0, "next": 1, "produced": null}',
        b'{"version": 1, "entries": [{"n": "../../etc/passwd", "hash": "zz"}], '
        b'"cursor": 0, "next": 1, "produced": null}',
        b'{"version": 1, "entries": [], "cursor": 7, "next": 1, "produced": null}',
        b"\xff\xfe\x00",
        b"[" * 100000,
        b"0" * (eh.MAX_INDEX_BYTES + 10),
    ],
)
def test_corrupted_index_is_quarantined_and_history_restarts(doc: Doc, payload: bytes) -> None:
    hist = History(doc.path)
    edit(hist, doc, "v1\n")
    _index(doc).write_bytes(payload)
    hist2 = History(doc.path)
    # undo has nothing trustworthy to go back to, and says so
    out = hist2.undo(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code in {"nothing_to_undo", "history_corrupt"}
    assert doc.text == "v1\n"
    # recording works again from a clean index
    edit(hist2, doc, "v2\n")
    assert isinstance(hist2.undo(doc.text, doc.write), Restored)
    assert doc.text == "v1\n"
    assert list(history_dir(doc).glob("index.json.corrupt-*"))


def test_tampered_snapshot_content_is_not_restored(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    snap = next(history_dir(doc).glob("0001-*.yaml"))
    snap.write_text("tampered\n")
    out = hist.undo(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code == "snapshot_corrupt"
    assert doc.text == "v1\n"


def test_missing_snapshot_is_a_refusal(doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    next(history_dir(doc).glob("0001-*.yaml")).unlink()
    out = hist.undo(doc.text, doc.write)
    assert isinstance(out, Refusal) and out.code == "snapshot_unreadable"


def test_index_cannot_name_a_file_outside_the_directory(tmp_path: Path, doc: Doc, hist: History) -> None:
    edit(hist, doc, "v1\n")
    victim = tmp_path / "victim.yaml"
    victim.write_text("v0\n")
    data = json.loads(_index(doc).read_text())
    data["entries"][0]["file"] = "../victim.yaml"
    _index(doc).write_text(json.dumps(data))
    out = hist.undo(doc.text, doc.write)
    # the extra key is ignored: names are derived from number and hash only
    assert isinstance(out, (Restored, Refusal))
    assert victim.read_text() == "v0\n"


def test_label_is_sanitized(doc: Doc, hist: History) -> None:
    old = doc.text
    doc.write("v1\n")
    hist.record_write(old, "v1\n", "bad\nlabel\x1b[31m" + "y" * 500)
    entries = json.loads(_index(doc).read_text())["entries"]
    for e in entries:
        assert e["label"].isprintable() and len(e["label"]) <= eh.MAX_LABEL


# ---- concurrency ----


def test_concurrent_writers_keep_index_consistent(doc: Doc) -> None:
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            h = History(doc.path)
            for i in range(10):
                h.record_write(f"a{n}-{i}\n", f"b{n}-{i}\n", f"w{n}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = json.loads(_index(doc).read_text())
    nums = [e["n"] for e in data["entries"]]
    assert nums == sorted(set(nums))  # unique and ordered
    assert 0 <= data["cursor"] < len(nums)
    for e in data["entries"]:
        assert (history_dir(doc) / f"{e['n']:04d}-{e['hash'][:8]}.yaml").is_file()
    on_disk = {p.name for p in history_dir(doc).glob("*.yaml")}
    assert len(on_disk) == len(nums)  # no orphans


def test_lock_wait_is_bounded(doc: Doc, hist: History, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eh, "LOCK_TIMEOUT", 0.2)
    edit(hist, doc, "v1\n")
    import fcntl

    fd = os.open(history_dir(doc) / ".lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(HistoryError, match="locked"):
            hist.record_write("v1\n", "v2\n", "x")
    finally:
        os.close(fd)


# ---- gitignore helper ----


def test_ignore_line() -> None:
    assert eh.IGNORE_LINE == f"{eh.HISTORY_DIRNAME}/"


def test_gitignore_untouched_without_a_work_tree(tmp_path: Path) -> None:
    cfg = tmp_path / "site.yaml"
    cfg.write_text("v0\n")
    assert eh.ensure_gitignore(cfg) is False
    assert not (tmp_path / ".gitignore").exists()


def test_gitignore_line_added_once_in_a_work_tree(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "site"
    sub.mkdir()
    cfg = sub / "site.yaml"
    cfg.write_text("v0\n")
    (tmp_path / ".gitignore").write_text("node_modules/")  # no trailing newline
    assert eh.ensure_gitignore(cfg) is True
    assert eh.ensure_gitignore(cfg) is False  # already there
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert lines == ["node_modules/", eh.IGNORE_LINE]


def test_gitignore_created_when_missing(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")  # worktree/submodule form
    cfg = tmp_path / "site.yaml"
    cfg.write_text("v0\n")
    assert eh.ensure_gitignore(cfg) is True
    assert (tmp_path / ".gitignore").read_text() == eh.IGNORE_LINE + "\n"


def test_gitignore_symlink_is_not_followed(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    target = tmp_path / "target.txt"
    target.write_text("keep\n")
    (tmp_path / ".gitignore").symlink_to(target)
    cfg = tmp_path / "site.yaml"
    cfg.write_text("v0\n")
    with pytest.raises(HistoryError, match="symlink"):
        eh.ensure_gitignore(cfg)
    assert target.read_text() == "keep\n"
