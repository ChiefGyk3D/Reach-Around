# SPDX-License-Identifier: Apache-2.0
"""scripts/mutation-check.py: the gate mutation harness (issue #270).

Hermetic: no node, no vitest. The real list is checked against the real source
(every edit still applies exactly once, every control marker in the kit and
alert code has a mutation), and the harness is run against a tiny fake tree
with the vitest call replaced, to prove it never writes the original tree and
always removes its copy.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mutation-check.py"
LIST = ROOT / "scripts" / "mutation-check.toml"
MARKER = re.compile(r"CONTROL:([a-z0-9-]+)")


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutation_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mc = load()


def entries() -> list[dict[str, Any]]:
    return list(mc.load_list(LIST)[1])


def test_every_edit_in_the_list_applies_exactly_once() -> None:
    assert mc.validate(entries(), ROOT / "worker") == []


def test_every_control_marker_in_the_kit_and_alert_code_has_a_mutation() -> None:
    found: set[str] = set()
    for sub in ("kit", "alerts"):
        for path in sorted((ROOT / "worker" / "src" / sub).glob("*.ts")):
            found.update(MARKER.findall(path.read_text(encoding="utf-8")))
    assert found, "no control markers found: the scan itself is broken"
    claimed = {c for m in entries() for c in m.get("controls", [])}
    assert sorted(found - claimed) == []
    assert sorted(claimed - found) == [], (
        "a mutation names a marker that does not exist"
    )


def test_a_control_named_by_a_mutation_is_in_the_file_the_mutation_edits() -> None:
    for m in entries():
        for control in m.get("controls", []):
            text = (ROOT / "worker" / m["file"]).read_text(encoding="utf-8")
            near = [
                ln
                for i, ln in enumerate(text.splitlines())
                if f"CONTROL:{control}" in ln
            ]
            assert near, f"{m['id']}: no CONTROL:{control} marker in {m['file']}"


def test_every_equivalent_claim_has_a_reason_and_every_test_file_exists() -> None:
    default_tests, muts = mc.load_list(LIST)
    for t in default_tests:
        assert (ROOT / "worker" / t).is_file(), t
    for m in muts:
        for t in m.get("tests", default_tests):
            assert (ROOT / "worker" / t).is_file(), f"{m['id']}: {t}"
        if "equivalent" in m:
            assert len(m["equivalent"]) >= 20


def test_suite_files_named_by_the_list_include_the_abuse_suite() -> None:
    default_tests, _ = mc.load_list(LIST)
    assert any(t.startswith("test/abuse/") for t in default_tests)


# ---- entry checks ----


def good() -> dict[str, Any]:
    return {
        "id": "some-mutation",
        "file": "src/kit/x.ts",
        "find": "a",
        "replace": "b",
        "what": "w",
    }


def test_check_entry_accepts_a_good_entry() -> None:
    assert mc.check_entry(good(), set()) == []


@pytest.mark.parametrize(
    "change",
    [
        {"id": "X"},
        {"id": "ab"},
        {"find": ""},
        {"replace": "a"},
        {"what": ""},
        {"file": "../x.ts"},
        {"file": "/etc/passwd"},
        {"file": "test/kit/x.test.ts"},
        {"equivalent": "short"},
    ],
)
def test_check_entry_refuses_bad_entries(change: dict[str, Any]) -> None:
    assert mc.check_entry({**good(), **change}, set()) != []


def test_check_entry_refuses_a_duplicate_id() -> None:
    seen: set[str] = set()
    assert mc.check_entry(good(), seen) == []
    assert any("duplicate" in p for p in mc.check_entry(good(), seen))


def test_validate_refuses_a_find_text_that_matches_zero_or_two_times(
    tmp_path: Path,
) -> None:
    f = tmp_path / "src" / "kit" / "x.ts"
    f.parent.mkdir(parents=True)
    f.write_text("aa b", encoding="utf-8")
    assert any("2 times" in p for p in mc.validate([good()], tmp_path))
    f.write_text("zzz", encoding="utf-8")
    assert any("0 times" in p for p in mc.validate([good()], tmp_path))
    f.write_text("a", encoding="utf-8")
    assert mc.validate([good()], tmp_path) == []


def test_classify() -> None:
    assert mc.classify(0, {"numFailedTests": 0, "numTotalTests": 5}) == "SURVIVED"
    assert mc.classify(1, {"numFailedTests": 2, "numTotalTests": 5}) == "KILLED"
    # a run that broke (no report, a timeout, a failed suite with no failed
    # test) is never a kill
    assert mc.classify(1, {}) == "ERROR"
    assert mc.classify(124, {}) == "ERROR"
    assert mc.classify(1, {"numFailedTests": 0, "numTotalTests": 0}) == "ERROR"
    assert mc.classify(0, {}) == "ERROR"


# ---- the run itself, against a fake tree ----


def tree_hash(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def make_tree(base: Path) -> Path:
    """A minimal repository: worker/ with one source file and the fixture."""
    w = base / "repo" / "worker"
    (w / "src" / "kit").mkdir(parents=True)
    (w / "test").mkdir()
    (w / "migrations").mkdir()
    (w / "node_modules").mkdir()
    (w / "src" / "kit" / "gate.ts").write_text(
        "export const limit = 5;\nexport const open = false;\n", encoding="utf-8"
    )
    (w / "test" / "gate.test.ts").write_text("// test\n", encoding="utf-8")
    for name in ("package.json", "tsconfig.json", "vitest.config.ts", "wrangler.toml"):
        (w / name).write_text("{}\n", encoding="utf-8")
    fx = base / "repo" / "tests" / "fixtures"
    fx.mkdir(parents=True)
    (fx / "email_vectors.json").write_text("[]\n", encoding="utf-8")
    (base / "list.toml").write_text(
        """
default_tests = ["test/gate.test.ts"]

[[mutation]]
id = "limit-raised"
file = "src/kit/gate.ts"
what = "raises the limit"
find = "limit = 5"
replace = "limit = 500"

[[mutation]]
id = "opened-up"
file = "src/kit/gate.ts"
what = "opens the gate"
find = "open = false"
replace = "open = true"
equivalent = "the fake test never reads this value, so no test can see it"
""",
        encoding="utf-8",
    )
    return base / "repo"


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: Any,
    extra: list[str] | None = None,
) -> int:
    repo = tmp_path / "repo"
    if not repo.exists():
        make_tree(tmp_path)
    monkeypatch.setattr(mc, "REPO", repo.resolve())
    monkeypatch.setattr(mc, "WORKER", (repo / "worker").resolve())
    monkeypatch.setattr(mc, "run_vitest", fake)
    work = tmp_path / "work"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutation-check",
            "--list",
            str(tmp_path / "list.toml"),
            "--workdir",
            str(work),
        ]
        + (extra or []),
    )
    return int(mc.main())


def test_the_run_never_writes_the_original_tree_and_removes_its_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[str] = []

    def fake(copy: Path, tests: list[str], workers: int) -> tuple[int, dict[str, Any]]:
        text = (copy / "src" / "kit" / "gate.ts").read_text(encoding="utf-8")
        seen.append(text)
        failed = 1 if "limit = 500" in text else 0
        return (1 if failed else 0), {"numFailedTests": failed, "numTotalTests": 3}

    make_tree(tmp_path)
    before = tree_hash(tmp_path / "repo")
    code = run_main(monkeypatch, tmp_path, fake)
    out = capsys.readouterr().out
    assert code == 0  # one killed, one survivor that is marked equivalent
    assert tree_hash(tmp_path / "repo") == before
    # the baseline ran unmutated, then each mutation ran with exactly its own edit
    assert "limit = 5;" in seen[0] and "open = false" in seen[0]
    assert "limit = 500" in seen[1] and "open = false" in seen[1]
    assert "open = true" in seen[2] and "limit = 5;" in seen[2]
    assert "| limit-raised" in out and "KILLED" in out
    assert "SURVIVED (equivalent)" in out
    assert list((tmp_path / "work").iterdir()) == []  # the temporary copy is gone


def test_an_unexplained_survivor_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake(copy: Path, tests: list[str], workers: int) -> tuple[int, dict[str, Any]]:
        return 0, {"numFailedTests": 0, "numTotalTests": 3}

    code = run_main(monkeypatch, tmp_path, fake)
    out = capsys.readouterr().out
    assert code == 1
    assert "need attention" in out and "SURVIVED: limit-raised" in out


def test_a_failing_baseline_stops_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def fake(copy: Path, tests: list[str], workers: int) -> tuple[int, dict[str, Any]]:
        calls.append(1)
        return 1, {"numFailedTests": 1, "numTotalTests": 3}

    assert run_main(monkeypatch, tmp_path, fake) == 2
    assert calls == [1]
    assert list((tmp_path / "work").iterdir()) == []


def test_a_broken_run_is_an_error_not_a_kill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    n = {"i": 0}

    def fake(copy: Path, tests: list[str], workers: int) -> tuple[int, dict[str, Any]]:
        n["i"] += 1
        if n["i"] == 1:
            return 0, {"numFailedTests": 0, "numTotalTests": 3}
        return 1, {}

    assert run_main(monkeypatch, tmp_path, fake) == 1
    assert "ERROR: limit-raised" in capsys.readouterr().out


def test_skip_test_removes_a_file_from_every_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran: list[list[str]] = []

    def fake(copy: Path, tests: list[str], workers: int) -> tuple[int, dict[str, Any]]:
        ran.append(tests)
        return 0, {"numFailedTests": 0, "numTotalTests": 3}

    run_main(monkeypatch, tmp_path, fake, ["--skip-test", "test/gate.test.ts"])
    assert ran and all(t == [] for t in ran)


def test_a_missing_node_modules_is_reported_not_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake(copy: Path, tests: list[str], workers: int) -> tuple[int, dict[str, Any]]:
        raise AssertionError("must not run")

    make_tree(tmp_path)
    (tmp_path / "repo" / "worker" / "node_modules").rmdir()
    monkeypatch.setattr(mc, "REPO", (tmp_path / "repo").resolve())
    monkeypatch.setattr(mc, "WORKER", (tmp_path / "repo" / "worker").resolve())
    monkeypatch.setattr(mc, "run_vitest", fake)
    monkeypatch.setattr(
        sys, "argv", ["mutation-check", "--list", str(tmp_path / "list.toml")]
    )
    assert mc.main() == 2
    assert "npm ci" in capsys.readouterr().err
