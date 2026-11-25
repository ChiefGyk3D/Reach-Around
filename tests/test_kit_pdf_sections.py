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


# --- the live block (#629) ----------------------------------------------------


def live_doc() -> dict[str, Any]:
    found = kit_doc()
    found["live"] = [
        {
            "platform": platform,
            "period_start": "2027-01-04",
            "period_end": "2027-01-31",
            "as_of": "2027-03-01",
            "avg_ccv": base,
            "peak_ccv": base * 3,
            "hours_watched": base * 20,
            "unique_chatters": base + 7,
            "followers_gained": base * 5,
            "hours_streamed_per_week": 18,
        }
        for platform, base in (("twitch", 41), ("kick", 29))
    ]
    return found


def test_the_public_pdf_prints_the_live_block_and_the_check_bites(
    tmp_path: Path,
) -> None:
    config = make_config(live_doc())
    _, site, _ = build_instance(tmp_path, config)
    out = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    text = "\n".join(extract_pages(out.read_bytes()))
    kit = config.kit
    assert kit is not None
    assert "Averageconcurrentviewers41" in flat(out)
    assert "Averageconcurrentviewers29" in flat(out)
    check_text(kit, text, public=True, config=config, check_days=(TODAY,))
    with pytest.raises(PdfError, match="live block"):
        check_text(
            kit,
            text.replace("Average concurrent viewers", ""),
            public=True,
            config=config,
            check_days=(TODAY,),
        )


# --- the channel block (#633) -------------------------------------------------


def test_the_public_pdf_prints_the_channel_cards_and_the_check_bites(
    tmp_path: Path,
) -> None:
    found = kit_doc()
    found["sections"] = [{"platform": "peertube"}, {"platform": "newsletter"}]
    found["channels"] = [
        {
            "platform": "peertube",
            "url": "https://video.example/c/example",
            "as_of": "2027-03-08",
            "subscribers": 340,
            "unique_viewers": 8200,
        },
        {"platform": "newsletter", "as_of": "2027-03-08", "delivered": 2300},
    ]
    config = make_config(found)
    _, site, _ = build_instance(tmp_path, config)
    out = build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit-public.pdf",
        created=CREATED,
        renderer=fake_renderer,
    ).path
    kit = config.kit
    assert kit is not None
    text = "\n".join(extract_pages(out.read_bytes()))
    flat_text = "".join(text.split())
    for line in (
        "Counts are as reported by the instance.",
        "https://video.example/c/example",
        "Not provided by Newsletter: opens.",
        "Not provided: Subscribers, Email clicks (30 days).",
    ):
        assert "".join(line.split()) in flat_text, line
        with pytest.raises(PdfError, match="platform card"):
            check_text(
                kit,
                text.replace(line, ""),
                public=True,
                config=config,
                check_days=(TODAY,),
            )
    check_text(kit, text, public=True, config=config, check_days=(TODAY,))
