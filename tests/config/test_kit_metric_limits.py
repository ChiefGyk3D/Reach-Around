# SPDX-License-Identifier: Apache-2.0
"""Media-kit metric definitions and config size limits (#764).

Every name here is synthetic (fake brand, example.com).
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.blocks import KIT_PLATFORM_SLUGS
from reach_around.config.kit import KitConfig, PlatformMetric
from reach_around.config.loader import ConfigError
from reach_around.config.models import (
    MAX_BLOCKS_PER_TAB,
    MAX_CONFIG_BYTES,
    MAX_CONFIG_DEPTH,
    MAX_CONFIG_NODES,
    MAX_LINKS_PER_SECTION,
    MAX_MATERIALIZED_PROBLEMS,
    MAX_REPORTED_PROBLEMS,
    MAX_SOCIALS,
    MAX_TABS,
    Link,
    check_config_budget,
    problem_text,
)


def manual(label: str = "Followers", **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "platform": "twitch",
        "label": label,
        "source": "manual",
        "value": 100,
        "as_of": "2027-03-01",
    }
    out.update(kw)
    return out


def counts(platform: str = "twitch", label: str = "Followers") -> dict[str, Any]:
    return {"platform": platform, "label": label, "source": "counts"}


def kit_doc(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": "Example kit", "about": "A fake creator.", "metrics": metrics}


def site_doc(**kw: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example"},
        "tabs": [{"id": "links", "title": "Links", "blocks": [link(0)]}],
    }
    doc.update(kw)
    return doc


def link(i: int) -> dict[str, Any]:
    return {"id": f"l{i}", "title": "L", "url": "https://example.com/"}


def test_second_counts_metric_on_one_platform_refused() -> None:
    both = [counts(), counts(label="Subscribers")]
    with pytest.raises(ValidationError, match="one counts metric per platform") as exc:
        KitConfig.model_validate(kit_doc(both))
    assert "twitch" not in str(exc.value)
    assert KitConfig.model_validate(kit_doc([counts(), counts("kick")]))


@pytest.mark.parametrize(
    "label", ["followers", "FOLLOWERS", "Ｆollowers", "ＦOLLOWERS"]
)
def test_metric_labels_compare_normalized(label: str) -> None:
    with pytest.raises(ValidationError, match="more than once") as exc:
        KitConfig.model_validate(kit_doc([manual(), manual(label=label)]))
    text = problem_text(exc.value)
    assert "more than once" in text
    assert "followers" not in text.casefold()


def test_duplicate_label_canary_is_absent_from_the_real_output() -> None:
    canary = "ZQCANARYLABEL"
    pair = [manual(label=canary), manual(label=canary.lower())]
    with pytest.raises(ValidationError) as exc:
        KitConfig.model_validate(kit_doc(pair))
    text = problem_text(exc.value)
    assert "more than once" in text
    assert canary.casefold() not in text.casefold()


def test_distinct_labels_still_allowed() -> None:
    assert KitConfig.model_validate(kit_doc([manual(), manual(label="Subs")]))


def test_tabs_capped() -> None:
    tabs = [
        {"id": f"t{i}", "title": "T", "blocks": [link(i)]} for i in range(MAX_TABS + 1)
    ]
    with pytest.raises(ValidationError, match="at most"):
        SiteConfig.model_validate(site_doc(tabs=tabs))


def test_socials_capped() -> None:
    social = {"name": "S", "url": "https://example.com/", "icon": "github"}
    creator = {"name": "E", "socials": [social] * (MAX_SOCIALS + 1)}
    with pytest.raises(ValidationError, match="at most"):
        SiteConfig.model_validate(site_doc(creator=creator))


def test_blocks_capped() -> None:
    blocks = [link(i) for i in range(MAX_BLOCKS_PER_TAB + 1)]
    tabs = [{"id": "a", "title": "A", "blocks": blocks}]
    with pytest.raises(ValidationError, match="at most"):
        SiteConfig.model_validate(site_doc(tabs=tabs))


def test_section_links_capped() -> None:
    links = [link(i) for i in range(MAX_LINKS_PER_SECTION + 1)]
    section = {"id": "s", "title": "S", "links": links}
    tabs = [{"id": "a", "title": "A", "blocks": [section]}]
    with pytest.raises(ValidationError, match="at most"):
        SiteConfig.model_validate(site_doc(tabs=tabs))


def _valid_site_text(pad_bytes: int = 0) -> str:
    """A valid site config, padded with comments to at least ``pad_bytes``."""
    base = (
        "version: 1\ncreator:\n  name: E\ntabs:\n"
        "  - id: links\n    title: T\n    blocks:\n"
        "      - {id: l0, title: L, url: 'https://example.com/'}\n"
    )
    pad = "# CANARY-VALUE padding line\n"
    need = max(0, pad_bytes - len(base))
    return base + pad * (need // len(pad) + (1 if need % len(pad) else 0))


def test_file_over_the_byte_limit_refused_before_parsing(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(_valid_site_text(MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigError, match="file too large") as exc:
        load_site_config(path)
    assert "CANARY-VALUE" not in str(exc.value)
    # The same document, one line shorter than the limit, loads: only the size differs.
    path.write_text(_valid_site_text(MAX_CONFIG_BYTES - 64))
    assert path.stat().st_size <= MAX_CONFIG_BYTES
    assert load_site_config(path).creator.name == "E"


def test_two_million_links_stay_under_every_list_cap_but_not_the_budget() -> None:
    # 50 tabs x 200 sections x 200 links passes each per-list cap (the review
    # finding) and is refused by the whole-document budget before any model.
    links = [link(i) for i in range(MAX_LINKS_PER_SECTION)]
    section = {"id": "s", "title": "S", "links": links}
    tabs = [
        {
            "id": f"t{i}",
            "title": "T",
            "blocks": [dict(section, id=f"s{j}") for j in range(MAX_BLOCKS_PER_TAB)],
        }
        for i in range(MAX_TABS)
    ]
    with pytest.raises(ValidationError, match="more than 20000 values") as exc:
        SiteConfig.model_validate(site_doc(tabs=tabs))
    assert exc.value.error_count() == 1


def test_budget_is_checked_before_any_model_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[object] = []
    real = Link.__init__

    def spy(self: Link, **kw: Any) -> None:
        built.append(kw)
        real(self, **kw)

    monkeypatch.setattr(Link, "__init__", spy)
    big = site_doc(
        tabs=[{"id": "a", "title": "A", "blocks": [link(i) for i in range(200)]}]
    )
    big["extra_pad"] = list(range(MAX_CONFIG_NODES))
    with pytest.raises(ValidationError, match="more than"):
        SiteConfig.model_validate(big)
    assert built == []


def test_budget_boundary() -> None:
    check_config_budget({"a": list(range(MAX_CONFIG_NODES - 3))})
    with pytest.raises(ValueError, match="more than"):
        check_config_budget({"a": list(range(MAX_CONFIG_NODES - 2))})
    # Wide input is counted without recursion; the message repeats nothing.
    with pytest.raises(ValueError) as exc:
        check_config_budget({f"CANARY-{i}": [[]] for i in range(MAX_CONFIG_NODES)})
    assert "CANARY" not in str(exc.value)


def test_hundred_thousand_unknown_keys_refused_without_collecting_errors(
    tmp_path: Path,
) -> None:
    # 100,000 unknown keys would be 100,000 validation errors: that file is
    # refused on size, and a
    # smaller one (under the byte limit) is refused on the node budget with one problem.
    lines = [
        "version: 1",
        "creator:",
        "  name: E",
        "tabs:",
        "  - {id: a, title: A, blocks: []}",
    ]
    lines += [f"k{i}: 1" for i in range(12_000)]
    path = tmp_path / "site.yaml"
    path.write_text("\n".join(lines) + "\n")
    assert path.stat().st_size < MAX_CONFIG_BYTES
    with pytest.raises(ConfigError, match="more than 20000 values") as exc:
        load_site_config(path)
    assert str(exc.value).count("more than") == 1
    path.write_text("\n".join(lines + [f"j{i}: 1" for i in range(100_000)]) + "\n")
    with pytest.raises(ConfigError, match="file too large"):
        load_site_config(path)


def test_problem_list_is_capped(tmp_path: Path) -> None:
    lines = ["version: 1", "creator:", "  name: E", "tabs:"]
    for i in range(40):
        lines += [f"  - id: BAD_{i}", "    title: T", "    blocks: []"]
    path = tmp_path / "site.yaml"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ConfigError) as exc:
        load_site_config(path)
    text = str(exc.value)
    assert text.count("tabs[") == MAX_REPORTED_PROBLEMS
    assert "and 20 more problems" in text
    assert "BAD_" not in text
    assert len(text) < 8000


def test_error_formatting_stops_at_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reach_around.config import models

    calls: list[int] = []
    real = models._dotted

    def counting(loc: tuple[int | str, ...], **kw: Any) -> str:
        calls.append(1)
        return real(loc, **kw)

    monkeypatch.setattr(models, "_dotted", counting)
    lines = ["version: 1", "creator:", "  name: E", "tabs:"]
    for i in range(MAX_TABS):
        lines += [f"  - id: BAD_{i}", "    title: T", "    blocks: []"]
    path = tmp_path / "site.yaml"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(
        ConfigError, match=f"and {MAX_TABS - MAX_REPORTED_PROBLEMS} more problems"
    ):
        load_site_config(path)
    assert len(calls) == MAX_REPORTED_PROBLEMS


def test_error_details_are_requested_without_input_or_context() -> None:
    seen: dict[str, Any] = {}

    class Fake:
        def errors(self, **kwargs: Any) -> list[dict[str, Any]]:
            seen.update(kwargs)
            return [{"loc": ("tabs", 0, "id"), "msg": "Value error, bad"}] * 3

        def error_count(self) -> int:
            return 3

    assert problem_text(Fake()) == "tabs[0].id: bad; " * 2 + "tabs[0].id: bad"  # type: ignore[arg-type]
    assert seen == {
        "include_url": False,
        "include_input": False,
        "include_context": False,
    }


# ---- container shapes that must not slip past the budget (round 3) ----------


def _walk_count(node: object) -> int:
    """Independent node count (values, containers, keys) for the ratio tests."""
    total, stack = 0, [node]
    while stack:
        cur = stack.pop()
        total += 1
        if isinstance(cur, dict):
            total += len(cur)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return total


def _deep(levels: int) -> object:
    node: object = 1
    for _ in range(levels):
        node = [node]
    return node


_SHARED = list(range(500))
_CYCLE: dict[str, Any] = {}
_CYCLE["self"] = _CYCLE


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(tuple({"id": "x"} for _ in range(5)), id="tuple"),
        pytest.param({1, 2, 3}, id="set"),
        pytest.param(frozenset({1}), id="frozenset"),
        pytest.param((i for i in range(3)), id="generator"),
        pytest.param([(1,), (2,)], id="tuple-inside-list"),
        pytest.param({"a": ((),)}, id="nested-tuple"),
        pytest.param(OrderedDict(a=1), id="dict-subclass"),
        pytest.param({1: "non-text key"}, id="non-text-key"),
        pytest.param({("t",): 1}, id="tuple-key"),
        pytest.param(b"bytes", id="bytes"),
        pytest.param(object(), id="object"),
    ],
)
def test_budget_refuses_shapes_the_models_would_expand(shape: object) -> None:
    with pytest.raises(ValueError, match="not allowed|not text"):
        check_config_budget(shape)


def test_tuple_of_tabs_is_refused_with_one_error_not_one_per_tab() -> None:
    # The round 3 reproduction: one tuple node, 100,000 entries inside it.
    bad = tuple({"id": "BAD_", "title": "T", "blocks": ()} for _ in range(100_000))
    with pytest.raises(ValidationError, match="not allowed") as exc:
        SiteConfig.model_validate(site_doc(tabs=bad))
    assert exc.value.error_count() == 1


def test_deep_nesting_refused_at_the_depth_limit() -> None:
    check_config_budget(_deep(MAX_CONFIG_DEPTH - 1))
    with pytest.raises(ValueError, match="levels deep") as exc:
        check_config_budget(_deep(MAX_CONFIG_DEPTH + 1))
    assert "1" not in str(exc.value).replace(str(MAX_CONFIG_DEPTH), "")


def test_very_deep_input_is_refused_without_recursion() -> None:
    with pytest.raises(ValueError, match="levels deep"):
        check_config_budget(_deep(50_000))


def test_wide_list_and_wide_mapping_refused() -> None:
    with pytest.raises(ValueError, match="more than"):
        check_config_budget(list(range(MAX_CONFIG_NODES + 1)))
    with pytest.raises(ValueError, match="more than"):
        check_config_budget({f"k{i}": None for i in range(MAX_CONFIG_NODES + 1)})


def test_alias_expansion_counts_each_reference() -> None:
    # One 500-item list referenced 100 times is 50,000 values once expanded.
    with pytest.raises(ValueError, match="more than"):
        check_config_budget({f"k{i}": _SHARED for i in range(100)})
    check_config_budget({f"k{i}": _SHARED for i in range(10)})


def test_cycle_is_refused_not_looped() -> None:
    with pytest.raises(ValueError, match="more than|levels deep"):
        check_config_budget(_CYCLE)


def test_dates_are_leaf_values_and_models_are_refused() -> None:
    from datetime import date

    check_config_budget({"a": date(2027, 1, 1), "b": None, "c": 1.5, "d": True})
    with pytest.raises(ValueError, match="type that is not allowed"):
        check_config_budget([Link(id="a", title="t", url="https://example.com/")])


def test_model_construct_cannot_hide_a_huge_list_behind_one_node() -> None:
    from reach_around.config.models import Creator

    creator = Creator.model_construct(name="E", socials=[object()] * 1_000_000)
    with pytest.raises(ValidationError, match="type that is not allowed"):
        SiteConfig.model_validate(site_doc(creator=creator))


# ---- the error list is only built when it is short --------------------------


class _Boom:
    """A ValidationError stand-in whose errors() must never be called."""

    def __init__(self, count: int) -> None:
        self.count = count

    def error_count(self) -> int:
        return self.count

    def errors(self, **_: Any) -> list[dict[str, Any]]:
        raise AssertionError("errors() built a list that is over the cap")


def test_problem_text_never_builds_an_oversized_error_list() -> None:
    text = problem_text(_Boom(MAX_MATERIALIZED_PROBLEMS + 1))  # type: ignore[arg-type]
    assert f"{MAX_MATERIALIZED_PROBLEMS + 1} problems" in text
    assert "too many to list" in text
    # At the limit the list is still built and capped at 20 entries.
    with pytest.raises(AssertionError, match="over the cap"):
        problem_text(_Boom(MAX_MATERIALIZED_PROBLEMS))  # type: ignore[arg-type]


_MAX_ERRORS_PER_NODE = 8


def _hostile_docs() -> dict[str, dict[str, Any]]:
    empty_blocks: list[dict[str, Any]] = [{} for _ in range(MAX_BLOCKS_PER_TAB)]
    bad_sections = [
        {"id": f"s{i}", "links": [{} for _ in range(20)]}
        for i in range(MAX_BLOCKS_PER_TAB)
    ]

    def bad_tabs(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # As many tabs as fit in the node budget (the walker would refuse more).
        for count in range(MAX_TABS, 0, -1):
            tabs = [{"id": f"T_{i}", "blocks": list(blocks)} for i in range(count)]
            if _walk_count(site_doc(tabs=tabs)) <= MAX_CONFIG_NODES:
                return tabs
        raise AssertionError("one tab is over the node budget")

    kinds = [
        {"id": "a", "markdown": 1},
        {"id": "b", "src": 1},
        {"id": "c", "coins": 1},
        {"id": "d", "address": 1},
        {"id": "e", "items": 1},
        {"id": "f", "players": 1},
        {"id": "g", "url": 1, "bogus": 1},
    ]
    return {
        "empty-blocks": site_doc(tabs=bad_tabs(empty_blocks)),
        "bad-sections": site_doc(tabs=bad_tabs(bad_sections)),
        "every-kind-bad": site_doc(
            tabs=bad_tabs([kinds[i % 7] for i in range(MAX_BLOCKS_PER_TAB)])
        ),
        "unknown-keys": site_doc(**{f"k{i}": 1 for i in range(9_000)}),
        "bad-socials": site_doc(
            creator={"name": "E", "socials": [{} for _ in range(MAX_SOCIALS)]}
        ),
    }


_GEN_COUNT = 60


def _generated_family() -> dict[str, dict[str, Any]]:
    """Seeded hostile docs over every block tag and many kinds of bad value."""
    import random

    from reach_around.config.models import _BLOCK_TAGS

    rng = random.Random(764)  # noqa: S311 (seeded test data, not security)
    bad_values: list[Any] = [1, None, [], {}, [{}], "", "x" * 5000, {"a": [1, 2]}]
    fields = [
        "id", "title", "url", "markdown", "src", "coins", "address", "items",
        "players", "links", "platform", "kind", "bogus",
    ]  # fmt: skip
    docs: dict[str, dict[str, Any]] = {}
    for n in range(_GEN_COUNT):
        blocks = []
        for _ in range(MAX_BLOCKS_PER_TAB):
            block = {f: rng.choice(bad_values) for f in rng.sample(fields, 5)}
            if rng.random() < 0.8:
                block["kind"] = rng.choice(sorted(_BLOCK_TAGS | {"nope"}))
            blocks.append(block)
        docs[f"gen-{n}"] = site_doc(tabs=[{"id": "T_1", "blocks": blocks}])
    return docs


def _all_hostile() -> dict[str, dict[str, Any]]:
    return {**_hostile_docs(), **_generated_family()}


@pytest.mark.parametrize("name", sorted(_all_hostile()))
def test_worst_case_error_count_is_bounded_by_the_node_budget(name: str) -> None:
    # Measured, not enforced. pydantic builds every error before anything can
    # slice the list, so the work is (node budget, enforced) x (errors per node,
    # a property of the schema). This asserts a hard ceiling on the hand-built
    # worst shapes plus a seeded generated family over every block tag, so a
    # schema change that raises the factor fails here. Production enforces the
    # node budget and the 1,000-entry materialization cap in problem_text only.
    doc = _all_hostile()[name]
    nodes = _walk_count(doc)
    assert nodes <= MAX_CONFIG_NODES
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate(doc)
    assert 0 < exc.value.error_count() <= nodes * _MAX_ERRORS_PER_NODE
    assert exc.value.error_count() <= MAX_CONFIG_NODES * _MAX_ERRORS_PER_NODE
    assert len(problem_text(exc.value)) < 8000


def test_hostile_load_reports_a_count_not_a_list(tmp_path: Path) -> None:
    lines = ["version: 1", "creator:", "  name: E", "tabs:"]
    for i in range(MAX_TABS):
        lines += [f"  - id: BAD_{i}", "    blocks:"]
        lines += ["      - {}"] * MAX_BLOCKS_PER_TAB
    path = tmp_path / "site.yaml"
    path.write_text("\n".join(lines) + "\n")
    assert path.stat().st_size < MAX_CONFIG_BYTES
    with pytest.raises(ConfigError, match="too many to list") as exc:
        load_site_config(path)
    assert "BAD_" not in str(exc.value)
    assert len(str(exc.value)) < 2000


# ---- the duplicate-label message holds no platform ---------------------------


def test_duplicate_label_message_is_fixed_text_for_every_platform() -> None:
    for slug in sorted(KIT_PLATFORM_SLUGS):
        pair = [manual(platform=slug), manual(platform=slug, label="followers")]
        with pytest.raises(ValidationError) as exc:
            KitConfig.model_validate(kit_doc(pair))
        msgs = [e["msg"] for e in exc.value.errors(include_input=False)]
        assert len(msgs) == 1
        assert slug not in msgs[0]
        assert "more than once" in msgs[0]


def test_metric_platform_is_a_closed_set() -> None:
    # The proof that no free-form platform text can reach the duplicate check:
    # an unknown platform is refused by the field itself, with fixed text.
    with pytest.raises(ValidationError) as exc:
        PlatformMetric.model_validate(manual(platform="CANARY-PLATFORM"))
    msgs = [e["msg"] for e in exc.value.errors(include_input=False)]
    assert msgs and all("CANARY" not in m for m in msgs)


# ---- error locations never echo the file's own keys --------------------------

_HOSTILE_KEYS = [
    "SECRET_API_KEY_abc123",
    "k" * 100_000,
    "bad\x00key\x07\r\nINJECT",
    "\x1b[31mred\x1b[0m",
    "line1\nline2",
    "café‮",
]


def _printable_ascii(text: str) -> bool:
    return all(c.isascii() and c.isprintable() for c in text)


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
def test_extra_key_at_the_root_is_not_echoed(key: str) -> None:
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate(site_doc(**{key: 1}))
    text = problem_text(exc.value)
    assert "<unknown field>" in text
    assert key[:8] not in text
    assert _printable_ascii(text), repr(text)
    assert len(text) < 200


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
def test_extra_key_nested_is_not_echoed(key: str) -> None:
    doc = site_doc(creator={"name": "E", key: 1})
    doc["tabs"][0]["blocks"] = [{**link(0), key: 1}]
    with pytest.raises(ValidationError) as exc:
        SiteConfig.model_validate(doc)
    text = problem_text(exc.value)
    assert text.count("<unknown field>") == 2
    assert key[:8] not in text
    assert _printable_ascii(text), repr(text)


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
def test_any_path_segment_is_escaped_and_cut(key: str) -> None:
    from reach_around.config.models import MAX_LOC_SEGMENT_CHARS, _dotted

    out = _dotted((key, 3, "name"))
    assert _printable_ascii(out), repr(out)
    assert len(out) <= MAX_LOC_SEGMENT_CHARS + len("...") + len("[3].name")


def test_long_paths_show_a_few_segments_and_a_count() -> None:
    from reach_around.config.models import MAX_LOC_SEGMENTS, _dotted

    out = _dotted(tuple(f"s{i}" for i in range(30)))
    assert out.count(".") == MAX_LOC_SEGMENTS - 1 + out.count("...")
    assert f"+{30 - MAX_LOC_SEGMENTS} more path segments" in out
