# SPDX-License-Identifier: Apache-2.0
"""Media-kit metric definitions and config size limits (#764).

Every name here is synthetic (fake brand, example.com).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.kit import KitConfig
from reach_around.config.loader import ConfigError
from reach_around.config.models import (
    MAX_BLOCKS_PER_TAB,
    MAX_LINKS_PER_SECTION,
    MAX_SOCIALS,
    MAX_TABS,
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
    with pytest.raises(ValidationError, match="one counts metric per platform"):
        KitConfig.model_validate(kit_doc(both))
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


def test_oversized_file_refused_without_echo(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_bytes(b"# CANARY-VALUE\n" * 100_000)
    with pytest.raises(ConfigError) as exc:
        load_site_config(path)
    assert "CANARY-VALUE" not in str(exc.value)


def test_problem_list_is_capped(tmp_path: Path) -> None:
    lines = ["version: 1", "creator:", "  name: E", "tabs:"]
    for i in range(40):
        lines += [f"  - id: BAD_{i}", "    title: T", "    blocks: []"]
    path = tmp_path / "site.yaml"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ConfigError) as exc:
        load_site_config(path)
    assert "more problems" in str(exc.value)
    assert len(str(exc.value)) < 8000
