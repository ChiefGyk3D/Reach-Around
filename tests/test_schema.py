# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import pytest

from reach_around.cli import main
from reach_around.schema import render_schema, schema_matches

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "docs" / "site.schema.json"
REGENERATE = "reach-around schema --out docs/site.schema.json"


def test_committed_schema_is_current() -> None:
    committed = SCHEMA_FILE.read_text(encoding="utf-8")
    assert schema_matches(committed), (
        "docs/site.schema.json is out of date with the config models. "
        f"Regenerate it with: {REGENERATE}"
    )


def test_guard_bites_on_a_stale_schema() -> None:
    committed = SCHEMA_FILE.read_text(encoding="utf-8")
    assert '"tabs"' in committed
    assert not schema_matches(committed.replace('"tabs"', '"tabz"', 1))
    assert not schema_matches("")


def test_schema_is_deterministic_and_sorted() -> None:
    first = render_schema()
    assert first == render_schema()
    data = json.loads(first)
    expected = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert first == expected
    assert data["$schema"].startswith("https://json-schema.org/")
    assert data["additionalProperties"] is False
    assert "tabs" in data["properties"]


def test_schema_command_writes_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0
    assert capsys.readouterr().out == render_schema()


def test_schema_command_writes_a_file(tmp_path: Path) -> None:
    out = tmp_path / "s.json"
    assert main(["schema", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == render_schema()
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_schema_command_reports_an_unwritable_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["schema", "--out", str(tmp_path / "missing" / "s.json")])
    assert code == 1
    assert "Traceback" not in capsys.readouterr().err
