#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Flatten docs/wiki/ into the layout a GitHub Wiki stores.

A GitHub Wiki is a flat folder of pages. The repository keeps the sources in
folders (reviewed in pull requests); this script turns them into the flat tree
the publish workflow pushes. It is a pure transform of the files under the
source directory: no network, no git, nothing read from outside it.

Naming rule (one rule, no exceptions):

* ``Home.md`` at the root is required and stays ``Home.md``.
* ``_Sidebar.md`` and ``_Footer.md`` are allowed at the root only and keep
  their names. If there is no ``_Sidebar.md``, one is generated from the page
  list (the home page, then the pages grouped by their top-level folder).
* Any other page ``a/b/c.md`` becomes ``a-b-c.md``: the folders and the file
  stem joined with a hyphen. In a subfolder, ``index.md`` or ``README.md``
  names the folder itself: ``a/b/index.md`` becomes ``a-b.md``.
* Allowed characters in a path segment are letters, digits, ``.``, ``_`` and
  ``-``. Two sources that map to one flat name are an error, never a silent
  overwrite.
* Images (png, jpg, jpeg, gif, svg, webp) are flattened the same way and keep
  their extension. Any other file type, a symlink or a hidden file is an error.

Links: relative links in Markdown (inline, images and reference definitions)
are resolved against the source file and rewritten to the flat name. Page links
drop the ``.md`` (the wiki's own form) and keep ``#anchor``. A link to a folder
resolves to its ``index.md`` or ``README.md``. Absolute URLs, ``mailto:``,
anchors on the same page and anything inside code spans or fenced code are left
alone. A relative link that is missing, leaves the source directory, or is a
root-absolute path is an error.

Output (and every error) is "path: message" only; page content is never
printed.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PAGE_SUFFIXES = {".md"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ROOT_ONLY = {"_Sidebar.md", "_Footer.md"}
RESERVED_LOWER = {n.lower() for n in ROOT_ONLY}
FOLDER_PAGE_NAMES = {"index.md", "readme.md"}
MAX_FILE_BYTES = 5 * 1024 * 1024

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# [text](target "title") and ![alt](target "title"); target may be <...>.
INLINE_LINK_RE = re.compile(
    r"(?P<head>!?\[(?:[^\[\]]|\[[^\]]*\])*\]\()"
    r"(?P<ws1>\s*)"
    r"(?P<target><[^>\n]*>|[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)"
    r"(?P<tail>(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\))"
)
# [label]: target "title"
REF_DEF_RE = re.compile(
    r"^(?P<head>\s{0,3}\[[^\]]+\]:\s*)(?P<target><[^>\n]*>|\S+)(?P<tail>.*)$"
)


class FlattenError(Exception):
    """Collected problems; each entry is ``(path, message)``."""

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        super().__init__("; ".join(f"{p}: {m}" for p, m in problems))
        self.problems = problems


def flat_name(rel: str) -> str:
    """Return the flat file name for a source path relative to the root."""
    parts = rel.split("/")
    base = parts[-1]
    if len(parts) == 1:
        return base
    stem, ext = posixpath.splitext(base)
    folders = parts[:-1]
    if base.lower() in FOLDER_PAGE_NAMES:
        return "-".join(folders) + ".md"
    return "-".join([*folders, stem]) + ext


def _check_sources(
    files: dict[str, bytes],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Validate names; return source -> flat name and the problems found."""
    problems: list[tuple[str, str]] = []
    mapping: dict[str, str] = {}
    seen: dict[str, str] = {}
    for rel in sorted(files):
        parts = rel.split("/")
        if any(not SEGMENT_RE.match(p) or p.startswith(".") for p in parts):
            problems.append(
                (rel, "path segment has a character or leading dot that is not allowed")
            )
            continue
        suffix = posixpath.splitext(rel)[1].lower()
        if suffix not in PAGE_SUFFIXES | ASSET_SUFFIXES:
            problems.append((rel, "file type is not allowed in the wiki source"))
            continue
        if len(files[rel]) > MAX_FILE_BYTES:
            problems.append((rel, "file is larger than the size cap"))
            continue
        flat = flat_name(rel)
        if (len(parts) > 1 and parts[-1].lower() in RESERVED_LOWER) or (
            flat.lower() in RESERVED_LOWER and rel not in ROOT_ONLY
        ):
            problems.append(
                (
                    rel,
                    "_Sidebar.md and _Footer.md are allowed at the root only,"
                    " in that exact spelling",
                )
            )
            continue
        if flat.lower() in seen:
            problems.append((rel, "flat output name collides with another source"))
            continue
        seen[flat.lower()] = rel
        mapping[rel] = flat
    if "Home.md" not in files:
        problems.append(("Home.md", "required home page is missing"))
    return mapping, problems


def _split_code(line: str) -> list[tuple[bool, str]]:
    """Split a line into (is_code, text) so code spans are never rewritten."""
    out: list[tuple[bool, str]] = []
    pos = 0
    for m in CODE_SPAN_RE.finditer(line):
        if m.start() > pos:
            out.append((False, line[pos : m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(line):
        out.append((False, line[pos:]))
    return out


class _Rewriter:
    def __init__(
        self, files: dict[str, bytes], mapping: dict[str, str], asset_base: str
    ) -> None:
        self.files = files
        self.mapping = mapping
        self.asset_base = asset_base
        self.problems: list[tuple[str, str]] = []

    def _resolve(self, src: str, target: str) -> str | None:
        """Return the new target (the input if left alone), or None on error."""
        raw = (
            target[1:-1] if target.startswith("<") and target.endswith(">") else target
        )
        if not raw or raw.startswith("#"):
            return target
        try:
            parsed = urlsplit(raw)
        except Exception:  # noqa: BLE001 - parser errors can quote the link text
            self.problems.append((src, "relative link target not valid"))
            return None
        if parsed.scheme or parsed.netloc:
            return target
        path = unquote(parsed.path)
        if path.startswith("/"):
            self.problems.append(
                (src, "root-absolute link is not supported; use a relative path")
            )
            return None
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(src), path))
        if resolved == ".." or resolved.startswith("../") or resolved == ".":
            self.problems.append(
                (src, "link leaves the wiki source directory or names no file")
            )
            return None
        candidates = [resolved]
        if not posixpath.splitext(resolved)[1]:
            candidates += [
                f"{resolved}.md",
                f"{resolved}/index.md",
                f"{resolved}/README.md",
                f"{resolved}/readme.md",
                f"{resolved}/Index.md",
            ]
        hit = next((c for c in candidates if c in self.mapping), None)
        if hit is None:
            self.problems.append((src, "relative link target not found"))
            return None
        flat = self.mapping[hit]
        frag = f"#{parsed.fragment}" if parsed.fragment else ""
        if flat.endswith(".md"):
            new = flat[: -len(".md")] + frag
        elif self.asset_base:
            new = self.asset_base + flat + frag
        else:
            new = flat + frag
        return f"<{new}>" if target.startswith("<") else new

    def _inline(self, src: str, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            new = self._resolve(src, m.group("target"))
            if new is None:
                return m.group(0)
            return f"{m.group('head')}{m.group('ws1')}{new}{m.group('tail')}"

        return INLINE_LINK_RE.sub(repl, text)

    def page(self, src: str, data: bytes) -> bytes:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.problems.append((src, "page is not valid UTF-8"))
            return data
        out: list[str] = []
        fence: str | None = None
        for line in text.split("\n"):
            m = FENCE_RE.match(line)
            if fence is None and m:
                fence = m.group(1)
                out.append(line)
                continue
            if fence is not None:
                if (
                    m
                    and m.group(1)[0] == fence[0]
                    and len(m.group(1)) >= len(fence)
                    and line.strip().strip(fence[0]) == ""
                ):
                    fence = None
                out.append(line)
                continue
            ref = REF_DEF_RE.match(line)
            if ref:
                new = self._resolve(src, ref.group("target"))
                out.append(
                    line
                    if new is None
                    else f"{ref.group('head')}{new}{ref.group('tail')}"
                )
                continue
            out.append(
                "".join(
                    seg if code else self._inline(src, seg)
                    for code, seg in _split_code(line)
                )
            )
        return "\n".join(out).encode("utf-8")


def _title(stem: str) -> str:
    return stem.replace("-", " ")


def make_sidebar(mapping: dict[str, str]) -> str:
    """A sidebar from the page list: home first, then pages by top-level folder."""
    pages = sorted(
        (rel, flat[:-3])
        for rel, flat in mapping.items()
        if flat.endswith(".md") and flat not in {"Home.md", *ROOT_ONLY}
    )
    lines = ["**[Home](Home)**", ""]
    group = ""
    for rel, stem in pages:
        top = rel.split("/")[0] if "/" in rel else ""
        if top != group:
            group = top
            lines += ["", f"**{_title(top)}**" if top else "**Pages**", ""]
        lines.append(f"- [{_title(stem)}]({stem})")
    return "\n".join(lines).rstrip("\n") + "\n"


def flatten(files: dict[str, bytes], asset_base: str = "") -> dict[str, bytes]:
    """Return the flat wiki tree for ``files`` (relative POSIX path -> bytes).

    Raises FlattenError listing every problem found.
    """
    mapping, problems = _check_sources(files)
    rewriter = _Rewriter(files, mapping, asset_base)
    out: dict[str, bytes] = {}
    for rel, flat in mapping.items():
        if flat.endswith(".md"):
            out[flat] = rewriter.page(rel, files[rel])
        else:
            out[flat] = files[rel]
    problems += rewriter.problems
    if problems:
        raise FlattenError(sorted(set(problems)))
    if "_Sidebar.md" not in out:
        out["_Sidebar.md"] = make_sidebar(mapping).encode("utf-8")
    return out


def read_tree(root: Path) -> dict[str, bytes]:
    """Read every file under ``root``; a symlink or non-regular entry is refused."""
    problems: list[tuple[str, str]] = []
    files: dict[str, bytes] = {}
    stack = [root]
    while stack:
        cur = stack.pop()
        for entry in sorted(cur.iterdir()):
            rel = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                problems.append((rel, "symlinks are not allowed in the wiki source"))
            elif entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                if entry.stat().st_size > MAX_FILE_BYTES:
                    problems.append((rel, "file is larger than the size cap"))
                else:
                    files[rel] = entry.read_bytes()
            else:
                problems.append((rel, "not a regular file"))
    if problems:
        raise FlattenError(sorted(problems))
    return files


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--src", type=Path, default=Path("docs/wiki"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--asset-base",
        default="",
        help="URL prefix for image links (the wiki's own address)",
    )
    args = parser.parse_args(argv)
    src: Path = args.src
    out: Path = args.out
    if not src.is_dir() or src.is_symlink():
        print(f"{src}: source directory not found", file=sys.stderr)
        return 1
    if out.resolve() == src.resolve() or src.resolve() in out.resolve().parents:
        print(f"{out}: output must be outside the source directory", file=sys.stderr)
        return 1
    if out.exists() and any(out.iterdir()):
        print(f"{out}: output directory must be empty", file=sys.stderr)
        return 1
    try:
        tree = flatten(read_tree(src), args.asset_base)
    except FlattenError as exc:
        for path, msg in exc.problems:
            print(f"{path}: {msg}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - never print a traceback that may quote content
        print("wiki-flatten: unexpected error while flattening", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(tree.items()):
        (out / name).write_bytes(data)
    print(f"wiki-flatten: wrote {len(tree)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
