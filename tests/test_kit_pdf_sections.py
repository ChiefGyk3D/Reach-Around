# SPDX-License-Identifier: Apache-2.0
"""Platform cards in both PDFs (kit v2 slice 3, #628). Synthetic data only."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from kitpdfharness import TODAY, build_instance, fake_renderer, kit_doc, make_config
from reach_around.config.trust import LEGEND_HEADING, LEGEND_SENTENCES
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


# --- the trust legend heading is printed in capitals (#890) --------------------


def test_the_legend_heading_is_checked_without_case_but_not_without_letters(
    tmp_path: Path,
) -> None:
    config, out = public_pdf(tmp_path)
    kit = config.kit
    assert kit is not None
    text = "\n".join(extract_pages(out.read_bytes()))
    assert LEGEND_HEADING in text

    def run(value: str) -> None:
        check_text(kit, value, public=True, config=config, check_days=(TODAY,))

    def swap(value: str) -> str:
        return text.replace(LEGEND_HEADING, value)

    run(text)  # original case
    run(swap(LEGEND_HEADING.upper()))  # as printed
    # a line wrap between words collapses to one space
    run(swap(LEGEND_HEADING.replace(" ", "\n", 2)))
    run(swap(LEGEND_HEADING.upper().replace(" ", "  \n ")))
    for bad in (
        "",  # absent
        LEGEND_HEADING.replace("read", "raed"),  # altered
        LEGEND_HEADING[:-8],  # truncated
        LEGEND_HEADING.replace("read", "re ad"),  # a space inside a word
        LEGEND_HEADING.upper().replace("READ", "RE\nAD"),  # a break inside a word
        LEGEND_HEADING.replace("to read", "toread"),  # a space removed
        LEGEND_HEADING.upper().replace("HOW TO", "HOWTO"),
        LEGEND_HEADING.replace("s", "ſ"),  # long s
        LEGEND_HEADING.replace("numbers", "numberß"),  # sharp s
        LEGEND_HEADING.upper().replace("S", "ẞ"),  # capital sharp s
        LEGEND_HEADING.replace("o", "ο"),  # Greek omicron lookalike
        LEGEND_HEADING.replace("e", "е"),  # Cyrillic e lookalike
        LEGEND_HEADING.upper().replace("T", "İ"),  # dotted capital I
        LEGEND_HEADING.replace("r", "ı"),  # dotless i
        LEGEND_HEADING.replace("How", "Hﬁow"),  # fi ligature
        LEGEND_HEADING.replace("read", "rea​d"),  # zero-width space
        LEGEND_HEADING.upper().replace("A", "Ａ"),  # fullwidth A
    ):
        assert bad not in (LEGEND_HEADING, LEGEND_HEADING.upper())
        with pytest.raises(PdfError, match="trust legend heading"):
            run(swap(bad))


def test_the_legend_heading_must_come_before_the_legend_sentences(
    tmp_path: Path,
) -> None:
    # The check is text order over the whole document: it is not a page, card
    # or visibility check, which the text layer cannot show (#890).
    config, out = public_pdf(tmp_path)
    kit = config.kit
    assert kit is not None
    text = "\n".join(extract_pages(out.read_bytes()))
    moved = text.replace(LEGEND_HEADING, "") + "\n" + LEGEND_HEADING.upper()
    with pytest.raises(PdfError, match="trust legend heading"):
        check_text(kit, moved, public=True, config=config, check_days=(TODAY,))


def test_the_legend_sentences_still_keep_their_case(tmp_path: Path) -> None:
    config, out = public_pdf(tmp_path)
    kit = config.kit
    assert kit is not None
    text = "\n".join(extract_pages(out.read_bytes()))
    sentence = LEGEND_SENTENCES[0]
    with pytest.raises(PdfError, match="as written"):
        check_text(
            kit,
            text.replace(sentence, sentence.upper()),
            public=True,
            config=config,
            check_days=(TODAY,),
        )
