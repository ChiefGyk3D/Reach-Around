# SPDX-License-Identifier: Apache-2.0
"""Public placeholder assets stay equal to their reviewed inventory."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = Path("scripts/check-public-assets.py")
INVENTORY = Path("public-assets.json")


def run(root: Path, inventory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed local interpreter and script
        [
            sys.executable,
            "-I",
            str(REPO / CHECK),
            "--root",
            str(root),
            "--inventory",
            str(inventory),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def copy_reviewed_assets(root: Path) -> Path:
    inventory = json.loads((REPO / INVENTORY).read_text(encoding="utf-8"))
    for relative in inventory["roots"]:
        (root / relative).mkdir(parents=True)
    for relative in inventory["files"]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / relative, target)
    copied_inventory = root / INVENTORY
    shutil.copy(REPO / INVENTORY, copied_inventory)
    return copied_inventory


def test_the_public_asset_inventory_matches_the_repository() -> None:
    result = run(REPO, REPO / INVENTORY)
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_altered_placeholder_asset_is_refused(tmp_path: Path) -> None:
    inventory = json.loads((REPO / INVENTORY).read_text(encoding="utf-8"))
    relative = Path(next(iter(inventory["files"])))
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    shutil.copy(REPO / relative, target)
    copied_inventory = tmp_path / INVENTORY
    shutil.copy(REPO / INVENTORY, copied_inventory)
    target.write_bytes(target.read_bytes() + b"private replacement")

    result = run(tmp_path, copied_inventory)

    assert result.returncode == 1
    assert "does not match its reviewed public placeholder" in result.stdout


def test_an_unreviewed_public_font_is_refused(tmp_path: Path) -> None:
    copied_inventory = copy_reviewed_assets(tmp_path)
    private_font = tmp_path / "examples/instance/fonts/private.ttf"
    private_font.parent.mkdir(parents=True, exist_ok=True)
    private_font.write_bytes(b"private font")

    result = run(tmp_path, copied_inventory)

    assert result.returncode == 1
    assert (
        "examples/instance/fonts/private.ttf: unreviewed public asset" in result.stdout
    )


def test_an_altered_public_crypto_config_is_refused(tmp_path: Path) -> None:
    copied_inventory = copy_reviewed_assets(tmp_path)
    config = tmp_path / "examples/crypto/site.yaml"
    shutil.copy(REPO / "examples/crypto/site.yaml", config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "bc1qexampleexampleexampleexampleexample",
            "bc1qa-real-wallet-address-replacement",
        ),
        encoding="utf-8",
    )

    result = run(tmp_path, copied_inventory)

    assert result.returncode == 1
    assert "does not match its reviewed public placeholder" in result.stdout


def test_an_unreviewed_hidden_asset_is_refused(tmp_path: Path) -> None:
    copied_inventory = copy_reviewed_assets(tmp_path)
    hidden_asset = tmp_path / "examples/instance/media/.avatar.svg"
    hidden_asset.write_text("<svg/>", encoding="utf-8")

    result = run(tmp_path, copied_inventory)

    assert result.returncode == 1
    assert (
        "examples/instance/media/.avatar.svg: unreviewed public asset" in result.stdout
    )
