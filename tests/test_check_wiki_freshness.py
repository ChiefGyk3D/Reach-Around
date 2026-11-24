# SPDX-License-Identifier: Apache-2.0
"""scripts/check-wiki-freshness.py: stale wiki pages fail, current ones pass."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-wiki-freshness.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_wiki_freshness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cwf = load()


def git(root: Path, *args: str, when: str | None = None) -> None:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(root), *args],  # noqa: S607
        check=True,
        env=env,
        capture_output=True,
    )


def commit(root: Path, message: str, when: str) -> None:
    git(root, "add", "-A")
    git(
        root,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        message,
        when=when,
    )


def registry(root: Path, pages: dict) -> None:
    (root / "docs/wiki").mkdir(parents=True, exist_ok=True)
    (root / "docs/wiki/FRESHNESS.json").write_text(json.dumps({"pages": pages}))


def make_repo(tmp_path: Path, verified: str = "2026-11-10") -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/engine.py").write_text("v1\n")
    (tmp_path / "docs/wiki").mkdir(parents=True)
    (tmp_path / "docs/wiki/Page.md").write_text("page\n")
    registry(
        tmp_path, {"Page.md": {"verified": verified, "sources": ["src/engine.py"]}}
    )
    commit(tmp_path, "initial", "2026-11-01T12:00:00+00:00")
    return tmp_path


def test_current_page_passes(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert cwf.check(root) == []
    assert cwf.main(["--root", str(root)]) == 0


def test_source_changed_after_verified_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "src/engine.py").write_text("v2\n")
    commit(root, "change", "2026-11-15T12:00:00+00:00")
    problems = cwf.check(root)
    assert len(problems) == 1
    assert "src/engine.py changed on 2026-11-15" in problems[0]
    assert cwf.main(["--root", str(root)]) == 1


def test_same_day_change_is_not_stale_and_bump_clears(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "src/engine.py").write_text("v2\n")
    commit(root, "same day", "2026-11-10T23:59:00+00:00")
    assert cwf.check(root) == []
    (root / "src/engine.py").write_text("v3\n")
    commit(root, "later", "2026-11-20T01:00:00+00:00")
    assert len(cwf.check(root)) == 1
    registry(
        root, {"Page.md": {"verified": "2026-11-20", "sources": ["src/engine.py"]}}
    )
    commit(root, "bump", "2026-11-20T02:00:00+00:00")
    assert cwf.check(root) == []


def test_directory_source_is_watched(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    registry(root, {"Page.md": {"verified": "2026-11-10", "sources": ["src"]}})
    (root / "src/other.py").write_text("x\n")
    commit(root, "dir change", "2026-11-12T12:00:00+00:00")
    assert len(cwf.check(root)) == 1


def test_unregistered_missing_and_bad_entries_fail(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "docs/wiki/New.md").write_text("new\n")
    registry(
        root,
        {
            "Page.md": {"verified": "not-a-date", "sources": ["src/engine.py"]},
            "Gone.md": {"verified": "2026-11-10", "sources": ["src/engine.py"]},
        },
    )
    text = "\n".join(cwf.check(root))
    assert "New.md: wiki page has no entry" in text
    assert "Gone.md: registered but not found" in text
    assert "Page.md: 'verified' must be a YYYY-MM-DD date" in text


def test_missing_and_escaping_sources_fail(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    registry(
        root,
        {
            "Page.md": {
                "verified": "2026-11-10",
                "sources": ["src/nope.py", "../outside"],
            }
        },
    )
    assert len(cwf.check(root)) == 2


def test_unreadable_registry_fails(tmp_path: Path) -> None:
    assert "cannot read" in cwf.check(tmp_path)[0]


def test_repo_registry_is_valid() -> None:
    """The committed registry parses, and every page and source it names exists."""
    problems = [p for p in cwf.check(ROOT) if "changed on" not in p]
    assert problems == []
