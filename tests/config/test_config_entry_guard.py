# SPDX-License-Identifier: Apache-2.0
"""The source guard for the two raw-data models, and the JSON and output caps (#764).

``SiteConfig`` and ``ImportedHistory`` are built from outside data only at their
own entry (the budget runs there). A syntactic guard keeps the rest of ``src``
from building them any other way. It resolves import renames, assignments of the
class to another name, ``getattr`` with a forbidden or computed name, and a string
import of the config modules. It is a lint, not a sandbox: ``eval``, ``exec``, a
computed attribute name and a class reached through ``globals()`` or ``vars()``
are unsupported boundaries, and a test below pins what is and is not caught so the
limit is written down, not implied.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_core import InitErrorDetails

import reach_around
from reach_around.config import SiteConfig
from reach_around.config.budget import json_over_limit
from reach_around.config.imported_history import MAX_IMPORTED_BYTES, ImportedHistory
from reach_around.config.models import (
    MAX_CONFIG_BYTES,
    MAX_REPORTED_PROBLEMS,
    problem_text,
)

SRC = Path(reach_around.__file__).parent

GUARDED = {"SiteConfig", "ImportedHistory"}
RAW_METHODS = {
    "model_validate",
    "model_validate_json",
    "model_validate_strings",
    "model_construct",
}
# The one file per class that may build it from raw data, and which class that is.
# A file may build only the class it owns (SiteConfig in imported_history.py is a
# violation).
OWNERS = {
    "config/models.py": "SiteConfig",
    "config/imported_history.py": "ImportedHistory",
}
# Parsing JSON for a guarded model goes through model_validate_json only, which
# refuses an over-size document before the parse. These other ways to parse JSON
# are not allowed anywhere in src.
JSON_BYPASS_ATTRS = {"validate_json"}
JSON_BYPASS_NAMES = {"TypeAdapter"}
IMPORTERS = {"import_module", "__import__"}
CONFIG_MODULES = ("reach_around.config.models", "reach_around.config.imported_history")


def _names(node: ast.AST | None) -> set[str]:
    """The plain names an expression is made of (``a``, ``mod.a``, ``a or b``)."""
    if node is None:
        return set()
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _folded(node: ast.AST | None) -> str | None:
    """A string built only from constants (``"a" + "b"``, ``f"a{'b'}"``), or None."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            inner = _folded(
                value.value if isinstance(value, ast.FormattedValue) else value
            )
            if inner is None:
                return None
            parts.append(inner)
        return "".join(parts)
    return None


def _aliases(tree: ast.AST, seed: set[str] | None = None) -> set[str]:
    """Every name that stands for a guarded class: renames and plain assignments,
    repeated until nothing new is found (``A = SiteConfig; B = A``)."""
    aliases = set(GUARDED if seed is None else seed)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom | ast.Import):
                for item in node.names:
                    if (
                        item.asname
                        and item.name.split(".")[-1] in aliases
                        and item.asname not in aliases
                    ):
                        aliases.add(item.asname)
                        changed = True
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if _names(node.value) & aliases:
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in aliases:
                            aliases.add(target.id)
                            changed = True
    return aliases


def guard_violations(source: str, *, owner: str | None = None) -> list[str]:
    """What in ``source`` builds a guarded model other than through its entry.

    ``owner`` names the class the module defines: the module may call that class's
    own entry points, but never ``model_construct`` and never another guarded class.
    """
    tree = ast.parse(source)
    # A bare string statement is a docstring or a comment-like note.
    notes = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }
    aliases = _aliases(tree, GUARDED - {owner} if owner else None)
    found: list[str] = []

    def hit(node: ast.AST, why: str) -> None:
        found.append(f"line {getattr(node, 'lineno', 0)}: {why}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute | ast.Name):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name == "model_construct":
                hit(node, "model_construct")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in notes
        ):
            if node.value == "model_construct":
                hit(node, "model_construct named by string")
            if node.value.startswith(CONFIG_MODULES):
                hit(node, "config module named by string")
        if isinstance(node, ast.BinOp | ast.JoinedStr):
            # A string built from constants ("reach_around.config." + "models").
            folded = _folded(node)
            if folded is not None and folded.startswith(CONFIG_MODULES):
                hit(node, "config module named by a computed string")
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if name in IMPORTERS and node.args:
                folded = _folded(node.args[0])
                if folded is not None and folded.startswith(CONFIG_MODULES):
                    hit(node, "config module imported by a computed string")
            if isinstance(func, ast.Name | ast.Attribute) and name in aliases:
                # SiteConfig(...) and m.SiteConfig(...) / config.models.SiteConfig(...)
                hit(node, "direct construction")
            if isinstance(func, ast.Name) and func.id == "getattr" and node.args:
                second = node.args[1] if len(node.args) > 1 else None
                computed = second is not None and not isinstance(second, ast.Constant)
                named = isinstance(second, ast.Constant) and second.value in RAW_METHODS
                if named or (computed and _names(node.args[0]) & aliases):
                    hit(node, "getattr of a raw entry point")
        if isinstance(node, ast.Attribute) and node.attr in RAW_METHODS:
            if _names(node.value) & aliases:
                hit(node, f"{node.attr} on a guarded class")
    return found


def json_bypass_violations(source: str) -> list[str]:
    """Every parse of JSON that skips the byte cap: ``TypeAdapter`` (its
    ``validate_json`` never reaches the model's ``model_validate_json``) or a
    ``validate_json`` call of any kind. Nothing in ``src`` may use either."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in JSON_BYPASS_NAMES:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in (
            JSON_BYPASS_ATTRS | JSON_BYPASS_NAMES
        ):
            found.append(f"line {node.lineno}: {node.attr}")
        elif isinstance(node, ast.alias) and node.name in JSON_BYPASS_NAMES:
            found.append(f"line {node.lineno}: import of {node.name}")
    return found


def test_src_builds_guarded_models_only_through_their_entry() -> None:
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        for violation in guard_violations(
            path.read_text(encoding="utf-8"), owner=OWNERS.get(rel)
        ):
            hits.append(f"{rel} {violation}")
    assert hits == []


def test_src_never_parses_json_around_the_byte_cap() -> None:
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        for violation in json_bypass_violations(path.read_text(encoding="utf-8")):
            hits.append(f"{path.relative_to(SRC).as_posix()} {violation}")
    assert hits == []


@pytest.mark.parametrize(
    "snippet",
    [
        "from pydantic import TypeAdapter\nTypeAdapter(SiteConfig).validate_json(b)",
        "import pydantic\npydantic.TypeAdapter(ImportedHistory)",
        "adapter.validate_json(data)",
    ],
)
def test_the_json_guard_catches_a_typeadapter_parse(snippet: str) -> None:
    assert json_bypass_violations(snippet) != []


def test_the_json_guard_allows_the_supported_entry() -> None:
    assert json_bypass_violations("SiteConfig.model_validate_json(data)") == []


@pytest.mark.parametrize(
    "snippet",
    [
        "SiteConfig(**raw)",
        "SiteConfig.model_validate(raw)",
        "SiteConfig.model_validate_json(text)",
        "ImportedHistory.model_validate(raw)",
        "SC = SiteConfig\nSC.model_validate(raw)",
        "SC = SiteConfig\nOther = SC\nOther(**raw)",
        "from reach_around.config import SiteConfig as SC\nSC(**raw)",
        "from reach_around.config.models import SiteConfig as SC\nSC.model_validate(r)",
        "import reach_around.config.models as m\nm.SiteConfig.model_validate(raw)",
        "getattr(SiteConfig, 'model_validate')(raw)",
        "SC = SiteConfig\ngetattr(SC, name)(raw)",
        "f = SiteConfig.model_validate\nf(raw)",
        "SiteConfig.model_construct(version=1)",
        "x = Model.model_construct\nx()",
        "getattr(Model, 'model_construct')()",
        "import importlib\nimportlib.import_module('reach_around.config.models')",
        "__import__('reach_around.config.imported_history')",
        "import reach_around.config.models as m\nm.SiteConfig(**raw)",
        "from reach_around import config\nconfig.SiteConfig(**raw)",
        "import reach_around\nreach_around.config.models.SiteConfig(**raw)",
        "import importlib\nimportlib.import_module('reach_around.config.' + 'models')",
        "import importlib\n"
        "importlib.import_module(f'reach_around.config.{\"models\"}')",
        "m = 'reach_around.config.' + 'models'",
    ],
)
def test_the_guard_catches_the_cheap_bypasses(snippet: str) -> None:
    assert guard_violations(snippet) != []


@pytest.mark.parametrize(
    "snippet",
    [
        "from reach_around.config import SiteConfig\n\n"
        "def f(c: SiteConfig) -> None:\n    return c.tabs\n",
        "from reach_around.config import SiteConfig as SC\n\n"
        "def f(c: SC) -> int:\n    return len(c.tabs)\n",
        '"""A docstring may say model_construct."""\n',
        "isinstance(x, SiteConfig)",
    ],
)
def test_the_guard_leaves_annotations_and_docstrings_alone(snippet: str) -> None:
    assert guard_violations(snippet) == []


def test_the_owner_module_may_use_its_own_entry_but_not_model_construct() -> None:
    owned = "SiteConfig.model_validate(raw)"
    assert guard_violations(owned, owner="SiteConfig") == []
    assert guard_violations("SiteConfig.model_construct()", owner="SiteConfig") != []


@pytest.mark.parametrize(
    "owner, snippet",
    [
        ("ImportedHistory", "SiteConfig.model_validate(raw)"),
        ("ImportedHistory", "SiteConfig(**raw)"),
        ("SiteConfig", "ImportedHistory.model_validate_json(raw)"),
        ("SiteConfig", "H = ImportedHistory\nH(**raw)"),
    ],
)
def test_an_owner_module_may_not_build_the_other_class(
    owner: str, snippet: str
) -> None:
    assert guard_violations(snippet, owner=owner) != []


def test_the_documented_unsupported_boundary_is_not_claimed_as_caught() -> None:
    # Pinned so the limit is written down: a class reached through globals() is
    # not resolved by a syntactic guard. If a future change starts catching it,
    # this test says the docs must change with it.
    assert guard_violations("globals()['SiteConfig'](**raw)") == []
    # So is a module name built from something other than constants.
    assert guard_violations("import importlib\nimportlib.import_module(name)") == []


# ---- the located-reason output cap -------------------------------------------


def _value_error(pieces: str) -> InitErrorDetails:
    return {
        "type": "value_error",
        "loc": (),
        "input": None,
        "ctx": {"error": ValueError(pieces)},
    }


def _located(tab: int, count: int) -> str:
    return "; ".join(
        f"tabs[{tab}].blocks[{i}].url: unknown tab 'x' in 'tab:x'" for i in range(count)
    )


def test_located_reasons_obey_the_one_output_cap() -> None:
    exc = ValidationError.from_exception_data(
        "SiteConfig", [_value_error(_located(0, 60))]
    )
    text = problem_text(exc)
    assert text.count("unknown tab") == MAX_REPORTED_PROBLEMS
    assert text.endswith("and more problems")


def test_located_reasons_across_errors_obey_the_same_cap() -> None:
    errors = [_value_error(_located(i, 15)) for i in range(10)]
    text = problem_text(ValidationError.from_exception_data("SiteConfig", errors))
    assert text.count("unknown tab") == MAX_REPORTED_PROBLEMS
    # 10 errors of 15 located reasons: 150 entries, 20 printed, 130 omitted.
    assert text.endswith("and 130 more problems")


def test_a_short_located_list_prints_whole_with_no_remainder() -> None:
    exc = ValidationError.from_exception_data(
        "SiteConfig", [_value_error(_located(0, 3))]
    )
    text = problem_text(exc)
    assert text.count("unknown tab") == 3
    assert "more problems" not in text


def _ordinary() -> InitErrorDetails:
    return {"type": "missing", "loc": ("version",), "input": None}


@pytest.mark.parametrize(
    "ordinary, located, expect",
    [
        # (ordinary errors before, located reasons in one error after, remainder)
        (0, 20, None),
        (1, 20, 1),
        (19, 1, None),
        (19, 2, 1),
        (20, 1, 1),
        (25, 0, 5),
        (5, 16, 1),
        (5, 30, "more"),
    ],
)
def test_the_remainder_counts_ordinary_and_located_entries(
    ordinary: int, located: int, expect: int | str | None
) -> None:
    errors = [_ordinary() for _ in range(ordinary)]
    if located:
        errors.append(_value_error(_located(0, located)))
    text = problem_text(ValidationError.from_exception_data("SiteConfig", errors))
    if expect is None:
        assert "more problems" not in text
    elif expect == "more":
        assert text.endswith("; and more problems")
    else:
        assert text.endswith(f"and {expect} more problems")


def test_an_unknown_remainder_is_not_given_a_number() -> None:
    # More located reasons than the scan reads: the true count is not known.
    exc = ValidationError.from_exception_data(
        "SiteConfig", [_value_error(_located(0, 400))]
    )
    assert problem_text(exc).endswith("; and more problems")


# ---- JSON entry points are byte-capped before the parse ----------------------


def test_site_config_json_is_refused_above_the_byte_cap_before_parsing() -> None:
    big = '{"version":1,"x":[' + "0," * MAX_CONFIG_BYTES + "0]}"
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate_json(big)
    assert exc.value.error_count() == 1
    assert f"larger than {MAX_CONFIG_BYTES} bytes" in problem_text(exc.value)


def test_site_config_json_at_the_cap_reaches_the_budget() -> None:
    text = '{"version":1,"x":[' + "0," * 50_000 + "0]}"
    assert len(text) <= MAX_CONFIG_BYTES
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate_json(text)
    assert "more than" in problem_text(exc.value)


@pytest.mark.parametrize("code_point", [0xE9, 0x20AC, 0x1F600])
def test_the_json_cap_counts_encoded_bytes_for_text(code_point: int) -> None:
    char = chr(code_point)
    width = len(char.encode("utf-8"))
    fits = MAX_CONFIG_BYTES // width
    assert not json_over_limit(char * fits, MAX_CONFIG_BYTES)
    assert json_over_limit(char * (fits + 1), MAX_CONFIG_BYTES)


def test_the_json_cap_is_exact_for_ascii_text_and_bytes() -> None:
    assert not json_over_limit("a" * 10, 10)
    assert json_over_limit("a" * 11, 10)
    assert not json_over_limit(b"a" * 10, 10)
    assert json_over_limit(bytearray(b"a" * 11), 10)
    assert json_over_limit(chr(0xD800) * 4, 10)  # a lone surrogate is 3 bytes


def test_a_multibyte_site_config_text_is_refused_by_encoded_size() -> None:
    text = '{"a":"' + chr(0x1F600) * (MAX_CONFIG_BYTES // 4) + '"}'
    assert len(text) < MAX_CONFIG_BYTES
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate_json(text)
    assert f"larger than {MAX_CONFIG_BYTES} bytes" in problem_text(exc.value)


def test_a_valid_site_config_json_still_loads() -> None:
    text = (
        '{"version":1,"creator":{"name":"E"},"tabs":[{"id":"links","title":"Links",'
        '"blocks":[{"id":"l0","title":"L","url":"https://example.com/"}]}]}'
    )
    assert SiteConfig.model_validate_json(text).tabs[0].id == "links"


def test_imported_history_json_is_refused_above_the_byte_cap() -> None:
    big = '{"version":1,"x":[' + "0," * MAX_IMPORTED_BYTES + "0]}"
    with pytest.raises(ValidationError) as exc:
        ImportedHistory.model_validate_json(big)
    assert exc.value.error_count() == 1
    assert exc.value.errors()[0]["loc"] == ()
    assert f"larger than {MAX_IMPORTED_BYTES} bytes" in str(exc.value)


def test_imported_history_json_under_the_cap_reaches_the_budget() -> None:
    text = '{"version":1,"x":[' + "0," * 10_000 + "0]}"
    assert len(text) < MAX_IMPORTED_BYTES
    with pytest.raises(ValidationError) as exc:
        ImportedHistory.model_validate_json(text)
    assert "values" in str(exc.value) or "more than" in str(exc.value)
