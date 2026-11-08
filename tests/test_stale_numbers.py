# SPDX-License-Identifier: Apache-2.0
"""Hand-kept number hygiene (#389): the stale gate, the reminder and the CSV import.

Synthetic data only (example.com, no real handles).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from reach_around.cli import main
from reach_around.config import SiteConfig
from reach_around.config.numbers import Numbers, NumbersEntry
from reach_around.config.stale import all_numbers, due_numbers
from reach_around.counts.importer import ImportFailed, merge, parse_import
from reach_around.site.platforms import platform_rows

TODAY = date(2027, 3, 10)


def ago(days: int, today: date = TODAY) -> str:
    return (today - timedelta(days=days)).isoformat()


def item(platform: str, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "platform": platform,
        "handle": "@example",
        "url": "https://example.com/p",
    }
    out.update(kw)
    return out


def config_doc(items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "timezone": "UTC",
        "creator": {"name": "Example"},
        "tabs": [
            {
                "id": "home",
                "title": "Home",
                "blocks": [{"id": "socials", "items": items}],
            }
        ],
        **extra,
    }


def cfg(items: list[dict[str, Any]], **extra: Any) -> SiteConfig:
    return SiteConfig.model_validate(config_doc(items, **extra))


def numbers_of(**found: tuple[int, date]) -> Numbers:
    return Numbers(
        entries={
            k: NumbersEntry(count=v, as_of=datetime(d.year, d.month, d.day, tzinfo=UTC))
            for k, (v, d) in found.items()
        }
    )


GITHUB_SOURCE = {"sources": [{"platform": "github", "kind": "github", "user": "u"}]}

# --- who keeps a number and when it is stale ---------------------------------


def test_typed_numbers_are_hand_kept_and_stale_past_the_default_limit() -> None:
    config = cfg(
        [item("kick", count=5, as_of=ago(31)), item("tiktok", count=6, as_of=ago(30))]
    )
    found = {n.platform: n for n in all_numbers(config, None, TODAY)}
    assert found["kick"].hand_kept and found["kick"].stale and found["kick"].days == 31
    assert found["tiktok"].hand_kept and not found["tiktok"].stale


def test_the_limit_is_configurable_per_platform() -> None:
    config = cfg(
        [item("kick", count=5, as_of=ago(10)), item("tiktok", count=6, as_of=ago(10))],
        numbers={"stale_days": {"kick": 7}},
    )
    found = {n.platform: n for n in all_numbers(config, None, TODAY)}
    assert found["kick"].limit == 7 and found["kick"].stale
    assert found["tiktok"].limit == 30 and not found["tiktok"].stale


def test_a_fetched_number_is_not_hand_kept() -> None:
    config = cfg([item("github"), item("kick")], numbers=GITHUB_SOURCE)
    old = TODAY - timedelta(days=40)
    book = numbers_of(github=(10, old), kick=(7, old))
    found = {n.platform: n for n in all_numbers(config, book, TODAY)}
    assert not found["github"].hand_kept
    assert found["kick"].hand_kept


def test_kit_metrics_are_judged_too() -> None:
    kit = {
        "title": "Kit",
        "about": "About",
        "metrics": [
            {"platform": "kick", "label": "Subscribers", "value": 3, "as_of": ago(40)}
        ],
    }
    config = cfg([item("kick")], kit=kit)
    kits = [n for n in all_numbers(config, None, TODAY) if n.scope == "kit"]
    assert len(kits) == 1 and kits[0].stale and kits[0].hand_kept


def test_due_numbers_include_reminders_before_the_limit() -> None:
    config = cfg(
        [item("kick", count=5, as_of=ago(22)), item("tiktok", count=6, as_of=ago(5))]
    )
    due = due_numbers(config, None, TODAY)
    assert [n.platform for n in due] == ["kick"] and not due[0].stale


def test_an_old_fetched_number_is_never_due() -> None:
    config = cfg([item("github")], numbers=GITHUB_SOURCE)
    book = numbers_of(github=(10, TODAY - timedelta(days=90)))
    assert due_numbers(config, book, TODAY) == []


# --- the page never shows a stale number as current --------------------------


def test_a_stale_row_is_marked_and_a_fresh_one_is_not() -> None:
    config = cfg(
        [item("kick", count=5, as_of=ago(45)), item("tiktok", count=6, as_of=ago(2))]
    )
    block: Any = config.tabs[0].blocks[0]
    rows = {r.name: r for r in platform_rows(block, None, TODAY, config)}
    assert rows["Kick"].stale and rows["Kick"].as_of == ago(45)
    assert not rows["TikTok"].stale


# --- the check gate ----------------------------------------------------------


def write(tmp_path: Path, data: Any) -> Path:
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def run_check(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(["check", *argv])
    cap = capsys.readouterr()
    return code, cap.out + cap.err


def real_today() -> date:
    return datetime.now(UTC).date()


def stale_site(tmp_path: Path, days: int = 45) -> Path:
    day = real_today()
    return write(
        tmp_path, config_doc([item("kick", count=424242, as_of=ago(days, day))])
    )


def test_check_warns_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run_check(capsys, "--config", str(stale_site(tmp_path)))
    assert code == 0 and "Warning" in out and "424242" not in out


def test_production_check_fails_on_a_stale_hand_kept_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run_check(
        capsys,
        "--config",
        str(stale_site(tmp_path)),
        "--production",
        "--format",
        "json",
    )
    report = json.loads(out)
    assert code == 2 and report["valid"] is False
    assert [e["kind"] for e in report["errors"]] == ["stale_hand_kept"]
    assert "424242" not in out


def test_the_policy_flag_overrides_the_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = stale_site(tmp_path)
    code, _ = run_check(capsys, "--config", str(site), "--stale", "fail")
    assert code == 2
    code, out = run_check(
        capsys, "--config", str(site), "--production", "--stale", "warn"
    )
    assert code == 0 and "Warning" in out


def test_a_fresh_number_passes_the_production_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = stale_site(tmp_path, days=3)
    code, out = run_check(capsys, "--config", str(site), "--production")
    assert code == 0 and "days old" not in out


def test_a_stale_kit_number_fails_the_production_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kit = {
        "title": "Kit",
        "about": "About",
        "metrics": [
            {
                "platform": "kick",
                "label": "Subscribers",
                "value": 3,
                "as_of": ago(60, real_today()),
            }
        ],
    }
    site = write(tmp_path, config_doc([item("kick")], kit=kit))
    code, out = run_check(
        capsys, "--config", str(site), "--production", "--format", "json"
    )
    assert code == 2
    assert json.loads(out)["errors"][0]["field"] == "kit.metrics"


# --- counts stale ------------------------------------------------------------


def test_counts_stale_json_lists_platforms_and_exits_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["counts", "stale", "--config", str(stale_site(tmp_path)), "--json"])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert code == 4
    assert report["due"][0]["platform"] == "kick"
    assert report["due"][0]["status"] == "stale"
    assert report["due"][0]["days_over"] == 15
    assert "424242" not in out


def test_counts_stale_exits_0_when_nothing_is_due(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["counts", "stale", "--config", str(stale_site(tmp_path, days=2))])
    assert code == 0 and "no hand-kept number is due" in capsys.readouterr().out


def test_counts_stale_remind_days_is_adjustable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = stale_site(tmp_path, days=10)
    assert main(["counts", "stale", "--config", str(site)]) == 0
    capsys.readouterr()
    code = main(["counts", "stale", "--config", str(site), "--remind-days", "7"])
    assert code == 4 and "reminder" in capsys.readouterr().out


# --- CSV import --------------------------------------------------------------

HEADER = "platform,metric,value,as_of\n"


def imp(csv_text: str, config: SiteConfig | None = None, prev: Any = None) -> Any:
    config = config or cfg([item("kick", label="subscribers"), item("tiktok")])
    return parse_import(csv_text.encode(), config, prev or {}, TODAY)


def test_a_good_file_parses() -> None:
    rows = imp(
        HEADER + f"kick,subscribers,120,{ago(1)}\ntiktok,followers,1500,{ago(0)}\n"
    )
    assert [(r.key, r.count) for r in rows] == [("kick", 120), ("tiktok", 1500)]


@pytest.mark.parametrize(
    "line",
    [
        "kick,followers,5,2027-03-09",  # kick is labeled subscribers
        "kick,subscribers,=1+1,2027-03-09",
        "kick,subscribers,-5,2027-03-09",
        "kick,subscribers,1,500,2027-03-09",
        "kick,subscribers,1,2027-03-11",  # future
        "kick,subscribers,1,2027-02-30",  # not a real day
        "kick,subscribers,1,2027-03-09T00:00:00Z",
        "kick,subscribers,1,1999-12-31",
        "nowhere,followers,1,2027-03-09",
        "=cmd,followers,1,2027-03-09",
        "kick,subscribers,99999999999999,2027-03-09",
        "kick,subscribers,01,2027-03-09",
        "kick,subscribers,1.5,2027-03-09",
        "kick,subscribers,1,2027-03-09 ",
        "",
    ],
)
def test_bad_rows_are_refused_without_echoing_the_cell(line: str) -> None:
    with pytest.raises(ImportFailed) as err:
        imp(HEADER + line + "\n")
    text = " ".join(err.value.problems)
    for fragment in ("=cmd", "=1+1", "99999999999999"):
        assert fragment not in text
    assert "row 2" in text


NUL = bytes([0])

BAD_FILES = [
    b"",
    b"platform,metric,value\nkick,subscribers,1\n",
    bytes([0xFF, 0xFE]),
    HEADER.encode() + b"kick,subscribers,1,2027-03-09" + NUL + b"\n",
    (HEADER + "kick,subscribers,1,2027-03-09\n" * 201).encode(),
    b"x" * (70 * 1024),
    HEADER.encode(),
]


@pytest.mark.parametrize("data", BAD_FILES)
def test_bad_files_are_refused(data: bytes) -> None:
    config = cfg([item("kick", label="subscribers")])
    with pytest.raises(ImportFailed):
        parse_import(data, config, {}, TODAY)


def test_a_duplicate_platform_is_refused() -> None:
    with pytest.raises(ImportFailed):
        imp(HEADER + "kick,subscribers,1,2027-03-09\nkick,subscribers,2,2027-03-09\n")


def test_a_fetched_platform_is_refused() -> None:
    config = cfg([item("github")], numbers=GITHUB_SOURCE)
    with pytest.raises(ImportFailed, match="fetch source"):
        imp(HEADER + "github,followers,1,2027-03-09\n", config)


def test_an_older_date_than_the_file_holds_is_refused() -> None:
    prev = numbers_of(kick=(9, date(2027, 3, 9))).entries
    with pytest.raises(ImportFailed, match="older"):
        imp(HEADER + "kick,subscribers,1,2027-03-08\n", prev=prev)


def test_merge_stores_utc_midnight() -> None:
    rows = imp(HEADER + "kick,subscribers,120,2027-03-09\n")
    entry = merge({}, rows)["kick"]
    assert entry.as_of == datetime(2027, 3, 9, tzinfo=UTC) and entry.count == 120


def import_site(tmp_path: Path) -> Path:
    return write(tmp_path, config_doc([item("kick", label="subscribers")]))


def test_import_writes_the_numbers_file(tmp_path: Path) -> None:
    site = import_site(tmp_path)
    day = real_today().isoformat()
    csv_file = tmp_path / "in.csv"
    csv_file.write_text(HEADER + f"kick,subscribers,321,{day}\n", encoding="utf-8")
    argv = ["counts", "import", "--config", str(site), "--csv", str(csv_file)]
    assert main(argv) == 0
    doc = json.loads((tmp_path / "numbers.json").read_text())
    assert doc["platforms"]["kick"] == {"count": 321, "as_of": f"{day}T00:00:00Z"}


def test_a_refused_file_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = import_site(tmp_path)
    csv_file = tmp_path / "in.csv"
    csv_file.write_text(HEADER + "kick,subscribers,=2+2,2027-03-09\n", encoding="utf-8")
    code = main(["counts", "import", "--config", str(site), "--csv", str(csv_file)])
    err = capsys.readouterr().err
    assert code == 2 and "=2+2" not in err
    assert not (tmp_path / "numbers.json").exists()


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    site = import_site(tmp_path)
    csv_file = tmp_path / "in.csv"
    csv_file.write_text(
        HEADER + f"kick,subscribers,1,{real_today()}\n", encoding="utf-8"
    )
    argv = ["counts", "import", "--config", str(site), "--csv", str(csv_file)]
    assert main([*argv, "--dry-run"]) == 0
    assert not (tmp_path / "numbers.json").exists()


def test_a_symlinked_csv_is_refused(tmp_path: Path) -> None:
    site = import_site(tmp_path)
    real = tmp_path / "real.csv"
    real.write_text(HEADER + f"kick,subscribers,1,{real_today()}\n", encoding="utf-8")
    link = tmp_path / "link.csv"
    link.symlink_to(real)
    assert main(["counts", "import", "--config", str(site), "--csv", str(link)]) == 2
