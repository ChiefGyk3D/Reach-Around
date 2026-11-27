# SPDX-License-Identifier: Apache-2.0
"""The legacy-erasure shell block of docs/privacy.md, EXECUTED with fakes on PATH.

The block is copied out of the document, never restated here, so a doc that goes
wrong (a `set -e` that stops on the tool's exit 10, a cleanup that removes the SQL
file before it is applied) fails this test. The fake `reach-around` writes the SQL
file and exits with the status the test chooses; the fake wrangler logs every call
with whether the SQL file existed at that moment. Everything is synthetic."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = Path(os.environ.get("LEGACY_BLOCK_DOC", ROOT / "docs" / "privacy.md"))
LEAD = "0123456789abcdef0123456789abcdef"

FAKE_REACH_AROUND = """#!{python}
import os, sys
args = sys.argv[1:]
sys.stdin.read()
out = args[args.index("--sql-out") + 1]
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, b"DELETE FROM leads WHERE id = '{lead}' AND email = 'x@example.test';\\n")
os.close(fd)
with open(os.environ["CALL_LOG"], "a") as fh:
    fh.write("reach-around\\n")
sys.exit(int(os.environ["FAKE_RA_EXIT"]))
"""

FAKE_WRANGLER = """#!{python}
import json, os, sys
args = sys.argv[1:]
log = os.environ["CALL_LOG"]
if "--file" in args:
    path = args[args.index("--file") + 1]
    kind = "apply file_exists=" + ("yes" if os.path.exists(path) else "no")
    if not os.path.exists(path):
        sys.exit(1)
elif "--command" in args:
    sql = args[args.index("--command") + 1]
    kind = "readback" if "leads_remaining" in sql else "export"
    if kind == "readback":
        left = int(os.environ.get("FAKE_LEFT", "0"))
        rows = [{{"leads_remaining": left, "events_remaining": 0}}]
    else:
        rows = [{{"id": "{lead}", "email": "x@example.test"}}]
    print(json.dumps([{{"results": rows, "success": True, "meta": {{}}}}]))
else:
    kind = "other"
with open(log, "a") as fh:
    fh.write(kind + "\\n")
"""


def block() -> str:
    text = DOC.read_text(encoding="utf-8")
    for part in text.split("```\n")[1::2]:
        if "--legacy-database " in part and part.lstrip().startswith("("):
            return part
    raise AssertionError("the legacy erasure block is not in the document")


def run_block(
    tmp_path: Path, ra_exit: int, left: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    repo = tmp_path / "repo"
    (repo / "worker" / "node_modules" / ".bin").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    work_parent = tmp_path / "tmp"
    work_parent.mkdir()
    log = tmp_path / "calls.log"
    fmt = {"python": sys.executable, "lead": LEAD}
    wrangler = repo / "worker" / "node_modules" / ".bin" / "wrangler"
    wrangler.write_text(FAKE_WRANGLER.format(**fmt))
    wrangler.chmod(0o700)
    ra = bindir / "reach-around"
    ra.write_text(FAKE_REACH_AROUND.format(**fmt))
    ra.chmod(0o700)
    sleep = bindir / "sleep"
    sleep.write_text("#!/bin/sh\necho sleep >> \"$CALL_LOG\"\n")
    sleep.chmod(0o700)
    script = tmp_path / "block.sh"
    script.write_text(block(), encoding="utf-8")
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "TMPDIR": str(work_parent),
        "CALL_LOG": str(log),
        "FAKE_RA_EXIT": str(ra_exit),
        "FAKE_LEFT": str(left),
        "HOME": str(tmp_path),
    }
    done = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env=env,
        input="x@example.test\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls = log.read_text().split() if log.exists() else []
    return done, [c for c in calls if c != "sleep"], work_parent


def test_the_block_accepts_exit_10_applies_the_file_reads_back_and_cleans_last(
    tmp_path: Path,
) -> None:
    done, calls, work_parent = run_block(tmp_path, ra_exit=10)
    assert done.returncode == 0, done.stderr
    assert calls[:2] == ["export", "reach-around"]
    log_text = (tmp_path / "calls.log").read_text()
    assert log_text.count("apply file_exists=yes") == 2
    assert "file_exists=no" not in log_text
    assert log_text.index("readback") > log_text.rindex("apply")
    assert log_text.count("readback") == 2
    assert list(work_parent.iterdir()) == []  # cleaned up, and only at the end
    assert "x@example.test" not in done.stdout + done.stderr


@pytest.mark.parametrize("status", [1, 2])
def test_any_other_exit_stops_the_block_before_anything_is_applied(
    tmp_path: Path, status: int
) -> None:
    done, _calls, work_parent = run_block(tmp_path, ra_exit=status)
    assert done.returncode != 0
    log_text = (tmp_path / "calls.log").read_text()
    assert "apply" not in log_text and "readback" not in log_text
    assert list(work_parent.iterdir()) == []


def test_a_read_back_that_is_not_zero_fails_the_block(tmp_path: Path) -> None:
    done, _calls, work_parent = run_block(tmp_path, ra_exit=10, left=1)
    assert done.returncode != 0
    assert "NOT confirmed erased" in done.stderr
    assert list(work_parent.iterdir()) == []
