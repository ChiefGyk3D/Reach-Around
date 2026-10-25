# SPDX-License-Identifier: Apache-2.0
"""The asset-slot validation hook of ``editing.apply``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from reach_around import editing
from reach_around.editing import EditError, apply, set_value

ROOT = Path(__file__).resolve().parents[1]
MINIMAL = ROOT / "examples" / "instance-variants" / "minimal" / "site.yaml"


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "site.yaml"
    path.write_text(MINIMAL.read_text())
    return path


def run(cfg: Path, key: str, value: str, **kw: Any) -> editing.Result:
    return apply(
        cfg,
        lambda text, raw: set_value(text, raw, key=key, value=value),
        dry_run=kw.pop("dry_run", False),
        backup=False,
        force_path=True,
        **kw,
    )


def test_default_asset_keys_is_empty() -> None:
    assert editing.ASSET_KEYS == frozenset()


def test_hook_not_called_without_asset_keys(cfg: Path) -> None:
    calls: list[tuple[str, Any]] = []
    run(
        cfg, "creator.name", "New Name", validate_slot=lambda k, v: calls.append((k, v))
    )
    assert calls == []


def test_hook_called_for_a_changed_asset_key(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editing, "ASSET_KEYS", frozenset({"creator.name"}))
    calls: list[tuple[str, Any]] = []
    run(
        cfg, "creator.name", "New Name", validate_slot=lambda k, v: calls.append((k, v))
    )
    assert calls == [("creator.name", "New Name")]
    assert "New Name" in cfg.read_text()


def test_hook_not_called_for_other_keys(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editing, "ASSET_KEYS", frozenset({"creator.name"}))
    calls: list[Any] = []
    run(cfg, "theme.mode", "dark", validate_slot=lambda k, v: calls.append(k))
    assert calls == []


def test_refusal_leaves_file_untouched(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editing, "ASSET_KEYS", frozenset({"creator.name"}))
    before = cfg.read_bytes()

    def refuse(key: str, value: Any) -> None:
        raise EditError(f"{key}: no such image")

    with pytest.raises(EditError, match="no such image"):
        run(cfg, "creator.name", "New Name", validate_slot=refuse)
    assert cfg.read_bytes() == before
    assert [p.name for p in cfg.parent.iterdir()] == ["site.yaml"]  # no temp files


def test_refusal_also_applies_to_dry_run(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editing, "ASSET_KEYS", frozenset({"creator.name"}))

    def refuse(key: str, value: Any) -> None:
        raise EditError("nope")

    with pytest.raises(EditError, match="nope"):
        run(cfg, "creator.name", "New Name", dry_run=True, validate_slot=refuse)


def test_asset_key_missing_in_candidate_is_skipped(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editing, "ASSET_KEYS", frozenset({"creator.avatar", "a.b.c"}))
    calls: list[Any] = []
    run(cfg, "creator.name", "New Name", validate_slot=lambda k, v: calls.append(k))
    assert calls == []


def test_no_hook_means_no_check(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editing, "ASSET_KEYS", frozenset({"creator.name"}))
    assert run(cfg, "creator.name", "New Name").written
