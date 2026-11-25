# SPDX-License-Identifier: Apache-2.0
"""The size and shape budget for a parsed document, checked before any model is built.

Both raw-data models (``SiteConfig`` and ``ImportedHistory``) run this from a
``model_validator(mode="before")``, so ``Model(...)`` and ``Model.model_validate(...)``
both pass through it. ``model_validate_json`` parses before any validator runs, so
both models override it to refuse an over-size document before the parse (see
``json_over_limit``); the node and depth budget then runs on the parsed value.
A model instance is
refused by the check itself: pydantic does not revalidate an instance of the
model (unless ``revalidate_instances="always"`` is set, which both models do), so
an instance built with ``model_construct`` would otherwise hide any amount of
data behind one node. ``model_construct`` is unsupported for external data; a
test scans ``src`` for any call to it.

No pydantic or model import here, so any config module can use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Only these exact types are plain values; a subclass (a StrEnum, an IntEnum, a
# str subclass with its own methods) is refused, not trusted.
_FIXED_SIZE_LEAVES = (float, bool, date, datetime)


@dataclass(frozen=True)
class Budget:
    """The caps for one kind of document and the word its messages use for it."""

    nodes: int
    depth: int
    scalar_chars: int
    total_chars: int
    what: str = "config"


def json_over_limit(json_data: str | bytes | bytearray, limit: int) -> bool:
    """Whether raw JSON is larger than ``limit`` bytes.

    ``model_validate_json`` parses before any validator runs, so the models refuse
    an over-size document here, before pydantic reads it. A text is measured in
    characters (a lower bound of its UTF-8 size), which keeps the check O(1); the
    parse of what passes is bounded by ``limit`` times a small constant.
    """
    return len(json_data) > limit


def _refuse(message: str) -> None:
    raise ValueError(message)


def _scalar_size(value: str | int, budget: Budget) -> int:
    """The size of a text (its length) or an integer (its decimal digits).

    Both are O(1) to read (an integer's digits come from its bit length, a
    slight over-count), and a scalar over the cap is refused here, before
    anything reads it.
    """
    size = (
        len(value)
        if isinstance(value, str)
        else (value.bit_length() * 30103 // 100000 + 1)
    )
    if size > budget.scalar_chars:
        _refuse(f"a value in the {budget.what} is longer than {budget.scalar_chars}")
    return size


def check_budget(raw: object, budget: Budget) -> None:
    """Refuse a parsed document that is too big, too deep or of a wrong shape.

    A document is built only from ``dict`` (with ``str`` keys), ``list`` and
    plain scalars. Any other container (a tuple, a set, a generator, a mapping
    subclass) is refused, because pydantic would accept it as a list or mapping
    and validate every element while this count saw one node. A pydantic model
    instance is refused too: parsed YAML or JSON never holds one. Every value,
    every container and every mapping key counts as one node; a value reachable
    twice (a YAML alias, a shared list) is counted each time it is reached, so
    an alias bomb is its expanded size. The walk is iterative (no recursion),
    stops at the first node over the node cap or over the depth cap, and cannot
    loop on a cycle (each visit is counted), so the work is bounded by the node
    cap whatever the input. Only exact ``str``, ``int``, ``float``, ``bool``,
    ``date`` and ``datetime`` scalars are allowed (a subclass is refused). The
    size of the scalars is bounded too, so work is bounded by size and not only
    by count. Messages are fixed: they never repeat a value from the document.
    """
    what = budget.what
    seen = 0
    size = 0
    stack: list[tuple[object, int]] = [(raw, 1)]
    while stack:
        node, depth = stack.pop()
        seen += 1
        if depth > budget.depth:
            _refuse(f"the {what} is nested more than {budget.depth} levels deep")
        if type(node) is dict:
            seen += len(node)
            if seen > budget.nodes:
                break
            for key, value in node.items():
                if type(key) is not str:
                    _refuse(f"a mapping key in the {what} is not text")
                size += _scalar_size(key, budget)
                stack.append((value, depth + 1))
        elif type(node) is list:
            if seen + len(node) > budget.nodes:
                seen += len(node)
                break
            stack.extend((item, depth + 1) for item in node)
        elif type(node) is str or type(node) is int:
            size += _scalar_size(node, budget)
        elif not (node is None or type(node) in _FIXED_SIZE_LEAVES):
            _refuse(f"the {what} holds a value of a type that is not allowed")
        if size > budget.total_chars:
            _refuse(
                f"the {what} holds more than {budget.total_chars} characters "
                "of text and numbers; shorten or remove entries"
            )
        if seen > budget.nodes:
            break
    if seen > budget.nodes:
        _refuse(
            f"the {what} holds more than {budget.nodes} values; "
            "split it or remove entries"
        )
