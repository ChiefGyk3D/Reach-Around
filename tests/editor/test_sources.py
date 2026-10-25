# SPDX-License-Identifier: Apache-2.0
"""Source grep for the editor front end: no dangerous sinks, no remote code."""

from __future__ import annotations

import re
from importlib import resources

import pytest

# Collect loosely (every file in the folder), assert strictly (per rule below).
RULES: dict[str, re.Pattern[str]] = {
    "innerHTML-family": re.compile(
        r"\b(innerHTML|outerHTML|insertAdjacentHTML|srcdoc)\b", re.I
    ),
    "document.write": re.compile(r"document\s*\.\s*write(ln)?\b"),
    "eval": re.compile(r"\beval\s*\(|\bnew\s+Function\b|\bFunction\s*\("),
    "string timer": re.compile(r"\bset(Timeout|Interval)\s*\(\s*[\"'`]"),
    "inline handler attribute": re.compile(r"""[\s"'/]on[a-z]{3,}\s*=""", re.I),
    "handler property": re.compile(r"\.on[a-z]{3,}\s*="),
    "remote URL": re.compile(r"(https?:)?//[A-Za-z0-9\[]", re.I),
    "javascript: URL": re.compile(r"javascript\s*:", re.I),
    "import from URL": re.compile(
        r"""\bimport\b[^;\n]*?["'](https?:|//|data:|blob:)""", re.I
    ),
    "inline style attribute": re.compile(r"""\sstyle\s*=""", re.I),
    "storage": re.compile(r"\b(localStorage|sessionStorage|document\s*\.\s*cookie)\b"),
}


def scan(text: str) -> list[str]:
    return sorted(name for name, pattern in RULES.items() if pattern.search(text))


def editor_sources() -> dict[str, str]:
    root = resources.files("reach_around").joinpath("editor_assets")
    return {
        item.name: item.read_text(encoding="utf-8")
        for item in root.iterdir()
        if item.is_file() and item.name.endswith((".html", ".js", ".css", ".svg"))
    }


SHELL_FILES = {
    "index.html",
    "app.css",
    "app.js",
    "strings.js",
    "token.js",
    "api.js",
    "store.js",
    "announce.js",
    "view.js",
}
BUDGET_BYTES = 150 * 1024
IMPORT_SPECIFIER = re.compile(
    r"""(?:\bimport\b[^;()]*?\bfrom\s*|\bimport\s*\(?\s*|\bexport\b[^;()]*?\bfrom\s*)"""
    r"""["']([^"']*)["']"""
)


def module_targets(text: str) -> list[str]:
    return IMPORT_SPECIFIER.findall(text)


def test_sources_were_collected() -> None:
    names = set(editor_sources())
    assert SHELL_FILES <= names
    assert names == SHELL_FILES, "a new editor asset needs a line in SHELL_FILES"


def test_no_https_url_anywhere_in_the_assets() -> None:
    for name, text in editor_sources().items():
        assert "https:" not in text.lower(), name


def test_assets_fit_the_size_budget() -> None:
    root = resources.files("reach_around").joinpath("editor_assets")
    sizes = [item.read_bytes() for item in root.iterdir() if item.is_file()]
    assert len(sizes) >= len(SHELL_FILES)
    assert sum(len(blob) for blob in sizes) <= BUDGET_BYTES


def test_every_import_is_a_same_folder_module_that_exists() -> None:
    sources = editor_sources()
    seen = 0
    for name, text in sources.items():
        if not name.endswith(".js"):
            continue
        for target in module_targets(text):
            seen += 1
            assert re.fullmatch(r"\./[a-z]+\.js", target), (name, target)
            assert target[2:] in sources, (name, target)
    assert seen >= 6


def test_the_entry_module_is_the_only_script_in_the_shell() -> None:
    html = editor_sources()["index.html"]
    scripts = re.findall(r"<script\b[^>]*>", html)
    assert scripts == ['<script type="module" src="/__edit/app.js">']


def test_every_js_module_is_reachable_from_the_entry() -> None:
    sources = editor_sources()
    reached = {"app.js"}
    queue = ["app.js"]
    while queue:
        for target in module_targets(sources[queue.pop()]):
            if target[2:] not in reached:
                reached.add(target[2:])
                queue.append(target[2:])
    assert reached == {n for n in sources if n.endswith(".js")}


@pytest.mark.parametrize(
    "target",
    [
        "https://cdn.example/x.js",
        "//cdn.example/x.js",
        "/__edit/x.js",
        "../x.js",
        "./sub/x.js",
        "data:text/javascript,1",
        "blob:x",
        "./X.js",
    ],
)
def test_the_import_rule_rejects_non_local_specifiers(target: str) -> None:
    assert module_targets(f'import x from "{target}";') == [target]
    assert not re.fullmatch(r"\./[a-z]+\.js", target)


def test_the_import_scanner_sees_every_form() -> None:
    text = (
        'import a from "./a.js";\n'
        'import { b } from "./b.js";\n'
        'import * as c from "./c.js";\n'
        'export { d } from "./d.js";\n'
        'const e = await import("./e.js");\n'
        'import "./f.js";\n'
    )
    assert module_targets(text) == [f"./{n}.js" for n in "abcdef"]


def test_no_dangerous_sinks_in_editor_sources() -> None:
    problems = {name: scan(text) for name, text in editor_sources().items()}
    assert {name: found for name, found in problems.items() if found} == {}


def test_html_references_only_same_origin_files() -> None:
    html = editor_sources()["index.html"]
    targets = re.findall(r"""(?:src|href)\s*=\s*["']([^"']*)["']""", html)
    assert targets
    assert any(not target.startswith("#") for target in targets)
    for target in targets:
        if target.startswith("#"):
            assert re.fullmatch(r"#[a-z-]+", target), target
            assert f'id="{target[1:]}"' in html, target
            continue
        assert re.fullmatch(r"/__edit/[a-z]+\.(js|css)", target), target
        assert target.split("/")[-1] in editor_sources(), target


@pytest.mark.parametrize(
    ("hostile", "rule"),
    [
        ("el.innerHTML = x", "innerHTML-family"),
        ("el.outerHTML = x", "innerHTML-family"),
        ("el.insertAdjacentHTML('x', y)", "innerHTML-family"),
        ("document.write(x)", "document.write"),
        ("document . write (x)", "document.write"),
        ("eval(x)", "eval"),
        ("new Function('x')", "eval"),
        ("setTimeout('x()', 1)", "string timer"),
        ('<img src="x" onerror="x()">', "inline handler attribute"),
        ("<body onload=x>", "inline handler attribute"),
        ("el.onclick = f", "handler property"),
        ('<script src="https://cdn.example/x.js"></script>', "remote URL"),
        ("fetch('//cdn.example/x')", "remote URL"),
        ("import x from 'https://cdn.example/x.js'", "import from URL"),
        ("import('data:text/javascript,1')", "import from URL"),
        ('<a href="javascript:alert(1)">', "javascript: URL"),
        ('<div style="color:red">', "inline style attribute"),
        ("localStorage.setItem('t', t)", "storage"),
    ],
)
def test_the_scanner_flags_hostile_input(hostile: str, rule: str) -> None:
    assert rule in scan(hostile)


def test_the_scanner_passes_clean_code() -> None:
    clean = (
        "const el = document.createElement('p');\n"
        "el.textContent = value;\n"
        "el.addEventListener('click', handler);\n"
        "history.replaceState(null, '', location.pathname);\n"
    )
    assert scan(clean) == []
