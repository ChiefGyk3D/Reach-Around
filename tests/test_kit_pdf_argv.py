# SPDX-License-Identifier: Apache-2.0
"""The browser command line the kit PDF renderer builds (#706).

A stand-in executable records its arguments, so this runs without a Chromium.
Recent Chromium builds tag every PDF by default; the kit scrub refuses tagged
output, so the renderer must ask the browser not to tag (``--disable-pdf-tagging``).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from reach_around.kit.pdf import chromium_renderer


def _recording_browser(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "argv.txt"
    script = tmp_path / "fake-chromium"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"open({str(log)!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
        "out = [a for a in sys.argv if a.startswith('--print-to-pdf=')][0]\n"
        "open(out.split('=', 1)[1], 'wb').write(b'%PDF-1.4\\n')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log


def test_the_browser_is_told_not_to_tag_the_pdf(tmp_path: Path) -> None:
    script, log = _recording_browser(tmp_path)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    chromium_renderer(script)("<p>x</p>", work)
    args = log.read_text(encoding="utf-8").splitlines()
    assert "--disable-pdf-tagging" in args
    assert any(a.startswith("--print-to-pdf=") for a in args)
