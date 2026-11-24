# SPDX-License-Identifier: Apache-2.0
"""Platform cards in both PDFs (kit v2 slice 3, #628). Synthetic data only."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kitpdfharness import TODAY, build_instance, fake_renderer, kit_doc, make_config
from reach_around.kit.pdf import PdfError, build_public_kit_pdf, check_text
from reach_around.kit.pdftext import extract_pages

CREATED = date(2027, 3, 10)


def doc() -> dict[str, Any]:
    found = kit_doc()
    found["sections"] = [
        {"platform": "twitch", "account": "example_twitch"},
        {"platform": "kick", "account": "example_kick"},
    ]
    return found


def flat(path: Path) -> str:
    return "".join(" ".join(extract_pages(path.read_bytes())).split())


def public_pdf(tmp_path: Path) -> tuple[Any, Path]:
    config = make_config(doc())
    _, site, _ = build_instance(tmp_path, config)
    out = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    return config, out


def test_the_public_pdf_prints_both_cards_the_same_way(tmp_path: Path) -> None:
    _, out = public_pdf(tmp_path)
    text = flat(out)
    assert "Twitchexample_twitch" in text
    assert "1,200Followers" in text
    assert "Kickexample_kick" in text
    assert "Nonumbersyet" in text


def test_the_text_check_passes_on_the_real_text_and_fails_without_a_card_line(
    tmp_path: Path,
) -> None:
    config, out = public_pdf(tmp_path)
    kit = config.kit
    assert kit is not None
    text = "\n".join(extract_pages(out.read_bytes()))
    check_text(kit, text, public=True, config=config, check_days=(TODAY,))
    assert "No numbers yet" in text
    with pytest.raises(PdfError, match="platform card"):
        check_text(
            kit,
            text.replace("No numbers yet", ""),
            public=True,
            config=config,
            check_days=(TODAY,),
        )
    with pytest.raises(PdfError, match="platform card"):
        check_text(
            kit,
            text.replace("example_kick", ""),
            public=True,
            config=config,
            check_days=(TODAY,),
        )
