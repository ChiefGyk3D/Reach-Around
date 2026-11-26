# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite the golden files under tests/ instead of comparing",
    )
    parser.addoption(
        "--run-visual",
        action="store_true",
        default=False,
        help="run the browser layout tests marked 'visual' (needs Playwright)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "visual: browser layout tests, run only with --run-visual"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-visual"):
        return
    skip = pytest.mark.skip(reason="visual test; pass --run-visual to run")
    for item in items:
        if "visual" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


# --- no test may reach a real wrangler (#905) ---------------------------------


def _capture_real_exec() -> Any:
    from reach_around.commands import leads

    return leads._exec


_REAL_EXEC = _capture_real_exec()

# Tests of `leads delete` may start NO process at all. Every other test (the
# erasure registry runs bash backup scripts, for one) may start what it needs,
# except a command whose program is wrangler, npx, npm or node (a real remote
# call); `leads._exec` is replaced for all of them.
_STRICT_MODULES = ("test_leads",)
_FORBIDDEN_NAMES = ("wrangler", "npx", "npm", "node")


def _is_forbidden(command: object, strict: bool) -> bool:
    if strict:
        return True
    first = command[0] if isinstance(command, (list, tuple)) and command else command
    if not isinstance(first, (str, bytes, os.PathLike)):
        return False
    text = os.fsdecode(first).split(" ")[0]
    return os.path.basename(text) in _FORBIDDEN_NAMES


@pytest.fixture
def real_exec() -> Any:
    """The real process runner, for the tests that check how it calls subprocess
    (with `subprocess.Popen` replaced by the test)."""
    return _REAL_EXEC


@pytest.fixture(autouse=True)
def _no_real_wrangler(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """No test may start a real wrangler (or node, npm, npx), whatever code path
    asks. The guard sits at the lowest level: `subprocess.Popen` (which
    `subprocess.run` and `check_output` use), `os.system`, `os.exec*`,
    `os.posix_spawn*`, and the module's own `_exec`. In the `leads` tests and the
    erasure guard every process start fails. A test that checks how `_exec` calls
    subprocess replaces `subprocess.Popen` itself, after this guard."""
    from reach_around.commands import leads

    strict = request.module.__name__.startswith(_STRICT_MODULES)

    def refuse(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise AssertionError("a test reached the real process runner (wrangler)")

    monkeypatch.setattr(leads, "_exec", refuse)

    class GuardedPopen(subprocess.Popen[bytes]):
        def __init__(self, args: Any, *a: Any, **k: Any) -> None:
            if _is_forbidden(args, strict):
                raise AssertionError("a test started a real process (wrangler guard)")
            super().__init__(args, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)

    def guard_path(real: Any) -> Any:
        def guarded(path: Any, *a: Any, **k: Any) -> Any:
            if _is_forbidden(path, strict):
                raise AssertionError("a test started a real process (wrangler guard)")
            return real(path, *a, **k)

        return guarded

    for name in (
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "posix_spawn",
        "posix_spawnp",
        "system",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, guard_path(getattr(os, name)))


@pytest.fixture
def fake_wrangler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Installs a FakeWrangler as the process runner and a fake repository whose
    `worker/node_modules` holds a wrangler install, so the path and install checks
    run for real on files in a temporary directory."""
    import json

    from leads_fakes import FakeWrangler, write_install
    from reach_around.commands import leads

    lock = json.loads((leads.REPO_ROOT / "worker" / "package-lock.json").read_text())
    version = lock["packages"]["node_modules/wrangler"]["version"]
    fake = FakeWrangler(version)
    root = tmp_path / "repo"
    root.mkdir()
    fake.path = write_install(root, version)
    fake.root = root
    monkeypatch.setattr(leads, "REPO_ROOT", root)
    monkeypatch.setattr(leads, "_exec", fake)
    monkeypatch.setattr(leads, "pause", lambda _s: None)
    return fake
