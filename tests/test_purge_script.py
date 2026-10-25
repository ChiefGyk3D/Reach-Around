# SPDX-License-Identifier: Apache-2.0
"""The optional cache-purge step, run offline against a stub ``curl``."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "purge-cache.sh"
BEARER = "synthetic-token-value-1234567890"
ZONE = "0123456789abcdef0123456789abcdef"
HOST = "links.example.com"

# The stub records its arguments and its stdin for each call, then answers as
# STUB_MODE says. Nothing here touches the network.
STUB = r"""#!/usr/bin/env bash
n=$(ls "$STUB_DIR" | grep -c '^call-.*\.args$')
n=$((n + 1))
printf '%s\n' "$@" > "$STUB_DIR/call-$n.args"
cat > "$STUB_DIR/call-$n.stdin"
prev=""
for arg in "$@"; do
  if [ "$prev" = "--header" ] && [ "${arg#@}" != "$arg" ] && [ "$arg" != "@-" ]; then
    file="${arg#@}"
    cat "$file" > "$STUB_DIR/call-$n.header"
    stat -c '%a' "$file" > "$STUB_DIR/call-$n.hmode"
    printf '%s' "$file" > "$STUB_DIR/call-$n.hpath"
  fi
  prev="$arg"
done
mode=$(sed -n "${n}p" "$STUB_DIR/modes" 2>/dev/null)
case "${mode:-ok}" in
  ok) echo '{"success": true, "errors": [], "messages": [], "result": {"id": "x"}}' ;;
  denied) echo '{"success": false, "errors": [{"code": 10000,
    "message": "Authentication error"}]}' ;;
  slow) sleep 2; echo '{"success": true}' ;;
  curlfail) echo "curl: (6) Could not resolve host" >&2; exit 6 ;;
esac
"""


@dataclass
class Run:
    code: int
    out: str
    err: str
    summary: str
    calls: list[dict[str, object]]


def run_script(
    tmp_path: Path,
    paths: list[str],
    *,
    modes: list[str] | None = None,
    env: dict[str, str] | None = None,
    raw: str | None = None,
) -> Run:
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(STUB)
    curl.chmod(0o755)
    (stub_dir / "modes").write_text("\n".join(modes or []) + "\n")
    listing = tmp_path / "paths.txt"
    listing.write_text(raw if raw is not None else "\n".join(paths) + "\n")
    summary = tmp_path / "summary.md"
    full = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(stub_dir),
        "CLOUDFLARE_PURGE_TOKEN": BEARER,
        "ZONE_ID": ZONE,
        "SITE_HOST": HOST,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    full.update(env or {})
    for key in [k for k, v in full.items() if v is None]:
        del full[key]
    done = subprocess.run(  # noqa: S603 - fixed argv, synthetic env
        ["/bin/bash", str(SCRIPT), str(listing)],
        env=full,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    calls: list[dict[str, object]] = []
    for args_file in sorted(
        stub_dir.glob("call-*.args"), key=lambda p: int(p.stem[5:])
    ):
        args = args_file.read_text().splitlines()
        stdin = args_file.with_suffix(".stdin").read_text()

        def side(suffix: str, args_file: Path = args_file) -> str:
            other = args_file.with_suffix(suffix)
            return other.read_text() if other.exists() else ""

        calls.append(
            {
                "args": args,
                "stdin": stdin,
                "header": side(".header"),
                "hmode": side(".hmode").strip(),
                "hpath": side(".hpath"),
            }
        )
    return Run(
        done.returncode,
        done.stdout,
        done.stderr,
        summary.read_text() if summary.exists() else "",
        calls,
    )


def body_of(call: dict[str, object]) -> dict[str, list[str]]:
    # the body travels on stdin (--data-binary @-), never in argv
    body: dict[str, list[str]] = json.loads(str(call["stdin"]))
    return body


def urls(count: int) -> list[str]:
    return [f"/p{i}.txt" for i in range(count)]


def test_the_script_exists_and_is_executable_bash() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.read_text().startswith("#!/usr/bin/env bash\n")
    assert "set -x" not in SCRIPT.read_text()


def test_it_batches_at_most_thirty_urls_per_request(tmp_path: Path) -> None:
    result = run_script(tmp_path, urls(65))
    assert result.code == 0
    sizes = [len(body_of(c)["files"]) for c in result.calls]
    assert sizes == [30, 30, 5]
    sent = [u for c in result.calls for u in body_of(c)["files"]]
    assert sent == [f"https://{HOST}{p}" for p in urls(65)]


def test_exactly_thirty_is_one_request_and_thirty_one_is_two(tmp_path: Path) -> None:
    one = tmp_path / "one"
    one.mkdir()
    two = tmp_path / "two"
    two.mkdir()
    assert [len(body_of(c)["files"]) for c in run_script(one, urls(30)).calls] == [30]
    assert [len(body_of(c)["files"]) for c in run_script(two, urls(31)).calls] == [
        30,
        1,
    ]


def test_the_request_is_https_post_to_the_zone_with_the_token_only_on_stdin(
    tmp_path: Path,
) -> None:
    result = run_script(tmp_path, ["/", "/index.html"])
    (call,) = result.calls
    args = call["args"]
    assert isinstance(args, list)
    assert args[-1] == f"https://api.cloudflare.com/client/v4/zones/{ZONE}/purge_cache"
    assert args[args.index("--request") + 1] == "POST"
    assert args[args.index("--proto") + 1] == "=https"
    assert "Content-Type: application/json" in args
    # the token is only in a 0600 header file; the body is only on stdin
    assert call["header"] == f"Authorization: Bearer {BEARER}\n"
    assert call["hmode"] == "600"
    assert args[args.index("--header") + 1] == f"@{call['hpath']}"
    assert args[args.index("--data-binary") + 1] == "@-"
    assert "--data" not in args
    assert BEARER not in str(call["stdin"])
    assert not any(BEARER in str(a) for a in args)
    assert not any("https://" + HOST in str(a) for a in args)
    assert not any("files" in str(a) for a in args)
    assert body_of(call) == {
        "files": [f"https://{HOST}/", f"https://{HOST}/index.html"]
    }


def test_the_header_file_lives_under_runner_temp_and_is_removed(
    tmp_path: Path,
) -> None:
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    result = run_script(tmp_path, urls(65), env={"RUNNER_TEMP": str(temp)})
    assert len(result.calls) == 3
    for call in result.calls:
        assert str(call["hpath"]).startswith(str(temp) + "/")
    assert list(temp.iterdir()) == []


def test_the_header_file_is_removed_after_a_failed_request_too(
    tmp_path: Path,
) -> None:
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    run_script(
        tmp_path, urls(40), modes=["curlfail", "denied"], env={"RUNNER_TEMP": str(temp)}
    )
    assert list(temp.iterdir()) == []


def test_the_token_is_never_printed(tmp_path: Path) -> None:
    for mode in ("ok", "denied", "curlfail"):
        sub = tmp_path / mode
        sub.mkdir()
        result = run_script(sub, urls(3), modes=[mode])
        assert BEARER not in result.out + result.err + result.summary


def test_success_is_a_notice_and_a_summary_line(tmp_path: Path) -> None:
    result = run_script(tmp_path, urls(3))
    assert result.code == 0
    assert "::notice::purged 3 URL(s) in 1 request(s)" in result.out
    assert "::warning::" not in result.out
    assert "purged 3 URL(s)" in result.summary


def test_an_api_refusal_is_a_warning_and_never_fails_the_deploy(
    tmp_path: Path,
) -> None:
    result = run_script(tmp_path, urls(3), modes=["denied"])
    assert result.code == 0
    assert "::warning::" in result.out
    assert "Authentication error" in result.out
    assert "WARNING" in result.summary
    assert "3 URL(s) were not purged" in result.out


def test_a_transport_failure_is_a_warning_and_later_batches_still_run(
    tmp_path: Path,
) -> None:
    result = run_script(tmp_path, urls(40), modes=["curlfail", "ok"])
    assert result.code == 0
    assert len(result.calls) == 2
    assert "curl exit 6" in result.out
    assert "purged 10 URL(s); 30 URL(s) were not purged" in result.out


def test_a_missing_token_or_zone_skips_with_a_notice(tmp_path: Path) -> None:
    for name in ("CLOUDFLARE_PURGE_TOKEN", "ZONE_ID"):
        sub = tmp_path / name
        sub.mkdir()
        result = run_script(sub, urls(3), env={name: ""})
        assert result.code == 0
        assert result.calls == []
        assert "::notice::" in result.out and "skipping" in result.out
        assert "::warning::" not in result.out


@pytest.mark.parametrize(
    "env",
    [
        {"ZONE_ID": "not-a-zone"},
        {"ZONE_ID": "0123456789ABCDEF0123456789ABCDEF"},
        {"ZONE_ID": ZONE + "/../x"},
        {"SITE_HOST": "host/with/slash"},
        {"SITE_HOST": "evil.example.com@attacker.example"},
        {"SITE_HOST": ""},
    ],
)
def test_a_malformed_setting_skips_with_a_warning(
    tmp_path: Path, env: dict[str, str]
) -> None:
    result = run_script(tmp_path, urls(3), env=env)
    assert result.code == 0
    assert result.calls == []
    assert "::warning::" in result.out


@pytest.mark.parametrize(
    "bad",
    ["http://evil.example/x", "//evil.example/x", "no-slash", "/a b", "/a?x=1", "/a#f"],
)
def test_a_path_with_odd_characters_is_skipped_not_sent(
    tmp_path: Path, bad: str
) -> None:
    result = run_script(tmp_path, ["/ok", bad, "/also-ok"])
    assert result.code == 0
    (call,) = result.calls
    assert body_of(call)["files"] == [f"https://{HOST}/ok", f"https://{HOST}/also-ok"]
    assert "::warning::skipped a path" in result.out


def test_an_empty_list_or_only_comments_sends_nothing(tmp_path: Path) -> None:
    result = run_script(tmp_path, [], raw="# nothing changed\n\n")
    assert result.code == 0
    assert result.calls == []
    assert "nothing to purge" in result.out


def test_a_missing_list_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/bash", str(SCRIPT), str(tmp_path / "absent.txt")],
        env={"PATH": os.environ["PATH"], "CLOUDFLARE_PURGE_TOKEN": BEARER},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0
    assert "::warning::" in done.stdout


def test_a_last_line_without_a_newline_is_still_read(tmp_path: Path) -> None:
    result = run_script(tmp_path, [], raw="/a\n/b")
    (call,) = result.calls
    assert body_of(call)["files"] == [f"https://{HOST}/a", f"https://{HOST}/b"]


# ----------------------------------------------------- total-work limits


def sent_urls(result: Run) -> list[str]:
    return [u for c in result.calls for u in body_of(c)["files"]]


def test_a_huge_list_is_capped_at_300_urls_and_10_requests(tmp_path: Path) -> None:
    result = run_script(tmp_path, urls(20000))
    assert result.code == 0
    assert len(result.calls) == 10
    assert sent_urls(result) == [f"https://{HOST}{p}" for p in urls(300)]
    assert "::warning::" in result.out
    assert "20000" in result.out and "300" in result.out
    assert "WARNING" in result.summary


def test_a_list_at_the_cap_is_not_a_warning(tmp_path: Path) -> None:
    result = run_script(tmp_path, urls(300))
    assert len(sent_urls(result)) == 300
    assert "::warning::" not in result.out


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("50", 50),
        ("1000", 1000),
        ("5000", 1000),
        ("abc", 300),
        ("0", 300),
        ("-3", 300),
        ("", 300),
    ],
)
def test_the_cap_can_be_raised_to_a_hard_ceiling_only(
    tmp_path: Path, setting: str, expected: int
) -> None:
    result = run_script(tmp_path, urls(1500), env={"PURGE_MAX": setting})
    assert len(sent_urls(result)) == expected
    assert len(result.calls) == -(-expected // 30)


def test_the_first_lines_survive_the_cap_so_entry_points_come_first(
    tmp_path: Path,
) -> None:
    lines = ["/", "/404.html", "/robots.txt", *urls(400)]
    sent = sent_urls(run_script(tmp_path, lines, env={"PURGE_MAX": "3"}))
    assert sent == [f"https://{HOST}{p}" for p in lines[:3]]


def test_a_total_time_budget_stops_further_requests(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        urls(90),
        modes=["slow", "slow", "slow"],
        env={"PURGE_BUDGET_SECONDS": "1"},
    )
    assert result.code == 0
    assert len(result.calls) == 1
    assert "time budget" in result.out


# ------------------------------------------------------- path length limits


def test_a_path_of_exactly_2000_characters_is_sent(tmp_path: Path) -> None:
    path = "/" + "a" * 1999
    assert len(path) == 2000
    result = run_script(tmp_path, ["/ok", path])
    (call,) = result.calls
    assert body_of(call)["files"] == [f"https://{HOST}/ok", f"https://{HOST}{path}"]
    assert "::warning::" not in result.out


def test_a_path_over_2000_characters_is_skipped_counted_and_warned(
    tmp_path: Path,
) -> None:
    result = run_script(tmp_path, ["/ok", "/" + "a" * 2000, "/" + "b" * 5000, "/fine"])
    (call,) = result.calls
    assert body_of(call)["files"] == [f"https://{HOST}/ok", f"https://{HOST}/fine"]
    assert "::warning::" in result.out
    assert "2 path(s)" in result.out and "2000" in result.out


def test_a_two_million_character_path_never_reaches_curl_or_argv(
    tmp_path: Path,
) -> None:
    result = run_script(tmp_path, ["/ok", "/" + "z" * 2_000_000])
    assert result.code == 0
    (call,) = result.calls
    assert body_of(call)["files"] == [f"https://{HOST}/ok"]
    args = call["args"]
    assert isinstance(args, list)
    assert max(len(a) for a in args) < 200
