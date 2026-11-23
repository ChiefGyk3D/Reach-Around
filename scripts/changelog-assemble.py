#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fold per-PR changelog fragments into CHANGELOG.md (stdlib only).

Each PR adds one file ``changelog.d/<issue-number>-<slug>.md`` holding one entry in
the CHANGELOG entry style, so parallel PRs never edit the same lines.

  changelog-assemble.py verify               fragments are valid and [Unreleased]
                                             holds no hand-written entry
  changelog-assemble.py assemble             preview: print CHANGELOG.md as it would
                                             read with the fragments in [Unreleased];
                                             writes and deletes nothing
  changelog-assemble.py release VERSION --date YYYY-MM-DD
                                             move them into a new [VERSION] section,
                                             restore the empty [Unreleased], delete them
  changelog-assemble.py check-pr             CI: a pull request adds one fragment
                                             unless it says ``no-changelog: <reason>``
                                             in its body or carries the label

Entries are ordered by issue number, highest first, joined by one newline (the style
[Unreleased] has always had), so the output is byte-stable. A fragment that is
malformed, has no issue number in its name, repeats an issue number, or repeats another
fragment's text is refused.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT_DIR = "changelog.d"
POINTER = (
    "<!-- Entries live in changelog.d/ (one fragment per PR, named "
    "<issue>-<slug>.md); scripts/changelog-assemble.py folds them in at release. -->"
)
NO_CHANGELOG_LABEL = "no-changelog"
_NAME = re.compile(r"(\d+)-([a-z0-9]+(?:-[a-z0-9]+)*)\.md")
_EXEMPT_LINE = re.compile(r"^no-changelog: \S.*$", re.MULTILINE)
_VERSION = re.compile(r"\d+\.\d+\.\d+(?:-rc\.\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UNRELEASED = "## [Unreleased]"


class ChangelogError(Exception):
    """A fragment, the changelog or the pull request breaks a rule."""


@dataclass(frozen=True)
class Fragment:
    issue: int
    slug: str
    text: str


def parse_fragment(name: str, raw: bytes) -> Fragment:
    """Validate one fragment file and return its entry (no trailing newline)."""
    match = _NAME.fullmatch(name)
    if match is None:
        raise ChangelogError(
            f"{name}: fragment names look like <issue-number>-<slug>.md "
            "(digits, a dash, then lowercase words joined by dashes)"
        )
    issue = int(match.group(1))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChangelogError(f"{name}: not valid UTF-8") from exc
    if "\r" in text or "\t" in text:
        raise ChangelogError(f"{name}: carriage returns and tabs are not allowed")
    if text and not text.endswith("\n"):
        raise ChangelogError(f"{name}: the file must end with a newline")
    body = text.rstrip("\n")
    lines = body.split("\n")
    if not body.strip() or not lines[0].startswith("- ") or not lines[0][2:].strip():
        raise ChangelogError(f"{name}: the entry must start with '- ' and some text")
    for line in lines[1:]:
        if line and not line.startswith(" "):
            raise ChangelogError(
                f"{name}: one entry per file; continuation lines are indented "
                f"(found {line[:40]!r})"
            )
    if any(line != line.rstrip() for line in lines):
        raise ChangelogError(f"{name}: trailing whitespace")
    if re.search(rf"#{issue}(?!\d)", body) is None:
        raise ChangelogError(f"{name}: the entry never mentions #{issue}")
    return Fragment(issue, match.group(2), body)


def load_fragments(directory: Path) -> list[Fragment]:
    """Read, validate and order every fragment in ``directory`` (may not exist)."""
    if not directory.is_dir():
        return []
    found: list[Fragment] = []
    for path in sorted(directory.iterdir()):
        if path.name == "README.md" and path.is_file():
            continue
        if not path.is_file() or path.is_symlink():
            raise ChangelogError(
                f"{path.name}: only regular fragment files belong here"
            )
        found.append(parse_fragment(path.name, path.read_bytes()))
    seen_issue: dict[int, str] = {}
    seen_text: dict[str, str] = {}
    for frag in found:
        label = f"{frag.issue}-{frag.slug}.md"
        if frag.issue in seen_issue:
            raise ChangelogError(
                f"{label}: duplicate issue number {frag.issue} "
                f"(also {seen_issue[frag.issue]})"
            )
        if frag.text in seen_text:
            raise ChangelogError(f"{label}: same text as {seen_text[frag.text]}")
        seen_issue[frag.issue] = label
        seen_text[frag.text] = label
    return sorted(found, key=lambda f: (-f.issue, f.slug))


def render_entries(fragments: Sequence[Fragment]) -> str:
    return "\n".join(f.text for f in fragments)


def split_unreleased(changelog: str) -> tuple[str, str, str]:
    """Return (through the heading line, the section body, the rest from next ##)."""
    lines = changelog.split("\n")
    try:
        start = lines.index(_UNRELEASED)
    except ValueError as exc:
        raise ChangelogError("CHANGELOG.md has no '## [Unreleased]' heading") from exc
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
        len(lines),
    )
    head = "\n".join(lines[: start + 1])
    body = "\n".join(lines[start + 1 : end])
    rest = "\n".join(lines[end:])
    return head, body, rest


def check_unreleased_empty(body: str) -> None:
    for line in body.split("\n"):
        if line.strip() and line.strip() != POINTER:
            raise ChangelogError(
                "[Unreleased] holds a hand-written entry; add it as a fragment "
                f"in {FRAGMENT_DIR}/ instead (first line: {line[:60]!r})"
            )


def assemble(changelog: str, fragments: Sequence[Fragment]) -> str:
    head, body, rest = split_unreleased(changelog)
    check_unreleased_empty(body)
    if not fragments:
        return changelog
    return f"{head}\n\n{render_entries(fragments)}\n\n{rest}"


def release(
    changelog: str, fragments: Sequence[Fragment], version: str, date: str
) -> str:
    if _VERSION.fullmatch(version) is None:
        raise ChangelogError(f"version {version!r} is not x.y.z or x.y.z-rc.N")
    if _DATE.fullmatch(date) is None:
        raise ChangelogError(f"date {date!r} is not YYYY-MM-DD")
    if not fragments:
        raise ChangelogError(f"no fragments in {FRAGMENT_DIR}/; nothing to release")
    head, body, rest = split_unreleased(changelog)
    check_unreleased_empty(body)
    return (
        f"{head}\n\n{POINTER}\n\n## [{version}] - {date}\n\n"
        f"{render_entries(fragments)}\n\n{rest}"
    )


def exempt(body: str | None, labels: Iterable[str]) -> bool:
    """Narrow exemption: the exact label, or a body line ``no-changelog: <reason>``."""
    if NO_CHANGELOG_LABEL in set(labels):
        return True
    return bool(body and _EXEMPT_LINE.search(body.replace("\r\n", "\n")))


def check_pull_request(
    added: Iterable[str], body: str | None, labels: Iterable[str]
) -> None:
    """Raise unless the PR added exactly one fragment (or says it is exempt)."""
    prefix = f"{FRAGMENT_DIR}/"
    paths = [
        p
        for p in added
        if p.startswith(prefix) and p != f"{prefix}README.md" and p.endswith(".md")
    ]
    if exempt(body, labels):
        return
    if len(paths) > 1:
        raise ChangelogError(
            "a pull request adds one fragment, found " + ", ".join(sorted(paths))
        )
    if not paths:
        raise ChangelogError(
            f"add one fragment {prefix}<issue-number>-<slug>.md, or put a line "
            "'no-changelog: <reason>' in the PR body (or use the "
            f"'{NO_CHANGELOG_LABEL}' label)"
        )


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/usr/bin/git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout


def _added_files(root: Path, sha: str) -> list[str]:
    """Files the merge commit ``sha`` adds over its first parent (the base)."""
    _git(root, "fetch", "--no-tags", "--depth=2", "origin", sha)
    out = _git(root, "diff", "--name-only", "--diff-filter=A", f"{sha}^1", sha)
    return [line for line in out.split("\n") if line]


def cmd_check_pr(root: Path, event_path: str | None, sha: str | None) -> int:
    if not event_path or not Path(event_path).is_file():
        print("check-pr: no event payload; not a pull request run, skipped")
        return 0
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        print("check-pr: not a pull_request event, skipped")
        return 0
    if not sha:
        raise ChangelogError("check-pr: GITHUB_SHA is not set")
    labels = [str(lbl.get("name", "")) for lbl in pr.get("labels") or []]
    check_pull_request(_added_files(root, sha), pr.get("body"), labels)
    print("check-pr: ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    sub.add_parser("assemble")
    rel = sub.add_parser("release")
    rel.add_argument("version")
    rel.add_argument("--date", required=True)
    sub.add_parser("check-pr")
    args = parser.parse_args(argv)
    root: Path = args.root
    changelog_path = root / "CHANGELOG.md"
    directory = root / FRAGMENT_DIR
    try:
        if args.command == "check-pr":
            return cmd_check_pr(
                root, os.environ.get("GITHUB_EVENT_PATH"), os.environ.get("GITHUB_SHA")
            )
        fragments = load_fragments(directory)
        text = changelog_path.read_text(encoding="utf-8")
        if args.command == "verify":
            check_unreleased_empty(split_unreleased(text)[1])
            print(f"verify: {len(fragments)} fragment(s) ok")
        elif args.command == "assemble":
            sys.stdout.write(assemble(text, fragments))
        else:
            changelog_path.write_text(
                release(text, fragments, args.version, args.date), encoding="utf-8"
            )
            for path in sorted(directory.glob("*.md")):
                if path.name != "README.md":
                    path.unlink()
    except (ChangelogError, subprocess.CalledProcessError) as exc:
        print(f"changelog-assemble: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
