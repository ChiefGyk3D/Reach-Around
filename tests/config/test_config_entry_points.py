# SPDX-License-Identifier: Apache-2.0
"""Every way a root config or imported history is built from outside data (#764).

The budget (size, depth, shape) runs from a before-validator on both models, an
existing instance is revalidated (and refused), every loader goes through one
function, and ``model_construct`` is not used anywhere in ``src``. Error text
never carries a key the file's author typed, on any printing path.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

import reach_around
from reach_around.commands import check as check_cmd
from reach_around.config import SiteConfig, load_site_config
from reach_around.config.budget import Budget, check_budget
from reach_around.config.imported_history import (
    IMPORTED_BUDGET,
    MAX_IMPORTED_BYTES,
    MAX_PERIODS,
    ImportedHistory,
    load_imported,
    parse_imported,
)
from reach_around.config.loader import ConfigError, load_yaml
from reach_around.config.models import (
    MAX_CONFIG_BYTES,
    MAX_CONFIG_DEPTH,
    MAX_CONFIG_NODES,
    MAX_CONFIG_SCALAR_CHARS,
    MAX_SCALAR_CHARS,
    UNKNOWN_KEY,
    Creator,
    _dotted,
    check_config_budget,
    problem_text,
    validate_site_config,
)
from reach_around.counts.beacons import IMPORT_NOTE, IMPORT_PLATFORM, IMPORT_SOURCE

SRC = Path(reach_around.__file__).parent


def site_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example"},
        "tabs": [
            {
                "id": "links",
                "title": "Links",
                "blocks": [{"id": "l0", "title": "L", "url": "https://example.com/"}],
            }
        ],
    }
    doc.update(kw)
    return doc


def period(day: int = 1, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start": f"2026-01-{day:02d}",
        "end": f"2026-01-{day:02d}",
        "days": 1,
        "views": 1,
        "clicks": 1,
    }
    out.update(kw)
    return out


def history(periods: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version": 1,
        "source": IMPORT_SOURCE,
        "note": IMPORT_NOTE,
        "platform": IMPORT_PLATFORM,
        "periods": periods if periods is not None else [period()],
    }
    out.update(kw)
    return out


# ---- 1. a root SiteConfig can be built only through the budget --------------


def _constructed_root() -> SiteConfig:
    return SiteConfig.model_construct(
        version=1,
        creator=Creator.model_construct(name="E"),
        tabs=[object()] * 1_000_000,
    )


def test_a_constructed_root_is_refused_by_model_validate() -> None:
    bad = _constructed_root()
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate(bad)
    assert "type that is not allowed" in problem_text(exc.value)


def test_a_constructed_root_is_refused_by_the_one_loader_function() -> None:
    with pytest.raises(ValidationError):
        validate_site_config(_constructed_root())


def test_a_valid_instance_is_refused_too() -> None:
    # Nothing in src revalidates an instance; refusing every instance is what
    # makes a constructed one impossible to smuggle through.
    good = validate_site_config(site_doc())
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(good)


@pytest.mark.parametrize("how", ["constructor", "model_validate", "one_function"])
def test_every_construction_path_runs_the_budget(how: str) -> None:
    doc = site_doc(extra=[1] * (MAX_CONFIG_NODES + 1))
    with pytest.raises(ValidationError) as exc:
        if how == "constructor":
            SiteConfig(**doc)
        elif how == "model_validate":
            SiteConfig.model_validate(doc)
        else:
            validate_site_config(doc)
    assert f"more than {MAX_CONFIG_NODES} values" in problem_text(exc.value)


def _calls_in_src() -> list[tuple[Path, ast.Call]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                found.append((path, node))
    return found


def test_src_never_calls_model_construct() -> None:
    # model_construct skips every validator, so it is unsupported for any model
    # built from outside data; nothing in src may call it (a docstring may name it).
    hits = [
        f"{path.relative_to(SRC)}:{node.lineno}"
        for path, node in _calls_in_src()
        if isinstance(node.func, ast.Attribute) and node.func.attr == "model_construct"
    ]
    assert hits == []


def test_every_loader_builds_a_site_config_through_one_function() -> None:
    hits = []
    for path, node in _calls_in_src():
        func = node.func
        direct = isinstance(func, ast.Name) and func.id == "SiteConfig"
        method = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "SiteConfig"
            and func.attr in {"model_validate", "model_validate_json"}
        )
        if (direct or method) and path.name != "models.py":
            hits.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert hits == []


def test_check_command_uses_the_one_function() -> None:
    source = Path(check_cmd.__file__).read_text(encoding="utf-8")
    assert "validate_site_config(raw)" in source


# ---- 2. ImportedHistory is budgeted at its own entry ------------------------


def test_a_valid_history_at_the_period_cap_is_accepted() -> None:
    periods = [
        {
            "start": f"{2000 + i // 12}-{i % 12 + 1:02d}-01",
            "end": f"{2000 + i // 12}-{i % 12 + 1:02d}-28",
            "days": 28,
            "views": 10**12,
            "clicks": 10**12,
        }
        for i in range(MAX_PERIODS)
    ]
    assert len(ImportedHistory.model_validate(history(periods)).periods) == MAX_PERIODS


@pytest.mark.parametrize("how", ["constructor", "model_validate", "parse_imported"])
def test_a_huge_history_is_refused_before_validation(how: str) -> None:
    doc = history([period() for _ in range(IMPORTED_BUDGET.nodes)])
    with pytest.raises((ValidationError, ConfigError)):
        if how == "constructor":
            ImportedHistory(**doc)
        elif how == "model_validate":
            ImportedHistory.model_validate(doc)
        else:
            parse_imported(json.dumps(doc).encode())


def test_a_huge_history_fails_once_at_the_root() -> None:
    # One error at the root, not one per bad period: nothing below the root was
    # validated, so no per-period error list was built.
    doc = history([period(views=-1) for _ in range(IMPORTED_BUDGET.nodes)])
    with pytest.raises(ValidationError) as exc:
        ImportedHistory.model_validate(doc)
    assert exc.value.error_count() == 1
    assert exc.value.errors(include_input=False)[0]["loc"] == ()


def test_history_scalar_depth_and_shape_are_refused() -> None:
    cases: dict[str, dict[Any, Any]] = {
        "scalar": history(note="x" * (IMPORTED_BUDGET.scalar_chars + 1)),
        "digits": history([period(views=10**300)]),
        "depth": history(extra=[[[[[[[[1]]]]]]]]),
        "tuple": history() | {"periods": (period(),)},
        "set": history() | {"extra": {1}},
        "key": {**history(), 1: 2},
    }
    for label, doc in cases.items():
        with pytest.raises(ValidationError):
            ImportedHistory.model_validate(doc)
        assert label


def test_history_budget_boundaries() -> None:
    cap = IMPORTED_BUDGET
    check_budget([0] * (cap.nodes - 1), cap)
    with pytest.raises(ValueError):
        check_budget([0] * cap.nodes, cap)
    check_budget("x" * cap.scalar_chars, cap)
    with pytest.raises(ValueError):
        check_budget("x" * (cap.scalar_chars + 1), cap)


def test_a_constructed_history_is_refused() -> None:
    bad = ImportedHistory.model_construct(periods=[object()] * 1_000_000)
    with pytest.raises(ValidationError):
        ImportedHistory.model_validate(bad)


def test_the_file_size_bound_holds_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    path.write_bytes(b" " * (MAX_IMPORTED_BYTES + 1))
    with pytest.raises(ConfigError, match="too large"):
        load_imported(path)
    path.write_text(json.dumps(history()))
    assert load_imported(path).periods


def test_a_full_period_list_fits_the_byte_and_node_caps() -> None:
    text = json.dumps(history([period() for _ in range(MAX_PERIODS)]))
    assert len(text) < MAX_IMPORTED_BYTES
    check_budget(json.loads(text), IMPORTED_BUDGET)


# ---- 3. enforced caps hold at their boundaries -----------------------------


def test_node_cap_boundary() -> None:
    check_config_budget([0] * (MAX_CONFIG_NODES - 1))
    with pytest.raises(ValueError, match="more than"):
        check_config_budget([0] * MAX_CONFIG_NODES)


def test_depth_cap_boundary() -> None:
    def nest(levels: int) -> object:
        out: object = 0
        for _ in range(levels):
            out = [out]
        return out

    check_config_budget(nest(MAX_CONFIG_DEPTH - 1))
    with pytest.raises(ValueError, match="nested"):
        check_config_budget(nest(MAX_CONFIG_DEPTH + 1))


def test_scalar_and_total_character_cap_boundaries() -> None:
    check_config_budget("x" * MAX_SCALAR_CHARS)
    with pytest.raises(ValueError, match="longer than"):
        check_config_budget("x" * (MAX_SCALAR_CHARS + 1))
    parts = MAX_CONFIG_SCALAR_CHARS // MAX_SCALAR_CHARS
    check_config_budget(["x" * MAX_SCALAR_CHARS] * (parts - 1))
    with pytest.raises(ValueError, match="characters"):
        check_config_budget(["x" * MAX_SCALAR_CHARS] * (parts + 1))


def test_byte_cap_boundary_is_applied_before_yaml_parsing(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text("a: " + "x" * (MAX_CONFIG_BYTES - 4) + "\n")
    assert path.stat().st_size == MAX_CONFIG_BYTES
    load_yaml(path, max_bytes=MAX_CONFIG_BYTES)
    path.write_text("a: " + "x" * MAX_CONFIG_BYTES + "\n")
    with pytest.raises(ConfigError, match="too large"):
        load_site_config(path)


def test_custom_budget_object_is_honoured() -> None:
    tiny = Budget(nodes=3, depth=2, scalar_chars=2, total_chars=3, what="thing")
    check_budget([1, 2], tiny)
    with pytest.raises(ValueError, match="the thing holds more than 3 values"):
        check_budget([1, 2, 3], tiny)


# ---- 4. error locations never carry a key the file wrote --------------------

HOSTILE = [
    "SECRET_API_KEY_abc123",
    "k" * 60_000,
    "bad\x00key\x07\r\nINJECT",
    "\x1b[31mred\x1b[0m",
    "café‮",
]


class _Mapped(BaseModel):
    values: dict[str, int]


@pytest.mark.parametrize("key", HOSTILE)
def test_a_mapping_key_in_a_location_is_replaced(key: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _Mapped.model_validate({"values": {key: "bad"}})
    text = problem_text(exc.value)
    assert key[:8] not in text
    assert UNKNOWN_KEY in text


@pytest.mark.parametrize("key", HOSTILE)
def test_dotted_prints_only_schema_names(key: str) -> None:
    assert _dotted(("creator", key, 2, "name")) == f"creator.{UNKNOWN_KEY}[2].name"


@pytest.mark.parametrize("key", HOSTILE)
def test_unknown_keys_through_load_site_config(tmp_path: Path, key: str) -> None:
    doc = site_doc(creator={"name": "E", key: 1})
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True))
    with pytest.raises(ConfigError) as exc:
        load_site_config(path)
    assert key[:8] not in str(exc.value)
    assert str(exc.value).isascii()


@pytest.mark.parametrize("key", HOSTILE)
def test_unknown_keys_through_the_check_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str
) -> None:
    from reach_around.cli import main

    doc = site_doc(creator={"name": "E", key: 1})
    doc["tabs"][0]["blocks"][0]["bogus_" + key] = 1
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True))
    main(["check", "--config", str(path)])
    out = capsys.readouterr()
    assert key[:8] not in out.out + out.err


HOSTILE_YAML = {
    "duplicate key": "SECRET_KEY_zz: 1\nSECRET_KEY_zz: 2\n",
    "tag": "a: !SECRET_TAG_" + "t" * 5_000 + " x\n",
    "syntax": "a: [SECRET_VALUE_" + "v" * 5_000 + "\n  : :\n",
}


@pytest.mark.parametrize("label", sorted(HOSTILE_YAML))
def test_loader_errors_never_echo_the_file(tmp_path: Path, label: str) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(HOSTILE_YAML[label])
    with pytest.raises(ConfigError) as exc:
        load_yaml(path)
    text = str(exc.value)
    # A YAML tag is the one thing named (cut to 40 characters); nothing else is.
    assert ("t" * 50 not in text) if label == "tag" else ("SECRET" not in text)
    assert len(text) < 400
    assert text.isascii()
