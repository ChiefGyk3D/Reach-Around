# SPDX-License-Identifier: Apache-2.0
"""The instance-file copy path: one allowlist for every slot, and fail closed."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from assetpaths import asset_file
from reach_around.config import SiteConfig
from reach_around.site import assets
from reach_around.site.assets import collect_assets
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG = b"\x89PNG\r\n\x1a\nsynthetic" + IEND


def make(**parts: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "C", **parts.pop("creator", {})},
        "tabs": [
            {
                "id": "a",
                "title": "A",
                "blocks": parts.pop("blocks", [])
                or [{"id": "one", "title": "One", "url": "https://example.com/1"}],
            }
        ],
    }
    if parts:
        data["theme"] = parts
    return SiteConfig.model_validate(data)


def avatar_config() -> SiteConfig:
    return make(creator={"avatar": "media/a.png"})


def run(cfg: SiteConfig, work: Path, out: Path) -> None:
    build_site(cfg, out, today=TODAY, assets_dir=work)


# --- one allowlist for every slot ----------------------------------------------


@pytest.mark.parametrize("name", ["site.yaml", "notes.txt", "key.pem", "a"])
def test_image_block_cannot_publish_a_non_image_file(tmp_path: Path, name: str) -> None:
    work = tmp_path / "inst"
    work.mkdir()
    (work / name).write_text("SECRET=1")
    cfg = make(blocks=[{"id": "pic", "src": name, "alt": "x"}])
    with pytest.raises(ValueError, match=r"tabs\[0\]\.blocks\[0\]\.src.*image"):
        run(cfg, work, tmp_path / "out")
    assert not (tmp_path / "out" / name).exists()


def test_an_image_block_with_an_image_extension_still_works(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    work.mkdir()
    (work / "p.png").write_bytes(PNG)
    cfg = make(blocks=[{"id": "pic", "src": "p.png", "alt": "x"}])
    run(cfg, work, tmp_path / "out")
    assert asset_file(tmp_path / "out", "p.png").read_bytes() == PNG


def _forge(cfg: SiteConfig, key: str, value: str) -> SiteConfig:
    """Return ``cfg`` with one slot set past validation, as a bug could."""
    if key.startswith("creator."):
        creator = cfg.creator.model_copy(update={key.split(".")[1]: value})
        return cfg.model_copy(update={"creator": creator})
    if key.startswith("theme.fonts."):
        fonts = cfg.theme.fonts.model_copy(update={key.split(".")[2]: value})
        theme = cfg.theme.model_copy(update={"fonts": fonts})
        return cfg.model_copy(update={"theme": theme})
    theme = cfg.theme.model_copy(update={key.split(".")[1]: value})
    return cfg.model_copy(update={"theme": theme})


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("creator.avatar", "secret.yaml"),
        ("creator.favicon", "secret.yaml"),
        ("theme.mascot", "secret.yaml"),
        ("theme.background", "secret.yaml"),
        ("theme.fonts.body", "secret.yaml"),
        ("theme.fonts.heading", "a.png"),
        ("creator.avatar", "font.woff2"),
    ],
)
def test_every_slot_goes_through_the_same_check(
    tmp_path: Path, key: str, bad: str
) -> None:
    work = tmp_path / "inst"
    work.mkdir()
    for name in ("secret.yaml", "a.png", "font.woff2"):
        (work / name).write_bytes(PNG)
    cfg = _forge(make(), key, bad)
    # Fonts are found by their own extension, so a non-font name there is
    # simply not a local file; every other slot must be refused by name.
    if key.startswith("theme.fonts.") and not bad.endswith(".woff2"):
        assert collect_assets(cfg, work) == {}
        return
    with pytest.raises(ValueError, match=key.replace(".", r"\.")):
        collect_assets(cfg, work)


# --- fail closed ---------------------------------------------------------------


def test_intermediate_directory_symlink_is_refused(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    work.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.png").write_bytes(PNG)
    (work / "media").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run(avatar_config(), work, tmp_path / "out")


def test_final_component_symlink_is_refused(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    (work / "media").mkdir(parents=True)
    (tmp_path / "x.png").write_bytes(PNG)
    (work / "media" / "a.png").symlink_to(tmp_path / "x.png")
    with pytest.raises(ValueError, match="symlink"):
        run(avatar_config(), work, tmp_path / "out")


def test_a_symlinked_instance_root_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "media").mkdir(parents=True)
    (real / "media" / "a.png").write_bytes(PNG)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run(avatar_config(), link, tmp_path / "out")


def test_confinement_does_not_depend_on_proc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_exists = os.path.exists
    monkeypatch.setattr(
        os.path,
        "exists",
        lambda p: False if str(p).startswith("/proc") else real_exists(p),
    )
    work = tmp_path / "inst"
    work.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.png").write_bytes(PNG)
    (work / "media").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run(avatar_config(), work, tmp_path / "out")
    (work / "media").unlink()
    (work / "media").mkdir()
    (work / "media" / "a.png").write_bytes(PNG)
    run(avatar_config(), work, tmp_path / "out2")
    assert asset_file(tmp_path / "out2", "media/a.png").read_bytes() == PNG


def test_a_fifo_is_refused_without_hanging(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    (work / "media").mkdir(parents=True)
    os.mkfifo(work / "media" / "a.png")
    with pytest.raises(ValueError, match="regular file"):
        run(avatar_config(), work, tmp_path / "out")


def test_the_size_cap_is_enforced_on_bytes_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "inst"
    (work / "media").mkdir(parents=True)
    (work / "media" / "a.png").write_bytes(PNG[:8] + b"x" * 2048 + IEND)
    monkeypatch.setattr(assets, "MAX_ASSET_BYTES", 1024)
    with pytest.raises(ValueError, match="larger than"):
        run(avatar_config(), work, tmp_path / "out")
    (work / "media" / "a.png").write_bytes(PNG[:8] + b"x" * (1024 - len(PNG)) + IEND)
    run(avatar_config(), work, tmp_path / "out2")


def test_a_file_just_over_8_mib_is_refused(tmp_path: Path) -> None:
    work = tmp_path / "inst"
    (work / "media").mkdir(parents=True)
    (work / "media" / "a.png").write_bytes(PNG[:8] + b"x" * (8 * 1024 * 1024) + IEND)
    with pytest.raises(ValueError, match="8 MiB"):
        run(avatar_config(), work, tmp_path / "out")
