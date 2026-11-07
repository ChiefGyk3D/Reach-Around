# SPDX-License-Identifier: Apache-2.0
"""The editor refuses a config path that crosses a symlink (T-26)."""

from __future__ import annotations

from pathlib import Path

import pytest

from editkit import CONFIG as CONFIG_TEXT
from reach_around import editing
from reach_around.editor.server import EditorError, EditorSession


def make_real(tmp_path: Path) -> Path:
    real = tmp_path / "real"
    real.mkdir()
    cfg = real / "site.yaml"
    cfg.write_text(CONFIG_TEXT, encoding="utf-8")
    return cfg


def test_a_regular_config_is_accepted(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    session = EditorSession(cfg)
    assert session.config == cfg.resolve()


def test_a_symlinked_config_is_refused_and_untouched(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    link = tmp_path / "link.yaml"
    link.symlink_to(cfg)
    with pytest.raises(EditorError, match="unsafe config path"):
        EditorSession(link)
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEXT


def test_a_symlinked_parent_directory_is_refused(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    (tmp_path / "alias").symlink_to(cfg.parent, target_is_directory=True)
    with pytest.raises(EditorError, match="unsafe config path"):
        EditorSession(tmp_path / "alias" / "site.yaml")


def test_a_dangling_or_missing_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EditorError):
        EditorSession(tmp_path / "nope.yaml")


def test_a_write_refuses_a_config_swapped_for_a_symlink(tmp_path: Path) -> None:
    cfg = make_real(tmp_path)
    victim = tmp_path / "victim.yaml"
    victim.write_text(CONFIG_TEXT, encoding="utf-8")
    cfg.unlink()
    cfg.symlink_to(victim)

    def compute(text: str, raw: dict[str, object]) -> tuple[str, dict[str, object]]:
        return text, dict(raw)

    with pytest.raises(editing.EditError, match="unsafe config path"):
        editing.apply(
            cfg, compute, dry_run=False, backup=True, force_path=True, no_symlinks=True
        )
    assert victim.read_text(encoding="utf-8") == CONFIG_TEXT
    assert not (tmp_path / "victim.yaml.bak").exists()


def test_a_swap_between_the_check_and_the_rename_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_real(tmp_path)
    victim = tmp_path / "victim.yaml"
    victim.write_text(CONFIG_TEXT, encoding="utf-8")
    moved = tmp_path / "moved"

    def swap(path: Path) -> None:
        cfg.parent.rename(moved)
        cfg.parent.symlink_to(moved, target_is_directory=True)

    monkeypatch.setattr(editing, "_before_replace", swap)

    def compute(text: str, raw: dict[str, object]) -> tuple[str, dict[str, object]]:
        return text + "# x\n", dict(raw)

    with pytest.raises(editing.EditError):
        editing.apply(
            cfg, compute, dry_run=False, backup=False, force_path=True, no_symlinks=True
        )
    assert (moved / "site.yaml").read_text(encoding="utf-8") == CONFIG_TEXT
