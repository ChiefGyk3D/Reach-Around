# SPDX-License-Identifier: Apache-2.0
"""``build --assets-inventory`` and ``deploy-purge-list``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from assetpaths import asset
from reach_around.cli import main
from reach_around.site.inventory import load_inventory

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "instance" / "site.yaml"


def build(out: Path, *extra: str) -> int:
    return main(["build", "--config", str(EXAMPLE), "--out", str(out), *extra])


def test_the_inventory_is_written_beside_out_by_default(tmp_path: Path) -> None:
    out = tmp_path / "site-out"
    assert build(out) == 0
    sibling = tmp_path / "site-out.assets.json"
    assert sibling.is_file()
    inventory = load_inventory(sibling)
    assert asset(out, "static/site.css") in inventory
    assert not [p for p in out.rglob("*") if "assets.json" in p.name]
    assert (tmp_path / "site-out.manifest.json").is_file()


def test_the_inventory_flag_is_honored(tmp_path: Path) -> None:
    out = tmp_path / "site-out"
    target = tmp_path / "elsewhere" / "inv.json"
    assert build(out, "--assets-inventory", str(target)) == 0
    assert load_inventory(target)
    assert not (tmp_path / "site-out.assets.json").exists()


@pytest.mark.parametrize("inside", ["inv.json", "sub/inv.json"])
def test_the_inventory_inside_out_is_refused_before_emptying(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], inside: str
) -> None:
    out = tmp_path / "site-out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    assert build(out, "--assets-inventory", str(out / inside)) == 1
    assert "inventory" in capsys.readouterr().err
    assert (out / "keep.txt").is_file()
    assert not (out / inside).exists()


def test_the_inventory_may_not_be_a_directory_the_config_or_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "site-out"
    assert build(out, "--assets-inventory", str(tmp_path)) == 1
    assert "inventory" in capsys.readouterr().err
    config = tmp_path / "site.yaml"
    config.write_text(EXAMPLE.read_text())
    before = config.read_text()
    argv = ["build", "--config", str(config), "--out", str(out)]
    assert main([*argv, "--assets-inventory", str(config)]) == 1
    assert config.read_text() == before
    same = tmp_path / "both.json"
    assert build(out, "--manifest", str(same), "--assets-inventory", str(same)) == 1
    assert "inventory" in capsys.readouterr().err


def test_a_refused_inventory_leaves_the_out_directory_alone(tmp_path: Path) -> None:
    out = tmp_path / "site-out"
    assert build(out) == 0
    before = sorted(p.name for p in out.iterdir())
    assert build(out, "--assets-inventory", str(out / "x.json")) == 1
    assert sorted(p.name for p in out.iterdir()) == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_an_unwritable_default_location_is_a_warning_and_the_build_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # like a container with a read-only root: --out is writable, its parent is not
    parent = tmp_path / "ro"
    out = parent / "out"
    out.mkdir(parents=True)
    manifest = tmp_path / "m.json"
    parent.chmod(0o555)
    try:
        assert build(out, "--manifest", str(manifest)) == 0
        assert (out / "index.html").is_file()
        err = capsys.readouterr().err
        assert "warning: assets inventory not written" in err
        assert "--assets-inventory" in err
        # an explicit path that cannot be written is still an error
        blocked = parent / "inv.json"
        assert (
            build(out, "--manifest", str(manifest), "--assets-inventory", str(blocked))
            == 1
        )
    finally:
        parent.chmod(0o755)


# ------------------------------------------------------- deploy-purge-list


def test_deploy_purge_list_prints_one_path_per_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build(tmp_path / "a") == 0
    previous = tmp_path / "a.assets.json"
    data = json.loads(previous.read_text())
    # the previous deploy had a file the new one dropped, and an older icon
    data["files"]["manifest.json"] = data["files"]["index.html"]
    old = tmp_path / "old.assets.json"
    old.write_text(json.dumps(data))
    capsys.readouterr()
    code = main(
        [
            "deploy-purge-list",
            "--previous",
            str(old),
            "--current",
            str(previous),
        ]
    )
    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert "/manifest.json" in lines
    assert {"/", "/404.html", "/robots.txt"} <= set(lines)
    assert lines == sorted(set(lines))
    assert all(line.startswith("/") for line in lines)


def test_deploy_purge_list_reports_a_bad_inventory_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build(tmp_path / "a") == 0
    good = tmp_path / "a.assets.json"
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 1, "files": {"../x": {}}}')
    capsys.readouterr()
    code = main(["deploy-purge-list", "--previous", str(bad), "--current", str(good)])
    captured = capsys.readouterr()
    assert code == 1
    assert "bad.json" in captured.err and "Traceback" not in captured.err
    assert captured.out == ""
    code = main(
        [
            "deploy-purge-list",
            "--previous",
            str(tmp_path / "no"),
            "--current",
            str(good),
        ]
    )
    assert code == 1
