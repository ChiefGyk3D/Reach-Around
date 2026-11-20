# SPDX-License-Identifier: Apache-2.0
"""scripts/check-turnstile-storage.py: the clean-profile measurement harness.

The pure parts run everywhere. The one browser run (``--run-visual``, through the
quiet launcher) uses ``--stub``: Turnstile is answered locally, nothing contacts
Cloudflare.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-turnstile-storage.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_turnstile_storage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HEADERS = """/*
  Content-Security-Policy: strict
  X-Content-Type-Options: nosniff

/e-frame/
  ! Content-Security-Policy
  Content-Security-Policy: helper

/e-frame/*
  X-Robots-Tag: noindex
"""


def test_headers_for_applies_the_detach_and_the_exact_path() -> None:
    mod = load()
    rules = mod.parse_headers(HEADERS)
    assert mod.headers_for(rules, "/")["content-security-policy"] == "strict"
    page = mod.headers_for(rules, "/e-frame/")
    assert page["content-security-policy"] == "helper"
    assert page["x-content-type-options"] == "nosniff"
    assert page["x-robots-tag"] == "noindex"
    assert "x-robots-tag" not in mod.headers_for(rules, "/more/")


def test_foreign_hosts_ignores_this_machine_and_non_network_urls() -> None:
    mod = load()
    urls = [
        "http://127.0.0.1:8000/",
        "http://localhost/x",
        "https://challenges.cloudflare.com/turnstile/v0/api.js",
        "data:text/plain,x",
        "blob:http://127.0.0.1/abc",
        "https://other.example.test/",
    ]
    assert mod.foreign_hosts(urls) == [
        "challenges.cloudflare.com",
        "other.example.test",
    ]


def report(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "mode": "stub",
        "browser": "chromium",
        "state": "verified",
        "waited": 1.0,
        "cookies": [],
        "local_storage": {},
        "session_storage": {},
        "indexed_db": {},
        "hosts": {"127.0.0.1": 3},
        "foreign": [],
        "console_errors": [],
    }
    base.update(over)
    return base


def test_storage_found_sees_each_kind_and_bites() -> None:
    mod = load()
    assert mod.storage_found(report()) is False
    assert mod.storage_found(report(cookies=[{"name": "a"}])) is True
    assert mod.storage_found(report(local_storage={"x": ["k"]})) is True
    assert mod.storage_found(report(session_storage={"x": ["k"]})) is True
    assert mod.storage_found(report(indexed_db={"x": ["db"]})) is True
    assert mod.storage_found(report(local_storage={"x": []})) is False


def test_render_prints_names_never_values() -> None:
    mod = load()
    text = mod.render(
        report(
            cookies=[
                {
                    "name": "cf_example",
                    "domain": ".example.test",
                    "path": "/",
                    "expires": "session",
                    "flags": "secure",
                }
            ],
            local_storage={"challenges.example.test": ["alpha", "beta"]},
        )
    )
    assert "cf_example" in text and "alpha, beta" in text
    assert "STUB RUN" in text


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv: this interpreter and our script
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_run_needs_an_explicit_mode() -> None:
    done = run()
    assert done.returncode == 2
    assert "--live" in done.stderr and "--stub" in done.stderr
    both = run("--live", "--stub")
    assert both.returncode == 2


def test_the_wait_is_bounded() -> None:
    assert run("--stub", "--wait", "0").returncode == 2
    assert run("--stub", "--wait", "9999").returncode == 2


@pytest.mark.visual
def test_stub_run_in_a_clean_browser() -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "src"),
        "RA_CHROMIUM": os.environ.get("RA_CHROMIUM", ""),
    }
    done = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(SCRIPT), "--stub", "--json", "--wait", "20"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert data["mode"] == "stub"
    assert data["state"] == "verified"  # the page opened the helper, got a result
    assert data["cookies"] == []
    assert data["foreign"] == ["challenges.cloudflare.com"]
    assert "STUB.token-0" not in done.stdout  # the token is never printed
