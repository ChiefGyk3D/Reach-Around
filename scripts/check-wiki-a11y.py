#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Accessibility and dark-mode structure check for docs that get published.

Scans docs/wiki/ (or the paths given) and prints "path:line: rule" for:

  h1-count        a page must have exactly one H1 (HTML comments and code ignored)
  heading-skip    a heading level jumps down by more than one (H2 straight to H4)
  heading-empty   a heading with no text
  link-text       link text that says nothing out of context ("click here",
                  "here", "link", "this", "read more", "more") or is a bare URL
  bare-url        a raw http(s) URL in prose (not in a link, code or angle brackets)
  table-header    a table with an empty header cell
  fence-lang      a code fence with no language (use "text" for plain output)
  image-alt       an image with empty or filename-like alt text
  image-dark      a raster or SVG asset that is unsafe on a dark background: a PNG
                  or WebP/GIF/JPEG with no alpha channel is flagged when it is
                  listed nowhere in the allowlist, and an SVG with a white or
                  near-white fill is flagged

Image assets are read by header only (PNG color type, SVG fill attributes), no
imaging library. A photo that is meant to be opaque goes in the allowlist file
docs/wiki/.a11y-opaque-images (one relative path per line, "#" comments).

Mermaid text descriptions, broken links, light-mode color hints and publish
safety live in scripts/check-wiki-scrub.py; this script does not repeat them.
Output never includes page text. Stdlib only, no network.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

PAGE_SUFFIXES = {".md", ".markdown"}
IMAGE_SUFFIXES = {".png", ".svg", ".gif", ".jpg", ".jpeg", ".webp"}
ALLOWLIST_NAME = ".a11y-opaque-images"
# Navigation fragments the wiki host includes in every page; no H1 by design.
NAV_PAGES = {"_Sidebar.md", "_Footer.md", "_Header.md"}

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([A-Za-z0-9_+#.-]*)")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
BARE_URL = re.compile(r"(?<![\w(<\[\"'`/=])https?://[^\s<>)\]`\"']+")
VAGUE_TEXT = {
    "here",
    "click here",
    "link",
    "this link",
    "this",
    "this page",
    "read more",
    "more",
    "learn more",
    "see more",
    "details",
    "page",
}
URL_TEXT = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)
FILENAME_ALT = re.compile(r"^[\w .-]+\.(png|jpe?g|gif|svg|webp)$", re.IGNORECASE)
GENERIC_ALT = {"image", "img", "picture", "photo", "screenshot", "diagram", "graphic"}
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
WHITE_FILL = re.compile(
    r"""(?:fill|stop-color|background(?:-color)?|style)\s*[=:]\s*["']?[^"';>]*?"""
    r"""(#fff(?:fff)?\b|#fefefe|#fdfdfd|#fafafa|#f[0-9a-f]{5}\b|white\b|"""
    r"""rgb\(\s*25[0-5]\s*,\s*25[0-5]\s*,\s*25[0-5]\s*\))""",
    re.IGNORECASE,
)


@dataclass
class Doc:
    path: Path
    lines: list[str]
    skip: list[bool] = field(default_factory=list)  # in fence or HTML comment

    def __post_init__(self) -> None:
        self.skip = [False] * len(self.lines)
        marker = ""
        in_comment = False
        for index, line in enumerate(self.lines):
            m = FENCE.match(line)
            if marker:
                self.skip[index] = True
                if m and m.group(1)[0] == marker[0] and not m.group(2):
                    marker = ""
                continue
            if in_comment:
                self.skip[index] = True
                if "-->" in line:
                    in_comment = False
                continue
            if m:
                marker = m.group(1)
                self.skip[index] = True
                continue
            if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
                in_comment = True
                self.skip[index] = True
            elif line.strip().startswith("<!--") and line.strip().endswith("-->"):
                self.skip[index] = True


Rule = Callable[[Doc], Iterator[int]]


def _strip_code(line: str) -> str:
    return re.sub(r"`[^`]*`", "", line)


def _headings(doc: Doc) -> Iterator[tuple[int, int, str]]:
    for index, line in enumerate(doc.lines):
        if doc.skip[index]:
            continue
        m = HEADING.match(line)
        if m:
            yield index + 1, len(m.group(1)), (m.group(2) or "").strip()


def h1_count(doc: Doc) -> Iterator[int]:
    if doc.path.name in NAV_PAGES:
        return
    h1s = [n for n, level, _ in _headings(doc) if level == 1]
    if len(h1s) == 1:
        return
    if not h1s:
        yield 1
    else:
        yield from h1s[1:]


def heading_skip(doc: Doc) -> Iterator[int]:
    previous = 0
    for number, level, _ in _headings(doc):
        if previous and level > previous + 1:
            yield number
        previous = level


def heading_empty(doc: Doc) -> Iterator[int]:
    for number, _, text in _headings(doc):
        if not text:
            yield number


def link_text(doc: Doc) -> Iterator[int]:
    for index, line in enumerate(doc.lines):
        if doc.skip[index]:
            continue
        for m in MD_LINK.finditer(line):
            text = re.sub(r"\s+", " ", m.group(1).replace("`", "")).strip().lower()
            text = text.strip(".:")
            if text in VAGUE_TEXT or URL_TEXT.match(text) or not text:
                yield index + 1
                break


def bare_url(doc: Doc) -> Iterator[int]:
    for index, line in enumerate(doc.lines):
        if doc.skip[index]:
            continue
        text = MD_IMAGE.sub("", MD_LINK.sub("", _strip_code(line)))
        text = re.sub(r"<https?://[^>]*>", "", text)
        text = re.sub(r"\]\(\S*\)", "", text)
        text = re.sub(r'(?:href|src)="[^"]*"', "", text)
        if BARE_URL.search(text):
            yield index + 1


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


def table_header(doc: Doc) -> Iterator[int]:
    for index in range(1, len(doc.lines)):
        line = doc.lines[index]
        if doc.skip[index] or "-" not in line or "|" not in line:
            continue
        if not TABLE_SEP.match(line):
            continue
        head = doc.lines[index - 1]
        if doc.skip[index - 1] or "|" not in head:
            continue
        if any(not cell for cell in _cells(head)):
            yield index


def fence_lang(doc: Doc) -> Iterator[int]:
    marker = ""
    for index, line in enumerate(doc.lines):
        m = FENCE.match(line)
        if not m:
            continue
        if marker:
            if m.group(1)[0] == marker[0] and not m.group(2):
                marker = ""
            continue
        marker = m.group(1)
        if not m.group(2):
            yield index + 1


def _alts(line: str) -> Iterator[str]:
    for m in MD_IMAGE.finditer(line):
        yield m.group(1)
    for m in HTML_IMG.finditer(line):
        alt = re.search(r"""\balt\s*=\s*["']([^"']*)["']""", m.group(0), re.I)
        if alt:
            yield alt.group(1)


def image_alt(doc: Doc) -> Iterator[int]:
    for index, line in enumerate(doc.lines):
        if doc.skip[index]:
            continue
        for alt in _alts(line):
            clean = alt.strip().lower()
            if not clean or FILENAME_ALT.match(clean) or clean in GENERIC_ALT:
                yield index + 1
                break


RULES: dict[str, Rule] = {
    "h1-count": h1_count,
    "heading-skip": heading_skip,
    "heading-empty": heading_empty,
    "link-text": link_text,
    "bare-url": bare_url,
    "table-header": table_header,
    "fence-lang": fence_lang,
    "image-alt": image_alt,
}


def png_has_alpha(data: bytes) -> bool | None:
    """True when a PNG can carry transparency; None when it is not a PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    color_type = data[25]
    if color_type in (4, 6):
        return True
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        if data[pos + 4 : pos + 8] == b"tRNS":
            return True
        pos += 12 + length
    return False


def image_problem(path: Path, allow: set[str], rel: str) -> bool:
    if rel in allow:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return True
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return bool(WHITE_FILL.search(data.decode("utf-8", errors="replace")))
    if suffix == ".png":
        return png_has_alpha(data) is not True
    # gif, jpeg and webp: opaque or unknowable by header; require the allowlist
    return True


def load_allowlist(wiki: Path) -> set[str]:
    try:
        text = (wiki / ALLOWLIST_NAME).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def collect(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in PAGE_SUFFIXES | IMAGE_SUFFIXES
            )
        elif path.is_file():
            found.append(path)
    return found


def check(
    root: Path, paths: list[Path], rules: dict[str, Rule] | None = None
) -> list[tuple[str, int, str]]:
    active = RULES if rules is None else rules
    wiki = root / "docs" / "wiki"
    allow = load_allowlist(wiki)
    results: list[tuple[str, int, str]] = []
    for path in collect(paths):
        try:
            rel = str(path.resolve().relative_to(wiki.resolve()))
            shown = str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = shown = str(path)
        if path.suffix.lower() in IMAGE_SUFFIXES:
            if image_problem(path, allow, rel):
                results.append((shown, 0, "image-dark"))
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            results.append((shown, 0, "unreadable"))
            continue
        doc = Doc(path=path, lines=lines)
        for name, rule in active.items():
            for number in sorted(set(rule(doc))):
                results.append((shown, number, name))
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    default_root = Path(__file__).resolve().parent.parent
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=default_root)
    args = parser.parse_args(argv)
    paths = args.paths or [args.root / "docs" / "wiki"]
    if not any(p.exists() for p in paths):
        print("ok: nothing to check")
        return 0
    results = check(args.root, paths)
    for shown, number, rule in results:
        print(f"{shown}:{number}: {rule}")
    if results:
        print(f"FAIL {len(results)} accessibility finding(s)")
        return 1
    print("ok: wiki accessibility structure")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
