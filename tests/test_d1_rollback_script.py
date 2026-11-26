# SPDX-License-Identifier: Apache-2.0
"""scripts/check-d1-migration-rollback.py stays safe and in sync (#907).

The script itself is a hand-run check that starts the pinned Wrangler; it is not
run here (the test guard forbids tests from starting Wrangler). These tests only
read the script: it exists, compiles, does nothing at import, refuses under a CI
marker, never names the remote flag, and drops the same triggers as migration
0022. They need neither worker/node_modules nor Node.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-d1-migration-rollback.py"
MIGRATION = ROOT / "worker" / "migrations" / "0022_kit_floor_guard_recreate.sql"


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the script with every process start forbidden."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError(
            "the script started a process or touched a process API at import"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    spec = importlib.util.spec_from_file_location("d1_rollback_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists_and_compiles() -> None:
    assert SCRIPT.is_file()
    compile(SCRIPT.read_text(), str(SCRIPT), "exec")


def test_import_has_no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(monkeypatch)
    assert callable(module.main)
    assert module.__name__ == "d1_rollback_check"  # not run as __main__


@pytest.mark.parametrize(
    "marker", ["CI", "GITHUB_ACTIONS", "BUILD_ID", "TF_BUILD", "JENKINS_URL"]
)
def test_refused_under_a_ci_marker(
    marker: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(monkeypatch)
    calls: list[Any] = []

    def fake(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))
        raise AssertionError("the runner must not be called under a CI marker")

    code = module.main(env={marker: "1"}, runner=fake)
    out = capsys.readouterr().out
    assert code == 2
    assert f"REFUSED: {marker} is set" in out
    assert calls == []


def test_owner_run_flag_lifts_only_the_ci_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(monkeypatch)

    def no_process(*_a: Any, **_k: Any) -> None:
        raise OSError("no process may start in this test")

    module.main(env={"CI": "1", "RA_ROLLBACK_CHECK_OWNER_RUN": "1"}, runner=no_process)
    assert "is set" not in capsys.readouterr().out


def test_script_text_is_local_only() -> None:
    text = SCRIPT.read_text()
    assert "--local" in text
    assert "--remote" not in text
    assert "shell=True" not in text
    assert "CLOUDFLARE" not in text


def test_trigger_names_match_migration_0022(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(monkeypatch)
    real = re.findall(
        r"^DROP TRIGGER IF EXISTS (\w+);", MIGRATION.read_text(), flags=re.MULTILINE
    )
    assert real, "migration 0022 no longer drops triggers: update the script"
    built = re.findall(
        r"^DROP TRIGGER IF EXISTS (\w+);", module.DROPS, flags=re.MULTILINE
    )
    assert built == real
    assert set(module.TRIGGERS) == set(real)
