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
    config.addinivalue_line(
        "markers",
        "allow_local_wrangler: let ONE module start the pinned wrangler with "
        "--local (see the opt-out comment in conftest.py)",
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


# --- the one narrow opt-out: a LOCAL wrangler in one module (#907) -------------
#
# `@pytest.mark.allow_local_wrangler` lets a test start the PINNED wrangler
# against a disposable local D1 database. It is honored only when ALL hold:
#   1. the requesting module is exactly tests/test_d1_migration_rollback.py (a
#      marker anywhere else, test_leads* included, is ignored);
#   2. the command is a list whose program is the pinned
#      worker/node_modules/.bin/wrangler of this repository, no other word names
#      wrangler, no shell string;
#   3. the command has `--local` and no `--remote`;
#   4. the child environment is passed explicitly, holds no CLOUDFLARE_*, CF_* or
#      token, secret or key variable, and its HOME is inside pytest's base temp
#      directory.
# Anything else is refused by the normal guard.
_ROOT = Path(__file__).resolve().parent.parent
_PINNED_WRANGLER = str(_ROOT / "worker" / "node_modules" / ".bin" / "wrangler")
_LOCAL_WRANGLER_MODULE = (
    Path(__file__).resolve().parent / "test_d1_migration_rollback.py"
)
_SECRETISH = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")


def _env_is_clean(env: dict[str, str], base_temp: str) -> bool:
    for key in env:
        upper = str(key).upper()
        if upper.startswith(("CLOUDFLARE_", "CF_")):
            return False
        if any(bad in upper for bad in _SECRETISH):
            return False
    home = env.get("HOME")
    if not home:
        return False
    try:
        Path(home).resolve().relative_to(Path(base_temp).resolve())
    except ValueError:
        return False
    return True


def _local_wrangler_allowed(
    command: object,
    env: object,
    module_file: str | None,
    marked: bool,
    base_temp: str | None,
) -> bool:
    if not marked or module_file is None or base_temp is None:
        return False
    if Path(module_file).resolve() != _LOCAL_WRANGLER_MODULE:
        return False
    if not isinstance(command, (list, tuple)) or not command:
        return False
    if not all(isinstance(w, (str, os.PathLike)) for w in command):
        return False
    words = [os.fsdecode(w) for w in command]
    if os.path.abspath(words[0]) != _PINNED_WRANGLER:
        return False
    if any(_names_wrangler(w) for w in words[1:]):
        return False
    if "--local" not in words or "--remote" in words:
        return False
    if not isinstance(env, dict):
        return False
    return _env_is_clean(env, base_temp)


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
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
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
    defense in depth, not a sandbox.

    One narrow opt-out exists (#907): the marker `allow_local_wrangler`, honored
    only in tests/test_d1_migration_rollback.py, for the pinned wrangler with
    `--local`, an explicit clean environment (no CLOUDFLARE_*, CF_* or token
    variable, HOME inside pytest's temp directory). The rules are listed above
    `_local_wrangler_allowed`; a marker anywhere else changes nothing."""
    from reach_around.commands import leads

    strict = request.module.__name__.startswith(_STRICT_MODULES)
    marked = request.node.get_closest_marker("allow_local_wrangler") is not None
    module_file = getattr(request.module, "__file__", None)
    base_temp = str(tmp_path_factory.getbasetemp())

    def refuse(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise AssertionError("a test reached the real process runner (wrangler)")

    monkeypatch.setattr(leads, "_exec", refuse)

    class GuardedPopen(subprocess.Popen[bytes]):
        def __init__(self, args: Any, *a: Any, **k: Any) -> None:
            if not strict and _local_wrangler_allowed(
                args, k.get("env"), module_file, marked, base_temp
            ):
                super().__init__(args, *a, **k)
                return
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
