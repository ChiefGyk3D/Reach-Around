#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Scrub and publish-safety check for docs that would be published.

Scans docs/wiki/ (or the paths given) and fails on content that must never be
published: em dashes, owner-private terms, radio callsign and grid patterns,
private hostnames and addresses, secret shapes, session URLs, broken relative
links, images without alt text, Mermaid blocks without a text description, and
light-mode-only color hints.

Output is "path:line: rule" only. A matched value is never printed.

Forbidden terms (the owner's employer, a work handle, real zone names) are read
at run time from the WIKI_SCRUB_FORBIDDEN environment variable (comma
separated) or from a gitignored local file (.wiki-scrub-forbidden by default,
one term per line, "#" comments). They are never stored in the repository.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

SUFFIXES = {".md", ".markdown", ".html", ".htm", ".svg", ".txt", ".yml", ".yaml"}
FORBIDDEN_ENV = "WIKI_SCRUB_FORBIDDEN"
FORBIDDEN_FILE = ".wiki-scrub-forbidden"

# Placeholders the project allows in examples.
ALLOWED_CALLSIGNS = {"N0CALL", "N0TST"}
ALLOWED_GRIDS = {"fn31pr"}
# Uppercase tokens shaped like a callsign that are ordinary technical words.
CALLSIGN_FALSE_POSITIVES = {"K8S", "H2O"}

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)")


@dataclass
class Fence:
    start: int  # zero-based index of the opening line
    end: int  # zero-based index of the closing line (last line if unclosed)
    lang: str


@dataclass
class Doc:
    path: Path
    root: Path
    lines: list[str]
    forbidden: list[str] = field(default_factory=list)
    fences: list[Fence] = field(default_factory=list)
    in_fence: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.in_fence = [False] * len(self.lines)
        open_at = -1
        marker = ""
        lang = ""
        for index, line in enumerate(self.lines):
            m = FENCE.match(line)
            if open_at < 0 and m:
                open_at, marker, lang = index, m.group(1), m.group(2).lower()
            elif open_at >= 0 and m and m.group(1)[0] == marker[0] and not m.group(2):
                self.fences.append(Fence(open_at, index, lang))
                open_at = -1
        if open_at >= 0:
            self.fences.append(Fence(open_at, len(self.lines) - 1, lang))
        for fence in self.fences:
            for index in range(fence.start, fence.end + 1):
                self.in_fence[index] = True


Rule = Callable[[Doc], Iterator[int]]


def _regex_rule(pattern: re.Pattern[str]) -> Rule:
    def rule(doc: Doc) -> Iterator[int]:
        for number, line in enumerate(doc.lines, 1):
            if pattern.search(line):
                yield number

    return rule


def em_dash(doc: Doc) -> Iterator[int]:
    for number, line in enumerate(doc.lines, 1):
        if "\u2014" in line or "&mdash;" in line or "&#8212;" in line:
            yield number


def forbidden_term(doc: Doc) -> Iterator[int]:
    if not doc.forbidden:
        return
    for number, line in enumerate(doc.lines, 1):
        low = line.lower()
        if any(term in low for term in doc.forbidden):
            yield number


CALLSIGN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}[0-9][A-Z]{1,3}(?![A-Za-z0-9])")
GRID = re.compile(r"(?<![A-Za-z0-9])[A-R]{2}[0-9]{2}[a-xA-X]{2}(?![A-Za-z0-9])")


def radio_identifier(doc: Doc) -> Iterator[int]:
    ok_calls = ALLOWED_CALLSIGNS | CALLSIGN_FALSE_POSITIVES
    for number, line in enumerate(doc.lines, 1):
        calls = any(m.group(0) not in ok_calls for m in CALLSIGN.finditer(line))
        grids = any(
            m.group(0).lower() not in ALLOWED_GRIDS for m in GRID.finditer(line)
        )
        if calls or grids:
            yield number


PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
]
IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
PRIVATE_HOST = re.compile(
    r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\."
    r"(?:lan|local|internal|home\.arpa|corp|intranet|localdomain|ts\.net)\b",
    re.IGNORECASE,
)
LOCAL_PATH = re.compile(
    r"(?:~|/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+)/\.?[A-Za-z]"
)


def private_host(doc: Doc) -> Iterator[int]:
    for number, line in enumerate(doc.lines, 1):
        hit = bool(PRIVATE_HOST.search(line)) or bool(LOCAL_PATH.search(line))
        for m in IPV4.finditer(line):
            try:
                addr = ipaddress.ip_address(m.group(0))
            except ValueError:
                continue
            if any(addr in net for net in PRIVATE_NETS):
                hit = True
        if hit:
            yield number


SECRET_SHAPES = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{35}\b"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bsk-[A-Za-z0-9_-]{24,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:\b(?:token|secret|api[_-]?key|password)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_-]{32,})"
)
SESSION_URL = re.compile(r"claude\.ai/|Claude-Session\s*:", re.IGNORECASE)

LIGHT_HINTS = re.compile(
    r"prefers-color-scheme\s*:\s*light"
    r"|color-scheme\s*:\s*light\s*[;}\"']"
    r"|gh-light-mode-only"
    r"|data-theme\s*=\s*[\"']?light"
    r"|background(?:-color)?\s*:\s*(?:#fff(?:fff)?\b|white\b|#f[0-9a-f]{5}\b)",
    re.IGNORECASE,
)

INLINE_CODE = re.compile(r"`[^`]*`")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
WIKI_LINK = re.compile(r"\[\[([^\]]+)\]\]")
IMAGE = re.compile(r"!\[([^\]]*)\]\(")
HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ALT_ATTR = re.compile(r"\balt\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def _prose_lines(doc: Doc) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(doc.lines, 1):
        if not doc.in_fence[number - 1]:
            yield number, INLINE_CODE.sub("", line)


def _resolves(doc: Doc, target: str, wiki: bool = False) -> bool:
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return True
    base = doc.path.parent
    if target.startswith("/"):
        base, target = doc.root, target.lstrip("/")
    candidate = (base / target).resolve()
    options = [candidate]
    if wiki or not candidate.suffix:
        options += [
            candidate.with_name(candidate.name + ".md"),
            candidate.with_name(candidate.name.replace(" ", "-") + ".md"),
        ]
    return any(p.exists() for p in options)


def broken_link(doc: Doc) -> Iterator[int]:
    for number, line in _prose_lines(doc):
        for m in LINK.finditer(line):
            target = m.group(1)
            if EXTERNAL.match(target) or target.startswith("#"):
                continue
            if not _resolves(doc, target):
                yield number
        for m in WIKI_LINK.finditer(line):
            target = m.group(1).split("|")[-1].strip()
            if not EXTERNAL.match(target) and not _resolves(doc, target, wiki=True):
                yield number


def image_alt(doc: Doc) -> Iterator[int]:
    for number, line in _prose_lines(doc):
        if any(not m.group(1).strip() for m in IMAGE.finditer(line)):
            yield number
            continue
        for m in HTML_IMG.finditer(line):
            alt = ALT_ATTR.search(m.group(0))
            if alt is None or not alt.group(2).strip():
                yield number
                break


def mermaid_text(doc: Doc) -> Iterator[int]:
    for fence in doc.fences:
        if fence.lang != "mermaid":
            continue
        nxt = fence.end + 1
        while nxt < len(doc.lines) and not doc.lines[nxt].strip():
            nxt += 1
        text = doc.lines[nxt].strip() if nxt < len(doc.lines) else ""
        plain = (
            len(text) >= 20
            and not text.startswith(("#", "!", "<", "|", ">"))
            and not FENCE.match(text)
        )
        if not plain:
            yield fence.start + 1


RULES: dict[str, Rule] = {
    "em-dash": em_dash,
    "forbidden-term": forbidden_term,
    "radio-identifier": radio_identifier,
    "private-host": private_host,
    "secret-shape": _regex_rule(SECRET_SHAPES),
    "session-url": _regex_rule(SESSION_URL),
    "broken-link": broken_link,
    "image-alt": image_alt,
    "mermaid-text": mermaid_text,
    "light-mode": _regex_rule(LIGHT_HINTS),
}


def load_forbidden(root: Path, explicit: Path | None) -> list[str]:
    terms = [
        part.strip().lower()
        for part in os.environ.get(FORBIDDEN_ENV, "").split(",")
        if part.strip()
    ]
    try:
        text = (explicit or root / FORBIDDEN_FILE).read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line.lower())
    return terms


def collect(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in SUFFIXES
            )
        elif path.is_file():
            found.append(path)
    return found


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def check(
    root: Path,
    paths: list[Path],
    forbidden: list[str],
    rules: dict[str, Rule] | None = None,
) -> list[tuple[str, int, str]]:
    active = RULES if rules is None else rules
    results: list[tuple[str, int, str]] = []
    for path in collect(paths):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            results.append((_rel(root, path), 0, "unreadable"))
            continue
        doc = Doc(
            path=path.resolve(), root=root.resolve(), lines=lines, forbidden=forbidden
        )
        for name, rule in active.items():
            for number in sorted(set(rule(doc))):
                results.append((_rel(root, path), number, name))
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parent.parent
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--forbidden-file", type=Path)
    parser.add_argument(
        "--require-forbidden",
        action="store_true",
        help="fail when no forbidden terms are configured",
    )
    args = parser.parse_args(argv)
    paths = args.paths or [args.root / "docs" / "wiki"]
    forbidden = load_forbidden(args.root, args.forbidden_file)
    if not forbidden:
        print("note: no forbidden terms configured; forbidden-term rule skipped")
        if args.require_forbidden:
            print("FAIL forbidden terms are required but none are configured")
            return 1
    if not any(p.exists() for p in paths):
        print("note: nothing to scan")
        return 0
    results = check(args.root, paths, forbidden)
    for rel, number, name in results:
        print(f"FAIL {rel}:{number}: {name}")
    if results:
        return 1
    print("wiki scrub check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
