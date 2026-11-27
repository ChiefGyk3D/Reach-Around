# SPDX-License-Identifier: Apache-2.0
"""The UnitFileTooLarge recovery steps in docs/config.md match the code (#945)."""

from __future__ import annotations

import re
from pathlib import Path

from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts.fediverse_commit import (
    MAX_DIR_ENTRIES,
    MAX_STAGED_CANDIDATES,
    UnitFileTooLarge,
)
from reach_around.counts.fediverse_store import MAX_SNAPSHOT_BYTES

CONFIG = Path(__file__).resolve().parent.parent / "docs" / "config.md"


def _section() -> str:
    text = " ".join(CONFIG.read_text(encoding="utf-8").split())
    start = text.index("Recovering from `UnitFileTooLarge`.")
    return text[start : text.index("The build feeds [`kit.fediverse`]")]


def test_section_names_the_word_and_the_constants_of_the_code() -> None:
    doc = _section()
    assert UnitFileTooLarge.__name__ in doc
    assert f"{MAX_DIR_ENTRIES:,} entries" in doc
    assert f"more than {MAX_STAGED_CANDIDATES} staged" in doc
    assert f"{MAX_SNAPSHOT_BYTES // 1024} KiB" in doc
    assert f"{MAX_NUMBERS_BYTES // 1024} KiB" in doc


def test_section_names_the_safe_files_and_the_untouchable_ones() -> None:
    doc = _section()
    assert "`.numbers.fediverse.json.`" in doc
    assert "`.staged` or `.tmp`" in doc
    assert "16 hex digits" in doc
    for kept in ("`numbers.json`", "`numbers.fediverse.json`", "`.numbers.json.unit`"):
        assert kept in doc
    assert "Never delete or edit" in doc
    assert "`.numbers.json.unit.bad`" in doc


def test_section_gives_the_order_and_the_confirmation() -> None:
    doc = _section()
    positions = [doc.index(f"{n}. ") for n in range(1, 6)]
    assert positions == sorted(positions)
    assert "every file is kept as evidence" in doc
    assert "`committed: false`" in doc
    assert "exits 0" in doc and "`committed: true`" in doc


def test_listed_grep_pattern_matches_the_names_the_code_sweeps() -> None:
    doc = _section()
    grep = re.search(r"grep -E '([^']+)'", doc)
    assert grep is not None
    pattern = re.compile(grep.group(1))
    assert pattern.match(".numbers.fediverse.json.0123456789abcdef.staged")
    assert pattern.match(".numbers.fediverse.json.0123456789abcdef.tmp")
    assert not pattern.match("numbers.fediverse.json")
    assert not pattern.match(".numbers.json.unit")
