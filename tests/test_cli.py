# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import pytest

from reach_around import __version__
from reach_around.cli import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "instance" / "site.yaml"


def test_version_flag_prints_version_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"reach-around {__version__}\n"


def test_no_arguments_prints_help_and_returns_two() -> None:
    assert main([]) == 2


def test_build_command_writes_the_site(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main(["build", "--config", str(EXAMPLE), "--out", str(out)]) == 0
    assert (out / "index.html").is_file()
    assert not (out / "manifest.json").exists()


def test_build_writes_the_manifest_next_to_out_by_default(tmp_path: Path) -> None:
    out = tmp_path / "site-out"
    assert main(["build", "--config", str(EXAMPLE), "--out", str(out)]) == 0
    sibling = tmp_path / "site-out.manifest.json"
    assert sibling.is_file()
    data = json.loads(sibling.read_text())
    assert list(data) == ["links"] and data["links"]
    assert not [p for p in out.rglob("*") if "manifest" in p.name.lower()]


def test_build_manifest_flag_is_honored(tmp_path: Path) -> None:
    out = tmp_path / "site-out"
    target = tmp_path / "elsewhere" / "ids.json"
    argv = ["build", "--config", str(EXAMPLE), "--out", str(out)]
    assert main([*argv, "--manifest", str(target)]) == 0
    assert json.loads(target.read_text())["links"]
    assert not (tmp_path / "site-out.manifest.json").exists()


@pytest.mark.parametrize("inside", ["manifest.json", "sub/m.json"])
def test_build_refuses_a_manifest_inside_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], inside: str
) -> None:
    out = tmp_path / "site-out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    argv = ["build", "--config", str(EXAMPLE), "--out", str(out)]
    assert main([*argv, "--manifest", str(out / inside)]) == 1
    assert "manifest" in capsys.readouterr().err
    assert (out / "keep.txt").is_file()  # refused before emptying
    assert not (out / inside).exists()


def test_build_refuses_a_manifest_that_is_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "site-out"
    argv = ["build", "--config", str(EXAMPLE), "--out", str(out)]
    assert main([*argv, "--manifest", str(tmp_path)]) == 1
    assert "manifest" in capsys.readouterr().err


def test_build_command_reports_a_config_error_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\n")
    out = tmp_path / "out"
    assert main(["build", "--config", str(bad), "--out", str(out)]) == 1
    captured = capsys.readouterr()
    assert "bad.yaml" in captured.err
    assert "Traceback" not in captured.err + captured.out
    assert not out.exists()


def test_build_command_refuses_to_clobber_the_config_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    conf = tmp_path / "conf"
    conf.mkdir()
    config_file = conf / "site.yaml"
    config_file.write_text(EXAMPLE.read_text())
    assert main(["build", "--config", str(config_file), "--out", str(conf)]) == 1
    assert config_file.is_file()
    assert "Traceback" not in capsys.readouterr().err


def test_build_refuses_a_manifest_that_is_the_config_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_file = tmp_path / "site.yaml"
    config_file.write_text(EXAMPLE.read_text())
    before = config_file.read_text()
    argv = ["build", "--config", str(config_file), "--out", str(tmp_path / "o")]
    assert main([*argv, "--manifest", str(config_file)]) == 1
    assert config_file.read_text() == before
    assert "manifest" in capsys.readouterr().err
