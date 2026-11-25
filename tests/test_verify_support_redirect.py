# SPDX-License-Identifier: Apache-2.0
"""scripts/verify-support-redirect.sh against throwaway 127.0.0.1 servers.

One server plays the old support host, one plays the new host. Every case
that the runbook relies on is covered, and each failing case must exit
non-zero (the verifier must never false-pass). Each server records every
request it receives, so the passing case proves which requests were made."""

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

Headers = list[tuple[str, str]]
# (method, path) -> (status, headers); a list so a header can repeat.
SiteReply = Callable[[str, str], tuple[int, Headers]]

PROBE_1 = "/?probe=a%2Fb&probe=two"
PROBE_2 = "/?q=x%20y&utm_source=mail&empty="
OTHER = "/other-path"


class Site:
    """A tiny server whose reply is chosen by a callable; it logs requests."""

    def __init__(self, reply: SiteReply) -> None:
        self.reply = reply
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                outer.requests.append((self.command, self.path))
                status, headers = outer.reply(self.command, self.path)
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                self._serve()

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve()

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
    return run_hosts(src.host, dst.host, **env)


def run_hosts(
    src: str,
    dst: str,
    *,
    script: Path = SCRIPT,
    **env: str,
) -> subprocess.CompletedProcess[str]:
    e = {
        "PATH": os.environ["PATH"],
        "SRC_HOST": src,
        "EXP_HOST": dst,
        "VERIFY_SCHEME": "http",
    }
    e.update(env)
    return subprocess.run(  # noqa: S603 - fixed argv, loopback only
        ["/usr/bin/bash", str(script)],
        env=e,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# A source reply sees (path, destination host) and returns (status, headers).
SrcReply = Callable[[str, str], tuple[int, Headers | dict[str, str]]]


def _hdrs(h: Headers | dict[str, str]) -> Headers:
    return list(h.items()) if isinstance(h, dict) else h


def make(
    sites: list[Site],
    src_reply: SrcReply,
    dst_status: int = 200,
    *,
    src_head: SrcReply | None = None,
    dst_head_status: int = 200,
) -> tuple[Site, Site]:
    """GET uses src_reply/dst_status. HEAD uses src_head/dst_head_status (default
    the same as GET for the source, 200 for the destination), so a case can
    answer HEAD correctly while GET is broken."""

    def dst_reply(method: str, path: str) -> tuple[int, Headers]:
        if path != "/support/":
            return 404, []
        return (dst_status if method == "GET" else dst_head_status), []

    dst = Site(dst_reply)

    def src_fn(method: str, path: str) -> tuple[int, Headers]:
        fn = src_head if (method == "HEAD" and src_head is not None) else src_reply
        status, h = fn(path, dst.host)
        return status, _hdrs(h)

    src = Site(src_fn)
    sites.extend([dst, src])
    return src, dst


def good(status: int = 302, other: int = 200) -> SrcReply:
    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path.startswith("/?"):
            return status, {"Location": f"http://{dst}/support/{path[1:]}"}
        return other, {}

    return reply


def test_good_302_passes_and_makes_exactly_the_expected_requests(
    sites: list[Site],
) -> None:
    src, dst = make(sites, good())
    r = run(src, dst)
    assert r.returncode == 0, r.stderr
    assert "all checks passed" in r.stdout
    # Both probes, the other path and /support/ were each requested, by GET only.
    assert src.requests == [("GET", PROBE_1), ("GET", PROBE_2), ("GET", OTHER)]
    assert dst.requests == [("GET", "/support/")]


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
    """A rule that hard-codes one query must not pass a different probe. The
    other path is healthy, so only the probe checks can produce the failure."""

    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path == OTHER:
            return 200, {}
        return 302, {"Location": f"http://{dst}/support/?probe=a%2Fb&probe=two"}

    r = run(*make(sites, reply))
    assert r.returncode == 1 and "not exactly" in r.stderr


def test_each_probe_is_checked_on_its_own(sites: list[Site]) -> None:
    """Only one probe is wrong: the other passes, and the run must still fail,
    so neither probe can be dropped or ignored unnoticed."""

    def only_second_wrong(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path == PROBE_2:
            return 302, {"Location": f"http://{dst}/support/"}
        return good()(path, dst)

    def only_first_wrong(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path == PROBE_1:
            return 302, {"Location": f"http://{dst}/support/"}
        return good()(path, dst)

    for fn in (only_second_wrong, only_first_wrong):
        src, dst = make(sites, fn)
        r = run(src, dst)
        assert r.returncode == 1 and "not exactly" in r.stderr


def test_trailing_slash_difference_fails(sites: list[Site]) -> None:
    r = run(
        *make(sites, lambda p, d: (302, {"Location": f"http://{d}/support{p[1:]}"}))
    )
    assert r.returncode == 1


def test_scheme_case_difference_in_location_fails(sites: list[Site]) -> None:
    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        return 302, {"Location": f"HTTP://{dst}/support/{path[1:]}"}

    assert run(*make(sites, reply)).returncode == 1


def test_source_answers_200_with_location_fails(sites: list[Site]) -> None:
    """A 200 carrying a good Location must not pass."""

    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        return 200, {"Location": f"http://{dst}/support/{path[1:]}"}

    r = run(*make(sites, reply))
    assert r.returncode == 1 and "expected 302" in r.stderr


def test_lowercase_location_header_name_is_accepted(sites: list[Site]) -> None:
    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path.startswith("/?"):
            return 302, [("location", f"http://{dst}/support/{path[1:]}")]
        return 200, {}

    assert run(*make(sites, reply)).returncode == 0


# --- GET, not HEAD ---------------------------------------------------------


def test_probe_get_broken_while_head_is_good_fails(sites: list[Site]) -> None:
    broken: SrcReply = lambda p, d: (500, {})  # noqa: E731
    src, dst = make(sites, broken, src_head=good())
    r = run(src, dst)
    assert r.returncode == 1
    assert src.requests and all(m == "GET" for m, _ in src.requests)


def test_probe_get_serves_old_page_while_head_redirects_fails(
    sites: list[Site],
) -> None:
    old_page: SrcReply = lambda p, d: (200, {})  # noqa: E731
    r = run(*make(sites, old_page, src_head=good()))
    assert r.returncode == 1 and "expected 302" in r.stderr


def test_support_get_500_while_head_200_fails(sites: list[Site]) -> None:
    src, dst = make(sites, good(), dst_status=500, dst_head_status=200)
    r = run(src, dst)
    assert r.returncode == 1 and "expected 200" in r.stderr
    assert dst.requests == [("GET", "/support/")]


# --- duplicate Location headers --------------------------------------------


def test_two_location_headers_from_a_real_server_fail(sites: list[Site]) -> None:
    """Whatever the installed curl does with two Location fields, the run fails."""

    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if not path.startswith("/?"):
            return 200, {}
        ok = ("Location", f"http://{dst}/support/{path[1:]}")
        return 302, [ok, ("Location", "http://evil.example/")]

    assert run(*make(sites, reply)).returncode == 1


def fake_curl_dir(tmp_path: Path, raw_headers: str) -> Path:
    """A directory holding a fake `curl` that prints raw_headers and exits 0, so
    the script's own header parser is tested whatever the real curl does. The
    headers are stored in a file; CRLF is written exactly as given."""
    d = tmp_path / "fakebin"
    d.mkdir()
    (d / "resp.txt").write_bytes(raw_headers.encode())
    sh = d / "curl"
    sh.write_text(
        "#!/usr/bin/bash\n"
        'hdr=""; prev=""; for a in "$@"; do '
        'if [ "$prev" = "-D" ]; then hdr="$a"; fi; prev="$a"; done\n'
        f'cat "{d / "resp.txt"}" > "$hdr"\n'
    )
    sh.chmod(0o755)
    return d


def script_with_curl(tmp_path: Path, curl: Path) -> Path:
    """Copy the verifier and replace only its trusted curl candidates."""
    script = tmp_path / "verify-support-redirect.sh"
    source = SCRIPT.read_text()
    source = source.replace(
        "readonly CURL_PRIMARY=/usr/bin/curl\nreadonly CURL_FALLBACK=/bin/curl\n",
        f"readonly CURL_PRIMARY={curl!s}\nreadonly CURL_FALLBACK={curl!s}\n",
    )
    assert source != SCRIPT.read_text()
    script.write_text(source)
    script.chmod(0o755)
    return script


def run_fake(
    tmp_path: Path, raw_headers: str, **env: str
) -> subprocess.CompletedProcess[str]:
    d = fake_curl_dir(tmp_path, raw_headers)
    return run_hosts(
        "127.0.0.1:1",
        "127.0.0.1:2",
        script=script_with_curl(tmp_path, d / "curl"),
        PATH=f"{d}:{os.environ['PATH']}",
        **env,
    )


GOOD_LOC = "http://127.0.0.1:2/support/?probe=a%2Fb&probe=two"


@pytest.mark.parametrize("first_ok", [True, False])
@pytest.mark.parametrize("name2", ["Location", "location", "LOCATION"])
def test_two_location_fields_fail_in_the_parser(
    tmp_path: Path, first_ok: bool, name2: str
) -> None:
    ok = f"Location: {GOOD_LOC}"
    evil = f"{name2}: http://evil.example/"
    lines = [ok, evil] if first_ok else [evil, ok]
    raw = "HTTP/1.1 302 Found\r\n" + "\r\n".join(lines) + "\r\n\r\n"
    r = run_fake(tmp_path, raw)
    assert r.returncode == 1 and "2 Location headers" in r.stderr


def test_two_identical_location_fields_fail_in_the_parser(tmp_path: Path) -> None:
    raw = f"HTTP/1.1 302 Found\r\nLocation: {GOOD_LOC}\r\nLocation: {GOOD_LOC}\r\n\r\n"
    r = run_fake(tmp_path, raw)
    assert r.returncode == 1 and "exactly one" in r.stderr


def test_single_location_with_crlf_and_odd_case_passes_probe_one(
    tmp_path: Path,
) -> None:
    """One field, any case, CRLF and padding: the probe-one check passes, so the
    failure must come later (probe two sees the same wrong Location)."""
    raw = f"HTTP/1.1 302 Found\r\nLoCaTiOn:   {GOOD_LOC}  \r\n\r\n"
    r = run_fake(tmp_path, raw)
    assert r.returncode == 1
    assert "ok: 302" in r.stdout and "not exactly" in r.stderr


@pytest.mark.parametrize("continuation", [" folded", "\tfolded"])
def test_obsolete_folded_location_fails(tmp_path: Path, continuation: str) -> None:
    raw = f"HTTP/1.1 302 Found\r\nLocation: {GOOD_LOC}\r\n{continuation}\r\n\r\n"
    r = run_fake(tmp_path, raw)
    assert r.returncode == 1 and "folded header" in r.stderr
    assert "ok: 302" not in r.stdout


def test_only_last_response_header_block_is_parsed(tmp_path: Path) -> None:
    raw = (
        "HTTP/1.1 200 Connection established\r\n"
        "Proxy-Agent: test\r\n\r\n"
        f"HTTP/1.1 302 Found\r\nLocation: {GOOD_LOC}\r\n\r\n"
    )
    r = run_fake(tmp_path, raw)
    assert r.returncode == 1
    assert "ok: 302" in r.stdout and "not exactly" in r.stderr


def test_final_header_block_without_status_line_fails(tmp_path: Path) -> None:
    raw = (
        "HTTP/1.1 302 Found\r\n"
        f"Location: {GOOD_LOC}\r\n\r\n"
        "not an HTTP response\r\n\r\n"
    )
    r = run_fake(tmp_path, raw)
    assert r.returncode == 1 and "no parsable HTTP status" in r.stderr


def test_unparsable_status_line_fails(tmp_path: Path) -> None:
    r = run_fake(tmp_path, "garbage without a status line\r\n\r\n")
    assert r.returncode == 1 and "no parsable HTTP status" in r.stderr


def test_proxy_environment_is_ignored_for_loopback_requests(
    sites: list[Site],
) -> None:
    src, dst = make(sites, good())
    bogus = "http://127.0.0.1:1"
    r = run(
        src,
        dst,
        http_proxy=bogus,
        https_proxy=bogus,
        HTTP_PROXY=bogus,
        HTTPS_PROXY=bogus,
        ALL_PROXY=bogus,
        NO_PROXY="",
    )
    assert r.returncode == 0, r.stderr
    assert src.requests == [("GET", PROBE_1), ("GET", PROBE_2), ("GET", OTHER)]
    assert dst.requests == [("GET", "/support/")]


def test_curl_is_called_with_timeouts_get_and_no_redirect_following(
    tmp_path: Path,
) -> None:
    d = tmp_path / "fakebin"
    d.mkdir()
    log = tmp_path / "argv.log"
    sh = d / "curl"
    sh.write_text(
        '#!/usr/bin/bash\nprintf "%s\\n" "$*" >> "' + str(log) + '"\n'
        'hdr=""; prev=""; for a in "$@"; do '
        'if [ "$prev" = "-D" ]; then hdr="$a"; fi; prev="$a"; done\n'
        'printf "HTTP/1.1 200 OK\\r\\n\\r\\n" > "$hdr"\n'
    )
    sh.chmod(0o755)
    # Every call answers 200, so the run fails at the first probe; one call is
    # enough to inspect the arguments the script builds.
    r = run_hosts(
        "127.0.0.1:1",
        "127.0.0.1:2",
        script=script_with_curl(tmp_path, sh),
        PATH=f"{d}:{os.environ['PATH']}",
    )
    assert r.returncode == 1
    argv = log.read_text().splitlines()[0].split()
    assert argv[0] == "-q"
    for needed in ("--max-time", "--connect-timeout", "--noproxy", "-sS", "-D", "-o"):
        assert needed in argv, needed
    assert argv[argv.index("--noproxy") + 1] == "*"
    assert argv[argv.index("--max-time") + 1].isdigit()
    assert argv[argv.index("--connect-timeout") + 1].isdigit()
    for forbidden in ("-L", "--location", "-I", "--head", "-X"):
        assert forbidden not in argv, forbidden


# --- other path: must not redirect at all ----------------------------------


def _other(status: int, headers: Callable[[str], Headers]) -> SrcReply:
    base = good()

    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path == OTHER:
            return status, headers(dst)
        return base(path, dst)

    return reply


def test_other_path_redirecting_to_support_fails(sites: list[Site]) -> None:
    r = run(*make(sites, _other(302, lambda d: [("Location", f"http://{d}/support/")])))
    assert r.returncode == 1 and "other path" in r.stderr


def test_other_path_redirecting_to_another_host_fails(sites: list[Site]) -> None:
    r = run(*make(sites, _other(302, lambda d: [("Location", "http://evil.example/")])))
    assert r.returncode == 1 and "other path" in r.stderr


def test_other_path_redirecting_to_itself_fails(sites: list[Site]) -> None:
    r = run(*make(sites, _other(301, lambda d: [("Location", OTHER)])))
    assert r.returncode == 1 and "other path" in r.stderr


def test_other_path_200_with_location_header_fails(sites: list[Site]) -> None:
    r = run(*make(sites, _other(200, lambda d: [("Location", "http://evil.example/")])))
    assert r.returncode == 1 and "Location header" in r.stderr


def test_other_path_500_fails(sites: list[Site]) -> None:
    r = run(*make(sites, _other(500, lambda d: [])))
    assert r.returncode == 1 and "expected 200" in r.stderr


def test_other_path_404_needs_the_expected_status_variable(sites: list[Site]) -> None:
    src, dst = make(sites, _other(404, lambda d: []))
    assert run(src, dst).returncode == 1
    assert run(src, dst, OTHER_EXPECT_STATUS="404").returncode == 0
    # A configured 404 still fails if the path then serves something else.
    src2, dst2 = make(sites, good())
    assert run(src2, dst2, OTHER_EXPECT_STATUS="404").returncode == 1


def test_custom_other_path_is_the_one_requested(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    assert run(src, dst, OTHER_PATH="/old/page.html").returncode == 0
    assert ("GET", "/old/page.html") in src.requests
    assert ("GET", OTHER) not in src.requests


# --- support page ----------------------------------------------------------


def test_support_page_500_fails(sites: list[Site]) -> None:
    r = run(*make(sites, good(), dst_status=500))
    assert r.returncode == 1 and "expected 200" in r.stderr


def test_support_page_redirect_fails(sites: list[Site]) -> None:
    r = run(*make(sites, good(), dst_status=302))
    assert r.returncode == 1 and "expected 200" in r.stderr


def test_unreachable_source_fails(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    src.close()
    r = run(src, dst)
    assert r.returncode == 1 and "did not complete" in r.stderr


# --- usage errors (exit 2, before any request) -----------------------------


def test_missing_and_equal_hosts_are_usage_errors(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    r = run(src, dst, SRC_HOST="")
    assert r.returncode == 2 and "SRC_HOST is required" in r.stderr
    r = run(src, dst, EXP_HOST="")
    assert r.returncode == 2 and "EXP_HOST is required" in r.stderr
    assert run(src, dst, EXP_HOST=src.host).returncode == 2
    assert run(src, dst, EXP_HOST="bad host;x").returncode == 2
    assert run(src, dst, EXPECT_STATUS="307").returncode == 2
    assert run(src, dst, OTHER_EXPECT_STATUS="302").returncode == 2
    assert run(src, dst, OTHER_EXPECT_STATUS="abc").returncode == 2
    assert run(src, dst, OTHER_PATH="no-slash").returncode == 2
    assert run(src, dst, OTHER_PATH="/a b").returncode == 2
    assert run(src, dst, VERIFY_SCHEME="ftp").returncode == 2
    assert src.requests == [] and dst.requests == []


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        ("Example.com", "example.com"),
        ("example.com", "EXAMPLE.COM"),
        ("example.com.", "example.com"),
        ("Example.COM.", "example.com"),
        ("example.com:8443", "EXAMPLE.com:8443"),
    ],
)
def test_case_or_trailing_dot_host_difference_is_a_usage_error(
    src: str, dst: str
) -> None:
    r = run_hosts(src, dst, VERIFY_SCHEME="https")
    assert r.returncode == 2 and "must differ" in r.stderr


@pytest.mark.parametrize(
    ("scheme", "src", "dst"),
    [
        ("https", "example.com", "example.com:443"),
        ("https", "example.com:443", "example.com"),
        ("http", "127.0.0.1", "127.0.0.1:80"),
        ("http", "127.0.0.1:80", "127.0.0.1"),
    ],
)
def test_default_port_does_not_make_equal_authorities_different(
    scheme: str, src: str, dst: str
) -> None:
    r = run_hosts(src, dst, VERIFY_SCHEME=scheme)
    assert r.returncode == 2 and "must differ" in r.stderr


def test_nondefault_port_keeps_authorities_different() -> None:
    r = run_hosts("example.com", "example.com:8443", VERIFY_SCHEME="https")
    assert r.returncode == 1
    assert "must differ" not in r.stderr


PORT_MSG = "is not a plain host with a valid port"
DNS_MSG = "is not a valid DNS name or IPv4 address"


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("example.com:0", PORT_MSG),
        ("example.com:080", PORT_MSG),
        ("example.com:65536", PORT_MSG),
        ("example.com:99999", PORT_MSG),
        ("example.com:abc", DNS_MSG),
        ("example.com:123456", PORT_MSG),
    ],
)
@pytest.mark.parametrize("role", ["SRC_HOST", "EXP_HOST"])
def test_invalid_ports_are_usage_errors(role: str, bad: str, message: str) -> None:
    env = {"SRC_HOST": "support.example.com", "EXP_HOST": "links.example.com"}
    env[role] = bad
    r = run_hosts(env["SRC_HOST"], env["EXP_HOST"], VERIFY_SCHEME="https")
    assert r.returncode == 2 and f"{role} {message}" in r.stderr


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("https://expected.example@evil.example", DNS_MSG),
        ("expected.example@evil.example", DNS_MSG),
        ("user:pw@evil.example", DNS_MSG),
        ("evil.example/path", DNS_MSG),
        ("evil.example?x=1", DNS_MSG),
        ("evil.example#frag", DNS_MSG),
        ("-evil.example", DNS_MSG),
        ("--max-time=1", DNS_MSG),
        ("evil.example:port", DNS_MSG),
        ("evil.example:123456789", PORT_MSG),
        ("evil .example", DNS_MSG),
        ("evil.example\t", DNS_MSG),
        ("ev$(id).example", DNS_MSG),
        ("ev;il.example", DNS_MSG),
        ("[::1]", DNS_MSG),
        (".evil.example", DNS_MSG),
        ("evil..example:80/", DNS_MSG),
    ],
)
@pytest.mark.parametrize("role", ["SRC_HOST", "EXP_HOST"])
def test_hostile_host_values_are_usage_errors(
    role: str, bad: str, message: str
) -> None:
    env = {"SRC_HOST": "support.example.com", "EXP_HOST": "links.example.com"}
    env[role] = bad
    r = run_hosts(env["SRC_HOST"], env["EXP_HOST"], VERIFY_SCHEME="https")
    assert r.returncode == 2 and f"{role} {message}" in r.stderr


def test_http_refused_for_non_loopback(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    r = run(src, dst, SRC_HOST="support.example.com", EXP_HOST="links.example.com")
    assert r.returncode == 2 and "127.0.0.1" in r.stderr


def test_http_refused_for_loopback_lookalike_hosts(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    port = dst.host.split(":")[1]
    for look in (
        f"127.0.0.1.evil.example:{port}",
        "127.0.0.1.evil.example",
        f"localhost:{port}",
    ):
        r = run_hosts(look, dst.host)
        assert r.returncode == 2, look
        r = run_hosts(src.host, look)
        assert r.returncode == 2, look
    assert src.requests == [] and dst.requests == []


@pytest.mark.parametrize(
    "bad",
    [
        "a..example",
        f"{'a' * 64}.example",
        f"{'a.' * 126}aa",
        "-bad.example",
        "bad_name.example",
        "caf\u00e9.example",
        "example.com..",
    ],
)
@pytest.mark.parametrize("role", ["SRC_HOST", "EXP_HOST"])
def test_malformed_dns_names_are_usage_errors(role: str, bad: str) -> None:
    env = {"SRC_HOST": "support.example.com", "EXP_HOST": "links.example.com"}
    env[role] = bad
    r = run_hosts(env["SRC_HOST"], env["EXP_HOST"], VERIFY_SCHEME="https")
    assert r.returncode == 2
    assert "valid DNS name or IPv4 address" in r.stderr


def test_curl_receives_only_the_controlled_environment(tmp_path: Path) -> None:
    d = tmp_path / "fakebin"
    d.mkdir()
    environment_log = tmp_path / "environment.log"
    arguments_log = tmp_path / "arguments.log"
    sh = d / "curl"
    sh.write_text(
        "#!/usr/bin/bash\n"
        f"tr '\\0' '\\n' < /proc/$$/environ | sort > {environment_log!s}\n"
        f'printf "%s\\n" "$@" > {arguments_log!s}\n'
        'hdr=""; prev=""; for a in "$@"; do '
        'if [ "$prev" = "-D" ]; then hdr="$a"; fi; prev="$a"; done\n'
        'printf "HTTP/1.1 200 OK\\r\\n\\r\\n" > "$hdr"\n'
    )
    sh.chmod(0o755)

    r = run_hosts(
        "127.0.0.1:1",
        "127.0.0.1:2",
        script=script_with_curl(tmp_path, sh),
        PATH=f"{d}:{os.environ['PATH']}",
        CURL_CA_BUNDLE="/bogus/ca-bundle",
        SSL_CERT_FILE="/bogus/cert-file",
        SSL_CERT_DIR="/bogus/cert-dir",
        SSLKEYLOGFILE="/bogus/key-log",
        CURL_HOME="/bogus/curl-home",
        http_proxy="http://127.0.0.1:9",
        HTTPS_PROXY="http://127.0.0.1:9",
    )

    assert r.returncode == 1
    passed_environment = environment_log.read_text().splitlines()
    names = {entry.partition("=")[0] for entry in passed_environment}
    assert names == {"HOME", "LC_ALL", "PATH"}
    values = dict(entry.split("=", 1) for entry in passed_environment)
    assert values["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert values["LC_ALL"] == "C"
    curl_home = Path(values["HOME"])
    assert curl_home.name.startswith("tmp.")
    assert not curl_home.exists()

    arguments = arguments_log.read_text().splitlines()
    assert arguments[0] == "-q"
    assert arguments[arguments.index("--noproxy") + 1] == "*"
    assert "-L" not in arguments


def test_caller_path_cannot_select_curl(tmp_path: Path) -> None:
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    marker = tmp_path / "called"
    fake = fakebin / "curl"
    fake.write_text(f"#!/usr/bin/bash\ntouch {marker!s}\nexit 0\n")
    fake.chmod(0o755)

    r = run_hosts(
        "127.0.0.1:1",
        "127.0.0.1:2",
        PATH=f"{fakebin}:{os.environ['PATH']}",
    )

    assert r.returncode == 1
    assert not marker.exists()


@pytest.mark.parametrize("mode", ["missing", "world-writable"])
def test_untrusted_curl_path_is_a_usage_error(tmp_path: Path, mode: str) -> None:
    curl = tmp_path / "curl"
    if mode == "world-writable":
        curl.write_text("#!/usr/bin/bash\nexit 0\n")
        curl.chmod(0o777)
    script = script_with_curl(tmp_path, curl)

    r = run_hosts(
        "127.0.0.1:1",
        "127.0.0.1:2",
        script=script,
    )

    assert r.returncode == 2
    assert "trusted curl" in r.stderr
    if mode == "world-writable":
        assert "group or world writable" in r.stderr
    else:
        assert "not available" in r.stderr


@pytest.mark.parametrize("bad", ["", ".", ":443"])
def test_host_empty_after_normalization_is_a_usage_error(bad: str) -> None:
    r = run_hosts(bad, "links.example.com", VERIFY_SCHEME="https")
    assert r.returncode == 2
    assert "SRC_HOST" in r.stderr


@pytest.mark.parametrize(
    "bad",
    ["127.1", "0x7f.1", "2130706433", "017700000001", "0177.0.0.1"],
)
@pytest.mark.parametrize("role", ["SRC_HOST", "EXP_HOST"])
def test_legacy_ipv4_spellings_are_usage_errors(role: str, bad: str) -> None:
    hosts = {
        "SRC_HOST": "support.example.com",
        "EXP_HOST": "links.example.com",
    }
    hosts[role] = bad
    r = run_hosts(
        hosts["SRC_HOST"],
        hosts["EXP_HOST"],
        VERIFY_SCHEME="https",
    )
    assert r.returncode == 2
    assert f"{role} {DNS_MSG}" in r.stderr


def test_canonical_loopback_ipv4_still_works(sites: list[Site]) -> None:
    src, dst = make(sites, good())
    assert src.host.startswith("127.0.0.1:")
    assert dst.host.startswith("127.0.0.1:")
    r = run(src, dst)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "bad",
    [
        "good.example\nevil.example",
        "good.example\r",
        "good.example\t",
        r"good.example\0evil.example",
        "good.example\n--max-time.example",
    ],
)
@pytest.mark.parametrize("role", ["SRC_HOST", "EXP_HOST"])
def test_control_or_nul_looking_host_input_is_a_usage_error(
    role: str, bad: str
) -> None:
    hosts = {
        "SRC_HOST": "support.example.com",
        "EXP_HOST": "links.example.com",
    }
    hosts[role] = bad
    r = run_hosts(
        hosts["SRC_HOST"],
        hosts["EXP_HOST"],
        VERIFY_SCHEME="https",
    )
    assert r.returncode == 2
    assert f"{role} {DNS_MSG}" in r.stderr


def test_oversized_response_headers_fail_within_timeout(sites: list[Site]) -> None:
    huge = "x" * (200 * 1024)

    def reply(path: str, dst: str) -> tuple[int, Headers | dict[str, str]]:
        if path.startswith("/?"):
            return 302, [
                ("X-Oversized", huge),
                ("Location", f"http://{dst}/support/{path[1:]}"),
            ]
        return 200, {}

    src, dst = make(sites, reply)
    r = run(src, dst)
    assert r.returncode == 1
    assert "request did not complete" in r.stderr
