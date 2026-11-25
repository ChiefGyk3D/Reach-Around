# SPDX-License-Identifier: Apache-2.0
"""Config error text and scalar size (#764). Every name is synthetic."""

from __future__ import annotations

import enum
import typing
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError
from pydantic.types import Tag

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.loader import ConfigError
from reach_around.config.models import (
    _BLOCK_TAGS,
    MAX_CONFIG_NODES,
    MAX_CONFIG_SCALAR_CHARS,
    MAX_SCALAR_CHARS,
    Block,
    check_config_budget,
    error_message,
    problem_text,
)

CANARY = "ZQCANARY-" + "A" * 3000 + "\n\x1b[31m‮"


def link(i: int) -> dict[str, Any]:
    return {"kind": "link", "id": f"l{i}", "title": "L", "url": "https://example.com"}


def site_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "E"},
        "tabs": [{"id": "t", "title": "T", "blocks": [link(0)]}],
    }
    doc.update(kw)
    return doc


def _text_of(doc: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate(doc)
    return problem_text(exc.value)


def _canary_documents() -> dict[str, dict[str, Any]]:
    tab = {"id": "t", "title": "T", "blocks": [link(0)]}
    return {
        "bad-kind-tag": site_doc(tabs=[{**tab, "blocks": [{"kind": CANARY}]}]),
        "bad-enum-layout": site_doc(tabs=[{**tab, "layout": CANARY}]),
        "bad-literal-version": site_doc(version=CANARY),
        "bad-timezone-validator": site_doc(timezone=CANARY),
        "bad-url-validator": site_doc(
            tabs=[{**tab, "blocks": [{**link(0), "url": "https://" + CANARY}]}]
        ),
        "duplicate-id-validator": site_doc(
            tabs=[
                {**tab, "blocks": [{**link(0), "id": "dup"}, {**link(1), "id": "dup"}]}
            ]
        ),
        "too-long-title": site_doc(tabs=[{**tab, "title": CANARY * 3}]),
        "pattern-id": site_doc(tabs=[{**tab, "id": CANARY}]),
        "wrong-type": site_doc(creator={"name": [CANARY]}),
        "extra-key": site_doc(**{CANARY: CANARY}),
        "bad-date": site_doc(cutover_date=CANARY),
        "non-mapping-block": site_doc(tabs=[{**tab, "blocks": [CANARY]}]),
    }


@pytest.mark.parametrize("name", sorted(_canary_documents()))
def test_no_error_type_the_models_raise_echoes_the_value(name: str) -> None:
    text = _text_of(_canary_documents()[name])
    assert "ZQCANARY" not in text
    assert "\x1b" not in text
    assert "‮" not in text
    assert "\n" not in text
    assert len(text) < 1000, len(text)


def test_load_site_config_bounds_the_output_for_a_hostile_kind(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\ncreator: {name: E}\ntabs:\n  - id: links\n    title: Links\n"
        f'    blocks:\n      - kind: "ZQCANARY-{"A" * 100_000}"\n'
    )
    with pytest.raises(ConfigError) as exc:
        load_site_config(path)
    assert "ZQCANARY" not in str(exc.value)
    assert len(str(exc.value)) < 1000


def test_messages_come_from_the_table_not_from_pydantic() -> None:
    class Fake:
        def errors(self, **_: Any) -> list[dict[str, Any]]:
            return [
                {"loc": ("a",), "type": "value_error", "msg": "Value error, " + CANARY},
                {"loc": ("b",), "type": "brand_new_type", "msg": CANARY},
                {"loc": ("c",), "msg": CANARY},
            ]

        def error_count(self) -> int:
            return 3

    text = problem_text(cast(ValidationError, Fake()))
    assert "ZQCANARY" not in text
    assert text.count("invalid value") == 2
    assert error_message("no_such_type") == "invalid value"


# ---- scalar size is bounded at the one entry point ---------------------------


@pytest.mark.parametrize(
    "value",
    ["A" * (MAX_SCALAR_CHARS + 1), 10 ** (MAX_SCALAR_CHARS + 10)],
    ids=["huge-string", "huge-int"],
)
def test_one_huge_scalar_is_refused_before_validation(value: Any) -> None:
    with pytest.raises(ValidationError, match="longer than"):
        SiteConfig.model_validate(site_doc(creator={"name": value}))
    with pytest.raises(ValueError, match="longer than"):
        check_config_budget({"a": value})


def test_huge_key_is_refused() -> None:
    with pytest.raises(ValueError, match="longer than"):
        check_config_budget({"K" * (MAX_SCALAR_CHARS + 1): 1})


def _count(node: object) -> int:
    if isinstance(node, dict):
        return 1 + sum(1 + _count(v) for v in node.values())
    if isinstance(node, list):
        return 1 + sum(_count(v) for v in node)
    return 1


def test_many_medium_strings_hit_the_aggregate_cap() -> None:
    each = MAX_SCALAR_CHARS // 2
    count = MAX_CONFIG_SCALAR_CHARS // each + 1
    doc = site_doc(creator={"name": "E", "bio": ["B" * each] * count})
    assert _count(doc) < MAX_CONFIG_NODES
    with pytest.raises(ValidationError, match="characters of text"):
        SiteConfig.model_validate(doc)
    check_config_budget({"a": ["B" * each] * (count - 2)})


def test_aggregate_counts_an_aliased_value_each_time() -> None:
    shared = "S" * (MAX_SCALAR_CHARS // 2)
    with pytest.raises(ValueError, match="characters of text"):
        check_config_budget([shared] * (MAX_CONFIG_SCALAR_CHARS // len(shared) + 1))


def test_scalar_subclasses_are_refused() -> None:
    class StrEnumLike(enum.StrEnum):
        a = "a"

    class IntEnumLike(enum.IntEnum):
        a = 1

    class Str2(str):
        pass

    for bad in (StrEnumLike.a, IntEnumLike.a, Str2("x")):
        with pytest.raises(ValueError, match="type that is not allowed"):
            check_config_budget({"a": bad})
    check_config_budget({"a": ["x", 1, 1.5, True, None]})


# ---- block-tag coverage is enumerated from the real union --------------------


def _real_block_tags() -> set[str]:
    union = typing.get_args(Block)[0]
    tags: set[str] = set()
    for member in typing.get_args(union):
        for meta in typing.get_args(member)[1:]:
            if isinstance(meta, Tag):
                tags.add(meta.tag)
    return tags


def test_block_tag_table_matches_the_real_union() -> None:
    assert _real_block_tags() == _BLOCK_TAGS
    assert len(_BLOCK_TAGS) >= 9


# The discriminator picks a tag from the keys a block holds, so each tag needs
# its own hostile shape (a marker key plus bad values and an unknown key).
_HOSTILE_SHAPES: dict[str, dict[str, Any]] = {
    "link": {"id": [{}], "title": [{}], "url": [{}]},
    "section": {"id": [{}], "title": [{}], "links": [{}]},
    "text": {"id": [{}], "markdown": [{}]},
    "image": {"id": [{}], "src": [{}]},
    "crypto": {"id": [{}], "coins": [{}]},
    "email": {"id": [{}], "address": [{}]},
    "platforms": {"id": [{}], "items": [{}]},
    "live": {"id": [{}], "players": [{}]},
    "divider": {"id": [{}]},
}


def test_every_real_block_tag_has_a_hostile_shape() -> None:
    assert set(_HOSTILE_SHAPES) == _real_block_tags()


@pytest.mark.parametrize("tag", sorted(_real_block_tags()))
def test_every_block_tag_has_a_hostile_document(tag: str) -> None:
    # A divider is a block holding only an id, so it takes no unknown key.
    block = dict(_HOSTILE_SHAPES[tag], **({} if tag == "divider" else {"bogus": 1}))
    doc = site_doc(tabs=[{"id": "T_1", "blocks": [block] * 20}])
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate(doc)
    # The error location carries the union tag, so this tag was exercised.
    tags_seen = {
        e["loc"][4] for e in exc.value.errors(include_input=False) if len(e["loc"]) > 4
    }
    assert tag in tags_seen, (tag, tags_seen)
