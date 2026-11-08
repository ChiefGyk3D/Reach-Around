# SPDX-License-Identifier: Apache-2.0
"""``reach-around leads delete``: synthetic addresses only (example.test)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.commands.leads import (
    LeadsError,
    mask_email,
    normalize_email,
)

ID_A = "0123456789abcdef0123456789abcdef"
ID_B = "fedcba9876543210fedcba9876543210"
ADDR = "partner@example.test"


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
    code = main(["leads", "delete", *argv])
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
        assert statements == [
            "DELETE FROM kit_events WHERE lead_id = '?';".replace("?", ID_A),
            "DELETE FROM leads WHERE id = '?';".replace("?", ID_A),
            "SELECT COUNT(*) AS remaining FROM leads WHERE id = '?';".replace(
                "?", ID_A
            ),
        ]
        assert ID_B not in out
        # The address appears nowhere: not in the SQL, not in the messages.
        for stream in (out, err):
            assert "partner" not in stream and "example.test" not in stream
        assert "1 lead" in err and "p***@***.test" in err

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
