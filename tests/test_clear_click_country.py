# SPDX-License-Identifier: Apache-2.0
"""scripts/clear-click-country.sh: the owner-run cleanup of old raw click countries.

A stub stands in for ``wrangler`` and runs the SQL on a local SQLite file."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clear-click-country.sh"

STUB = """#!{python}
import json, os, sqlite3, sys
args = sys.argv[1:]
sql = args[args.index("--command") + 1]
db = sqlite3.connect(os.environ["STUB_DB"])
cur = db.execute(sql)
db.commit()
cols = [c[0] for c in cur.description] if cur.description else []
rows = [dict(zip(cols, r)) for r in cur.fetchall()]
print(json.dumps([{{"results": rows, "success": True}}]))
"""


def make(
    tmp_path: Path, counters: list[str], raw_countries: int
) -> tuple[Path, dict[str, str]]:
    db_path = tmp_path / "d1.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE click_country_daily (site, day, link_id, country, clicks)")
    db.execute("CREATE TABLE click_events (id INTEGER PRIMARY KEY, country, src)")
    for day in counters:
        db.execute(
            "INSERT INTO click_country_daily VALUES ('links', ?, 'a', 'US', 3)", (day,)
        )
    for _ in range(raw_countries):
        db.execute("INSERT INTO click_events (country) VALUES ('US')")
    db.execute("INSERT INTO click_events (country, src) VALUES (NULL, 'instagram')")
    db.commit()
    stub = tmp_path / "wrangler-stub"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o700)
    env = {"PATH": "/usr/bin:/bin", "WRANGLER": str(stub), "STUB_DB": str(db_path)}
    return db_path, env


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/usr/bin/bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def left(db_path: Path) -> int:
    return int(
        sqlite3.connect(db_path)
        .execute("SELECT count(*) FROM click_events WHERE country IS NOT NULL")
        .fetchone()[0]
    )


def today(offset: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=offset)).date().isoformat()


def test_a_dry_run_counts_and_changes_nothing(tmp_path: Path) -> None:
    db_path, env = make(tmp_path, [today()], 4)
    r = run(env, "reach-db")
    assert r.returncode == 0, r.stderr
    assert "holding a country: 4" in r.stdout and "dry run" in r.stdout
    assert left(db_path) == 4


def test_apply_clears_the_country_and_keeps_the_source(tmp_path: Path) -> None:
    db_path, env = make(tmp_path, [today(1)], 4)
    r = run(env, "reach-db", "--apply")
    assert r.returncode == 0, r.stderr
    assert "still holding a country: 0" in r.stdout
    assert left(db_path) == 0
    kept = sqlite3.connect(db_path).execute(
        "SELECT count(*) FROM click_events WHERE src = 'instagram'"
    )
    assert kept.fetchone()[0] == 1


@pytest.mark.parametrize("days", [[], [today(10)]])
def test_it_refuses_while_the_counters_are_empty_or_stale(
    tmp_path: Path, days: list[str]
) -> None:
    db_path, env = make(tmp_path, days, 4)
    for extra in ([], ["--apply"]):
        r = run(env, "reach-db", *extra)
        assert r.returncode == 3 and "refused" in r.stderr
    assert left(db_path) == 4


@pytest.mark.parametrize(
    "args", [[], ["a", "b"], ["-x"], ["bad name"], ["db", "--force"]]
)
def test_usage_errors(tmp_path: Path, args: list[str]) -> None:
    _, env = make(tmp_path, [today()], 1)
    assert run(env, *args).returncode == 2


def test_a_failing_query_stops_with_exit_1(tmp_path: Path) -> None:
    _, env = make(tmp_path, [today()], 1)
    sqlite3.connect(env["STUB_DB"]).execute(
        "DROP TABLE click_events"
    ).connection.commit()
    assert run(env, "reach-db").returncode == 1
