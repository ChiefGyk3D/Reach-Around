#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail when a database dump holds a network address or a user agent.

Usage: python3 -I scripts/check-dump.py [--forbid VALUE]... [--allow-empty] FILE|-

FILE is a SQL dump (for example the output of ``wrangler d1 export``), or ``-``
for stdin. Reads only; stdlib only. It checks, per line:

  * an IPv4 address, or an IPv6 address (any text with ``::`` between hex
    groups, or the full eight-group form);
  * a user agent: a ``product/1.2`` token, ``Mozilla``, ``AppleWebKit``, or a
    common platform string;
  * a column in a CREATE TABLE named like an address, user agent or referrer;
  * each ``--forbid VALUE`` literal (for a sentinel the caller planted).

A report names the line number and the kind of finding, NEVER the value, so a
CI log cannot become a copy of what it caught. A dump with no INSERT row fails
unless ``--allow-empty``: an empty or truncated export must not read as clean.

Exit: 0 clean, 1 findings (or no rows), 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
IPV4 = re.compile(rf"(?<![\d.]){OCTET}(?:\.{OCTET}){{3}}(?!\d|\.\d)")
# "::" between or after hex groups (not "a::b" in prose: needs a hex group or
# an edge on each side), or eight colon-separated groups.
IPV6 = re.compile(
    r"(?<![0-9a-f:])(?:"
    r"(?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?"
    r"|::(?:[0-9a-f]{1,4}:){0,6}[0-9a-f]{1,4}"
    r"|(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}"
    r")(?![0-9a-f:])",
    re.IGNORECASE,
)
UA = re.compile(
    r"mozilla|applewebkit|gecko/|\b[a-z][a-z0-9_-]*/\d+(?:\.\d+)+|"
    r"windows nt|\bx11;|linux x86_64|android \d|iphone os|cpu os \d|\bmsie \d",
    re.IGNORECASE,
)
BAD_COLUMN = re.compile(
    r"^\s*[\"`\[]?(ip|ip_?addr(?:ess)?|client_?ip|remote_?(?:addr|ip)|"
    r"user_?agent|ua|referr?er|referer_?url|x_forwarded_for)[\"`\]]?\s",
    re.IGNORECASE,
)


def scan(lines: list[str], forbid: list[str]) -> tuple[list[tuple[int, str]], int]:
    """Return (findings as (line number, kind), count of INSERT rows)."""
    findings: list[tuple[int, str]] = []
    rows = 0
    in_create = False
    for n, line in enumerate(lines, start=1):
        head = line.lstrip()[:20].upper()
        if head.startswith("INSERT"):
            rows += 1
        if head.startswith("CREATE TABLE"):
            in_create = True
            body = line.split("(", 1)[1] if "(" in line else ""
            # Single-line CREATE: check each comma-separated column.
            if body:
                for part in body.split(","):
                    if BAD_COLUMN.match(part + " "):
                        findings.append((n, "forbidden column name"))
        elif in_create and BAD_COLUMN.match(line):
            findings.append((n, "forbidden column name"))
        if in_create and ";" in line:
            in_create = False
        if IPV4.search(line):
            findings.append((n, "ipv4 address"))
        if IPV6.search(line):
            findings.append((n, "ipv6 address"))
        if UA.search(line):
            findings.append((n, "user agent"))
        for value in forbid:
            if value in line:
                findings.append((n, "forbidden value"))
    return findings, rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--forbid", action="append", default=[], metavar="VALUE")
    ap.add_argument("--allow-empty", action="store_true")
    ap.add_argument("file", help="SQL dump path, or - for stdin")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        text = (
            sys.stdin.read()
            if args.file == "-"
            else Path(args.file).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError) as e:
        print(
            f"usage error: cannot read {args.file!r}: {type(e).__name__}",
            file=sys.stderr,
        )
        return 2
    findings, rows = scan(text.splitlines(), [v for v in args.forbid if v])
    for n, kind in sorted(set(findings)):
        print(f"FAIL: line {n}: {kind}")
    if rows == 0 and not args.allow_empty:
        print("FAIL: the dump has no INSERT row (empty or truncated export?)")
        return 1
    if findings:
        return 1
    print(f"ok: {rows} row statement(s) scanned, no address or user agent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
