# SPDX-License-Identifier: Apache-2.0
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
    assert (out / "manifest.json").is_file()


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
