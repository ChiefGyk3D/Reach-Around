# SPDX-License-Identifier: Apache-2.0
"""``reach-around build --numbers`` and the default numbers.json."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reach_around.cli import main

SITE = """\
version: 1
creator: {name: Example Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: twitch, handle: "@example", url: "https://example.com/t"}
"""


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def numbers_text(count: int = 424242, extra: dict[str, object] | None = None) -> str:
    platforms: dict[str, object] = {"twitch": {"count": count, "as_of": stamp()}}
    platforms.update(extra or {})
    return json.dumps({"version": 1, "platforms": platforms})


@pytest.fixture
def inst(tmp_path: Path) -> Path:
    root = tmp_path / "inst"
    root.mkdir()
    (root / "site.yaml").write_text(SITE, "utf-8")
    return root


def run(inst: Path, tmp_path: Path, *extra: str) -> int:
    return main(
        [
            "build",
            "--config",
            str(inst / "site.yaml"),
            "--out",
            str(tmp_path / "out"),
            *extra,
        ]
    )


def html(tmp_path: Path) -> str:
    return (tmp_path / "out" / "index.html").read_text("utf-8")


def test_the_default_file_is_used_when_present(inst: Path, tmp_path: Path) -> None:
    (inst / "numbers.json").write_text(numbers_text(), "utf-8")
    assert run(inst, tmp_path) == 0
    assert "424,242 followers" in html(tmp_path)
    assert not list((tmp_path / "out").rglob("numbers.json"))


def test_no_default_file_is_a_silent_skip(
    inst: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(inst, tmp_path) == 0
    assert capsys.readouterr().err == ""
    assert "platform-count" not in html(tmp_path)


def test_an_invalid_default_file_is_an_error(
    inst: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (inst / "numbers.json").write_text("{not json", "utf-8")
    assert run(inst, tmp_path) == 1
    err = capsys.readouterr().err
    assert str(inst / "numbers.json") in err


def test_an_explicit_path_overrides_the_default(inst: Path, tmp_path: Path) -> None:
    (inst / "numbers.json").write_text(numbers_text(1000), "utf-8")
    other = tmp_path / "elsewhere.json"
    other.write_text(numbers_text(2000), "utf-8")
    assert run(inst, tmp_path, "--numbers", str(other)) == 0
    assert "2,000 followers" in html(tmp_path)


def test_a_missing_explicit_file_is_an_error_naming_it(
    inst: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "gone.json"
    assert run(inst, tmp_path, "--numbers", str(missing)) == 1
    assert str(missing) in capsys.readouterr().err


def test_an_invalid_explicit_file_is_an_error_naming_it(
    inst: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(numbers_text(-5), "utf-8")
    assert run(inst, tmp_path, "--numbers", str(bad)) == 1
    err = capsys.readouterr().err
    assert str(bad) in err and "count" in err


def test_a_numbers_path_inside_out_is_refused(
    inst: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inside = tmp_path / "out" / "numbers.json"
    inside.parent.mkdir()
    inside.write_text(numbers_text(), "utf-8")
    assert run(inst, tmp_path, "--numbers", str(inside)) == 1
    assert "inside the output directory" in capsys.readouterr().err
    assert inside.exists(), "a refused build must not empty the directory"


def test_a_numbers_path_that_is_the_config_is_refused(
    inst: Path, tmp_path: Path
) -> None:
    assert run(inst, tmp_path, "--numbers", str(inst / "site.yaml")) == 1


def test_unknown_keys_are_ignored_with_a_count(
    inst: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extra: dict[str, object] = {
        "nonsense": {"count": 1, "as_of": stamp()},
        "x-ghost": {"count": 1, "as_of": stamp()},
    }
    path = tmp_path / "n.json"
    path.write_text(numbers_text(5, extra), "utf-8")
    assert run(inst, tmp_path, "--numbers", str(path)) == 0
    assert "2 key(s)" in capsys.readouterr().err
    assert "5 followers" in html(tmp_path)


def test_counts_do_not_leak_into_the_manifest(inst: Path, tmp_path: Path) -> None:
    (inst / "numbers.json").write_text(numbers_text(), "utf-8")
    assert run(inst, tmp_path) == 0
    manifest = (tmp_path / "out.manifest.json").read_text("utf-8")
    assert "424242" not in manifest
    assert "socials-twitch" in manifest
