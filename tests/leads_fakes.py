# SPDX-License-Identifier: Apache-2.0
"""Test doubles for `reach-around leads delete` (#905): a stand-in wrangler over
in-memory SQLite, and a fake repository install for the path checks. Shared by
conftest.py, the leads tests and the erasure registry guard."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

LEADS_NAME = "reach-around-leads"
ALERTS_NAME = "ra-alerts"
LEADS_UUID = "11111111-2222-4333-8444-555555555555"
ALERTS_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
INTEGRITY = "sha512-testdigest"


class FakeWrangler:
    """A stand-in for the pinned wrangler: in-memory SQLite databases, answers in
    the `--json` shape (a list of one result block), and `d1 info` answers with
    the database id. Records every call."""

    def __init__(self, version: str = "0.0.0") -> None:
        self.version = version
        self.calls: list[tuple[str, str]] = []  # (database, statement)
        self.infos: list[str] = []  # database names asked about
        self.envs: list[dict[str, str]] = []
        self.argvs: list[list[str]] = []
        self.dbs: dict[str, sqlite3.Connection] = {}
        self.ids: dict[str, str] = {LEADS_NAME: LEADS_UUID, ALERTS_NAME: ALERTS_UUID}
        for name in (LEADS_NAME, ALERTS_NAME):
            self.dbs[name] = sqlite3.connect(":memory:")
        lead = self.dbs[LEADS_NAME]
        lead.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT)")
        lead.execute("CREATE TABLE kit_events (id INTEGER PRIMARY KEY, lead_id TEXT)")
        self.dbs[ALERTS_NAME].execute(
            "CREATE TABLE alert_dedup (site TEXT, event_hash TEXT, expires_at INTEGER)"
        )
        self.raw: bytes | None = None  # answer every query with this body
        self.exit_code = 0
        self.extra_blocks = False
        self.after_clear: Any = None  # called with the connection after a clear
        self.fail_on: str | None = None  # a substring of a statement that fails
        self.before_statement: Any = None  # called with (connection, statement)
        self.on_info: Any = None  # called with (fake, name, nth ask of that name)
        self.info_body: Any = None  # replaces the `d1 info` answer when set
        self.after_exec: Any = None  # called with (fake, name, statement) after a run
        self.after_info: Any = None  # called with (fake, name, nth) after the answer
        self.points: dict[str, str] = {}  # name -> database it really points at
        self.events: list[tuple[str, str]] = []  # ("info" | "exec", name), in order
        self.path: Path = Path("/unset")  # the fake install's .bin entry
        self.root: Path = Path("/unset")  # the fake repository root

    def add_database(
        self, name: str, tables: list[str], uuid: str | None = None
    ) -> None:
        self.dbs[name] = sqlite3.connect(":memory:")
        self.ids[name] = uuid or f"{len(self.ids):08x}-0000-4000-8000-000000000000"
        for table in tables:
            cols = {
                "alert_dedup": "site TEXT, event_hash TEXT, expires_at INTEGER",
                "leads": "id TEXT, email TEXT",
                "kit_events": "id INTEGER, lead_id TEXT",
            }[table]
            self.dbs[name].execute(f"CREATE TABLE {table} ({cols})")

    def single_database(self) -> None:
        """One database holds the lead tables and the alert table (one binding)."""
        self.dbs[LEADS_NAME].execute(
            "CREATE TABLE alert_dedup (site TEXT, event_hash TEXT, expires_at INTEGER)"
        )

    def __call__(
        self, argv: list[str], env: dict[str, str], timeout: int
    ) -> tuple[int, bytes]:
        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        assert timeout == 120 or timeout > 0
        if argv[1:3] == ["d1", "info"]:
            name = argv[3]
            self.infos.append(name)
            self.events.append(("info", name))
            nth = self.infos.count(name)
            if self.on_info:
                self.on_info(self, name, nth)
            if self.exit_code or name not in self.dbs:
                return 1, b""
            real = self.points.get(name, name)
            reply: bytes
            if self.info_body is not None:
                reply = self.info_body
            else:
                answer = {"uuid": self.ids[real], "name": name, "version": "production"}
                reply = json.dumps(answer).encode()
            if self.after_info:
                self.after_info(self, name, nth)
            return 0, reply
        name, statement = argv[3], argv[argv.index("--command") + 1]
        self.events.append(("exec", name))
        database = self.points.get(name, name)  # where the name really points
        self.calls.append((name, statement))
        if self.exit_code:
            return self.exit_code, b""
        if self.raw is not None:
            return 0, self.raw
        if database not in self.dbs or (self.fail_on and self.fail_on in statement):
            return 1, b""
        db = self.dbs[database]
        if self.before_statement:
            self.before_statement(db, statement)
        cur = db.execute(statement)
        rows: list[dict[str, Any]] = []
        if cur.description:
            names = [d[0] for d in cur.description]
            rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        db.commit()
        if statement.startswith("DELETE FROM alert_dedup") and self.after_clear:
            self.after_clear(db)
        if self.after_exec:
            self.after_exec(self, name, statement)
        block = {"results": rows, "success": True, "meta": {}}
        body = [block, block] if self.extra_blocks else [block]
        return 0, json.dumps(body).encode()


def write_install(
    root: Path, version: str, *, integrity: str = INTEGRITY, hidden: bool = True
) -> Path:
    """A fake repository: worker/package-lock.json and a wrangler install under
    worker/node_modules (package, `.bin` symlink, npm's install record). Every
    directory is made 0755 whatever the umask. Returns the `.bin` entry."""
    worker = root / "worker"
    nm = worker / "node_modules"
    pkg = nm / "wrangler"
    (pkg / "bin").mkdir(parents=True, exist_ok=True)
    (nm / ".bin").mkdir(exist_ok=True)
    for directory in (root, worker, nm, pkg, pkg / "bin", nm / ".bin"):
        os.chmod(directory, 0o700)
    entry = {"version": version, "integrity": integrity}
    (worker / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/wrangler": entry}})
    )
    meta = {"name": "wrangler", "version": version}
    (pkg / "package.json").write_text(json.dumps(meta))
    script = pkg / "bin" / "wrangler.js"
    script.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(script, 0o700)
    link = nm / ".bin" / "wrangler"
    if not link.is_symlink():
        link.symlink_to("../wrangler/bin/wrangler.js")
    if hidden:
        (nm / ".package-lock.json").write_text(
            json.dumps({"packages": {"node_modules/wrangler": entry}})
        )
    # Files are made 0600 whatever the umask: the install check refuses a group or
    # other write bit anywhere inside the repository.
    for name in (
        worker / "package-lock.json",
        pkg / "package.json",
        nm / ".package-lock.json",
    ):
        if name.exists():
            os.chmod(name, 0o600)
    return link
