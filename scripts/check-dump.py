#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail when a database dump holds a network address or a user agent.

Usage: python3 -I scripts/check-dump.py [--forbid VALUE]... [--allow-empty]
       [--schema FILE] DUMP|-

DUMP is a SQL dump (for example the output of ``wrangler d1 export``), or ``-``
for stdin. Reads only; stdlib only. The dump is parsed, not grepped:

  * Schema. Every CREATE TABLE is parsed. A table or a column that is not in
    the allowlist (scripts/dump-schema.json, kept equal to the Worker's
    migrations by a test) fails, whatever it is called. A view, a trigger or a
    table created by SELECT fails. This is what catches a renamed column.
  * Rows. Every INSERT is parsed into values (strings, numbers, NULL, X'..'
    blobs, ``||`` concatenation joined). Any other expression fails. A value
    count that does not match the columns fails. Any blob fails.
  * Values. Each string is percent-decoded (repeatedly), then checked for an
    IPv4 address (dotted, ``0x`` hex or a whole-number string), an IPv6 address
    (text or 32 unseparated hex digits), a user-agent shape, characters a
    closed-vocabulary column never holds, the column's own pattern, and each
    ``--forbid VALUE`` (also decoded).

A report names the line number, the kind of finding and, for a schema finding,
the table or column name, NEVER a data value, so a CI log cannot become a copy
of what it caught. A dump with no INSERT row fails unless ``--allow-empty``.

Exit: 0 clean, 1 findings (or no rows), 2 usage or unreadable input.

Known limits: a short token that fits an allowed column and matches none of the
checks above (for example a made-up name in a free-text place column) is not
distinguishable from data. The Worker test inspects the bind values instead.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

DEFAULT_SCHEMA = Path(__file__).resolve().with_name("dump-schema.json")

OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
IPV4 = re.compile(rf"(?<![\d.]){OCTET}(?:\.{OCTET}){{3}}(?!\d|\.\d)")
HEX_IP = re.compile(
    r"(?<![0-9a-z])0x(?:[0-9a-f]{8}|[0-9a-f]{32})(?![0-9a-z])", re.IGNORECASE
)
IPV6 = re.compile(
    r"(?<![0-9a-f:])(?:"
    r"(?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){0,6})?"
    r"|::(?:[0-9a-f]{1,4}:){0,6}[0-9a-f]{1,4}"
    r"|(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}"
    r")(?![0-9a-f:])",
    re.IGNORECASE,
)
HEX32 = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.IGNORECASE)
UA = re.compile(
    r"mozilla|applewebkit|gecko/|\b[a-z][a-z0-9_-]*/\d+(?:\.\d+)*|"
    r"windows nt|\bx11;|linux x86_64|android \d|iphone os|cpu os \d|\bmsie \d",
    re.IGNORECASE,
)
BAD_CHARS = re.compile(r"[/()\\;<>\"\x00-\x08\x0b-\x1f]")
MAX_VALUE = 256
MAX_DECODE = 4096  # longer values are refused without decoding
TOKEN = re.compile(r"[0-9A-Za-z.:]+")
DIGIT_RUN = re.compile(r"(?<![0-9a-z])\d{8,}(?![0-9a-z])", re.IGNORECASE)
NUMBER_TEXT = re.compile(r"[+-]?\d+(?:\.\d+)?")
HEX8_ANY = re.compile(r"(?<![0-9a-z])[0-9a-f]{8}(?![0-9a-z])", re.IGNORECASE)
HEX8 = re.compile(
    r"(?<![0-9a-z])(?=[0-9a-f]*[a-f])[0-9a-f]{8}(?![0-9a-z])", re.IGNORECASE
)
SKIP_CREATE = ("CREATE INDEX", "CREATE UNIQUE INDEX")
SKIP_OTHER = (
    "PRAGMA",
    "BEGIN",
    "COMMIT",
    "END",
    "ROLLBACK",
    "DELETE FROM",
    "SAVEPOINT",
    "RELEASE",
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")
IDENT = r'(?:"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]|[A-Za-z_][A-Za-z0-9_$]*)'
CONSTRAINT_WORDS = {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"}
CREATE_RE = re.compile(
    rf"^CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"(?:{IDENT}\s*\.\s*)?({IDENT})\s*\(",
    re.IGNORECASE,
)
INSERT_RE = re.compile(
    rf"^INSERT\s+(?:OR\s+\w+\s+)?INTO\s+({IDENT})\s*(\([^)]*\))?\s*VALUES\s*",
    re.IGNORECASE,
)
NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
HEX_INT = re.compile(r"0[xX][0-9a-fA-F]+")
KEYWORD = re.compile(r"(?:NULL|TRUE|FALSE)\b", re.IGNORECASE)


class Finding(Exception):  # noqa: N818 - control flow for one statement
    pass


def name_for_report(name: str) -> str:
    return name if SAFE_NAME.match(name) else "<odd name>"


def statements(text: str) -> list[tuple[int, str]]:
    """Split on ';' outside quotes and comments. Returns (first line, text)."""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 1
    line = 1
    i = 0
    n = len(text)
    quote = ""
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\n":
                line += 1
            if c == quote:
                if quote != "]" and text[i + 1 : i + 2] == quote:
                    buf.append(quote)
                    i += 1
                else:
                    quote = ""
            i += 1
            continue
        if c == "-" and text[i + 1 : i + 2] == "-":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            buf.append(" ")
            i = end
            continue
        if not buf and c.isspace():
            if c == "\n":
                line += 1
            i += 1
            start = line
            continue
        if c in "'\"`":
            quote = c
        elif c == "[":
            quote = "]"
        if c == ";" and not quote:
            s = "".join(buf).strip()
            if s:
                out.append((start, s))
            buf = []
            i += 1
            continue
        buf.append(c)
        if c == "\n":
            line += 1
        i += 1
    s = "".join(buf).strip()
    if s:
        out.append((start, s))
    return out


def unquote_ident(tok: str) -> str:
    tok = tok.strip()
    if tok[:1] == '"':
        return tok[1:-1].replace('""', '"')
    if tok[:1] in ("`", "["):
        return tok[1:-1]
    return tok


def split_top(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    cur: list[str] = []
    for c in body:
        if quote:
            cur.append(c)
            if c == quote:
                quote = ""
            continue
        if c in "'\"`":
            quote = c
        elif c == "[":
            quote = "]"
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def parse_create(stmt: str) -> tuple[str, list[str]]:
    m = CREATE_RE.match(stmt)
    if not m:
        raise Finding("unexpected statement (table not created by column list)")
    table = unquote_ident(m.group(1))
    depth = 1
    i = m.end()
    start = i
    quote = ""
    while i < len(stmt) and depth:
        c = stmt[i]
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c == "[":
            quote = "]"
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    cols: list[str] = []
    for part in split_top(stmt[start : i - 1]):
        first = re.match(IDENT, part)
        if not first:
            continue
        word = first.group(0)
        if word.upper() in CONSTRAINT_WORDS:
            continue
        cols.append(unquote_ident(word))
    return table, cols


class Val:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str) -> None:
        self.kind = kind  # str, blob, num, null
        self.text = text


def skip_space(s: str, i: int) -> int:
    while s[i : i + 1].isspace():
        i += 1
    return i


def parse_atom(s: str, i: int) -> tuple[Val, int]:
    c = s[i : i + 1]
    if c == "'":
        j = i + 1
        buf: list[str] = []
        while j < len(s):
            if s[j] == "'":
                if s[j + 1 : j + 2] == "'":
                    buf.append("'")
                    j += 2
                    continue
                return Val("str", "".join(buf)), j + 1
            buf.append(s[j])
            j += 1
        raise Finding("unsupported value expression")
    if c in ("x", "X") and s[i + 1 : i + 2] == "'":
        j = s.find("'", i + 2)
        if j < 0:
            raise Finding("unsupported value expression")
        return Val("blob", s[i + 2 : j]), j + 1
    m = HEX_INT.match(s, i)
    if m:
        return Val("str", m.group(0)), m.end()
    m = NUM.match(s, i)
    if m:
        return Val("num", m.group(0)), m.end()
    m = KEYWORD.match(s, i)
    if m:
        return Val("null", ""), m.end()
    raise Finding("unsupported value expression")


def parse_tuple(s: str, i: int) -> tuple[list[Val], int]:
    """Parse '(' value, ... ')' at s[i]; '||' chains are joined."""
    if s[i : i + 1] != "(":
        raise Finding("unsupported value expression")
    i += 1
    vals: list[Val] = []
    while True:
        parts: list[Val] = []
        while True:
            i = skip_space(s, i)
            v, i = parse_atom(s, i)
            parts.append(v)
            i = skip_space(s, i)
            if s[i : i + 2] == "||":
                i += 2
                continue
            break
        if len(parts) == 1:
            vals.append(parts[0])
        else:
            if any(p.kind not in ("str", "num") for p in parts):
                raise Finding("unsupported value expression")
            vals.append(Val("str", "".join(p.text for p in parts)))
        c = s[i : i + 1]
        i += 1
        if c == ",":
            continue
        if c == ")":
            return vals, i
        raise Finding("unsupported value expression")


def decode_layers(text: str) -> list[str]:
    """The text and every percent-decoding of it, down to a fixed point. Each
    pass that changes the text makes it shorter, so the loop ends; the caller
    bounds the input at MAX_DECODE."""
    layers = [text]
    while True:
        nxt = unquote(layers[-1])
        if nxt == layers[-1]:
            return layers
        layers.append(nxt)


def decode(text: str) -> str:
    return decode_layers(text)[-1]


COMP_DEC = re.compile(r"\d+")
COMP_HEX = re.compile(r"0[xX][0-9a-fA-F]*")


def reads_as_ipv4(tok: str) -> bool:
    """True when a resolver, or a lenient decimal parser, reads tok as an IPv4
    address: 1 to 4 dot-separated components, each decimal, legacy octal (a
    leading 0) or hex (0x), including the shortened forms 198.51.25607,
    198.3302407 and 3325256711. One bare component only counts from 2^24 up, so
    ordinary small numbers are not addresses."""
    comps = tok.split(".")
    if not 1 <= len(comps) <= 4 or any(
        not (COMP_DEC.fullmatch(c) or COMP_HEX.fullmatch(c)) for c in comps
    ):
        return False
    try:
        packed = socket.inet_aton(tok)
        value: int | None = int.from_bytes(packed, "big")
    except OSError:
        value = None
    if value is None and all(COMP_DEC.fullmatch(c) for c in comps):
        # Not valid octal (a 8 or 9 after a leading 0): read as plain decimal.
        nums = [int(c, 10) for c in comps]
        limit = 256 ** (5 - len(nums))
        if all(n < 256 for n in nums[:-1]) and nums[-1] < limit:
            value = 0
            for n in nums[:-1]:
                value = value * 256 + n
            value = (
                value * limit + nums[-1] if len(nums) < 4 else value * 256 + nums[-1]
            )
    if value is None:
        return False
    return len(comps) > 1 or value >= 2**24


def reads_as_ipv6(tok: str) -> bool:
    if ":" not in tok:
        return False
    try:
        socket.inet_pton(socket.AF_INET6, tok)
    except OSError:
        return False
    return True


def address_kinds(text: str) -> list[str]:
    """Check every decoding layer, and within it every token, with the semantics
    of an address parser rather than a pattern for each spelling."""
    kinds: list[str] = []
    for layer in decode_layers(text):
        candidates = {layer.strip()}
        for tok in TOKEN.findall(layer):
            candidates.update({tok, tok.strip("."), tok.rstrip("."), tok.strip(":.")})
            if tok.startswith("::"):
                candidates.add(tok.rstrip(":."))
        for tok in candidates:
            if tok and reads_as_ipv4(tok.strip(".")):
                kinds.append("ipv4 address")
            if tok and reads_as_ipv6(tok):
                kinds.append("ipv6 address")
    return kinds


def number_ok(text: str, colkey: str, cfg: dict[str, Any], literal: bool) -> bool:
    """A number must sit inside its column's declared ranges. A numeric literal
    in a column with no declared rule is always refused (fail closed); a number
    written as text in such a column only has to stay below 2^24 in size."""
    try:
        n = float(text)
    except ValueError:
        return False
    kind = cfg["numeric"].get(colkey)
    if kind is None:
        return False if literal else abs(n) < 2**24
    return any(lo <= n <= hi for lo, hi in cfg["numeric_kinds"][kind])


def token_kinds(text: str, colkey: str, cfg: dict[str, Any]) -> list[str]:
    """8-character hex tokens (digits only included) and long digit runs are
    refused in every text column that does not declare it expects them."""
    kinds: list[str] = []
    if colkey in cfg["hex_ids"]:
        return kinds
    digits_ok = colkey in cfg["digit_tokens_ok"]
    hex_ok = colkey in cfg["hex_tokens_ok"]
    for m in HEX8_ANY.finditer(text):
        tok = m.group(0)
        if tok.isdigit():
            if digits_ok or hex_ok:
                continue
        elif hex_ok:
            continue
        kinds.append("bare hex token")
    for m in DIGIT_RUN.finditer(text):
        if len(m.group(0)) == 8 and not (digits_ok or hex_ok):
            continue  # already reported as a hex token
        if hex_ok:
            continue
        if digits_ok and int(m.group(0)) < 2**24:
            continue
        if digits_ok and colkey in cfg["numeric"]:
            continue  # held to its range by the whole-value check
        kinds.append("long digit token")
    return kinds


def value_kinds(
    val: Val, table: str, col: str, cfg: dict[str, Any], forbid: list[str]
) -> list[str]:
    kinds: list[str] = []
    colkey = f"{table}.{col}"
    if val.kind == "null":
        return kinds
    if val.kind == "num":
        if not number_ok(val.text, colkey, cfg, True):
            kinds.append("numeric value out of range")
        return kinds
    text = val.text
    if val.kind == "blob":
        kinds.append("blob value")
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            return [*kinds, "malformed blob"]
        if len(raw) in (4, 16):
            kinds.append("binary address")
        text = raw.decode("latin-1")
    if len(text) > MAX_DECODE:
        return [*kinds, "oversize value"]
    if len(text) > MAX_VALUE:
        kinds.append("oversize value")
    text = decode(text)
    if len(text) > MAX_VALUE and "oversize value" not in kinds:
        kinds.append("oversize value")
    if IPV4.search(text) or HEX_IP.search(text):
        kinds.append("ipv4 address")
    kinds.extend(address_kinds(text))
    stripped = text.strip()
    if NUMBER_TEXT.fullmatch(stripped) and not number_ok(stripped, colkey, cfg, False):
        kinds.append(
            "numeric value out of range" if colkey in cfg["numeric"] else "ipv4 address"
        )
    kinds.extend(token_kinds(text, colkey, cfg))
    if IPV6.search(text) or (colkey not in cfg["hex_ids"] and HEX32.search(text)):
        kinds.append("ipv6 address")
    if UA.search(text):
        kinds.append("user agent")
    if BAD_CHARS.search(text) or (
        col not in cfg["spaces_ok"] and re.search(r"\s", text)
    ):
        kinds.append("unexpected characters")
    pat = cfg["patterns"].get(col)
    if pat is not None and not re.fullmatch(pat, text):
        kinds.append("value outside the column's vocabulary")
    low = text.lower()
    if any(f in text or f.lower() in low for f in forbid):
        kinds.append("forbidden value")
    return kinds


def load_cfg(path: Path) -> dict[str, Any]:
    cfg: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    cfg["tables"] = {
        t.lower(): [c.lower() for c in cols] for t, cols in cfg["tables"].items()
    }
    cfg["hex_ids"] = {x.lower() for x in cfg["hex_ids"]}
    for k in ("digit_tokens_ok", "hex_tokens_ok", "optional_tables"):
        cfg[k] = {x.lower() for x in cfg[k]}
    cfg["numeric"] = {k.lower(): v for k, v in cfg["numeric"].items()}
    cfg["numeric_kinds"] = {
        k: [tuple(r) for r in v] for k, v in cfg["numeric_kinds"].items()
    }
    if not set(cfg["numeric"].values()) <= set(cfg["numeric_kinds"]):
        raise ValueError("numeric rule names an unknown kind")
    cfg["spaces_ok"] = set(cfg["spaces_ok"])
    cfg["patterns"] = dict(cfg["patterns"])
    return cfg


def check_columns(
    line: int, table: str, cols: list[str], tables: dict[str, list[str]]
) -> list[tuple[int, str]]:
    return [
        (line, f"unexpected column {name_for_report(c)} in {name_for_report(table)}")
        for c in cols
        if c.lower() not in tables[table.lower()]
    ]


def scan_insert(
    line: int,
    stmt: str,
    cfg: dict[str, Any],
    seen_cols: dict[str, list[str]],
    forbid: list[str],
) -> list[tuple[int, str]]:
    tables: dict[str, list[str]] = cfg["tables"]
    m = INSERT_RE.match(stmt)
    if not m:
        return [(line, "unexpected statement (insert not by VALUES)")]
    table = unquote_ident(m.group(1))
    tl = table.lower()
    if tl not in tables:
        return [(line, f"unexpected table {name_for_report(table)}")]
    if m.group(2):
        cols = [unquote_ident(c) for c in m.group(2)[1:-1].split(",")]
        bad = check_columns(line, table, cols, tables)
        if bad:
            return bad
    else:
        cols = seen_cols.get(tl) or tables[tl]
    out: list[tuple[int, str]] = []
    i = m.end()
    while True:
        vals, i = parse_tuple(stmt, i)
        if len(vals) != len(cols):
            out.append((line, "value count does not match the columns"))
        else:
            for col, val in zip(cols, vals, strict=True):
                for kind in value_kinds(val, tl, col.lower(), cfg, forbid):
                    out.append(
                        (
                            line,
                            f"{kind} in {name_for_report(tl)}."
                            f"{name_for_report(col.lower())}",
                        )
                    )
        i = skip_space(stmt, i)
        if stmt[i : i + 1] == ",":
            i = skip_space(stmt, i + 1)
            continue
        break
    if stmt[i:].strip():
        out.append((line, "unsupported value expression"))
    return out


def scan(
    text: str, cfg: dict[str, Any], forbid: list[str]
) -> tuple[list[tuple[int, str]], int]:
    """Return (findings as (line, message), count of INSERT statements)."""
    tables: dict[str, list[str]] = cfg["tables"]
    seen_cols: dict[str, list[str]] = {}
    findings: list[tuple[int, str]] = []
    rows = 0
    for line, stmt in statements(text):
        upper = " ".join(stmt[:40].upper().split())
        try:
            if upper.startswith(("CREATE TABLE", "CREATE TEMP")):
                table, cols = parse_create(stmt)
                if table.lower() not in tables:
                    findings.append(
                        (line, f"unexpected table {name_for_report(table)}")
                    )
                    continue
                seen_cols[table.lower()] = [c.lower() for c in cols]
                findings += check_columns(line, table, cols, tables)
            elif upper.startswith(SKIP_CREATE):
                continue
            elif upper.startswith("INSERT"):
                rows += 1
                findings += scan_insert(line, stmt, cfg, seen_cols, forbid)
            elif upper.startswith(SKIP_OTHER):
                continue
            else:
                findings.append((line, "unexpected statement"))
        except (Finding, IndexError) as e:
            findings.append((line, str(e) or "unsupported value expression"))
    if seen_cols:
        findings += missing_schema(seen_cols, cfg)
    return findings, rows


def missing_schema(
    seen_cols: dict[str, list[str]], cfg: dict[str, Any]
) -> list[tuple[int, str]]:
    """The other half of the comparison: what the allowlist expects but the dump
    does not have. Only checked when the dump carries a schema at all."""
    out: list[tuple[int, str]] = []
    for table, cols in cfg["tables"].items():
        if table not in seen_cols:
            if table not in cfg["optional_tables"]:
                out.append((0, f"missing table {name_for_report(table)}"))
            continue
        have = seen_cols[table]
        for c in cols:
            if c not in have:
                out.append(
                    (
                        0,
                        f"missing column {name_for_report(c)} "
                        f"in {name_for_report(table)}",
                    )
                )
        if all(c in have for c in cols) and [c for c in have if c in cols] != cols:
            out.append((0, f"column order differs in {name_for_report(table)}"))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--forbid", action="append", default=[], metavar="VALUE")
    ap.add_argument("--allow-empty", action="store_true")
    ap.add_argument("--schema", default=str(DEFAULT_SCHEMA), metavar="FILE")
    ap.add_argument("file", help="SQL dump path, or - for stdin")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        cfg = load_cfg(Path(args.schema))
    except (OSError, ValueError, KeyError, AttributeError) as e:
        print(f"usage error: bad schema allowlist: {type(e).__name__}", file=sys.stderr)
        return 2
    try:
        if args.file == "-":
            text = sys.stdin.read()
        else:
            text = Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"usage error: cannot read the dump: {type(e).__name__}", file=sys.stderr)
        return 2
    forbid = [decode(v) for v in args.forbid if v]
    findings, rows = scan(text, cfg, forbid)
    for n, kind in sorted(set(findings)):
        print(f"FAIL: line {n}: {kind}" if n else f"FAIL: {kind}")
    if rows == 0 and not args.allow_empty:
        print("FAIL: the dump has no INSERT row (empty or truncated export?)")
        return 1
    if findings:
        return 1
    print(f"ok: {rows} row statement(s) scanned, no address or user agent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
