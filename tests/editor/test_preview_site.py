# SPDX-License-Identifier: Apache-2.0
"""The editor's preview listener serves the real built site (#204)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from editorharness import request
from reach_around import cli
from reach_around import preview as preview_module
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
