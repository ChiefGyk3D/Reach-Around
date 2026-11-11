# SPDX-License-Identifier: Apache-2.0
"""Executable documentation: the config examples in the docs must load."""

from __future__ import annotations

import re
from pathlib import Path

from reach_around.config.models import load_site_config

ROOT = Path(__file__).resolve().parent.parent
_YAML_BLOCK = re.compile(r"^```yaml\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def operations_kit_refresh_section() -> str:
    text = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    start = text.index("## Every 30 days: refresh the media kit")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def first_yaml_block(doc: Path) -> str:
    match = _YAML_BLOCK.search(doc.read_text(encoding="utf-8"))
    assert match is not None, f"no fenced yaml block in {doc}"
    return match.group(1)


def test_config_doc_example_validates(tmp_path: Path) -> None:
    sample = tmp_path / "site.yaml"
    sample.write_text(first_yaml_block(ROOT / "docs" / "config.md"), encoding="utf-8")
    config = load_site_config(sample)
    assert config.tabs


def test_customizing_doc_example_validates(tmp_path: Path) -> None:
    sample = tmp_path / "site.yaml"
    sample.write_text(
        first_yaml_block(ROOT / "docs" / "customizing.md"), encoding="utf-8"
    )
    config = load_site_config(sample)
    assert config.tabs


def test_kit_doc_example_validates(tmp_path: Path) -> None:
    text = (ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    section = text[text.index("## `kit`") : text.index("## `numbers`")]
    match = _YAML_BLOCK.search(section)
    assert match is not None
    body = (
        "version: 1\ncreator: {name: Example}\ntabs:\n  - id: links\n    title: Links\n"
        "    blocks:\n      - {id: home, title: Home, url: 'https://example.com/'}\n"
        + match.group(1)
    )
    sample = tmp_path / "site.yaml"
    sample.write_text(body, encoding="utf-8")
    config = load_site_config(sample)
    assert config.kit is not None and config.kit.rates


def test_kit_guide_example_validates(tmp_path: Path) -> None:
    body = (
        "version: 1\ncreator: {name: Example}\ntabs:\n  - id: links\n    title: Links\n"
        "    blocks:\n      - {id: home, title: Home, url: 'https://example.com/'}\n"
        + first_yaml_block(ROOT / "docs" / "kit.md")
    )
    sample = tmp_path / "site.yaml"
    sample.write_text(body, encoding="utf-8")
    config = load_site_config(sample)
    assert config.kit is not None and config.kit.has_gated_content
    assert config.kit.turnstile_site_key is not None


def test_operations_kit_refresh_commands_are_in_run_order() -> None:
    section = operations_kit_refresh_section()
    commands = [
        "reach-around counts history-add --config site.yaml",
        "reach-around counts refresh --config site.yaml",
        "reach-around build --config site.yaml --out site-out --gated-out gated-out",
        "reach-around counts stale --config site.yaml",
        "reach-around check --config site.yaml --numbers numbers.json --production",
        "~/private/deploy.sh ~/private/deploy.settings",
        "reach-around kit pdf --config site.yaml --site-out site-out",
        "reach-around kit pdf --public --config site.yaml --site-out site-out",
        "SELECT COUNT(*) AS lead_count FROM leads",
        "SELECT value FROM kit_meta WHERE key = 'last_purge_ts'",
    ]
    positions = [section.index(command) for command in commands]
    assert positions == sorted(positions)


def test_operations_kit_refresh_stops_on_stale_numbers_and_links_dependencies() -> None:
    section = operations_kit_refresh_section()
    assert "Do not publish while either command reports a stale number" in section
    assert "[hand-kept number hygiene](../CHANGELOG.md#unreleased) (#389)" in section
    assert (
        "[analytics operations runbook](#every-week-about-20-minutes) (#403)" in section
    )
