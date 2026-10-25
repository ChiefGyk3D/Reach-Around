# SPDX-License-Identifier: Apache-2.0
"""The safe edit commands: add-link, remove-link, move-link, set."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from reach_around.cli import main
from reach_around.config import Link, Section, load_site_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
MINIMAL = ROOT / "examples" / "instance-variants" / "minimal" / "site.yaml"

COMMENTED = """\
# Synthetic config with comments in awkward places.
version: 1   # schema version
creator:
  name: Example Creator   # shown in the header
  # title: Example Creator Links
tabs:
  - id: links
    title: Links
    blocks:
      # the first link
      - id: first
        title: First
        url: https://example.com/1   # keep me
      # a section
      - id: group
        title: Group
        links:
          # grouped one
          - id: one
            title: One
            url: https://example.com/one
          - id: two
            title: Two
            url: https://example.com/two

      # the last link
      - id: last
        title: Last
        url: https://example.com/last
  # the second tab
  - id: more
    title: More
    blocks:
      - id: only
        title: Only
        url: https://example.com/only
# trailing comment
"""

ADD = ["--title", "T", "--url", "https://e.com/"]


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    path.write_text(EXAMPLE.read_text(), encoding="utf-8")
    return path


@pytest.fixture
def commented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    path.write_text(COMMENTED, encoding="utf-8")
    return path


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(any(line == other for other in it) for line in small)


def run(*argv: str) -> int:
    return main(list(argv))


def ids(path: Path, tab: str) -> list[str]:
    (found,) = [t for t in load_site_config(path).tabs if t.id == tab]
    return [b.id for b in found.blocks]


def link_at(path: Path, tab: int, index: int) -> Link:
    block = load_site_config(path).tabs[tab].blocks[index]
    assert isinstance(block, Link)
    return block


# ---- add-link ----


def test_add_link_appends_and_preserves_every_other_byte(
    cfg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = lines(cfg)
    code = run(
        "add-link", "--config", str(cfg), "--tab", "events",
        "--title", "Summer Show", "--url", "https://tickets.example.com/s",
        "--subtitle", "Bring friends", "--icon", "link", "--affiliate",
    )  # fmt: skip
    assert code == 0
    after = lines(cfg)
    assert is_subsequence(before, after)
    assert len(after) == len(before) + 6
    assert ids(cfg, "events") == ["spring-meetup", "summer-show"]
    link = link_at(cfg, 1, 1)
    assert link.title == "Summer Show"
    assert link.affiliate is True
    assert "+      - id: summer-show" in capsys.readouterr().out
    assert (cfg.parent / "site.yaml.bak").read_bytes() == EXAMPLE.read_bytes()


def test_add_link_at_a_position(cfg: Path) -> None:
    before = lines(cfg)
    code = run(
        "add-link", "--config", str(cfg), "--tab", "start-here",
        "--title", "Front", "--url", "https://example.com/f", "--position", "1",
    )  # fmt: skip
    assert code == 0
    assert ids(cfg, "start-here") == ["front", "home-page", "gear-picks"]
    assert is_subsequence(before, lines(cfg))


def test_add_link_keeps_comments_attached_to_the_next_item(commented: Path) -> None:
    code = run(
        "add-link", "--config", str(commented), "--tab", "links",
        "--title", "Mid", "--url", "https://example.com/m", "--position", "2",
    )  # fmt: skip
    assert code == 0
    text = commented.read_text()
    assert text.index("# keep me") < text.index("id: mid") < text.index("# a section")
    assert is_subsequence(COMMENTED.splitlines(keepends=True), lines(commented))


def test_add_link_generates_unique_ids(cfg: Path) -> None:
    for _ in range(2):
        code = run(
            "add-link", "--config", str(cfg), "--tab", "events",
            "--title", "Home Page!", "--url", "https://example.com/x",
        )  # fmt: skip
        assert code == 0
    assert ids(cfg, "events")[1:] == ["home-page-2", "home-page-3"]


def test_add_link_duplicate_explicit_id_is_refused(
    cfg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["add-link", "--config", str(cfg), "--tab", "events", *ADD, "--id", "mine"]
    assert run(*args) == 0
    original = cfg.read_bytes()
    assert run(*args) == 1
    assert "duplicate id" in capsys.readouterr().err
    assert cfg.read_bytes() == original


@pytest.mark.parametrize(
    "extra",
    [
        ["--url", "http://insecure.example.com/"],
        ["--id", "Bad Id"],
        ["--title", "x\ny"],
        ["--position", "9"],
        ["--position", "0"],
        ["--tab", "nope"],
        ["--icon", "Not A Slug"],
    ],
)
def test_add_link_invalid_input_changes_nothing(
    cfg: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    before = cfg.read_bytes()
    argv = ["add-link", "--config", str(cfg), "--tab", "events", *ADD, *extra]
    assert run(*argv) == 1
    assert cfg.read_bytes() == before
    assert not (cfg.parent / "site.yaml.bak").exists()
    err = capsys.readouterr().err
    assert err.startswith("error:") and "Traceback" not in err


def test_add_link_quotes_values_that_need_it(cfg: Path) -> None:
    code = run(
        "add-link", "--config", str(cfg), "--tab", "events",
        "--title", "yes", "--url", "https://e.com/?a=b&c=d",
        "--subtitle", "Cost: 5 # dollars", "--id", "no",
    )  # fmt: skip
    assert code == 0
    link = link_at(cfg, 1, 1)
    assert (link.id, link.title, link.subtitle) == ("no", "yes", "Cost: 5 # dollars")
    assert link.url == "https://e.com/?a=b&c=d"


# ---- remove-link ----


def test_remove_link_removes_exactly_its_lines(commented: Path) -> None:
    before = lines(commented)
    assert run("remove-link", "--config", str(commented), "first") == 0
    after = lines(commented)
    assert before[9:13] == [
        "      # the first link\n",
        "      - id: first\n",
        "        title: First\n",
        "        url: https://example.com/1   # keep me\n",
    ]
    assert after == before[:9] + before[13:]
    assert ids(commented, "links") == ["group", "last"]


def test_remove_link_inside_a_section(commented: Path) -> None:
    assert run("remove-link", "--config", str(commented), "one") == 0
    text = commented.read_text()
    assert "# grouped one" not in text and "id: one" not in text
    assert "id: two" in text and "# trailing comment" in text


def test_remove_last_item_keeps_following_comments(commented: Path) -> None:
    assert run("remove-link", "--config", str(commented), "last") == 0
    text = commented.read_text()
    assert "# the second tab" in text and "# trailing comment" in text
    assert "id: last" not in text and "# the last link" not in text


@pytest.mark.parametrize("target", ["group", "links", "nope"])
def test_remove_link_refuses_non_links(
    commented: Path, target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("remove-link", "--config", str(commented), target) == 1
    assert commented.read_text() == COMMENTED
    assert "error:" in capsys.readouterr().err


def test_remove_the_only_block_of_a_tab_is_refused(
    commented: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("remove-link", "--config", str(commented), "only") == 1
    assert commented.read_text() == COMMENTED
    assert "error:" in capsys.readouterr().err


# ---- move-link ----


def test_move_link_to_the_front_and_back(commented: Path) -> None:
    code = run("move-link", "--config", str(commented), "last", "--position", "1")
    assert code == 0
    assert ids(commented, "links") == ["last", "first", "group"]
    text = commented.read_text()
    assert text.index("# the last link") < text.index("id: last")
    assert text.index("id: last") < text.index("# the first link")
    code = run("move-link", "--config", str(commented), "last", "--position", "3")
    assert code == 0
    assert ids(commented, "links") == ["first", "group", "last"]
    assert sorted(lines(commented)) == sorted(COMMENTED.splitlines(keepends=True))


def test_move_link_inside_a_section(commented: Path) -> None:
    code = run("move-link", "--config", str(commented), "two", "--position", "1")
    assert code == 0
    group = load_site_config(commented).tabs[0].blocks[1]
    assert isinstance(group, Section)
    assert [x.id for x in group.links] == ["two", "one"]
    assert sorted(lines(commented)) == sorted(COMMENTED.splitlines(keepends=True))


@pytest.mark.parametrize(
    ("target", "pos"), [("first", "9"), ("first", "0"), ("nope", "1")]
)
def test_move_link_bad_input_changes_nothing(
    commented: Path, target: str, pos: str
) -> None:
    argv = ["move-link", "--config", str(commented), target, "--position", pos]
    assert run(*argv) == 1
    assert commented.read_text() == COMMENTED


# ---- set ----


def test_set_replaces_a_value_and_keeps_the_trailing_comment(commented: Path) -> None:
    assert run("set", "--config", str(commented), "creator.name", "New Name") == 0
    expected = COMMENTED.replace("name: Example Creator   #", "name: New Name   #")
    assert commented.read_text() == expected


def test_set_adds_missing_keys_and_parents(cfg: Path) -> None:
    before = lines(cfg)
    for key, value in [
        ("theme.colors.accent", "#ff69b4"),
        ("theme.mode", "dark"),
        ("theme.background_dim", "0.4"),
        ("creator.title", "My Links"),
    ]:
        assert run("set", "--config", str(cfg), key, value) == 0
    config = load_site_config(cfg)
    assert config.theme.colors == {"accent": "#ff69b4"}
    assert (config.theme.mode, config.theme.background_dim) == ("dark", 0.4)
    assert config.creator.title == "My Links"
    assert is_subsequence(before, lines(cfg))


def test_set_creates_a_missing_top_level_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    text = MINIMAL.read_text()
    path.write_text(text, encoding="utf-8")
    assert run("set", "--config", str(path), "theme.colors.bg", "#101418") == 0
    assert load_site_config(path).theme.colors == {"bg": "#101418"}
    assert is_subsequence(text.splitlines(keepends=True), lines(path))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("theme.mode", "purple"),
        ("theme.background_dim", "5"),
        ("theme.background_dim", "nan"),
        ("theme.background_dim", "abc"),
        ("theme.colors.accent", "red; x"),
        ("theme.colors.nope", "#fff"),
        ("creator.name", ""),
        ("creator.name", "a\nb"),
        ("creator.favicon", "x.png"),
        ("tabs", "x"),
        ("version", "2"),
        ("theme", "x"),
    ],
)
def test_set_refuses_bad_keys_and_values(
    cfg: Path, key: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    before = cfg.read_bytes()
    assert run("set", "--config", str(cfg), key, value) == 1
    assert cfg.read_bytes() == before
    assert capsys.readouterr().err.startswith("error:")


def test_set_refuses_a_flow_style_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    text = COMMENTED.replace("tabs:", "theme: {mode: auto}\ntabs:")
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    assert run("set", "--config", str(path), "theme.mode", "dark") == 1
    assert path.read_bytes() == before
    assert "flow" in capsys.readouterr().err


# ---- shared behavior ----


def test_dry_run_changes_nothing_but_prints_a_diff(
    cfg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = cfg.read_bytes()
    assert run("set", "--config", str(cfg), "theme.mode", "dark", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "+  mode: dark" in out and "dry run" in out
    others = [
        ["remove-link", "home-page"],
        ["move-link", "example-stand", "--position", "1"],
        ["add-link", "--tab", "events", *ADD],
    ]
    for argv in others:
        assert run(argv[0], "--config", str(cfg), *argv[1:], "--dry-run") == 0
        shown = capsys.readouterr().out
        assert "dry run" in shown and "@@" in shown
    assert cfg.read_bytes() == before
    assert sorted(p.name for p in cfg.parent.iterdir()) == ["site.yaml"]


def test_no_backup_flag(cfg: Path) -> None:
    assert run("set", "--config", str(cfg), "theme.mode", "dark", "--no-backup") == 0
    assert sorted(p.name for p in cfg.parent.iterdir()) == ["site.yaml"]


def test_atomic_write_failure_leaves_the_original(
    cfg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = cfg.read_bytes()

    def boom(src: object, dst: object) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    assert run("set", "--config", str(cfg), "theme.mode", "dark") == 1
    assert cfg.read_bytes() == before
    left = {p.name for p in cfg.parent.iterdir()}
    assert left <= {"site.yaml", "site.yaml.bak"}
    assert "disk went away" in capsys.readouterr().err


def test_symlinked_config_is_refused(
    cfg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    link = cfg.parent / "link.yaml"
    link.symlink_to(cfg)
    before = cfg.read_bytes()
    assert run("set", "--config", str(link), "theme.mode", "dark") == 1
    assert cfg.read_bytes() == before and link.is_symlink()
    assert "symlink" in capsys.readouterr().err


def test_path_outside_the_working_tree_needs_force_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(EXAMPLE.read_text(), encoding="utf-8")
    monkeypatch.chdir(work)
    before = elsewhere.read_bytes()
    argv = ["set", "--config", str(elsewhere), "theme.mode", "dark"]
    assert run(*argv) == 1
    assert elsewhere.read_bytes() == before
    assert "working tree" in capsys.readouterr().err
    assert run(*argv, "--force-path") == 0
    assert load_site_config(elsewhere).theme.mode == "dark"


def test_force_path_follows_a_symlink_to_the_real_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    real = tmp_path / "real.yaml"
    real.write_text(EXAMPLE.read_text(), encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    argv = ["set", "--config", str(link), "theme.mode", "dark", "--force-path"]
    assert run(*argv) == 0
    assert link.is_symlink() and load_site_config(real).theme.mode == "dark"


Mutation = Callable[[str], str]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("\n", "\r\n"),
        lambda t: t.replace("  - id: events", "  - &a id: events", 1),
        lambda t: "version: 1\n" + t,
    ],
    ids=["crlf", "anchor", "duplicate-key"],
)
def test_unsupported_or_hostile_files_are_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Mutation,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    path.write_bytes(mutate(EXAMPLE.read_text()).encode())
    before = path.read_bytes()
    code = run("add-link", "--config", str(path), "--tab", "events", *ADD)
    assert code == 1
    assert path.read_bytes() == before
    assert "Traceback" not in capsys.readouterr().err


def test_flow_style_blocks_are_refused_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\ncreator:\n  name: X\ntabs:\n  - id: t\n    title: T\n"
        "    blocks: [{id: a, title: A, url: 'https://e.com/'}]\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    assert run("remove-link", "--config", str(path), "a") == 1
    assert path.read_bytes() == before
    assert "flow" in capsys.readouterr().err
