# SPDX-License-Identifier: Apache-2.0
"""scripts/changelog-assemble.py: fragments fold into CHANGELOG.md deterministically."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "changelog-assemble.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("changelog_assemble", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ca = load()

CHANGELOG = f"""# Changelog

## [Unreleased]

{ca.POINTER}

## [0.3.0-rc.6] - 2026-11-23

- Older entry (#595).
"""


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def project(tmp_path: Path, fragments: dict[str, str]) -> Path:
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    for name, text in fragments.items():
        write(tmp_path / "changelog.d", name, text)
    return tmp_path


def cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_orders_by_issue_number_highest_first(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "5-low.md": "- Low (#5).\n",
            "700-high.md": "- High (#700).\n",
            "90-mid.md": "- Mid (#90).\n",
        },
    )
    frags = ca.load_fragments(root / "changelog.d")
    assert [f.issue for f in frags] == [700, 90, 5]
    out = ca.assemble(CHANGELOG, frags)
    assert "- High (#700).\n- Mid (#90).\n- Low (#5).\n\n## [0.3.0-rc.6]" in out


def test_output_is_byte_stable(tmp_path: Path) -> None:
    files = {"1-a.md": "- A (#1).\n", "2-b.md": "- B (#2).\n  - sub (#2)\n"}
    root = project(tmp_path, files)
    first = ca.assemble(CHANGELOG, ca.load_fragments(root / "changelog.d"))
    for _ in range(3):
        again = ca.assemble(CHANGELOG, ca.load_fragments(root / "changelog.d"))
        assert again == first
    # Creation order must not matter.
    other = tmp_path / "other"
    other.mkdir()
    for name in reversed(list(files)):
        write(other / "changelog.d", name, files[name])
    assert ca.assemble(CHANGELOG, ca.load_fragments(other / "changelog.d")) == first


def test_empty_or_missing_directory_changes_nothing(tmp_path: Path) -> None:
    assert ca.load_fragments(tmp_path / "absent") == []
    (tmp_path / "changelog.d").mkdir()
    write(tmp_path / "changelog.d", "README.md", "# how this works\n")
    frags = ca.load_fragments(tmp_path / "changelog.d")
    assert frags == []
    assert ca.assemble(CHANGELOG, frags) == CHANGELOG


@pytest.mark.parametrize(
    ("name", "text", "why"),
    [
        ("notes.md", "- X (#1).\n", "fragment names look like"),
        ("abc-slug.md", "- X (#1).\n", "fragment names look like"),
        ("1-Slug.md", "- X (#1).\n", "fragment names look like"),
        ("1-x.md", "Plain text (#1).\n", "must start with"),
        ("1-x.md", "", "must start with"),
        ("1-x.md", "- X (#1).", "end with a newline"),
        ("1-x.md", "- X (#1).\n- Y (#1).\n", "one entry per file"),
        ("1-x.md", "- X (#1).\n## [1.0.0]\n", "one entry per file"),
        ("1-x.md", "- X (#2).\n", "never mentions #1"),
        ("1-x.md", "- X (#12).\n", "never mentions #1"),
        ("1-x.md", "- X (#1). \n", "trailing whitespace"),
        ("1-x.md", "- X (#1).\r\n", "carriage returns"),
    ],
)
def test_malformed_fragment_is_refused(
    tmp_path: Path, name: str, text: str, why: str
) -> None:
    write(tmp_path / "changelog.d", name, text)
    with pytest.raises(ca.ChangelogError, match=why):
        ca.load_fragments(tmp_path / "changelog.d")


def test_non_utf8_fragment_is_refused(tmp_path: Path) -> None:
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "1-x.md").write_bytes(b"- X \xff (#1).\n")
    with pytest.raises(ca.ChangelogError, match="UTF-8"):
        ca.load_fragments(tmp_path / "changelog.d")


def test_duplicate_issue_number_is_refused(tmp_path: Path) -> None:
    write(tmp_path / "changelog.d", "7-one.md", "- One (#7).\n")
    write(tmp_path / "changelog.d", "7-two.md", "- Two (#7).\n")
    with pytest.raises(ca.ChangelogError, match="duplicate issue number 7"):
        ca.load_fragments(tmp_path / "changelog.d")


def test_duplicate_text_is_refused(tmp_path: Path) -> None:
    write(tmp_path / "changelog.d", "7-one.md", "- Same (#7, #8).\n")
    write(tmp_path / "changelog.d", "8-two.md", "- Same (#7, #8).\n")
    with pytest.raises(ca.ChangelogError, match="same text as"):
        ca.load_fragments(tmp_path / "changelog.d")


def test_hand_written_unreleased_entry_is_refused() -> None:
    text = CHANGELOG.replace(ca.POINTER, "- Hand written (#9).")
    with pytest.raises(ca.ChangelogError, match="hand-written"):
        ca.assemble(text, [ca.Fragment(1, "a", "- A (#1).")])


def test_release_moves_entries_and_restores_the_pointer(tmp_path: Path) -> None:
    root = project(tmp_path, {"2-b.md": "- B (#2).\n", "1-a.md": "- A (#1).\n"})
    out = ca.release(
        CHANGELOG, ca.load_fragments(root / "changelog.d"), "0.3.0-rc.7", "2026-11-24"
    )
    assert out == (
        f"# Changelog\n\n## [Unreleased]\n\n{ca.POINTER}\n\n"
        "## [0.3.0-rc.7] - 2026-11-24\n\n- B (#2).\n- A (#1).\n\n"
        "## [0.3.0-rc.6] - 2026-11-23\n\n- Older entry (#595).\n"
    )


@pytest.mark.parametrize(
    ("version", "date"), [("1.0", "2026-11-24"), ("1.0.0", "11-24-2026")]
)
def test_release_refuses_bad_version_or_date(version: str, date: str) -> None:
    with pytest.raises(ca.ChangelogError):
        ca.release(CHANGELOG, [ca.Fragment(1, "a", "- A (#1).")], version, date)


def test_release_with_no_fragments_is_refused() -> None:
    with pytest.raises(ca.ChangelogError, match="nothing to release"):
        ca.release(CHANGELOG, [], "1.0.0", "2026-11-24")


def test_cli_release_deletes_fragments_and_keeps_readme(tmp_path: Path) -> None:
    root = project(tmp_path, {"1-a.md": "- A (#1).\n"})
    write(root / "changelog.d", "README.md", "# readme\n")
    run = cli(root, "release", "1.0.0", "--date", "2026-11-24")
    assert run.returncode == 0, run.stderr
    assert sorted(p.name for p in (root / "changelog.d").iterdir()) == ["README.md"]
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.0.0] - 2026-11-24\n\n- A (#1)." in text


def test_cli_refuses_a_malformed_fragment(tmp_path: Path) -> None:
    root = project(tmp_path, {"1-a.md": "not an entry\n"})
    run = cli(root, "assemble")
    assert run.returncode == 1
    assert "must start with" in run.stderr
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG


# The pre-migration [Unreleased] text of CHANGELOG.md, in the order fragments produce.
OLD_UNRELEASED = (
    "- Third entry (#30, epic #1): details\n"
    "  - nested line\n"
    "- Second entry (#20).\n"
    "- First entry (#10).\n"
)


def test_current_entries_as_fragments_reproduce_todays_text(tmp_path: Path) -> None:
    old = (
        "# Changelog\n\n## [Unreleased]\n\n"
        f"{OLD_UNRELEASED}\n## [0.3.0-rc.6] - x\n\n- Old (#1).\n"
    )
    entries = re.split(r"\n(?=- )", OLD_UNRELEASED.rstrip("\n"))
    for entry in entries:
        issue = re.search(r"#(\d+)", entry)
        assert issue is not None
        write(tmp_path / "changelog.d", f"{issue.group(1)}-entry.md", entry + "\n")
    empty = old.replace(OLD_UNRELEASED, f"{ca.POINTER}\n")
    frags = ca.load_fragments(tmp_path / "changelog.d")
    assert ca.assemble(empty, frags) == old


def test_repo_changelog_is_clean_and_fragments_valid() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    ca.check_unreleased_empty(ca.split_unreleased(text)[1])
    ca.load_fragments(ROOT / "changelog.d")


# The CI check -------------------------------------------------------------------


def test_pr_with_one_fragment_passes() -> None:
    ca.check_pull_request(["src/x.py", "changelog.d/12-thing.md"], "body", [])


def test_pr_without_a_fragment_fails() -> None:
    with pytest.raises(ca.ChangelogError, match="add one fragment"):
        ca.check_pull_request(["src/x.py"], "body", [])


def test_pr_with_two_fragments_fails_even_when_exempt() -> None:
    with pytest.raises(ca.ChangelogError, match="adds one fragment"):
        ca.check_pull_request(
            ["changelog.d/1-a.md", "changelog.d/2-b.md"],
            "no-changelog: x",
            ["no-changelog"],
        )


def test_readme_and_other_dirs_do_not_count_as_fragments() -> None:
    with pytest.raises(ca.ChangelogError):
        ca.check_pull_request(
            ["changelog.d/README.md", "docs/changelog.d/1-a.md"], "", []
        )


def test_exemption_by_body_line_or_label() -> None:
    ca.check_pull_request([], "Fix.\n\nno-changelog: docs typo\n", [])
    ca.check_pull_request([], None, ["no-changelog"])
    ca.check_pull_request([], "a\r\nno-changelog: release\r\n", [])


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "no-changelog:",
        "no-changelog:   ",
        "This is not a no-changelog: case",
        "- no-changelog: bulleted",
        "NO-CHANGELOG: shouted",
        "no changelog: spaced",
    ],
)
def test_exemption_stays_narrow(body: str | None) -> None:
    with pytest.raises(ca.ChangelogError):
        ca.check_pull_request([], body, ["no-changelog-needed", "changelog"])


def git(root: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/usr/bin/git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def make_pr(tmp_path: Path, with_fragment: bool) -> tuple[Path, str]:
    """A shallow clone whose HEAD adds (or not) one fragment over its parent."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@example.com")
    git(origin, "config", "user.name", "t")
    write(origin, "a.txt", "base\n")
    write(origin / "changelog.d", "1-old.md", "- Old (#1).\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "base")
    if with_fragment:
        write(origin / "changelog.d", "5-new.md", "- New (#5).\n")
    write(origin, "b.txt", "pr\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "pr")
    sha = git(origin, "rev-parse", "HEAD")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth=1", f"file://{origin}", str(clone))
    return clone, sha


def run_check_pr(
    tmp_path: Path, clone: Path, sha: str, body: str
) -> subprocess.CompletedProcess[str]:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"body": body, "labels": []}}))
    env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_SHA": sha, "PATH": "/usr/bin:/bin"}
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), "--root", str(clone), "check-pr"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_check_pr_cli_passes_with_a_fragment_in_the_merge_commit(
    tmp_path: Path,
) -> None:
    clone, sha = make_pr(tmp_path, with_fragment=True)
    run = run_check_pr(tmp_path, clone, sha, "anything")
    assert run.returncode == 0, run.stderr
    assert "ok" in run.stdout


def test_check_pr_cli_fails_without_a_fragment_unless_exempt(tmp_path: Path) -> None:
    clone, sha = make_pr(tmp_path, with_fragment=False)
    bad = run_check_pr(tmp_path, clone, sha, "plain")
    assert bad.returncode == 1
    assert "add one fragment" in bad.stderr
    good = run_check_pr(tmp_path, clone, sha, "no-changelog: docs only")
    assert good.returncode == 0, good.stderr


def test_check_pr_skips_a_push_event(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"ref": "refs/heads/main"}))
    run = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), "--root", str(ROOT), "check-pr"],
        capture_output=True,
        text=True,
        check=False,
        env={"GITHUB_EVENT_PATH": str(event), "PATH": "/usr/bin:/bin"},
    )
    assert run.returncode == 0
    assert "skipped" in run.stdout
