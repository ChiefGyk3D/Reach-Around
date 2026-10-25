# SPDX-License-Identifier: Apache-2.0
"""The state, version and op routes: contract, error shape, stale handling, caps."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from editorharness import Response, request, tree_snapshot
from reach_around import editing
from reach_around.editor.server import EditorSession
from reach_around.editor_history import History, Restored

CONFIG = """\
# a comment that must survive
version: 1
creator:
  name: Example Creator   # keep me
tabs:
  - id: links
    title: Links
    blocks:
      - id: first
        title: First
        url: https://example.com/1
      - id: second
        title: Second
        url: https://example.com/2
      - id: socials
        layout: chips
        items:
          - platform: twitch
            handle: examplecreator
            url: https://example.com/twitch
            count: 10
            as_of: 2027-03-01
          - platform: kick
            handle: examplecreator
            url: https://example.com/kick
"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Editor:
    def __init__(self, session: EditorSession, config: Path) -> None:
        self.session = session
        self.config = config

    @property
    def text(self) -> str:
        return self.config.read_text(encoding="utf-8")

    def get(self, path: str) -> Response:
        return request(
            self.session.port, "GET", path, {"X-RA-Token": self.session.token}
        )

    def post(self, body: object, path: str = "/__edit/api/op") -> Response:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers = {
            "X-RA-Token": self.session.token,
            "Content-Type": "application/json",
        }
        return request(self.session.port, "POST", path, headers, data, same_origin=True)

    def op(self, op: str, args: dict[str, Any], **extra: Any) -> Response:
        return self.post({"base": sha(self.text), "op": op, "args": args, **extra})


def make(tmp_path: Path, **kw: Any) -> Editor:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    session = EditorSession(config, **kw)
    session.start()
    return Editor(session, config)


@pytest.fixture
def ed(tmp_path: Path) -> Iterator[Editor]:
    editor = make(tmp_path)
    try:
        yield editor
    finally:
        editor.session.shutdown()


def body_of(got: Response) -> dict[str, Any]:
    data = json.loads(got.body)
    assert isinstance(data, dict)
    return data


# ---- state -------------------------------------------------------------------------


def test_state_contract(ed: Editor) -> None:
    got = ed.get("/__edit/api/state")
    assert got.status == 200
    data = body_of(got)
    assert data["hash"] == sha(CONFIG)
    assert data["config"]["creator"]["name"] == "Example Creator"
    assert data["config"]["tabs"][0]["blocks"][2]["items"][0]["as_of"] == "2027-03-01"
    assert data["preview_url"] == f"http://127.0.0.1:{ed.session.preview_port}/"
    assert data["build"] == {"ok": True, "error": None}
    assert data["limits"]["json_bytes"] == 64 * 1024
    assert isinstance(data["version"], int)
    assert isinstance(data["mtime"], float)


def test_state_of_an_invalid_config_still_answers(tmp_path: Path) -> None:
    editor = make(tmp_path)
    try:
        editor.config.write_text("title: Example\n", encoding="utf-8")
        data = body_of(editor.get("/__edit/api/state"))
        assert data["config"] == {}
        assert data["build"]["ok"] is False
        assert data["build"]["error"]
        assert data["hash"] == sha("title: Example\n")
        assert str(tmp_path) not in json.dumps(data)
    finally:
        editor.session.shutdown()


def test_state_preview_url_follows_the_host_used(ed: Editor) -> None:
    got = request(
        ed.session.port,
        "GET",
        "/__edit/api/state",
        {"X-RA-Token": ed.session.token},
        host=f"localhost:{ed.session.port}",
    )
    assert body_of(got)["preview_url"].startswith("http://localhost:")


# ---- version -----------------------------------------------------------------------


def test_version_changes_only_when_the_file_changes(ed: Editor) -> None:
    first = body_of(ed.get("/__edit/api/version"))
    again = body_of(ed.get("/__edit/api/version"))
    assert first == again
    assert first["hash"] == sha(CONFIG)
    ed.config.write_text(CONFIG + "# more\n", encoding="utf-8")
    changed = body_of(ed.get("/__edit/api/version"))
    assert changed["version"] != first["version"]
    assert changed["hash"] == sha(CONFIG + "# more\n")


def test_polling_does_not_reset_the_idle_timer_but_an_op_does(
    tmp_path: Path,
) -> None:
    now = [0.0]
    editor = make(tmp_path, idle_seconds=100.0, clock=lambda: now[0])
    try:
        now[0] = 60.0
        assert editor.get("/__edit/api/version").status == 200
        now[0] = 101.0
        assert editor.session.check_idle() is True
    finally:
        editor.session.shutdown()
    editor = make(tmp_path, idle_seconds=100.0, clock=lambda: now[0])
    try:
        now[0] = 160.0
        assert editor.op("set", {"key": "creator.name", "value": "New"}).status == 200
        now[0] = 220.0
        assert editor.session.check_idle() is False
    finally:
        editor.session.shutdown()


# ---- every op equals the core, byte for byte ---------------------------------------

Compute = Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]

CASES: list[tuple[str, dict[str, Any], Compute]] = [
    (
        "add_link",
        {"tab": "links", "link": {"title": "Third", "url": "https://example.com/3"}},
        lambda t, r: editing.add_link(
            t,
            r,
            tab="links",
            link={"id": "third", "title": "Third", "url": "https://example.com/3"},
            position=None,
        ),
    ),
    (
        "remove_link",
        {"id": "second"},
        lambda t, r: editing.remove_link(t, r, link_id="second"),
    ),
    (
        "move_link",
        {"id": "second", "position": 1},
        lambda t, r: editing.move_link(t, r, link_id="second", position=1),
    ),
    (
        "set",
        {"key": "creator.name", "value": "New Name"},
        lambda t, r: editing.set_value(t, r, key="creator.name", value="New Name"),
    ),
    (
        "set",
        {"key": "theme.background_dim", "value": 0.4},
        lambda t, r: editing.set_value(t, r, key="theme.background_dim", value="0.4"),
    ),
    (
        "set_link_field",
        {"id": "first", "field": "title", "value": "Renamed"},
        lambda t, r: editing.set_link_field(
            t, r, link_id="first", field="title", value="Renamed"
        ),
    ),
    (
        "set_link_field",
        {"id": "first", "field": "featured", "value": True},
        lambda t, r: editing.set_link_field(
            t, r, link_id="first", field="featured", value=True
        ),
    ),
    (
        "add_platform_item",
        {
            "block": "socials",
            "item": {"platform": "youtube", "handle": "x", "url": "https://e.com/y"},
            "position": 1,
        },
        lambda t, r: editing.add_platform_item(
            t,
            r,
            block="socials",
            item={"platform": "youtube", "handle": "x", "url": "https://e.com/y"},
            position=1,
        ),
    ),
    (
        "remove_platform_item",
        {"block": "socials", "index": 2},
        lambda t, r: editing.remove_platform_item(t, r, block="socials", index=2),
    ),
    (
        "move_platform_item",
        {"block": "socials", "index": 2, "position": 1},
        lambda t, r: editing.move_platform_item(
            t, r, block="socials", index=2, position=1
        ),
    ),
    (
        "set_platform_item_field",
        {"block": "socials", "index": 1, "field": "count", "value": 25},
        lambda t, r: editing.set_platform_item_field(
            t, r, block="socials", index=1, field="count", value=25
        ),
    ),
]


@pytest.mark.parametrize(("op", "args", "direct"), CASES)
def test_op_result_equals_the_core_result(
    tmp_path: Path, op: str, args: dict[str, Any], direct: Compute
) -> None:
    twin = tmp_path / "twin"
    twin.mkdir()
    twin_cfg = twin / "site.yaml"
    twin_cfg.write_text(CONFIG, encoding="utf-8")
    editing.apply(twin_cfg, direct, dry_run=False, backup=False, force_path=True)
    live = tmp_path / "live"
    live.mkdir()
    editor = make(live)
    try:
        got = editor.op(op, args)
        assert got.status == 200, got.body
        assert editor.text == twin_cfg.read_text(encoding="utf-8")
        assert "# a comment that must survive" in editor.text
        data = body_of(got)
        assert data["state"]["hash"] == sha(editor.text)
        assert data["history"]["hash"] == sha(editor.text)
        assert data["history"]["label"]
    finally:
        editor.session.shutdown()


def test_op_records_history_and_a_backup(ed: Editor) -> None:
    assert ed.op("set", {"key": "creator.name", "value": "New"}).status == 200
    assert (ed.config.parent / "site.yaml.bak").read_text() == CONFIG
    produced = ed.text
    restored = History(ed.config).undo(
        produced, lambda text: ed.config.write_text(text, encoding="utf-8")
    )
    assert isinstance(restored, Restored)
    assert ed.text == CONFIG


def test_op_uses_the_label_when_given(ed: Editor) -> None:
    got = ed.op(
        "set", {"key": "creator.name", "value": "New"}, label="Rename the creator"
    )
    assert body_of(got)["history"]["label"] == "Rename the creator"


# ---- the error shape ---------------------------------------------------------------


def error_of(got: Response) -> dict[str, Any]:
    error = body_of(got)["error"]
    assert set(error) >= {"code", "message", "path"}
    assert isinstance(error["message"], str)
    return dict(error)


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (b"not json", 400, "bad_json"),
        (b"[1]", 400, "bad_json"),
        (b'{"base": "a", "base": "b"}', 400, "bad_json"),
        (b'{"base": NaN}', 400, "bad_json"),
        (b"[" * 5000 + b"]" * 5000, 400, "bad_json"),
        (b"\xff\xfe", 400, "bad_json"),
        (b"{}", 422, "bad_request"),
        (b'{"base": "xyz", "op": "set", "args": {}}', 422, "bad_request"),
    ],
)
def test_malformed_bodies_get_the_error_shape(
    ed: Editor, body: bytes, status: int, code: str
) -> None:
    before = tree_snapshot(ed.config.parent)
    got = ed.post(body)
    assert got.status == status
    assert error_of(got)["code"] == code
    assert tree_snapshot(ed.config.parent) == before


def test_unknown_op_and_unknown_arguments_are_422(ed: Editor) -> None:
    before = tree_snapshot(ed.config.parent)
    got = ed.op("write_yaml", {"text": "x: 1"})
    assert got.status == 422
    assert error_of(got)["code"] == "unknown_op"
    got = ed.op("set", {"key": "creator.name", "value": "N", "path": "/etc/x"})
    assert got.status == 422
    assert error_of(got)["code"] == "bad_argument"
    assert error_of(got)["path"] == "args.path"
    got = ed.op("set", {"key": "creator.name"})
    assert error_of(got)["path"] == "args.value"
    got = ed.op("move_link", {"id": "first", "position": "1"})
    assert error_of(got)["path"] == "args.position"
    got = ed.op("add_link", {"tab": "links", "link": {"title": "T", "evil": "x"}})
    assert got.status == 422
    assert tree_snapshot(ed.config.parent) == before


def test_a_key_outside_the_safe_list_is_refused(ed: Editor) -> None:
    got = ed.op("set", {"key": "tabs", "value": "x"})
    assert got.status == 422
    assert error_of(got)["code"] == "refused"
    assert ed.text == CONFIG


def test_a_core_refusal_is_422_refused_and_writes_nothing(ed: Editor) -> None:
    got = ed.op("move_link", {"id": "first", "position": 99})
    assert got.status == 422
    error = error_of(got)
    assert error["code"] == "refused"
    assert str(ed.config.parent) not in error["message"]
    assert ed.text == CONFIG
    assert not (ed.config.parent / ".reach-around-history").exists()


def test_errors_never_echo_the_token(ed: Editor) -> None:
    got = ed.op("set", {"key": "no.such.key", "value": "x"})
    assert ed.session.token not in got.body.decode()


def test_a_candidate_that_would_not_build_is_refused(ed: Editor) -> None:
    got = ed.op("set", {"key": "theme.mode", "value": "purple"})
    assert got.status == 422
    assert ed.text == CONFIG


# ---- base and expect ---------------------------------------------------------------


def test_unrelated_outside_edit_is_merged_when_expect_still_matches(ed: Editor) -> None:
    base = sha(ed.text)
    ed.config.write_text(
        ed.text.replace("# a comment that must survive", "# changed outside"),
        encoding="utf-8",
    )
    got = ed.post(
        {
            "base": base,
            "op": "set",
            "args": {"key": "creator.name", "value": "Mine"},
            "expect": {"value": "Example Creator"},
        }
    )
    assert got.status == 200, got.body
    assert "# changed outside" in ed.text
    assert "name: Mine" in ed.text


def test_outside_edit_of_the_same_field_is_409_stale_with_fresh_state(
    ed: Editor,
) -> None:
    base = sha(ed.text)
    ed.config.write_text(
        ed.text.replace("Example Creator", "Someone Else"), encoding="utf-8"
    )
    outside = ed.text
    got = ed.post(
        {
            "base": base,
            "op": "set",
            "args": {"key": "creator.name", "value": "Mine"},
            "expect": {"value": "Example Creator"},
        }
    )
    assert got.status == 409
    data = body_of(got)
    assert data["error"]["code"] == "stale"
    assert data["error"]["path"] == "expect.value"
    assert data["state"]["hash"] == sha(outside)
    assert data["state"]["config"]["creator"]["name"] == "Someone Else"
    assert ed.text == outside


def test_a_changed_base_without_expect_is_stale(ed: Editor) -> None:
    base = sha(ed.text)
    ed.config.write_text(ed.text + "# x\n", encoding="utf-8")
    got = ed.post(
        {"base": base, "op": "set", "args": {"key": "creator.name", "value": "M"}}
    )
    assert got.status == 409
    assert "# x" in ed.text


def test_expect_for_each_list_op(ed: Editor) -> None:
    base = sha(ed.text)
    ed.config.write_text(ed.text + "# x\n", encoding="utf-8")
    ok = ed.post(
        {
            "base": base,
            "op": "move_link",
            "args": {"id": "second", "position": 1},
            "expect": {"order": ["first", "second", "socials"]},
        }
    )
    assert ok.status == 200
    base = sha(ed.text)
    ed.config.write_text(ed.text + "# y\n", encoding="utf-8")
    stale = ed.post(
        {
            "base": base,
            "op": "move_link",
            "args": {"id": "second", "position": 2},
            "expect": {"order": ["first", "second", "socials"]},
        }
    )
    assert stale.status == 409


def test_expect_with_unknown_keys_is_422(ed: Editor) -> None:
    got = ed.op(
        "set",
        {"key": "creator.name", "value": "M"},
        expect={"value": "Example Creator", "extra": 1},
    )
    assert got.status == 422
    assert error_of(got)["path"] == "expect.extra"


def test_a_matching_base_ignores_expect(ed: Editor) -> None:
    got = ed.op("set", {"key": "creator.name", "value": "M"}, expect={"value": "wrong"})
    assert got.status == 200


def test_a_link_removed_outside_makes_the_op_stale_not_a_500(ed: Editor) -> None:
    base = sha(ed.text)
    ed.config.write_text(
        ed.text.replace(
            "      - id: second\n        title: Second\n"
            "        url: https://example.com/2\n",
            "",
        ),
        encoding="utf-8",
    )
    got = ed.post(
        {
            "base": base,
            "op": "move_link",
            "args": {"id": "second", "position": 1},
            "expect": {"order": ["first", "second", "socials"]},
        }
    )
    assert got.status == 409


# ---- the guard runs before every new route -----------------------------------------

ROUTES = [
    ("GET", "/__edit/api/state"),
    ("GET", "/__edit/api/version"),
    ("POST", "/__edit/api/op"),
]


def hit(
    ed: Editor, method: str, path: str, headers: dict[str, str], body: bytes = b"{}"
) -> Response:
    sent = dict(headers)
    if method == "POST":
        sent.setdefault("Content-Type", "application/json")
    return request(
        ed.session.port, method, path, sent, body if method == "POST" else b""
    )


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_no_token_is_401_and_nothing_is_written(
    ed: Editor, method: str, path: str
) -> None:
    before = tree_snapshot(ed.config.parent)
    origin = {"Origin": f"http://127.0.0.1:{ed.session.port}"}
    assert hit(ed, method, path, origin).status == 401
    assert hit(ed, method, path, {**origin, "X-RA-Token": "wrong"}).status == 401
    assert tree_snapshot(ed.config.parent) == before


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_wrong_origin_is_403_and_nothing_is_written(
    ed: Editor, method: str, path: str
) -> None:
    before = tree_snapshot(ed.config.parent)
    for origin in ("http://evil.example", f"http://127.0.0.1:{ed.session.port + 1}"):
        headers = {"X-RA-Token": ed.session.token, "Origin": origin}
        assert hit(ed, method, path, headers).status == 403, origin
    assert tree_snapshot(ed.config.parent) == before


def test_op_without_origin_is_403(ed: Editor) -> None:
    headers = {"X-RA-Token": ed.session.token, "Content-Type": "application/json"}
    got = request(ed.session.port, "POST", "/__edit/api/op", headers, b"{}")
    assert got.status == 403


def test_oversize_op_body_is_413_and_nothing_is_written(ed: Editor) -> None:
    before = tree_snapshot(ed.config.parent)
    big = b'{"base": "' + b"a" * (64 * 1024) + b'"}'
    got = ed.post(big)
    assert got.status == 413
    assert tree_snapshot(ed.config.parent) == before


def test_get_on_op_and_post_on_state_are_not_routes(ed: Editor) -> None:
    assert ed.get("/__edit/api/op").status == 404
    assert ed.post({}, path="/__edit/api/state").status == 404
    assert ed.post({}, path="/__edit/api/version").status == 404


# ---- the rate limit ----------------------------------------------------------------


def test_the_21st_mutation_in_a_minute_is_429_with_retry_after(ed: Editor) -> None:
    for number in range(20):
        got = ed.op("set", {"key": "creator.name", "value": f"Name {number}"})
        assert got.status == 200, number
    got = ed.op("set", {"key": "creator.name", "value": "One more"})
    assert got.status == 429
    assert error_of(got)["code"] == "too_many_requests"
    assert 1 <= int(got.headers["retry-after"]) <= 60
    assert "Name 19" in ed.text
    # reads are not limited
    assert ed.get("/__edit/api/state").status == 200
    assert ed.get("/__edit/api/version").status == 200


def test_refused_mutations_count_toward_the_limit(ed: Editor) -> None:
    for _ in range(20):
        assert ed.post(b"nope").status == 400
    assert ed.post(b"nope").status == 429


def test_the_window_slides(tmp_path: Path) -> None:
    now = [0.0]
    editor = make(tmp_path, clock=lambda: now[0])
    try:
        for _ in range(20):
            assert editor.post(b"nope").status == 400
        assert editor.post(b"nope").status == 429
        now[0] = 61.0
        assert editor.post(b"nope").status == 400
    finally:
        editor.session.shutdown()
