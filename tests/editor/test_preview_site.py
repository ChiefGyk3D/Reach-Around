# SPDX-License-Identifier: Apache-2.0
"""The editor's preview listener serves the real built site (#204)."""

from __future__ import annotations

import json
import os
import re
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from editorharness import request
from reach_around import cli
from reach_around import preview as preview_module
from reach_around.editor import server as server_module
from reach_around.editor.server import EditorSession, preview_csp
from reach_around.preview import Preview

CONFIG = """\
version: 1
creator:
  name: Example Creator
tabs:
  - id: links
    title: Links
    blocks:
      - id: first
        title: First
        url: https://example.com/1
"""


class Rig:
    def __init__(self, session: EditorSession, config: Path) -> None:
        self.session = session
        self.config = config

    def api(self, path: str = "/__edit/api/state") -> dict[str, object]:
        got = request(
            self.session.port, "GET", path, {"X-RA-Token": self.session.token}
        )
        assert got.status == 200
        data = json.loads(got.body)
        assert isinstance(data, dict)
        return data

    def page(self, path: str = "/") -> tuple[int, bytes]:
        got = request(self.session.preview_port, "GET", path)
        return int(got.status or 0), got.body

    def rename(self, name: str) -> None:
        digest = str(self.api()["hash"])
        body = json.dumps(
            {
                "base": digest,
                "op": "set",
                "args": {"key": "creator.name", "value": name},
            }
        ).encode()
        headers = {
            "X-RA-Token": self.session.token,
            "Content-Type": "application/json",
        }
        got = request(
            self.session.port,
            "POST",
            "/__edit/api/op",
            headers,
            body,
            same_origin=True,
        )
        assert got.status == 200


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    session = EditorSession(config)
    session.start()
    try:
        yield Rig(session, config)
    finally:
        session.shutdown()


def test_preview_shows_the_built_page_and_its_stylesheet(rig: Rig) -> None:
    status, body = rig.page()
    assert status == 200
    assert b"Example Creator" in body
    assert b"The preview appears here." not in body
    link = re.search(rb'href="(static/site\.[0-9a-f]{20}\.css)"', body)
    assert link
    css_status, css = rig.page("/" + link.group(1).decode())
    assert css_status == 200 and css


def test_an_edit_is_built_and_shown_without_a_restart(rig: Rig) -> None:
    rig.rename("Renamed Person")
    assert rig.api()["build"] == {"ok": True, "error": None}
    assert b"Renamed Person" in rig.page()[1]


def test_a_failing_build_is_reported_and_the_last_good_page_stays(rig: Rig) -> None:
    assert b"Example Creator" in rig.page()[1]
    rig.config.write_text("title: Example\n", encoding="utf-8")
    build = rig.api()["build"]
    assert isinstance(build, dict)
    assert build["ok"] is False and build["error"]
    assert str(rig.config.parent) not in str(build["error"])
    assert b"Example Creator" in rig.page()[1]
    rig.config.write_text(CONFIG.replace("Example Creator", "Back Again"))
    assert rig.api()["build"] == {"ok": True, "error": None}
    assert b"Back Again" in rig.page()[1]


def test_a_build_failure_after_a_loading_config_is_reported(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("the build broke")

    monkeypatch.setattr(preview_module, "build_site", boom)
    rig.config.write_text(CONFIG.replace("Example Creator", "Other"))
    for route in ("/__edit/api/state", "/__edit/api/version"):
        build = rig.api(route)["build"]
        assert isinstance(build, dict)
        assert build["ok"] is False
    assert "the build broke" in str(rig.api()["build"])
    assert b"Example Creator" in rig.page()[1]


def test_without_any_good_build_the_preview_shows_a_placeholder(
    tmp_path: Path,
) -> None:
    config = tmp_path / "site.yaml"
    config.write_text("title: Example\n", encoding="utf-8")
    session = EditorSession(config)
    session.start()
    try:
        got = request(session.preview_port, "GET", "/")
        assert got.status == 200
        assert b"preview" in got.body.lower()
        assert request(session.preview_port, "GET", "/missing").status == 404
    finally:
        session.shutdown()


def test_the_preview_origin_cannot_reach_the_editor_api(rig: Rig) -> None:
    port = rig.session.preview_port
    for path in (
        "/__edit/",
        "/__edit/api/state",
        "/__edit/api/version",
        "/__edit/api/history",
        "/__edit/app.js",
    ):
        got = request(port, "GET", path, {"X-RA-Token": rig.session.token})
        assert got.status == 404
        assert rig.session.token.encode() not in got.body
    got = request(port, "POST", "/__edit/api/op", {"Content-Type": "application/json"})
    assert got.status == 405
    page = request(port, "GET", "/")
    assert rig.session.token not in page.body.decode()
    assert rig.session.token not in "".join(page.headers.values())
    assert "access-control-allow-origin" not in page.headers


def test_the_preview_refuses_traversal_and_does_not_leak_the_config(rig: Rig) -> None:
    port = rig.session.preview_port
    for path in (
        "/../site.yaml",
        "/%2e%2e/site.yaml",
        "/..%2fsite.yaml",
        "/static/../../site.yaml",
        "/site.yaml",
        "//etc/passwd",
        "/%00",
        "/..\\site.yaml",
    ):
        got = request(port, "GET", path)
        assert got.status == 404, path
        assert b"Example Creator" not in got.body, path
        assert b"version: 1" not in got.body, path


def test_the_preview_policy_blocks_remote_images_by_default() -> None:
    policy = preview_csp("http://127.0.0.1:1")
    assert "img-src 'self' data:" in policy
    assert "https:" not in policy
    assert "unsafe" not in policy
    assert "sandbox allow-scripts;" in policy
    assert "allow-same-origin" not in policy


def test_the_remote_image_flag_widens_only_the_image_source() -> None:
    policy = preview_csp("http://127.0.0.1:1", allow_remote_images=True)
    assert "img-src 'self' data: https:" in policy
    assert "default-src 'self'" in policy
    assert policy.count("https:") == 1


def test_the_listener_sends_the_policy_for_the_session_choice(
    tmp_path: Path,
) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    for allow in (False, True):
        session = EditorSession(config, allow_remote_images=allow)
        session.start()
        try:
            got = request(session.preview_port, "GET", "/")
            policy = got.headers["content-security-policy"]
            assert ("img-src 'self' data: https:" in policy) is allow
            assert "img-src 'self' data:" in policy
        finally:
            session.shutdown()


def test_the_flag_reaches_the_command_line() -> None:
    args = cli._parser().parse_args(["edit", "--config", "x.yaml"])
    assert args.allow_remote_images is False
    args = cli._parser().parse_args(
        ["edit", "--config", "x.yaml", "--allow-remote-images"]
    )
    assert args.allow_remote_images is True


def test_a_preview_without_a_port_builds_but_does_not_listen(tmp_path: Path) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    site = Preview(config, "127.0.0.1", None)
    try:
        assert site.error is None
        assert (site.root / "index.html").is_file()
        assert site.root.is_relative_to(site.workdir)
    finally:
        site.close()
    assert not site.workdir.exists()


def test_shutdown_removes_the_build_directory(tmp_path: Path) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    session = EditorSession(config)
    session.start()
    work = session.site_workdir
    assert work is not None and work.exists()
    session.shutdown()
    assert not work.exists()


# -- the file-serving sink (Daybreak round 1, #204) -----------------------------


def _build_file(rig: Rig, name: str, data: bytes) -> Path:
    path = rig.session.site.root / name  # type: ignore[union-attr]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_a_symlink_that_escapes_the_build_root_is_refused(
    rig: Rig, tmp_path: Path
) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET-OUTSIDE", encoding="utf-8")
    link = rig.session.site.root / "leak.txt"  # type: ignore[union-attr]
    link.symlink_to(secret)
    got = request(rig.session.preview_port, "GET", "/leak.txt")
    assert got.status == 404
    assert b"SECRET-OUTSIDE" not in got.body


def test_the_open_refuses_a_symlink_swapped_in_after_the_path_check(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET-OUTSIDE", encoding="utf-8")
    link = tmp_path / "page.txt"
    link.symlink_to(secret)
    assert server_module._open_site_file(link) is None


def test_the_open_refuses_a_path_that_names_another_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "page.txt"
    page.write_text("page", encoding="utf-8")
    other = tmp_path / "other.txt"
    other.write_text("other", encoding="utf-8")
    real_lstat = os.lstat
    monkeypatch.setattr(
        os,
        "lstat",
        lambda path, *a, **k: real_lstat(other) if path == page else real_lstat(path),
    )
    assert server_module._open_site_file(page) is None


def test_the_open_refuses_a_directory_and_a_fifo(tmp_path: Path) -> None:
    assert server_module._open_site_file(tmp_path) is None
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    assert server_module._open_site_file(fifo) is None


def test_an_open_descriptor_keeps_the_old_object_when_the_build_is_replaced(
    tmp_path: Path,
) -> None:
    root = tmp_path / "build"
    root.mkdir()
    page = root / "page.txt"
    page.write_text("old build", encoding="utf-8")
    opened = server_module._open_site_file(page)
    assert opened is not None
    fd, size = opened
    try:
        moved = tmp_path / "moved"
        root.rename(moved)
        root.mkdir()
        (root / "page.txt").write_text("OUTSIDE-NEW", encoding="utf-8")
        assert os.read(fd, size) == b"old build"
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "path",
    [
        "/%252e%252e/site.yaml",
        "/%2e%2E/site.yaml",
        "/.%2e/site.yaml",
        "/%2e./site.yaml",
        "/..%5csite.yaml",
        "/static%5c..%5c..%5csite.yaml",
        "/%5c%5cserver%5cshare",
        "/a%00.html",
        "/index.html%00.txt",
        "/./index.html",
        "/static/./x.css",
        "/static/../index.html",
        "/%252fetc%252fpasswd",
        "/..;/site.yaml",
    ],
)
def test_encoded_and_mixed_traversal_never_reaches_a_file_outside(
    rig: Rig, path: str
) -> None:
    got = request(rig.session.preview_port, "GET", path)
    assert got.status == 404, path
    assert b"version: 1" not in got.body
    assert b"Example Creator" not in got.body


def test_a_directory_path_is_not_listed(rig: Rig) -> None:
    root = rig.session.site.root  # type: ignore[union-attr]
    (root / "emptydir").mkdir()
    for path in ("/emptydir", "/emptydir/", "/static", "/static/"):
        got = request(rig.session.preview_port, "GET", path)
        assert got.status in (200, 404), path
        assert b"Index of" not in got.body
        assert b"site.yaml" not in got.body
    assert request(rig.session.preview_port, "GET", "/emptydir/").status == 404


def test_head_sends_the_headers_and_no_body(rig: Rig) -> None:
    full = request(rig.session.preview_port, "GET", "/")
    head = request(rig.session.preview_port, "HEAD", "/")
    assert head.status == 200
    assert head.body == b""
    assert head.headers["content-length"] == str(len(full.body))


def test_a_range_header_is_ignored_and_the_whole_file_is_sent(rig: Rig) -> None:
    _build_file(rig, "ten.txt", b"0123456789")
    got = request(rig.session.preview_port, "GET", "/ten.txt", {"Range": "bytes=2-4"})
    assert got.status == 200
    assert got.body == b"0123456789"
    assert "content-range" not in got.headers
    assert got.headers["content-length"] == "10"


def test_a_file_over_the_size_cap_is_refused_without_reading_it(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = rig.session.site.root / "huge.bin"  # type: ignore[union-attr]
    with path.open("wb") as handle:
        handle.truncate(server_module.SITE_FILE_CAP + 1)  # sparse: no disk used
    reads: list[int] = []
    real_read = os.read

    def spy(fd: int, count: int) -> bytes:
        reads.append(count)
        return real_read(fd, count)

    monkeypatch.setattr(server_module, "_read_fd", spy)
    got = request(rig.session.preview_port, "GET", "/huge.bin")
    assert got.status == 413
    assert reads == []
    assert len(got.body) < 1000


def test_a_big_file_is_streamed_in_bounded_chunks(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk = 4096
    monkeypatch.setattr(server_module, "_FILE_CHUNK", chunk)
    data = bytes(range(256)) * 80 + b"tail"  # five chunks and a bit
    assert len(data) > 5 * chunk
    _build_file(rig, "big.bin", data)
    writes: list[int] = []
    writer: Any = vars(socketserver)["_SocketWriter"]
    real_write = writer.write

    def spy(self: object, payload: bytes) -> int | None:
        writes.append(len(payload))
        return real_write(self, payload)  # type: ignore[no-any-return]

    monkeypatch.setattr(writer, "write", spy)
    got = request(rig.session.preview_port, "GET", "/big.bin")
    assert got.status == 200
    assert got.body == data
    assert got.headers["content-length"] == str(len(data))
    assert max(writes) <= chunk
    assert len(writes) >= len(data) // chunk


def test_file_streams_are_bounded_and_a_busy_preview_answers_503(rig: Rig) -> None:
    _build_file(rig, "one.txt", b"one")
    rig.session.file_slots = threading.BoundedSemaphore(1)
    assert request(rig.session.preview_port, "GET", "/one.txt").status == 200
    assert request(rig.session.preview_port, "GET", "/one.txt").status == 200
    assert rig.session.file_slots.acquire(blocking=False)
    got = request(rig.session.preview_port, "GET", "/one.txt")
    assert got.status == 503
    assert b"one" != got.body


def test_a_foreign_host_is_refused_on_both_listeners(rig: Rig) -> None:
    session = rig.session
    for port, token in ((session.port, session.token), (session.preview_port, "")):
        for host in (
            "evil.example",
            f"evil.example:{port}",
            f"127.0.0.1:{port + 1}",
            "127.0.0.1",
            f"localhost.evil.example:{port}",
        ):
            got = request(port, "GET", "/", {"X-RA-Token": token}, host=host)
            assert got.status == 421, (port, host)
        assert request(port, "GET", "/", host=None).status in (400, 421)
    other = session.preview_port if session.port != session.preview_port else 0
    cross = request(
        session.port, "GET", "/", host=f"127.0.0.1:{other}"
    )  # the preview's name sent to the editor
    assert cross.status == 421


def test_the_preview_origin_is_refused_when_it_calls_the_editor_port(
    rig: Rig,
) -> None:
    session = rig.session
    origin = f"http://127.0.0.1:{session.preview_port}"
    headers = {"X-RA-Token": session.token, "Origin": origin}
    got = request(session.port, "GET", "/__edit/api/state", headers)
    assert got.status == 403
    cross = {**headers, "Sec-Fetch-Site": "cross-site"}
    assert request(session.port, "GET", "/__edit/api/state", cross).status == 403
    post = {**cross, "Content-Type": "application/json"}
    got = request(session.port, "POST", "/__edit/api/op", post, b"{}")
    assert got.status == 403


def test_the_preview_port_neither_needs_nor_honors_the_editor_token(
    rig: Rig,
) -> None:
    port = rig.session.preview_port
    plain = request(port, "GET", "/")
    with_token = request(port, "GET", "/", {"X-RA-Token": rig.session.token})
    assert plain.status == with_token.status == 200
    assert plain.body == with_token.body
    api = request(port, "GET", "/__edit/api/state", {"X-RA-Token": rig.session.token})
    assert api.status == 404


def test_the_preview_sets_no_cookies(rig: Rig) -> None:
    _build_file(rig, "one.txt", b"one")
    for path in ("/", "/one.txt", "/missing", "/%00"):
        got = request(rig.session.preview_port, "GET", path)
        assert "set-cookie" not in got.headers, path


def test_error_pages_do_not_echo_the_request(rig: Rig) -> None:
    marker = "zq9xmarker"
    port = rig.session.preview_port
    answers = [
        request(port, "GET", f"/{marker}"),
        request(port, "GET", f"/{marker}/..%2f{marker}"),
        request(port, "POST", f"/{marker}"),
        request(port, "GET", f"/{marker}", {"X-Probe": marker}, host=f"{marker}:1"),
    ]
    for got in answers:
        assert got.status is not None and got.status >= 400
        assert marker.encode() not in got.body
        assert marker not in "".join(got.headers.values())


def test_the_flag_is_forwarded_to_the_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, object]] = []

    def fake(config: Path, **kwargs: object) -> int:
        seen.append({"config": config, **kwargs})
        return 0

    monkeypatch.setattr(cli, "run_editor", fake)
    assert cli.main(["edit", "--config", "x.yaml", "--allow-remote-images"]) == 0
    assert cli.main(["edit", "--config", "x.yaml"]) == 0
    assert seen[0]["allow_remote_images"] is True
    assert seen[1]["allow_remote_images"] is False


def test_a_config_changed_during_start_is_rebuilt_at_the_first_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")

    def racing(path: Path, host: str, port: int | None) -> Preview:
        config.write_text(CONFIG.replace("Example Creator", "Raced Person"))
        site = Preview(path, host, port)
        config.write_text(CONFIG, encoding="utf-8")  # restored before the first load
        return site

    monkeypatch.setattr("reach_around.editor.server.Preview", racing)
    session = EditorSession(config)
    session.start()
    try:
        assert b"Raced Person" in request(session.preview_port, "GET", "/").body
        got = request(
            session.port, "GET", "/__edit/api/state", {"X-RA-Token": session.token}
        )
        assert got.status == 200
        page = request(session.preview_port, "GET", "/").body
        assert b"Example Creator" in page
        assert b"Raced Person" not in page
    finally:
        session.shutdown()


def test_a_failed_start_removes_the_build_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    made: list[Path] = []

    def recording(path: Path, host: str, port: int | None) -> Preview:
        site = Preview(path, host, port)
        made.append(site.workdir)
        return site

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("no listener")

    monkeypatch.setattr("reach_around.editor.server.Preview", recording)
    monkeypatch.setattr(EditorSession, "_listen", boom)
    session = EditorSession(config)
    with pytest.raises(OSError):
        session.start()
    assert len(made) == 1
    assert not made[0].exists()
