# SPDX-License-Identifier: Apache-2.0
"""``reach-around leads delete``: synthetic addresses only (example.test)."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

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

_FAKE: dict[str, Any] = {}


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
    args = [a for a in args if a != "--omit-alerts-database"]
    if "--yes" in args:
        if "--alerts-database" not in args and not omit:
            args += ["--alerts-database", ALERTS_DB]
        if "--wrangler" not in args:
            args += ["--wrangler", str(_FAKE["w"].path)]
    stdin: str | None = None
    if "--email" in args:
        at = args.index("--email") + 1
        if args[at] != "-":
            stdin, args[at] = args[at] + "\n", "-"
    if "--checklist" not in args and "--database" not in args:
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
        assert code == 0
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
            f"SELECT (SELECT COUNT(*) FROM leads WHERE id = '{ID_A}') "  # noqa: S608
            "AS leads_remaining, "
            f"(SELECT COUNT(*) FROM kit_events WHERE lead_id = '{ID_A}') "  # noqa: S608
            "AS events_remaining;",
        ]
        assert statements == expected
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
        assert code == 0 and "alert_dedup" not in out
        assert [db for db, _ in fake_wrangler.calls] == [LEADS_DB, ALERTS_DB]
        assert all("sqlite_master" in sql for _, sql in fake_wrangler.calls)
        assert "NOT complete" in err and "--finish" in err
        last = err.rstrip().splitlines()[-1]
        assert last.startswith(f"{fake_wrangler.path} d1 execute {LEADS_DB} --remote")
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
        assert fake_wrangler.calls == [] and fake_wrangler.versions_asked == 0

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
        assert fake_wrangler.calls == [] and fake_wrangler.versions_asked == 0

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
        assert code == 0 and ID_A in out
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
        for argv in fake_wrangler.argvs[1:]:
            assert argv[1:3] == ["d1", "execute"] and "--remote" in argv
            assert "--json" in argv and "--file" not in argv

    def test_the_real_runner_never_uses_a_shell_or_stdin(
        self, monkeypatch: pytest.MonkeyPatch, real_exec: Any
    ) -> None:
        """The one function that starts a process, checked on its own: it asks
        subprocess for an argv list, no shell, no stdin, a timeout, and drops
        stderr."""
        import subprocess

        seen: dict[str, Any] = {}

        def fake_run(argv: list[str], **kw: Any) -> Any:
            seen.update(kw, argv=argv)
            return subprocess.CompletedProcess(argv, 0, stdout=b"[]")

        monkeypatch.setattr(subprocess, "run", fake_run)
        real_exec(["/x/wrangler", "--version"], {"PATH": "/usr/bin:/bin"}, 120)
        assert seen["shell"] is False and seen["timeout"] == 120
        assert seen["stdin"] == subprocess.DEVNULL
        assert seen["stderr"] == subprocess.DEVNULL
        assert seen["env"] == {"PATH": "/usr/bin:/bin"}
        assert isinstance(seen["argv"], list)

    def test_the_default_wrangler_is_the_repository_install_or_a_fixed_message(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        missing = leads_cmd.REPO_ROOT / "worker" / "node_modules" / ".bin" / "wrangler"
        if missing.exists():  # a developer machine with the install present
            pytest.skip("the pinned install exists here")
        code, out, err = main_run_without_wrangler(capsys, export)
        assert code == 2 and out == ""
        assert "cd worker && npm ci --ignore-scripts" in err
        assert fake_wrangler.calls == []

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
        if (leads_cmd.REPO_ROOT / "worker/node_modules/.bin/wrangler").exists():
            pytest.skip("the pinned install exists here")
        code, _, _ = main_run_without_wrangler(capsys, export)
        assert code == 2
        assert fake_wrangler.calls == [] and fake_wrangler.versions_asked == 0

    @pytest.mark.parametrize("kind", ["relative", "dir", "group_writable", "world_dir"])
    def test_an_untrusted_wrangler_path_is_refused(
        self,
        capsys: pytest.CaptureFixture[str],
        export: Path,
        fake_wrangler: Any,
        tmp_path: Path,
        kind: str,
    ) -> None:
        target = fake_wrangler.path
        if kind == "relative":
            arg = "wrangler"
        elif kind == "dir":
            arg = str(tmp_path)
        elif kind == "group_writable":
            target.chmod(0o770)
            arg = str(target)
        else:
            target.parent.chmod(0o777)
            arg = str(target)
        try:
            code, out, err = run(
                capsys, "--input", str(export), "--email", ADDR, "--yes",
                "--wrangler", arg,
            )  # fmt: skip
        finally:
            target.parent.chmod(0o700)
        assert code == 2 and out == "" and fake_wrangler.calls == []
        assert fake_wrangler.versions_asked == 0

    def test_a_non_executable_wrangler_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.path.chmod(0o600)
        code, out, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and fake_wrangler.versions_asked == 0

    def test_a_wrangler_with_hostile_characters_in_its_path_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        for arg in ("/opt/x\nrm -rf", "/opt/x\x00y", "/opt/x\x1b[0m"):
            code, out, _ = run(
                capsys, "--input", str(export), "--email", ADDR, "--yes",
                "--wrangler", arg,
            )  # fmt: skip
            assert code == 2 and out == "" and fake_wrangler.versions_asked == 0

    def test_a_wrangler_of_another_version_is_refused(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        fake_wrangler.version = "3.0.0"
        code, out, err = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and out == "" and "pinned" in err
        assert fake_wrangler.calls == []
        # A line that names two versions is not accepted either.
        fake_wrangler.version = "4.0.0 wrangler 9.9.9"
        code, _, _ = run(capsys, "--input", str(export), "--email", ADDR, "--yes")
        assert code == 2 and fake_wrangler.calls == []

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
        assert code == 0 and ID_A in out

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
        assert code == 0 and ID_A in out

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
        assert code == 0 and ID_A in out

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

    def test_finish_runs_the_schema_checks_again(
        self, capsys: pytest.CaptureFixture[str], export: Path, fake_wrangler: Any
    ) -> None:
        code, _, _ = run(
            capsys, "--input", str(export), "--email", ADDR, "--yes", "--finish",
            "--alerts-database", LEADS_DB,
        )  # fmt: skip
        assert code == 2
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
