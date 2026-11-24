# SPDX-License-Identifier: Apache-2.0
"""``leads show`` and ``leads delete --checklist`` (#781): synthetic data only."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main

ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"
ADDR = "partner@example.test"
HOUR = 3_600_000
CREATED = 1_700_000_000_000  # 2023-11-14T22:13:20Z
VERIFIED = 1_700_003_600_000
EVENT_TS = 1_700_002_800_000 - (1_700_002_800_000 % HOUR)  # on the hour


def cli(argv: list[str]) -> int:
    """`main`, with the now mandatory `--database` added to `leads show` and
    `leads delete` unless the test names one (or asks for the checklist)."""
    args = list(argv)
    if (
        args[:1] == ["leads"]
        and args[1:2] in (["show"], ["delete"])
        and "--checklist" not in args
        and "--database" not in args
    ):
        args += ["--database", "reach-around-leads"]
    return main(args)


def write(path: Path, data: Any) -> Path:
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )
    return path


def lead(lead_id: str, email: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": lead_id,
        "email": email,
        "created_ts": CREATED,
        "verified_ts": None,
        "country": None,
    }
    row.update(extra)
    return row


def event(lead_id: str, kind: str, ts: int = EVENT_TS) -> dict[str, Any]:
    return {"lead_id": lead_id, "kind": kind, "ts": ts}


def blocks(*row_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"results": rows, "success": True, "meta": {}} for rows in row_lists]


@pytest.fixture
def export(tmp_path: Path) -> Path:
    leads = [
        lead(ID_A, ADDR, verified_ts=VERIFIED, country="DE"),
        lead(ID_B, "other@example.test", country="FR"),
    ]
    events = [
        event(ID_A, "unlock"),
        event(ID_A, "view", EVENT_TS + HOUR),
        event(ID_B, "view"),
    ]
    return write(tmp_path / "leads.json", blocks(leads, events))


def show(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
    stdin: str = ADDR + "\n",
    *extra: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli(["leads", "show", "--input", str(export), "--email-file", "-", *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_show_prints_only_what_a_request_needs(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    code, out, err = show(capsys, monkeypatch, export)
    assert code == 0
    assert "p***@***.test" in out
    assert "country: DE" in out
    assert "created: 2023-11-14T22:13:20Z" in out
    assert "verified: 2023-11-14T23:13:20Z" in out
    assert "unlock 2023-11-14T23:00Z" in out
    assert "view 2023-11-15T00:00Z" in out
    # Nothing about the other lead, no id, no full address, no other column.
    for text in (out, err):
        assert ADDR not in text
        assert ID_A not in text and ID_B not in text
        assert "other" not in text and "FR" not in text


def test_show_unverified_and_no_events(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "e.json", blocks([lead(ID_A, ADDR)], []))
    code, out, _ = show(capsys, monkeypatch, path)
    assert code == 0
    assert "verified: no" in out
    assert "country: unknown" in out
    assert "events: none" in out


def test_show_refuses_an_export_that_lacks_the_kit_events_result(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Only the leads query was exported: "events: none" would be a false
    # statement, so the export is refused. An empty second result is fine.
    cases = {
        "leads-only": blocks([lead(ID_A, ADDR)]),
        "three-blocks": blocks([lead(ID_A, ADDR)], [], []),
        "swapped": blocks([event(ID_A, "view")], [lead(ID_A, ADDR)]),
        "events-in-first": blocks([lead(ID_A, ADDR), event(ID_A, "view")], []),
        "bare-rows": [lead(ID_A, ADDR)],
    }
    for name, data in cases.items():
        path = write(tmp_path / f"{name}.json", data)
        code, out, err = show(capsys, monkeypatch, path)
        assert code == 2 and out == "", name
        assert ADDR not in err and "events: none" not in out, name
        assert "two result sets" in err, name


def test_show_no_match_exit_1(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    code, out, err = show(capsys, monkeypatch, export, "nobody@example.test\n")
    assert code == 1
    assert out == ""
    # No form of the address, masked or not, is written to stderr (it can reach
    # a log). The message is fixed.
    assert "nobody@example.test" not in err
    assert "n***" not in err and "***" not in err
    assert "no lead matches the supplied address" in err


def test_show_duplicate_match_refuses(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "e.json", blocks([lead(ID_A, ADDR), lead(ID_B, ADDR)], []))
    code, out, err = show(capsys, monkeypatch, path)
    assert code == 2
    assert out == ""
    assert ADDR not in err
    assert "***" not in err
    assert "more than one lead matches the supplied address" in err


def test_show_reads_address_from_a_file(
    capsys: pytest.CaptureFixture[str], export: Path, tmp_path: Path
) -> None:
    addr_file = write(tmp_path / "addr.txt", ADDR + "\n")
    code = cli(
        ["leads", "show", "--input", str(export), "--email-file", str(addr_file)]
    )
    out = capsys.readouterr().out
    assert code == 0 and "p***@***.test" in out


@pytest.mark.parametrize(
    "bad_stdin",
    ["not an address\n", "'; DROP TABLE leads;--@example.test\n", "x" * 400, ""],
)
def test_show_bad_address_never_echoed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
    bad_stdin: str,
) -> None:
    code, out, err = show(capsys, monkeypatch, export, bad_stdin)
    assert code == 2
    assert out == ""
    for text in (out, err):
        assert bad_stdin.strip() == "" or bad_stdin.strip() not in text


def test_show_unreadable_address_file_does_not_leak_path_or_content(
    capsys: pytest.CaptureFixture[str], export: Path, tmp_path: Path
) -> None:
    code = cli(
        ["leads", "show", "--input", str(export), "--email-file", str(tmp_path / ADDR)]
    )
    err = capsys.readouterr().err
    assert code == 2
    assert ADDR not in err


def test_show_email_on_argv_is_refused_without_echo(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    # A valid address on argv must not be used even when stdin also has one.
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = cli(
        ["leads", "show", "--input", str(export), "--email-file", "-", "--email", ADDR]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert ADDR not in captured.err


def test_show_abbreviated_flag_reads_the_value_as_a_path_not_an_address(
    capsys: pytest.CaptureFixture[str], export: Path
) -> None:
    code = cli(["leads", "show", "--input", str(export), "--email-f", ADDR])
    captured = capsys.readouterr()
    assert code == 2
    assert ADDR not in captured.out + captured.err


def test_show_no_sql_is_ever_printed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    _, out, err = show(capsys, monkeypatch, export)
    for word in ("DELETE", "SELECT", "INSERT", "UPDATE"):
        assert word not in out + err


@pytest.mark.parametrize(
    "rows",
    [
        # a column outside the leads table (an IP address)
        [lead(ID_A, ADDR, ip="192.0.2.1")],
        # an event row with an extra column
        [event(ID_A, "view") | {"ua": "x"}],
        # an event with a kind outside the schema
        [event(ID_A, "purchase")],
        # an event with a bad lead id
        [event("NOT-HEX", "view")],
        # a negative or boolean timestamp
        [lead(ID_A, ADDR, created_ts=-1)],
        [lead(ID_A, ADDR, verified_ts=True)],
        # a country that is not two capital letters
        [lead(ID_A, ADDR, country="de")],
        [lead(ID_A, ADDR, country="DEU")],
        # a non-normalized stored address
        [lead(ID_A, "Partner@Example.test")],
    ],
)
def test_show_refuses_a_hostile_export(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    if rows and "lead_id" in rows[0]:
        data = blocks([lead(ID_A, ADDR)], rows)
    else:
        data = blocks(rows, [])
    path = write(tmp_path / "e.json", data)
    code, out, err = show(capsys, monkeypatch, path)
    assert code == 2
    assert out == ""
    assert ADDR not in out + err


def test_show_refuses_failed_result_block(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = blocks([lead(ID_A, ADDR)], [])
    data[1]["success"] = False
    code, out, _ = show(capsys, monkeypatch, write(tmp_path / "e.json", data))
    assert code == 2 and out == ""


def test_show_events_of_other_leads_never_attributed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path / "e.json",
        blocks([lead(ID_A, ADDR)], [event(ID_B, "verify")]),
    )
    _, out, _ = show(capsys, monkeypatch, path)
    assert "verify" not in out
    assert "events: none" in out


def test_existing_delete_still_rejects_the_richer_export(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    """Delete keeps its strict id/email contract: event rows are refused."""
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = cli(["leads", "delete", "--input", str(export), "--email", "-"])
    assert code == 2
    assert ADDR not in capsys.readouterr().err


def wrangler_two_statement_output(
    leads: list[dict[str, Any]], events: list[dict[str, Any]]
) -> str:
    """The shape of wrangler d1 execute --remote --json with two statements.

    Sources: the D1 result object (success, meta with the keys below, results
    as an array; developers.cloudflare.com/d1/worker-api/return-object, read
    2026-11-24) and the wrangler `--command` flag, which takes several queries
    separated by ';' (developers.cloudflare.com/d1/wrangler-commands, read
    2026-11-24). INFERRED, not captured from a live run: that wrangler prints
    one such object per statement, in one JSON array, with no other text on
    stdout. If a live capture differs, replace this builder with the capture.
    """

    def result(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "results": rows,
            "success": True,
            "meta": {
                "served_by": "v3-prod",
                "served_by_region": "ENAM",
                "served_by_primary": True,
                "timings": {"sql_duration_ms": 0.3},
                "duration": 0.3,
                "changes": 0,
                "last_row_id": 0,
                "changed_db": False,
                "size_after": 28672,
                "rows_read": len(rows),
                "rows_written": 0,
                "total_attempts": 1,
            },
        }

    return json.dumps([result(leads), result(events)], indent=2)


def test_show_reads_the_documented_wrangler_output_shape(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    leads = [
        {
            "id": ID_A,
            "email": ADDR,
            "created_ts": CREATED,
            "verified_ts": None,
            "country": "DE",
        }
    ]
    events = [{"lead_id": ID_A, "kind": "view", "ts": EVENT_TS + 5}]
    path = write(tmp_path / "w.json", wrangler_two_statement_output(leads, events))
    code, out, _ = show(capsys, monkeypatch, path)
    assert code == 0
    assert "country: DE" in out
    assert "view 2023-11-14T23:00Z" in out


def test_show_lead_rows_must_have_exactly_the_documented_columns(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A column of the leads table that the access export does not select, and a
    # missing selected column, are both a wrong export: refused.
    extra = lead(ID_A, ADDR, last_seen_ts=CREATED)
    missing = lead(ID_A, ADDR)
    del missing["country"]
    for row in (extra, missing):
        path = write(tmp_path / "e.json", blocks([row], []))
        code, out, err = show(capsys, monkeypatch, path)
        assert code == 2 and out == ""
        assert ADDR not in err


def test_show_accepts_event_times_that_are_not_on_the_hour(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = [event(ID_A, "view", EVENT_TS + 2_800_005)]
    path = write(tmp_path / "e.json", blocks([lead(ID_A, ADDR)], rows))
    code, out, _ = show(capsys, monkeypatch, path)
    assert code == 0
    # Shown with its hour only; the minutes and seconds are dropped.
    assert "view 2023-11-14T23:00Z" in out


@pytest.mark.parametrize("second_addr", [ADDR, "other@example.test"])
def test_show_refuses_a_lead_id_that_appears_twice(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    second_addr: str,
) -> None:
    for rows in (
        [lead(ID_A, ADDR), lead(ID_A, second_addr, country="FR")],
        [lead(ID_A, second_addr, country="FR"), lead(ID_A, ADDR)],
    ):
        path = write(tmp_path / "e.json", blocks(rows, []))
        code, out, err = show(capsys, monkeypatch, path)
        assert code == 2 and out == ""
        assert "a lead id appears more than once" in err
        # The warning never carries lead data.
        for text in (ADDR, second_addr, ID_A, "FR", "***"):
            assert text not in err


def test_show_database_is_named(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    code, out, err = show(
        capsys, monkeypatch, export, ADDR + "\n", "--database", "reach-around-leads"
    )
    assert code == 0 and "database: reach-around-leads" in err


@pytest.mark.parametrize(
    "name",
    [
        "reach-around-clicks",
        "analytics",
        "DB",
        "x;rm -rf",
        "leads" + "x" * 80,
        # contained "leads" but are not the leads database
        "analytics-leads",
        "leads",
        "LEADS-backup",
        "Reach-Around-Leads",
        "my-leads-db",
    ],
)
def test_show_refuses_an_export_from_a_database_that_is_not_the_leads_one(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
    name: str,
) -> None:
    code, out, err = show(capsys, monkeypatch, export, ADDR + "\n", "--database", name)
    assert code == 2 and out == ""
    assert ADDR not in err and "***" not in err


@pytest.fixture
def delete_export(tmp_path: Path) -> Path:
    """The two-column export that `leads delete` takes."""
    rows = [{"id": ID_A, "email": ADDR}, {"id": ID_B, "email": "other@example.test"}]
    return write(tmp_path / "d.json", blocks(rows))


def test_delete_names_the_leads_database_and_refuses_the_analytics_one(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
) -> None:
    export = delete_export
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = cli(["leads", "delete", "--input", str(export), "--email", "-", "--yes"])
    captured = capsys.readouterr()
    assert code == 0
    assert "database: reach-around-leads" in captured.err
    assert (
        "npx wrangler d1 execute reach-around-leads --remote --file delete-lead.sql"
        in captured.err
    )
    assert "reach-around-clicks" not in captured.out + captured.err
    for db in ("reach-around-clicks", "analytics"):
        monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
        code = cli(
            [
                "leads",
                "delete",
                "--input",
                str(export),
                "--email",
                "-",
                "--yes",
                "--database",
                db,
            ]
        )
        captured = capsys.readouterr()
        assert code == 2
        assert "DELETE" not in captured.out + captured.err
        assert ADDR not in captured.err


@pytest.mark.parametrize("name", ["analytics-leads", "leads", "LEADS-backup"])
def test_delete_refuses_a_database_name_that_only_contains_leads(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
    name: str,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    argv = ["leads", "delete", "--input", str(delete_export), "--email", "-", "--yes"]
    code = cli([*argv, "--database", name])
    captured = capsys.readouterr()
    assert code == 2
    assert "DELETE" not in captured.out + captured.err
    assert "wrangler" not in captured.out + captured.err


@pytest.mark.parametrize("second_addr", [ADDR, "other@example.test"])
def test_delete_refuses_a_lead_id_that_appears_twice(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    second_addr: str,
) -> None:
    rows = [{"id": ID_A, "email": ADDR}, {"id": ID_A, "email": second_addr}]
    path = write(tmp_path / "d.json", blocks(rows))
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = cli(["leads", "delete", "--input", str(path), "--email", "-", "--yes"])
    captured = capsys.readouterr()
    assert code == 2
    assert "DELETE" not in captured.out
    assert "a lead id appears more than once" in captured.err
    for text in (ADDR, second_addr, ID_A):
        assert text not in captured.err


def test_delete_success_writes_no_form_of_the_address_to_stderr(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    argv = ["leads", "delete", "--input", str(delete_export), "--email", "-", "--yes"]
    code = cli(argv)
    err = capsys.readouterr().err
    assert code == 0
    assert "***" not in err and "partner" not in err


def test_delete_refuses_the_address_on_the_command_line(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
) -> None:
    # Round 3: the address on the command line is refused outright, before the
    # export is read (a missing file would otherwise be the first error).
    missing = delete_export.with_name("missing.json")
    for flags in (["--yes"], []):
        code = cli(
            ["leads", "delete", "--input", str(missing), "--email", ADDR, *flags]
        )
        captured = capsys.readouterr()
        assert code == 2 and captured.out == ""
        assert ADDR not in captured.err
        assert "not accepted on the command line" in captured.err
        assert "cannot read" not in captured.err
    # The stdin form is accepted.
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = cli(["leads", "delete", "--input", str(delete_export), "--email", "-"])
    assert code == 0 and "not accepted" not in capsys.readouterr().err


def _sqlite_with(rows: list[tuple[str, str]], events: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT UNIQUE)")
    conn.execute("CREATE TABLE kit_events (id INTEGER PRIMARY KEY, lead_id TEXT)")
    conn.executemany("INSERT INTO leads VALUES (?, ?)", rows)
    conn.executemany(
        "INSERT INTO kit_events (lead_id) VALUES (?)", [(e,) for e in events]
    )
    return conn


def _statements(sql: str) -> list[str]:
    out = []
    for statement in [s for s in sql.split(";\n") if s.strip()]:
        body = "\n".join(
            ln for ln in statement.splitlines() if not ln.startswith("--")
        ).strip()
        if body:
            out.append(body)
    return out


def _apply(
    conn: sqlite3.Connection, sql: str, *, only: tuple[int, ...] | None = None
) -> tuple[int, int]:
    """Run the printed SQL (or only the numbered statements); return the
    read-back as (leads remaining, kit events remaining). Success is (0, 0)."""
    result = (-1, -1)
    for index, body in enumerate(_statements(sql)):
        if only is not None and index not in only:
            continue
        cursor = conn.execute(body)
        if body.startswith("SELECT"):
            row = cursor.fetchone()
            result = (row[0], row[1])
    return result


def test_delete_sql_deletes_only_the_row_that_still_pairs_the_id_and_address(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A forged or stale export claims ID_A belongs to the requester. In the
    # live database ID_A belongs to someone else.
    forged = write(tmp_path / "forged.json", blocks([{"id": ID_A, "email": ADDR}]))
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = cli(["leads", "delete", "--input", str(forged), "--email", "-", "--yes"])
    sql = capsys.readouterr().out
    assert code == 0
    live = _sqlite_with([(ID_A, "someone-else@example.test")], [ID_A, ID_A])
    remaining = _apply(live, sql)
    # Nothing was deleted, and the read-back says so (not (0, 0)).
    assert remaining == (1, 2)
    assert live.execute("SELECT COUNT(*) FROM kit_events").fetchone()[0] == 2
    # The honest case: the live row still pairs the id and the address.
    honest = _sqlite_with(
        [(ID_A, ADDR), (ID_B, "other@example.test")], [ID_A, ID_A, ID_A, ID_B, ID_B]
    )
    assert _apply(honest, sql) == (0, 0)
    assert honest.execute("SELECT id FROM leads").fetchall() == [(ID_B,)]
    assert honest.execute("SELECT COUNT(*) FROM kit_events").fetchone()[0] == 2


def _printed_sql(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> str:
    path = write(tmp_path / "ok.json", blocks([{"id": ID_A, "email": ADDR}]))
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    assert cli(["leads", "delete", "--input", str(path), "--email", "-", "--yes"]) == 0
    return capsys.readouterr().out


def test_the_read_back_is_not_zero_when_only_the_lead_row_was_deleted(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sql = _printed_sql(capsys, monkeypatch, tmp_path)
    statements = _statements(sql)
    # Run only the lead deletion and the read-back: events remain, so the
    # read-back must not say success.
    leads_delete = next(
        i for i, s in enumerate(statements) if s.startswith("DELETE FROM leads")
    )
    last = len(statements) - 1
    live = _sqlite_with([(ID_A, ADDR)], [ID_A, ID_A, ID_A])
    assert _apply(live, sql, only=(leads_delete, last)) == (0, 3)


def test_events_added_between_the_deletes_are_cleaned_up_by_the_statements(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sql = _printed_sql(capsys, monkeypatch, tmp_path)
    statements = _statements(sql)
    live = _sqlite_with([(ID_A, ADDR)], [ID_A, ID_A])
    # Run every statement but let an event arrive after the first delete.
    for index, body in enumerate(statements):
        live.execute(body)
        if index == 0:
            live.execute("INSERT INTO kit_events (lead_id) VALUES (?)", (ID_A,))
    row = live.execute(statements[-1]).fetchone()
    assert (row[0], row[1]) == (0, 0)


def _delete_to(monkeypatch: pytest.MonkeyPatch, export_path: Path, target: Path) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    return cli(
        [
            "leads",
            "delete",
            "--input",
            str(export_path),
            "--email",
            "-",
            "--yes",
            "--sql-out",
            str(target),
        ]
    )


def test_sql_out_creates_a_private_file_and_prints_no_sql(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "delete-lead.sql"
    old = os.umask(0)  # the tool must not depend on the shell's umask
    try:
        code = _delete_to(monkeypatch, delete_export, target)
    finally:
        os.umask(old)
    captured = capsys.readouterr()
    assert code == 0
    assert "DELETE" not in captured.out
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "DELETE FROM leads" in target.read_text(encoding="utf-8")
    assert ADDR not in captured.err


def test_sql_out_refuses_an_existing_file_and_a_symlink(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "exists.sql"
    existing.write_text("keep", encoding="utf-8")
    assert _delete_to(monkeypatch, delete_export, existing) == 2
    assert existing.read_text(encoding="utf-8") == "keep"
    victim = tmp_path / "victim.txt"
    victim.write_text("precious", encoding="utf-8")
    link = tmp_path / "link.sql"
    os.symlink(victim, link)
    assert _delete_to(monkeypatch, delete_export, link) == 2
    assert victim.read_text(encoding="utf-8") == "precious"
    dangling = tmp_path / "dangling.sql"
    os.symlink(tmp_path / "not-there.txt", dangling)
    assert _delete_to(monkeypatch, delete_export, dangling) == 2
    assert not (tmp_path / "not-there.txt").exists()
    captured = capsys.readouterr()
    assert "DELETE" not in captured.out and ADDR not in captured.err


def test_sql_out_needs_yes(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    target = tmp_path / "x.sql"
    argv = ["leads", "delete", "--input", str(delete_export), "--email", "-"]
    code = cli([*argv, "--sql-out", str(target)])
    assert code == 2 and not target.exists()
    capsys.readouterr()


def test_show_refuses_an_export_that_is_a_symlink(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
    tmp_path: Path,
) -> None:
    link = tmp_path / "link.json"
    os.symlink(export, link)
    code, out, err = show(capsys, monkeypatch, link)
    assert code == 2 and out == ""
    assert ADDR not in err


def test_cleanup_never_deletes_events_of_a_lead_that_still_exists(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sql = _printed_sql(capsys, monkeypatch, tmp_path)
    live = _sqlite_with([(ID_A, "someone-else@example.test")], [ID_A, ID_A])
    assert _apply(live, sql) == (1, 2)


@pytest.mark.parametrize(
    ("lead_id", "email"),
    [
        ("x'; DROP TABLE leads;--", ADDR),
        (ID_A.upper(), ADDR),
        (ID_A + "0", ADDR),
        (ID_A, "a'b@example.test"),
        (ID_A, "a@example.test'; DELETE FROM leads;--"),
        (ID_A, "A@Example.test"),
    ],
)
def test_delete_sql_builder_refuses_anything_the_tool_did_not_validate(
    lead_id: str, email: str
) -> None:
    from reach_around.commands.leads import LeadsError, _sql

    with pytest.raises(LeadsError):
        _sql(lead_id, email)


def test_delete_requires_a_database_name_and_show_too(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
    delete_export: Path,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = main(["leads", "show", "--input", str(export), "--email-file", "-"])
    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert "--database is required" in captured.err
    monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
    code = main(
        ["leads", "delete", "--input", str(delete_export), "--email", "-", "--yes"]
    )
    captured = capsys.readouterr()
    assert code == 2 and "DELETE" not in captured.out
    assert "--database is required" in captured.err


def test_the_database_line_says_what_is_and_is_not_proven(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    export: Path,
) -> None:
    code, _, err = show(capsys, monkeypatch, export)
    assert code == 0
    assert "database: reach-around-leads" in err
    assert "named by you" in err and "does not prove" in err


def test_delete_errors_never_carry_the_masked_address(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    delete_export: Path,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("nobody@example.test\n"))
    code = cli(["leads", "delete", "--input", str(delete_export), "--email", "-"])
    err = capsys.readouterr().err
    assert code == 1
    assert "***" not in err and "nobody" not in err


def test_checklist_claims_only_what_the_tool_checks_and_lists_the_hand_checks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["leads", "delete", "--checklist"])
    out = capsys.readouterr().out
    assert code == 0
    lowered = out.lower()
    # No completeness claim; an explicit statement of what is and is not checked.
    assert "every copy" not in lowered and "all copies" not in lowered
    assert "does not check" in lowered or "checks nothing" in lowered
    # Places the owner must check by hand, beyond the first round's list.
    for needle in (
        "Worker log",
        "collector",
        "delivery log",
        "automated backup",
        "CI",
        "monitoring",
    ):
        assert needle in out, needle


def test_checklist_names_the_leads_database_and_takes_no_database_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli(["leads", "delete", "--checklist"])
    assert code == 0
    assert "reach-around-leads" in capsys.readouterr().out
    code = cli(["leads", "delete", "--checklist", "--database", "reach-around-leads"])
    assert code == 2


class TestChecklist:
    def test_names_every_copy(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = cli(["leads", "delete", "--checklist"])
        out = capsys.readouterr().out
        assert code == 0
        for needle in (
            "leads",
            "kit_events",
            "Discord",
            "Matrix",
            "mailbox",
            "inquiry",
            "Time Travel",
            "7 days",
            "30 days",
            "export",
        ):
            assert needle in out, needle

    def test_needs_no_input_and_prints_no_sql_or_address(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli(["leads", "delete", "--checklist"])
        assert code == 0
        out = capsys.readouterr().out
        assert "DELETE FROM" not in out
        assert "@" not in out

    def test_refuses_to_mix_with_an_address_or_input(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code = cli(["leads", "delete", "--checklist", "--email", ADDR])
        captured = capsys.readouterr()
        assert code == 2
        assert ADDR not in captured.out + captured.err
        with pytest.raises(SystemExit) as raised:
            main(["leads", "delete", "--checklist", "--yes"])
        assert raised.value.code == 2

    def test_delete_without_checklist_still_needs_input_and_email(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli(["leads", "delete"])
        assert code == 2
        assert "--input" in capsys.readouterr().err
