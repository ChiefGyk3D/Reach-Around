# SPDX-License-Identifier: Apache-2.0
"""The version has one source: reach_around.__version__."""

import re
import tomllib
from pathlib import Path
from typing import Any

from reach_around import __version__

ROOT = Path(__file__).resolve().parents[1]
# Plain x.y.z, or x.y.z-rc.N for a release candidate (the tag is v plus this string).
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-rc\.\d+)?$")


def _pyproject() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_version_is_semver_or_release_candidate() -> None:
    assert SEMVER.match(__version__), __version__


def test_pyproject_reads_the_version_from_the_package() -> None:
    data = _pyproject()
    project = data["project"]
    assert "version" not in project, "a static version would duplicate __version__"
    assert "version" in project["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "reach_around.__version__"
    }


def test_changelog_top_entry_matches_the_version() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## \[(\d+\.\d+\.\d+(?:-rc\.\d+)?)\]", text, re.MULTILINE)
    assert headings, "CHANGELOG.md has no version headings"
    assert headings[0] == __version__


def test_release_candidate_is_pep440_valid() -> None:
    from packaging.version import Version

    Version(__version__)
