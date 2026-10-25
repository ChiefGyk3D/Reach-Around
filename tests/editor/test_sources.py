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


def test_sources_were_collected() -> None:
    names = set(editor_sources())
    assert {"index.html", "app.js", "app.css"} <= names


def test_no_dangerous_sinks_in_editor_sources() -> None:
    problems = {name: scan(text) for name, text in editor_sources().items()}
    assert {name: found for name, found in problems.items() if found} == {}


def test_html_references_only_same_origin_files() -> None:
    html = editor_sources()["index.html"]
    targets = re.findall(r"""(?:src|href)\s*=\s*["']([^"']*)["']""", html)
    assert targets
    for target in targets:
        assert target.startswith("/__edit/") or target.startswith("app."), target
        assert "//" not in target.replace("/__edit/", "")


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
