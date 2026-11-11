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
