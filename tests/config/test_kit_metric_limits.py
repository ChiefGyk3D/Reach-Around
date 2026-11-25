# SPDX-License-Identifier: Apache-2.0
"""Media-kit metric definitions and config size limits (#764).

Every name here is synthetic (fake brand, example.com).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.kit import KitConfig
from reach_around.config.loader import ConfigError
from reach_around.config.models import (
    MAX_BLOCKS_PER_TAB,
    MAX_CONFIG_BYTES,
    MAX_CONFIG_NODES,
    MAX_LINKS_PER_SECTION,
    MAX_REPORTED_PROBLEMS,
    MAX_SOCIALS,
    MAX_TABS,
    Link,
    check_config_budget,
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
    assert label.strip() not in str(exc.value) or label.strip() == "Followers"


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

    def counting(loc: tuple[int | str, ...]) -> str:
        calls.append(1)
        return real(loc)

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
    from reach_around.config.models import problem_text

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
