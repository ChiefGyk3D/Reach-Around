#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Mutation check for the media-kit gate (issue #270).

Applies a list of named, single-edit mutations to the Worker's gate source in a
TEMPORARY COPY of worker/, runs the relevant vitest files against each, and
prints a table of which mutations were KILLED (a test failed) and which
SURVIVED (every test still passed). A survivor is either a missing test or an
equivalent mutation; an equivalent one must say why in the list
(`equivalent = "..."`), otherwise the run exits non-zero.

Safety: the repository's working tree is never written. The copy lives in a
fresh directory made by tempfile.mkdtemp and is removed at the end. Nothing
mutated is ever committed or left behind.

    python3 -I scripts/mutation-check.py --validate          # structure only, no node
    python3 -I scripts/mutation-check.py                     # the full run
    python3 -I scripts/mutation-check.py --only token-expiry-boundary

Needs `npm ci --ignore-scripts` done once in worker/ (the copy links to that
node_modules). Standard library only. See docs/security/abuse-suite.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
WORKER = REPO / "worker"
DEFAULT_LIST = Path(__file__).resolve().parent / "mutation-check.toml"
COPY_ITEMS = (
    "src",
    "test",
    "migrations",
    "package.json",
    "tsconfig.json",
    "vitest.config.ts",
    "wrangler.toml",
)
# Files outside worker/ that the Worker tests import (relative to the repository root).
ROOT_ITEMS = ("tests/fixtures/email_vectors.json",)
TIMEOUT_SECONDS = 900
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


class ListError(Exception):
    """The mutation list is malformed or no longer matches the source."""


def load_list(path: Path) -> tuple[list[str], list[dict[str, object]]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    default_tests = data.get("default_tests", [])
    muts = data.get("mutation", [])
    if not isinstance(default_tests, list) or not isinstance(muts, list) or not muts:
        raise ListError("the list needs default_tests and at least one [[mutation]]")
    groups = data.get("groups", {})
    for m in muts:
        if "tests" not in m and m.get("group") is not None:
            if m["group"] not in groups:
                raise ListError(f"{m.get('id')}: unknown group {m['group']!r}")
            m["tests"] = list(groups[m["group"]])
    return [str(t) for t in default_tests], muts


def check_entry(m: dict[str, object], seen: set[str]) -> list[str]:
    """Structural problems of one entry (no source read)."""
    problems: list[str] = []
    mid = m.get("id")
    if not isinstance(mid, str) or not _ID_RE.match(mid):
        problems.append(f"bad id {mid!r}")
    elif mid in seen:
        problems.append(f"{mid}: duplicate id")
    else:
        seen.add(mid)
    for k in ("file", "find", "replace", "what"):
        if not isinstance(m.get(k), str) or (k != "replace" and m.get(k) == ""):
            problems.append(f"{mid}: missing or empty {k}")
    if m.get("find") == m.get("replace"):
        problems.append(f"{mid}: find and replace are identical")
    f = m.get("file")
    if isinstance(f, str) and (
        Path(f).is_absolute() or ".." in Path(f).parts or not f.startswith("src/")
    ):
        problems.append(f"{mid}: file must be a relative path under src/")
    eq = m.get("equivalent")
    if eq is not None and (not isinstance(eq, str) or len(eq) < 20):
        problems.append(f"{mid}: equivalent needs a real reason (20+ characters)")
    return problems


def validate(muts: list[dict[str, object]], root: Path) -> list[str]:
    """Every entry well formed, and its `find` text occurs once in its file."""
    problems: list[str] = []
    seen: set[str] = set()
    for m in muts:
        problems += check_entry(m, seen)
        f, find = m.get("file"), m.get("find")
        if isinstance(f, str) and isinstance(find, str) and find:
            src = root / f
            if not src.is_file():
                problems.append(f"{m.get('id')}: {f} does not exist")
                continue
            n = src.read_text(encoding="utf-8").count(find)
            if n != 1:
                problems.append(
                    f"{m.get('id')}: find text occurs {n} times in {f} (need 1)"
                )
    return problems


def run_vitest(
    copy: Path, tests: list[str], workers: int
) -> tuple[int, dict[str, Any]]:
    """Run vitest in the copy; return (exit code, parsed JSON report or {}).

    The JSON reporter is used because the default reporter needs a newer Node
    than some machines have.
    """
    report = copy / "mutation-report.json"
    report.unlink(missing_ok=True)
    cmd = [
        "nice",
        "-n",
        "19",
        "npx",
        "vitest",
        "run",
        *tests,
        f"--maxWorkers={workers}",
        "--reporter=json",
        f"--outputFile={report}",
    ]
    try:
        p = subprocess.run(  # noqa: S603 - fixed argv, test paths from our own list
            cmd,
            cwd=copy,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, {}
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"stderr": p.stderr[-2000:]}
    return p.returncode, data if isinstance(data, dict) else {}


def summarize(report: dict[str, Any]) -> str:
    total, failed = report.get("numTotalTests"), report.get("numFailedTests")
    if not isinstance(total, int) or not isinstance(failed, int):
        return "no report"
    note = f"{failed} failed of {total}"
    if failed:
        for suite in report.get("testResults", []):
            for t in suite.get("assertionResults", []):
                if t.get("status") == "failed":
                    return f"{note}; first: {str(t.get('fullName', ''))[:90]}"
    return note


def classify(code: int, report: dict[str, Any]) -> str:
    """KILLED only when tests ran and some failed; ERROR when the run itself broke."""
    failed = report.get("numFailedTests")
    if code == 0 and failed == 0:
        return "SURVIVED"
    if isinstance(failed, int) and failed > 0:
        return "KILLED"
    return "ERROR"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--list", type=Path, default=DEFAULT_LIST, help="mutation list (TOML)"
    )
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="run only this mutation id (repeatable)",
    )
    ap.add_argument(
        "--skip-test",
        action="append",
        default=[],
        help="leave this test file out of every run (to measure what the rest catches)",
    )
    ap.add_argument("--workers", type=int, default=2, help="vitest workers (default 2)")
    ap.add_argument(
        "--validate",
        action="store_true",
        help="check the list against the source and stop (no node needed)",
    )
    ap.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="parent directory for the temporary copy (default: the system temp dir)",
    )
    args = ap.parse_args()

    default_tests, muts = load_list(args.list)
    problems = validate(muts, WORKER)
    if problems:
        print("mutation list problems:", *problems, sep="\n  ", file=sys.stderr)
        return 2
    if args.validate:
        print(f"ok: {len(muts)} mutations, every find text matches exactly once")
        return 0
    if args.only:
        known = {str(m["id"]) for m in muts}
        unknown = [o for o in args.only if o not in known]
        if unknown:
            print("unknown mutation ids:", *unknown, file=sys.stderr)
            return 2
        muts = [m for m in muts if m["id"] in args.only]
    if not (WORKER / "node_modules").is_dir():
        print(
            "worker/node_modules is missing: run `npm ci --ignore-scripts` first",
            file=sys.stderr,
        )
        return 2

    parent = args.workdir.resolve() if args.workdir else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    copy_root = Path(tempfile.mkdtemp(prefix="ra-mutation-", dir=parent))
    if REPO in (copy_root, *copy_root.parents):
        print("refusing to work inside the repository", file=sys.stderr)
        return 2
    copy = copy_root / "worker"
    rows: list[tuple[str, str, str, str]] = []
    try:
        copy.mkdir()
        for item in COPY_ITEMS:
            src = WORKER / item
            if src.is_dir():
                shutil.copytree(src, copy / item)
            else:
                shutil.copy2(src, copy / item)
        for item in ROOT_ITEMS:
            dest = copy_root / item
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / item, dest)
        (copy / "node_modules").symlink_to(WORKER / "node_modules")

        skip = tuple(args.skip_test)
        started = time.monotonic()
        code, out = run_vitest(
            copy, [t for t in default_tests if not t.startswith(skip)], args.workers
        )
        if code != 0:
            print(
                "BASELINE FAILED: the unmutated tests do not pass, so no mutation "
                f"result would mean anything. {summarize(out)}",
                file=sys.stderr,
            )
            return 2
        print(f"baseline ok ({summarize(out)})", flush=True)

        for m in muts:
            target = copy / str(m["file"])
            original = target.read_text(encoding="utf-8")
            tests = [
                str(t)
                for t in m.get("tests", default_tests)
                if not str(t).startswith(skip)
            ]
            mutated = original.replace(str(m["find"]), str(m["replace"]), 1)
            if mutated == original:
                raise ListError(f"{m['id']}: the edit changed nothing")
            try:
                target.write_text(mutated, encoding="utf-8")
                code, out = run_vitest(copy, tests, args.workers)
            finally:
                target.write_text(original, encoding="utf-8")
            verdict = classify(code, out)
            note = summarize(out)
            if verdict == "SURVIVED" and m.get("equivalent"):
                verdict = "SURVIVED (equivalent)"
            elif verdict == "KILLED" and m.get("equivalent"):
                note += " [marked equivalent but a test catches it: drop the claim]"
            rows.append((str(m["id"]), verdict, note, str(m["what"])))
            print(f"{rows[-1][0]:44} {verdict:22} {note}", flush=True)

        print(f"\nelapsed {time.monotonic() - started:.0f}s\n")
    finally:
        shutil.rmtree(copy_root, ignore_errors=True)

    width = max(len(r[0]) for r in rows) if rows else 10
    print(f"| {'mutation':{width}} | verdict | what it changes |")
    print(f"|{'-' * (width + 2)}|---------|-----------------|")
    for mid, verdict, _note, what in rows:
        print(f"| {mid:{width}} | {verdict} | {what} |")
    killed = sum(1 for r in rows if r[1] == "KILLED")
    equivalent = sum(1 for r in rows if r[1] == "SURVIVED (equivalent)")
    bad = [r for r in rows if r[1] in ("SURVIVED", "ERROR")]
    print(
        f"\n{len(rows)} mutations: {killed} killed, {equivalent} survived as "
        f"equivalent, {len(bad)} need attention"
    )
    for r in bad:
        print(f"  {r[1]}: {r[0]}")
    return 1 if bad else 0


if __name__ == "__main__":
    if os.name != "posix":
        sys.exit("posix only")
    sys.exit(main())
