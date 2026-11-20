# SPDX-License-Identifier: Apache-2.0
"""A count source with ``show_on_hub: false`` feeds the kit and the numbers
file and puts nothing on the link hub (#689). Everything here is synthetic."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fakenet import FakeTransport, reply
from reach_around.cli import main
from reach_around.commands.counts import run_refresh
from reach_around.config import SiteConfig
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.config.stale import all_numbers
from reach_around.site.build import build_site

GOLDEN = Path(__file__).parent / "golden_kit_only"
TODAY = date(2027, 3, 10)
STAMP = datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
GH = "https://api.github.com/users/some-user"

SOURCE_ON = {"platform": "github", "kind": "github", "user": "some-user"}


def config(
    *, hidden: bool | None, with_item: bool, extra_item: bool = False
) -> SiteConfig:
    source: dict[str, Any] = dict(SOURCE_ON)
    if hidden is not None:
        source["show_on_hub"] = not hidden
    items: list[dict[str, Any]] = []
    if with_item:
        items.append(
            {"platform": "github", "handle": "@example", "url": "https://example.com/g"}
        )
    if extra_item:
        items.append(
            {
                "platform": "bluesky",
                "handle": "@example",
                "url": "https://example.com/b",
            }
        )
    blocks: list[dict[str, Any]] = [
        {"id": "one", "title": "One", "url": "https://example.com/1"}
    ]
    if items:
        blocks.append({"id": "socials", "items": items})
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "features": {"kit_gate": False},
            "tabs": [{"id": "start", "title": "Start", "blocks": blocks}],
            "kit": {
                "title": "Example kit",
                "about": "A synthetic test kit.",
                "metrics": [
                    {"platform": "github", "label": "Followers", "source": "counts"}
                ],
            },
            "numbers": {
                "sources": [
                    source,
                    *(
                        [
                            {
                                "platform": "bluesky",
                                "kind": "bluesky",
                                "actor": "a.example.com",
                            }
                        ]
                        if extra_item
                        else []
                    ),
                ]
            },
        }
    )


def numbers(count: int = 24739) -> Numbers:
    entries = {
        "github": NumbersEntry(count=count, as_of=STAMP),
        "bluesky": NumbersEntry(count=1234567, as_of=STAMP),
    }
    return Numbers(entries=entries)


def build(tmp_path: Path, conf: SiteConfig, nums: Numbers) -> tuple[str, str]:
    out = tmp_path / "public"
    build_site(conf, out, today=TODAY, numbers=nums)
    hub = (out / "index.html").read_text(encoding="utf-8")
    kit = (out / "kit" / "index.html").read_text(encoding="utf-8")
    return hub, kit


def test_a_hub_with_only_kit_only_sources_has_no_chip_and_no_count(
    tmp_path: Path,
) -> None:
    hub, kit = build(tmp_path, config(hidden=True, with_item=False), numbers())
    for banned in ("platform", "24,739", "24739", "24.7k", "followers"):
        assert banned not in hub.lower(), banned
    # the kit still prints it, with its date
    assert "24,739" in kit and "2027-03-09" in kit


def test_a_platforms_item_of_a_hidden_source_keeps_its_link_but_no_count(
    tmp_path: Path,
) -> None:
    hub, kit = build(
        tmp_path, config(hidden=True, with_item=True, extra_item=True), numbers()
    )
    assert 'href="https://example.com/g"' in hub
    assert "24,739" not in hub and "24.7k" not in hub
    # the visible sibling still shows its count
    assert "1.2M" in hub and "1,234,567" in hub
    assert hub.count("platform-count") == 1
    assert "24,739" in kit


def test_the_default_is_byte_identical_to_a_config_without_the_key(
    tmp_path: Path,
) -> None:
    conf_default = config(hidden=None, with_item=True)
    conf_explicit = config(hidden=False, with_item=True)
    a, _ = build(tmp_path / "a", conf_default, numbers())
    b, _ = build(tmp_path / "b", conf_explicit, numbers())
    assert a == b
    assert "platform-count" in a and "24,739" in a


def test_a_hidden_number_is_not_a_hub_number_for_the_stale_list(
    tmp_path: Path,
) -> None:
    old = Numbers(
        entries={
            "github": NumbersEntry(count=5, as_of=datetime(2026, 1, 1, tzinfo=UTC))
        }
    )
    notes = all_numbers(config(hidden=True, with_item=True), old, TODAY)
    assert [n.scope for n in notes] == ["kit"]


def test_refresh_fetches_and_writes_a_kit_only_source_and_the_kit_prints_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "inst"
    root.mkdir()
    (root / "site.yaml").write_text(
        """\
version: 1
creator: {name: Example Creator}
features: {kit_gate: false}
tabs:
  - id: start
    title: Start
    blocks:
      - {id: one, title: One, url: "https://example.com/1"}
kit:
  title: Example kit
  about: A synthetic test kit.
  metrics:
    - {platform: github, label: Followers, source: counts}
numbers:
  sources:
    - {platform: github, kind: github, user: some-user, show_on_hub: false}
""",
        "utf-8",
    )
    transport = FakeTransport({GH: reply({"followers": 24739})})
    code = run_refresh(
        root / "site.yaml", None, None, False, transport=transport, env={}
    )
    assert code == 0
    written = json.loads((root / "numbers.json").read_text())
    assert set(written["platforms"]) == {"github"}
    assert written["platforms"]["github"]["count"] == 24739

    out = tmp_path / "out"
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
    hub = (out / "index.html").read_text("utf-8")
    kit = (out / "kit" / "index.html").read_text("utf-8")
    assert "24,739" not in hub and "platform-count" not in hub
    assert "24,739" in kit
    assert re.search(r"20\d\d-\d\d-\d\d", kit)
    # and the check command accepts the configuration
    assert main(["check", "--config", str(root / "site.yaml")]) == 0


def test_the_kit_only_hub_and_kit_pages_match_the_golden_files(
    tmp_path: Path, update_golden: bool
) -> None:
    hub, kit = build(
        tmp_path, config(hidden=True, with_item=True, extra_item=True), numbers()
    )
    built = {"hub.html": hub, "kit.html": kit}
    if update_golden:
        GOLDEN.mkdir(exist_ok=True)
        for name, text in built.items():
            (GOLDEN / name).write_text(text, encoding="utf-8")
    for name, text in built.items():
        assert (GOLDEN / name).is_file(), "no golden file; run pytest --update-golden"
        assert text == (GOLDEN / name).read_text(encoding="utf-8"), name
