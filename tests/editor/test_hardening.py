# SPDX-License-Identifier: Apache-2.0
"""Editor route hardening (#197): history truthfulness, fixed error text, torn reads."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_routes import CONFIG, Editor, body_of, make, sha

from reach_around.editor_history import History


@pytest.fixture
def ed(tmp_path: Path) -> Iterator[Editor]:
    editor = make(tmp_path)
    try:
        yield editor
    finally:
        editor.session.shutdown()


# ---- history never claims a version that is no longer current -----------------------


def test_an_outside_write_before_the_history_record_is_not_called_recorded(
    ed: Editor, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = History.record_write
    outside = CONFIG.replace("# keep me", "# edited elsewhere")
    assert outside != CONFIG

    def racing(self: History, old: str, new: str, label: str) -> None:
        ed.config.write_text(outside, encoding="utf-8")
        real(self, old, new, label)

    monkeypatch.setattr(History, "record_write", racing)
    got = ed.op("set_link_field", {"id": "first", "field": "title", "value": "One"})
    assert got.status == 200
    data = body_of(got)
    assert data["state"]["hash"] == sha(ed.text) == sha(outside)
    assert data["history"]["recorded"] is False
    assert "warning" in data


def test_an_undisturbed_edit_is_still_recorded(ed: Editor) -> None:
    got = ed.op("set_link_field", {"id": "first", "field": "title", "value": "One"})
    data = body_of(got)
    assert data["history"]["recorded"] is True
    assert "warning" not in data


# ---- errors use fixed text and a generic path ---------------------------------------


def token_bodies(token: str) -> list[tuple[str, dict[str, Any]]]:
    base = sha(CONFIG)
    good = {"key": "creator.name", "value": "N"}
    return [
        ("op", {"base": base, "op": token, "args": {}}),
        ("args", {"base": base, "op": "set", "args": {**good, token: 1}}),
        (
            "expect",
            {
                "base": base,
                "op": "set",
                "args": good,
                "expect": {token: 1},
            },
        ),
        ("top", {"base": base, "op": "set", "args": good, token: 1}),
        (
            "link",
            {
                "base": base,
                "op": "add_link",
                "args": {"tab": "links", "link": {"title": "T", token: "x"}},
            },
        ),
        (
            "item",
            {
                "base": base,
                "op": "add_platform_item",
                "args": {"block": "x", "item": {token: "x"}},
            },
        ),
        ("undo", {"base": base, token: True}),
    ]


@pytest.mark.parametrize("which", range(7))
def test_validation_errors_never_echo_a_field_name(ed: Editor, which: int) -> None:
    label, body = token_bodies(ed.session.token)[which]
    route = "/__edit/api/undo" if label == "undo" else "/__edit/api/op"
    got = ed.post(body, route)
    assert got.status in (409, 422), label
    assert ed.session.token not in got.body.decode(), label
    assert ed.text == CONFIG


def test_a_hostile_field_name_with_markup_is_not_echoed(ed: Editor) -> None:
    name = "<script>alert(1)</script>"
    got = ed.op("set", {"key": "creator.name", "value": "N", name: 1})
    assert got.status == 422
    assert name not in got.body.decode()
    assert json.loads(got.body)["error"]["path"] == "args"


# ---- a read that never settles is a retryable error, not a mixed state --------------


def test_a_state_read_that_never_settles_is_503(
    ed: Editor, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = iter(range(1000))

    def moving(path: Path) -> bytes:
        return f"creator:\n  name: n{next(counter)}\n".encode()

    monkeypatch.setattr(ed.session.service, "_read", moving)
    got = ed.get("/__edit/api/state")
    assert got.status == 503
    assert body_of(got)["error"]["code"] == "busy"
    assert "hash" not in body_of(got)


def test_a_settled_read_is_still_200(ed: Editor) -> None:
    assert ed.get("/__edit/api/state").status == 200
