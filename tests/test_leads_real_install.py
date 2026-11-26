# SPDX-License-Identifier: Apache-2.0
"""The wrangler resolver of `reach-around leads delete` on a REAL install (#905).

The synthetic layout in test_leads_delete.py is a lock-file approximation. This
file runs the resolver on the real ``worker/node_modules`` of this checkout, with
the real repository root (no fake repository), so a refusal that only a real
``npm ci --ignore-scripts`` tree triggers (group-writable files from umask 002,
npm's own ``.bin`` links, nested packages) shows up. It runs only when the
dependencies are installed. Nothing is started: the resolver only reads.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from reach_around.commands import leads as leads_cmd

_REAL = Path(__file__).resolve().parent.parent / "worker" / "node_modules" / "wrangler"


# Skipped only when the dependencies are absent: cd worker && npm ci --ignore-scripts
@pytest.mark.skipif(not _REAL.is_dir(), reason="worker/node_modules is not installed")
def test_the_resolver_accepts_a_real_npm_ci_install_of_this_repository() -> None:
    """Developer coverage, not a CI gate: this test is SKIPPED when
    ``worker/node_modules`` is absent (no worker dependencies installed), and it
    proves acceptance only, not refusal of a hidden second account."""
    assert Path(leads_cmd.REPO_ROOT).resolve() == _REAL.parents[2]
    resolved = leads_cmd._resolve_wrangler(None)
    assert os.path.relpath(resolved, leads_cmd.REPO_ROOT) == os.path.join(
        "worker", "node_modules", "wrangler", "bin", "wrangler.js"
    )
