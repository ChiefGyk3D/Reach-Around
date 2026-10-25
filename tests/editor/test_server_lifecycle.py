# SPDX-License-Identifier: Apache-2.0
"""Binding, connection cap, idle timeout, shutdown, URL printing, CLI wiring."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from editorharness import CONFIG_TEXT, request, running_editor
from reach_around.cli import main
from reach_around.editor.server import EditorError, EditorSession


def port_is_closed(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_for(condition: object, seconds: float = 5.0) -> bool:
    assert callable(condition)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_binds_loopback_only_on_random_distinct_ports(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        assert session.address == "127.0.0.1"
        assert session.editor_sockname()[0] == "127.0.0.1"
        assert session.preview_sockname()[0] == "127.0.0.1"
        assert session.port != session.preview_port
        assert session.port > 1023
        assert session.preview_port > 1023
    with running_editor(tmp_path) as other:
        assert other.port > 1023


@pytest.mark.parametrize(
    "host", [".".join(["0"] * 4), "192.0.2.1", "::", "", "localhost", "evil"]
)
def test_non_loopback_bind_is_refused_at_start(tmp_path: Path, host: str) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    with pytest.raises(EditorError):
        EditorSession(config, host=host)


def test_url_has_the_token_in_the_fragment_only(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        assert session.url == (
            f"http://127.0.0.1:{session.port}/__edit/#t={session.token}"
        )
        path_part = session.url.split("#")[0]
        assert session.token not in path_part


def test_idle_timeout_stops_listeners_and_frees_ports(tmp_path: Path) -> None:
    with running_editor(tmp_path, idle_seconds=0.3) as session:
        editor_port, preview_port = session.port, session.preview_port
        assert wait_for(session.stopped.is_set)
        assert wait_for(lambda: port_is_closed(editor_port))
        assert wait_for(lambda: port_is_closed(preview_port))
        assert session.stop_reason == "idle"


def test_idle_with_fake_clock_and_touch_and_polls(tmp_path: Path) -> None:
    now = [1000.0]
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    session = EditorSession(
        config, idle_seconds=60.0, clock=lambda: now[0], watchdog_interval=3600.0
    )
    session.start()
    try:
        now[0] += 59
        assert session.check_idle() is False
        # a read-only poll must not reset the timer
        request(session.port, "GET", "/__edit/api/state", {"X-RA-Token": session.token})
        now[0] += 2
        assert session.check_idle() is True
        assert session.stopped.is_set()
    finally:
        session.shutdown()


def test_touch_resets_the_idle_timer(tmp_path: Path) -> None:
    now = [0.0]
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    session = EditorSession(
        config, idle_seconds=60.0, clock=lambda: now[0], watchdog_interval=3600.0
    )
    session.start()
    try:
        now[0] = 50
        session.touch()
        now[0] = 100
        assert session.check_idle() is False
        now[0] = 111
        assert session.check_idle() is True
    finally:
        session.shutdown()


def test_default_idle_timeout_is_thirty_minutes(tmp_path: Path) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    assert EditorSession(config).idle_seconds == 30 * 60


def test_shutdown_is_clean_and_idempotent(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        editor_port, preview_port = session.port, session.preview_port
        threads_before = threading.active_count()
        session.shutdown()
        session.shutdown()
        assert session.stopped.is_set()
        assert port_is_closed(editor_port)
        assert port_is_closed(preview_port)
        assert threading.active_count() < threads_before
    assert (tmp_path / "site.yaml").read_text() == CONFIG_TEXT


def test_wait_returns_after_shutdown_from_another_thread(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        timer = threading.Timer(0.1, session.shutdown)
        timer.start()
        assert session.wait(timeout=5) is True
        timer.join()


def test_state_hashes_the_config_text(tmp_path: Path) -> None:
    import hashlib
    import json

    with running_editor(tmp_path) as session:
        got = request(
            session.port, "GET", "/__edit/api/state", {"X-RA-Token": session.token}
        )
        data = json.loads(got.body)
        assert data["hash"] == hashlib.sha256(CONFIG_TEXT.encode()).hexdigest()
        assert data["config"] == {}


def test_state_for_a_fifo_or_missing_config_does_not_hang_or_500(
    tmp_path: Path,
) -> None:
    with running_editor(tmp_path) as session:
        config = tmp_path / "site.yaml"
        config.unlink()
        os.mkfifo(config)
        got = request(
            session.port, "GET", "/__edit/api/state", {"X-RA-Token": session.token}
        )
        assert got.status is not None
        assert got.status < 500
        config.unlink()
        got = request(
            session.port, "GET", "/__edit/api/state", {"X-RA-Token": session.token}
        )
        assert got.status is not None
        assert got.status < 500


def test_connection_cap_does_not_starve_the_server(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        held = []
        try:
            for _ in range(60):
                sock = socket.create_connection(("127.0.0.1", session.port))
                held.append(sock)  # never sends a byte
            assert session.active_connections() <= 32
        finally:
            for sock in held:
                sock.close()
        assert wait_for(lambda: session.active_connections() == 0, 15)
        assert request(session.port, "GET", "/__edit/").status == 200


def test_slow_client_is_cut_off_by_the_read_timeout(tmp_path: Path) -> None:
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    session = EditorSession(config, read_timeout=0.3)
    session.start()
    try:
        with socket.create_connection(("127.0.0.1", session.port)) as sock:
            sock.settimeout(5)
            sock.sendall(b"GET /__edit/ HTTP/1.1\r\nHost: 127")  # never finishes
            assert sock.recv(100) == b""  # server closed the connection
    finally:
        session.shutdown()


def test_cli_edit_prints_url_with_fragment_token_and_never_opens_a_browser(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import webbrowser

    opened: list[object] = []
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda *a, **k: opened.append(a))
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")

    # shorten the run: shut the session down once it prints its URL
    import reach_around.editor.server as server

    real_start = server.EditorSession.start

    def start_then_stop(self: EditorSession) -> None:
        real_start(self)
        threading.Timer(0.2, self.shutdown).start()

    monkeypatch.setattr(server.EditorSession, "start", start_then_stop)
    assert main(["edit", "--config", str(config)]) == 0
    out = capsys.readouterr()
    lines = [line for line in out.out.splitlines() if "/__edit/#t=" in line]
    assert len(lines) == 1
    assert lines[0].startswith("http://127.0.0.1:")
    token = lines[0].split("#t=")[1]
    assert token and token not in out.err
    assert opened == []


def test_cli_edit_refuses_a_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["edit", "--config", str(tmp_path / "nope.yaml")]) == 1
    assert "error:" in capsys.readouterr().err


def test_token_is_not_in_argv_or_environ_of_the_process(tmp_path: Path) -> None:
    with running_editor(tmp_path) as session:
        assert session.token not in " ".join(sys.argv)
        assert session.token not in "".join(os.environ.values())


def test_token_never_reaches_stdout_or_stderr_of_a_real_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reach_around.editor.server as server

    sessions: list[EditorSession] = []
    real_start = server.EditorSession.start

    def record(self: EditorSession) -> None:
        real_start(self)
        sessions.append(self)

    monkeypatch.setattr(server.EditorSession, "start", record)
    config = tmp_path / "site.yaml"
    config.write_text(CONFIG_TEXT, encoding="utf-8")
    worker = threading.Thread(
        target=main, args=(["edit", "--config", str(config)],), daemon=True
    )
    worker.start()
    assert wait_for(lambda: bool(sessions))
    session = sessions[0]
    for path in ("/__edit/", "/__edit/api/state", "/nope"):
        request(session.port, "GET", path, {"X-RA-Token": session.token})
        request(
            session.port,
            "GET",
            path,
            {"X-RA-Token": "bad-guess", "Origin": "http://evil.example"},
        )
    session.shutdown()
    worker.join(10)
    out = capsys.readouterr()
    assert out.out.count(session.token) == 1  # the one printed URL, nothing else
    assert session.token not in out.err
    assert "evil" not in out.out + out.err
