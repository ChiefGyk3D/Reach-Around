# SPDX-License-Identifier: Apache-2.0
"""scripts/check-wiki-a11y.py: accessibility and dark-mode structure of the wiki.

Every fixture is synthetic. Each rule has one failing fixture and one passing
fixture; the revert proof removes the rule from the registry and requires the
failing fixture to pass, so a deleted rule turns its test red.
"""

from __future__ import annotations

import importlib.util
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-wiki-a11y.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_wiki_a11y", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_wiki_a11y"] = module
    spec.loader.exec_module(module)
    return module


mod = load()


def png(color_type: int, extra: bytes = b"") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + extra
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


def run(
    tmp_path: Path, name: str, text: str, rules: dict[str, object] | None = None
) -> list[tuple[str, int, str]]:
    wiki = tmp_path / "docs" / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    page = wiki / name
    page.write_text(text, encoding="utf-8")
    found: list[tuple[str, int, str]] = mod.check(tmp_path, [page], rules)
    return found


GOOD = "# Title\n\n## Part\n\n### Sub\n\nText [the guide](Guide) and `code`.\n"

BAD = {
    "h1-count": "## Only a second level\n",
    "heading-skip": "# T\n\n### Skipped\n",
    "heading-empty": "# T\n\n##\n",
    "link-text": "# T\n\nSee [click here](Guide) now.\n",
    "bare-url": "# T\n\nVisit https://example.com/page today.\n",
    "table-header": "# T\n\n| | B |\n|---|---|\n| 1 | 2 |\n",
    "fence-lang": "# T\n\n```\nplain\n```\n",
    "image-alt": "# T\n\n![diagram](pic.svg)\n",
}


@pytest.mark.parametrize("rule", sorted(BAD))
def test_each_rule_fires(tmp_path: Path, rule: str) -> None:
    found = {r for _, _, r in run(tmp_path, "Page.md", BAD[rule])}
    assert rule in found


@pytest.mark.parametrize("rule", sorted(BAD))
def test_revert_proof_rule_removed_passes(tmp_path: Path, rule: str) -> None:
    rules = {k: v for k, v in mod.RULES.items() if k != rule}
    found = {r for _, _, r in run(tmp_path, "Page.md", BAD[rule], rules)}
    assert rule not in found


def test_good_page_is_clean(tmp_path: Path) -> None:
    assert run(tmp_path, "Page.md", GOOD) == []


def test_code_and_comments_are_ignored(tmp_path: Path) -> None:
    text = (
        "<!-- c -->\n# T\n\n```bash\n# not a heading\nhttps://example.com\n```\n"
        "<!--\n## hidden\n-->\n`https://example.com` and <https://example.com>.\n"
        "[`code text`](Guide)\n[the site](https://example.com)\n"
    )
    assert run(tmp_path, "Page.md", text) == []


def test_sidebar_needs_no_h1(tmp_path: Path) -> None:
    assert run(tmp_path, "_Sidebar.md", "**Nav**\n- [Home](Home)\n") == []
    assert run(tmp_path, "Home.md", "**Nav**\n") != []


def test_two_h1_flagged_on_the_second(tmp_path: Path) -> None:
    got = run(tmp_path, "Page.md", "# A\n\n# B\n")
    assert [(n, r) for _, n, r in got] == [(3, "h1-count")]


@pytest.mark.parametrize("text", ["here", "Link", "Read more", "https://x.io/a"])
def test_vague_link_texts(tmp_path: Path, text: str) -> None:
    got = run(tmp_path, "Page.md", f"# T\n\n[{text}](Guide)\n")
    assert any(r == "link-text" for _, _, r in got)


def test_alt_text_variants(tmp_path: Path) -> None:
    for line in ("![](a.png)", "![a.png](a.png)", '<img src="a.png" alt="">'):
        got = run(tmp_path, "Page.md", f"# T\n\n{line}\n")
        assert any(r == "image-alt" for _, _, r in got), line
    ok = run(tmp_path, "Page.md", "# T\n\n![Flow from config to page](a.png)\n")
    assert not any(r == "image-alt" for _, _, r in ok)


def test_png_alpha_detection() -> None:
    assert mod.png_has_alpha(png(6)) is True
    assert mod.png_has_alpha(png(4)) is True
    assert mod.png_has_alpha(png(2)) is False
    assert mod.png_has_alpha(png(2, b"\x00\x00\x00\x00tRNS\x00\x00\x00\x00")) is True
    assert mod.png_has_alpha(b"not a png at all, just text") is None


def test_image_assets(tmp_path: Path) -> None:
    wiki = tmp_path / "docs" / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "ok.png").write_bytes(png(6))
    (wiki / "opaque.png").write_bytes(png(2))
    (wiki / "photo.png").write_bytes(png(2))
    (wiki / "good.svg").write_text('<svg><rect fill="#101418"/></svg>')
    (wiki / "white.svg").write_text('<svg><rect fill="#ffffff"/></svg>')
    (wiki / "named.svg").write_text('<svg><rect style="fill:white"/></svg>')
    (wiki / "x.jpg").write_bytes(b"\xff\xd8\xff")
    (wiki / mod.ALLOWLIST_NAME).write_text("# photos\nphoto.png\n")
    flagged = {s for s, _, r in mod.check(tmp_path, [wiki]) if r == "image-dark"}
    assert flagged == {
        "docs/wiki/opaque.png",
        "docs/wiki/white.svg",
        "docs/wiki/named.svg",
        "docs/wiki/x.jpg",
    }


def test_the_real_wiki_is_clean() -> None:
    assert mod.check(ROOT, [ROOT / "docs" / "wiki"]) == []


def test_cli_exit_codes(tmp_path: Path) -> None:
    wiki = tmp_path / "docs" / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "Page.md").write_text(BAD["fence-lang"], encoding="utf-8")
    cmd = [sys.executable, "-I", str(SCRIPT), "--root", str(tmp_path)]
    bad = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        cmd, capture_output=True, text=True, check=False
    )
    assert bad.returncode == 1
    assert "docs/wiki/Page.md:3: fence-lang" in bad.stdout
    assert "plain" not in bad.stdout
    (wiki / "Page.md").write_text(GOOD, encoding="utf-8")
    good = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        cmd, capture_output=True, text=True, check=False
    )
    assert good.returncode == 0
