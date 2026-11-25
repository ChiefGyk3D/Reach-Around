# SPDX-License-Identifier: Apache-2.0
"""scripts/verify-support-redirect.sh against throwaway 127.0.0.1 servers.

One server plays the old support host, one plays the new host. Every case
that the runbook relies on is covered, and each failing case must exit
non-zero (the verifier must never false-pass)."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-support-redirect.sh"


class Site:
    """A tiny server whose reply is chosen by a callable (path -> reply)."""

    def __init__(self, reply: Callable[[str], tuple[int, dict[str, str]]]) -> None:
        self.reply = reply
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self) -> None:  # noqa: N802
                status, headers = outer.reply(self.path)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def sites() -> Iterator[list[Site]]:
    made: list[Site] = []
    yield made
    for s in made:
        s.close()


def run(src: Site, dst: Site, **env: str) -> subprocess.CompletedProcess[str]:
    e = {
        "PATH": os.environ["PATH"],
        "SRC_HOST": src.host,
        "EXP_HOST": dst.host,
        "VERIFY_SCHEME": "http",
    }
    e.update(env)
    return subprocess.run(  # noqa: S603 - fixed argv, loopback only
        ["/usr/bin/bash", str(SCRIPT)],
        env=e,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


Reply = Callable[[str, str], tuple[int, dict[str, str]]]


def make(
    sites: list[Site], src_reply: Reply, dst_status: int = 200
) -> tuple[Site, Site]:
    dst = Site(lambda p: (dst_status, {}))
    src = Site(lambda p: src_reply(p, dst.host))
    sites.extend([dst, src])
    return src, dst


def good(status: int = 302) -> Reply:
    def reply(path: str, dst: str) -> tuple[int, dict[str, str]]:
        if path.startswith("/?"):
            return status, {"Location": f"http://{dst}/support/{path[1:]}"}
        return 200, {}

    return reply


def test_good_302_passes(sites: list[Site]) -> None:
    r = run(*make(sites, good()))
    assert r.returncode == 0, r.stderr
    assert "all checks passed" in r.stdout


def test_good_301_passes_only_when_expected(sites: list[Site]) -> None:
    src, dst = make(sites, good(301))
    assert run(src, dst).returncode == 1
    assert run(src, dst, EXPECT_STATUS="301").returncode == 0


def test_wrong_host_fails(sites: list[Site]) -> None:
    r = run(
        *make(
            sites,
            lambda p, d: (302, {"Location": f"http://127.0.0.1:1/support/{p[1:]}"}),
        )
    )
    assert r.returncode == 1 and "not exactly" in r.stderr


def test_relative_location_fails(sites: list[Site]) -> None:
    r = run(*make(sites, lambda p, d: (302, {"Location": f"/support/{p[1:]}"})))
    assert r.returncode == 1


def test_missing_location_fails(sites: list[Site]) -> None:
    r = run(*make(sites, lambda p, d: (302, {})))
    assert r.returncode == 1 and "no Location" in r.stderr


def test_query_dropped_fails(sites: list[Site]) -> None:
    r = run(*make(sites, lambda p, d: (302, {"Location": f"http://{d}/support/"})))
    assert r.returncode == 1


def test_query_changed_fails(sites: list[Site]) -> None:
    r = run(*make(sites, lambda p, d: (302, {"Location": f"http://{d}/support/?x=1"})))
    assert r.returncode == 1


def test_hardcoded_destination_query_fails(sites: list[Site]) -> None:
    """A rule that hard-codes one query must not pass a different probe."""

    def reply(path: str, dst: str) -> tuple[int, dict[str, str]]:
        return 302, {"Location": f"http://{dst}/support/?probe=a%2Fb&probe=two"}

    assert run(*make(sites, reply)).returncode == 1


def test_trailing_slash_difference_fails(sites: list[Site]) -> None:
    r = run(
        *make(sites, lambda p, d: (302, {"Location": f"http://{d}/support{p[1:]}"}))
    )
    assert r.returncode == 1


def test_host_case_difference_fails(sites: list[Site]) -> None:
    # 127.0.0.1 has no letters, so upper-case the scheme to prove case matters.
    def reply2(path: str, dst: str) -> tuple[int, dict[str, str]]:
        return 302, {"Location": f"HTTP://{dst}/support/{path[1:]}"}

    assert run(*make(sites, reply2)).returncode == 1


def test_source_answers_200_with_location_fails(sites: list[Site]) -> None:
    """Review finding 1: a 200 carrying a good Location must not pass."""

    def reply(path: str, dst: str) -> tuple[int, dict[str, str]]:
        return 200, {"Location": f"http://{dst}/support/{path[1:]}"}

    r = run(*make(sites, reply))
    assert r.returncode == 1 and "expected 302" in r.stderr


def test_other_path_redirecting_to_support_fails(sites: list[Site]) -> None:
    def reply(path: str, dst: str) -> tuple[int, dict[str, str]]:
        if path.startswith("/?"):
            return 302, {"Location": f"http://{dst}/support/{path[1:]}"}
        return 302, {"Location": f"http://{dst}/support/"}

    r = run(*make(sites, reply))
    assert r.returncode == 1 and "other path" in r.stderr


def test_support_page_500_fails(sites: list[Site]) -> None:
    r = run(*make(sites, good(), dst_status=500))
    assert r.returncode == 1 and "expected 200" in r.stderr


def test_unreachable_source_fails(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    src.close()
    r = run(src, dst)
    assert r.returncode == 1 and "did not complete" in r.stderr


def test_missing_and_equal_hosts_are_usage_errors(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    assert run(src, dst, SRC_HOST="").returncode == 2
    assert run(src, dst, EXP_HOST=src.host).returncode == 2
    assert run(src, dst, EXP_HOST="bad host;x").returncode == 2
    assert run(src, dst, EXPECT_STATUS="307").returncode == 2


def test_http_refused_for_non_loopback(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    r = run(src, dst, SRC_HOST="support.example.com", EXP_HOST="links.example.com")
    assert r.returncode == 2
