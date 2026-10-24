# SPDX-License-Identifier: Apache-2.0
"""Browser check: background_dim keeps white icons readable. Needs --run-visual."""

from __future__ import annotations

import base64
import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import Browser, Page, sync_playwright

from reach_around.config import load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)

# Worst case: a pure white page background behind the white icons.
BRIGHT_BACKGROUND = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
    " preserveAspectRatio='xMidYMid slice'>"
    "<rect width='100' height='100' fill='#ffffff'/></svg>"
)
SAMPLE_LIGHTEST = """async (b64) => {
  const img = new Image();
  img.src = 'data:image/png;base64,' + b64;
  await img.decode();
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height).data;
  let hi = 0;
  for (let i = 0; i < d.length; i += 4) {
    hi = Math.max(hi, d[i], d[i + 1], d[i + 2]);
  }
  return hi;
}"""


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        binary = os.environ.get("RA_CHROMIUM")
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=binary or None
        )
        yield instance
        instance.close()


def _lum(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _open(browser: Browser, url: str, width: int) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(url)
    return page


def icon_contrast(browser: Browser, tmp_path: Path, dim: float, width: int) -> float:
    """Contrast of a white social icon against the lightest pixel behind it."""
    raw = yaml.safe_load(EXAMPLE.read_text())
    raw["theme"] = {"background": "media/bg.svg", "background_dim": dim}
    raw["creator"]["avatar"] = "media/bg.svg"
    raw["creator"]["favicon"] = "media/bg.svg"
    work = tmp_path / f"inst-{dim}-{width}"
    (work / "media").mkdir(parents=True)
    (work / "media" / "bg.svg").write_text(BRIGHT_BACKGROUND)
    (work / "site.yaml").write_text(yaml.safe_dump(raw))
    out = work / "out"
    config = load_site_config(work / "site.yaml")
    build_site(config, out, today=TODAY, assets_dir=work)
    handler = functools.partial(_Quiet, directory=str(out))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page = _open(browser, f"http://127.0.0.1:{server.server_address[1]}/", width)
        color = page.evaluate(
            "() => getComputedStyle(document.querySelector('.social')).color"
        )
        assert color == "rgb(255, 255, 255)"
        page.add_style_tag(content=".socials { visibility: hidden !important; }")
        box = page.evaluate(
            "() => { const r = document.querySelector('.social .icon')"
            ".getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }"
        )
        png = page.screenshot(
            clip={"x": box[0], "y": box[1], "width": box[2], "height": box[3]}
        )
        probe = browser.new_context().new_page()
        lightest = probe.evaluate(SAMPLE_LIGHTEST, base64.b64encode(png).decode())
        probe.context.close()
        page.context.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return (1.0 + 0.05) / (_lum(int(lightest)) + 0.05)


@pytest.mark.parametrize("width", [1440, 390])
def test_background_dim_keeps_white_icons_readable_on_a_bright_background(
    browser: Browser, tmp_path: Path, width: int
) -> None:
    dimmed = icon_contrast(browser, tmp_path, 0.6, width)
    assert dimmed >= 3.0, dimmed
    # Negative control: with no dimming the white icon sits on white.
    flat = icon_contrast(browser, tmp_path, 0.0, width)
    assert flat < 3.0, flat
