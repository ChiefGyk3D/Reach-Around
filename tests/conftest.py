# SPDX-License-Identifier: Apache-2.0
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


@pytest.fixture
def real_exec() -> Any:
    """The real process runner, for the one test that checks how it calls
    subprocess (with subprocess itself replaced)."""
    return _REAL_EXEC


@pytest.fixture(autouse=True)
def _no_real_wrangler(monkeypatch: pytest.MonkeyPatch) -> None:
    """`leads delete` starts a process in exactly one function, `_exec`. Every test
    runs with it replaced by a function that fails, so a test that forgets to
    install the fake cannot start wrangler, node or anything else. Tests that need
    the online path use the `fake_wrangler` fixture."""
    from reach_around.commands import leads

    def refuse(*_a: object, **_k: object) -> tuple[int, bytes]:
        raise AssertionError("a test reached the real process runner (wrangler)")

    monkeypatch.setattr(leads, "_exec", refuse)


class FakeWrangler:
    """A stand-in for the pinned wrangler: in-memory SQLite databases, answers in
    the `--json` shape (a list of one result block). Records every call."""

    def __init__(self, version: str) -> None:
        import sqlite3

        self.version = version
        self.calls: list[tuple[str, str]] = []  # (database, statement)
        self.envs: list[dict[str, str]] = []
        self.argvs: list[list[str]] = []
        self.versions_asked = 0
        self.dbs: dict[str, sqlite3.Connection] = {}
        for name in ("reach-around-leads", "ra-alerts"):
            self.dbs[name] = sqlite3.connect(":memory:")
        lead = self.dbs["reach-around-leads"]
        lead.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT)")
        lead.execute("CREATE TABLE kit_events (id INTEGER PRIMARY KEY, lead_id TEXT)")
        self.dbs["ra-alerts"].execute(
            "CREATE TABLE alert_dedup (site TEXT, event_hash TEXT, expires_at INTEGER)"
        )
        self.raw: bytes | None = None  # answer every query with this body
        self.exit_code = 0
        self.extra_blocks = False
        self.after_clear: Any = None  # called with the connection after a clear
        self.fail_on: str | None = None  # a substring of a statement that fails

    def add_database(self, name: str, tables: list[str]) -> None:
        import sqlite3

        self.dbs[name] = sqlite3.connect(":memory:")
        for table in tables:
            cols = {
                "alert_dedup": "site TEXT, event_hash TEXT, expires_at INTEGER",
                "leads": "id TEXT, email TEXT",
                "kit_events": "id INTEGER, lead_id TEXT",
            }[table]
            self.dbs[name].execute(f"CREATE TABLE {table} ({cols})")

    def __call__(
        self, argv: list[str], env: dict[str, str], timeout: int
    ) -> tuple[int, bytes]:
        import json

        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        assert timeout == 120 or timeout > 0
        if argv[1:] == ["--version"]:
            self.versions_asked += 1
            return 0, f" wrangler {self.version}\n".encode()
        database, statement = argv[3], argv[argv.index("--command") + 1]
        self.calls.append((database, statement))
        if self.exit_code:
            return self.exit_code, b""
        if self.raw is not None:
            return 0, self.raw
        if database not in self.dbs or (self.fail_on and self.fail_on in statement):
            return 1, b""
        db = self.dbs[database]
        cur = db.execute(statement)
        rows: list[dict[str, Any]] = []
        if cur.description:
            names = [d[0] for d in cur.description]
            rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        db.commit()
        if statement.startswith("DELETE FROM alert_dedup") and self.after_clear:
            self.after_clear(db)
        block = {"results": rows, "success": True, "meta": {}}
        body = [block, block] if self.extra_blocks else [block]
        return 0, json.dumps(body).encode()


@pytest.fixture
def fake_wrangler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Installs a FakeWrangler as the process runner and an executable file for
    `--wrangler`. The binary is real on disk so the path checks run for real."""
    import json
    import os

    from reach_around.commands import leads

    lock = json.loads((leads.REPO_ROOT / "worker" / "package-lock.json").read_text())
    fake = FakeWrangler(lock["packages"]["node_modules/wrangler"]["version"])
    bindir = tmp_path / "wbin"
    bindir.mkdir(mode=0o700)
    binary = bindir / "wrangler"
    binary.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(binary, 0o700)
    fake.path = binary  # type: ignore[attr-defined]
    monkeypatch.setattr(leads, "_exec", fake)
    monkeypatch.setattr(leads, "pause", lambda _s: None)
    return fake
