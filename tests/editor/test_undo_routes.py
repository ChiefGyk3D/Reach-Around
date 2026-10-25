# SPDX-License-Identifier: Apache-2.0
"""Undo, redo and the history listing: the routes, the guard and stale handling."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_routes import CONFIG, Editor, body_of, make, sha

from editorharness import Response, request, tree_snapshot
from reach_around import editing
from reach_around.editor_history import History

TITLE = {"id": "first", "field": "title"}


@pytest.fixture
def ed(tmp_path: Path) -> Iterator[Editor]:
    editor = make(tmp_path)
    try:
        yield editor
    finally:
        editor.session.shutdown()


def rename(ed: Editor, value: str) -> Response:
    return ed.op("set_link_field", {**TITLE, "value": value})


def move(ed: Editor, route: str, **extra: Any) -> Response:
    return ed.post({"base": sha(ed.text), **extra}, f"/__edit/api/{route}")


def state(ed: Editor) -> dict[str, Any]:
    return body_of(ed.get("/__edit/api/state"))


def outside_edit(ed: Editor) -> str:
    text = ed.text.replace("# keep me", "# edited elsewhere")
    ed.config.write_text(text, encoding="utf-8")
    return text


# ---- state and listing -------------------------------------------------------------


def test_state_says_nothing_to_undo_before_any_write(ed: Editor) -> None:
    got = state(ed)
    assert got["can_undo"] is False and got["can_redo"] is False
    assert got["outside_edit"] is False


def test_state_follows_the_history(ed: Editor) -> None:
    assert rename(ed, "One").status == 200
    got = state(ed)
    assert (got["can_undo"], got["can_redo"]) == (True, False)
    assert move(ed, "undo").status == 200
    got = state(ed)
    assert (got["can_undo"], got["can_redo"]) == (False, True)


def test_history_lists_labels_times_and_hashes_but_no_text(ed: Editor) -> None:
    rename(ed, "One")
    after = ed.text
    got = body_of(ed.get("/__edit/api/history"))
    assert got["can_undo"] is True and got["can_redo"] is False
    assert got["outside_edit"] is False
    entries = got["entries"]
    assert [e["label"] for e in entries] == ["start", "Set link field"]
    assert entries[0]["hash"] == sha(CONFIG)
    assert entries[1]["hash"] == sha(after)
    assert [e["current"] for e in entries] == [False, True]
    assert all(set(e) == {"id", "hash", "label", "time", "current"} for e in entries)
    assert "One" not in json.dumps(got)


def test_history_of_a_fresh_session_is_empty(ed: Editor) -> None:
    got = body_of(ed.get("/__edit/api/history"))
    assert got["entries"] == []
    assert (got["can_undo"], got["can_redo"]) == (False, False)


# ---- undo and redo -----------------------------------------------------------------


def test_undo_restores_the_exact_text_and_redo_brings_it_back(ed: Editor) -> None:
    assert rename(ed, "One").status == 200
    changed = ed.text
    got = move(ed, "undo")
    assert got.status == 200
    assert ed.text == CONFIG  # comments and layout included
    data = body_of(got)
    assert data["state"]["hash"] == sha(CONFIG)
    assert data["state"]["can_redo"] is True
    assert data["history"]["hash"] == sha(CONFIG)
    got = move(ed, "redo")
    assert got.status == 200 and ed.text == changed
    assert body_of(got)["state"]["can_undo"] is True


def test_a_new_op_clears_redo(ed: Editor) -> None:
    rename(ed, "One")
    move(ed, "undo")
    assert state(ed)["can_redo"] is True
    rename(ed, "Two")
    assert state(ed)["can_redo"] is False
    got = move(ed, "redo")
    assert got.status == 409 and body_of(got)["error"]["code"] == "nothing_to_redo"


def test_nothing_to_undo_is_409_and_writes_nothing(ed: Editor) -> None:
    before = tree_snapshot(ed.config.parent)
    got = move(ed, "undo")
    assert got.status == 409
    assert body_of(got)["error"]["code"] == "nothing_to_undo"
    assert tree_snapshot(ed.config.parent) == before


def test_undo_with_a_stale_base_is_409_stale_with_fresh_state(ed: Editor) -> None:
    rename(ed, "One")
    before = ed.text
    got = ed.post({"base": sha(CONFIG)}, "/__edit/api/undo")
    assert got.status == 409
    data = body_of(got)
    assert data["error"]["code"] == "stale" and data["error"]["path"] == "base"
    assert data["state"]["hash"] == sha(before)
    assert ed.text == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"base": "nope"},
        {"base": "0" * 64, "extra": 1},
        {"base": "0" * 64, "anyway": "yes"},
        [],
    ],
)
def test_bad_undo_bodies_are_refused_without_writing(ed: Editor, body: Any) -> None:
    rename(ed, "One")
    before = tree_snapshot(ed.config.parent)
    got = ed.post(body, "/__edit/api/undo")
    assert got.status in (400, 422)
    assert set(body_of(got)["error"]) == {"code", "message", "path"}
    assert tree_snapshot(ed.config.parent) == before


def test_redo_takes_no_anyway(ed: Editor) -> None:
    rename(ed, "One")
    move(ed, "undo")
    got = move(ed, "redo", anyway=True)
    assert got.status == 422


# ---- the hash-equals-last guard and "restore anyway" -------------------------------


def test_undo_after_an_outside_edit_is_refused_and_offers_restore_anyway(
    ed: Editor,
) -> None:
    rename(ed, "One")
    outside = outside_edit(ed)
    assert state(ed)["outside_edit"] is True
    before = tree_snapshot(ed.config.parent)
    got = move(ed, "undo")
    assert got.status == 409
    data = body_of(got)
    assert data["error"]["code"] == "outside_edit"
    assert data["restore_anyway"] is True
    assert data["state"]["hash"] == sha(outside)
    assert "changed outside" in data["error"]["message"]
    assert ed.text == outside
    assert tree_snapshot(ed.config.parent) == before  # nothing at all was written


def test_redo_after_an_outside_edit_is_refused_too(ed: Editor) -> None:
    rename(ed, "One")
    move(ed, "undo")
    outside = outside_edit(ed)
    got = move(ed, "redo")
    assert got.status == 409
    assert body_of(got)["error"]["code"] == "outside_edit"
    assert ed.text == outside


def test_restore_anyway_snapshots_the_outside_text_first_and_is_undoable(
    ed: Editor,
) -> None:
    rename(ed, "One")
    changed = ed.text
    outside = outside_edit(ed)
    got = move(ed, "undo", anyway=True)
    assert got.status == 200
    assert ed.text == CONFIG
    assert sha(outside)[:8] in "".join(
        p.name for p in History(ed.config).directory.iterdir()
    )
    assert move(ed, "redo").status == 200 and ed.text == changed
    assert move(ed, "redo").status == 200 and ed.text == outside


def test_anyway_with_a_stale_base_still_refuses(ed: Editor) -> None:
    rename(ed, "One")
    seen = sha(ed.text)
    outside_edit(ed)
    again = ed.text + "# and more\n"
    ed.config.write_text(again, encoding="utf-8")
    got = ed.post({"base": seen, "anyway": True}, "/__edit/api/undo")
    assert got.status == 409 and body_of(got)["error"]["code"] == "stale"
    assert ed.text == again


def test_restore_anyway_can_repair_a_broken_outside_edit(ed: Editor) -> None:
    rename(ed, "One")
    ed.config.write_text("tabs: [oops\n", encoding="utf-8")
    assert move(ed, "undo").status == 409
    got = move(ed, "undo", anyway=True)
    assert got.status == 200 and ed.text == CONFIG


def test_a_tampered_snapshot_is_refused_and_writes_nothing(ed: Editor) -> None:
    rename(ed, "One")
    hist = History(ed.config)
    snap = next(
        p for p in hist.directory.iterdir() if p.name.startswith("0001-")
    )  # the "start" state
    # the store checks each snapshot against its recorded hash before restoring
    snap.write_text("tabs: [oops\n", encoding="utf-8")
    before = ed.text
    got = move(ed, "undo")
    assert got.status == 409
    assert body_of(got)["error"]["code"] == "snapshot_corrupt"
    assert ed.text == before


# ---- the mutation guard ------------------------------------------------------------


def test_undo_and_redo_need_the_token_and_origin(ed: Editor) -> None:
    rename(ed, "One")
    before = tree_snapshot(ed.config.parent)
    payload = json.dumps({"base": sha(ed.text)}).encode()
    for route in ("undo", "redo"):
        no_token = request(
            ed.session.port,
            "POST",
            f"/__edit/api/{route}",
            {"Content-Type": "application/json"},
            payload,
            same_origin=True,
        )
        assert no_token.status == 401
        no_origin = request(
            ed.session.port,
            "POST",
            f"/__edit/api/{route}",
            {"X-RA-Token": ed.session.token, "Content-Type": "application/json"},
            payload,
        )
        assert no_origin.status == 403
    assert request(
        ed.session.port,
        "POST",
        "/__edit/api/history",
        {"X-RA-Token": ed.session.token},
        b"{}",
        same_origin=True,
    ).status in (404, 405, 415)
    assert tree_snapshot(ed.config.parent) == before


def test_undo_and_redo_count_toward_the_mutation_rate_limit(tmp_path: Path) -> None:
    editor = make(tmp_path)
    try:
        statuses = []
        for _ in range(21):
            statuses.append(move(editor, "undo").status)
        assert statuses[:20] == [409] * 20  # refused, but each one counts
        assert statuses[20] == 429
    finally:
        editor.session.shutdown()


# ---- the core restore path ---------------------------------------------------------


def test_apply_restore_skips_the_one_change_check_but_not_the_loader(
    tmp_path: Path,
) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    other = CONFIG.replace("Example Creator", "Someone Else")
    result = editing.apply(
        config,
        lambda text, raw: (other, {}),
        dry_run=False,
        backup=True,
        force_path=True,
        restore=True,
    )
    assert result.written and config.read_text(encoding="utf-8") == other
    with pytest.raises(editing.EditError):
        editing.apply(
            config,
            lambda text, raw: ("tabs: [oops\n", {}),
            dry_run=False,
            backup=True,
            force_path=True,
            restore=True,
        )
    assert config.read_text(encoding="utf-8") == other
    with pytest.raises(editing.EditError):  # without restore the check still bites
        editing.apply(
            config,
            lambda text, raw: (CONFIG, {}),
            dry_run=False,
            backup=True,
            force_path=True,
        )
