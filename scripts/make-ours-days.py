#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build ours-days.csv for check-analytics.py from the D1 database, read-only.

Usage: python3 -I scripts/make-ours-days.py --out DIR --since DAY --until DAY
       [--site links] [--beacons FILE]

Runs the two acceptance queries of docs/analytics-acceptance.md for the UTC
days SINCE to UNTIL (both full, past days) and writes
``DIR/ours-days-SINCE_UNTIL.csv`` in the shape check-analytics.py reads. With
``--beacons FILE`` it then runs check-analytics.py on the two files and exits
with its code.

Credentials come only from the environment, never from an argument:
  RA_D1_TOKEN         a Cloudflare API token with D1 Read and nothing else
  RA_D1_ACCOUNT_ID    the account id (32 hex characters)
  RA_D1_DATABASE_ID   the D1 database id (a UUID)

Read-only by construction: every statement is checked to be one plain SELECT
before it is sent, and a response that reports a changed database is refused.
Cloudflare offers no call that says what a token may do, so the script cannot
tell a write-capable token from a read token. It refuses one case it can see:
the same value in CLOUDFLARE_API_TOKEN (the deploy token). Use a token made for
this job.

Output: counts and exit codes only, never a value from the data. The file is
mode 0600 in an existing private directory (mode 0700, not a symlink, not
inside a git work tree), and an existing file is never overwritten.

Exit: 0 done (or the check passed), 1 the check found a mismatch or a gap,
2 usage or refusal, 3 the query failed or returned something unexpected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_DAYS = 31
MAX_BODY = 4 * 1024 * 1024
MAX_ROWS = 10_000
SITE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
ACCOUNT_RE = re.compile(r"[0-9a-f]{32}")
DB_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
API = "https://api.cloudflare.com/client/v4/accounts/{acct}/d1/database/{db}/query"

Runner = Callable[[str, list[Any]], list[dict[str, Any]]]


class Refused(Exception):
    """The script will not do this. The message holds no value from the data."""


class QueryFailed(Exception):
    """The database call failed or answered something unexpected."""


def select_only(inner: Runner) -> Runner:
    """Wrap a runner so only a single plain SELECT ever reaches it."""

    def guarded(sql: str, params: list[Any]) -> list[dict[str, Any]]:
        if not re.match(r"\s*SELECT\s", sql, re.IGNORECASE) or ";" in sql:
            raise Refused("only a single SELECT statement is allowed")
        return inner(sql, params)

    return guarded


VIEWS_SQL = (
    "SELECT day, SUM(views) AS views FROM view_daily "
    "WHERE site = ? AND bot_class = 'h' AND day >= ? AND day <= ? GROUP BY day"
)
CLICKS_SQL = (
    "SELECT date(ts / 1000, 'unixepoch') AS day, COUNT(*) AS clicks "
    "FROM click_events WHERE site = ? AND ts >= ? AND ts < ? GROUP BY day"
)


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def build_queries(
    site: str, since: date, until: date
) -> tuple[tuple[str, list[Any]], tuple[str, list[Any]]]:
    """The two acceptance queries with their bound parameters (no splicing)."""
    views = (VIEWS_SQL, [site, since.isoformat(), until.isoformat()])
    clicks = (CLICKS_SQL, [site, _ms(since), _ms(until + timedelta(days=1))])
    return views, clicks


def credentials(env: Mapping[str, str]) -> tuple[str, str, str] | None:
    """(token, account, database) from the environment, or None if not set."""
    token = env.get("RA_D1_TOKEN", "")
    acct = env.get("RA_D1_ACCOUNT_ID", "")
    db = env.get("RA_D1_DATABASE_ID", "")
    if not (token and acct and db):
        return None
    if env.get("CLOUDFLARE_API_TOKEN") == token:
        raise Refused(
            "RA_D1_TOKEN is the deploy token; use a D1 Read token made for this"
        )
    if not ACCOUNT_RE.fullmatch(acct) or not (
        DB_RE.fullmatch(db) or ACCOUNT_RE.fullmatch(db)
    ):
        raise Refused("account or database id has the wrong shape")
    return token, acct, db


def parse_response(body: str) -> list[dict[str, Any]]:
    """Rows of the one statement in a D1 REST answer; refuse anything odd."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        raise QueryFailed("answer is not JSON") from None
    if not isinstance(data, dict) or data.get("success") is not True:
        raise QueryFailed("the database call reported failure")
    result = data.get("result")
    if (
        not isinstance(result, list)
        or len(result) != 1
        or not isinstance(result[0], dict)
    ):
        raise QueryFailed("unexpected answer shape")
    block = result[0]
    if block.get("success") is not True:
        raise QueryFailed("the statement reported failure")
    meta = block.get("meta")
    if isinstance(meta, dict) and (meta.get("changed_db") or meta.get("rows_written")):
        raise QueryFailed("the answer says the database changed; stopping")
    rows = block.get("results")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise QueryFailed("unexpected rows")
    if not all(isinstance(r, dict) for r in rows):
        raise QueryFailed("unexpected rows")
    return rows


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a: object, **_k: object) -> None:
        return None


def http_runner(token: str, acct: str, db: str) -> Runner:
    url = API.format(acct=acct, db=db)
    opener = urllib.request.build_opener(_NoRedirect)

    def run(sql: str, params: list[Any]) -> list[dict[str, Any]]:
        req = urllib.request.Request(  # noqa: S310 - fixed https URL
            url,
            data=json.dumps({"sql": sql, "params": params}).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with opener.open(req, timeout=30) as resp:  # noqa: S310
                body = resp.read(MAX_BODY + 1)
        except (urllib.error.URLError, OSError, ValueError):
            raise QueryFailed("the database call failed") from None
        if len(body) > MAX_BODY:
            raise QueryFailed("the answer is too large")
        return parse_response(body.decode("utf-8", "replace"))

    return run


def collect(
    rows: list[dict[str, Any]], metric: str, since: date, until: date
) -> dict[date, int]:
    out: dict[date, int] = {}
    for i, r in enumerate(rows, 1):
        day_text, n = r.get("day"), r.get(metric)
        if not isinstance(day_text, str) or len(day_text) != 10:
            raise QueryFailed(f"{metric}: row {i}: day is not YYYY-MM-DD")
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            raise QueryFailed(f"{metric}: row {i}: day is not YYYY-MM-DD") from None
        if not since <= day <= until:
            raise QueryFailed(f"{metric}: row {i}: day is outside the window")
        if day in out:
            raise QueryFailed(f"{metric}: row {i}: day appears twice")
        if isinstance(n, bool) or not isinstance(n, int) or n < 0 or n > 10**12:
            raise QueryFailed(f"{metric}: row {i}: not a whole number")
        out[day] = n
    return out


def merge(
    views: dict[date, int], clicks: dict[date, int]
) -> list[tuple[date, int, int]]:
    """One row per day seen in either query; the other metric is 0 there.

    A day in neither query is left out, so check-analytics reports a gap
    instead of reading a collection outage as a quiet day.
    """
    return [
        (d, views.get(d, 0), clicks.get(d, 0)) for d in sorted(set(views) | set(clicks))
    ]


def in_git_tree(path: Path) -> bool:
    return any((p / ".git").exists() for p in (path, *path.parents))


def check_out_dir(raw: str) -> Path:
    p = Path(raw)
    try:
        st = os.lstat(p)
    except OSError:
        raise Refused("--out must be an existing directory") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise Refused("--out must be a real directory, not a symlink")
    if st.st_mode & 0o077:
        raise Refused("--out must be mode 700 (only you can use it)")
    if st.st_uid != os.getuid():
        raise Refused("--out must be owned by you")
    real = p.resolve()
    repo = Path(__file__).resolve().parents[1]
    if real == repo or repo in real.parents or in_git_tree(real):
        raise Refused(
            "--out is inside a git work tree; an export must never be committed"
        )
    return real


def write_new(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _day(text: str, name: str) -> date:
    try:
        d = date.fromisoformat(text)
    except ValueError:
        raise Refused(f"{name} is not YYYY-MM-DD") from None
    if text != d.isoformat():
        raise Refused(f"{name} is not YYYY-MM-DD")
    return d


def main(
    argv: list[str],
    runner: Runner | None = None,
    today: date | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", required=True, metavar="DIR")
    ap.add_argument("--since", required=True, metavar="DAY")
    ap.add_argument("--until", required=True, metavar="DAY")
    ap.add_argument("--site", default="links")
    ap.add_argument("--beacons", metavar="FILE")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    env = os.environ if env is None else env
    today = today or datetime.now(UTC).date()
    try:
        since, until = _day(args.since, "--since"), _day(args.until, "--until")
        if until < since:
            raise Refused("the window ends before it starts")
        if (until - since).days + 1 > MAX_DAYS:
            raise Refused(f"the window is longer than {MAX_DAYS} days")
        if until >= today:
            raise Refused("--until must be a full past UTC day (today is still open)")
        if not SITE_RE.fullmatch(args.site):
            raise Refused("--site is not a site id")
        out_dir = check_out_dir(args.out)
        target = out_dir / f"ours-days-{since.isoformat()}_{until.isoformat()}.csv"
        if target.exists() or target.is_symlink():
            raise Refused("the output file already exists; it is never overwritten")
        if runner is None:
            creds = credentials(env)
            if creds is None:
                raise Refused("set RA_D1_TOKEN, RA_D1_ACCOUNT_ID and RA_D1_DATABASE_ID")
            runner = http_runner(*creds)
        run = select_only(runner)
        q_views, q_clicks = build_queries(args.site, since, until)
        views = collect(run(*q_views), "views", since, until)
        clicks = collect(run(*q_clicks), "clicks", since, until)
        rows = merge(views, clicks)
        text = "day,views,clicks\n" + "".join(
            f"{d.isoformat()},{v},{c}\n" for d, v, c in rows
        )
        write_new(target, text)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except QueryFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except OSError:
        print("error: could not write the output file", file=sys.stderr)
        return 3
    print(
        f"queries: 2 ok (SELECT only); view days {len(views)}, "
        f"click days {len(clicks)}; wrote {len(rows)} day(s), mode 600"
    )
    if len(rows) < (until - since).days + 1:
        print(
            f"note: {(until - since).days + 1 - len(rows)} day(s) "
            "had no rows and are left out"
        )
    if not args.beacons:
        return 0
    check = Path(__file__).with_name("check-analytics.py")
    r = subprocess.run(  # noqa: S603 - fixed argv, python and a sibling script
        [
            sys.executable,
            "-I",
            str(check),
            "--beacons",
            args.beacons,
            "--ours",
            str(target),
            "--since",
            since.isoformat(),
            "--until",
            until.isoformat(),
        ],
        check=False,
    )
    print(f"check-analytics exit={r.returncode}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
