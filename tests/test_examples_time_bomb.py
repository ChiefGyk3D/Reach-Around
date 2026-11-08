# SPDX-License-Identifier: Apache-2.0
"""No shipped example or doc snippet may fail to build because of the date.

Tests elsewhere pin a 2027 ``today``; users build on the real clock. Every
example config must build today, and still build far in the future (old numbers
only warn). Synthetic dates in examples must therefore be in the past.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.numbers import load_numbers
from reach_around.site.build import build_site
from reach_around.site.visibility import today_in

ROOT = Path(__file__).resolve().parent.parent
FAR_FUTURE = date(2099, 1, 1)
CONFIGS = sorted(
    p for p in (ROOT / "examples").rglob("site.yaml") if ".github" not in p.parts
)
_YAML_BLOCK = re.compile(r"^```yaml\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def _build(config: SiteConfig, root: Path, out: Path, today: date) -> None:
    build_site(
        config,
        out,
        today=today,
        assets_dir=root,
        gated_out=out.parent / (out.name + "-gated"),
    )


def test_the_guard_collects_the_examples() -> None:
    assert len(CONFIGS) >= 7 and any(p.parent.name == "platforms" for p in CONFIGS)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.parent.name)
def test_each_example_builds_on_the_real_clock_and_far_in_the_future(
    path: Path, tmp_path: Path
) -> None:
    config = load_site_config(path)
    real = today_in(config.timezone, datetime.now(UTC))
    _build(config, path.parent, tmp_path / "now", real)
    _build(config, path.parent, tmp_path / "later", FAR_FUTURE)


def test_the_example_numbers_file_loads_on_the_real_clock() -> None:
    load_numbers(ROOT / "examples" / "platforms" / "numbers.example.json")


def test_the_guard_bites_on_a_future_dated_example(tmp_path: Path) -> None:
    text = (ROOT / "examples" / "platforms" / "site.yaml").read_text("utf-8")
    bad = re.sub(r"as_of: \d{4}-\d{2}-\d{2}", "as_of: 2099-01-01", text)
    (tmp_path / "site.yaml").write_text(bad, "utf-8")
    config = load_site_config(tmp_path / "site.yaml")
    with pytest.raises(ValueError, match="in the future"):
        _build(config, tmp_path, tmp_path / "out", date.today())


def _platform_item_dates(doc: str) -> list[str]:
    text = (ROOT / "docs" / doc).read_text("utf-8")
    if doc == "config.md":
        text = text[text.index("#### Platforms") : text.index("##### The numbers file")]
        return re.findall(r"as_of: (\d{4}-\d{2}-\d{2})", text)
    return re.findall(r"platform:[^\n]*url:[^\n]*as_of: (\d{4}-\d{2}-\d{2})", text)


@pytest.mark.parametrize("doc", ["config.md", "customizing.md"])
def test_platform_snippets_in_the_docs_are_not_future_dated(doc: str) -> None:
    dates = _platform_item_dates(doc)
    assert dates, "the guard found no platform item dates"
    assert all(date.fromisoformat(d) <= date.today() for d in dates)
