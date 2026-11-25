# SPDX-License-Identifier: Apache-2.0
"""The kit PDF renders dark, with the screen's own tokens (#687, decision D9).

The contrast figures come from the real token values in ``tokens.css`` and
``site.css``, not from numbers typed into the test.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import reach_around
from kitpdfharness import build_instance
from reach_around.kit.pdf import chromium_renderer, find_chromium
from reach_around.kit.stage import PRINT_CSS, stage_page

STATIC = Path(reach_around.__file__).parent / "site" / "static"
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
SITE = (STATIC / "site.css").read_text(encoding="utf-8")


def dark_half(value: str) -> str:
    """The dark half of ``light-dark(a, b)``, else the value itself."""
    match = re.fullmatch(r"light-dark\(\s*[^,]+,\s*(#[0-9A-Fa-f]{6})\s*\)", value)
    return match.group(1) if match else value


def token(name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*([^;]+);", TOKENS + SITE)
    assert match, f"{name} is not defined by the screen's stylesheets"
    value = match.group(1).strip()
    # follow one level of var(--x) to the value the screen resolves to
    ref = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)
    return dark_half(token(ref.group(1)) if ref else value)


def rgb(hexcolor: str) -> tuple[float, float, float]:
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", hexcolor), hexcolor
    return tuple(int(hexcolor[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def luminance(c: tuple[float, float, float]) -> float:
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(rgb(a)), luminance(rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(fg: str, bg: str, pct: float) -> str:
    f, b = rgb(fg), rgb(bg)
    out = [round((x * pct + y * (1 - pct)) * 255) for x, y in zip(f, b, strict=True)]
    return "#{:02X}{:02X}{:02X}".format(*out)


def test_the_print_page_is_dark_not_white() -> None:
    assert "color-scheme:dark" in PRINT_CSS
    assert "color-scheme:light" not in PRINT_CSS
    for light in ("#fff", "#111", "#0645ad", "white"):
        assert light not in PRINT_CSS.lower()
    assert "html,body{background:var(--ra-color-bg)!important" in PRINT_CSS
    # the page margins are dark too, not a white frame
    assert "@page{size:Letter;margin:0.6in;background:var(--ra-color-bg)}" in PRINT_CSS


def test_the_print_page_uses_only_tokens_the_screen_defines() -> None:
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", PRINT_CSS))
    assert used, "the print sheet should read the screen's tokens"
    for name in used:
        assert re.search(rf"{re.escape(name)}:", TOKENS + SITE), name
    # no literal color is invented: every color in the sheet is a token reference
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", PRINT_CSS)


def test_dark_fills_are_forced_into_the_pdf() -> None:
    """Without this Chromium drops backgrounds when it prints."""
    assert "print-color-adjust:exact" in PRINT_CSS
    assert "-webkit-print-color-adjust:exact" in PRINT_CSS


def test_body_text_contrast_from_the_real_tokens() -> None:
    bg, surface = token("--ra-color-bg"), token("--ra-color-surface")
    text = token("--ra-color-text")
    muted = mix(text, bg, 0.72)  # --ra-color-muted is color-mix(text 72%, bg)
    assert "--ra-color-muted: color-mix(in srgb, var(--ra-color-text) 72%" in SITE
    link = token("--ra-brand-amber")
    rule = token("--ra-brand-gray")
    for fg in (text, muted, link):
        assert contrast(fg, bg) >= 4.5, (fg, bg)
        assert contrast(fg, surface) >= 4.5, (fg, surface)
    assert contrast(rule, bg) >= 3.0  # table rules


def test_contrast_helper_bites() -> None:
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast("#333333", "#0E0E0E") < 3.0


def test_staged_page_carries_the_dark_sheet_and_still_has_its_text(
    tmp_path: Path,
) -> None:
    _, site, gated = build_instance(tmp_path)
    html = stage_page(gated, site).html
    assert html.count(PRINT_CSS) == 1
    assert "--ra-color-bg" in html  # the tokens travel with the page
    assert "Exampleland" in html


@pytest.mark.visual
def test_real_chromium_page_is_dark_edge_to_edge(tmp_path: Path) -> None:
    """Render the staged page and read the pixels: margins and body are dark,
    the text is still real text. Run with ``pytest --run-visual``."""
    chromium = find_chromium(None)
    pdftoppm, pdftotext = shutil.which("pdftoppm"), shutil.which("pdftotext")
    if chromium is None or pdftoppm is None or pdftotext is None:
        pytest.skip("needs Chromium (RA_CHROMIUM), pdftoppm and pdftotext")
    _, site, gated = build_instance(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(chromium_renderer(chromium)(stage_page(gated, site).html, work))
    ppm = tmp_path / "page"
    subprocess.run(  # noqa: S603 - fixed argv, no shell, synthetic file
        [pdftoppm, "-r", "20", "-f", "1", "-l", "1", str(pdf), str(ppm)], check=True
    )
    raw = next(tmp_path.glob("page-*.ppm")).read_bytes()
    match = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", raw)
    assert match is not None
    width, height, data = int(match[1]), int(match[2]), raw[match.end() :]

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        i = (y * width + x) * 3
        return data[i], data[i + 1], data[i + 2]

    want = tuple(round(c * 255) for c in rgb(token("--ra-color-bg")))
    for x, y in ((1, 1), (width - 2, height - 2), (width // 2, height - 2)):
        assert pixel(x, y) == want, (x, y)
    text = subprocess.run(  # noqa: S603 - fixed argv, no shell, synthetic file
        [pdftotext, str(pdf), "-"], check=True, capture_output=True, text=True
    ).stdout
    assert "Example Creator media kit" in text
