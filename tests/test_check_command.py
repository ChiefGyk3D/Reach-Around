# SPDX-License-Identifier: Apache-2.0
"""``reach-around check``: plain-language validation that never echoes values."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.cli import main

TEMPLATE = Path(__file__).resolve().parents[1] / "examples" / "instance-repo"

# A value that must never reach the output. Each hostile config plants it in
# the value of the field that fails (and in a field that does not).
SENTINEL = "SENTINEL-4f2a9c"


def good() -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "links",
                "title": "Links",
                "blocks": [
                    {
                        "id": "home",
                        "title": "Home",
                        "url": "https://example.com/",
                    },
                    {
                        "id": "news",
                        "title": "News",
                        "url": "https://news.example.com/",
                    },
                ],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [
                    {"id": "extra", "title": "Extra", "url": "https://example.org/"}
                ],
            },
        ],
    }


def write(tmp_path: Path, data: Any, name: str = "site.yaml") -> Path:
    path = tmp_path / name
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(["check", *argv])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def check(
    capsys: pytest.CaptureFixture[str], path: Path, *extra: str
) -> tuple[int, str]:
    return run(capsys, "--config", str(path), *extra)


# --- the good case -----------------------------------------------------------


def test_a_valid_config_exits_zero_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = check(capsys, write(tmp_path, good()))
    assert code == 0
    assert out == "site.yaml is valid: 2 tabs and 3 blocks.\n"


def test_the_shipped_template_config_is_valid(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = check(capsys, TEMPLATE / "site.yaml")
    assert code == 0, out
    assert "is valid" in out


# --- the bad cases: one short sentence each, naming tab, block and field -----


def bad(mutate: Any) -> dict[str, Any]:
    data = good()
    mutate(data)
    return data


CASES: list[tuple[str, Any, list[str]]] = [
    (
        "url without https",
        lambda d: d["tabs"][0]["blocks"][1].update(url="http://news.example.com/"),
        [
            'Tab "Links", block "news", field "url": ',
            "the web address must start with https://",
            "Fix: ",
        ],
    ),
    (
        "missing title",
        lambda d: d["tabs"][1]["blocks"][0].pop("title"),
        ['Tab "More", block "extra", field "title": ', "is missing"],
    ),
    (
        "misspelled key",
        lambda d: d["tabs"][0]["blocks"][0].update(titel="x"),
        [
            'Tab "Links", block "home", field "(unknown setting #4)": ',
            "not a setting",
        ],
    ),
    (
        "bad block id",
        lambda d: d["tabs"][0]["blocks"][0].update(id="Bad Id"),
        ['Tab "Links", block 1, field "id": ', "lowercase letters"],
    ),
    (
        "duplicate id",
        lambda d: d["tabs"][1]["blocks"][0].update(id="home"),
        ['The id "home" is used more than once'],
    ),
    (
        "link to a missing tab",
        lambda d: d["tabs"][0]["blocks"][0].update(url="tab:nowhere"),
        ['Tab "Links", block "home", field "url": ', "tab that does not exist"],
    ),
    (
        "bad theme mode",
        lambda d: d.update(theme={"mode": "neon"}),
        ['Setting "theme.mode": ', "auto, light or dark"],
    ),
    (
        "tab title too long",
        lambda d: d["tabs"][1].update(title="x" * 201),
        ['field "title": ', "at most 200"],
    ),
    (
        "no tabs",
        lambda d: d.update(tabs=[]),
        ['Setting "tabs": ', "at least 1"],
    ),
    (
        "wrong version",
        lambda d: d.update(version=2),
        ['Setting "version": '],
    ),
    (
        "unknown time zone",
        lambda d: d.update(timezone="Mars/Olympus"),
        ['Setting "timezone": ', "time zone"],
    ),
]


@pytest.mark.parametrize(
    ("label", "mutate", "fragments"), CASES, ids=[c[0] for c in CASES]
)
def test_a_bad_config_exits_two_with_a_plain_sentence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    label: str,
    mutate: Any,
    fragments: list[str],
) -> None:
    code, out = check(capsys, write(tmp_path, bad(mutate)))
    assert code == 2, out
    assert out.splitlines()[0].startswith("site.yaml has ")
    for fragment in fragments:
        assert fragment in out, (label, fragment, out)
    assert "Traceback" not in out
    assert "pydantic" not in out.lower()
    assert "Input should" not in out


def test_every_sentence_names_a_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for label, mutate, _ in CASES:
        _, out = check(capsys, write(tmp_path, bad(mutate)))
        problems = [ln for ln in out.splitlines() if ln[:1].isdigit()]
        assert problems, label
        for line in problems:
            assert " Fix: " in line, (label, line)


def test_several_problems_are_all_listed_in_a_stable_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = good()
    data["tabs"][0]["blocks"][0]["url"] = "ftp://example.com/"
    data["tabs"][1]["blocks"][0].pop("title")
    data["theme"] = {"mode": "neon"}
    path = write(tmp_path, data)
    code, first = check(capsys, path)
    _, second = check(capsys, path)
    assert code == 2
    assert first == second
    assert first.splitlines()[0] == "site.yaml has 3 problems."
    numbered = [ln for ln in first.splitlines() if ln[:1].isdigit()]
    assert len(numbered) == 3
    assert numbered[0].startswith("1. ")


def test_the_json_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = good()
    data["tabs"][0]["blocks"][1]["url"] = "http://news.example.com/"
    code, out = check(capsys, write(tmp_path, data), "--format", "json")
    assert code == 2
    report = json.loads(out)
    assert report["valid"] is False
    assert report["errors"] == [
        {
            "tab": "Links",
            "block": "news",
            "field": "url",
            "kind": "url_scheme",
            "message": "the web address must start with https://",
            "fix": "Write the full address, for example https://example.com/page.",
        }
    ]
    assert report["error_count"] == 1
    code, out = check(capsys, write(tmp_path, good(), "ok.yaml"), "--format", "json")
    assert code == 0
    assert json.loads(out) == {
        "valid": True,
        "file": "ok.yaml",
        "tabs": 2,
        "blocks": 3,
        "errors": [],
        "error_count": 0,
    }


def test_a_missing_file_exits_two_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = check(capsys, tmp_path / "nope.yaml")
    assert code == 2
    assert "could not be read" in out
    assert "Traceback" not in out


def test_an_unknown_format_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        run(capsys, "--config", str(write(tmp_path, good())), "--format", "xml")
    assert exc.value.code == 2


# --- hostile files: one typed, plain message each, nothing echoed ------------

HOSTILE_TEXTS: dict[str, str] = {
    "anchor": f"version: 1\nx: &a {SENTINEL}\ny: *a\n",
    "merge key": f"version: 1\nbase: {{a: {SENTINEL}}}\n<<: {{b: 1}}\n",
    "duplicate key": f"version: 1\nversion: {SENTINEL}\n",
    "python tag": f"version: !!python/object/apply:os.system ['{SENTINEL}']\n",
    "syntax error": f"version: 1\ncreator: [{SENTINEL}\n  : :\n",
    "not a mapping": f"- {SENTINEL}\n- b\n",
    "empty": "",
    "scalar": SENTINEL + "\n",
    "deep nesting": "a: " + "[" * 200 + SENTINEL + "]" * 200 + "\n",
    "two documents": f"version: 1\n---\nversion: {SENTINEL}\n",
}


@pytest.mark.parametrize("label", sorted(HOSTILE_TEXTS))
def test_hostile_files_are_refused_plainly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    code, out = check(capsys, write(tmp_path, HOSTILE_TEXTS[label]))
    assert code == 2, out
    assert SENTINEL not in out
    assert "Traceback" not in out
    assert out.startswith("site.yaml has 1 problem.")


def test_binary_and_oversized_and_symlinked_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "bin.yaml"
    binary.write_bytes(b"\xff\xfe" + SENTINEL.encode())
    big = tmp_path / "big.yaml"
    big.write_text("a: " + "x" * 2_000_000 + SENTINEL + "\n", encoding="utf-8")
    real = write(tmp_path, good(), "real.yaml")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    for path in (binary, big, link):
        code, out = check(capsys, path)
        assert code == 2, path
        assert SENTINEL not in out
        assert "Traceback" not in out


def test_a_directory_and_a_fifo_do_not_hang_or_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import os

    fifo = tmp_path / "pipe.yaml"
    os.mkfifo(fifo)
    for path in (tmp_path, fifo):
        code, out = check(capsys, path)
        assert code == 2
        assert "Traceback" not in out


# --- no value beyond the failing key appears ---------------------------------


def hostile_variants() -> dict[str, dict[str, Any]]:
    """Bad configs whose failing values (and neighbours) carry the sentinel."""
    out: dict[str, dict[str, Any]] = {}

    def add(label: str, mutate: Any) -> None:
        data = good()
        mutate(data)
        out[label] = data

    add("url", lambda d: d["tabs"][0]["blocks"][0].update(url=f"http://{SENTINEL}"))
    add(
        "url with password",
        lambda d: d["tabs"][0]["blocks"][0].update(
            url=f"https://user:{SENTINEL}@example.com/"
        ),
    )
    add(
        "url bad port",
        lambda d: d["tabs"][0]["blocks"][0].update(
            url=f"https://example.com:{SENTINEL}/"
        ),
    )
    add(
        "token field",
        lambda d: d["tabs"][0]["blocks"][0].update(api_token=SENTINEL),
    )
    add(
        "secret-named store field",
        lambda d: d.update(store={"kind": "sqlite", "password": SENTINEL}),
    )
    add("dsn", lambda d: d.update(store={"dsn_env": f"lower {SENTINEL}"}))
    add("mode", lambda d: d.update(theme={"mode": SENTINEL}))
    add("color", lambda d: d.update(theme={"colors": {SENTINEL: "#fff"}}))
    add("color value", lambda d: d.update(theme={"colors": {"accent": SENTINEL}}))
    add("font", lambda d: d.update(theme={"fonts": {"body": f"{SENTINEL}; x"}}))
    add("timezone", lambda d: d.update(timezone=SENTINEL))
    add("version", lambda d: d.update(version=SENTINEL))
    add("tab ref", lambda d: d["tabs"][0]["blocks"][0].update(url=f"tab:{SENTINEL}"))
    add(
        "tab ref in socials",
        lambda d: d["creator"].update(
            socials=[{"name": "n", "url": f"tab:{SENTINEL}", "icon": "video"}]
        ),
    )
    add("dup id", lambda d: d["tabs"][1]["blocks"][0].update(id="home"))
    add("block id", lambda d: d["tabs"][0]["blocks"][0].update(id=SENTINEL))
    # The tab title is printed on purpose (it says where the problem is), so
    # the sentinel goes in the other fields of the tab.
    add("tab layout", lambda d: d["tabs"][0].update(layout=SENTINEL))
    add(
        "date",
        lambda d: d["tabs"][0]["blocks"][0].update(show_from=f"{SENTINEL}"),
    )
    add(
        "image",
        lambda d: d["tabs"][0]["blocks"][0].update(image=f"../{SENTINEL}.png"),
    )
    add(
        "email",
        lambda d: d["tabs"][0]["blocks"].append(
            {"id": "mail", "address": f"{SENTINEL}", "title": "Mail"}
        ),
    )
    add(
        "crypto",
        lambda d: d["tabs"][0]["blocks"].append(
            {"id": "coin", "coins": [{"coin": "btc", "address": SENTINEL + "!!"}]}
        ),
    )
    add("not a block", lambda d: d["tabs"][0]["blocks"].append(SENTINEL))
    add("tab not a map", lambda d: d["tabs"].append(SENTINEL))
    add("tabs not a list", lambda d: d.update(tabs=SENTINEL))
    add("creator", lambda d: d.update(creator=SENTINEL))
    add("unknown nested", lambda d: d["creator"].update({SENTINEL: SENTINEL}))
    add(
        "footer",
        lambda d: d.update(footer={"credit": SENTINEL, "text": "‮" + SENTINEL}),
    )
    add("robots", lambda d: d.update(robots={"noindex_paths": [SENTINEL]}))
    add("title template", lambda d: d.update(title_template="{" + SENTINEL + "}"))
    add("long title", lambda d: d["tabs"][0]["blocks"][0].update(title=SENTINEL * 80))
    return out


HOSTILE = hostile_variants()


@pytest.mark.parametrize("label", sorted(HOSTILE))
def test_no_config_value_beyond_the_failing_key_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    path = write(tmp_path, HOSTILE[label])
    for fmt in ("plain", "json"):
        code, out = check(capsys, path, "--format", fmt)
        assert code == 2, (label, out)
        assert SENTINEL not in out, (label, fmt, out)
        assert "Traceback" not in out


def test_the_sentinel_battery_is_collected_and_strict() -> None:
    # Collected loosely, asserted strictly: the battery must exist.
    assert len(HOSTILE) >= 30


def test_an_unknown_setting_is_never_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only schema names are printed; an unknown key is a position."""
    data = good()
    block = data["tabs"][0]["blocks"][0]
    block["api_token"] = SENTINEL
    block["titel"] = "x"
    block["ghp_" + "A1b2C3d4" * 5] = "y"
    for fmt in ("plain", "json"):
        code, out = check(capsys, write(tmp_path, data), "--format", fmt)
        assert code == 2
        for leaked in ("api_token", "titel", "ghp_", SENTINEL):
            assert leaked not in out, (fmt, leaked)
    _, plain = check(capsys, write(tmp_path, data))
    assert plain.count("(unknown setting #") == 3
    assert "#4" in plain and "#5" in plain and "#6" in plain


def test_an_unknown_nested_key_is_a_position_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = good()
    data["theme"] = {"colors": {"accent": "#fff"}, "mood": "x"}
    _, out = check(capsys, write(tmp_path, data))
    assert 'Setting "theme.(unknown setting #2)"' in out
    assert "mood" not in out


def test_a_schema_name_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from reach_around.commands.check import known_fields

    assert {"url", "title", "mode", "blocks", "tabs", "colors"} <= known_fields()
    assert "api_token" not in known_fields()
    data = good()
    data["tabs"][0]["blocks"][0]["url"] = "http://x"
    _, out = check(capsys, write(tmp_path, data))
    assert 'field "url"' in out


def test_a_hostile_title_cannot_inject_markup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = good()
    data["tabs"][0]["title"] = '`x` [click](http://evil.example) <b>@octocat</b> "q"'
    data["tabs"][0]["blocks"][0]["url"] = "http://x"
    code, out = check(capsys, write(tmp_path, data))
    assert code == 2
    line = next(ln for ln in out.splitlines() if ln.startswith("1. "))
    where = line.split(": ", 1)[0]
    for char in "`[]<>@|\\*_#~":
        assert char not in where, (char, where)
    assert where.count('"') == 6  # tab, block, field: two marks each
    assert "](" not in out
    assert "<" not in out
    assert "@octocat" not in out


def test_a_hostile_block_id_is_replaced_by_its_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = good()
    data["tabs"][0]["blocks"][1]["id"] = "Bad`Id\n- injected"
    code, out = check(capsys, write(tmp_path, data))
    assert code == 2
    assert "block 2" in out
    assert "injected" not in out


def test_a_very_long_tab_title_is_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = good()
    data["tabs"][0]["title"] = "T" * 150
    data["tabs"][0]["blocks"][0]["url"] = "http://x"
    _, out = check(capsys, write(tmp_path, data))
    assert "T" * 41 not in out


# --- --against: which blocks changed -----------------------------------------


def test_against_lists_added_changed_and_removed_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = good()
    head = copy.deepcopy(base)
    head["tabs"][0]["blocks"][0]["title"] = f"Changed {SENTINEL}"
    head["tabs"][0]["blocks"].append(
        {"id": "added", "title": "A", "url": "https://a.example.com/"}
    )
    del head["tabs"][1]["blocks"][0]
    head["theme"] = {"mode": "dark"}
    base_path = write(tmp_path, base, "base.yaml")
    head_path = write(tmp_path, head, "site.yaml")
    code, out = check(capsys, head_path, "--against", str(base_path))
    assert code == 0, out
    assert out.splitlines() == [
        "site.yaml is valid: 2 tabs and 3 blocks.",
        "",
        "Changed since the base:",
        '- Tab "Links": block "home" changed.',
        '- Tab "Links": block "added" was added.',
        '- Tab "More": block "extra" was removed.',
        "- Site settings changed: theme.",
    ]
    assert SENTINEL not in out
    _, as_json = check(
        capsys, head_path, "--against", str(base_path), "--format", "json"
    )
    assert json.loads(as_json)["changes"][0] == 'Tab "Links": block "home" changed.'


def test_against_with_no_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, good())
    _, out = check(capsys, path, "--against", str(path))
    assert out.splitlines()[-1] == "No blocks or settings changed."


def test_against_still_lists_changes_when_the_new_file_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    head = good()
    head["tabs"][0]["blocks"][0]["url"] = "http://x"
    code, out = check(
        capsys,
        write(tmp_path, head, "site.yaml"),
        "--against",
        str(write(tmp_path, good(), "base.yaml")),
    )
    assert code == 2
    assert 'block "home" changed.' in out


def test_against_an_unreadable_base_is_a_note_not_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = check(
        capsys, write(tmp_path, good()), "--against", str(tmp_path / "missing.yaml")
    )
    assert code == 0
    assert "Could not compare with the base" in out


def test_against_a_hostile_base_prints_nothing_from_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = good()
    base["tabs"][0]["title"] = "`x` [y](http://evil.example)"
    base["tabs"][0]["blocks"][0]["id"] = "x" * 20 + "`"
    code, out = check(
        capsys,
        write(tmp_path, good()),
        "--against",
        str(write(tmp_path, base, "base.yaml")),
    )
    assert code == 0
    assert "`" not in out
    assert "](" not in out
