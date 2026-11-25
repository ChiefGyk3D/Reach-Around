# SPDX-License-Identifier: Apache-2.0
"""scripts/check-wiki-scrub.py: publish-safety scrub of docs that get published.

Every fixture is synthetic. Each rule has one failing fixture; the revert proof
removes that rule from the registry and requires the same fixture to pass, so a
rule that is deleted turns its test red.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-wiki-scrub.py"

DASH = "—"
TERM = "zz-synthetic-term"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_wiki_scrub", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_wiki_scrub"] = module
    spec.loader.exec_module(module)
    return module


scrub = load()

# rule -> (page text, expected line number of the finding)
BAD: dict[str, tuple[str, int]] = {
    "em-dash": (f"# Page\n\nA phrase {DASH} with a dash.\n", 3),
    "forbidden-term": (f"# Page\n\nWorks at {TERM.upper()} Inc.\n", 3),
    "radio-identifier": ("# Page\n\nCall ZZ9ZZ on grid AA00aa.\n", 3),
    "private-host": ("# Page\n\nReach the box at 192.168.4.20 today.\n", 3),
    "secret-shape": ("# Page\n\nkey: " + "gh" + "p_" + "a" * 36 + "\n", 3),
    "session-url": ("# Page\n\nSee https://claude" + ".ai/code/session_x\n", 3),
    "broken-link": ("# Page\n\nSee [missing](./no-such-page.md).\n", 3),
    "image-alt": ("# Page\n\n![](diagram.svg)\n", 3),
    "mermaid-text": ("# Page\n\n```mermaid\ngraph TD\n  A-->B\n```\n\n## Next\n", 3),
    "light-mode": ("# Page\n\n<style>body { background: #fff; }</style>\n", 3),
}

GOOD = """# Page

Plain prose with N0CALL, N0TST and FN31pr as placeholders.
Host links.example.com and 203.0.113.9 are documentation values.
See [the other page](other.md#part) and [[other]] and https://example.com/x.

![A labeled diagram of the flow](diagram.svg)

```mermaid
graph TD
  A-->B
```

Text description: A sends data to B, and B stores it.
"""


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    directory = tmp_path / "docs" / "wiki"
    directory.mkdir(parents=True)
    (directory / "other.md").write_text("# Other\n", encoding="utf-8")
    (directory / "diagram.svg").write_text("<svg></svg>\n", encoding="utf-8")
    return directory


def scan(
    root: Path,
    wiki: Path,
    rules: dict[str, Callable[[Any], Iterator[int]]] | None = None,
) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = scrub.check(root, [wiki], [TERM], rules)
    return found


def test_good_page_is_clean(wiki: Path) -> None:
    (wiki / "page.md").write_text(GOOD, encoding="utf-8")
    assert scan(wiki.parents[1], wiki) == []


@pytest.mark.parametrize("rule", sorted(BAD))
def test_rule_flags_its_fixture(rule: str, wiki: Path) -> None:
    text, line = BAD[rule]
    (wiki / "page.md").write_text(text, encoding="utf-8")
    found = scan(wiki.parents[1], wiki)
    assert ("docs/wiki/page.md", line, rule) in found
    assert {name for _, _, name in found} == {rule}


@pytest.mark.parametrize("rule", sorted(BAD))
def test_removing_the_rule_turns_its_fixture_green(rule: str, wiki: Path) -> None:
    text, _ = BAD[rule]
    (wiki / "page.md").write_text(text, encoding="utf-8")
    without = {k: v for k, v in scrub.RULES.items() if k != rule}
    assert scan(wiki.parents[1], wiki, without) == []


def test_every_rule_has_a_fixture() -> None:
    assert set(BAD) == set(scrub.RULES)


def test_private_variants(wiki: Path) -> None:
    body = "a.lan\nsee 10.1.2.3\n100.64.0.5\n172.20.0.1\nin ~/.config/x\nok 8.8.8.8\n"
    (wiki / "page.md").write_text(body, encoding="utf-8")
    lines = [n for _, n, r in scan(wiki.parents[1], wiki) if r == "private-host"]
    assert lines == [1, 2, 3, 4, 5]


def test_code_fences_hide_links_but_not_secrets(wiki: Path) -> None:
    key = "gh" + "p_" + "b" * 36
    body = f"```\n[x](./gone.md)\n{key}\n```\n"
    (wiki / "page.md").write_text(body, encoding="utf-8")
    assert [(n, r) for _, n, r in scan(wiki.parents[1], wiki)] == [(3, "secret-shape")]


def test_html_image_without_alt(wiki: Path) -> None:
    (wiki / "page.md").write_text(
        '<img src="diagram.svg">\n<img src="diagram.svg" alt="ok">\n'
    )
    assert [(n, r) for _, n, r in scan(wiki.parents[1], wiki)] == [(1, "image-alt")]


def run(
    tmp_path: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), "--root", str(tmp_path), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        check=False,
        env=env,
    )


def test_cli_prints_file_and_line_never_the_value(tmp_path: Path, wiki: Path) -> None:
    key = "gh" + "p_" + "c" * 36
    (wiki / "page.md").write_text(f"# Page\n\n{key}\nAbout {TERM}.\n", encoding="utf-8")
    forbidden = tmp_path / "forbidden.txt"
    forbidden.write_text(f"# comment\n{TERM}\n", encoding="utf-8")
    done = run(tmp_path, "--forbidden-file", str(forbidden))
    assert done.returncode == 1
    assert "docs/wiki/page.md:3: secret-shape" in done.stdout
    assert "docs/wiki/page.md:4: forbidden-term" in done.stdout
    assert key not in done.stdout + done.stderr
    assert TERM not in done.stdout + done.stderr


def test_forbidden_terms_from_environment(tmp_path: Path, wiki: Path) -> None:
    (wiki / "page.md").write_text(f"About {TERM}.\n", encoding="utf-8")
    env = {"WIKI_SCRUB_FORBIDDEN": f"other-term, {TERM}", "PATH": ""}
    done = run(tmp_path, env=env)
    assert done.returncode == 1
    assert "docs/wiki/page.md:1: forbidden-term" in done.stdout


def test_cli_clean_and_missing_directory(tmp_path: Path, wiki: Path) -> None:
    (wiki / "page.md").write_text(GOOD, encoding="utf-8")
    assert run(tmp_path).returncode == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run(empty, "--root", str(empty))
    assert done.returncode == 0
    assert "nothing to scan" in done.stdout


def test_require_forbidden_fails_when_unconfigured(tmp_path: Path, wiki: Path) -> None:
    (wiki / "page.md").write_text(GOOD, encoding="utf-8")
    done = run(tmp_path, "--require-forbidden", env={"PATH": ""})
    assert done.returncode == 1
