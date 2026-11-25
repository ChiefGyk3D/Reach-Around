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
        "token_under_sandbox": True,
        "attack": None,
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


REFUSED = {
    "parent_dom_read": False,
    "parent_dom_write": False,
    "parent_storage_write": False,
    "own_storage_write": False,
    "own_cookie_read": False,
    "parent_attr_set": False,
    "parent_storage_set": False,
    "forged_result_accepted": False,
    "forged_no_nonce": True,
    "forged_wrong_nonce": True,
}


def test_strict_passes_the_documented_success_state_of_each_mode() -> None:
    mod = load()
    assert mod.strict_failures(report(mode="stub", state="verified")) == []
    assert (
        mod.strict_failures(
            report(mode="stub-attack", state="unverified", attack=dict(REFUSED))
        )
        == []
    )


def test_strict_is_mode_specific_and_bites() -> None:
    mod = load()
    # a plain run that never verified fails
    assert mod.strict_failures(report(mode="stub", state="unverified"))
    # an attack run that verified (a forged result got through) fails
    assert mod.strict_failures(
        report(mode="stub-attack", state="verified", attack=dict(REFUSED))
    )
    # an attack that worked fails, and so does a missing entry
    worked = {**REFUSED, "parent_dom_write": True}
    assert mod.strict_failures(
        report(mode="stub-attack", state="unverified", attack=worked)
    )
    missing = {k: v for k, v in REFUSED.items() if k != "own_cookie_read"}
    assert mod.strict_failures(
        report(mode="stub-attack", state="unverified", attack=missing)
    )
    # storage and a foreign host fail every mode
    assert mod.strict_failures(
        report(
            mode="stub-attack",
            state="unverified",
            attack=dict(REFUSED),
            cookies=[{"name": "a"}],
        )
    )
    assert mod.strict_failures(
        report(mode="stub", state="verified", foreign=["evil.example"])
    )


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


def test_render_says_plainly_when_no_token_came_under_the_sandbox() -> None:
    mod = load()
    assert "token under opaque sandbox: yes" in mod.render(report())
    text = mod.render(report(state="unverified", token_under_sandbox=False))
    assert "token under opaque sandbox: NO" in text
    assert "docs/turnstile-storage-check.md" in text


def test_the_attack_script_tries_every_documented_thing() -> None:
    mod = load()
    for needle in (
        "parent.document.title",
        "setAttribute",
        "parent.localStorage",
        "document.cookie",
        "postMessage",
        '"0".repeat(32)',
    ):
        assert needle in mod.ATTACK_SCRIPT, needle
    assert "callback" not in mod.ATTACK_SCRIPT  # it never hands over a token


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
    assert "--stub-attack" in done.stderr
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


@pytest.mark.visual
def test_hostile_vendor_code_in_the_frame_is_contained() -> None:
    """Vendor-context code cannot read or change the parent page or its storage,
    and forged results without the nonce never produce a verified state."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "src"),
        "RA_CHROMIUM": os.environ.get("RA_CHROMIUM", ""),
    }
    done = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable,
            str(SCRIPT),
            "--stub-attack",
            "--strict",
            "--json",
            "--wait",
            "30",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    attack = data["attack"]
    # The attempts really ran (loose), and every one that matters was refused.
    assert "forged_no_nonce" in attack and "parent_dom_read" in attack
    assert attack["forged_no_nonce"] is True  # sent, so the guard was exercised
    assert attack["forged_wrong_nonce"] is True
    for name in (
        "parent_dom_read",
        "parent_dom_write",
        "parent_storage_write",
        "own_storage_write",
        "own_cookie_read",
        "parent_attr_set",
        "parent_storage_set",
        "forged_result_accepted",
    ):
        assert attack[name] is False, name
    assert data["state"] == "unverified"  # the timeout, not a forged message
    assert "FORGED" not in done.stdout
