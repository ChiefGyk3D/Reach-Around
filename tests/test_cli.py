# SPDX-License-Identifier: Apache-2.0
import pytest

from reach_around import __version__
from reach_around.cli import main


def test_version_flag_prints_version_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"reach-around {__version__}\n"


def test_no_arguments_prints_help_and_returns_two() -> None:
    assert main([]) == 2
