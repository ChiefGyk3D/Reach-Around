# SPDX-License-Identifier: Apache-2.0
"""refresh writes a numbers file, and the build shows it on a platforms block."""

import json
import re
from pathlib import Path

from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.commands.counts import run_refresh

SITE = """\
version: 1
creator: {name: Example Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: github, handle: "@example", url: "https://example.com/g"}
          - {platform: bluesky, handle: "@example", url: "https://example.com/b"}
numbers:
  sources:
    - {platform: github, kind: github, user: some-user}
    - {platform: socials-bluesky, kind: bluesky, actor: example.bsky.social}
"""
GH = "https://api.github.com/users/some-user"
BSKY = "https://public.api.bsky.app/"


def build(root: Path, out: Path) -> str:
    code = main(
        [
            "build",
            "--config",
            str(root / "site.yaml"),
            "--out",
            str(out),
            "--numbers",
            str(root / "numbers.json"),
        ]
    )
    assert code == 0
    return (out / "index.html").read_text("utf-8")


def test_refresh_output_drives_a_build(tmp_path: Path) -> None:
    root = tmp_path / "inst"
    root.mkdir()
    (root / "site.yaml").write_text(SITE, "utf-8")
    transport = FakeTransport(
        {GH: reply({"followers": 24739}), BSKY: reply({"followersCount": 1234567})}
    )
    code = run_refresh(
        root / "site.yaml", None, None, False, transport=transport, env={}
    )
    assert code == 0
    written = json.loads((root / "numbers.json").read_text())
    assert written["version"] == 1
    # one key is a platform slug, the other a platforms-block item id
    assert set(written["platforms"]) == {"github", "socials-bluesky"}

    page = build(root, tmp_path / "out")
    text = re.sub(r"<[^>]+>", " ", page)
    assert "24,739 followers" in text
    assert "1,234,567 followers" in text
