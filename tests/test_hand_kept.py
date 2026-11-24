# SPDX-License-Identifier: Apache-2.0
"""Kit v2 slice 6 (#631): hand-kept entries and the YouTube analytics export
importer. Everything is synthetic (example names, fixed dates)."""

from __future__ import annotations

import ast
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import handkept, youtube_export
from reach_around.config.handkept import (
    HandKeptEntry,
    HandKeptError,
    load_entries,
    make_entry,
    stale_entries,
    to_measure,
    write_entries,
)
from reach_around.config.measures import REGISTRY, visible
from reach_around.config.youtube_export import import_youtube

FIXTURE = Path(__file__).parent / "fixtures" / "youtube_export" / "chart_data.csv"
TODAY = date(2027, 3, 1)
RECENT = datetime.now(UTC).date() - timedelta(days=3)


def _entry(**kw: Any) -> HandKeptEntry:
    base: dict[str, Any] = {
        "platform": "bluesky",
        "key": "followers",
        "value": 120,
        "as_of": date(2027, 2, 20),
        "today": TODAY,
    }
    base.update(kw)
    return make_entry(**base)


# --- a typed entry ----------------------------------------------------------


def test_a_typed_entry_uses_a_registry_key_and_is_hand_kept() -> None:
    m = to_measure(_entry())
    assert (m.key, m.unit, m.value, m.as_of) == (
        "followers",
        "follows",
        120,
        date(2027, 2, 20),
    )
    assert m.trust is not None and m.trust.label == "hand-kept"
    assert "Typed by the owner" in m.trust.method


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(HandKeptError, match="key"):
        _entry(key="no_such_measure")


def test_a_wrong_unit_is_refused() -> None:
    with pytest.raises(HandKeptError, match="unit"):
        _entry(unit="hours")
    assert _entry(unit="follows").key == "followers"
    with pytest.raises(HandKeptError, match="unit"):
        _entry(unit="furlongs")


def test_a_computed_measure_cannot_be_typed() -> None:
    assert REGISTRY["engagement_rate"].computed
    with pytest.raises(HandKeptError, match="worked out"):
        _entry(key="engagement_rate", platform="tiktok")


@pytest.mark.parametrize("value", [-1, True, 1.5, "12", 10**13])
def test_bad_values_are_refused(value: object) -> None:
    with pytest.raises(HandKeptError, match="value"):
        _entry(value=value)


def test_only_a_signed_measure_may_be_negative() -> None:
    assert _entry(key="followers_change_30d", value=-35).value == -35


@pytest.mark.parametrize(
    "extra",
    [
        {"platform": "Bad Platform"},
        {"platform": ""},
        {"as_of": date(2027, 3, 2)},  # after today
        {"as_of": date(1999, 12, 31)},
        {"period": (date(2027, 2, 10), date(2027, 2, 1))},
        {"period": (date(2027, 2, 1), date(2027, 2, 25))},  # ends after as_of
        {"note": "x" * 201},
        {"note": "<b>hi</b>"},
        {"note": "line\nbreak"},
    ],
)
def test_bad_fields_are_refused(extra: dict[str, Any]) -> None:
    with pytest.raises(HandKeptError):
        _entry(**extra)


def test_an_error_never_echoes_the_value() -> None:
    canary = "SECRET-CANARY-12345"
    with pytest.raises(HandKeptError) as err:
        _entry(note=canary + "<")
    assert canary not in str(err.value)
    with pytest.raises(HandKeptError) as err:
        _entry(key=canary)
    assert canary not in str(err.value)


# --- the file ---------------------------------------------------------------


def test_the_file_round_trips_and_is_deterministic(tmp_path: Path) -> None:
    a = _entry(platform="mastodon")
    b = _entry(key="analytics_stat", value=900)
    path = tmp_path / "hand-kept.json"
    write_entries(path, [b, a])
    first = path.read_bytes()
    assert load_entries(path, today=TODAY) == sorted(
        [a, b], key=lambda e: (e.platform, e.key, e.as_of)
    )
    write_entries(path, [a, b])
    assert path.read_bytes() == first
    assert oct(path.stat().st_mode & 0o777) == "0o644"


def test_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_entries(tmp_path / "none.json", today=TODAY) == []


def _doc(entries: list[dict[str, Any]]) -> bytes:
    return json.dumps({"version": 1, "entries": entries}).encode()


GOOD: dict[str, Any] = {
    "platform": "bluesky",
    "key": "followers",
    "value": 5,
    "as_of": "2027-02-20",
}


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b'{"version": 2, "entries": []}',
        b'{"version": 1}',
        b'{"version": 1, "entries": [], "x": 1}',
        b'{"version": 1, "version": 1, "entries": []}',
        b'{"version": 1, "entries": [NaN]}',
        _doc([{**GOOD, "extra": 1}]),
        _doc([{**GOOD, "value": 1.0}]),
        _doc([{**GOOD, "value": True}]),
        _doc([{**GOOD, "key": "nope"}]),
        _doc([{**GOOD, "export": "unknown-export"}]),
        _doc([GOOD, GOOD]),  # duplicate
        b"\xff\xfe",
        b"[" * 100000,
    ],
)
def test_a_bad_file_is_refused(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "hand-kept.json"
    path.write_bytes(raw)
    with pytest.raises(HandKeptError):
        load_entries(path, today=TODAY)


def test_the_file_has_size_and_count_caps(tmp_path: Path) -> None:
    path = tmp_path / "hand-kept.json"
    path.write_bytes(b" " * (handkept.MAX_BYTES + 1))
    with pytest.raises(HandKeptError, match="larger"):
        load_entries(path, today=TODAY)
    many = [{**GOOD, "value": i} for i in range(handkept.MAX_ENTRIES + 1)]
    path.write_bytes(_doc(many))
    with pytest.raises(HandKeptError):
        load_entries(path, today=TODAY)


def test_a_symlink_or_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_bytes(_doc([GOOD]))
    link = tmp_path / "hand-kept.json"
    link.symlink_to(real)
    with pytest.raises(HandKeptError):
        load_entries(link, today=TODAY)
    d = tmp_path / "dir.json"
    d.mkdir()
    with pytest.raises(HandKeptError):
        load_entries(d, today=TODAY)
    with pytest.raises(HandKeptError):
        write_entries(link, [_entry()])


def test_a_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "hand-kept.json"
    os.mkfifo(fifo)
    with pytest.raises(HandKeptError):
        load_entries(fifo, today=TODAY)


# --- stale ------------------------------------------------------------------


def test_stale_entries_use_whole_days_and_a_limit() -> None:
    old = _entry(as_of=date(2027, 1, 30))  # 30 days: not past
    older = _entry(key="analytics_stat", value=3, as_of=date(2027, 1, 29))  # 31 days
    found = stale_entries([old, older], TODAY, limit_days=30)
    assert [(e.key, days) for e, days in found] == [("analytics_stat", 31)]
    assert len(stale_entries([old, older], TODAY, limit_days=29)) == 2


def test_only_the_newest_entry_of_a_measure_is_judged() -> None:
    new = _entry(as_of=date(2027, 2, 28), value=130)
    old = _entry(as_of=date(2026, 1, 1), value=100)
    assert stale_entries([old, new], TODAY, limit_days=30) == []


# --- the wall ---------------------------------------------------------------


def test_a_typed_entry_is_never_wider_than_its_source_field() -> None:
    from reach_around.config import measures

    entry = _entry(key="followers", field="engagement")
    assert measures.source_level("engagement") != "public"
    assert to_measure(entry).visibility == measures.source_level("engagement")
    assert visible([to_measure(entry)], frozenset({"public"})) == []


def test_an_unknown_source_field_is_refused() -> None:
    with pytest.raises(HandKeptError, match="field"):
        _entry(field="no_such_field")


# --- the YouTube analytics export -------------------------------------------


def test_the_fixture_imports_as_hand_kept_measures_naming_the_export() -> None:
    entries = import_youtube(FIXTURE, today=TODAY)
    by = {e.key: e for e in entries}
    assert set(by) == {"views", "watch_hours"}
    assert by["views"].value == 600
    assert by["watch_hours"].value == 42  # 41.5 hours, rounded half up
    for e in entries:
        assert e.platform == "youtube"
        assert e.export == "youtube-analytics"
        assert e.period == (date(2027, 2, 1), date(2027, 2, 3))
        assert e.as_of == date(2027, 2, 3)
        m = to_measure(e)
        assert m.trust is not None and m.trust.label == "hand-kept"
        assert "YouTube analytics export" in m.trust.method
        assert m.period == e.period


def test_the_total_row_is_not_double_counted() -> None:
    sums = {e.key: e.value for e in import_youtube(FIXTURE, today=TODAY)}
    assert sums["views"] == 100 + 150 + 350


def test_imported_youtube_figures_are_private_everywhere() -> None:
    # The owner decision of 2026-11-19: YouTube Analytics figures stay private.
    for e in import_youtube(FIXTURE, today=TODAY):
        m = to_measure(e)
        assert m.visibility == "private"
        assert visible([m], frozenset({"public", "gated", "private"})) == []


def test_an_importer_floor_cannot_be_overridden_by_the_file(tmp_path: Path) -> None:
    path = tmp_path / "hand-kept.json"
    write_entries(path, import_youtube(FIXTURE, today=TODAY))
    doc = json.loads(path.read_text())
    assert all("visibility" not in e and "field" not in e for e in doc["entries"])
    loaded = load_entries(path, today=TODAY)
    assert {to_measure(e).visibility for e in loaded} == {"private"}


def _csv(tmp_path: Path, text: str | bytes) -> Path:
    path = tmp_path / "export.csv"
    path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return path


HEAD = "Date,Views,Watch time (hours)\n"


@pytest.mark.parametrize(
    "text",
    [
        "",
        HEAD,  # no data rows
        "Date,Views\n2027-02-01,5\n",  # missing column
        "Date,Views,Views,Watch time (hours)\n2027-02-01,1,1,1\n",  # duplicate header
        HEAD + "2027-02-01,5\n",  # short row
        HEAD + "2027-02-01,5,1,9\n",  # long row
        HEAD + "2027-02-01,-5,1\n",
        HEAD + "2027-02-01,5.5,1\n",
        HEAD + "2027-02-01,five,1\n",
        HEAD + "2027-02-01,5,-1\n",
        HEAD + "2027-02-01,5,nan\n",
        HEAD + "2027-02-01,5,inf\n",
        HEAD + "2027-02-01,5,1e3\n",
        HEAD + "2027-02-01,5,1.23456\n",
        HEAD + "2027-2-1,5,1\n",
        HEAD + "02/01/2027,5,1\n",
        HEAD + "2027-03-02,5,1\n",  # after today
        HEAD + "2027-02-01,5,1\n2027-02-01,6,1\n",  # duplicate day
        HEAD + "2027-02-01,9999999999999,1\n",
        HEAD + "2027-02-01,5,1\n" + "Total,5,1\nTotal,5,1\n",
        b"\xff\xfe,Views\n",
        HEAD + '2027-02-01,5,"1\n',  # unterminated quote
        HEAD + "2027-02-01,5,1\x00\n",
    ],
)
def test_a_malformed_export_is_refused(tmp_path: Path, text: str | bytes) -> None:
    with pytest.raises(HandKeptError):
        import_youtube(_csv(tmp_path, text), today=TODAY)


def test_a_formula_cell_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HandKeptError):
        import_youtube(_csv(tmp_path, HEAD + "=1+1,5,1\n"), today=TODAY)


def test_a_utf8_bom_is_accepted(tmp_path: Path) -> None:
    text = ("﻿" + HEAD + "2027-02-01,5,1.5\n").encode()
    got = import_youtube(_csv(tmp_path, text), today=TODAY)
    assert {e.key: e.value for e in got} == {"views": 5, "watch_hours": 2}


def test_the_export_has_size_row_and_field_caps(tmp_path: Path) -> None:
    big = _csv(tmp_path, b"x" * (youtube_export.MAX_BYTES + 1))
    with pytest.raises(HandKeptError, match="larger"):
        import_youtube(big, today=TODAY)
    rows = "".join(
        f"{date.fromordinal(date(2024, 1, 1).toordinal() + i).isoformat()},1,1\n"
        for i in range(youtube_export.MAX_ROWS + 1)
    )
    with pytest.raises(HandKeptError, match="rows"):
        import_youtube(_csv(tmp_path, HEAD + rows), today=date(2030, 1, 1))
    wide = HEAD + "2027-02-01," + "9" * 5000 + ",1\n"
    with pytest.raises(HandKeptError):
        import_youtube(_csv(tmp_path, wide), today=TODAY)


def test_a_link_to_the_export_is_refused(tmp_path: Path) -> None:
    real = _csv(tmp_path, HEAD + "2027-02-01,5,1\n")
    link = tmp_path / "link.csv"
    link.symlink_to(real)
    with pytest.raises(HandKeptError):
        import_youtube(link, today=TODAY)


def test_the_importer_cannot_reach_the_network() -> None:
    banned = {"socket", "urllib", "http", "ssl", "requests", "httpx", "subprocess"}
    for module in (youtube_export, handkept):
        tree = ast.parse(Path(str(module.__file__)).read_text())
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                seen.add(node.module.split(".")[0])
        assert not seen & banned, module.__name__


def test_error_messages_never_echo_cell_values(tmp_path: Path) -> None:
    canary = "SECRET-CANARY-98765"
    with pytest.raises(HandKeptError) as err:
        import_youtube(_csv(tmp_path, HEAD + f"2027-02-01,{canary},1\n"), today=TODAY)
    assert canary not in str(err.value)
    with pytest.raises(HandKeptError) as err:
        import_youtube(_csv(tmp_path, f"{canary},Views\n"), today=TODAY)
    assert canary not in str(err.value)


# --- the command ------------------------------------------------------------


def _cli(*argv: str) -> int:
    from reach_around.cli import main

    return main(["kit", "measure", *argv])


def test_measure_add_writes_the_entry_and_prints_only_a_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "hand-kept.json"
    code = _cli(
        "add", "--file", str(f), "--platform", "bluesky", "--key", "followers",
        "--value", "4242", "--as-of", RECENT.isoformat(), "--unit", "follows",
        "--note", "from the app",
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "added 1 entry (1 in the file)"
    assert "4242" not in out.out + out.err and "from the app" not in out.out + out.err
    assert [e.value for e in load_entries(f)] == [4242]


@pytest.mark.parametrize(
    "extra",
    [
        ["--key", "no_such_key"],
        ["--unit", "hours"],
        ["--key", "engagement_rate"],
        ["--value", "-3"],
        ["--as-of", "2999-01-01"],
    ],
)
def test_measure_add_refuses_bad_input_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    f = tmp_path / "hand-kept.json"
    base = {
        "--platform": "bluesky", "--key": "followers", "--value": "5",
        "--as-of": "2027-02-20",
    }  # fmt: skip
    base.update(dict(zip(extra[::2], extra[1::2], strict=True)))
    argv = [x for pair in base.items() for x in pair]
    assert _cli("add", "--file", str(f), *argv) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert not f.exists()


def test_measure_add_refuses_a_repeat(tmp_path: Path) -> None:
    f = tmp_path / "hand-kept.json"
    argv = ["--platform", "bluesky", "--key", "followers", "--value", "5",
            "--as-of", RECENT.isoformat()]  # fmt: skip
    assert _cli("add", "--file", str(f), *argv) == 0
    assert _cli("add", "--file", str(f), *argv) == 1
    assert len(load_entries(f)) == 1


def test_import_youtube_command_writes_private_named_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "hand-kept.json"
    csv_file = tmp_path / "export.csv"
    days = [RECENT - timedelta(days=n) for n in (2, 1, 0)]
    rows = "".join(
        f"{d.isoformat()},{n},1\n" for d, n in zip(days, (100, 150, 350), strict=True)
    )
    csv_file.write_text("Date,Views,Watch time (hours)\n" + rows)
    code = _cli("import-youtube", "--file", str(f), "--export", str(csv_file))
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip() == "imported 2 entries (2 in the file)"
    assert "600" not in out.out
    assert {to_measure(e).visibility for e in load_entries(f)} == {"private"}
    assert _cli("import-youtube", "--file", str(f), "--export", str(csv_file)) == 1
