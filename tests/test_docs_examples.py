# SPDX-License-Identifier: Apache-2.0
"""Executable documentation: the config examples in the docs must load."""

from __future__ import annotations

import re
from pathlib import Path

from reach_around.config.models import load_site_config

ROOT = Path(__file__).resolve().parent.parent
_YAML_BLOCK = re.compile(r"^```yaml\n(.*?)^```$", re.DOTALL | re.MULTILINE)


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
