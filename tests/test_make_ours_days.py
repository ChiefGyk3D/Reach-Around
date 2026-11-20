# SPDX-License-Identifier: Apache-2.0
"""scripts/make-ours-days.py and scripts/proof-export.sh: the day-2 export.

A fake runner stands in for D1; nothing here touches the network. Fixtures are
hand-written D1 REST answers under tests/fixtures/d1."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make-ours-days.py"
WRAPPER = ROOT / "scripts" / "proof-export.sh"
CHECK = ROOT / "scripts" / "check-analytics.py"
FIX = ROOT / "tests" / "fixtures" / "d1"
TODAY = date(2026, 11, 10)


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_ours_days", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_ours_days"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = load()


def fixture_rows(name: str) -> list[dict[str, object]]:
    data = json.loads((FIX / name).read_text())
    return data["result"][0]["results"]  # type: ignore[no-any-return]


class Fake:
    """Answers the views query and the clicks query from fixtures."""

    def __init__(self, views: str = "views.json", clicks: str = "clicks.json") -> None:
        self.views = fixture_rows(views)
        self.clicks = fixture_rows(clicks)
        self.seen: list[str] = []
        self.params: list[list[object]] = []

    def __call__(self, sql: str, params: list[object]) -> list[dict[str, object]]:
        self.seen.append(sql)
        self.params.append(params)
        return self.views if "view_daily" in sql else self.clicks


@pytest.fixture
def out(tmp_path: Path) -> Path:
    d = tmp_path / "private"
    d.mkdir(mode=0o700)
    return d


def go(out: Path, fake: object, *extra: str, env: dict[str, str] | None = None) -> int:
    argv = ["--out", str(out), "--since", "2026-11-01", "--until", "2026-11-02", *extra]
    return int(mod.main(argv, runner=fake, today=TODAY, env=env or {}))


def test_writes_csv_mode_600_and_prints_counts_only(
    out: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert go(out, Fake()) == 0
    files = sorted(out.iterdir())
    assert [f.name for f in files] == ["ours-days-2026-11-01_2026-11-02.csv"]
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert files[0].read_text() == (
        "day,views,clicks\n2026-11-01,100,20\n2026-11-02,120,30\n"
    )
    said = capsys.readouterr()
    assert "2 day(s)" in said.out
    for secret in ("100", "120", "2026-11-01,"):
        assert secret not in said.out + said.err


def test_every_statement_is_a_select(out: Path) -> None:
    fake = Fake()
    assert go(out, fake) == 0
    assert len(fake.seen) == 2
    assert all(
        s.lstrip().upper().startswith("SELECT") and ";" not in s for s in fake.seen
    )
    assert "site = ?" in fake.seen[0] and "bot_class = 'h'" in fake.seen[0]
    assert fake.params[0] == ["links", "2026-11-01", "2026-11-02"]
    assert fake.params[1] == ["links", 1793491200000, 1793664000000]


def test_guard_refuses_non_select() -> None:
    called: list[str] = []

    def inner(sql: str, _params: list[object]) -> list[dict[str, object]]:
        called.append(sql)
        return []

    guarded = mod.select_only(inner)
    for bad in (
        "DELETE FROM view_daily",
        "  insert into x values (1)",
        "PRAGMA table_info(x)",
        "WITH a AS (SELECT 1) SELECT * FROM a",
        "SELECT 1; DROP TABLE x",
        "",
    ):
        with pytest.raises(mod.Refused):
            guarded(bad, [])
    assert called == []
    guarded("SELECT 1", [])
    assert called == ["SELECT 1"]


def test_roundtrip_matching_beacons_exits_0(out: Path, tmp_path: Path) -> None:
    assert go(out, Fake()) == 0
    ours = next(out.iterdir())
    beacons = tmp_path / "b.csv"
    beacons.write_text("day,views,clicks\n2026-11-01,102,21\n2026-11-02,118,29\n")
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [
            sys.executable,
            "-I",
            str(CHECK),
            "--beacons",
            str(beacons),
            "--ours",
            str(ours),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_roundtrip_gap_beyond_tolerance_exits_nonzero(
    out: Path, tmp_path: Path
) -> None:
    assert go(out, Fake()) == 0
    ours = next(out.iterdir())
    beacons = tmp_path / "b.csv"
    beacons.write_text("day,views,clicks\n2026-11-01,300,21\n2026-11-02,118,29\n")
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [
            sys.executable,
            "-I",
            str(CHECK),
            "--beacons",
            str(beacons),
            "--ours",
            str(ours),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1, r.stdout + r.stderr


def test_beacons_option_runs_the_check_and_passes_its_exit_code(
    out: Path, tmp_path: Path
) -> None:
    good = tmp_path / "g.csv"
    good.write_text("day,views,clicks\n2026-11-01,100,20\n2026-11-02,120,30\n")
    bad = tmp_path / "x.csv"
    bad.write_text("day,views,clicks\n2026-11-01,900,20\n2026-11-02,120,30\n")
    assert go(out, Fake(), "--beacons", str(good)) == 0
    for f in out.iterdir():
        f.unlink()
    assert go(out, Fake(), "--beacons", str(bad)) == 1


def test_check_table_goes_to_a_private_file_not_the_terminal(
    out: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    beacons = tmp_path / "b.csv"
    beacons.write_text("day,views,clicks\n2026-11-01,731,20\n2026-11-02,120,30\n")
    assert go(out, Fake(), "--beacons", str(beacons)) == 1
    said = capsys.readouterr()
    for sentinel in ("731", "FAIL", "PASS", "2026-11-01 "):
        assert sentinel not in said.out + said.err
    assert "check-analytics exit=1" in said.out
    report = out / "ours-days-2026-11-01_2026-11-02.check.txt"
    assert str(report) in said.out
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    table = report.read_text()
    assert "731" in table and "FAIL" in table and "Summary:" in table


def test_existing_check_file_is_never_overwritten(out: Path, tmp_path: Path) -> None:
    report = out / "ours-days-2026-11-01_2026-11-02.check.txt"
    report.write_text("old")
    beacons = tmp_path / "b.csv"
    beacons.write_text("day,views,clicks\n2026-11-01,100,20\n2026-11-02,120,30\n")
    fake = Fake()
    assert go(out, fake, "--beacons", str(beacons)) == 2
    assert fake.seen == []
    assert report.read_text() == "old"


def test_check_that_cannot_start_exits_3_and_keeps_the_csv(
    out: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    beacons = tmp_path / "b.csv"
    beacons.write_text("day,views,clicks\n2026-11-01,100,20\n2026-11-02,120,30\n")

    def nope(*_a: object, **_k: object) -> None:
        raise OSError("no")

    monkeypatch.setattr(mod.subprocess, "run", nope)
    assert go(out, Fake(), "--beacons", str(beacons)) == 3
    said = capsys.readouterr().out
    assert "could not start" in said and "move or delete" in said
    assert [f.name for f in out.iterdir()] == ["ours-days-2026-11-01_2026-11-02.csv"]


def test_site_id_boundary_matches_the_worker_contract(out: Path) -> None:
    ok, too_long = "a" * 20, "a" * 21
    assert mod.SITE_RE.fullmatch(ok) and not mod.SITE_RE.fullmatch(too_long)
    base = ["--out", str(out), "--since", "2026-11-01", "--until", "2026-11-02"]
    fake = Fake()
    assert mod.main([*base, "--site", too_long], runner=fake, today=TODAY, env={}) == 2
    assert fake.seen == []
    assert mod.main([*base, "--site", ok], runner=fake, today=TODAY, env={}) == 0
    assert fake.params[0][0] == ok


def test_day_with_no_rows_is_left_out_so_it_reads_as_a_gap(out: Path) -> None:
    fake = Fake()
    fake.views = [r for r in fake.views if r["day"] != "2026-11-02"]
    fake.clicks = [r for r in fake.clicks if r["day"] != "2026-11-02"]
    assert go(out, fake) == 0
    assert next(out.iterdir()).read_text() == "day,views,clicks\n2026-11-01,100,20\n"


def test_day_in_one_query_only_gets_zero_for_the_other(out: Path) -> None:
    fake = Fake()
    fake.clicks = [r for r in fake.clicks if r["day"] != "2026-11-02"]
    assert go(out, fake) == 0
    assert "2026-11-02,120,0\n" in next(out.iterdir()).read_text()


def test_never_overwrites(out: Path) -> None:
    assert go(out, Fake()) == 0
    first = next(out.iterdir()).read_text()
    assert go(out, Fake(clicks="clicks.json")) == 2
    assert next(out.iterdir()).read_text() == first


@pytest.mark.parametrize(
    "bad_rows",
    [
        [{"day": "2026-12-01", "views": 1}],
        [{"day": "2026-11-01", "views": -1}],
        [{"day": "2026-11-01", "views": 1.5}],
        [{"day": "2026-11-01", "views": "7"}],
        [{"day": "2026-11-01", "views": 1}, {"day": "2026-11-01", "views": 2}],
        [{"views": 1}],
    ],
)
def test_bad_rows_are_refused_without_echoing_values(
    out: Path, capsys: pytest.CaptureFixture[str], bad_rows: list[dict[str, object]]
) -> None:
    fake = Fake()
    fake.views = bad_rows
    assert go(out, fake) == 3
    assert list(out.iterdir()) == []
    err = capsys.readouterr().err
    assert "2026-12-01" not in err and "1.5" not in err


def test_runner_failure_exits_3_and_writes_nothing(out: Path) -> None:
    def boom(_sql: str, _params: list[object]) -> list[dict[str, object]]:
        raise mod.QueryFailed("query failed")

    assert go(out, boom) == 3
    assert list(out.iterdir()) == []


@pytest.mark.parametrize(
    "args",
    [
        ["--since", "2026-11-01", "--until", "2026-11-10"],  # today is partial
        ["--since", "2026-11-02", "--until", "2026-11-01"],
        ["--since", "2026-11-01", "--until", "2026-12-30"],
        ["--since", "11/01", "--until", "2026-11-02"],
        ["--since", "2026-11-01", "--until", "2026-11-02", "--site", "x'; DROP"],
    ],
)
def test_bad_window_or_site_is_refused(out: Path, args: list[str]) -> None:
    fake = Fake()
    argv = ["--out", str(out), *args]
    assert mod.main(argv, runner=fake, today=TODAY, env={}) == 2
    assert fake.seen == []


def test_out_dir_must_be_private_and_outside_any_repo(tmp_path: Path) -> None:
    fake = Fake()
    open_dir = tmp_path / "open"
    open_dir.mkdir(mode=0o755)
    open_dir.chmod(0o755)
    assert go(open_dir, fake) == 2
    inside = ROOT / "tests" / ".proof-out-test"
    inside.mkdir(mode=0o700, exist_ok=True)
    try:
        assert go(inside, fake) == 2
    finally:
        inside.rmdir()
    assert go(tmp_path / "missing", fake) == 2
    assert fake.seen == []


def test_out_dir_symlink_refused(tmp_path: Path, out: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(out)
    assert go(link, Fake()) == 2


def test_credentials_only_from_env_and_deploy_token_reuse_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert mod.credentials({}) is None
    env = {
        "RA_D1_TOKEN": "tok-secret",
        "RA_D1_ACCOUNT_ID": "a" * 32,
        "RA_D1_DATABASE_ID": "b" * 32,
    }
    assert mod.credentials(env) is not None
    with pytest.raises(mod.Refused):
        mod.credentials({**env, "CLOUDFLARE_API_TOKEN": "tok-secret"})
    with pytest.raises(mod.Refused):
        mod.credentials({**env, "RA_D1_ACCOUNT_ID": "../x"})
    assert "tok-secret" not in capsys.readouterr().out


def test_no_credentials_means_usage_error_not_a_network_call(out: Path) -> None:
    argv = ["--out", str(out), "--since", "2026-11-01", "--until", "2026-11-02"]
    assert mod.main(argv, runner=None, today=TODAY, env={}) == 2


@pytest.mark.parametrize(
    "extra",
    [
        ["--token", "SECRETVALUE123"],
        ["--token=SECRETVALUE123"],
        ["--tok", "SECRETVALUE123"],
    ],
)
def test_token_is_not_an_option_and_never_echoed(extra: list[str]) -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            *extra,
            "--out",
            "/x",
            "--since",
            "2026-11-01",
            "--until",
            "2026-11-02",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 2
    assert "SECRETVALUE123" not in r.stdout + r.stderr


def test_http_runner_parses_rest_shape_and_rejects_write_flags() -> None:
    body = (FIX / "views.json").read_text()
    assert mod.parse_response(body)[0]["views"] == 100
    changed = json.loads(body)
    changed["result"][0]["meta"] = {"changed_db": True}
    with pytest.raises(mod.QueryFailed):
        mod.parse_response(json.dumps(changed))
    failed = json.loads(body)
    failed["success"] = False
    with pytest.raises(mod.QueryFailed):
        mod.parse_response(json.dumps(failed))
    with pytest.raises(mod.QueryFailed):
        mod.parse_response("not json")


def test_wrapper_is_thin_and_passes_exit_code() -> None:
    text = WRAPPER.read_text()
    assert "make-ours-days.py" in text and "set -euo pipefail" in text
    r = subprocess.run(  # noqa: S603 - fixed argv
        [
            shutil.which("bash") or "bash",
            str(WRAPPER),
            "--out",
            "/nonexistent-ra366",
            "--since",
            "2026-11-01",
            "--until",
            "2026-11-02",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"]},
    )
    assert r.returncode == 2, r.stdout + r.stderr


def test_check_file_appearing_after_the_precheck_is_kept(
    out: Path, tmp_path: Path
) -> None:
    report = out / "ours-days-2026-11-01_2026-11-02.check.txt"
    beacons = tmp_path / "b.csv"
    beacons.write_text("day,views,clicks\n2026-11-01,100,20\n2026-11-02,120,30\n")

    class Racy(Fake):
        def __call__(self, sql: str, params: list[object]) -> list[dict[str, object]]:
            report.write_text("someone else")
            return super().__call__(sql, params)

    assert go(out, Racy(), "--beacons", str(beacons)) == 3
    assert report.read_text() == "someone else"
