# SPDX-License-Identifier: Apache-2.0
"""Asset path length limits: a fingerprinted name must still fit the file system.

The build adds ``.`` plus the hash to a file's leaf name, and Linux limits a
leaf to 255 bytes. So a configured leaf may be at most 234 bytes (counted in
encoded bytes, not characters) and a whole path at most 1024 bytes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from assetpaths import asset, png
from reach_around.config import SiteConfig
from reach_around.config.pathlimits import (
    FINGERPRINT_RESERVE,
    MAX_LEAF_BYTES,
    MAX_PATH_BYTES,
    check_path_bytes,
)
from reach_around.site.build import build_site
from reach_around.site.fingerprint import HASH_LEN

TODAY = date(2027, 3, 10)


def leaf(size: int, ext: str = ".png") -> str:
    return "a" * (size - len(ext)) + ext


def config(**creator: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "creator": {"name": "Example Creator", **creator},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "one", "title": "One", "url": "https://e.com/1"}],
            }
        ],
    }


def test_the_numbers_follow_the_fingerprint_length() -> None:
    assert FINGERPRINT_RESERVE == 1 + HASH_LEN
    assert MAX_LEAF_BYTES == 255 - FINGERPRINT_RESERVE == 234
    assert MAX_PATH_BYTES == 1024


def test_the_leaf_boundary_is_234_bytes_for_ascii() -> None:
    check_path_bytes(leaf(MAX_LEAF_BYTES), "creator.avatar")
    with pytest.raises(ValueError, match="creator.avatar.*235 bytes"):
        check_path_bytes(leaf(MAX_LEAF_BYTES + 1), "creator.avatar")


def test_multibyte_names_count_bytes_not_characters() -> None:
    name = "é" * 118  # 236 bytes, 118 characters
    assert len(name) < MAX_LEAF_BYTES < len(name.encode())
    with pytest.raises(ValueError, match="236 bytes"):
        check_path_bytes(name, "creator.avatar")
    check_path_bytes("é" * 116, "creator.avatar")  # exactly 232 bytes
    # three-byte characters, still under the character count
    with pytest.raises(ValueError, match="bytes"):
        check_path_bytes("€" * 80, "creator.avatar")


def test_only_the_leaf_is_limited_to_234_but_the_whole_path_to_1024() -> None:
    folders = "/".join("d" * 200 for _ in range(4))  # 803 bytes
    check_path_bytes(f"{folders}/{leaf(100)}", "x")
    with pytest.raises(ValueError, match="path is .* bytes"):
        check_path_bytes(f"{folders}/{'e' * 200}/{leaf(100)}", "x")


def test_a_path_of_exactly_1024_bytes_is_accepted() -> None:
    parts = ["d" * 200] * 4 + ["f" * 100 + ".png"]
    path = "/".join(parts)
    padded = path[:-4] + "x" * (MAX_PATH_BYTES - len(path)) + ".png"
    assert len(padded.encode()) == MAX_PATH_BYTES
    check_path_bytes(padded, "x")
    with pytest.raises(ValueError, match="path is 1025 bytes"):
        check_path_bytes("p" + padded, "x")


@pytest.mark.parametrize(
    "build",
    [
        lambda p: config(avatar=p),
        lambda p: config(favicon=p),
        lambda p: {**config(), "theme": {"background": p}},
        lambda p: {**config(), "theme": {"mascot": p}},
    ],
)
def test_every_image_slot_enforces_the_leaf_limit(build: Any) -> None:
    SiteConfig.model_validate(build(leaf(MAX_LEAF_BYTES)))
    with pytest.raises(ValidationError, match="235 bytes"):
        SiteConfig.model_validate(build(leaf(MAX_LEAF_BYTES + 1)))


def test_the_other_slots_enforce_it_too() -> None:
    long_name = f"img/{leaf(MAX_LEAF_BYTES + 1)}"
    ok_name = f"img/{leaf(MAX_LEAF_BYTES)}"
    for tabs in (
        [{"id": "pic", "src": long_name, "alt": "A"}],
        [{"id": "l", "title": "L", "url": "https://e.com/1", "image": long_name}],
    ):
        data = config()
        data["tabs"][0]["blocks"] = tabs
        with pytest.raises(ValidationError, match="bytes"):
            SiteConfig.model_validate(data)
    data = config()
    data["tabs"][0]["blocks"] = [{"id": "pic", "src": ok_name, "alt": "A"}]
    SiteConfig.model_validate(data)
    font = {
        **config(),
        "theme": {"fonts": {"body": leaf(MAX_LEAF_BYTES + 1, ".woff2")}},
    }
    with pytest.raises(ValidationError, match="bytes"):
        SiteConfig.model_validate(font)


def test_a_boundary_length_leaf_builds_without_an_os_error(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    (instance / "img").mkdir(parents=True)
    name = f"img/{leaf(MAX_LEAF_BYTES)}"
    (instance / name).write_bytes(png("edge"))
    data = config(avatar=name)
    data["tabs"][0]["blocks"] = [{"id": "pic", "src": name, "alt": "Edge"}]
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY, assets_dir=instance)
    built = asset(out, name)
    assert len(Path(built).name.encode()) == 255
    assert (out / built).is_file()
