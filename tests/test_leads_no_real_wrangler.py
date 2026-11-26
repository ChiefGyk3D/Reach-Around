# SPDX-License-Identifier: Apache-2.0
"""The test suite can never start a real wrangler (#905).

This file does NOT use the `fake_wrangler` fixture, so it sees exactly what a test
that forgot to install the fake would see: the autouse guard in conftest.py, which
makes the one process-starting function of `leads delete` fail.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

from reach_around.cli import main
from reach_around.commands import leads


def test_the_process_runner_is_guarded_in_every_test() -> None:
    with pytest.raises(AssertionError, match="real process runner"):
        leads._exec(["/bin/true"], {}, 1)


def test_the_online_path_cannot_start_a_process_without_the_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    export = tmp_path / "e.json"
    export.write_text(
        json.dumps(
            [
                {
                    "results": [
                        {
                            "id": "0123456789abcdef0123456789abcdef",
                            "email": "a@example.test",
                        }
                    ],
                    "success": True,
                    "meta": {},
                }
            ]
        )
    )
    binary = tmp_path / "wrangler"
    binary.write_text("#!/bin/sh\necho started > started\n")
    os.chmod(binary, 0o700)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("a@example.test\n"))
    argv = ["leads", "delete", "--input", str(export), "--email", "-", "--yes"]
    argv += ["--database", "reach-around-leads", "--alerts-database", "x"]
    argv += ["--wrangler", str(binary)]
    with pytest.raises(AssertionError, match="real process runner"):
        main(argv)
    assert not (tmp_path / "started").exists()
