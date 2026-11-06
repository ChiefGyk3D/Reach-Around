# SPDX-License-Identifier: Apache-2.0
"""The token drift check and the brand-dark generator (issue #226).

The repo files must agree (hash pin, vendored tokens, generated theme), and the
check must fail when any one of them is changed on its own.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
LOCK = Path("vendor/brand/tokens.lock.json")
TOKENS = Path("vendor/brand/tokens.json")
THEME = Path("examples/themes/brand-dark.yaml")
GEN = Path("scripts/gen-brand-theme.py")
CHECK = Path("scripts/check-token-drift")
COPIED = (LOCK, TOKENS, THEME, GEN)


def load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, str(path)))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load(REPO / CHECK, "check_token_drift")
generator = load(REPO / GEN, "gen_brand_theme")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A scratch copy of the files the check reads."""
    for rel in COPIED:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    return tmp_path


def run(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = checker.main(["--root", str(root)])
    return code, capsys.readouterr().out


def repin(root: Path) -> None:
    lock = json.loads((root / LOCK).read_text())
    lock["sha256"] = hashlib.sha256((root / TOKENS).read_bytes()).hexdigest()
    (root / LOCK).write_text(json.dumps(lock))


def test_repo_is_in_sync(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(REPO, capsys)
    assert code == 0, out


def test_lock_records_source_commit_and_hash() -> None:
    lock = json.loads((REPO / LOCK).read_text())
    assert len(lock["source"]["commit"]) == 40
    assert lock["source"]["repo"] == "ChiefGyk3D/no-php-no-cry"
    assert lock["sha256"] == hashlib.sha256((REPO / TOKENS).read_bytes()).hexdigest()


def test_theme_maps_tokens_to_ra_colors() -> None:
    tokens = generator.flat_tokens(REPO / TOKENS)
    text = (REPO / THEME).read_text()
    assert text.count('"#') == len(generator.COLOR_MAP)
    for token, key in generator.COLOR_MAP:
        assert f'  {key}: "{tokens[token]}"\n' in text
    assert f"radius: {tokens['radius']}\n" in text


def test_edited_theme_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = root / THEME
    path.write_text(path.read_text().replace("#3A3530", "#333333"))
    code, out = run(root, capsys)
    assert code == 1
    assert "drift" in out


def test_edited_tokens_fail_on_hash(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = root / TOKENS
    path.write_text(path.read_text().replace("#FBB03B", "#FBB03C", 1))
    code, out = run(root, capsys)
    assert code == 1
    assert "hash mismatch" in out


def test_repinned_tokens_still_fail_on_theme_drift(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = root / TOKENS
    path.write_text(path.read_text().replace('"#0F0F0F"', '"#0F0F10"'))
    repin(root)
    code, out = run(root, capsys)
    assert code == 1
    assert "hash mismatch" not in out
    assert "drift" in out


def test_wrong_pin_fails(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lock = json.loads((root / LOCK).read_text())
    lock["sha256"] = "0" * 64
    (root / LOCK).write_text(json.dumps(lock))
    code, out = run(root, capsys)
    assert code == 1
    assert "hash mismatch" in out


def test_missing_tokens_and_bad_lock_fail(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (root / TOKENS).unlink()
    code, out = run(root, capsys)
    assert code == 1
    assert "missing" in out
    (root / LOCK).write_text("{")
    code, out = run(root, capsys)
    assert code == 1
    assert "cannot read lock" in out


def test_lock_path_cannot_escape_root(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = json.loads((root / LOCK).read_text())
    lock["vendored"] = "../outside.json"
    (root / LOCK).write_text(json.dumps(lock))
    code, out = run(root, capsys)
    assert code == 1
    assert "inside the root" in out


def test_generator_check_flag(tmp_path: Path) -> None:
    out = tmp_path / "theme.yaml"
    source = REPO / TOKENS
    assert generator.main(["--source", str(source), "--out", str(out)]) == 0
    assert out.read_text() == (REPO / THEME).read_text()
    assert generator.main(["--check", "--source", str(source), "--out", str(out)]) == 0
    out.write_text(out.read_text() + "# x\n")
    assert generator.main(["--check", "--source", str(source), "--out", str(out)]) == 1


def test_generator_rejects_bad_token(tmp_path: Path) -> None:
    data = json.loads((REPO / TOKENS).read_text())
    for group in data["groups"]:
        if "accent" in group["tokens"]:
            group["tokens"]["accent"] = "orange"
    bad = tmp_path / "tokens.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(generator.TokenError):
        generator.render(bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"repo": 1},
        {"path": ["not", "a", "string"]},
        {"commit": 1234567890123456789012345678901234567890},
        {"commit": ["a" * 40]},
        {"repo": ""},
        {"path": ""},
    ],
    ids=[
        "repo-int",
        "path-list",
        "commit-number",
        "commit-list",
        "repo-empty",
        "path-empty",
    ],
)
def test_source_fields_must_be_strings(
    root: Path, capsys: pytest.CaptureFixture[str], bad: dict[str, object]
) -> None:
    lock = json.loads((root / LOCK).read_text())
    lock["source"].update(bad)
    (root / LOCK).write_text(json.dumps(lock))
    code, out = run(root, capsys)
    assert code == 1
    assert "lock: source" in out
