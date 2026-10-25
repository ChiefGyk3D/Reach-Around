# SPDX-License-Identifier: Apache-2.0
"""Source guards for the editor forms: strings, attributes, controls, network."""

from __future__ import annotations

import re
from importlib import resources

import pytest

JS_NAMES = ("app.js", "api.js", "store.js", "strings.js", "view.js")
ATTRIBUTES = {
    "class",
    "for",
    "id",
    "name",
    "type",
    "value",
    "min",
    "max",
    "step",
    "inputmode",
    "autocomplete",
    "aria-describedby",
    "aria-invalid",
    "aria-labelledby",
    "data-key",
    "data-string",
    "disabled",
    "hidden",
    "novalidate",
    "tabindex",
    "src",
}


def source(name: str) -> str:
    root = resources.files("reach_around").joinpath("editor_assets")
    return root.joinpath(name).read_text(encoding="utf-8")


def string_ids() -> set[str]:
    return set(re.findall(r'^\s*"([A-Za-z0-9_.]+)"\s*:', source("strings.js"), re.M))


def used_ids(text: str) -> set[str]:
    """Literal ids passed to t(...), plus the prefixes of built-up ones."""
    found = set(re.findall(r'\bt\(\s*"([A-Za-z0-9_.]+)"', text))
    return {i for i in found if not i.endswith(".")}  # "a." + x is checked below


def literal_text_problems(text: str) -> list[str]:
    """User-visible text written in code instead of strings.js."""
    problems = []
    if re.search(r'textContent\s*=\s*"[^"]*[A-Za-z]', text):
        problems.append("textContent literal")
    if re.search(r'textual\(\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*"[^"]*[A-Za-z]', text):
        problems.append("textual literal")
    if re.search(r'withHidden\([^,]+,\s*"[^"]*[A-Za-z]', text):
        problems.append("withHidden literal")
    if re.search(r'setAttribute\(\s*"(aria-label|title|placeholder|alt)"', text):
        problems.append("visible-name attribute set in code")
    return problems


def attribute_problems(text: str) -> list[str]:
    names = re.findall(r'setAttribute\(\s*"([^"]+)"', text)
    names += re.findall(r'removeAttribute\(\s*"([^"]+)"', text)
    names += re.findall(r'hasAttribute\(\s*"([^"]+)"', text)
    return sorted({n for n in names if n not in ATTRIBUTES})


def test_the_forms_sources_were_collected() -> None:
    assert string_ids()
    assert len(source("view.js")) > 5000
    assert len(source("store.js")) > 5000


def test_every_literal_string_id_exists() -> None:
    known = string_ids()
    seen = 0
    for name in JS_NAMES:
        if name == "strings.js":
            continue
        for ident in used_ids(source(name)):
            seen += 1
            assert ident in known, (name, ident)
    assert seen > 30


def test_dynamic_string_ids_exist() -> None:
    known = string_ids()
    wanted = {f"theme.mode.{m}" for m in ("auto", "light", "dark")}
    wanted |= {
        f"color.{k}" for k in ("bg", "text", "accent", "on_accent", "surface", "border")
    }
    wanted |= {"links.tab", "links.group", "action.moveUp", "action.moveDown"}
    assert wanted <= known


def test_no_unused_strings_pile_up() -> None:
    code = "".join(source(n) for n in JS_NAMES if n != "strings.js")
    html = source("index.html")
    dynamic = re.compile(r"^(theme\.mode\.|color\.|links\.(tab|group)$|status\.)")
    unused = {
        i
        for i in string_ids()
        if f'"{i}"' not in code and f'"{i}"' not in html and not dynamic.match(i)
    }
    assert unused <= {
        "app.title",
        "skip.preview",
        "nav.config",
        "nav.preview",
        "config.heading",
        "config.readonly",
        "preview.heading",
    } | set(re.findall(r'data-string="([^"]+)"', html)), sorted(unused)


def test_no_user_visible_text_is_written_in_code() -> None:
    for name in ("view.js", "store.js", "app.js"):
        assert literal_text_problems(source(name)) == [], name


def test_only_known_attributes_are_set() -> None:
    for name in ("view.js", "app.js"):
        assert attribute_problems(source(name)) == [], name


def test_every_button_has_an_explicit_type() -> None:
    view = source("view.js")
    made = len(re.findall(r'make\(\s*"button"', view))
    typed = len(re.findall(r'setAttribute\(\s*"type",\s*"(button|submit)"\s*\)', view))
    assert made == 2  # the Save button and the shared Move up / Move down loop
    assert typed >= made


def test_network_goes_only_through_the_client_and_only_to_op_and_state() -> None:
    for name in JS_NAMES:
        text = source(name)
        if name != "api.js":
            assert not re.search(
                r"\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon", text
            )
    store = source("store.js")
    calls = re.findall(r"client\.(get|post)\(\s*([^,)]*)", store)
    assert calls == [("post", '"op"')]
    assert not re.search(r"client\.", source("view.js"))


def test_forms_never_navigate_or_open_anything() -> None:
    for name in ("view.js", "store.js", "app.js"):
        text = source(name)
        assert not re.search(
            r"\blocation\s*\.\s*(href|assign|replace)|window\.open", text
        )
        assert not re.search(r"""setAttribute\(\s*"href"|"mailto:|"tel:""", text)


def test_the_form_view_uses_text_content_for_values() -> None:
    view = source("view.js")
    assert view.count("textContent") >= 8
    assert not re.search(
        r"\.(inner|outer)HTML|insertAdjacent|createContextualFragment", view
    )


@pytest.mark.parametrize(
    ("snippet", "found"),
    [
        ('node.textContent = "Save";', "textContent literal"),
        ('textual("p", "hint", "Type here");', "textual literal"),
        ('withHidden(button, "Move up", name);', "withHidden literal"),
        (
            'node.setAttribute("aria-label", "Move");',
            "visible-name attribute set in code",
        ),
    ],
)
def test_the_text_guard_bites(snippet: str, found: str) -> None:
    assert found in literal_text_problems(snippet)


def test_the_text_guard_passes_clean_code() -> None:
    clean = 'node.textContent = t("action.save");\ntextual("p", "hint", spec.hint);'
    assert literal_text_problems(clean) == []


def test_the_attribute_guard_bites() -> None:
    assert attribute_problems('node.setAttribute("onclick", "x");') == ["onclick"]
    assert attribute_problems('node.setAttribute("style", "x");') == ["style"]
    assert attribute_problems('node.setAttribute("class", "x");') == []


def test_the_shell_has_the_places_the_forms_write_to() -> None:
    html = source("index.html")
    app = source("app.js")
    for ident in ("editor-view", "editor-note", "build-note", "config-view"):
        assert html.count(f'id="{ident}"') == 1, ident
        assert f'getElementById("{ident}")' in app, ident
    assert "<details" in html and "<summary" in html


def test_the_form_styles_cover_errors_focus_targets_and_no_remote_files() -> None:
    css = source("app.css")
    for needle in ("--error", ".field-error", '[aria-invalid="true"]', ".button"):
        assert needle in css, needle
    assert "min-block-size: 2.75rem" in css  # 44 px touch target
    assert not re.search(r"(?<![-\w])content\s*:", css)  # no text in CSS
