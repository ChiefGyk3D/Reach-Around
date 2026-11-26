# SPDX-License-Identifier: Apache-2.0
"""The rollback procedure in the operations doc names the real triggers (#907)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DROP = re.compile(r"DROP TRIGGER IF EXISTS (\w+);")


def _migration_trigger_names() -> list[str]:
    (path,) = sorted((ROOT / "worker" / "migrations").glob("0022_*.sql"))
    return _DROP.findall(path.read_text(encoding="utf-8"))


def _section() -> str:
    text = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    start = text.index("## Migration rollback check")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_migration_0022_drops_two_triggers() -> None:
    assert len(_migration_trigger_names()) == 2


def test_procedure_names_the_triggers_of_migration_0022() -> None:
    section = _section()
    for name in _migration_trigger_names():
        assert f"DROP TRIGGER IF EXISTS {name};" in section
        assert f"`{name}`" in section
