# SPDX-License-Identifier: Apache-2.0
"""``theme.fonts.license`` publishes a license text beside a custom font."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from assetpaths import asset_file
from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
FONT = b"wOF2" + b"\x00\x01\x00\x00" + (20).to_bytes(4, "big") + b"\x00" * 8
LICENSE = b"Copyright 2026 The Orbitron Project Authors\nSIL OPEN FONT LICENSE 1.1\n"
LICENSE_KEY = {"heading": "fonts/orbitron.woff2", "license": "fonts/OFL.txt"}


def make(fonts: dict[str, Any]) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "C"},
            "theme": {"fonts": fonts},
            "tabs": [
                {
                    "id": "a",
                    "title": "A",
                    "blocks": [
                        {"id": "one", "title": "One", "url": "https://x.example/"}
                    ],
                }
            ],
        }
    )


def inst(tmp_path: Path, license_data: bytes = LICENSE) -> Path:
    work = tmp_path / "inst"
    (work / "fonts").mkdir(parents=True)
    (work / "fonts" / "orbitron.woff2").write_bytes(FONT)
    (work / "fonts" / "OFL.txt").write_bytes(license_data)
    return work


def test_the_license_text_is_published_with_the_font(tmp_path: Path) -> None:
    out = tmp_path / "out"
    build_site(make(LICENSE_KEY), out, today=TODAY, assets_dir=inst(tmp_path))
    published = asset_file(out, "fonts/OFL.txt")
    assert published.read_bytes() == LICENSE
    assert b"SIL OPEN FONT LICENSE" in published.read_bytes()
    assert published.parent == asset_file(out, "fonts/orbitron.woff2").parent


def test_without_the_key_no_license_file_is_published(tmp_path: Path) -> None:
    cfg = make({"heading": "fonts/orbitron.woff2"})
    out = tmp_path / "out"
    build_site(cfg, out, today=TODAY, assets_dir=inst(tmp_path))
    files = [p for p in out.rglob("*") if p.is_file()]
    assert files  # something was built
    assert not any(p.read_bytes() == LICENSE for p in files)


@pytest.mark.parametrize(
    "value",
    ["fonts/OFL.pdf", "fonts/../OFL.txt", "/etc/passwd.txt", "https://x.example/a.txt"],
)
def test_a_bad_license_path_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        make({"heading": "fonts/orbitron.woff2", "license": value})


def test_a_license_needs_a_font_file() -> None:
    with pytest.raises(ValidationError, match="font file"):
        make({"body": "Georgia, serif", "license": "fonts/OFL.txt"})


@pytest.mark.parametrize("data", [b"abc\x00def", b"\xff\xfe\x00bad", b""])
def test_a_license_that_is_not_text_is_refused(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(ValueError, match=r"theme\.fonts\.license"):
        build_site(
            make(LICENSE_KEY),
            tmp_path / "out",
            today=TODAY,
            assets_dir=inst(tmp_path, data),
        )


def test_an_oversized_license_is_refused(tmp_path: Path) -> None:
    big = b"a" * (256 * 1024 + 1)
    with pytest.raises(ValueError, match=r"theme\.fonts\.license"):
        build_site(
            make(LICENSE_KEY),
            tmp_path / "out",
            today=TODAY,
            assets_dir=inst(tmp_path, big),
        )
