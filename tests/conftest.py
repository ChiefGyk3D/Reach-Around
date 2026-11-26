# SPDX-License-Identifier: Apache-2.0
import os
import re
import subprocess
import sys
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
# except anything that runs wrangler (a real remote call; npx or npm are only
# refused when a word of the command is wrangler). Plain node, bash and the
# rest stay allowed there: tests/site runs node for the click JS.
# `leads._exec` is replaced for all of them.
_STRICT_MODULES = ("test_leads",)
_FORBIDDEN_NAMES = ("wrangler",)
_ENTRYPOINTS = ("wrangler.js", "wrangler.mjs", "wrangler.cjs")
# Every word of every argument is looked at, not only the program, so an
# allowed interpreter or launcher (`sh -c "wrangler ..."`, `env node x`,
# `python -c "...wrangler..."`) is refused too. What this still cannot see: a
# program that builds the name at run time, or a script file that starts one
# (only process starts through the patched module attributes, and the aliases in
# the imported test_leads* modules, are guarded, not what a child does).
_WORD = re.compile(r"[^A-Za-z0-9_./+-]+")
_OS_STARTERS = (
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "posix_spawn",
    "posix_spawnp",
    "system",
)


def _words(command: object) -> list[str]:
    parts: list[object]
    if isinstance(command, (list, tuple)):
        parts = list(command)
    else:
        parts = [command]
    words: list[str] = []
    for part in parts:
        if isinstance(part, (str, bytes, os.PathLike)):
            # Full-line shell comments are text, not commands: the deploy workflow
            # steps that tests run through `bash -c` mention wrangler in them.
            lines = os.fsdecode(part).splitlines()
            code = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
            words.extend(w for w in _WORD.split(code) if w)
    return words


def _is_forbidden(command: object, strict: bool) -> bool:
    if strict:
        return True
    return any(_names_wrangler(w) for w in _words(command))


def _names_wrangler(word: str) -> bool:
    """A word that is the wrangler program or a file of the wrangler package: the
    bin shim, its real entry file (`wrangler.js` and its module variants) started
    through node, or anything under the package or its `wrangler-dist` folder."""
    if os.path.basename(word) in _FORBIDDEN_NAMES + _ENTRYPOINTS:
        return True
    return any(p in ("wrangler", "wrangler-dist") for p in word.split("/"))


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
    subprocess replaces `subprocess.Popen` itself, after this guard.

    What it catches: a process start made through those module attributes at call
    time, and through names bound to the originals in the already imported
    `test_leads*` test modules. What it does not catch: an alias captured at
    import time in any other module (a library or a non-leads test), a process
    started by a child, or a program that builds its own name at run time. It is
    defense in depth, not a sandbox."""
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

    originals: dict[int, Any] = {id(GuardedPopen.__mro__[1]): GuardedPopen}
    for name in _OS_STARTERS:
        if hasattr(os, name):
            real = getattr(os, name)
            guarded = guard_path(real)
            originals[id(real)] = guarded
            monkeypatch.setattr(os, name, guarded)
    # Round 5 (#905, Low): a name bound at import time (`from subprocess import
    # Popen as saved`) keeps the original object, so patching the defining module
    # does not reach it. The names bound in the already imported `test_leads*`
    # modules are replaced too, for this one test.
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith(_STRICT_MODULES) or module is None:
            continue
        for attr, value in list(vars(module).items()):
            replacement = originals.get(id(value))
            if replacement is not None and attr not in ("subprocess", "os"):
                monkeypatch.setattr(module, attr, replacement)


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
    # An empty HOME: the install check refuses legacy node folders in it (#905).
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(leads, "_exec", fake)
    monkeypatch.setattr(leads, "pause", lambda _s: None)
    return fake
