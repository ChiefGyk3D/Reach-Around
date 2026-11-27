# SPDX-License-Identifier: Apache-2.0
"""``reach-around leads delete``: synthetic addresses only (example.test)."""

from __future__ import annotations

import io
import json
import os
import shlex
import sqlite3
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from leads_fakes import ALERTS_UUID, LEADS_UUID
from reach_around.cli import main
from reach_around.commands import leads as leads_cmd
from reach_around.commands.leads import (
    LeadsError,
    alert_readback_sql,
    mask_email,
    normalize_email,
)

ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"
ADDR = "partner@example.test"
LEADS_DB = "reach-around-leads"
ALERTS_DB = "ra-alerts"
# Step 1 passed its checks and printed the SQL; nothing is erased (#905 round 2).
CHECKED = leads_cmd.EXIT_CHECKED
LOCAL_NSS = "passwd: files\ngroup: files\n"

_FAKE: dict[str, Any] = {}


class _PipeProc:
    """A process stand-in for the real runner's tests: its stdout is a real pipe
    fed by a thread (`None` = a child that stays silent and never closes it)."""

    # A pid no process can have (above any pid_max): the runner signals the whole
    # process group on a cap or timeout, so this stand-in must name no real group.
    pid = 2**31 - 2

    def __init__(self, data: bytes | None) -> None:
        rfd, self._wfd = os.pipe()
        self.stdout = os.fdopen(rfd, "rb", buffering=0)
        self.killed = False
        self.written = 0
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._feed, args=(data,), daemon=True)
        self._thread.start()

    def _feed(self, data: bytes | None) -> None:
        try:
            if data is None:
                self._done.wait(5)
                return
            view = memoryview(data)
            while view and not self._done.is_set():
                sent = os.write(self._wfd, view[:65536])
                self.written += sent
                view = view[sent:]
        except OSError:
            pass  # the reader closed its end: the runner stopped reading
        finally:
            os.close(self._wfd)

    def kill(self) -> None:
        self.killed = True
        self._done.set()

    def wait(self, timeout: float | None = None) -> int:
        self._thread.join(5)
        return -9 if self.killed else 0


@pytest.fixture(autouse=True)
def _install_fake_wrangler(fake_wrangler: Any) -> Any:
    """Every test here runs `leads delete` against the in-memory fake wrangler."""
    _FAKE["w"] = fake_wrangler
    return fake_wrangler


def block(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The shape `wrangler d1 execute --json` prints."""
    return [{"results": rows, "success": True, "meta": {}}]


def write(path: Path, data: Any) -> Path:
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )
    return path


@pytest.fixture
def export(tmp_path: Path) -> Path:
    rows = [
        {"id": ID_A, "email": ADDR},
        {"id": ID_B, "email": "other@example.test"},
    ]
    return write(tmp_path / "leads.json", block(rows))


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """Run `leads delete`. Since round 3 the address is accepted only from stdin
    (`--email -`) and `--database` is mandatory, so this helper moves a value
    given as `--email ADDRESS` onto stdin and adds the database name. The
    assertions of the tests that use it are unchanged."""
    args = list(argv)
    omit = "--omit-alerts-database" in args
    omit_ids = "--omit-ids" in args
    args = [a for a in args if a not in ("--omit-alerts-database", "--omit-ids")]
    legacy = "--legacy-database" in args or "--confirm-legacy" in args
    if "--yes" in args and not legacy:
        if "--alerts-database" not in args and not omit:
            args += ["--alerts-database", ALERTS_DB]
        if not omit_ids:
            # The ids the owner copies from the Worker's config (#905 round 2).
            named = args[args.index("--alerts-database") + 1] if not omit else ""
            if "--leads-database-id" not in args:
                args += ["--leads-database-id", LEADS_UUID]
            if "--alerts-database-id" not in args:
                known = _FAKE["w"].ids.get(named, ALERTS_UUID)
                args += ["--alerts-database-id", known]
    if "--yes" in args and "--wrangler" not in args:
        args += ["--wrangler", str(_FAKE["w"].path)]
    stdin: str | None = None
    if "--email" in args:
        at = args.index("--email") + 1
        if args[at] != "-":
            stdin, args[at] = args[at] + "\n", "-"
    if "--checklist" not in args and "--database" not in args and not legacy:
        args += ["--database", "reach-around-leads"]
    old = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        code = main(["leads", "delete", *args])
    finally:
        sys.stdin = old
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def main_run_without_wrangler(
    capsys: pytest.CaptureFixture[str], export: Path
) -> tuple[int, str, str]:
    """`leads delete --yes` with no --wrangler: the default resolution applies."""
    old = sys.stdin
    sys.stdin = io.StringIO(ADDR + "\n")
    try:
        code = main(
            ["leads", "delete", "--input", str(export), "--email", "-", "--yes"]
            + ["--database", LEADS_DB, "--alerts-database", ALERTS_DB]
            + ["--leads-database-id", LEADS_UUID, "--alerts-database-id", ALERTS_UUID]
        )
    finally:
        sys.stdin = old
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestNormalize:
    def test_lowercases_and_strips_outer_space(self) -> None:
        assert normalize_email("  Partner+Tag@Example.TEST \n") == (
            "partner+tag@example.test"
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "no-at-sign",
            "a@b",
            "a@@example.test",
            "a b@example.test",
            "a@exa mple.test",
            "é@example.test",
            "a@éxample.test",
            "a@example.test\x00",
            "a@example.test\nb@example.test",
            "'; DROP TABLE leads;--@example.test",
            'a"b@example.test',
            "a@example.123",
            "a@192.0.2.1",
            "a@[192.0.2.1]",
            ".a@example.test",
            "a..b@example.test",
            "a@-example.test",
            "x" * 65 + "@example.test",
            "a@" + "x" * 64 + ".test",
            "a@example." + "t" * 300,
            "<a@example.test>",
            "a@example.test,b@example.test",
            "a%40example.test",
            # Malformed A-labels: the Worker's isValidALabel rejects these too
            # (shared vectors, see worker/test/kit/punycode.test.ts).
            "a@xn--abc.com",
            "a@xn--a.example.test",
            "a@xn--abc-.example.test",
            "a@xn--bcher-kva9.example.test",
            "a@xn--ascii-.example.test",
            "a@example.xn--abc",
            "a@ab--cd.example.test",
            # Valid shape that decodes but does not round trip (Worker vectors).
            "a@xn--abc-spef.example.test",
            "a@xn--a-ubb.example.test",
            "a@xn--1-ymc2p.example.test",
            # Right-to-left labels are out of scope for the gate (Worker too).
            "a@xn--" + "אב".encode("punycode").decode() + ".example.test",
        ],
    )
    def test_refuses_bad_addresses_without_echoing_them(self, bad: str) -> None:
        with pytest.raises(LeadsError) as info:
            normalize_email(bad)
        shown = bad.strip()
        if shown:
            assert shown not in str(info.value)

    @pytest.mark.parametrize(
        "good",
        [
            "a@xn--bcher-kva.example.test",
            "a@example.xn--p1ai",
            "a@xn--fiq228c.xn--fiq228c",
        ],
    )
    def test_accepts_valid_a_labels(self, good: str) -> None:
        assert normalize_email(good) == good

    def test_mask_hides_local_part_and_domain_name(self) -> None:
        masked = mask_email(ADDR)
        assert masked == "p***@***.test"
        assert "partner" not in masked and "example" not in masked
        assert mask_email("a@b.test") == "a***@***.test"


class TestDryRunDefault:
    def test_dry_run_changes_nothing_and_prints_no_sql_or_address(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        before = export.read_bytes()
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR)
        assert code == 0
        text = out + err
        assert "dry run" in text.lower()
        assert "1 lead" in text
        assert "p***@***.test" in text
        assert "partner" not in text and "example.test" not in text
        assert "DELETE" not in text and ID_A not in text
        assert export.read_bytes() == before

    def test_explicit_dry_run_flag_is_accepted_but_not_with_yes(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--dry-run")
        assert code == 0 and "DELETE" not in out
        with pytest.raises(SystemExit) as info:
            run(
                capsys,
                "--input",
                str(export),
                "--email",
                ADDR,
                "--dry-run",
                "--yes",
            )
        assert info.value.code == 2
        capsys.readouterr()


class TestYes:
    def test_emits_only_id_based_sql_on_stdout(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED  # step 1 is not an erasure, so it is not exit 0
        statements = [ln for ln in out.splitlines() if ln and not ln.startswith("--")]
        # Round 3: every statement is bound to the id AND the normalized address,
        # so the SQL deletes only if the live row still pairs them. The address
        # is therefore in the SQL (stdout) but never in the messages (stderr).
        pair = f"id = '{ID_A}' AND email = '{ADDR}'"
        # The strings below are expected text, never executed.
        expected = [
            "DELETE FROM kit_events WHERE lead_id IN "  # noqa: S608
            f"(SELECT id FROM leads WHERE {pair});",  # noqa: S608
            f"DELETE FROM leads WHERE {pair};",  # noqa: S608
            # Round 4: events that arrive after the first delete are removed once
            # the lead is gone, and the read-back counts both tables.
            f"DELETE FROM kit_events WHERE lead_id = '{ID_A}' AND NOT EXISTS "  # noqa: S608
            f"(SELECT 1 FROM leads WHERE id = '{ID_A}');",
        ]
        assert statements == expected
        # D1 returns no rows for a file run (one summary row only, wrangler 4.162.0,
        # checked 2026-11-27), so the read-back is not in the file: the tool prints it
        # as a separate --command, with the id and never the address.
        assert "leads_remaining" not in out
        read_back = (
            f"SELECT (SELECT COUNT(*) FROM leads WHERE id = '{ID_A}') "  # noqa: S608
            "AS leads_remaining, "
            f"(SELECT COUNT(*) FROM kit_events WHERE lead_id = '{ID_A}') "  # noqa: S608
            "AS events_remaining;"
        )
        line = err[err.rindex("\n", 0, err.rindex(" --json --command ")) + 1 :]
        tokens = shlex.split(line)
        assert tokens[-3:-1] == ["--json", "--command"] and tokens[-1] == read_back
        assert ID_B not in out
        assert "partner" not in err and "example.test" not in err
        # Fixed message: no form of the address reaches stderr (it can be logged).
        assert "1 lead matches." in err and "***" not in err

    def test_the_first_step_checks_the_schemas_and_changes_nothing(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
    ) -> None:
        """#905: `--yes` reads both schemas through the pinned wrangler (read-only,
        one `sqlite_master` statement per database) before it prints any SQL, and
        tells the owner the erasure is not complete until `--finish`."""
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and "alert_dedup" not in out
        assert [db for db, _ in fake_wrangler.calls] == [LEADS_DB, ALERTS_DB]
        assert all("sqlite_master" in sql for _, sql in fake_wrangler.calls)
        # Identity before and after each schema read (round 2: check, call, check).
        assert fake_wrangler.infos == [LEADS_DB, LEADS_DB, ALERTS_DB, ALERTS_DB]
        assert "NOTHING HAS BEEN REMOVED YET" in err and "--finish" in err
        assert f"exit {CHECKED}" in err and "Exit 0 is" not in err
        last = err.rstrip().splitlines()[-1]
        # Round 4 (#905, Low): the apply command names wrangler relative to the
        # repository root, never by its absolute path.
        real = fake_wrangler.path.resolve()
        rel = real.relative_to(fake_wrangler.root.resolve())
        assert last.startswith(f"{rel} d1 execute {LEADS_DB} --remote")
        assert str(fake_wrangler.root.resolve()) not in err + out
        assert "npx" not in err and "example.test" not in err
        _, out2, err2 = run(capsys, "--input", str(export), "--email", ADDR)
        assert "alert_dedup" not in out2 + err2  # a dry run prints nothing about it
        assert len(fake_wrangler.calls) == 2  # and the dry run made no call

    def test_the_dry_run_and_the_checklist_make_no_call(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        run(capsys, "--input", str(export), "--email", ADDR)
        run(capsys, "--input", str(export), "--email", ADDR, "--dry-run")
        assert main(["leads", "delete", "--checklist"]) == 0
        assert fake_wrangler.calls == [] and fake_wrangler.argvs == []

    def test_without_the_alert_database_nothing_is_printed(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """Erasure is not complete without the alert clear, so `--yes` alone fails
        closed (exit 2) before any SQL is printed or any call is made."""
        code, out, err = run(
            capsys,
            "--input",
            str(export),
            "--email",
            ADDR,
            "--yes",
            "--omit-alerts-database",
        )
        assert code == 2 and out == ""
        assert "--alerts-database" in err and "DELETE" not in err
        assert fake_wrangler.calls == [] and fake_wrangler.argvs == []

    @pytest.mark.parametrize(
        "bad",
        [
            "prod; touch /tmp/copied-command-ran #",
            "a b",
            "$(id)",
            "`id`",
            "x\ny",
            "",
            "x/../y",
            "a" * 65,
        ],
    )
    def test_a_hostile_alert_database_name_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        bad: str,
    ) -> None:
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", bad,
        )  # fmt: skip
        assert code == 2 and out == "" and fake_wrangler.calls == []
        assert bad.strip() == "" or bad not in err

    def test_wrong_database_the_leads_one_named_for_alerts_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """The first attempt read 'table absent' as success. Naming the leads
        database (no alert table in it) must fail hard, not report a clear."""
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        assert code == 2 and out == ""
        assert "alert_dedup" in err and "refusing" in err

    def test_a_named_database_that_holds_lead_tables_is_refused_in_a_split_site(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.add_database("mixed", ["alert_dedup", "leads", "kit_events"])
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", "mixed",
        )  # fmt: skip
        assert code == 2 and out == "" and "lead tables" in err

    def test_a_missing_alert_table_is_a_hard_failure_never_a_clear(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.add_database("empty-db", [])
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", "empty-db",
        )  # fmt: skip
        assert code == 2 and out == "" and "refusing" in err

    def test_an_unknown_database_fails_hard(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, out, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", "no-such-db",
        )  # fmt: skip
        assert code == 2 and out == ""

    def test_the_single_database_case_needs_the_alert_table_in_the_leads_database(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """One binding holding both: allowed only when the named alert database is
        the very database the lead SQL targets, and it holds all three tables."""
        fake_wrangler.dbs[LEADS_DB].execute(
            "CREATE TABLE alert_dedup (site TEXT, event_hash TEXT, expires_at INTEGER)"
        )
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        assert code == CHECKED and ID_A in out
        assert [db for db, _ in fake_wrangler.calls] == [LEADS_DB]
        # With a second alert table in the split database the site is ambiguous.
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", ALERTS_DB,
        )  # fmt: skip
        assert code == 2 and out == "" and "ambiguous" in err

    def test_a_leads_database_without_lead_tables_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.dbs[LEADS_DB].execute("DROP TABLE kit_events")
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == ""

    def test_multiple_result_blocks_fail(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.extra_blocks = True
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and "not accepted" in err

    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"not json",
            b"[]",
            b'[{"results": [], "success": false}]',
            b'[{"results": [], "success": 1}]',
            b'[{"results": "x", "success": true}]',
            b'[{"results": [], "success": true, "extra": 1}]',
            b'[{"results": [], "results": [], "success": true}]',
            b'[{"results": [], "success": NaN}]',
            b'[{"results": [{"name": "alert_dedup"}, {"name": "alert_dedup"}],'
            b' "success": true}]',
            b'[{"results": [{"name": "sqlite_sequence"}], "success": true}]',
            b'[{"results": [{"name": "alert_dedup", "x": 1}], "success": true}]',
            b'{"results": [], "success": true}',
            b"[" * 20000,
            b"[" + b" " * (2 * 1024 * 1024) + b"]",
        ],
    )
    def test_an_answer_that_is_not_one_good_block_fails(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        body: bytes,
    ) -> None:
        fake_wrangler.raw = body
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == ""
        assert "not accepted" in err or "failed" in err

    def test_wrangler_failure_is_exit_2_and_repeats_no_remote_text(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.exit_code = 1
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and "wrangler failed" in err

    def test_the_runner_gets_an_argv_list_a_minimal_environment_and_no_shell(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token-value-x")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-x")
        monkeypatch.setenv("HOME", "/home/someone")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
        monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
        run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert fake_wrangler.envs
        for env in fake_wrangler.envs:
            assert set(env) <= {
                "PATH",
                "CLOUDFLARE_API_TOKEN",
                "CLOUDFLARE_ACCOUNT_ID",
                "HOME",
            }
            assert env["PATH"] == "/usr/bin:/bin"
        for argv in fake_wrangler.argvs:
            assert isinstance(argv, list) and argv[0] == str(
                fake_wrangler.path.resolve()
            )
        for argv in fake_wrangler.argvs:
            assert argv[1] == "d1" and argv[2] in ("execute", "info")
            assert "--json" in argv and "--file" not in argv
            if argv[2] == "execute":
                assert "--remote" in argv

    def test_the_real_runner_never_uses_a_shell_or_stdin(
        self, monkeypatch: pytest.MonkeyPatch, real_exec: Any
    ) -> None:
        """The one function that starts a process, checked on its own: it asks
        subprocess for an argv list, no shell, no stdin, a neutral working
        directory, and drops stderr."""
        import subprocess

        seen: dict[str, Any] = {}

        def fake_popen(argv: list[str], **kw: Any) -> Any:
            seen.update(kw, argv=argv)
            return _PipeProc(b"[]")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        code, out = real_exec(["/x/wrangler", "d1"], {"PATH": "/usr/bin:/bin"}, 120)
        assert (code, out) == (0, b"[]")
        assert seen["shell"] is False and "timeout" not in seen
        assert seen["stdin"] == subprocess.DEVNULL
        assert seen["stderr"] == subprocess.DEVNULL
        assert seen["cwd"] == "/"
        # Review round 2 (Medium): its own session, so the group can be killed whole.
        assert seen["start_new_session"] is True
        assert seen["env"] == {"PATH": "/usr/bin:/bin"}
        assert isinstance(seen["argv"], list)

    def test_the_real_runner_stops_reading_and_kills_the_child_past_the_cap(
        self, monkeypatch: pytest.MonkeyPatch, real_exec: Any
    ) -> None:
        """Review round 1 (Medium): the cap was checked after everything was in
        memory. The child is killed as soon as the cap is passed."""
        import subprocess

        procs: list[Any] = []

        def fake_popen(argv: list[str], **_kw: Any) -> Any:
            procs.append(_PipeProc(b"x" * (4 * leads_cmd.MAX_WRANGLER_BYTES)))
            return procs[0]

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        with pytest.raises(leads_cmd.WranglerError):
            real_exec(["/x/wrangler", "d1"], {}, 30)
        proc = procs[0]
        assert proc.killed
        # What was read stays near the cap, nowhere near the whole stream.
        assert proc.written <= leads_cmd.MAX_WRANGLER_BYTES + 4 * 65536

    def test_the_real_runner_kills_a_child_that_stays_silent_past_the_time(
        self, monkeypatch: pytest.MonkeyPatch, real_exec: Any
    ) -> None:
        import subprocess

        procs: list[Any] = []

        def fake_popen(argv: list[str], **_kw: Any) -> Any:
            procs.append(_PipeProc(None))  # never writes, never closes
            return procs[0]

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        with pytest.raises(subprocess.TimeoutExpired):
            real_exec(["/x/wrangler", "d1"], {}, 0.3)
        assert procs[0].killed

    def test_the_default_wrangler_is_the_repository_install_or_a_fixed_message(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        monkeypatch.setattr(leads_cmd, "REPO_ROOT", empty)
        code, out, err = main_run_without_wrangler(capsys, export)
        assert code == 2 and out == ""
        assert "cd worker && npm ci --ignore-scripts" in err
        assert fake_wrangler.calls == []

    def test_the_default_wrangler_is_used_when_the_install_is_in_place(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, out, _ = main_run_without_wrangler(capsys, export)
        assert code == CHECKED and ID_A in out
        assert fake_wrangler.argvs[0][0] == str(fake_wrangler.path.resolve())

    def test_a_wrangler_is_never_searched_on_path(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A `wrangler` earlier on PATH (a substitution) is never used: with no
        --wrangler and no pinned install the command fails closed."""
        bindir = tmp_path / "evil"
        bindir.mkdir(mode=0o700)
        planted = bindir / "wrangler"
        planted.write_text("#!/bin/sh\nexit 0\n")
        planted.chmod(0o700)
        monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        monkeypatch.setattr(leads_cmd, "REPO_ROOT", empty)
        code, _, _ = main_run_without_wrangler(capsys, export)
        assert code == 2
        assert fake_wrangler.calls == [] and fake_wrangler.argvs == []

    @pytest.mark.parametrize(
        "kind",
        [
            "relative",
            "dir",
            "group_writable_file",
            "group_writable_parent",
            "group_writable_ancestor",
            "world_writable_ancestor",
            "foreign_owned_ancestor",
            "outside_repo",
            "wrapper_in_bin",
            "symlink_escape",
        ],
    )
    def test_an_untrusted_wrangler_is_refused_before_anything_runs(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        kind: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Review round 1 (High 3, Medium 1): the path must stay inside the
        repository's own wrangler package (a wrapper that prints the right version
        is not accepted) and EVERY directory above it must be unwritable by group
        and others and owned by root or you. Nothing is started, so no token is
        handed over: the fake saw no call at all."""
        root: Path = fake_wrangler.root
        target = fake_wrangler.path
        arg = str(target)
        monkeypatch.setattr(leads_cmd, "_group_is_private", lambda _gid: False)
        restore: list[tuple[Path, int]] = []

        def loosen(path: Path, mode: int) -> None:
            restore.append((path, path.stat().st_mode & 0o7777))
            path.chmod(mode)

        outside = tmp_path / "outside"
        outside.mkdir(mode=0o755)
        trusted = outside / "wrangler"
        trusted.write_text("#!/bin/sh\nexit 0\n")
        trusted.chmod(0o755)
        if kind == "relative":
            monkeypatch.chdir(target.parent)
            arg = "wrangler"
        elif kind == "dir":
            arg = str(tmp_path)
        elif kind == "group_writable_file":
            loosen(target.resolve(), 0o775)
        elif kind == "group_writable_parent":
            loosen(target.resolve().parent, 0o775)
        elif kind == "group_writable_ancestor":
            loosen(root / "worker", 0o775)
        elif kind == "world_writable_ancestor":
            loosen(root, 0o777)
        elif kind == "foreign_owned_ancestor":
            real_stat = os.stat
            foreign = str(root / "worker")

            def stat_as_foreign(path: Any, *a: Any, **k: Any) -> os.stat_result:
                info = real_stat(path, *a, **k)
                if str(path) != foreign:
                    return info
                fields = list(info)
                fields[4] = info.st_uid + 4242
                return os.stat_result(fields)

            monkeypatch.setattr(os, "stat", stat_as_foreign)
        elif kind == "outside_repo":
            arg = str(trusted)
        elif kind == "wrapper_in_bin":
            # A script that would print the right version, in the right place by
            # name, but not the package's own file.
            target.unlink()
            target.write_text("#!/bin/sh\necho 'wrangler 4.0.0'\n")
            target.chmod(0o755)
        else:  # a link in .bin that leaves the package
            target.unlink()
            target.symlink_to(trusted)
        try:
            code, out, err = run(
                capsys, "--input", str(export), "--email", ADDR, "--yes",
                "--wrangler", arg,
            )  # fmt: skip
        finally:
            for path, mode in reversed(restore):
                path.chmod(mode)
        assert code == 2 and out == "", err
        assert fake_wrangler.calls == [] and fake_wrangler.argvs == []

    def test_a_group_writable_directory_is_accepted_only_for_a_private_group(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Debian-style accounts get a group of their own and umask 002, so a
        group write bit there lets no other account write; a shared group does."""
        import grp
        import pwd
        from types import SimpleNamespace

        # Test change (#905 round 3): the group-writable directory is now one ABOVE
        # the repository; inside it no group write bit is accepted (next test).
        root: Path = fake_wrangler.root
        above = root.parent
        above.chmod(0o775)
        gid = above.stat().st_gid
        monkeypatch.setattr(os, "getegid", lambda: gid)
        # Test setup (#905): the local-files check reads nsswitch.conf; fake it.
        monkeypatch.setattr(leads_cmd, "_read_nsswitch", lambda: LOCAL_NSS)
        monkeypatch.setattr(pwd, "getpwuid", lambda _u: SimpleNamespace(pw_name="me"))
        monkeypatch.setattr(
            grp, "getgrgid", lambda _g: SimpleNamespace(gr_mem=["someone-else"])
        )
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []
        monkeypatch.setattr(grp, "getgrgid", lambda _g: SimpleNamespace(gr_mem=[]))
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out
        assert leads_cmd._group_is_private(gid + 1) is False  # not our own group

    def test_inside_the_repository_a_private_group_write_bit_is_accepted(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Real-install finding (#905, replaces the round 2 rule): a normal
        checkout on an account with its own group and umask 002 is group-writable
        by that group alone, so a group write bit inside the repository is accepted
        when the group is private; the same tree with a shared group is refused."""
        (fake_wrangler.root / "worker").chmod(0o770)
        monkeypatch.setattr(leads_cmd, "_group_is_private", lambda _gid: True)
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out, err
        started = len(fake_wrangler.argvs)
        monkeypatch.setattr(leads_cmd, "_group_is_private", lambda _gid: False)
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and len(fake_wrangler.argvs) == started

    @staticmethod
    def _private_group(monkeypatch: pytest.MonkeyPatch, gid: int) -> None:
        """Make ``gid`` this process's own group with no other member."""
        import grp
        import pwd
        from types import SimpleNamespace

        monkeypatch.setattr(os, "getegid", lambda: gid)
        monkeypatch.setattr(leads_cmd, "_read_nsswitch", lambda: LOCAL_NSS)
        monkeypatch.setattr(pwd, "getpwuid", lambda _u: SimpleNamespace(pw_name="me"))
        monkeypatch.setattr(grp, "getgrgid", lambda _g: SimpleNamespace(gr_mem=[]))
        monkeypatch.setattr(pwd, "getpwall", lambda: [])

    @pytest.mark.parametrize(
        "text",
        [
            "passwd: files sss\ngroup: files\n",
            "passwd: files\ngroup: files ldap\n",
            "passwd: files winbind\ngroup: files winbind\n",
            "passwd: compat\ngroup: compat\n",
            "passwd: files\n",  # no group line
            "passwd: files\ngroup: files\ngroup: files nis\n",  # two group lines
            "passwd: files\ngroup:\n",  # empty
            "passwd: files [NOTFOUND=return] sss\ngroup: files\n",
            # Round 6 finding: systemd-userdb answers targeted lookups for accounts
            # that enumeration never lists, so it is not a local files-only source.
            "passwd: files systemd\ngroup: files systemd\n",
            "passwd: files\ngroup: files [SUCCESS=merge] systemd\n",
            "passwd: systemd\ngroup: files\n",
        ],
    )
    def test_a_directory_service_in_nsswitch_refuses_the_private_group_rule(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        text: str,
    ) -> None:
        """A directory service can hide members or accounts from the enumeration,
        so the group write bit is refused unless both lines name only local
        sources (the reader is faked; the real file is never read)."""
        target: Path = fake_wrangler.path.resolve()
        target.chmod(0o775)
        self._private_group(monkeypatch, target.stat().st_gid)
        assert leads_cmd._group_is_private(target.stat().st_gid) is True
        monkeypatch.setattr(leads_cmd, "_read_nsswitch", lambda: text)
        assert leads_cmd._group_is_private(target.stat().st_gid) is False
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and "g-w" in err
        assert not fake_wrangler.calls

    def test_nsswitch_variants_that_stay_local_are_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._private_group(monkeypatch, os.getegid())
        for text in (
            "# c\npasswd:  files  # x\ngroup: files [NOTFOUND=return]\n",
            "passwd: files\ngroup: files\nhosts: dns sss\n",
        ):
            monkeypatch.setattr(leads_cmd, "_read_nsswitch", lambda t=text: t)
            assert leads_cmd._group_is_private(os.getegid()) is True, text

    def test_systemd_nss_cannot_hide_an_account_that_shares_the_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Round 6 finding (#905): with ``files systemd`` a second account that
        shares the GID can be missing from ``pwd.getpwall()`` (not enumerable), so
        the empty enumeration must not make the group look private."""
        gid = os.getegid()
        # _private_group fakes an EMPTY getpwall(): the second account sharing the
        # GID exists for targeted lookups only and is never enumerated.
        self._private_group(monkeypatch, gid)
        assert leads_cmd._group_is_private(gid) is True
        text = "passwd: files systemd\ngroup: files systemd\n"
        monkeypatch.setattr(leads_cmd, "_read_nsswitch", lambda: text)
        assert leads_cmd._group_is_private(gid) is False

    def test_an_unreadable_nsswitch_or_a_lookup_error_is_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import grp
        import pwd

        gid = os.getegid()
        self._private_group(monkeypatch, gid)
        assert leads_cmd._group_is_private(gid) is True

        def broken() -> str:
            raise OSError("no file")

        monkeypatch.setattr(leads_cmd, "_read_nsswitch", broken)
        assert leads_cmd._group_is_private(gid) is False
        monkeypatch.setattr(leads_cmd, "_read_nsswitch", lambda: LOCAL_NSS)
        assert leads_cmd._group_is_private(gid) is True

        def fail(*_a: Any) -> Any:
            raise RuntimeError("directory service down")

        for module, name in ((grp, "getgrgid"), (pwd, "getpwuid"), (pwd, "getpwall")):
            with monkeypatch.context() as patch:
                patch.setattr(module, name, fail)
                assert leads_cmd._group_is_private(gid) is False, name

    def test_a_supplementary_group_is_not_private(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only the effective primary group counts: a group the process merely
        belongs to, with no other member listed, is refused."""
        target: Path = fake_wrangler.path.resolve()
        target.chmod(0o775)
        gid = target.stat().st_gid
        self._private_group(monkeypatch, gid + 1000)  # primary group is another
        monkeypatch.setattr(os, "getgroups", lambda: [gid + 1000, gid])
        assert leads_cmd._group_is_private(gid) is False
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and not fake_wrangler.calls

    def test_a_normal_umask_002_tree_is_accepted(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Real-install finding (#905): every directory 0775 and every file 0664
        (what umask 002 gives a plain checkout and ``npm ci``), owned by the user,
        in a private group: accepted, the repository root and the wrangler's
        ancestors included."""
        root: Path = fake_wrangler.root
        self._private_group(monkeypatch, root.stat().st_gid)
        for path in [root, *root.rglob("*")]:
            if not path.is_symlink():
                path.chmod((path.stat().st_mode & 0o7777) | 0o020)
        assert root.stat().st_mode & 0o020
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out, err

    def test_a_group_with_another_member_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A group-writable file whose group lists another member is refused, and
        so is one whose group is another account's primary group."""
        import grp
        import pwd
        from types import SimpleNamespace

        target: Path = fake_wrangler.path.resolve()
        target.chmod(0o775)
        gid = target.stat().st_gid
        self._private_group(monkeypatch, gid)
        monkeypatch.setattr(
            grp, "getgrgid", lambda _g: SimpleNamespace(gr_mem=["someone-else"])
        )
        assert leads_cmd._group_is_private(gid) is False
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []
        monkeypatch.setattr(grp, "getgrgid", lambda _g: SimpleNamespace(gr_mem=[]))
        other = SimpleNamespace(pw_uid=os.geteuid() + 1, pw_gid=gid)
        monkeypatch.setattr(pwd, "getpwall", lambda: [other])
        assert leads_cmd._group_is_private(gid) is False
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []
        monkeypatch.setattr(pwd, "getpwall", lambda: [])
        assert leads_cmd._group_is_private(gid) is True
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out, err

    def test_a_world_write_bit_is_refused_even_with_a_private_group(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A private group never excuses a world write bit: on the file, in the
        repository, or at its root."""
        monkeypatch.setattr(leads_cmd, "_group_is_private", lambda _gid: True)
        root: Path = fake_wrangler.root
        for path in (fake_wrangler.path.resolve(), root / "worker", root):
            old = path.stat().st_mode & 0o7777
            path.chmod(old | 0o002)
            try:
                code, out, _ = run(
                    capsys, "--input", str(export), "--email", ADDR, "--yes"
                )
            finally:
                path.chmod(old)
            assert code == 2 and out == "" and fake_wrangler.argvs == [], path

    @staticmethod
    def _as_owner(monkeypatch: pytest.MonkeyPatch, owners: dict[str, int]) -> None:
        """Report these paths as owned by the given uid (root cannot be created in a
        test); every other path keeps its real owner."""
        real_stat = os.stat

        def stat_as(path: Any, *a: Any, **k: Any) -> os.stat_result:
            info = real_stat(path, *a, **k)
            uid = owners.get(os.fspath(path))
            if uid is None:
                return info
            fields = list(info)
            fields[4] = uid
            return os.stat_result(fields)

        monkeypatch.setattr(os, "stat", stat_as)

    def test_a_sticky_world_writable_root_owned_directory_outside_is_accepted(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        above = fake_wrangler.root.parent
        above.chmod(0o1777)  # like /tmp: others cannot rename or remove our entries
        self._as_owner(monkeypatch, {os.fspath(above): 0})
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out

    def test_a_sticky_world_writable_directory_owned_by_someone_else_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
    ) -> None:
        """Review round 2 (automated, Medium): the sticky exemption is for a
        root-owned shared directory; one a user owns can be rewritten by that user."""
        fake_wrangler.root.parent.chmod(0o1777)
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []

    @pytest.mark.parametrize("where", ["root", "worker", "node_modules", "package"])
    def test_a_sticky_world_writable_directory_inside_the_repository_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        where: str,
    ) -> None:
        """Even root-owned: inside the repository or the package tree no group or
        other write bit is accepted at all."""
        root: Path = fake_wrangler.root
        target = {
            "root": root,
            "worker": root / "worker",
            "node_modules": root / "worker" / "node_modules",
            "package": root / "worker" / "node_modules" / "wrangler",
        }[where]
        target.chmod(0o1777)
        self._as_owner(monkeypatch, {os.fspath(target): 0})
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []

    def test_a_root_owned_wrangler_file_is_accepted_and_a_foreign_one_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Review round 2 (Low): the docs say root or the current user may own the
        file and every directory above it; the file check used to demand the
        current user only (a root-owned 0755 file under an acceptable tree failed)."""
        real = os.fspath(fake_wrangler.path.resolve())
        monkeypatch.setattr(os, "geteuid", lambda: 4242)
        real_stat = os.stat
        mine = real_stat(real).st_uid

        def stat_as(path: Any, *a: Any, **k: Any) -> os.stat_result:
            info = real_stat(path, *a, **k)
            if info.st_uid != mine and os.fspath(path) != real:
                return info  # root-owned system directories stay root-owned
            fields = list(info)
            fields[4] = file_owner if os.fspath(path) == real else 4242
            return os.stat_result(fields)

        monkeypatch.setattr(os, "stat", stat_as)
        real_lstat = os.lstat

        def lstat_as(path: Any, *a: Any, **k: Any) -> os.stat_result:
            info = real_lstat(path, *a, **k)
            if info.st_uid != mine:
                return info
            fields = list(info)
            fields[4] = file_owner if os.fspath(path) == real else 4242
            return os.stat_result(fields)

        monkeypatch.setattr(os, "lstat", lstat_as)
        file_owner = 0
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out, err
        file_owner = 5555
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and "owned by root or you" in err

    def test_a_non_executable_wrangler_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.path.resolve().chmod(0o644)
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []

    def test_a_wrangler_with_hostile_characters_in_its_path_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        for arg in ("/opt/x\nrm -rf", "/opt/x\x00y", "/opt/x\x1b[0m"):
            code, out, _ = run(
                capsys, "--input", str(export), "--email", ADDR, "--yes",
                "--wrangler", arg,
            )  # fmt: skip
            assert code == 2 and out == "" and fake_wrangler.argvs == []

    @pytest.mark.parametrize(
        "tamper",
        [
            "package_version",
            "package_name",
            "record_version",
            "record_digest",
            "record_missing",
            "lock_without_digest",
        ],
    )
    def test_an_install_that_does_not_match_the_lock_file_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tamper: str,
    ) -> None:
        """High 3: the version is read from the package files and npm's install
        record, never from what the program prints. The files are compared with
        worker/package-lock.json (name, version, tarball digest). This is a
        metadata check: it does not hash the installed files."""
        root: Path = fake_wrangler.root
        nm = root / "worker" / "node_modules"
        version = fake_wrangler.version
        entry = {"version": version, "integrity": "sha512-testdigest"}

        def hidden(patch: dict[str, str]) -> None:
            data = {"packages": {"node_modules/wrangler": {**entry, **patch}}}
            (nm / ".package-lock.json").write_text(json.dumps(data))

        if tamper == "package_version":
            meta = {"name": "wrangler", "version": "3.0.0"}
            (nm / "wrangler" / "package.json").write_text(json.dumps(meta))
        elif tamper == "package_name":
            meta = {"name": "not-wrangler", "version": version}
            (nm / "wrangler" / "package.json").write_text(json.dumps(meta))
        elif tamper == "record_version":
            hidden({"version": "3.0.0"})
        elif tamper == "record_digest":
            hidden({"integrity": "sha512-other"})
        elif tamper == "record_missing":
            (nm / ".package-lock.json").unlink()
        else:
            lock = {"packages": {"node_modules/wrangler": {"version": version}}}
            (root / "worker" / "package-lock.json").write_text(json.dumps(lock))
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "", err
        assert fake_wrangler.argvs == []  # the program was never run, not even asked

    def test_the_wrangler_is_never_asked_for_its_version(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """A version printout is text the program chooses, not an integrity check."""
        run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert fake_wrangler.argvs
        assert all("--version" not in argv for argv in fake_wrangler.argvs)

    def test_normalizes_before_matching(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, _ = run(
            capsys,
            "--input",
            str(export),
            "--email",
            " PARTNER@Example.Test ",
            "--yes",
        )
        assert code == CHECKED and ID_A in out

    def test_no_match_is_exit_1_and_emits_nothing(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, err = run(
            capsys, "--input", str(export), "--email", "nobody@example.test", "--yes"
        )
        assert code == 1 and out == ""
        assert "no lead" in err.lower()
        assert "nobody" not in err

    def test_more_than_one_match_is_refused(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        both = write(
            tmp_path / "dup.json",
            block([{"id": ID_A, "email": ADDR}, {"id": ID_B, "email": ADDR}]),
        )
        code, out, err = run(capsys, "--input", str(both), "--email", ADDR, "--yes")
        assert code == 2 and out == ""
        assert "2" in err and "partner" not in err

    def test_stdin_address_keeps_it_off_argv(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(ADDR + "\n"))
        code, out, _ = run(capsys, "--input", str(export), "--email", "-", "--yes")
        assert code == CHECKED and ID_A in out

    def test_stdin_is_bounded(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("a" * 100_000))
        code, out, _ = run(capsys, "--input", str(export), "--email", "-", "--yes")
        assert code == 2 and out == ""

    def test_stdin_read_size_is_capped(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asked: list[int] = []

        class Stdin:
            def read(self, size: int = -1) -> str:
                asked.append(size)
                return ADDR

        monkeypatch.setattr("sys.stdin", Stdin())
        code, _, _ = run(capsys, "--input", str(export), "--email", "-")
        assert code == 0 and asked and 0 < asked[0] <= 1024

    def test_writes_no_file(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        work = tmp_path / "cwd"
        work.mkdir()
        monkeypatch.chdir(work)
        before = sorted(p.name for p in tmp_path.iterdir())
        run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        run(capsys, "--input", str(export), "--email", ADDR)
        assert sorted(p.name for p in tmp_path.iterdir()) == before
        assert list(work.iterdir()) == []


class TestHostileExport:
    @pytest.mark.parametrize(
        "bad_id",
        [
            "x'; DROP TABLE leads;--",
            ID_A.upper(),
            ID_A[:-1],
            ID_A + "0",
            ID_A[:-1] + "'",
            "",
            123,
            None,
        ],
    )
    def test_a_matched_row_with_a_bad_id_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        bad_id: Any,
    ) -> None:
        path = write(tmp_path / "x.json", block([{"id": bad_id, "email": ADDR}]))
        code, out, err = run(capsys, "--input", str(path), "--email", ADDR, "--yes")
        assert code == 2 and out == ""
        assert "partner" not in err and "DROP" not in err

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "",
            "{}",
            '{"id": 1}',
            "[]",
            "[1, 2]",
            "NaN",
            "[NaN]",
            '[{"results": [{"id": "' + ID_A + '", "id": "' + ID_B + '"}]}]',
            "[" * 5000 + "]" * 5000,
        ],
    )
    def test_malformed_json_is_refused(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, text: str
    ) -> None:
        path = write(tmp_path / "x.json", text)
        code, out, _ = run(capsys, "--input", str(path), "--email", ADDR, "--yes")
        assert code == 2 and out == ""

    @pytest.mark.parametrize(
        "row",
        [
            {"id": ID_A, "email": ADDR, "ip": "192.0.2.1"},
            {"id": ID_A, "email": ADDR, "user_agent": "x"},
            {"id": ID_A, "email": ADDR, "created_ts": {"a": 1}},
            {"id": ID_A, "email": ADDR, "source": "x" * 500},
            {"id": ID_A},
            {"email": ADDR},
            {"id": ID_A, "email": "NOT AN ADDRESS"},
        ],
    )
    def test_unexpected_columns_or_values_refuse_the_file(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, row: dict[str, Any]
    ) -> None:
        path = write(tmp_path / "x.json", block([row]))
        code, out, err = run(capsys, "--input", str(path), "--email", ADDR, "--yes")
        assert code == 2 and out == ""
        assert "192.0.2.1" not in err and "NOT AN" not in err

    def test_a_stored_address_with_a_malformed_a_label_refuses_the_file(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        path = write(
            tmp_path / "x.json", block([{"id": ID_A, "email": "a@xn--abc.com"}])
        )
        code, out, err = run(capsys, "--input", str(path), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and "xn--abc" not in err

    @pytest.mark.parametrize(
        "extra",
        [
            {"success": False},
            {},
            {"success": "true"},
            {"success": 1},
            {"success": None},
        ],
    )
    def test_a_result_block_must_have_success_true(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        extra: dict[str, Any],
    ) -> None:
        blocks = [{"results": [{"id": ID_A, "email": ADDR}], **extra}]
        path = write(tmp_path / "x.json", blocks)
        for flags in ((), ("--yes",)):
            code, out, err = run(capsys, "--input", str(path), "--email", ADDR, *flags)
            assert code == 2 and out == ""
            assert "success" in err

    def test_one_failed_block_among_good_ones_refuses_everything(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        good = {"results": [{"id": ID_A, "email": ADDR}], "success": True}
        bad = {"results": [], "success": False}
        path = write(tmp_path / "x.json", [good, bad])
        code, out, _ = run(capsys, "--input", str(path), "--email", ADDR, "--yes")
        assert code == 2 and out == ""

    def test_full_lead_rows_from_select_star_are_accepted(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        row = {
            "id": ID_A,
            "email": ADDR,
            "created_ts": 1,
            "last_seen_ts": 2,
            "verified_ts": None,
            "source": "gate",
            "country": "US",
            "last_view_alert_ts": None,
            "last_view_event_ts": None,
            "last_cc_alert_ts": None,
            "session_floor_ts": None,
        }
        path = write(tmp_path / "x.json", block([row]))
        code, out, _ = run(capsys, "--input", str(path), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out

    def test_symlink_missing_directory_and_oversize_inputs_are_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, tmp_path: Path
    ) -> None:
        link = tmp_path / "link.json"
        os.symlink(export, link)
        for target in (link, tmp_path / "missing.json", tmp_path):
            code, out, _ = run(capsys, "--input", str(target), "--email", ADDR, "--yes")
            assert code == 2 and out == ""
        big = tmp_path / "big.json"
        big.write_bytes(b"[" + b" " * (17 * 1024 * 1024) + b"]")
        code, out, _ = run(capsys, "--input", str(big), "--email", ADDR, "--yes")
        assert code == 2 and out == ""

    def test_a_bad_address_argument_is_refused_before_the_file_is_read(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        code, out, err = run(
            capsys,
            "--input",
            str(tmp_path / "missing.json"),
            "--email",
            "bad address",
            "--yes",
        )
        assert code == 2 and out == ""
        assert "bad address" not in err
        assert "email address" in err  # a fixed message names the problem class


def finish(
    capsys: pytest.CaptureFixture[str], export: Path, *extra: str
) -> tuple[int, str, str]:
    return run(
        capsys, "--input", str(export), "--email", ADDR, "--yes", "--finish", *extra
    )


def seed_alerts(fake: Any, *, expires: int = 5, count: int = 2) -> None:
    for n in range(count):
        fake.dbs[ALERTS_DB].execute(
            "INSERT INTO alert_dedup VALUES ('default', ?, ?)", (f"{n:032x}", expires)
        )


def alert_rows(fake: Any) -> int:
    return int(
        fake.dbs[ALERTS_DB].execute("SELECT COUNT(*) FROM alert_dedup").fetchone()[0]
    )


def seed_lead(fake: Any) -> None:
    fake.dbs[LEADS_DB].execute("INSERT INTO leads VALUES (?, ?)", (ID_A, ADDR))
    fake.dbs[LEADS_DB].execute("INSERT INTO kit_events (lead_id) VALUES (?)", (ID_A,))


class TestFinish:
    def test_success_clears_and_reads_zero_twice_after_the_lead_readback(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)
        code, out, err = finish(capsys, export)
        assert code == 0 and out == ""
        assert alert_rows(fake_wrangler) == 0
        order = [(db, sql.split()[0]) for db, sql in fake_wrangler.calls]
        first_clear = order.index((ALERTS_DB, "DELETE"))
        assert order.index((LEADS_DB, "SELECT")) < first_clear  # lead read-back first
        reads = [c for c in fake_wrangler.calls if "alert_dedup_remaining" in c[1]]
        assert len(reads) == 2  # right after the clear, and again after the pause
        assert "example.test" not in err and ID_A not in err and ID_A not in out
        assert "kill switch" in err

    def test_lead_rows_that_remain_exit_4_and_leave_the_alert_table_alone(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)
        seed_lead(fake_wrangler)
        code, _, err = finish(capsys, export)
        assert code == leads_cmd.EXIT_LEADS_UNCONFIRMED == 4
        assert alert_rows(fake_wrangler) == 2
        assert not any(sql.startswith("DELETE") for _, sql in fake_wrangler.calls)
        assert "apply the SQL first" in err

    def test_a_lead_readback_that_cannot_be_read_exits_4(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.fail_on = "leads_remaining"
        code, _, _ = finish(capsys, export)
        assert code == 4 and alert_rows(fake_wrangler) == 0
        assert not any(sql.startswith("DELETE") for _, sql in fake_wrangler.calls)

    def test_a_failed_clear_is_exit_3_not_success(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)
        fake_wrangler.fail_on = "DELETE FROM alert_dedup"
        code, _, err = finish(capsys, export)
        assert code == leads_cmd.EXIT_ALERTS_INCOMPLETE == 3
        assert alert_rows(fake_wrangler) == 2
        assert "NOT complete" in err and "kill switch" not in err.split("NOT")[0]

    def test_rows_that_remain_after_every_clear_are_exit_3(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        # A delete that does not take effect: the old row is back at once.
        fake_wrangler.after_clear = lambda db: db.execute(
            "INSERT INTO alert_dedup VALUES ('default', ?, 5)", ("b" * 32,)
        )
        code, _, err = finish(capsys, export)
        assert code == 3 and "rows remain after the clear" in err
        deletes = [c for c in fake_wrangler.calls if c[1].startswith("DELETE")]
        assert len(deletes) == leads_cmd.ALERT_ATTEMPTS

    def test_rows_created_after_the_start_are_named_and_fail(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        late = int(__import__("time").time() * 1000) + leads_cmd.ALERT_TTL_MS + 60_000
        fake_wrangler.after_clear = lambda db: db.execute(
            "INSERT OR REPLACE INTO alert_dedup VALUES ('default', ?, ?)",
            ("c" * 32, late),
        )
        code, _, err = finish(capsys, export)
        assert code == 3 and "created after the erasure started" in err

    def test_a_row_that_arrives_once_is_cleared_by_the_retry(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        hits: list[int] = []

        def once(db: Any) -> None:
            if not hits:
                hits.append(1)
                db.execute(
                    "INSERT INTO alert_dedup VALUES ('default', ?, 5)", ("d" * 32,)
                )

        fake_wrangler.after_clear = once
        code, _, _ = finish(capsys, export)
        assert code == 0 and alert_rows(fake_wrangler) == 0

    def test_a_row_that_arrives_during_the_settle_pause_fails(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            leads_cmd,
            "pause",
            lambda _s: fake_wrangler.dbs[ALERTS_DB].execute(
                "INSERT INTO alert_dedup VALUES ('default', ?, 5)", ("e" * 32,)
            ),
        )
        code, _, err = finish(capsys, export)
        assert code == 3 and "NOT complete" in err

    def test_a_lead_row_that_reappears_during_the_pause_fails(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(leads_cmd, "pause", lambda _s: seed_lead(fake_wrangler))
        code, _, err = finish(capsys, export)
        assert code == 3 and "lead rows reappeared" in err

    @pytest.mark.parametrize("when", ["before_finish", "during_clear", "during_pause"])
    def test_a_lead_recreated_for_the_same_address_is_never_a_success(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        when: str,
    ) -> None:
        """Review round 2 (High 1): the exported id was deleted, then a person who
        can still open the kit created a new lead (a NEW id) with the same address.
        The read-back used to ask about the exported id only and returned 0."""
        seed_alerts(fake_wrangler)

        def recreate(*_a: Any) -> None:
            fake_wrangler.dbs[LEADS_DB].execute(
                "INSERT INTO leads VALUES (?, ?)", (ID_B, ADDR)
            )

        if when == "before_finish":
            recreate()
        elif when == "during_clear":
            fake_wrangler.after_clear = recreate
        else:
            monkeypatch.setattr(leads_cmd, "pause", recreate)
        code, out, err = finish(capsys, export)
        if when == "before_finish":
            assert code == 4 and alert_rows(fake_wrangler) == 2  # untouched
            assert not any(s.startswith("DELETE") for _, s in fake_wrangler.calls)
        else:
            assert code == 3 and "lead rows reappeared" in err
        assert code != 0 and ADDR not in err + out and ID_B not in err + out

    def test_a_recreated_lead_with_events_only_for_the_new_id_is_seen(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.dbs[LEADS_DB].execute(
            "INSERT INTO leads VALUES (?, ?)", (ID_B, ADDR)
        )
        fake_wrangler.dbs[LEADS_DB].execute(
            "INSERT INTO kit_events (lead_id) VALUES (?)", (ID_B,)
        )
        code, _, _ = finish(capsys, export)
        assert code == 4

    SPELLINGS = (
        "PARTNER@EXAMPLE.TEST",
        "Partner@Example.test",
        "partner@example.test ",
        " partner@example.test",
        "partner@example.test\t",
        "\t Partner@Example.Test \n",
    )

    @pytest.mark.parametrize("spelling", SPELLINGS)
    @pytest.mark.parametrize("when", ["before_finish", "during_pause"])
    def test_a_lead_recreated_with_another_case_or_spacing_is_seen(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        spelling: str,
        when: str,
    ) -> None:
        """Round 4 (#905, Medium): the final read-back compared the address byte for
        byte, so a row for the same person stored with another case or surrounding
        white space (a new id) read 0 and `--finish` could exit 0. Both sides are
        now compared trimmed and lower-cased, as the store keeps them."""
        seed_alerts(fake_wrangler)

        def recreate(*_a: Any) -> None:
            fake_wrangler.dbs[LEADS_DB].execute(
                "INSERT INTO leads VALUES (?, ?)", (ID_B, spelling)
            )
            fake_wrangler.dbs[LEADS_DB].execute(
                "INSERT INTO kit_events (lead_id) VALUES (?)", (ID_B,)
            )

        if when == "before_finish":
            recreate()
        else:
            monkeypatch.setattr(leads_cmd, "pause", recreate)
        code, out, err = finish(capsys, export)
        assert code == (4 if when == "before_finish" else 3), err
        if when == "before_finish":
            assert alert_rows(fake_wrangler) == 2  # untouched
        text = (err + out).lower()
        assert "example.test" not in text and ID_B not in text

    def test_the_read_back_compares_the_normalized_form_not_the_raw_text(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """The statement itself folds the stored value, and the address side is the
        tool's own normalized form (an upper-case or padded input is the same
        person: `leads delete` normalizes it before it builds anything)."""
        seed_alerts(fake_wrangler)
        code, _, _ = run(
            capsys,
            "--input",
            str(export),
            "--email",
            "  PARTNER@Example.TEST ",
            "--yes",
            "--finish",
        )
        assert code == 0
        reads = [s for _, s in fake_wrangler.calls if "leads_remaining" in s]
        assert reads and all("lower(trim(email" in s for s in reads)
        assert all(f"'{ADDR}'" in s for s in reads)  # the normalized address
        assert not any("PARTNER" in s for _, s in fake_wrangler.calls)

    @pytest.mark.parametrize(
        "other",
        [
            "partner+tag@example.test",
            "p.artner@example.test",
            "partner@example.test.evil.test",
            "partnér@example.test",  # a different character, not a case variant
            "аrtner@example.test",  # Cyrillic a: a look-alike, never the same
            "Ｐartner@example.test",  # full-width P
        ],
    )
    def test_other_addresses_are_not_folded_into_the_match(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        other: str,
    ) -> None:
        """The read-back is not widened: the store treats no plus-address, dot or
        look-alike spelling as the same address, so neither does the check."""
        seed_alerts(fake_wrangler)
        fake_wrangler.dbs[LEADS_DB].execute(
            "INSERT INTO leads VALUES (?, ?)", (ID_B, other)
        )
        code, _, _ = finish(capsys, export)
        assert code == 0 and alert_rows(fake_wrangler) == 0

    def test_finish_runs_the_schema_checks_again(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", "--finish",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        # Test change (#905 round 3): 4, not 2, once --finish is running.
        assert code == 4
        assert not any(sql.startswith("DELETE") for _, sql in fake_wrangler.calls)

    def test_finish_needs_yes_and_takes_no_sql_file(
        self, capsys: pytest.CaptureFixture[str], export: Path, tmp_path: Path
    ) -> None:
        code, _, _ = run(capsys, "--input", str(export), "--email", ADDR, "--finish")
        assert code == 2
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", "--finish",
            "--sql-out", str(tmp_path / "x.sql"),
        )  # fmt: skip
        assert code == 2 and not (tmp_path / "x.sql").exists()

    @pytest.mark.parametrize(
        "row",
        [
            {"alert_dedup_remaining": -1, "alert_dedup_new": 0},
            {"alert_dedup_remaining": True, "alert_dedup_new": 0},
            {"alert_dedup_remaining": "0", "alert_dedup_new": 0},
            {"alert_dedup_remaining": 1.5, "alert_dedup_new": 0},
            {"alert_dedup_remaining": 10**12, "alert_dedup_new": 0},
            {"alert_dedup_remaining": 0},
            {"alert_dedup_remaining": 0, "alert_dedup_new": 0, "x": 0},
        ],
    )
    def test_counts_must_be_non_negative_integers_in_the_exact_shape(
        self, row: dict[str, Any]
    ) -> None:
        with pytest.raises(leads_cmd.WranglerError):
            leads_cmd._counts([row], ("alert_dedup_remaining", "alert_dedup_new"))
        with pytest.raises(leads_cmd.WranglerError):
            leads_cmd._counts([], ("alert_dedup_remaining", "alert_dedup_new"))
        with pytest.raises(leads_cmd.WranglerError):
            leads_cmd._counts(
                [{"alert_dedup_remaining": 0, "alert_dedup_new": 0}] * 2,
                ("alert_dedup_remaining", "alert_dedup_new"),
            )

    def test_the_readback_counts_rows_made_after_the_start(self) -> None:
        import sqlite3

        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE alert_dedup (site TEXT, event_hash TEXT, expires_at INTEGER)"
        )
        start = 1_000_000
        sql = alert_readback_sql(start)
        assert db.execute(sql).fetchone() == (0, 0)
        # made before the start: expires_at <= start + ttl; after: later than that
        db.execute(
            "INSERT INTO alert_dedup VALUES ('s', 'a', ?)",
            (start + leads_cmd.ALERT_TTL_MS,),
        )
        db.execute(
            "INSERT INTO alert_dedup VALUES ('s', 'b', ?)",
            (start + leads_cmd.ALERT_TTL_MS + 1,),
        )
        assert db.execute(sql).fetchone() == (2, 1)
        with pytest.raises(LeadsError):
            alert_readback_sql("1; DROP TABLE x")  # type: ignore[arg-type]
        with pytest.raises(LeadsError):
            alert_readback_sql(True)


OTHER_UUID = "ffffffff-ffff-4fff-8fff-ffffffffffff"


class TestExitCodes:
    """Review round 1 (High 1): step 1 is not a completed erasure."""

    def test_only_finish_exits_zero_for_an_erasure(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED == 10 and code != 0
        assert alert_rows(fake_wrangler) == 2  # step 1 erased nothing
        assert "NOTHING HAS BEEN REMOVED YET" in err
        code, _, _ = finish(capsys, export)
        assert code == 0 and alert_rows(fake_wrangler) == 0

    def test_step_one_with_an_sql_file_also_exits_ten(
        self, capsys: pytest.CaptureFixture[str], export: Path, tmp_path: Path
    ) -> None:
        sql = tmp_path / "d.sql"
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--sql-out", str(sql),
        )  # fmt: skip
        assert code == CHECKED and out == "" and sql.exists()

    def test_a_dry_run_requests_nothing_and_says_its_zero_is_no_erasure(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR)
        assert code == 0
        assert "not an erasure" in out + err and "Nothing was changed" in out + err

    def test_the_exit_code_ten_is_distinct_from_every_other_result(self) -> None:
        codes = {
            leads_cmd.EXIT_OK,
            leads_cmd.EXIT_NO_MATCH,
            leads_cmd.EXIT_USAGE,
            leads_cmd.EXIT_ALERTS_INCOMPLETE,
            leads_cmd.EXIT_LEADS_UNCONFIRMED,
            leads_cmd.EXIT_CHECKED,
        }
        assert len(codes) == 6 and leads_cmd.EXIT_CHECKED == 10


class TestDatabaseIdentity:
    """Review round 1 (High 2): the name this tool clears must resolve to the id
    the owner took from the Worker's own configuration."""

    def seeded_decoy(self, fake: Any) -> None:
        seed_alerts(fake)  # the Worker's real alert table
        fake.add_database("decoy-alerts", ["alert_dedup"])
        fake.dbs["decoy-alerts"].execute(
            "INSERT INTO alert_dedup VALUES ('default', ?, 5)", ("f" * 32,)
        )

    @pytest.mark.parametrize("step", ["step1", "finish"])
    def test_a_decoy_with_the_right_schema_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        step: str,
    ) -> None:
        self.seeded_decoy(fake_wrangler)
        extra = ["--finish"] if step == "finish" else []
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", *extra,
            "--alerts-database", "decoy-alerts",
            "--alerts-database-id", ALERTS_UUID,
        )  # fmt: skip
        # Test change (#905 round 3): once --finish runs it never exits 2 (exit 4:
        # the lead rows are not confirmed erased and the alert table is untouched).
        assert code == (2 if step == "step1" else 4) and out == ""
        assert "does not resolve to the id" in err
        assert not any(sql.startswith("DELETE") for _, sql in fake_wrangler.calls)
        # Test change (#905 round 3): each schema read is its own check, call,
        # check, so the leads schema may be read before the decoy name is refused;
        # nothing is ever run on the decoy.
        assert all(db == LEADS_DB for db, _ in fake_wrangler.calls)
        assert all("sqlite_master" in sql for _, sql in fake_wrangler.calls)
        assert alert_rows(fake_wrangler) == 2
        rows = fake_wrangler.dbs["decoy-alerts"].execute(
            "SELECT COUNT(*) FROM alert_dedup"
        )
        assert rows.fetchone() == (1,)

    def test_a_wrong_leads_id_is_refused_too(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--leads-database-id", OTHER_UUID,
        )  # fmt: skip
        assert code == 2 and out == "" and fake_wrangler.calls == []

    def test_names_and_ids_must_agree_on_one_or_two_databases(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        # Two names, one id: the "alerts" database would be the leads database.
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database-id", LEADS_UUID,
        )  # fmt: skip
        assert code == 2 and out == "" and "disagree" in err
        # One name, two ids.
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", LEADS_DB, "--alerts-database-id", ALERTS_UUID,
        )  # fmt: skip
        assert code == 2 and out == "" and "disagree" in err
        assert fake_wrangler.calls == [] and fake_wrangler.infos == []

    def test_both_ids_are_required_with_yes_and_nothing_runs_without_them(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", "--omit-ids"
        )
        assert code == 2 and out == "" and "--leads-database-id" in err
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", "--omit-ids",
            "--leads-database-id", LEADS_UUID,
        )  # fmt: skip
        assert code == 2 and out == "" and "--alerts-database-id" in err
        assert fake_wrangler.argvs == []

    @pytest.mark.parametrize(
        "bad", ["", "not-a-uuid", "1" * 36, LEADS_UUID + "0", "x" + LEADS_UUID]
    )
    def test_a_malformed_id_is_refused_without_a_call(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        bad: str,
    ) -> None:
        code, out, err = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--leads-database-id", bad,
        )  # fmt: skip
        assert code == 2 and out == "" and fake_wrangler.argvs == []

    def test_an_uppercase_id_is_the_same_id(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--leads-database-id", LEADS_UUID.upper(),
        )  # fmt: skip
        assert code == CHECKED

    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"not json",
            b"[]",
            b"{}",
            b'{"uuid": 5}',
            b'{"uuid": "nope"}',
            b'{"name": "x"}',
            b'{"uuid": "' + LEADS_UUID.encode() + b'", "uuid": "x"}',
            b"[{}, {}]",
            b"[" + b" " * (2 * 1024 * 1024) + b"]",
        ],
    )
    def test_an_info_answer_that_is_not_one_id_fails(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        body: bytes,
    ) -> None:
        fake_wrangler.info_body = body
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.calls == []

    def test_the_info_answer_may_be_a_one_element_list(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        # The shape is not documented on the commands page (read 2026-11-26); a
        # list of one object is tolerated, and the uuid must still match.
        fake_wrangler.single_database()
        fake_wrangler.info_body = json.dumps([{"uuid": LEADS_UUID}]).encode()
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        assert code == CHECKED
        fake_wrangler.info_body = json.dumps([{"uuid": OTHER_UUID}]).encode()
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        assert code == 2

    def test_a_failing_info_call_is_a_refusal(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.exit_code = 1
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == ""


class TestTheWindowBetweenCheckAndClear:
    """Review round 1 (Medium 3): the clear decides on the schema in its own
    statement, and the identity and schema are checked again around it."""

    def test_the_clear_deletes_nothing_when_a_lead_table_appeared_after_the_check(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)

        def morph(db: Any, sql: str) -> None:
            if sql.startswith("DELETE FROM alert_dedup"):
                db.execute("CREATE TABLE IF NOT EXISTS leads (id TEXT, email TEXT)")

        fake_wrangler.before_statement = morph
        code, _, err = finish(capsys, export)
        assert code == 3 and "NOT complete" in err
        assert alert_rows(fake_wrangler) == 2  # the guarded delete deleted nothing

    def test_the_split_clear_is_the_guarded_statement_and_the_single_is_plain(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)
        assert finish(capsys, export)[0] == 0
        deletes = [sql for _, sql in fake_wrangler.calls if sql.startswith("DELETE")]
        assert deletes == [leads_cmd.ALERT_CLEAR_SPLIT_SQL]
        assert "sqlite_master" in deletes[0] and "NOT EXISTS" in deletes[0]
        fake_wrangler.single_database()
        fake_wrangler.calls.clear()
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", "--finish",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        assert code == 0
        deletes = [sql for _, sql in fake_wrangler.calls if sql.startswith("DELETE")]
        assert deletes == [leads_cmd.ALERT_CLEAR_SQL]

    def test_every_statement_runs_between_two_identity_checks_of_its_database(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """Review round 2 (High 2): execute works by NAME, so each statement is
        wrapped (check, call, check) instead of trusting one check for the run."""
        seed_alerts(fake_wrangler)
        code, _, _ = finish(capsys, export)
        assert code == 0
        events = fake_wrangler.events
        runs = [i for i, (kind, _) in enumerate(events) if kind == "exec"]
        assert len(runs) >= 8
        for i in runs:
            name = events[i][1]
            assert events[i - 1] == ("info", name), (i, events)
            assert events[i + 1] == ("info", name), (i, events)

    def test_an_identity_swap_just_before_the_clear_clears_nothing(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)

        def swap(fake: Any, name: str, nth: int) -> None:
            if name == ALERTS_DB and nth == 4:  # the check right before the clear
                fake.ids[ALERTS_DB] = OTHER_UUID

        fake_wrangler.on_info = swap
        code, _, err = finish(capsys, export)
        assert code == 3 and "just before the clear" in err
        assert alert_rows(fake_wrangler) == 2
        assert not any(sql.startswith("DELETE") for _, sql in fake_wrangler.calls)

    def test_a_name_re_pointed_before_the_clear_call_is_caught_after_it(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """The reviewer's decoy: the check passes, the name then points at a decoy
        with an empty alert table that stays pointed there. The clear runs on the
        decoy, but the check after the call sees the other id: exit 3, never 0."""
        seed_alerts(fake_wrangler)
        fake_wrangler.add_database("decoy-alerts", ["alert_dedup"], OTHER_UUID)

        def swap(fake: Any, name: str, nth: int) -> None:
            if name == ALERTS_DB and nth == 5:  # the check before the first clear
                fake.points[ALERTS_DB] = "decoy-alerts"

        fake_wrangler.after_info = swap
        code, _, err = finish(capsys, export)
        assert code == 3 and "does not resolve to the id" in err
        assert alert_rows(fake_wrangler) == 2  # the real table was never cleared

    def test_an_identity_swap_after_the_clear_is_exit_3_not_success(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        seed_alerts(fake_wrangler)

        def swap(fake: Any, name: str, nth: int) -> None:
            if name == ALERTS_DB and nth == 6:  # the check right after the clear
                fake.ids[ALERTS_DB] = OTHER_UUID

        fake_wrangler.on_info = swap
        code, _, err = finish(capsys, export)
        assert code == 3 and "does not resolve to the id" in err
        assert alert_rows(fake_wrangler) == 0  # the read-back was 0, yet not trusted

    # The identity checks of a successful --finish: 10 of the leads database and 12
    # of the alert database (every statement is wrapped, see the order test above).
    @pytest.mark.parametrize(
        ("name", "nth"),
        [(LEADS_DB, n) for n in range(1, 11)] + [(ALERTS_DB, n) for n in range(1, 13)],
    )
    def test_a_swap_that_starts_at_any_check_is_never_exit_2_or_0(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        name: str,
        nth: int,
    ) -> None:
        """Review round 2 (Medium 1): every point of --finish after it starts. A
        name that stops resolving to its id from the nth identity check on is exit 4
        while the lead rows are not yet confirmed erased, exit 3 after, and the
        alert table is never cleared before the lead read-back passed."""
        seed_alerts(fake_wrangler)
        # Identity checks of the leads database before the lead read-back is done:
        # 2 (schemas) + 2 (read-back); of the alert database: 2 (schemas).
        before_confirmed = 4 if name == LEADS_DB else 2

        def swap(fake: Any, who: str, count: int) -> None:
            if who == name and count == nth:
                fake.ids[name] = OTHER_UUID

        fake_wrangler.on_info = swap
        code, _, err = finish(capsys, export)
        assert code == (4 if nth <= before_confirmed else 3), (code, err)
        if code == 4:
            assert alert_rows(fake_wrangler) == 2
            assert not any(s.startswith("DELETE") for _, s in fake_wrangler.calls)

    @pytest.mark.parametrize(
        ("needle", "fail_from", "expected"),
        [
            ("sqlite_master", 1, 4),  # the first schema read (leads database)
            ("sqlite_master", 2, 4),  # the alert database schema read
            ("sqlite_master", 3, 3),  # the schema reads right before the clear
            ("sqlite_master", 5, 3),  # the schema reads after the pause
            ("leads_remaining", 1, 4),  # the lead read-back
            ("leads_remaining", 2, 3),  # the last lead read
            ("DELETE FROM alert_dedup", 1, 3),  # the clear
            ("alert_dedup_remaining", 1, 3),  # the read-back after the clear
            ("alert_dedup_remaining", 2, 3),  # the read-back after the pause
        ],
    )
    def test_a_statement_that_fails_never_exits_2(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        needle: str,
        fail_from: int,
        expected: int,
    ) -> None:
        """Review round 2 (Medium 1): a failed call at any point of --finish is
        exit 3 or 4 (by whether the lead rows were confirmed erased), never 2."""
        seen = {"n": 0}
        real_call = fake_wrangler.__call__

        def flaky(argv: list[str], env: dict[str, str], timeout: int) -> Any:
            if "--command" in argv and needle in argv[argv.index("--command") + 1]:
                seen["n"] += 1
                if seen["n"] >= fail_from:
                    return 1, b""
            return real_call(argv, env, timeout)

        monkeypatch.setattr(leads_cmd, "_exec", flaky)
        code, _, err = finish(capsys, export)
        assert code == expected, (code, err)

    @pytest.mark.parametrize("last", ["identity", "statement"])
    def test_the_final_lead_read_fails_closed_as_exit_3(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        last: str,
    ) -> None:
        """The last remote read of --finish (the lead tables again): when it cannot
        be done the erasure is not confirmed, and the lead rows were already read
        as erased, so exit 3."""
        reads = {"n": 0}
        real_call = fake_wrangler.__call__

        def fail_second_lead_read(
            argv: list[str], env: dict[str, str], timeout: int
        ) -> Any:
            statement = argv[argv.index("--command") + 1] if "--command" in argv else ""
            if "leads_remaining" in statement:
                reads["n"] += 1
                if reads["n"] == 2:
                    if last == "identity":
                        fake_wrangler.ids[LEADS_DB] = OTHER_UUID
                    else:
                        return 1, b""
            return real_call(argv, env, timeout)

        monkeypatch.setattr(leads_cmd, "_exec", fail_second_lead_read)
        code, _, err = finish(capsys, export)
        assert code == 3 and "NOT complete" in err

    def test_documented_residual_a_swap_confined_to_each_call_is_not_detected(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """Review round 2 (High 2), stated limit. Nothing available to this tool
        proves which database one `d1 execute NAME` used (execute takes a name or
        binding; its JSON answer is not documented to carry an id). A name that
        points at a decoy only DURING each execute and at the real database at
        every check is not seen. This test pins that the docs say so; it does not
        endorse the outcome."""
        seed_alerts(fake_wrangler)
        fake_wrangler.add_database("decoy-alerts", ["alert_dedup"], OTHER_UUID)

        def during(fake: Any, name: str, nth: int) -> None:
            if name == ALERTS_DB and nth % 2 == 1:  # the check BEFORE each call
                fake.points[ALERTS_DB] = "decoy-alerts"

        fake_wrangler.after_info = during
        fake_wrangler.after_exec = lambda fake, name, _sql: fake.points.pop(name, None)
        code, _, err = finish(capsys, export)
        assert code == 0 and alert_rows(fake_wrangler) == 2  # the real rows remain
        root = Path(leads_cmd.__file__).parents[3]
        for page in (Path(leads_cmd.__file__), root / "docs" / "privacy.md"):
            text = " ".join(page.read_text(encoding="utf-8").split())
            assert "re-pointed to another database and back" in text, page
        assert "cannot be proven" in err

    def test_a_schema_that_changes_after_the_clear_is_exit_3(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            leads_cmd,
            "pause",
            lambda _s: fake_wrangler.dbs[ALERTS_DB].execute(
                "CREATE TABLE kit_events (id INTEGER, lead_id TEXT)"
            ),
        )
        code, _, err = finish(capsys, export)
        assert code == 3 and "NOT complete" in err


class TestDependencyWalk:
    """Review round 2 (automated, Medium): node loads the whole dependency tree with
    the token in its environment, so the wrangler package and the packages it
    names are checked, not only the entry script and its ancestors."""

    @staticmethod
    def add_package(
        root: Path,
        name: str,
        deps: list[str] | None = None,
        *,
        nested: Path | None = None,
        optional: list[str] | None = None,
        peers: list[str] | None = None,
        optional_peers: list[str] | None = None,
    ) -> Path:
        nm = nested or (root / "worker" / "node_modules")
        pkg = nm / name
        (pkg / "lib" / "deep").mkdir(parents=True)
        for directory in (pkg, pkg / "lib", pkg / "lib" / "deep"):
            directory.chmod(0o700)
        meta: dict[str, Any] = {
            "name": name,
            "version": "1.0.0",
            "dependencies": dict.fromkeys(deps or [], "1"),
            "optionalDependencies": dict.fromkeys(optional or [], "1"),
            "peerDependencies": dict.fromkeys(
                (peers or []) + (optional_peers or []), "1"
            ),
            "peerDependenciesMeta": {
                n: {"optional": True} for n in optional_peers or []
            },
        }
        (pkg / "package.json").write_text(json.dumps(meta))
        (pkg / "lib" / "deep" / "x.js").write_text("module.exports = 1;\n")
        for file in (pkg / "package.json", pkg / "lib" / "deep" / "x.js"):
            file.chmod(0o600)
        return pkg

    @staticmethod
    def need(root: Path, deps: list[str]) -> None:
        path = root / "worker" / "node_modules" / "wrangler" / "package.json"
        meta = json.loads(path.read_text())
        meta["dependencies"] = dict.fromkeys(deps, "1")
        path.write_text(json.dumps(meta))
        path.chmod(0o600)

    def test_a_clean_dependency_closure_is_accepted(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        root: Path = fake_wrangler.root
        # Round 4 (#905): a dependency that is listed but absent is now a refusal
        # unless it is optional, so "not-installed" moved to optionalDependencies.
        self.add_package(
            root,
            "dep-a",
            ["dep-b", "@scope/dep-c"],
            optional=["not-installed"],
            optional_peers=["also-not-installed"],
        )
        self.add_package(root, "dep-b")
        scope = root / "worker" / "node_modules" / "@scope"
        scope.mkdir()
        scope.chmod(0o700)
        self.add_package(root, "dep-c", nested=scope)
        self.need(root, ["dep-a"])
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == CHECKED and ID_A in out, err

    @pytest.mark.parametrize("where", ["wrangler", "direct", "transitive", "nested"])
    @pytest.mark.parametrize("how", ["file_writable", "dir_writable", "foreign"])
    def test_a_bad_file_deep_in_the_closure_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
        where: str,
        how: str,
    ) -> None:
        root: Path = fake_wrangler.root
        nm = root / "worker" / "node_modules"
        self.add_package(root, "dep-a", ["dep-b"])
        self.add_package(root, "dep-b")
        self.need(root, ["dep-a"])
        victim = {
            "wrangler": nm / "wrangler" / "bin",
            "direct": nm / "dep-a" / "lib" / "deep",
            "transitive": nm / "dep-b" / "lib" / "deep",
            "nested": nm / "dep-a" / "node_modules" / "dep-b" / "lib" / "deep",
        }[where]
        if where == "nested":
            # dep-b installed inside dep-a: node finds it there first
            (nm / "dep-a" / "node_modules").mkdir()
            (nm / "dep-a" / "node_modules").chmod(0o700)
            self.add_package(root, "dep-b", nested=nm / "dep-a" / "node_modules")
        leaf = victim / ("wrangler.js" if where == "wrangler" else "x.js")
        if how == "file_writable":
            leaf.chmod(0o666)
        elif how == "dir_writable":
            victim.chmod(0o777)
        else:
            real_lstat = os.lstat
            owned = os.fspath(leaf)

            def lstat_as(path: Any, *a: Any, **k: Any) -> os.stat_result:
                info = real_lstat(path, *a, **k)
                if os.fspath(path) != owned:
                    return info
                fields = list(info)
                fields[4] = info.st_uid + 4242
                return os.stat_result(fields)

            monkeypatch.setattr(os, "lstat", lstat_as)
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err

    def test_a_symlink_that_leaves_the_repository_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
    ) -> None:
        root: Path = fake_wrangler.root
        outside = tmp_path / "elsewhere.js"
        outside.write_text("x\n")
        outside.chmod(0o600)
        (root / "worker" / "node_modules" / "wrangler" / "bin" / "link.js").symlink_to(
            outside
        )
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []

    def test_a_tree_larger_than_the_bound_fails_closed_and_says_so(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(leads_cmd, "MAX_TREE_ENTRIES", 2)
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.argvs == []
        assert "larger than the bound" in err


def _no_local_path(err: str, root: Path, tmp_path: Path) -> None:
    """Round 4 (#905, Low): nothing printed names the repository's absolute path or
    the directories above it (they hold the local account's name)."""
    assert str(root.resolve()) not in err and str(root) not in err
    assert str(tmp_path) not in err and str(tmp_path.resolve()) not in err


class TestSymlinkedDirectories:
    """Round 4 (#905, Medium): a symlink to a directory inside a package used to be
    checked as one stat of its target and never walked, so a writable file below it
    was never seen. The target is now resolved, must stay inside
    ``worker/node_modules``, and is walked with the shared seen set and budget."""

    @staticmethod
    def shared(root: Path) -> Path:
        base = root / "worker" / "node_modules" / "shared-lib"
        sub = base / "sub"
        sub.mkdir(parents=True)
        for directory in (base, sub):
            directory.chmod(0o700)
        leaf = sub / "x.js"
        leaf.write_text("module.exports = 1;\n")
        leaf.chmod(0o600)
        return sub

    @staticmethod
    def link(root: Path, target: Path, name: str = "lib") -> None:
        bin_dir = root / "worker" / "node_modules" / "wrangler" / "bin"
        (bin_dir / name).symlink_to(target)

    def go(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> tuple[int, str, str]:
        return run(capsys, "--input", str(export), "--email", ADDR, "--yes")

    def test_a_clean_symlinked_directory_is_accepted(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        self.link(fake_wrangler.root, self.shared(fake_wrangler.root))
        code, _, err = self.go(capsys, export)
        assert code == CHECKED, err

    @pytest.mark.parametrize("what", ["child_file", "deep_dir", "ancestor_dir"])
    def test_an_unsafe_entry_below_a_symlinked_directory_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        what: str,
    ) -> None:
        sub = self.shared(fake_wrangler.root)
        self.link(fake_wrangler.root, sub)
        if what == "child_file":
            (sub / "x.js").chmod(0o666)
        elif what == "deep_dir":
            deep = sub / "deeper"
            deep.mkdir()
            deep.chmod(0o777)
        else:
            sub.parent.chmod(0o777)  # between the target and the repository root
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        _no_local_path(err, fake_wrangler.root, tmp_path)

    @pytest.mark.parametrize("where", ["in_repository", "outside_repository"])
    def test_a_symlinked_directory_that_leaves_node_modules_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        where: str,
    ) -> None:
        base = fake_wrangler.root if where == "in_repository" else tmp_path
        target = base / "unchecked"
        target.mkdir()
        target.chmod(0o700)
        (target / "payload.js").write_text("1\n")
        (target / "payload.js").chmod(0o600)
        self.link(fake_wrangler.root, target)
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        _no_local_path(err, fake_wrangler.root, tmp_path)

    def test_a_symlink_loop_ends_and_is_accepted(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        root: Path = fake_wrangler.root
        sub = self.shared(root)
        (sub / "up").symlink_to(sub.parent)  # sub/up -> shared-lib (holds sub)
        self.link(root, sub)
        self.link(root, root / "worker" / "node_modules" / "wrangler", "self")
        code, _, err = self.go(capsys, export)
        assert code == CHECKED, err

    def test_the_entries_below_a_symlink_count_against_the_bound(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sub = self.shared(fake_wrangler.root)
        for n in range(30):
            (sub / f"f{n}.js").write_text("1\n")
            (sub / f"f{n}.js").chmod(0o600)
        self.link(fake_wrangler.root, sub)
        monkeypatch.setattr(leads_cmd, "MAX_TREE_ENTRIES", 12)
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and "larger than the bound" in err


class TestResolutionOutsideTheCheckedTree:
    """Round 4 (#905, Medium): node resolves a missing package from every
    ``node_modules`` above the package, up to ``/``. A required dependency that is
    absent under ``worker/node_modules`` is now a refusal, and so is any
    ``node_modules`` above ``worker`` (or a legacy folder in HOME)."""

    add = staticmethod(TestDependencyWalk.add_package)
    need = staticmethod(TestDependencyWalk.need)

    def go(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> tuple[int, str, str]:
        return run(capsys, "--input", str(export), "--email", ADDR, "--yes")

    @pytest.mark.parametrize("kind", ["dependency", "peer"])
    def test_a_required_dependency_that_is_not_installed_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        kind: str,
    ) -> None:
        root: Path = fake_wrangler.root
        if kind == "dependency":
            self.need(root, ["gone-dep"])
        else:
            self.add(root, "dep-a", peers=["gone-dep"])
            self.need(root, ["dep-a"])
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        assert "gone-dep" in err and "not installed under worker/node_modules" in err
        _no_local_path(err, root, tmp_path)

    def test_a_package_that_exists_only_above_worker_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
    ) -> None:
        root: Path = fake_wrangler.root
        outer = root / "node_modules"
        outer.mkdir()
        outer.chmod(0o700)
        self.add(root, "dep-a", nested=outer)
        self.need(root, ["dep-a"])  # not under worker/node_modules
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        _no_local_path(err, root, tmp_path)

    @pytest.mark.parametrize("above", ["repository_root", "parent", "dangling_link"])
    def test_a_node_modules_above_worker_is_refused_even_when_nothing_needs_it(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        above: str,
    ) -> None:
        root: Path = fake_wrangler.root
        if above == "repository_root":
            (root / "node_modules").mkdir()
        elif above == "parent":
            (tmp_path / "node_modules").mkdir()
        else:
            (root / "node_modules").symlink_to(tmp_path / "missing-target")
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        assert "node_modules directory exists" in err
        _no_local_path(err, root, tmp_path)

    @pytest.mark.parametrize("folder", [".node_modules", ".node_libraries"])
    def test_a_legacy_module_folder_in_home_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        folder: str,
    ) -> None:
        (tmp_path / "home" / folder).mkdir()
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        assert "legacy node module folder" in err
        _no_local_path(err, fake_wrangler.root, tmp_path)

    def test_the_child_never_gets_a_module_path_or_node_or_npm_setting(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for key in (
            "NODE_PATH",
            "NODE_OPTIONS",
            "NODE_EXTRA_CA_CERTS",
            "NPM_CONFIG_PREFIX",
            "NPM_CONFIG_REGISTRY",
            "npm_config_cache",
            "LD_PRELOAD",
        ):
            monkeypatch.setenv(key, "/somewhere/else")
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token-value-x")
        code, _, err = self.go(capsys, export)
        assert code == CHECKED, err
        assert fake_wrangler.envs
        for env in fake_wrangler.envs:
            assert set(env) <= {
                "PATH",
                "CLOUDFLARE_API_TOKEN",
                "CLOUDFLARE_ACCOUNT_ID",
                "HOME",
            }


class TestSymlinkedFiles:
    """Round 5 (#905, Medium): a symlink to a FILE was checked as one stat of its
    target, so a safe-mode target under a group or world writable parent was
    accepted (another account could swap it). Every symlink target now has its
    parent directories checked up to the repository root, and must stay inside
    ``worker/node_modules`` like a directory target."""

    @staticmethod
    def target(root: Path, parent_mode: int = 0o700, *, under: str = "node") -> Path:
        base = root / "worker" / "node_modules" if under == "node" else root
        parent = base / "shared-lib"
        parent.mkdir()
        leaf = parent / "helper.js"
        leaf.write_text("module.exports = 1;\n")
        leaf.chmod(0o600)  # the file itself is safe
        parent.chmod(parent_mode)
        return leaf

    @staticmethod
    def link(root: Path, target: Path) -> None:
        bin_dir = root / "worker" / "node_modules" / "wrangler" / "bin"
        (bin_dir / "helper.js").symlink_to(target)

    def go(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> tuple[int, str, str]:
        return run(capsys, "--input", str(export), "--email", ADDR, "--yes")

    def test_a_clean_symlinked_file_is_accepted(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        self.link(fake_wrangler.root, self.target(fake_wrangler.root))
        code, _, err = self.go(capsys, export)
        assert code == CHECKED, err

    @pytest.mark.parametrize("mode", [0o770, 0o707, 0o777])
    def test_a_safe_target_under_a_writable_parent_is_refused_before_exec(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        mode: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Test setup change (#905): a group write bit is accepted for a private
        # group, so this case models a SHARED group; the assertions are unchanged.
        monkeypatch.setattr(leads_cmd, "_group_is_private", lambda _gid: False)
        self.link(fake_wrangler.root, self.target(fake_wrangler.root, mode))
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        _no_local_path(err, fake_wrangler.root, tmp_path)

    def test_a_file_target_outside_node_modules_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
    ) -> None:
        """Inside the repository, safe modes all the way up, but not under
        ``worker/node_modules``."""
        self.link(fake_wrangler.root, self.target(fake_wrangler.root, under="repo"))
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        _no_local_path(err, fake_wrangler.root, tmp_path)


_WR = "node_modules/wrangler"


def _synthesize_install(root: Path) -> dict[str, Any]:
    """Build ``worker/node_modules`` from the real ``worker/package-lock.json``
    the way ``cd worker && npm ci --ignore-scripts`` lays it out, without the
    network: every lock entry becomes a directory at its lock path holding a
    ``package.json`` with the entry's name, version and dependency fields and one
    code file (mode 0644, directories 0755, owned by the current user). The real
    lock file and npm's install record are written beside it. Returns the lock."""
    text = (
        Path(__file__).resolve().parent.parent / "worker" / "package-lock.json"
    ).read_text()
    lock: dict[str, Any] = json.loads(text)
    worker = root / "worker"
    nm = worker / "node_modules"
    for key, entry in lock["packages"].items():
        if not key or entry.get("link"):
            continue
        name = key.rsplit("node_modules/", 1)[1]
        pkg = worker / key
        (pkg / "lib").mkdir(parents=True, exist_ok=True)
        meta: dict[str, Any] = {"name": name, "version": entry["version"]}
        for field in (
            "dependencies",
            "optionalDependencies",
            "peerDependencies",
            "peerDependenciesMeta",
        ):
            if field in entry:
                meta[field] = entry[field]
        (pkg / "package.json").write_text(json.dumps(meta))
        (pkg / "lib" / "index.js").write_text("module.exports = 1;\n")
        for file in (pkg / "package.json", pkg / "lib" / "index.js"):
            file.chmod(0o644)
    script = nm / "wrangler" / "bin" / "wrangler.js"
    script.parent.mkdir(exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    record = {"packages": {_WR: lock["packages"][_WR]}}
    (nm / ".package-lock.json").write_text(json.dumps(record))
    (worker / "package-lock.json").write_text(text)
    for file in (nm / ".package-lock.json", worker / "package-lock.json"):
        file.chmod(0o644)
    for directory in [nm, *nm.rglob("*")]:
        if directory.is_dir() and not directory.is_symlink():
            directory.chmod(0o755)
    return lock


class TestInstallationMode:
    """Round 5 (#905, Medium): the only supported layout is the one
    ``cd worker && npm ci --ignore-scripts`` makes. The first test proves that
    layout is accepted with no refusal, from the repository's real lock file; the
    others show that a hoisted layout is refused with a message that says so."""

    def go(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> tuple[int, str, str]:
        return run(capsys, "--input", str(export), "--email", ADDR, "--yes")

    def test_the_layout_npm_ci_makes_from_the_real_lock_file_is_accepted(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        lock = _synthesize_install(fake_wrangler.root)
        assert len(lock["packages"]) > 50  # the real closure, not a stub
        code, _, err = self.go(capsys, export)
        assert code == CHECKED, err
        assert "supported" not in err and fake_wrangler.argvs

    def test_a_name_in_both_dependency_fields_may_be_absent_when_optional(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """Round 5 follow-up (#905, Medium): npm lets ``optionalDependencies``
        override a same-named ``dependencies`` entry, so a platform package named
        in both and not installed is not a missing required dependency."""
        _synthesize_install(fake_wrangler.root)
        meta_file = (
            fake_wrangler.root / "worker" / "node_modules" / "wrangler" / "package.json"
        )
        meta = json.loads(meta_file.read_text())
        meta.setdefault("dependencies", {})["platform-helper"] = "1.0.0"
        meta.setdefault("optionalDependencies", {})["platform-helper"] = "1.0.0"
        meta_file.write_text(json.dumps(meta))
        code, _, err = self.go(capsys, export)
        assert code == CHECKED, err
        # Only a plain dependency of that name, absent, is still a refusal.
        del meta["optionalDependencies"]["platform-helper"]
        meta_file.write_text(json.dumps(meta))
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and "platform-helper" in err

    def test_a_required_package_missing_from_that_layout_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        """The acceptance test above is not vacuous: dropping one package of the
        closure is a refusal."""
        _synthesize_install(fake_wrangler.root)
        shutil.rmtree(fake_wrangler.root / "worker" / "node_modules" / "blake3-wasm")
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and "blake3-wasm" in err

    @pytest.mark.parametrize("layout", ["hoisted_to_root", "not_installed_below"])
    def test_a_hoisted_layout_is_refused_and_the_message_names_the_one_mode(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        layout: str,
    ) -> None:
        root: Path = fake_wrangler.root
        _synthesize_install(root)
        moved = root / "worker" / "node_modules" / "blake3-wasm"
        if layout == "hoisted_to_root":
            (root / "node_modules").mkdir()
            shutil.move(moved, root / "node_modules" / "blake3-wasm")
        else:
            shutil.rmtree(moved)
        code, out, err = self.go(capsys, export)
        assert code == 2 and out == "" and fake_wrangler.argvs == [], err
        assert leads_cmd.INSTALL_WRANGLER == "cd worker && npm ci --ignore-scripts"
        assert leads_cmd.INSTALL_WRANGLER in err
        assert "hoisted" in err and "workspace" in err
        _no_local_path(err, root, tmp_path)


# ---- the legacy copy of a lead in the analytics database (#772) ---------------------

LEGACY_DB = "reach-around-clicks"


def _two_databases() -> dict[str, sqlite3.Connection]:
    """The same lead, with events, in the leads database and in the legacy copy."""
    out = {}
    for name in (LEADS_DB, LEGACY_DB):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT)")
        conn.execute("CREATE TABLE kit_events (lead_id TEXT, kind TEXT)")
        conn.execute("INSERT INTO leads VALUES (?, ?)", (ID_A, ADDR))
        conn.execute("INSERT INTO leads VALUES (?, ?)", (ID_B, "other@example.test"))
        conn.executemany(
            "INSERT INTO kit_events VALUES (?, 'view')", [(ID_A,), (ID_A,), (ID_B,)]
        )
        out[name] = conn
    return out


def _commands(err: str) -> tuple[list[str], str]:
    """(apply tokens, read-back SQL) from the last two lines of the message."""
    lines = err.rstrip("\n").split("\n")
    apply_tokens = shlex.split(lines[-2])
    read_back = shlex.split(lines[-1])
    return apply_tokens, read_back[-1]


def _apply_file(conn: sqlite3.Connection, sql: str) -> None:
    for statement in sql.split(";\n"):
        body = "\n".join(
            ln for ln in statement.splitlines() if not ln.startswith("--")
        ).strip()
        if body:
            conn.execute(body)


def _read(conn: sqlite3.Connection, read_back: str) -> tuple[int, int]:
    row = conn.execute(read_back).fetchone()
    return row[0], row[1]


class TestLegacyDatabase:
    def _legacy(
        self, capsys: pytest.CaptureFixture[str], export: Path, *extra: str
    ) -> tuple[int, str, str]:
        return run(
            capsys,
            "--input",
            str(export),
            "--email",
            ADDR,
            "--legacy-database",
            LEGACY_DB,
            "--confirm-legacy",
            LEGACY_DB,
            "--yes",
            *extra,
        )

    def test_both_erasures_are_produced_and_each_read_back_targets_its_database(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        dbs = _two_databases()
        # Run 1: the leads database (the normal run).
        code, out1, err1 = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes"
        )
        assert code == CHECKED
        apply1, read1 = _commands(err1)
        # Run 2: the legacy analytics database.
        code, out2, err2 = self._legacy(capsys, export)
        assert code == CHECKED, err2
        apply2, read2 = _commands(err2)
        # Each printed command names its own database; the two never cross.
        assert apply1[1:5] == ["d1", "execute", LEADS_DB, "--remote"]
        assert apply2[1:5] == ["d1", "execute", LEGACY_DB, "--remote"]
        assert "--file" in apply1 and "--file" in apply2
        # Before anything is applied, the read-backs see the lead in both copies.
        assert _read(dbs[LEADS_DB], read1) == (1, 2)
        assert _read(dbs[LEGACY_DB], read2) == (1, 2)
        # Apply each file to ITS database only; the other copy is untouched.
        _apply_file(dbs[LEGACY_DB], out2)
        assert _read(dbs[LEGACY_DB], read2) == (0, 0)
        assert _read(dbs[LEADS_DB], read1) == (1, 2)
        _apply_file(dbs[LEADS_DB], out1)
        assert _read(dbs[LEADS_DB], read1) == (0, 0)
        # The other person is untouched in both.
        for conn in dbs.values():
            assert conn.execute("SELECT id FROM leads").fetchall() == [(ID_B,)]
            assert conn.execute("SELECT COUNT(*) FROM kit_events").fetchone() == (1,)
        # No personal value on any printed command line, and the id only in the
        # read-back.
        for err in (err1, err2):
            assert ADDR not in err and "partner" not in err
        assert ADDR not in read1 + read2

    def test_the_legacy_sql_touches_only_the_two_explicit_tables(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, _ = self._legacy(capsys, export)
        assert code == CHECKED
        deletes = [ln for ln in out.splitlines() if ln.startswith("DELETE FROM")]
        assert deletes
        assert {ln.split()[2] for ln in deletes} == set(leads_cmd.LEGACY_TABLES)
        assert "DROP" not in out and "UPDATE" not in out

    @pytest.mark.parametrize(
        "extra",
        [
            ("--confirm-legacy", "typo-db"),
            ("--legacy-database", LEADS_DB, "--confirm-legacy", LEADS_DB),
            ("--legacy-database", "bad name;rm", "--confirm-legacy", "bad name;rm"),
            ("--database", LEADS_DB),
            ("--finish",),
        ],
    )
    def test_the_legacy_run_refuses_an_unconfirmed_or_mixed_request(
        self, capsys: pytest.CaptureFixture[str], export: Path, extra: tuple[str, ...]
    ) -> None:
        args = ["--input", str(export), "--email", ADDR, "--yes"]
        base = {"--legacy-database": LEGACY_DB, "--confirm-legacy": LEGACY_DB}
        merged = list(extra)
        for flag, value in base.items():
            if flag not in merged:
                merged += [flag, value]
        code, out, err = run(capsys, *args, *merged)
        assert code == 2 and out == ""
        assert ADDR not in err and "partner" not in err

    def test_the_legacy_run_without_yes_is_a_dry_run(
        self, capsys: pytest.CaptureFixture[str], export: Path
    ) -> None:
        code, out, err = run(
            capsys,
            "--input",
            str(export),
            "--email",
            ADDR,
            "--legacy-database",
            LEGACY_DB,
            "--confirm-legacy",
            LEGACY_DB,
        )
        assert code == 0 and "DELETE" not in out
        assert "not an erasure" in out
